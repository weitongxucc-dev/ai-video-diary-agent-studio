"""Exercise the shipped tool boundary, safe projection and durable receipts without a model."""
from __future__ import annotations

import json
import os
from app.subprocess_environment import subprocess_environment
import subprocess
from pathlib import Path

import pytest

from app.assistant.projection import _PiStreamProjector, _business_event_lines, _safe_business_failure
from app.conversation_repository import ConversationRepository
from app.schemas import AssistantStreamEvent


RUNNER = r"""
const {createBusinessRequest,RequestNotSubmitted,safeBusinessFailure} = await import(new URL('../agent_tools/diary/transport.ts', process.argv[1]));
const {createDiaryTools} = await import(new URL('../agent_tools/diary/runtime.ts', process.argv[1]));
const {registerExecutionTools} = await import(new URL('../agent_tools/diary/execution-tools.ts', process.argv[1]));
const {createHash} = await import('node:crypto');
const credentials = {authorization:'Bearer opaque-auth-value',cookie:'session=opaque-cookie-value',localMemberToken:'opaque-member-value'};
const detail = {error:'creation_window_revision_conflict', reason:'方案修订已变化，请读取新版。',repair_action:'读取原请求后修订窗口。',
 stage:'window_confirmation',request_id:'request-a',window_id:'window-a',current_window_id:'window-b',plan_id:'plan-a',expected_revision:2,current_revision:3,
 expected_input_fingerprint:'a'.repeat(64),current_input_fingerprint:'b'.repeat(64),
 raw_response:'private-body',path:'/Users/private/file',authorization:credentials.authorization};
const base = {apiBase:'http://fixture.invalid',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials:()=>credentials,getSignal:()=>undefined};
const capture = async request => {try{await request();return {unexpected_success:true};}
 catch(error){return {name:error.constructor.name,message:error.message,failure:error.businessFailure};}};
const output = {transport:{}};
for(const [name,status,payload] of [
 ['conflict',409,detail],['unknown_503',503,{...detail,error:'auth_dependency_unavailable',repair_action:'直接重试'}],
 ['known_503',503,{...detail,error:'input_dependency_unavailable',submission_effect:'rejected'}],
 ['unsafe',409,{...detail,reason:`已拒绝 ${credentials.authorization} ${credentials.cookie} ${credentials.localMemberToken} /Users/private/file https://upstream.invalid/private`,
   repair_action:'读取原请求 /srv/private/file',request_id:'/Users/private',query_url:'https://upstream.invalid/private',
   current_revision:true,current_input_fingerprint:'not-a-digest'}]]) {
 const request=createBusinessRequest({...base,fetchRequest:async()=>Response.json({detail:payload},{status})});
 output.transport[name]=await capture(()=>request('/api/creation-requests/request-a/windows/window-a/confirm',{method:'POST',body:'{}'}));
}
for(const name of ['connection','response_decode','pre_aborted','offline']) {
 let calls=0;const controller=new AbortController();if(name==='pre_aborted')controller.abort();
 const request=createBusinessRequest({...base,...(name==='offline'?{apiBase:undefined}:{}),getSignal:()=>controller.signal,
  fetchRequest:async()=>{calls++;if(name==='response_decode')return {ok:true,json:async()=>{throw new Error('private response');}};
   throw new Error(`private disconnect ${credentials.authorization} /Users/private/file`);}});
 output.transport[name]={...await capture(()=>request('/api/creation-tasks/task-a/edits/exact',{method:'POST',body:JSON.stringify({request_id:'request-write'})})),calls};
}
const initial=createBusinessRequest({...base,fetchRequest:async()=>{throw new Error('response lost');}});
output.transport.initialUnknown=await capture(()=>initial('/api/creation-requests',{method:'POST',body:JSON.stringify({request_key:'11111111-1111-4111-8111-111111111111'})}));
let preflightCalls=0;const preflightReceipts=[];
const preflight=createBusinessRequest({...base,taskId:'task-a',selectedTaskVersion:{timeline_version_id:'old',timeline_sequence:1},
 onFailure:failure=>preflightReceipts.push(failure),
 fetchRequest:async()=>{preflightCalls++;return Response.json({detail:{...detail,error:'auth_dependency_unavailable',reason:'当前身份服务不可用。'}},{status:503});}});
output.transport.preflight={...await capture(()=>preflight('/api/creation-tasks/task-a/edits/exact',{method:'POST',body:JSON.stringify({request_id:'original-write'})})),calls:preflightCalls,receipts:preflightReceipts};
const environment={AI_VIDEO_DIARY_AGENT_API_BASE:base.apiBase,AI_VIDEO_DIARY_AGENT_TASK_ID:'task-a',
 AI_VIDEO_DIARY_AGENT_STAGE:'montage',AI_VIDEO_DIARY_AGENT_TASK_READ_READY:'1',AI_VIDEO_DIARY_AGENT_TURN_ID:'turn-a',
 AI_VIDEO_DIARY_AGENT_INPUT_ID:'input-a',AI_VIDEO_DIARY_AGENT_AUTHORIZATION:credentials.authorization,
 AI_VIDEO_DIARY_AGENT_COOKIE:credentials.cookie,AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN:credentials.localMemberToken};
const tools=new Map();await createDiaryTools(environment).register({registerTool:tool=>tools.set(tool.name,tool)});
globalThis.fetch=async()=>Response.json({detail},{status:409});
output.caught=await tools.get('resume_creation_execution').execute('caught',{});
const pending=new Map();globalThis.fetch=(url)=>new Promise(resolve=>pending.set(new URL(url).pathname,resolve));
const a=tools.get('read_creation_execution').execute('a',{job_id:'job-a'});
const b=tools.get('read_storyboard_regeneration').execute('b',{request_id:'storyboard-b'});
pending.get('/api/creation-tasks/task-a/montage/plan/requests/storyboard-b')(Response.json({detail:{...detail,error:'storyboard_conflict',request_id:'storyboard-b'}},{status:409}));
pending.get('/api/creation-tasks/task-a/execution')(Response.json({detail:{...detail,error:'execution_conflict',request_id:'execution-a'}},{status:409}));
output.parallel=await Promise.all([a,b]);
const localTools=new Map();let localCalls=0;
globalThis.fetch=async()=>{localCalls++;throw new Error('must not contact HTTP');};
await createDiaryTools({...environment,AI_VIDEO_DIARY_AGENT_TASK_READ_READY:'0'}).register({registerTool:tool=>localTools.set(tool.name,tool)});
output.local={result:await localTools.get('regenerate_storyboard').execute('local',{}),calls:localCalls};
const scriptTools=new Map(),script={title:'总标题😀',sections:[{title:'段落',content:'有据的说明。',visual_prompt:'展示画面',evidence_reference_ids:['source-a']}]};
const scriptLocation={candidate_section_index:0,candidate_field:'title',candidate_fragment_index:0,
 candidate_field_digest:createHash('sha256').update(script.title,'utf8').digest('hex')};
const scriptIssues=[{error:'script_goal_conflict',reason_code:'goal_conflict',section_index:1,...scriptLocation,
 review_explanation:'总标题与原用户要求冲突。'},
 {error:'script_fact_unsupported',reason_code:'unsupported_fact',section_index:1,candidate_section_index:1,
  candidate_field:'content',candidate_fragment_index:0,candidate_field_digest:createHash('sha256').update(script.sections[0].content,'utf8').digest('hex'),
  review_explanation:`正文承诺缺少资料支持。 ${credentials.authorization} ${credentials.cookie} ${credentials.localMemberToken} /Users/private/file`,raw_response:'private-nested-response'},
 {error:'script_fact_unsupported',reason_code:'unsupported_fact',section_index:1,candidate_section_index:1,
  candidate_field:'content',candidate_fragment_index:0,candidate_field_digest:createHash('sha256').update('old field','utf8').digest('hex'),
  review_explanation:'private-stale-finding'}];
globalThis.fetch=async(url)=>url.endsWith('/script-context')?Response.json({creation_intent:{kind:'discussion'}})
 :Response.json({detail:{error:'script_goal_conflict',reason_code:'goal_conflict',section_index:1,retryable:false,
  ...scriptLocation,review_issues:scriptIssues,reason:'总标题与原用户要求冲突。',repair_action:'核对当前送审标题后修改冲突表述。'}},{status:422});
await createDiaryTools(environment).register({registerTool:tool=>scriptTools.set(tool.name,tool)});
output.scriptRejection=await scriptTools.get('preview_script_draft').execute('script-call',script);
output.completeReview=safeBusinessFailure({error:'script_fact_unsupported',section_index:25,
 candidate_section_index:25,candidate_field:'content',candidate_fragment_index:0,candidate_field_digest:'a'.repeat(64),
 review_issues:Array.from({length:13},(_,i)=>({...scriptIssues[1],section_index:i+21,candidate_section_index:i+21,
   review_explanation:'待核对资料中的完整问题。'}))});
// A failure before POST must not consume the one-write guard; an unknown POST must.
const execution=new Map();let marked=0,mode='not_ready',posts=0,turnId;
registerExecutionTools({registerTool:tool=>execution.set(tool.name,tool)},{taskId:'task-a',isTaskRead:()=>true,
 hasCreationAction:()=>marked>0,markCreationAction:()=>marked++,hasEditControlWrite:()=>false,hasPreview:()=>false,
 readActionSource:()=>({turnId}),getSignal:()=>undefined,readCurrentTaskFacts:async()=>({}),typedRequestId:()=> 'request-a',
 request:async(path,init)=>{if(init?.method==='POST'){if(mode==='preflight_refused')throw new RequestNotSubmitted('版本核验失败，未发出POST');posts++;throw new Error('response disconnected');}
   if(path.endsWith('/preview'))return {requirements:{can_continue:mode!=='not_ready',continuation_blocker:'缺少有效文案。'},input_digest:'digest'};
   return {job:null,video_ready:false};}});
const start=execution.get('start_creation_execution');
const missingTurn=await start.execute();const missingTurnMarked=marked;
turnId='trusted-turn';const notReady=await start.execute();const notReadyMarked=marked;
mode='preflight_refused';await start.execute();const preflightMarked=marked;
mode='ready';const unknown=await start.execute();const afterUnknown=marked;
await start.execute();
output.execution={missingTurn,missingTurnMarked,notReady,notReadyMarked,preflightMarked,unknown,afterUnknown,posts};
const concurrent=new Map();let rejectPost,concurrentPosts=0,concurrentMarked=0;
registerExecutionTools({registerTool:tool=>concurrent.set(tool.name,tool)},{taskId:'task-a',isTaskRead:()=>true,
 hasCreationAction:()=>concurrentMarked>0,markCreationAction:()=>concurrentMarked++,hasEditControlWrite:()=>false,hasPreview:()=>false,
 readActionSource:()=>({turnId:'trusted-turn'}),getSignal:()=>undefined,readCurrentTaskFacts:async()=>({}),typedRequestId:()=> 'request-a',
 request:async()=>{concurrentPosts++;return new Promise((_resolve,reject)=>{rejectPost=reject;});}});
const firstPost=concurrent.get('resume_creation_execution').execute();
const duplicate=await concurrent.get('cancel_creation_execution').execute();
rejectPost(new Error('response disconnected'));await firstPost;
output.concurrentExecution={posts:concurrentPosts,marked:concurrentMarked,duplicate};
console.log(JSON.stringify(output));
"""


