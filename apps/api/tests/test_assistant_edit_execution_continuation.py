"""One input's committed edit may continue; no model, worker or media process."""
import json
import os
import subprocess
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

import pytest
from starlette.requests import Request

import app.main as main
from app.conversation_repository import ConversationRepository
from app.assistant.result_projection import project_business_result, read_sdk_result
from app.assistant.failures import AssistantUnavailable, FAILURE_OVERLOADED
from test_typed_assistant_edits_api import metadata_http_state  # noqa: F401


def source_turn(state, conversations):
    member = state.members.resolve_member(state.token)
    chat = conversations.create("ws", member.member_id, "home", "home", "编辑并制作")
    chat = conversations.save_draft(chat["id"], "ws", member.member_id, "修改字幕并更新成片", 0)
    turn, _ = conversations.create_turn_idempotent(chat["id"], "ws", member.member_id,
        chat["draft_text"], str(uuid4()), chat["draft_version"], {"kind": "task", "task_id": state.task.id,
        "stage": "montage", "target_snapshot": {"timeline_version_id": state.base.id, "timeline_sequence": state.base.sequence}})
    return turn


@pytest.mark.parametrize("case", ["own", "other_turn", "unknown", "foreign_version", "other_input"])
def test_execution_accepts_only_this_inputs_committed_successor(metadata_http_state, tmp_path, monkeypatch, case):
    state = metadata_http_state
    conversations = ConversationRepository(tmp_path / "conversation.sqlite")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    turn = source_turn(state, conversations)
    from app.creation_execution import CreationExecutionActor
    monkeypatch.setattr(main, "_require_permit_actor", lambda *_args: CreationExecutionActor("ws", state.member.member_id))
    writer = source_turn(state, conversations) if case == "other_turn" else turn
    response = state.client.post(state.url + "/edits/exact", json={"operation_type": "UpdateSubtitleCue",
        "cue_id": "cue", "text": "已保存的新字幕", "request_id": str(uuid4()), "assistant_turn_id": writer["id"],
        "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": state.base.sequence})
    assert response.status_code == 200, response.text
    edited = state.repo.task_records.get_creation_task("ws", state.task.id)
    if case == "unknown":
        from app.creation.editing.exact_preview_use_case import ExactPreviewUseCase
        original = ExactPreviewUseCase.read_request_receipt
        monkeypatch.setattr(ExactPreviewUseCase, "read_request_receipt",
            lambda self, *args, **kwargs: {**original(self, *args, **kwargs), "status": "unknown"})
    elif case == "foreign_version":
        version = state.repo.timeline_records.get_timeline_version("ws", edited.current_timeline_version_id)
        state.repo.timeline_records.create_timeline_version(replace(version, id="foreign-version", parent_version_id=version.id, sequence=version.sequence+1))
    if case == "other_input":
        conversations.append_event(turn["id"], {"type": "assistant_input", "status": "consumed", "input_id": str(uuid4()),
            "target_turn_id": turn["id"], "message": "追加输入", "mode": "steer", "request_context": {
            "kind": "task", "task_id": state.task.id, "stage": "montage", "target_snapshot": {
            "timeline_version_id": state.base.id, "timeline_sequence": state.base.sequence}}})
    request = Request({"type": "http", "headers": [(b"x-local-member-token", state.token.encode())]})
    if case == "own":
        source, replay = main._assistant_execution_source(state.task.id, request, turn["id"], "resume")
        assert replay is None and source["expected_timeline_version_id"] == edited.current_timeline_version_id
        assert source["expected_timeline_sequence"] == state.base.sequence + 1
    else:
        with pytest.raises(main.HTTPException) as error:
            main._assistant_execution_source(state.task.id, request, turn["id"], "resume")
        assert error.value.status_code == 409


def completion(*events, answer="已处理", status="settled"):
    return {"status": status, "answer": answer, "input_id": "turn", "tool_results": list(events)}


def event(name, details, *, failed=False):
    return {"type": "tool_execution_end", "toolName": name, "isError": failed,
            "result": {"details": {**details, "invocation_input_id": "turn"}}}


@pytest.mark.parametrize("later", ["tool_failed", "provider_failed", "render_finished"])
def test_later_failure_or_render_does_not_erase_committed_edit(later):
    edit = event("apply_typed_edit", {"kind": "edit_applied", "request_id": "original", "result": {"operation": {"result_version_id": "edited-v3"}}})
    if later == "render_finished":
        rendered = event("resume_creation_execution", {"kind": "execution_action", "action": "resume",
            "status": {"task_id": "task", "job": {"id": "render", "status": "succeeded"}, "video_ready": True,
                       "current_timeline_version_id": "rendered-v4"}})
        reply, result = project_business_result(completion(edit, rendered))
        assert result["kind"] == "execution_action" and result["status"]["video_ready"]
        assert result["applied_edit"]["result"]["operation"]["result_version_id"] == "edited-v3"
    else:
        failed = event("resume_creation_execution", {"kind": "execution_action_failed"}, failed=True)
        reply, result = project_business_result(completion(edit, failed) if later == "tool_failed" else completion(edit, answer="", status="failed"))
        assert result["kind"] == "edit_applied" and result["request_id"] == "original"
        assert "修改已保存" in reply


