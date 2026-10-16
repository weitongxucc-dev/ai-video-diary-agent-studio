"""Installed SDK + shipped tools + real SQLite HTTP writes; synthetic model only.

Opaque fixture source bytes are never decoded, and no provider/media job is run.
"""
import json
import os
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
from dataclasses import asdict, replace
from app.production import CreationTaskScript, CreationTaskScriptSection, NarrationSegment

import app.main as main
from app.assistant.result_projection import read_sdk_result
from app.assistant.runtime_checks import _pi_executable, sdk_module
from app.conversation_repository import ConversationRepository
from app.assistant_goal_continuation import GoalResultProjection, GoalCandidate
from test_typed_assistant_edits_api import metadata_http_state  # noqa: F401


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
    plan = replace(state.repo.get_creation_plan("ws", task.plan_id), duration_target_seconds=15)
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
            current = state.repo.get_creation_task("ws", state.task.id)
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
                saved_source=state.repo.get_task_event("ws",f"event_edit_locator_{response.json()['turn_id']}_{locator['request_id']}").payload["source"]
                assert saved_source["expected_timeline_version_id"] == version.id
                candidate={"workspace_id":"ws","task_id":task.id,"event_id":f"recovered-{len(replies)}",
                    "request":{"locator":locator}}
                context=GoalCandidate(candidate,saved_source,{"status":"completed"},None,
                    state.repo.get_creation_task("ws",task.id),False)
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
        assert state.repo.get_creation_task("ws", state.task.id).narration_cache_key == state.narration.cache_key
        current=state.repo.get_creation_task("ws",state.task.id)
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
        source=state.repo.get_task_event("ws",f"event_edit_locator_{root_id}_{locator['request_id']}").payload["source"]
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
        version=state.repo.get_timeline_version("ws",state.repo.get_creation_task("ws",state.task.id).current_timeline_version_id)
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
    assert state.repo.get_creation_task("ws",state.task.id).current_timeline_version_id == state.base.id
    assert state.repo.list_edit_operations("ws",state.task.id) == []
