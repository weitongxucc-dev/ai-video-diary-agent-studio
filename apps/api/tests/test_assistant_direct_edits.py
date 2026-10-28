"""Installed SDK + shipped tools + real SQLite HTTP writes; synthetic model only.

Opaque fixture source bytes are never decoded, and no provider/media job is run.
"""
import json
import pytest
import os
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
from dataclasses import asdict, replace
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.creation.contracts import NarrationSegment

import app.main as main
from app.assistant.result_projection import read_sdk_result
from app.assistant.runtime_checks import _pi_executable, sdk_module
from app.conversation_repository import ConversationRepository
from app.assistant_goal_continuation import GoalResultProjection, GoalCandidate
from test_typed_assistant_edits_api import metadata_http_state  # noqa: F401


@pytest.fixture(autouse=True)
def edit_result_native_guards(request, monkeypatch):
    # Apply to this slice's original metadata representatives before their
    # metadata_http_state dependency is resolved; SDK/media nodes are not run.
    selected = {'test_rejected_direct_request_replays_original_error_without_business_writes',
                'test_http_exact_journal_source_is_readable_from_business_after_head_advances'}
    if request.node.originalname not in selected:
        return
    def forbidden(*args, **kwargs):
        pytest.fail('edit result metadata evidence forbids Popen/media/model/driver execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    import _media_preview_test_fixtures as helpers
    import test_creation_execution as creation_helpers
    for owner in (helpers, creation_helpers):
        for name in ('write_synthetic_video', 'register_synthetic_video_asset',
                     'register_synthetic_profiles', 'synthetic_wav_bytes'):
            monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(main, 'run_task_turn', forbidden)
    monkeypatch.setattr(main, 'start_assistant_continuation_driver', forbidden)
    monkeypatch.setattr(main.montage_renderer, 'render', forbidden)


SDK_RUNNER = r"""
import {pathToFileURL} from 'node:url';
import {join,dirname} from 'node:path';
import {writeFile} from 'node:fs/promises';
let raw='';for await(const part of process.stdin)raw+=part;
const input=JSON.parse(raw);
const {openCreationSession}=await import(pathToFileURL(input.sessionModule));
const {createSessionResult}=await import(pathToFileURL(input.resultModule));
const {creationInputExtension}=await import(pathToFileURL(input.inputModule));
const {AssistantMessageEventStream}=await import(pathToFileURL(join(dirname(dirname(input.modulePath)),
  'node_modules/@earendil-works/pi-ai/dist/utils/event-stream.js')));
await writeFile(join(input.cwd,'models.json'),JSON.stringify({providers:{'ai-video-diary':{
  baseUrl:'http://127.0.0.1:9',api:'openai-completions',apiKey:'unused',
  models:[{id:'synthetic-edit',contextWindow:32768,maxTokens:1024}],
}}}));
let current;
const inputExtension=creationInputExtension({getSession:()=>current.session,
  rebind:environment=>current.rebindTools({...process.env,...environment}),
  ask:async(phase,id,mode)=>{
    if(!input.inputBridge)return null;
    const response=await fetch(input.inputBridge,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({phase,input_id:id,mode})});
    if(!response.ok)throw new Error(await response.text());
    return response.json();
  }});
current=await openCreationSession({...input,modelId:'synthetic-edit',systemPrompt:'按工具真实回执回答。',
  extensionFactories:input.inputBridge?[inputExtension]:[]});
const result=createSessionResult(input.sessionId);const events=[];
current.session.subscribe(event=>{result.observe(event);events.push(event);});
let index=0;
current.session.agent.streamFunction=()=>{
  const stream=new AssistantMessageEventStream();const step=input.steps[index++];
  queueMicrotask(()=>{
    const content=step?[{type:'toolCall',id:'actual-call-'+index,name:step.name,arguments:step.args}]
      :[{type:'text',text:'已取得本轮业务回执。'}];
    const message={role:'assistant',content,api:'openai-completions',provider:'ai-video-diary',model:'synthetic-edit',
      stopReason:step?'toolUse':'stop',timestamp:Date.now(),
      usage:{input:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}}};
    stream.push({type:'done',reason:message.stopReason,message});stream.end(message);
  });return stream;
};
try{await current.session.prompt(input.message,{source:'rpc'});
  if(inputExtension.getFailure())throw inputExtension.getFailure();
  process.stdout.write(JSON.stringify({...result.value(),input_id:inputExtension.getLastConsumedInputId() ?? process.env.AI_VIDEO_DIARY_AGENT_TURN_ID ?? null,
    history_count:current.session.messages.length})+'\n');
}finally{current.session.dispose();}
"""


def test_real_sdk_subtitle_rounds_canvas_replay_restore_and_undo(metadata_http_state, tmp_path, monkeypatch):
    state = metadata_http_state
    # Fixed confirmed editorial input, not evidence of script generation.
    script = CreationTaskScript("fixed-script", "ws", state.task.id, "fixture-run", "fixture-topic", "既有文案", "zh-CN",
        [CreationTaskScriptSection("section", "开头", "原字幕", "既有画面", ["fixture-evidence"])], selection_status="confirmed")
    task = replace(state.task, confirmed_script_id=script.id)
    plan = replace(state.repo.plan_records.get_creation_plan("ws", task.plan_id), duration_target_seconds=15)
    with state.repo._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan),default=str),plan.id))
        conn.execute("INSERT INTO creation_task_scripts(id,workspace_id,task_id,run_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
            (script.id,"ws",task.id,script.run_id,json.dumps(asdict(script),default=str),script.updated_at.isoformat()))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?",(json.dumps(asdict(task),default=str),task.id))
    audio_path=tmp_path / "outputs/artifacts/task/fixed-narration.wav"
    with wave.open(str(audio_path),"wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(b"\0\0" * 64000)
    state.repo.save_narration_artifact(replace(state.narration,audio_path=str(audio_path)))
    segment=NarrationSegment("fixed-segment","ws",task.id,script.id,"section","原字幕",state.narration.audio_path,4,16000,
        "fixture-input","fixture-voice","1","zh","fixture-rules","ready")
    state.repo.save_narration_segments("ws",task.id,script.id,[segment])
    state.base=replace(state.base,id="confirmed-input",parent_version_id=state.base.id,sequence=2,
        snapshot={**state.base.snapshot,"subtitle_track_ids":["confirmed-zh"],
                  "narration_segments":[json.loads(json.dumps(asdict(segment),default=str))]})
    state.repo.create_timeline_version(state.base)
    state.repo.create_subtitle_track(replace(state.track,id="confirmed-zh",timeline_version_id=state.base.id))
    conversations = ConversationRepository(tmp_path / "assistant.sqlite")
    monkeypatch.setattr(main, "conversation_repo", conversations)
    monkeypatch.setattr(main, "creation_execution", main._new_creation_execution(state.repo))
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _: SimpleNamespace(
        effective_llm_provider="synthetic", effective_llm_model="synthetic-edit", effective_llm_api_key=None))
    monkeypatch.setattr(main, "_wake_assistant_continuations", lambda: None)
    requests = []
    input_headers = []
    delivery = {}

    class Bridge(BaseHTTPRequestHandler):
        def exchange(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length) if length else None
            if self.path == "/fixture/input-checkpoint":
                value=json.loads(body)
                if value["phase"] == "next" and not delivery.get("input_id") and any(
                        item[1].endswith("/edits/exact") for item in requests[delivery["request_offset"]:]):
                    current=conversations.get(chat["id"],"ws",state.member.member_id)
                    message=delivery["message"]
                    saved=conversations.save_draft(chat["id"],"ws",state.member.member_id,message,current["draft_version"])
                    input_id=str(uuid4())
                    accepted=state.client.post(f"/api/assistant/conversations/{chat['id']}/inputs",json={
                        "target_turn_id":delivery["turn_id"],"client_request_id":input_id,"message":message,"mode":"steer",
                        "expected_draft_version":saved["draft_version"],"expected_reference_version":saved["reference_version"]})
                    assert accepted.status_code == 200,accepted.text
                    delivery["input_id"]=input_id
                result=delivery["handler"](value["phase"],value.get("input_id"),value.get("mode"))
                self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
                self.wfile.write(json.dumps(result).encode());return
            response = state.client.request(self.command, self.path, content=body,
                headers={"Content-Type": "application/json", "X-Local-Member-Token": state.token,
                    **({"X-Assistant-Input-ID":self.headers["X-Assistant-Input-ID"]} if self.headers.get("X-Assistant-Input-ID") else {})})
            requests.append((self.command, self.path, json.loads(body) if body else None, response.status_code))
            input_headers.append(self.headers.get("X-Assistant-Input-ID"))
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)
        do_GET = exchange
        do_POST = exchange
        def log_message(self, *_args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    api_root = Path(__file__).parents[1]
    executable = _pi_executable()
    module = sdk_module(executable)
    node = str(Path(executable).parent / "node")
    runner = tmp_path / "sdk-direct-edit.mjs"
    runner.write_text(SDK_RUNNER)
    cwd = tmp_path / "session"
    cwd.mkdir()
    outputs = []
    controls = ["read_current_task", "read_task_edit_capabilities", "apply_typed_edit",
                "read_edit_control_context", "undo_last_edit"]

    def run_sdk(_settings, **kwargs):
        capabilities = state.client.get(state.url + "/assistant/edit-capabilities").json()
        assert "UpdateSubtitleCue" in [item["operation_type"] for item in capabilities["direct_apply"]["operations"]]
        version = kwargs["task_version"]
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
        env.update(AI_VIDEO_DIARY_AGENT_API_BASE=f"http://127.0.0.1:{server.server_port}",
            AI_VIDEO_DIARY_AGENT_TASK_ID=state.task.id, AI_VIDEO_DIARY_AGENT_STAGE="montage",
            AI_VIDEO_DIARY_AGENT_TURN_ID=kwargs["turn_id"], AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION=kwargs["message"],
            AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN=state.token,
            AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps(version),
            AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=json.dumps(capabilities),
            AI_VIDEO_DIARY_AGENT_PI_MODULE=str(module), AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS=",".join(controls))
        steps = [{"name": "read_current_task", "args": {}}]
        bridge=None
        if kwargs["message"] in {"先改成旧输入文字，然后继续生成视频","先修改，追加输入只作澄清"}:
            clarification=kwargs["message"] == "先修改，追加输入只作澄清"
            delivery.clear()
            delivery.update(handler=kwargs["input_handler"],turn_id=kwargs["turn_id"],request_offset=len(requests),
                message="只澄清当前保存结果，不再修改或生成" if clarification else "把字幕改成追加输入文字，然后继续生成视频")
            bridge=f"http://127.0.0.1:{server.server_port}/fixture/input-checkpoint"
            steps += [{"name":"apply_typed_edit","args":{"operation_type":"UpdateSubtitleCue","cue_id":"cue","text":"澄清前的修改" if clarification else "旧输入文字"}}]
            if not clarification:steps += [{"name":"read_current_task","args":{}},
                {"name":"read_task_edit_capabilities","args":{}},
                {"name":"apply_typed_edit","args":{"operation_type":"UpdateSubtitleCue","cue_id":"cue","text":"追加输入文字"}}]
        elif kwargs["message"] == "撤销上一步修改":
            steps += [{"name": "read_edit_control_context", "args": {}}, {"name": "undo_last_edit", "args": {}}]
        elif kwargs["message"] == "画布改成横屏16:9":
            steps += [{"name":"apply_typed_edit","args":{"operation_type":"ChangeCanvas","aspect_ratio":"16:9","fit_mode":"fit"}}]
        else:
            steps += [{"name": "apply_typed_edit", "args": {
                "operation_type": "UpdateSubtitleCue", "cue_id": "cue", "text": kwargs["message"].split("：")[1]}}]
        result = subprocess.run([node, str(runner)], env=env, cwd=cwd, text=True, capture_output=True,
            timeout=30, input=json.dumps({"modulePath": str(module), "cwd": str(cwd),
                "sessionModule": str(api_root / "agent_runtime/session.mjs"),
                "resultModule": str(api_root / "agent_runtime/result.mjs"),
                "inputModule": str(api_root / "agent_runtime/input-delivery.mjs"), "inputBridge":bridge,
                "extensionPath": str(api_root / "agent_tools/diary_tools.ts"),
                "sessionDir": str(cwd / "sessions"), "sessionId": session_id,
                "tools": controls, "message": kwargs["message"], "steps": steps}))
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert not any(item.get("isError") for item in output["tool_results"]), output["tool_results"]
        outputs.append(output)
        return read_sdk_result(result.stdout)

    monkeypatch.setattr(main, "run_task_turn", run_sdk)
    chat = state.client.post("/api/assistant/conversations/current", json={"scope_kind": "home", "scope_id": "home"}).json()
    session_id = str(uuid4())
    replies = []
    try:
        for message in ("把当前字幕改为：第一轮文字", "再把这句改为：第二轮文字", "撤销上一步修改", "画布改成横屏16:9", "撤销上一步修改"):
            current = state.repo.task_records.get_creation_task("ws", state.task.id)
            version = state.repo.get_timeline_version("ws", current.current_timeline_version_id)
            saved = conversations.get(chat["id"], "ws", state.member.member_id)
            draft = conversations.save_draft(chat["id"], "ws", state.member.member_id, message, saved["draft_version"])
            payload = {"conversation_id": chat["id"], "client_request_id": str(uuid4()),
                "message": message, "expected_draft_version": draft["draft_version"],
                "expected_reference_version": 0, "expected_timeline_version_id": version.id,
                "expected_timeline_sequence": version.sequence, "context_stage": "montage", "selected_object_refs": []}
            response = state.client.post(state.url + "/assistant/turn", json=payload)
            assert response.status_code == 200, response.text
            replies.append(response.json())
            replay = state.client.post(state.url + "/assistant/turn", json=payload)
            assert replay.status_code == 200 and replay.json()["turn_id"] == response.json()["turn_id"], replay.text
            if len(replies) < 3:
                assert response.json()["timeline_action"] == "apply"
                cue = state.repo.get_creation_task_montage_plan("ws", state.task.id)["cues"][0]
                assert cue["text"] == message.split("：")[1]
                locator=response.json()["edit_request"]
                saved_source=state.repo.execution_records.get_task_event("ws",f"event_edit_locator_{response.json()['turn_id']}_{locator['request_id']}").payload["source"]
                assert saved_source["expected_timeline_version_id"] == version.id
                candidate={"workspace_id":"ws","task_id":task.id,"event_id":f"recovered-{len(replies)}",
                    "request":{"locator":locator}}
                context=GoalCandidate(candidate,saved_source,{"status":"completed"},None,
                    state.repo.task_records.get_creation_task("ws",task.id),False)
                owner=GoalResultProjection(state.repo,conversations)
                observed=owner.request_result(context,{"status_code":200,"response":locator["result"]},runtime_available=False)
                projection=owner.request_projection(context,observed,{"edit_request":locator})
                assert projection.event["status"] == "confirmed" and projection.event["phase"] == "apply"
                assert projection.response_patch["timeline_action"] == "apply"
                assert projection.event["result_version_id"] == context.task.current_timeline_version_id
            if len(replies) == 4:
                assert response.json()["timeline_action"] == "apply"
                assert state.repo.get_creation_task_montage_plan("ws",task.id)["aspect_ratio"] == "16:9"
        assert replies[-1]["timeline_action"] == "undo"
        assert state.repo.get_creation_task_montage_plan("ws", state.task.id)["cues"][0]["text"] == "第一轮文字"
        writes = [item for item in requests if item[0] == "POST" and item[1].endswith("/edits/exact")]
        assert len(writes) == 3
        assert all(item[3] == 200 for item in writes)
        assert len({item[2]["request_id"] for item in writes}) == 3
        for _, _, body, _ in writes:
            receipt = state.client.get(state.url + f"/assistant/edit-requests/{body['request_id']}?phase=apply")
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["status"] == "recorded"
            assert receipt.json()["submitted_input"]["assistant_turn_id"] == body["assistant_turn_id"]
            assert receipt.json()["result"]["operation"]["operation_type"] == body["operation_type"]
        assert [output["history_count"] for output in outputs] == sorted({output["history_count"] for output in outputs})
        reopened = ConversationRepository(conversations.database_path)
        assert [turn["status"] for turn in reopened.list_turns(chat["id"])] == ["completed"] * 5
        assert state.repo.get_timeline_version("ws", state.base.id).snapshot == state.base.snapshot
        assert state.repo.task_records.get_creation_task("ws", state.task.id).narration_cache_key == state.narration.cache_key
        current=state.repo.task_records.get_creation_task("ws",state.task.id)
        version=state.repo.get_timeline_version("ws",current.current_timeline_version_id)
        saved=conversations.get(chat["id"],"ws",state.member.member_id)
        message="先改成旧输入文字，然后继续生成视频"
        draft=conversations.save_draft(chat["id"],"ws",state.member.member_id,message,saved["draft_version"])
        response=state.client.post(state.url+"/assistant/turn",json={"conversation_id":chat["id"],
            "client_request_id":str(uuid4()),"message":message,"expected_draft_version":draft["draft_version"],
            "expected_reference_version":draft["reference_version"],"expected_timeline_version_id":version.id,
            "expected_timeline_sequence":version.sequence,"context_stage":"montage","selected_object_refs":[]})
        assert response.status_code == 200,response.text
        root_id=response.json()["turn_id"]
        receipt=conversations.list_inputs(chat["id"],"ws",state.member.member_id)[0]
        assert receipt["input_id"] == delivery["input_id"] and receipt["status"] == "completed"
        goal=conversations.get_task_goal(root_id)
        assert goal["source_input_id"] == receipt["input_id"]
        edits=[item["result"]["details"] for item in outputs[-1]["tool_results"] if item["toolName"] == "apply_typed_edit"]
        assert [item["input_id"] for item in edits] == [None,receipt["input_id"]]
        assert edits[-1]["input_turn_id"] == root_id
        assert input_headers[-1] == receipt["input_id"]
        assert state.repo.get_creation_task_montage_plan("ws",state.task.id)["cues"][0]["text"] == "追加输入文字"
        locator=response.json()["edit_request"]
        source=state.repo.execution_records.get_task_event("ws",f"event_edit_locator_{root_id}_{locator['request_id']}").payload["source"]
        assert source["input_id"] == receipt["input_id"] and source["expected_timeline_version_id"] == receipt["request_context"]["target_snapshot"]["timeline_version_id"]
        monkeypatch.setattr(main,"LEGACY_WORKSPACE_ID","ws")
        assert main._reconcile_assistant_business_results() > 0
        continuations=[]
        def read_after_apply(_settings,**kwargs):
            continuations.append(kwargs)
            assert kwargs["continuation_context"]["source_input_id"] == receipt["input_id"]
            assert kwargs["continuation_context"]["business_event"]["phase"] == "apply"
            assert kwargs["task_version"]["timeline_version_id"] == locator["result"]["operation"]["result_version_id"]
            assert "然后继续生成视频" in kwargs["message"]
            assert kwargs["caller_user_id"] == state.member.member_id
            return "已读取真实保存结果，本测试不启动媒体。",None
        monkeypatch.setattr(main,"run_task_turn",read_after_apply)
        assert main._goal_service().reconcile() == 1 and len(continuations) == 1
        assert main._goal_service().reconcile() == 0
        assert conversations.list_turns(chat["id"])[-1]["user_message"] == ""
        assert len([item for item in requests if item[0] == "POST" and item[1].endswith("/edits/exact")]) == 5
        monkeypatch.setattr(main,"run_task_turn",run_sdk)
        version=state.repo.get_timeline_version("ws",state.repo.task_records.get_creation_task("ws",state.task.id).current_timeline_version_id)
        saved=conversations.get(chat["id"],"ws",state.member.member_id)
        message="先修改，追加输入只作澄清"
        draft=conversations.save_draft(chat["id"],"ws",state.member.member_id,message,saved["draft_version"])
        clarified=state.client.post(state.url+"/assistant/turn",json={"conversation_id":chat["id"],"client_request_id":str(uuid4()),
            "message":message,"expected_draft_version":draft["draft_version"],"expected_reference_version":draft["reference_version"],
            "context_stage":"montage","selected_object_refs":[],"expected_timeline_version_id":version.id,"expected_timeline_sequence":version.sequence})
        assert clarified.status_code == 200,clarified.text
        assert clarified.json()["timeline_action"] is None and clarified.json()["edit_request"] is None
        assert outputs[-1]["input_id"] == delivery["input_id"]
        old_receipts=[item["result"]["details"] for item in outputs[-1]["tool_results"] if item["toolName"] == "apply_typed_edit"]
        assert len(old_receipts) == 1 and old_receipts[0]["input_id"] is None
        assert conversations.get_task_goal(clarified.json()["turn_id"]) is None
        assert conversations.list_inputs(chat["id"],"ws",state.member.member_id)[-1]["status"] == "completed"
        original_receipt=state.client.get(old_receipts[0]["query_url"])
        assert original_receipt.status_code == 200 and original_receipt.json()["result_status_code"] == 200
        assert len([item for item in requests if item[0] == "POST" and item[1].endswith("/edits/exact")]) == 6
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_rejected_direct_request_replays_original_error_without_business_writes(metadata_http_state):
    state=metadata_http_state
    no_request=state.client.post(state.url+"/edits/exact",json={"operation_type":"UpdateSubtitleCue","cue_id":"cue","text":"不应写入",
        "assistant_turn_id":str(uuid4()),"expected_timeline_version_id":state.base.id,"expected_timeline_sequence":state.base.sequence})
    assert no_request.status_code == 422,no_request.text
    for changes,expected in (({"cue_id":"missing"},404),({"expected_timeline_version_id":"stale"},409)):
        payload={"operation_type":"UpdateSubtitleCue","cue_id":"cue","text":"未被采用的文字",
            "request_id":str(uuid4()),"expected_timeline_version_id":state.base.id,
            "expected_timeline_sequence":state.base.sequence,**changes}
        first=state.client.post(state.url+"/edits/exact",json=payload)
        assert first.status_code == expected, first.text
        replay=state.client.post(state.url+"/edits/exact",json=payload)
        assert replay.status_code == expected and replay.json()["detail"] == first.json()["detail"], replay.text
        receipt=state.client.get(state.url+f"/assistant/edit-requests/{payload['request_id']}?phase=apply")
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["result_status_code"] == expected
        assert receipt.json()["submitted_input"]["cue_id"] == payload["cue_id"]
    assert state.repo.task_records.get_creation_task("ws",state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.edit_records.list_edit_operations("ws",state.task.id) == []


def test_common_business_exact_apply_preview_and_linked_subtitle(metadata_http_state,monkeypatch):
    from app.creation_execution import CreationExecutionActor
    s=metadata_http_state
    actor=CreationExecutionActor('ws',s.member.member_id)
    conversations=ConversationRepository(s.repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    chat=conversations.create('ws',s.member.member_id,'task',s.task.id)
    conversations.save_draft(chat['id'],'ws',s.member.member_id,'调整现有作品',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'调整现有作品',str(uuid4()),1,{'kind':'task','task_id':s.task.id})
    origin={**conversations.task_turn_source(turn['id'],'ws',s.member.member_id,s.task.id),'assistant_turn_id':turn['id']}
    business=main._task_business()
    def invoke(operation,action):
        task=s.repo.task_records.get_creation_task('ws',s.task.id)
        version=s.repo.get_timeline_version('ws',task.current_timeline_version_id)
        payload={**action,'request_id':str(uuid4()),'expected_timeline_version_id':version.id,
                 'expected_timeline_sequence':version.sequence}
        return business.execute(operation,{'payload':payload,'draft_only':True},actor,task,{},origin,{},lambda conn:None)
    preview=invoke('preview_task_edit',{'operation_type':'ChangeCanvas','aspect_ratio':'1:1'})
    assert preview['proposal']['operation']['operation_type']=='ChangeCanvas'
    assert s.repo.task_records.get_creation_task('ws',s.task.id).current_timeline_version_id==s.base.id
    canvas=invoke('apply_task_edit',{'operation_type':'ChangeCanvas','aspect_ratio':'1:1'})
    assert canvas['montage']['aspect_ratio']=='1:1'
    linked=invoke('apply_task_edit',{'operation_type':'DuplicateClip','clip_id':'clip'})
    assert len(linked['montage']['clips'])==2
    task=s.repo.task_records.get_creation_task('ws',s.task.id)
    track=s.repo.list_subtitle_tracks('ws',task.id,timeline_version_id=task.current_timeline_version_id)[0]
    result=invoke('apply_task_edit',{'operation_type':'ReplaceSubtitleText','track_id':track.id,
        'find_text':track.cues[0].text,'replacement_text':'共同入口字幕'})
    current=s.repo.list_subtitle_tracks('ws',task.id,timeline_version_id=result['operation']['result_version_id'])
    assert current[0].cues[0].text=='共同入口字幕'


@pytest.mark.parametrize('operation',['ChangeCanvas','ChangeCrop'])
def test_common_exact_rechecks_guard_in_real_commit(metadata_http_state,monkeypatch,operation):
    from datetime import datetime,timezone
    from app.creation.editing.metadata_use_case import EditPreviewRejected
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.creation_execution import CreationExecutionActor
    from app.schemas import CreationTaskExactEditRequest
    s=metadata_http_state
    actor=CreationExecutionActor('ws',s.member.member_id)
    authority=TaskAuthorizationUseCase(s.repo,lambda identity:'ready')
    authority.ensure(s.task,actor,interactive=True)
    def guard(conn):
        assert conn.in_transaction
        if authority.status('ws',s.task.id,operation='task_goal',connection=conn,check_scope=False)!='valid':
            raise PermissionError('原任务授权已撤销')
    case=main._exact_preview_use_case(write_guard=guard)
    name='persist_subtitle_edit' if operation=='ChangeCanvas' else 'persist_montage_edit'
    persist=getattr(case.metadata,name)
    def revoke_before_commit(**values):
        current=s.repo.get_task_authorization('ws',s.task.id)
        s.repo.save_task_authorization(replace(current,revision=current.revision+1,revoked_at=datetime.now(timezone.utc)),
            expected_revision=current.revision)
        return persist(**values)
    monkeypatch.setattr(case.metadata,name,revoke_before_commit)
    action=({'aspect_ratio':'1:1'} if operation=='ChangeCanvas' else
        {'clip_id':'clip','fit_mode':'fit','crop_x':0.3,'crop_y':0.7})
    payload=CreationTaskExactEditRequest(operation_type=operation,**action,request_id=uuid4(),
        expected_timeline_version_id=s.base.id,expected_timeline_sequence=s.base.sequence)
    with pytest.raises(EditPreviewRejected) as error:
        case.apply(s.task,actor,payload,identity=s.identity)
    assert error.value.status_code==403
    assert s.repo.task_records.get_creation_task('ws',s.task.id).current_timeline_version_id==s.base.id
    assert s.repo.edit_records.list_edit_operations('ws',s.task.id)==[]


def test_business_original_read_survives_head_but_rejects_foreign_input_and_revocation(metadata_http_state,monkeypatch):
    from threading import Event
    from datetime import datetime,timezone
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_authorization import TaskAuthorizationUseCase
    s=metadata_http_state
    conversations=ConversationRepository(s.repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    chat=conversations.create('ws',s.member.member_id,'task',s.task.id)
    conversations.save_draft(chat['id'],'ws',s.member.member_id,'修改这个作品',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'修改这个作品',str(uuid4()),1,
        {'kind':'task','task_id':s.task.id})
    goal={'original_turn_id':turn['id'],'current_turn_id':turn['id'],'original_instruction':'修改这个作品',
        'workspace_id':'ws','member_id':s.member.member_id,'task_id':s.task.id,'conversation_id':chat['id'],
        'revision':0,'status':'running'}
    with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
    actor=CreationExecutionActor('ws',s.member.member_id)
    authority=TaskAuthorizationUseCase(s.repo,lambda identity:'ready')
    granted=authority.ensure(s.task,actor,interactive=True)
    required=authority._requirements('ws',s.task.id)[1]
    s.repo.save_task_authorization(replace(granted,revision=granted.revision+1,
        resource_scope={**granted.resource_scope,'references':required['references']}),expected_revision=granted.revision)
    authority.bind_business_context(s.task,actor,goal,memory={})
    business=main._task_business()
    business.authorizations=authority
    binding={'workspace_id':'ws','task_id':s.task.id,'member_id':s.member.member_id,'conversation_id':chat['id'],
        'turn_id':turn['id'],'input_id':None,'goal_revision':0,
        'task_version':{'timeline_version_id':s.base.id,'timeline_sequence':s.base.sequence}}
    def call(operation,arguments):
        from app.assistant.task_business_adapter import TaskBusinessAdapter
        return TaskBusinessAdapter(business)(operation,arguments,call_id='fixture',tool_call_id='fixture',source_binding=binding,
            cancel_event=Event(),deadline_monotonic=float('inf'))
    request_id=str(uuid4())
    saved=call('apply_task_edit',{'payload':{'operation_type':'ChangeCrop','clip_id':'clip','fit_mode':'fit','crop_x':0.3,'crop_y':0.7,
        'request_id':request_id,'expected_timeline_version_id':s.base.id,'expected_timeline_sequence':s.base.sequence}})
    assert saved['ok'],saved
    assert s.repo.task_records.get_creation_task('ws',s.task.id).current_timeline_version_id!=s.base.id
    read=call('read_task_edit_request',{'request_id':request_id,'phase':'apply'})
    assert read['ok'] and read['value']['result']==saved['value'],read
    assert not call('read_task_edit_request',{'task_id':'other-task','request_id':request_id,'phase':'apply'})['ok']
    foreign_id=str(uuid4())
    s.repo.claim_edit_request(s.task,s.member.member_id,foreign_id,'apply',{'_business_source':{
        'actor_identity':actor.identity_payload(),'assistant_turn_id':turn['id'],'input_id':'another-input','input_turn_id':turn['id']}})
    assert not call('read_task_edit_request',{'request_id':foreign_id,'phase':'apply'})['ok']
    authorization=s.repo.get_task_authorization('ws',s.task.id)
    s.repo.save_task_authorization(replace(authorization,revision=authorization.revision+1,revoked_at=datetime.now(timezone.utc)),
        expected_revision=authorization.revision)
    assert not call('read_task_edit_request',{'request_id':request_id,'phase':'apply'})['ok']


def test_edit_origin_queued_work_accepts_finished_turn_only_with_current_goal(metadata_http_state):
    from app.creation.editing.exact_preview_use_case import validate_edit_origin
    from app.creation_execution import CreationExecutionActor
    s=metadata_http_state
    conversations=ConversationRepository(s.repo.database_path)
    chat=conversations.create('ws',s.member.member_id,'task',s.task.id)
    conversations.save_draft(chat['id'],'ws',s.member.member_id,'继续完成这段修改',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'继续完成这段修改',str(uuid4()),1,
        {'kind':'task','task_id':s.task.id})
    actor=CreationExecutionActor('ws',s.member.member_id)
    source={**conversations.task_turn_source(turn['id'],'ws',s.member.member_id,s.task.id),
        'workspace_id':'ws','task_id':s.task.id,'member_id':s.member.member_id,
        'actor_identity':actor.identity_payload(),'assistant_turn_id':turn['id']}
    validate_edit_origin(conversations,source,actor)
    conversations.finish_turn(turn['id'],'completed','已排队',{})
    with pytest.raises(ValueError): validate_edit_origin(conversations,source,actor)
    goal={'workspace_id':'ws','task_id':s.task.id,'member_id':s.member.member_id,'original_turn_id':turn['id'],
        'current_turn_id':turn['id'],'status':'waiting_result','original_instruction':'继续完成这段修改'}
    with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
    validate_edit_origin(conversations,source,actor)
    with conversations._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        validate_edit_origin(conversations,source,actor,connection=conn)
        conversations._write_goal(conn,{'id':turn['id']},{**goal,'status':'stopped'})
        with pytest.raises(ValueError): validate_edit_origin(conversations,source,actor,connection=conn)


@pytest.mark.parametrize('phase',['apply','preview'])
def test_http_exact_journal_source_is_readable_from_business_after_head_advances(metadata_http_state,monkeypatch,phase):
    from threading import Event
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.assistant.task_business import TaskBusinessRejected
    from app.creation.editing.metadata_use_case import EditPreviewRejected
    s=metadata_http_state
    conversations=ConversationRepository(s.repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    chat=conversations.create('ws',s.member.member_id,'task',s.task.id)
    conversations.save_draft(chat['id'],'ws',s.member.member_id,'调整画幅并继续完成',0)
    turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'调整画幅并继续完成',str(uuid4()),1,
        {'kind':'task','task_id':s.task.id,'target_snapshot':{'timeline_version_id':s.base.id,'timeline_sequence':s.base.sequence}})
    goal={'original_turn_id':turn['id'],'current_turn_id':turn['id'],'original_instruction':'调整画幅并继续完成',
        'workspace_id':'ws','member_id':s.member.member_id,'task_id':s.task.id,'conversation_id':chat['id'],
        'revision':0,'status':'running'}
    with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
    actor=CreationExecutionActor('ws',s.member.member_id)
    authority=TaskAuthorizationUseCase(s.repo,lambda identity:'ready')
    granted=authority.ensure(s.task,actor,interactive=True)
    required=authority._requirements('ws',s.task.id)[1]
    s.repo.save_task_authorization(replace(granted,revision=granted.revision+1,
        resource_scope={**granted.resource_scope,'references':required['references']}),expected_revision=granted.revision)
    authority.bind_business_context(s.task,actor,goal,memory={})
    payload={'operation_type':'ChangeCanvas','aspect_ratio':'1:1','assistant_turn_id':turn['id'],
        'request_id':str(uuid4()),'expected_timeline_version_id':s.base.id,'expected_timeline_sequence':s.base.sequence}
    endpoint=s.url+'/edits/exact'+('/preview?draft=true' if phase=='preview' else '')
    forged=s.client.post(endpoint,json={**payload,'request_id':str(uuid4())},
        headers={'X-Assistant-Input-ID':'not-a-consumed-input'})
    assert forged.status_code==409,forged.text
    submitted=s.client.post(endpoint,json=payload)
    assert submitted.status_code==200,submitted.text
    if phase=='preview':
        from app.schemas import CreationTaskExactEditRequest
        case=main._exact_preview_use_case()
        case.apply(s.task,actor,CreationTaskExactEditRequest(operation_type='ChangeCrop',clip_id='clip',
            fit_mode='fit',crop_x=.3,crop_y=.7,request_id=uuid4(),expected_timeline_version_id=s.base.id,
            expected_timeline_sequence=s.base.sequence),identity=s.identity)
    current=s.repo.task_records.get_creation_task('ws',s.task.id)
    assert current.current_timeline_version_id!=s.base.id
    if phase == 'apply':
        from copy import deepcopy
        from starlette.requests import Request
        from app.assistant.edit_result_binding import AssistantEditResultBinding, EditResultRejected
        request = Request({'type':'http', 'headers':[(b'x-local-member-token', s.token.encode())]})
        locator = main._assistant_applied_edit_receipt(s.task.id, request, turn['id'], {
            'request_id':payload['request_id'], 'phase':'apply',
            'query_url':s.url+f"/assistant/edit-requests/{payload['request_id']}?phase=apply"})
        assert locator.result['operation']['result_version_id'] == current.current_timeline_version_id
        forged = deepcopy(locator.result)
        forged['operation']['result_version_id'] = 'not-the-committed-head'
        with pytest.raises(EditResultRejected, match='实际操作不一致'):
            AssistantEditResultBinding(s.repo, conversations).require_applied('ws', s.task.id, turn['id'],
                locator.model_copy(update={'result':forged}))
    business=main._task_business()
    business.authorizations=authority
    binding={'workspace_id':'ws','task_id':s.task.id,'member_id':s.member.member_id,'conversation_id':chat['id'],
        'turn_id':turn['id'],'input_id':None,'goal_revision':0,
        'task_version':{'timeline_version_id':s.base.id,'timeline_sequence':s.base.sequence}}
    from app.assistant.task_business_adapter import TaskBusinessAdapter
    result=TaskBusinessAdapter(business)('read_task_edit_request',{'request_id':payload['request_id'],'phase':phase},
        call_id='read-http',tool_call_id='read-http',source_binding=binding,cancel_event=Event(),deadline_monotonic=float('inf'))
    assert result['ok'] and result['value']['result']==submitted.json(),result
    from app.assistant_goal_continuation import GoalResultProjection
    event_id,_=GoalResultProjection(s.repo,conversations).request_claim(current,s.member.member_id,payload['request_id'],phase)
    with pytest.raises(PermissionError):
        main._exact_preview_use_case().read_request_source(current,event_id,replace(actor,member_id='other-actor'))
    with pytest.raises(TaskBusinessRejected):
        business.execute('read_task_edit_request',{'request_id':payload['request_id'],'phase':phase},actor,current,
            {**goal,'source_input_id':'another-input','source_input_turn_id':turn['id']},{},{},None)
    legacy_id=str(uuid4())
    legacy_event,_=s.repo.claim_edit_request(current,s.member.member_id,legacy_id,phase,{'request_id':legacy_id})
    s.repo.save_edit_request_result(current,legacy_event,status_code=200,response={'original_result':'preserved'})
    with pytest.raises(EditPreviewRejected) as unknown:
        business.execute('read_task_edit_request',{'request_id':legacy_id,'phase':phase},actor,current,goal,{},{},None)
    assert unknown.value.detail['submission_effect']=='unknown'
    assert s.repo.read_edit_request_result(current,legacy_event)=={'status_code':200,'response':{'original_result':'preserved'}}
    gap_payload={**payload,'request_id':str(uuid4())}
    gap_inputs={**gap_payload,**({'_draft':True} if phase=='preview' else {})}
    gap_event,_=s.repo.claim_edit_request(current,s.member.member_id,gap_payload['request_id'],phase,gap_inputs)
    gap=s.client.post(endpoint,json=gap_payload)
    assert gap.status_code==409 and gap.json()['detail']['submission_effect']=='unknown',gap.text
    with pytest.raises(KeyError): s.repo.execution_records.get_task_event('ws',gap_event+'_source')
    assert s.repo.read_edit_request_result(current,gap_event) is None
    conversations.finish_turn(turn['id'],'completed','原请求已提交',{})
    foreign_input=s.client.post(endpoint,json=payload,headers={'X-Assistant-Input-ID':'another-input'})
    assert foreign_input.status_code==403,foreign_input.text
    replay=s.client.post(endpoint,json=payload)
    assert replay.status_code==200 and replay.json()==submitted.json(),replay.text
    assert s.repo.task_records.get_creation_task('ws',s.task.id).current_timeline_version_id==current.current_timeline_version_id


def _bootstrap_input(s, bootstrap):
    from copy import deepcopy
    from app.creation.contracts import TimelineVersion
    from app.montage import creation_task_montage_plan_to_payload
    creation_plan=replace(s.repo.plan_records.get_creation_plan('ws',s.task.plan_id),id='bootstrap-plan')
    seeded_task=replace(s.task,id='bootstrap-task',plan_id=creation_plan.id,current_timeline_version_id=None,
        montage_plan_id=None)
    s.repo.plan_confirmation.confirm_creation_plan(creation_plan,seeded_task,verified_owner_user_id=s.member.member_id)
    montage=deepcopy(s.plan)
    montage.id='bootstrap-montage'
    montage.task_id=seeded_task.id
    s.repo.save_creation_task_montage_plan('ws',seeded_task.id,montage.id,creation_task_montage_plan_to_payload(montage))
    if bootstrap=='missing_montage':
        s.repo.create_timeline_version(TimelineVersion(id='bootstrap-input',workspace_id='ws',task_id=seeded_task.id,sequence=1,
            parent_version_id=None,created_by_operation_id=None,snapshot={}),
            expected_current_version_id=None)
    task=s.repo.task_records.get_creation_task('ws',seeded_task.id)
    return task,montage


@pytest.mark.parametrize('bootstrap',['no_timeline','missing_montage'])
@pytest.mark.parametrize('invalidation',['revoked','stopped','head_advanced'])
def test_bootstrap_revalidates_real_transaction_before_creating_a_version(metadata_http_state,monkeypatch,bootstrap,invalidation):
    from datetime import datetime,timezone
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_authorization import TaskAuthorizationUseCase
    from app.schemas import CreationTaskExactEditRequest
    s=metadata_http_state
    from app.creation.contracts import TimelineVersion
    task,montage=_bootstrap_input(s,bootstrap)
    conversations=ConversationRepository(s.repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    chat=conversations.create('ws',s.member.member_id,'task',task.id)
    conversations.save_draft(chat['id'],'ws',s.member.member_id,'调整画幅',0)
    expected_sequence=0 if bootstrap=='no_timeline' else s.base.sequence
    turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'调整画幅',str(uuid4()),1,
        {'kind':'task','task_id':task.id,'target_snapshot':{'timeline_version_id':task.current_timeline_version_id,
            'timeline_sequence':expected_sequence}})
    actor=CreationExecutionActor('ws',s.member.member_id)
    authority=TaskAuthorizationUseCase(s.repo,lambda identity:'ready')
    authorization=authority.ensure(task,actor,interactive=True)
    assert authority.status('ws',task.id,check_scope=False)=='valid',authorization
    def guard(conn):
        assert conn.in_transaction
        if authority.status('ws',task.id,operation='task_goal',connection=conn,check_scope=False)!='valid':
            raise PermissionError('原授权已撤销')
    case=main._exact_preview_use_case(write_guard=guard)
    payload=CreationTaskExactEditRequest(operation_type='ChangeCanvas',aspect_ratio='1:1',assistant_turn_id=turn['id'],
        request_id=uuid4(),expected_timeline_version_id=task.current_timeline_version_id,expected_timeline_sequence=expected_sequence)
    _,source=case._source(task,payload,s.identity,actor,None)
    if invalidation=='revoked':
        s.repo.save_task_authorization(replace(authorization,revision=authorization.revision+1,revoked_at=datetime.now(timezone.utc)),
            expected_revision=authorization.revision)
    elif invalidation=='stopped':
        goal={'workspace_id':'ws','task_id':task.id,'member_id':s.member.member_id,'original_turn_id':turn['id'],
            'current_turn_id':turn['id'],'status':'stopped'}
        with conversations._connect() as conn: conversations._write_goal(conn,{'id':turn['id']},goal)
    else:
        s.repo.create_timeline_version(TimelineVersion(id='concurrent-head',workspace_id='ws',task_id=task.id,
            sequence=expected_sequence+1,parent_version_id=task.current_timeline_version_id,created_by_operation_id=None,snapshot={}),
            expected_current_version_id=task.current_timeline_version_id)
    before_head=s.repo.task_records.get_creation_task('ws',task.id).current_timeline_version_id
    with s.repo._connect() as conn:
        before=[tuple(row) for row in conn.execute('SELECT * FROM timeline_versions ORDER BY id')]
        montage_before=[tuple(row) for row in conn.execute('SELECT * FROM creation_task_montage_plans ORDER BY id')]
    with pytest.raises((PermissionError,ValueError)):
        case.metadata.ensure_montage_base('ws',task,montage,source=source)
    with s.repo._connect() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM timeline_versions ORDER BY id')]==before
        assert [tuple(row) for row in conn.execute('SELECT * FROM creation_task_montage_plans ORDER BY id')]==montage_before
    assert s.repo.task_records.get_creation_task('ws',task.id).current_timeline_version_id==before_head


@pytest.mark.parametrize('authorization_state',['revoked','expired','wrong_identity','unverified','missing_assistant','manual'])
def test_http_bootstrap_without_write_guard_checks_existing_authority(metadata_http_state,monkeypatch,authorization_state):
    from datetime import datetime,timedelta,timezone
    from app.creation_execution import CreationExecutionActor
    from app.creation.task_authorization import TaskAuthorizationUseCase
    s=metadata_http_state
    task,_=_bootstrap_input(s,'no_timeline')
    actor=CreationExecutionActor('ws',s.member.member_id)
    conversations=ConversationRepository(s.repo.database_path)
    monkeypatch.setattr(main,'conversation_repo',conversations)
    assistant={}
    if authorization_state!='manual':
        chat=conversations.create('ws',s.member.member_id,'task',task.id)
        conversations.save_draft(chat['id'],'ws',s.member.member_id,'调整画幅',0)
        turn,_=conversations.create_turn_idempotent(chat['id'],'ws',s.member.member_id,'调整画幅',str(uuid4()),1,
            {'kind':'task','task_id':task.id,'target_snapshot':{'timeline_version_id':None,'timeline_sequence':0}})
        assistant={'assistant_turn_id':turn['id']}
    authority=TaskAuthorizationUseCase(s.repo,lambda identity:'ready')
    if authorization_state not in {'manual','missing_assistant'}:
        authorization=authority.ensure(task,actor,interactive=True)
        now=datetime.now(timezone.utc)
        changes={'revision':authorization.revision+1}
        if authorization_state=='revoked': changes['revoked_at']=now
        elif authorization_state=='expired': changes['expires_at']=now-timedelta(seconds=1)
        elif authorization_state=='unverified': changes['expires_at']=None
        else: changes['identity']={**authorization.identity,'member_id':'another-member'}
        s.repo.save_task_authorization(replace(authorization,**changes),expected_revision=authorization.revision)
    case=main._exact_preview_use_case()
    assert case.metadata.write_guard is None
    original_status=case.metadata.task_authorizations.status
    def status(workspace_id,task_id,**options):
        assert options['connection'].in_transaction
        assert options['identity']==authority.identity(actor)
        assert options['check_scope'] is False and 'operation' not in options
        return original_status(workspace_id,task_id,**options)
    monkeypatch.setattr(case.metadata.task_authorizations,'status',status)
    monkeypatch.setattr(main,'_exact_preview_use_case',lambda *args,**kwargs:case)
    monkeypatch.setattr(main,'_verify_creation_identity',lambda *_:pytest.fail('内容bootstrap不能请求设备生成许可'))
    with s.repo._connect() as conn:
        before=[tuple(row) for row in conn.execute('SELECT * FROM timeline_versions ORDER BY id')]
    response=s.client.post(f'/api/creation-tasks/{task.id}/edits/exact/preview?draft=true',json={
        'operation_type':'ChangeCanvas','aspect_ratio':'1:1','request_id':str(uuid4()),
        'expected_timeline_version_id':None,'expected_timeline_sequence':0,**assistant})
    current=s.repo.task_records.get_creation_task('ws',task.id)
    if authorization_state=='manual':
        assert response.status_code==200,response.text
        assert current.current_timeline_version_id is not None
        assert s.repo.get_task_authorization('ws',task.id) is None
    else:
        assert response.status_code==403,response.text
        with s.repo._connect() as conn:
            assert [tuple(row) for row in conn.execute('SELECT * FROM timeline_versions ORDER BY id')]==before
        assert current.current_timeline_version_id is None