@pytest.fixture(scope="module")
def tool_boundary():
    env = subprocess_environment({key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ})
    completed = subprocess.run(
        [os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", "--input-type=module", "-e", RUNNER, Path(__file__).as_uri()],
        text=True, capture_output=True, check=True, timeout=15, env=env,
    )
    return json.loads(completed.stdout)


def _project(result, tool_name="resume_creation_execution"):
    return _PiStreamProjector().project(json.dumps({"type": "tool_execution_end", "toolName": tool_name,
        "toolCallId": "call-a", "isError": result["isError"], "result": result}))


@pytest.mark.parametrize("case,effect,stage", [
    ("conflict", "rejected", "window_confirmation"),
    ("unknown_503", "unknown", "window_confirmation"),
    ("known_503", "rejected", "window_confirmation"),
    ("connection", "unknown", "connection"),
    ("response_decode", "unknown", "response_decode"),
    ("pre_aborted", "not_submitted", "connection"),
    ("offline", "not_submitted", "connection"),
    ("preflight", "not_submitted", "task_version_preflight"),
])
def test_transport_distinguishes_rejection_submission_and_unknown(tool_boundary, case, effect, stage):
    result = tool_boundary["transport"][case]
    assert result["failure"]["submission_effect"] == effect
    assert result["failure"]["stage"] == stage
    assert result["failure"]["request_id"] in {"request-a", "request-write", "original-write"}
    if case in {"pre_aborted", "offline"}:
        assert result["calls"] == 0
    if case == "preflight":
        assert result["calls"] == 1
        assert result["failure"]["error"] == "auth_dependency_unavailable"
        assert result["failure"]["reason"] == "当前身份服务不可用。"
        assert result["failure"]["current_revision"] == 3
        assert result["receipts"] == [result["failure"]]
        assert "读取原请求后修订窗口" in result["failure"]["repair_action"]
    if effect == "unknown":
        assert "先查询原请求" in result["failure"]["repair_action"]
        assert result["name"] == "BusinessResultUnknown"


def test_unsafe_response_fields_and_credentials_never_enter_receipt(tool_boundary):
    result = tool_boundary["transport"]["unsafe"]
    text = json.dumps(result, ensure_ascii=False)
    for private in ("opaque-auth-value", "opaque-cookie-value", "opaque-member-value", "/Users/private", "/srv/private", "upstream.invalid", "private-body"):
        assert private not in text
    assert result["failure"]["request_id"] == "request-a"
    assert "current_revision" not in result["failure"]
    assert "current_input_fingerprint" not in result["failure"]
    assert "raw_response" not in result["failure"]


def test_initial_post_unknown_keeps_request_key_without_inventing_server_id(tool_boundary):
    failure = tool_boundary["transport"]["initialUnknown"]["failure"]
    assert failure["submission_effect"] == "unknown"
    assert failure["request_key"] == "11111111-1111-4111-8111-111111111111"
    assert failure["query_tool"] == "read_creation_request"
    assert "request_id" not in failure and "query_url" not in failure
    assert _safe_business_failure(failure) == failure
    assert _safe_business_failure({"request_key": "opaque-private-value"}) == {}


def test_caught_http_error_reaches_model_and_projected_event(tool_boundary):
    result = tool_boundary["caught"]
    failure = result["details"]["failure"]
    assert failure["error"] == "creation_window_revision_conflict"
    assert failure["expected_revision"] == 2 and failure["current_revision"] == 3
    assert failure["current_window_id"] == "window-b" and failure["plan_id"] == "plan-a"
    assert json.loads(result["content"][0]["text"])["failure"] == failure
    assert result["structuredContent"]["failure"] == failure
    assert result["details"]["invocation_input_id"] == "input-a"
    event = _project(result)
    assert event["failure"] == failure
    assert "方案修订已变化" in event["summary"]


def test_async_tools_keep_their_own_first_business_receipt(tool_boundary):
    first, second = tool_boundary["parallel"]
    assert first["details"]["failure"]["error"] == "execution_conflict"
    assert first["details"]["failure"]["request_id"] == "execution-a"
    assert second["details"]["failure"]["error"] == "storyboard_conflict"
    assert second["details"]["failure"]["request_id"] == "storyboard-b"


def test_local_precondition_explains_its_actual_reason_without_http(tool_boundary):
    result = tool_boundary["local"]["result"]
    assert tool_boundary["local"]["calls"] == 0
    assert "请先读取当前任务和可信要求" in result["content"][0]["text"]
    event = _project(result, "regenerate_storyboard")
    assert "请先读取当前任务和可信要求" in event["failure"]["reason"]


def test_execution_guard_starts_at_submission_and_retains_unknown_write(tool_boundary):
    result = tool_boundary["execution"]
    assert result["missingTurnMarked"] == result["notReadyMarked"] == result["preflightMarked"] == 0
    assert result["missingTurn"]["details"]["failure"]["submission_effect"] == "not_submitted"
    assert result["notReady"]["details"]["failure"]["submission_effect"] == "not_submitted"
    assert result["afterUnknown"] == 1 and result["posts"] == 1
    concurrent = tool_boundary["concurrentExecution"]
    assert concurrent["posts"] == concurrent["marked"] == 1
    assert concurrent["duplicate"]["details"]["failure"]["submission_effect"] == "not_submitted"


def test_nested_receipt_and_durable_roundtrip_keep_safe_typed_fields(tool_boundary, tmp_path):
    result = tool_boundary["caught"]
    parent = {"type": "tool_execution_end", "toolName": "codemode", "isError": True, "result": {"details": {
        "business_receipts": [{"tool_name": "resume_creation_execution", "tool_call_id": "nested-a", "is_error": True, "result": result["details"]}]}}}
    events = [_PiStreamProjector().project(line) for line in _business_event_lines(json.dumps(parent))]
    event = next(event for event in events if event and "failure" in event)
    event = AssistantStreamEvent.model_validate(event).model_dump(mode="json", exclude_none=True)
    repo = ConversationRepository(tmp_path / "conversation.sqlite")
    conversation = repo.current_or_create("workspace", "member", "home", "home")
    draft = repo.save_draft(conversation["id"], "workspace", "member", "制作视频", 0)
    turn, created = repo.create_turn_idempotent(conversation["id"], "workspace", "member", "制作视频", "request-one", draft["draft_version"])
    assert created
    repo.append_event(turn["id"], event)
    stored = repo.get_turn(turn["id"])["events"][0]
    assert stored["failure"] == result["details"]["failure"]
    assert type(stored["failure"]["current_revision"]) is int


@pytest.mark.parametrize("receipt_kind", ["business", "sdk_validation", "script_locator"])
def test_actual_stream_adapter_persists_the_projected_business_receipt(tool_boundary, tmp_path, monkeypatch, receipt_kind):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.main as main

    repo = ConversationRepository(tmp_path / "stream.sqlite")
    conversation = repo.current_or_create("workspace", "member", "home", "home")
    draft = repo.save_draft(conversation["id"], "workspace", "member", "制作视频", 0)
    turn, _ = repo.create_turn_idempotent(conversation["id"], "workspace", "member", "制作视频", "request-one", draft["draft_version"])
    monkeypatch.setattr(main, "conversation_repo", repo)
    monkeypatch.setattr(main, "_workspace_settings_for", lambda _workspace: SimpleNamespace(effective_llm_api_key="synthetic-secret"))
    event = (_project(tool_boundary["caught"]) if receipt_kind == "business"
        else _project(tool_boundary["scriptRejection"], "preview_script_draft") if receipt_kind == "script_locator"
        else _project({"isError": True, "details": {}, "content": [{"type": "text", "text":
            'Validation failed for tool "preview_creation_plan":\n'
            '  - selected_person.profile_id: must have required properties profile_id, version\n\nReceived arguments:\n'
            '{"private":"/Users/private/reference.wav","token":"private-token"}'}]}, "preview_creation_plan"))
    app = FastAPI()

    @app.get("/stream")
    def stream():
        def execute(publish):
            publish(event)
            return SimpleNamespace(model_dump=lambda mode: {"reply": "先读取原请求。"})
        return main._assistant_stream_transport().response(execute, workspace_id="workspace", turn_id=turn["id"])

    response = TestClient(app).get("/stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    delivered = next(event for event in events if event["type"] == "tool_end")
    assert delivered["failure"] == event["failure"]
    assert repo.get_turn(turn["id"])["events"][0]["failure"] == delivered["failure"]
    if receipt_kind == "sdk_validation":
        assert delivered["failure"]["submission_effect"] == "not_submitted"
        assert delivered["failure"]["tool_name"] == "preview_creation_plan"
        assert "private" not in json.dumps(events)
    elif receipt_kind == "script_locator":
        assert delivered["failure"]["candidate_section_index"] == delivered["failure"]["candidate_fragment_index"] == 0
        assert delivered["failure"]["candidate_field"] == "title"
        assert delivered["failure"]["reason"].startswith("本次未保存送审稿的总标题第1个片段（第1–4个Unicode字符）与制作要求冲突。")
        assert "总标题与原用户要求冲突。" in delivered["failure"]["reason"]
        assert "定位指向本次提交但尚未保存的新稿，不是已保存旧稿" in delivered["failure"]["repair_action"]
        assert "核对当前送审标题后修改冲突表述。" in delivered["failure"]["repair_action"]
        assert "private-model-explanation" not in json.dumps(events) and "private-response-body" not in json.dumps(events)
        assert len(delivered["failure"]["review_issues"]) == 2
        assert [issue["candidate_field"] for issue in delivered["failure"]["review_issues"]] == ["title", "content"]
        assert "private-stale-finding" not in json.dumps(events) and "private-nested-response" not in json.dumps(events)


@pytest.mark.parametrize("details", [None, {}])
def test_sdk_validation_before_execute_retains_only_safe_field_diagnostics(details):
    raw = ('Validation failed for tool "preview_creation_plan":\n'
        '  - selected_person.profile_id: must have required properties profile_id, version\n'
        '  - duration_target_seconds: must be number\n'
        '  - selected_person: must be null\n'
        '  - selected_person: must match a schema in anyOf\n\nReceived arguments:\n'
        '{"path":"/Users/private/reference.wav","token":"opaque-token","nested":"- stolen: must be number"}')
    event = _project({"isError": True, "details": details, "content": [{"type": "text", "text": raw}]}, "preview_creation_plan")
    assert event["ok"] is False
    assert event["failure"] == {
        "error": "tool_arguments_invalid", "tool_name": "preview_creation_plan", "stage": "parameter_validation",
        "submission_effect": "not_submitted", "repair_action": "按工具参数契约补齐或修正上述字段后再提交；本次工具未执行。",
        "reason": "参数校验未通过，工具未执行。selected_person.profile_id: must have required properties profile_id, version；duration_target_seconds 必须使用数字。"}
    assert "selected_person.profile_id" in event["summary"]
    assert "工具未执行" in event["summary"]
    for raw_value in ("Received arguments", "opaque-token", "private", "stolen", "must be null", "anyOf"):
        assert raw_value not in json.dumps(event, ensure_ascii=False)


@pytest.mark.parametrize("raw,details", [
    ('ordinary model failure: opaque-private-value', {}),
    ('Validation failed for tool "other_tool":\n  - gain_db: must be number', {}),
    ('Validation failed for tool "preview_creation_plan":\nopaque-private-value', {}),
    ('Validation failed for tool "preview_creation_plan":\n  - gain_db: must be number', {}),
    ('Validation failed for tool "preview_creation_plan":\n  - /Users/private/key: must be number', {}),
    ('Validation failed for tool "preview_creation_plan":\n  - gain_db: must be number', {"kind": "business_result"}),
])
def test_sdk_validation_requires_matching_prefix_empty_details_and_field_lines(raw, details):
    event = _project({"isError": True, "details": details, "content": [{"type": "text", "text": raw}]}, "preview_creation_plan")
    assert "failure" not in event
    assert "opaque-private-value" not in json.dumps(event) and "/Users/private" not in json.dumps(event)


def test_sdk_validation_omits_unknown_validation_text():
    raw = ('Validation failed for tool "preview_creation_plan":\n'
        '  - selected_person.profile_id: opaque-private-value from an unknown validator\n\nReceived arguments:\n'
        '{"token":"another-private-value"}')
    event = _project({"isError": True, "details": {}, "content": [{"type": "text", "text": raw}]}, "preview_creation_plan")
    assert event["failure"]["reason"] == "参数校验未通过，工具未执行。"
    assert event["failure"]["submission_effect"] == "not_submitted"
    assert "private-value" not in json.dumps(event)


@pytest.mark.parametrize("problem,explanation", [
    ("missing", "limit 为必填参数"),
    ("expected number, got string", "limit 必须使用数字"),
    ("expected array, got object", "limit: expected array"),
])
def test_pi_schema_failure_preserves_safe_repair_fields(problem, explanation):
    raw = (f'Validation failed for tool "preview_creation_plan":\n  - limit: {problem}'
        '\n\nReceived arguments:\n{"token":"private-token","path":"/Users/private/file"}')
    event = _project({"isError": True, "details": {}, "content": [{"type": "text", "text": raw}]}, "preview_creation_plan")
    assert explanation in event["failure"]["reason"]
    assert event["failure"]["submission_effect"] == "not_submitted"
    assert "private" not in json.dumps(event)


def test_projection_whitelist_does_not_accept_raw_or_wrongly_typed_fields():
    safe = _safe_business_failure({"reason": "身份服务暂不可用。 /Users/private/a token=opaque", "repair_action": "读取原请求。",
        "error": "auth_dependency_unavailable", "stage": "task_version_preflight", "submission_effect": "not_submitted",
        "request_id": "request-one", "current_revision": True, "expected_revision": -1, "http_body": "private",
        "query_url": "https://private.invalid", "expected_input_fingerprint": "bad", "raw_error": "private"})
    assert safe["error"] == "auth_dependency_unavailable"
    assert "private" not in json.dumps(safe) and "opaque" not in json.dumps(safe)
    assert set(safe) == {"reason", "repair_action", "error", "stage", "submission_effect", "request_id"}
    assert _safe_business_failure({"tool_name": ["preview_creation_plan"]}) == {}
    assert _safe_business_failure({"tool_name": "unknown_tool"}) == {}


@pytest.mark.parametrize("invalid", [
    {"candidate_section_index": True}, {"candidate_section_index": -1}, {"candidate_section_index": 21},
    {"candidate_section_index": 2}, {"candidate_section_index": 0, "candidate_field": "content"},
    {"candidate_field": ["content"]}, {"candidate_field": "unknown"}, {"candidate_fragment_index": 20},
    {"candidate_field": "title", "candidate_fragment_index": 1}, {"candidate_field_digest": "private-key"},
    {"candidate_fragment_index": None}, {"section_index": True}, {"error": "script_review_invalid"},
])
def test_projection_drops_invalid_or_partial_script_location_atomically(invalid):
    failure = {"error": "script_fact_unsupported", "section_index": 1, "candidate_section_index": 1,
        "candidate_field": "content", "candidate_fragment_index": 0, "candidate_field_digest": "a" * 64, **invalid}
    safe = _safe_business_failure(failure)
    assert not any(key.startswith("candidate_") for key in safe)


def test_verified_script_location_reaches_model_and_projection_without_free_review_text(tool_boundary):
    import hashlib

    result = tool_boundary["scriptRejection"]
    failure = result["details"]["failure"]
    assert failure["candidate_field_digest"] == hashlib.sha256('总标题😀'.encode('utf-8')).hexdigest()
    assert failure["candidate_section_index"] == failure["candidate_fragment_index"] == 0
    assert failure["candidate_field"] == "title"
    assert failure["section_index"] == 1
    assert json.loads(result["content"][0]["text"]) == failure
    assert result["structuredContent"]["failure"] == failure
    assert _project(result, "preview_script_draft")["failure"] == failure
    assert "private-model-explanation" not in json.dumps(result) and "private-response-body" not in json.dumps(result)
    assert len(failure["review_issues"]) == 2
    assert failure["review_issues"][0]["review_explanation"] == '总标题与原用户要求冲突。'
    complete = tool_boundary["completeReview"]
    assert complete["candidate_section_index"] == 25
    assert [issue["section_index"] for issue in complete["review_issues"]] == list(range(21, 34))
    assert _safe_business_failure(complete) == complete
    for secret in ('opaque-auth-value', 'opaque-cookie-value', 'opaque-member-value', '/Users/private', 'private-stale-finding', 'private-nested-response'):
        assert secret not in json.dumps(result)


def test_review_issue_projection_keeps_only_bounded_validated_data_and_redacts_known_secrets():
    issue = {"error": "script_fact_unsupported", "reason_code": "unsupported_fact", "section_index": 1,
        "candidate_section_index": 1, "candidate_field": "content", "candidate_fragment_index": 0,
        "candidate_field_digest": "a" * 64,
        "review_explanation": "这是待核对数据。opaque-local-secret /Users/private/file Bearer hidden-value",
        "raw_response": "private-response-body"}
    failure = {"error": "script_fact_unsupported", "review_issues": [issue,
        {**issue, "candidate_field_digest": "invalid", "review_explanation": "private-invalid-location"},
        {**issue, "reason_code": "unknown", "review_explanation": "private-invalid-category"},
        {**issue, "review_explanation": "x" * 301}]}
    safe = _safe_business_failure(failure, secrets=("opaque-local-secret",))
    assert len(safe["review_issues"]) == 1
    assert "这是待核对数据" in safe["review_issues"][0]["review_explanation"]
    assert set(safe["review_issues"][0]) == set(issue) - {"raw_response"}
    for secret in ('opaque-local-secret', '/Users/private', 'hidden-value', 'private-response-body', 'private-invalid-location', 'private-invalid-category'):
        assert secret not in json.dumps(safe)
    unicode_issue = {**issue, "review_explanation": '😀' * 300}
    assert len(_safe_business_failure({"error": "script_fact_unsupported", "review_issues": [unicode_issue]})["review_issues"][0]["review_explanation"]) == 300
    assert len(_safe_business_failure({"error": "script_fact_unsupported", "review_issues": [issue] * 13})["review_issues"]) == 13
    for invalid_list in ([],):
        assert "review_issues" not in _safe_business_failure({"error": "script_fact_unsupported", "review_issues": invalid_list})
    assert "review_issues" not in _safe_business_failure({"error": "script_review_invalid", "review_issues": [issue]})