def test_529_error_retains_safe_service_classification():
    value = {"type": "creation_session_result", "protocol": 1, "owner": "pi-coding-agent-sdk",
             "status": "settled", "answer": "", "tool_results": [],
             "failure": '529: {"type":"overloaded_error","message":"当前服务集群负载较高 (2064)"}'}
    with pytest.raises(AssistantUnavailable) as error:
        read_sdk_result(json.dumps(value))
    assert error.value.cause == FAILURE_OVERLOADED and error.value.service_contacted
    assert "529" in str(error.value) and "没有留下" not in str(error.value)


TOOL_RUNNER = r"""
import {pathToFileURL} from 'node:url';
const {createDiaryExtension}=await import(process.argv[1]);
let raw='';for await(const part of process.stdin)raw+=part;
const input=JSON.parse(raw),calls=[],tools=new Map();
let task={id:'task',current_timeline_version_id:'base',timeline_sequence:2},receipt;
Object.assign(process.env,{
 AI_VIDEO_DIARY_AGENT_API_BASE:'https://fixture.invalid',AI_VIDEO_DIARY_AGENT_TASK_ID:'task',
 AI_VIDEO_DIARY_AGENT_STAGE:'montage',AI_VIDEO_DIARY_AGENT_TURN_ID:'turn',AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION:'改字幕并更新成片',
 AI_VIDEO_DIARY_AGENT_TASK_READ_READY:'1',AI_VIDEO_DIARY_AGENT_TASK_VERSION:JSON.stringify({timeline_version_id:'base',timeline_sequence:2}),
 AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES:JSON.stringify(input.capabilities)
});
globalThis.fetch=async(url,init)=>{
 const path=new URL(url).pathname,body=init?.body?JSON.parse(init.body):null;
 calls.push({path,body,method:init?.method||'GET'});
 let value={task};
 if(path.endsWith('/edits/exact')){
  const operation={id:'operation',task_id:'task',base_version_id:'base',result_version_id:'edited',status:'applied'};
  receipt={status:input.case==='unverified'?'unknown':'recorded',phase:'apply',request_id:body.request_id,result_status_code:200,submitted_input:body,result:{operation}};
  task={...task,current_timeline_version_id:input.case==='foreign'?'foreign':'edited',timeline_sequence:input.case==='foreign'?4:3};
  value={operation};
  if(input.case==='lost')throw new TypeError('original reply lost');
 }
 if(path.includes('/assistant/edit-requests/'))value=receipt;
 if(path.endsWith('/execution/resume'))value={task_id:'task',job:{id:'render',task_id:'task',status:'queued'}};
 return {ok:true,status:200,json:async()=>value};
};
const binding=createDiaryExtension(process.env);
await binding.extension({registerTool:tool=>tools.set(tool.name,tool),getAllTools:()=>[...tools.values()]});
const edit=await tools.get('apply_typed_edit').execute('direct',{operation_type:'UpdateSubtitleCue',cue_id:'cue',text:'新字幕'});
const execution=await tools.get('resume_creation_execution').execute('render',{});
console.log(JSON.stringify({edit,execution,calls}));
"""


@pytest.mark.parametrize("case", ["own", "unverified", "foreign", "lost"])
def test_tool_only_continues_after_verified_direct_edit(tmp_path, case):
    from app.schemas import CreationTaskExactEditRequest
    capabilities = {"schema": CreationTaskExactEditRequest.model_json_schema(), "supported_operations": ["UpdateSubtitleCue"],
        "direct_apply": {"endpoint": "/api/creation-tasks/task/edits/exact", "operations": [{
            "operation_type": "UpdateSubtitleCue", "available": True, "parameter_fields": ["cue_id", "text"],
            "required_fields": ["cue_id", "text"]}]}}
    module = Path(__file__).parents[1] / "agent_tools/diary_tools.ts"
    result = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", TOOL_RUNNER, module.as_uri()],
        input=json.dumps({"case": case, "capabilities": capabilities}), text=True, capture_output=True, check=True,
        env={"PATH": os.environ["PATH"]}, cwd=tmp_path, timeout=15)
    value = json.loads(result.stdout)
    render_posts = [call for call in value["calls"] if call["method"] == "POST" and call["path"].endswith('/execution/resume')]
    assert len(render_posts) == (1 if case == "own" else 0)
    if case == "own":
        assert value["edit"]["details"]["kind"] == "edit_applied"
        assert value["execution"]["details"]["kind"] == "execution_action"
    else:
        assert value["execution"]["isError"] is True


@pytest.mark.parametrize("kind", ["execution_action", "execution_result", "refused", "read_refused"])
def test_host_keeps_v3_edit_receipt_when_render_has_already_reached_v4(metadata_http_state, tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    state = metadata_http_state
    conversations = ConversationRepository(tmp_path / "chat.sqlite")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "_goal_service", lambda: SimpleNamespace(register_turn=lambda *_: None))
    monkeypatch.setattr(main, "_wake_assistant_continuations", lambda: None)
    turn = source_turn(state, conversations)
    request_id = str(uuid4())
    response = state.client.post(state.url + "/edits/exact", json={"operation_type": "UpdateSubtitleCue", "cue_id": "cue",
        "text": "已保存的新字幕", "request_id": request_id, "assistant_turn_id": turn["id"],
        "expected_timeline_version_id": state.base.id, "expected_timeline_sequence": state.base.sequence})
    assert response.status_code == 200, response.text
    edit_id = response.json()["operation"]["result_version_id"]
    edit_version = state.repo.timeline_records.get_timeline_version("ws", edit_id)
    state.repo.timeline_records.create_timeline_version(replace(edit_version, id="rendered-v4", parent_version_id=edit_id, sequence=edit_version.sequence+1))
    status = {"task_id": state.task.id, "job": {"id": "render", "status": "succeeded"}, "video_ready": True}
    def bound(*_args):
        if kind in {"refused", "read_refused"}:
            raise main.HTTPException(status_code=409, detail="制作来源不一致")
        assert state.repo.task_records.get_creation_task("ws", state.task.id).current_timeline_version_id == "rendered-v4"
        return status
    monkeypatch.setattr(main, "_bind_assistant_execution_result", bound)
    monkeypatch.setattr(main, "_read_bound_execution_result", bound)
    payload = main.CreationTaskAssistantTurnRequest(conversation_id=turn["conversation_id"], message="修改字幕并更新成片",
        client_request_id=uuid4(), expected_draft_version=1, context_stage="montage")
    details = {"kind": "execution_result" if kind in {"execution_result", "read_refused"} else "execution_action", "action": None if kind in {"execution_result", "read_refused"} else "resume",
        "status": status, "applied_edit": {"kind": "edit_applied", "task_id": state.task.id, "request_id": request_id,
        "phase": "apply", "query_url": state.url+"/assistant/edit-requests/"+request_id+"?phase=apply"}}
    request = Request({"type": "http", "headers": [(b"x-local-member-token", state.token.encode())]})
    result = main._task_turn_http_adapter().finish(state.task.id, request, payload, turn["id"], "真实回执", details, "montage")
    assert result.timeline_action == "apply" and str(result.edit_request.request_id) == request_id
    assert result.edit_request.result["operation"]["result_version_id"] == edit_id
    assert conversations.get_turn(turn["id"])["status"] == "completed"
    if kind in {"refused", "read_refused"}:
        assert result.execution_status is None and result.execution_action is None
        assert "修改已保存" in result.reply and "未通过核验" in result.reply
    else:
        assert result.execution_status["video_ready"]
        assert result.execution_action == ("resume" if kind == "execution_action" else None)


def test_query_after_lost_render_post_keeps_read_semantics_and_edit_receipt():
    edit = event("apply_typed_edit", {"kind": "edit_applied", "request_id": "original"})
    failed = event("resume_creation_execution", {"kind": "execution_action_failed"}, failed=True)
    query = event("read_creation_execution", {"kind": "execution_result", "status": {"job": {"id": "original", "status": "succeeded"}, "video_ready": True}})
    reply, result = project_business_result(completion(edit, failed, query))
    assert result["kind"] == "execution_result" and result["applied_edit"]["request_id"] == "original"
    assert "未新增制作请求" in reply and result["status"]["video_ready"]


@pytest.mark.parametrize("field,value", [("answer", "字幕529"), ("tool_results", [{"type": "tool_execution_end", "result": {"details": {"duration": 529}}}])])
def test_ordinary_content_529_is_not_provider_overload(field, value):
    from app.assistant.failures import _classify_output
    result = {"type": "creation_session_result", "owner": "pi-coding-agent-sdk", "protocol": 1, "answer": "", "tool_results": []}
    result[field] = value
    assert _classify_output(json.dumps(result)) != FAILURE_OVERLOADED
