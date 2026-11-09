"""Shipped tools and installed QuickJS, with HTTP doubles and no model/media calls."""
from tool_result_test_fixtures import TOOL_OUTCOME_JS

from assistant_sdk_fixtures import project_event_fixture
import json
import os
from app.subprocess_environment import subprocess_environment
import re
from pathlib import Path
from uuid import UUID
import subprocess

import pytest

from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS
import app.assistant_pi as host


@pytest.mark.parametrize('status', ['not_ready', 'authorization_ready', None])
def test_edit_and_restore_projection_keep_the_saved_authorization_receipt(status):
    from app.assistant.result_projection import project_business_result, timeline_restore_message
    readiness = {'status': status, 'reason_code': 'task_authorization_expired' if status == 'not_ready' else None,
        'device_admission': 'not_checked'} if status else None
    applied = {'kind': 'edit_applied', 'result': {'execution_readiness': readiness}}
    answer, result = project_business_result({'answer': '成片已经完成', 'status': 'settled', 'tool_results': [
        {'type': 'tool_execution_end', 'toolName': 'apply_typed_edit', 'isError': False, 'result': {'details': applied}}]})
    assert result is applied or result == applied
    assert result['result']['execution_readiness'] == readiness
    restored = timeline_restore_message({'receipt': {'status': 'applied', 'execution_readiness': readiness}})
    for message in (answer, restored):
        assert '保存' in message
        assert '本次保存时' in message if status else '后台执行授权' not in message
        if status == 'not_ready':
            assert '未就绪' in message and '原授权已过期' in message
        if status == 'authorization_ready':
            assert '设备准入尚未检查' in message
        assert '成片已经完成' not in message


def test_task_business_restore_transmits_the_same_nullable_readiness_receipt():
    from types import SimpleNamespace
    from app.assistant.task_business import TaskBusiness
    original = {'status': 'not_ready', 'reason_code': 'task_authorization_expired', 'device_admission': 'not_checked'}
    result = SimpleNamespace(operation={'id': 'inverse'}, montage=None, narration=None,
        restore_request={'execution_readiness': original}, execution_readiness=original)
    case = object.__new__(TaskBusiness)
    case.timeline_restores = SimpleNamespace(execute=lambda *args, **kwargs: result)
    value = case.execute('restore_timeline', {'action': 'undo', 'payload': {
        'request_id': '00000000-0000-4000-8000-000000000001', 'expected_timeline_version_id': 'version',
        'expected_timeline_sequence': 1}}, None, SimpleNamespace(workspace_id='w', id='task'),
        {'current_turn_id': 'turn'}, {}, None, None)
    assert value['execution_readiness'] is original
    assert value['restore_request']['execution_readiness'] is original


def test_segment_revision_projection_retains_only_the_current_invocation_and_real_adoption():
    from app.assistant.result_projection import project_business_result
    original = {'kind': 'segment_revision', 'action': 'submit', 'request_id': 'old-request',
        'invocation_input_id': 'old-input', 'revision': {'status': 'succeeded', 'adoption': {'status': 'current'}}}
    current = {'kind': 'segment_revision', 'action': 'query', 'request_id': 'original-request',
        'invocation_input_id': 'new-input', 'revision': {'status': 'succeeded', 'adoption': {'status': 'invalidated'}}}
    events = [{'type': 'tool_execution_end', 'toolName': 'revise_narration_segment', 'isError': False,
        'result': {'details': value}} for value in (current, original)]
    answer, result = project_business_result({'answer': '已经完成整片', 'status': 'settled',
        'input_id': 'new-input', 'tool_results': events})
    assert result == current
    assert '历史' in answer and '当前作品未采用' in answer
    assert '已经完成整片' not in answer


def test_segment_revision_resolver_reads_the_authorized_original_and_never_infers_non_submission_from_absence():
    from app.assistant.result_projection import resolve_segment_revision
    details = {'kind': 'segment_revision', 'action': 'submit', 'request_id': 'original-request',
        'revision': {'adoption': {'status': 'current'}}}
    actual = {'task_id': 'task', 'request_id': 'original-request', 'input_id': 'input',
        'job_id': 'original-job', 'status': 'failed', 'adoption': {'status': 'candidate'},
        'recovery': {'action': 'resume_original'}}
    result = resolve_segment_revision(details, task_id='task', input_id='input', read_original=lambda request_id: actual)
    assert result['revision'] is actual
    with pytest.raises(ValueError, match='可信输入'):
        resolve_segment_revision(details, task_id='task', input_id='new-input', read_original=lambda request_id: actual)
    def missing(request_id):
        assert request_id == 'original-request'
        raise KeyError(request_id)
    unknown = resolve_segment_revision(details, task_id='task', input_id='input', read_original=missing)
    assert unknown['revision'] is None and unknown['request_id'] == 'original-request'
    assert unknown['failure']['submission_effect'] == 'unknown'


def test_original_receipt_refresh_reuses_saved_locators_without_mutating_the_turn_or_replaying():
    from app.assistant.result_projection import refresh_original_receipts
    turn = {'id': 'old-turn', 'result': {'kind': 'task_turn', 'response': {
        'segment_revision': {'kind': 'segment_revision', 'action': 'submit', 'request_id': 'original-segment', 'revision': None,
            'failure': {'reason': '响应丢失', 'submission_effect': 'unknown'}},
        'timeline_restore': {'kind': 'timeline_restore_request', 'action': 'undo', 'request_id': 'original-restore', 'receipt': None,
            'failure': {'reason': '响应丢失', 'submission_effect': 'unknown'}}}}}
    before = json.loads(json.dumps(turn))
    calls = []
    def read_segment(request_id):
        calls.append(request_id)
        return {'task_id': 'task', 'request_id': request_id, 'input_id': 'original-input', 'status': 'succeeded',
            'adoption': {'status': 'superseded'}}
    def read_restore(request_id):
        calls.append(request_id)
        return {'task_id': 'task', 'request_id': request_id, 'action': 'undo', 'status': 'applied'}
    refreshed = refresh_original_receipts(turn, task_id='task', input_id='different-current-input',
        read_segment=read_segment, read_restore=read_restore)
    assert turn == before
    assert calls == ['original-segment', 'original-restore']
    assert refreshed['result']['response']['segment_revision']['revision']['adoption']['status'] == 'superseded'
    assert refreshed['result']['response']['timeline_restore']['receipt']['status'] == 'applied'
    assert 'failure' not in refreshed['result']['response']['segment_revision']
    assert 'failure' not in refreshed['result']['response']['timeline_restore']

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = TOOL_OUTCOME_JS + r"""
let raw=''; for await (const part of process.stdin) raw+=part;
const input=JSON.parse(raw), tools=new Map(), calls=[], results=[]; let failedPosts=0;
globalThis.fetch=async(url,init)=>{
 const path=new URL(url).pathname, body=init?.body?JSON.parse(init.body):null;
 calls.push({path,query:new URL(url).search,method:init?.method||'GET',body,token:init?.headers?.get('X-Local-Member-Token')});
 if(input.delay_ms){await new Promise(resolve=>setTimeout(resolve,input.delay_ms));if(init?.signal?.aborted)throw new Error('fixture call cancelled');}
 let value=input.workspace;
 if(path.endsWith('/assistant/edit-capabilities'))value=input.capabilities;
 if(path.startsWith('/api/assistant/profile-references/'))value=input.reference_data[path];
 if(path.includes('/candidates'))value=[];
 if(path.endsWith('/execution'))value=new URL(url).searchParams.has('job_id')?input.execution:(input.execution_before||{task_id:'task-current',job:null});
 if(path.endsWith('/execution/preview'))value={script_id:'confirmed-script',input_digest:'a'.repeat(64),requirements:{can_continue:true}};
 if(path.endsWith('/execution/start')||path.endsWith('/execution/resume')){value=input.execution;if(input.lost_receipt)throw new TypeError('fixture reply lost');}
 if(path.endsWith('/knowledge-items/search'))value=input.knowledge;
 if(path.endsWith('/assistant/script-context')&&input.script_context)value=input.script_context;
 if(path.endsWith('/observations')){value=init?.method==='POST'?input.observation_job:input.observations;
  if(init?.method==='POST'&&input.lost_receipt)throw new TypeError('fixture reply lost');}
 if(path.endsWith('/edits/exact/preview')){value=input.preview;if(input.lost_receipt)throw new TypeError('fixture reply lost');}
 if(path.includes('/assistant/edit-requests/')){const phase=new URL(url).searchParams.get('phase');value={request_id:path.split('/').pop(),phase,status:'recorded',result:phase==='observation'?input.observation_job:input.preview};}
 if(path.includes('/media-jobs/'))value=input.job;
 const failed=Boolean(body&&input.failure&&(!input.failure_once||failedPosts++===0));
 return {ok:!failed,status:failed?(input.failure_status||422):200,json:async()=>failed?{detail:'字段或版本拒绝'}:value};
};
process.env.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=JSON.stringify(input.capabilities);
const {createDiaryExtension,typedRequestId}=await import(process.argv[2]);
const api={registerTool:tool=>tools.set(tool.name,tool), getAllTools:()=>[...tools.values()],
 getSettings:()=>({}),appendEntry:()=>{}};
const binding=createDiaryExtension(process.env);await binding.extension(api);
for (const step of input.steps||[]) {
 if(step.rebind){await binding.rebind({...process.env,AI_VIDEO_DIARY_AGENT_SELECTED_REFERENCES:JSON.stringify(step.rebind)});continue;}
 try {const args=step.fromReceipt?{request_id:results[0].result.details.request_id}:(step.args||{});results.push(toolOutcome(await tools.get(step.tool).execute(results.length?'fixture-'+results.length:'fixture',args)));}
 catch(error){results.push({ok:false,error:error.message});}
}
if(input.code){
 const ctx={tools:[...tools.values()],sessionManager:{getBranch:()=>[]},executeTool:async(name,args,options)=>{
  try{const result=await tools.get(name).execute('nested',args,options.signal);
   return {toolCall:{id:'nested-'+calls.length},isError:Boolean(result.isError),result};}
  catch(error){return {toolCall:{id:'failed-'+calls.length},isError:true,result:{content:[{type:'text',text:error.message}]}};}
 }};
 results.push(toolOutcome(await tools.get('codemode').execute('script-fixture',{code:input.code},undefined,undefined,ctx)));
}
console.log(JSON.stringify({results,calls,parameters:tools.get('preview_typed_edit').parameters,requestIdentity:typedRequestId('trusted-turn','fixture')}));
"""


def caps():
    return {"task_id": "task-current", "timeline_version_id": "version-current", "timeline_sequence": 4,
            "schema": CreationTaskExactEditRequest.model_json_schema(),
            "supported_operations": list(EXACT_EDIT_FIELDS),
            "operations": [{"operation_type": operation, "available": True, "reason": None}
                           for operation in EXACT_EDIT_FIELDS], "units": {"source_start_seconds": "seconds"}}


def preview():
    return {"base_version_id": "version-current", "proposal": {
        "id": "proposal-current", "summary": "保留旁白，替换第二镜头", "target_refs": ["clip:second"],
        "operation": {"operation_type": "ReplaceClip"}, "status": "proposed",
        "preview": {"before": {"detail": "原画面"}, "after": {"detail": "新画面"}},
        "invalidated_refs": ["render:old"], "external_effects": []}}


def invoke(tmp_path, *, steps=(), workspace=None, code=None, failure=False, result=None, job=None, pi_module=None, lost_receipt=False, delay_ms=0, knowledge=None, script_context=None, stage="topic", failure_once=False, failure_status=422, observations=None, observation_job=None, execution=None, execution_before=None, references=(), reference_data=None):
    env = subprocess_environment({key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ})
    env.update(AI_VIDEO_DIARY_AGENT_API_BASE="https://fixture.invalid", AI_VIDEO_DIARY_AGENT_TASK_ID="task-current",
               AI_VIDEO_DIARY_AGENT_STAGE=stage, AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="fixture-member",
               AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1", AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="换第二镜头并保留旁白",
               AI_VIDEO_DIARY_AGENT_TURN_ID="trusted-turn",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id": "version-current", "timeline_sequence": 4}),
               AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS="read_current_task,read_task_object,preview_typed_edit")
    env["AI_VIDEO_DIARY_AGENT_SELECTED_REFERENCES"]=json.dumps(references)
    if pi_module:
        env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] = str(pi_module)
    workspace = workspace or {"task": {"id": "task-current", "stage": stage, "current_timeline_version_id": "version-current", "timeline_sequence": 4}}
    runner = tmp_path / "controlled-tools-fixture.mjs"
    runner.write_text(RUNNER)
    completed = subprocess.run([os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types",
                                str(runner), MODULE.as_uri()],
                               input=json.dumps({"steps": steps, "workspace": workspace, "capabilities": caps(),
                                                 "references":references,"reference_data":reference_data,"preview": result or preview(), "job": job, "failure": failure, "code": code, "lost_receipt": lost_receipt, "delay_ms": delay_ms,
                                                 "knowledge": knowledge, "script_context": script_context,
                                                 "failure_once": failure_once, "failure_status": failure_status,
                                                 "observations": observations, "observation_job": observation_job,
                                                 "execution": execution, "execution_before": execution_before}),
                               env=env, cwd=tmp_path, text=True, capture_output=True, timeout=45, check=True)
    return json.loads(completed.stdout)


def test_execution_tools_bind_start_to_host_turn_and_read_original_job(tmp_path):
    actual = {"task_id":"task-current", "job":{"id":"execution:one", "task_id":"task-current", "status":"queued"}, "video_ready":False}
    observed = invoke(tmp_path, stage="montage", execution=actual, steps=[{"tool":"start_creation_execution"},
        {"tool":"read_creation_execution", "args":{"job_id":"execution:one", "wait_seconds":0}}])
    posts = [call for call in observed["calls"] if call["method"] == "POST"]
    assert len(posts) == 1 and posts[0]["path"].endswith('/execution/start')
    assert posts[0]["query"] == '?assistant_turn_id=trusted-turn'
    assert posts[0]["body"]["requirements_confirmed"] is True
    queried = observed["calls"][-1]
    assert queried["path"].endswith('/execution') and queried["query"] == '?job_id=execution%3Aone'
    assert observed["results"][-1]["result"]["details"]["status"] == actual
    assert not any('/media-jobs/' in call["path"] for call in observed["calls"])


def test_execution_unknown_cannot_repost_and_job_switch_cannot_claim_original_success(tmp_path):
    actual = {"task_id":"task-current", "job":{"id":"execution:new", "task_id":"task-current", "status":"succeeded"}, "video_ready":True}
    observed = invoke(tmp_path, stage="montage", execution=actual, lost_receipt=True,
        steps=[{"tool":"start_creation_execution"}, {"tool":"start_creation_execution"},
               {"tool":"read_creation_execution", "args":{"job_id":"execution:old"}}])
    assert len([call for call in observed["calls"] if call["method"] == "POST"]) == 1
    assert observed["results"][0]["result"]["isError"] is True
    assert observed["results"][0]["result"]["details"]["failure"]["reason"] == '业务请求响应中断，写入结果未知。'
    assert 'fixture reply lost' not in json.dumps(observed["results"][0]["result"], ensure_ascii=False)
    assert observed["results"][1]["result"]["isError"] is True
    assert not observed["results"][2]["ok"] and '身份不一致' in observed["results"][2]["error"]


def test_api_schema_declares_model_fields_and_host_binds_http(tmp_path):
    observed = invoke(tmp_path, steps=[{"tool": "preview_typed_edit", "args": {
        "operation_type": "ReplaceClip", "clip_id": "second", "asset_id": "new-asset", "segment_id": "new-segment"}}])
    schema = observed["parameters"]
    actual = caps()["schema"]
    assert schema["properties"]["playback_rate"] == actual["properties"]["playback_rate"]
    assert schema.get("$defs") == actual.get("$defs")
    assert not ({"assistant_turn_id", "expected_timeline_version_id", "expected_timeline_sequence", "request_id"} & schema["properties"].keys())
    post = observed["calls"][-1]
    assert post["path"] == "/api/creation-tasks/task-current/edits/exact/preview"
    assert post["token"] == "fixture-member"
    assert post["body"]["assistant_turn_id"] == "trusted-turn"
    assert post["body"]["expected_timeline_sequence"] == 4
    assert "raw_command" not in post["body"]
    result = observed["results"][0]["result"]
    assert result["structuredContent"] == result["details"]
    assert {key:result["details"][key] for key in json.loads(result["content"][0]["text"])} == json.loads(result["content"][0]["text"])
    actual_preview = result["details"]["preview"]
    assert {key: actual_preview[key] for key in preview()} == preview()
    assert actual_preview["request_id"] == observed["requestIdentity"] == post["body"]["request_id"]
    assert UUID(actual_preview["request_id"]).version == 5


@pytest.mark.parametrize("field", ["mystery", "constructor", "__proto__", "assistant_turn_id", "expected_timeline_sequence", "request_id"])
def test_unknown_or_model_host_fields_never_reach_http(tmp_path, field):
    observed = invoke(tmp_path, steps=[{"tool": "preview_typed_edit", "args": {"operation_type": "TrimClip", field: "forged"}}])
    assert not observed["results"][0]["ok"]
    assert observed["calls"] == []


def test_failed_typed_request_is_not_replayed(tmp_path):
    step = {"tool": "preview_typed_edit", "args": {"operation_type": "TrimClip", "clip_id": "second", "source_start_seconds": 1}}
    observed = invoke(tmp_path, steps=[step, step], failure=True)
    first = observed["results"][0]["result"]
    assert first["isError"] and first["details"]["status"] == "rejected"
    assert "字段或版本拒绝" in first["details"]["failure_reason"]["text"]
    assert not observed["results"][1]["ok"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1


@pytest.mark.parametrize("action", [
    {"operation_type": "EditAudioTimeline", "audio_edit": {"action": "update", "target_id": "music-clip",
        "values": {"gain_db": -9, "fade_out_ms": 1000, "loudness_target_lufs": None}}},
    {"operation_type": "EditAudioTimeline", "audio_edit": {"action": "track", "target_id": "music-track",
        "values": {"muted": False, "duck_under_voice": True}}},
    {"operation_type": "AdjustVisualEffects", "clip_ids": ["second"], "effects": {"exposure": .5, "temperature": -.2}},
])
def test_common_typed_schema_passes_only_explicit_audio_or_visual_patch(tmp_path, action):
    assert CreationTaskExactEditRequest.model_validate(action).model_dump(exclude_unset=True) == action
    actual = preview()
    actual["proposal"]["operation"]["operation_type"] = action["operation_type"]
    seen = invoke(tmp_path, result=actual, steps=[{"tool": "preview_typed_edit", "args": action}])
    post = next(call for call in seen["calls"] if call["method"] == "POST")
    assert post["path"].endswith("/edits/exact/preview")
    assert post["body"] == {**action, "assistant_turn_id": "trusted-turn", "request_id": seen["requestIdentity"],
        "expected_timeline_version_id": "version-current", "expected_timeline_sequence": 4}
    assert seen["parameters"]["$defs"] == caps()["schema"]["$defs"]
    assert seen["results"][0]["result"]["details"]["kind"] == "edit_preview"
    events = [{"type": "tool_execution_end", "toolName": "preview_typed_edit", "isError": False,
        "result": seen["results"][0]["result"]},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "尚未应用"}]}},
        {"type": "agent_settled"}]
    reply, returned = project_event_fixture("\n".join(json.dumps(event) for event in events), "edit_preview")
    assert returned["proposal"]["operation"]["operation_type"] == action["operation_type"]
    assert "尚未应用修改" in reply


@pytest.mark.parametrize("action", [
    {"operation_type": "EditAudioTimeline", "audio_edit": {"action": "update", "target_id": "music-clip",
        "values": {"gain_db": -9, "fade_out_ms": 1000}}},
    {"operation_type": "AdjustVisualEffects", "clip_ids": ["second"], "effects": {"exposure": .5, "contrast": 1.1}},
])
def test_reordered_nested_patch_cannot_repeat_a_rejected_typed_input(tmp_path, action):
    reordered = {key: value for key, value in reversed(list(action.items()))}
    if "audio_edit" in action:
        reordered["audio_edit"] = {"values": dict(reversed(list(action["audio_edit"]["values"].items()))),
            "target_id": "music-clip", "action": "update"}
    else:
        reordered["effects"] = dict(reversed(list(action["effects"].items())))
    seen = invoke(tmp_path, failure=True, steps=[{"tool": "preview_typed_edit", "args": action},
        {"tool": "preview_typed_edit", "args": reordered}])
    assert seen["results"][0]["result"]["details"]["correction_allowed"]
    assert seen["results"][1]["result"]["isError"] is True
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1


def test_parameter_corrections_are_not_capped_at_three_candidates(tmp_path):
    steps = [{"tool": "preview_typed_edit", "args": {"operation_type": "TrimClip",
              "clip_id": "second", "source_start_seconds": position}}
             for position in (1, 2, 3, 4)]
    seen = invoke(tmp_path, steps=[*steps, steps[-1]], failure=True)
    assert all(not item["ok"] and item["result"]["isError"]
               and item["result"]["details"]["status"] == "rejected"
               and item["result"]["details"]["correction_allowed"] for item in seen["results"][:4])
    assert seen["results"][4]["result"]["isError"] is True
    posts = [call for call in seen["calls"] if call["method"] == "POST"]
    assert len(posts) == 4
    assert len({call["body"]["request_id"] for call in posts}) == 4
    assert {call["body"]["expected_timeline_version_id"] for call in posts} == {"version-current"}


def test_known_parameter_rejection_allows_distinct_bounded_correction(tmp_path):
    first = {"tool": "preview_typed_edit", "args": {"operation_type": "TrimClip", "clip_id": "second", "source_start_seconds": 1}}
    correction = {"tool": "preview_typed_edit", "args": {**first["args"], "source_start_seconds": 2}}
    seen = invoke(tmp_path, steps=[first, first, correction], failure=True, failure_once=True)
    assert seen["results"][0]["result"]["details"]["correction_allowed"] is True
    assert seen["results"][1]["result"]["isError"] is True  # Identical input remains sealed.
    assert seen["results"][2]["result"]["details"]["kind"] == "edit_preview"
    posts = [call for call in seen["calls"] if call["method"] == "POST"]
    assert len(posts) == 2 and posts[0]["body"]["request_id"] != posts[1]["body"]["request_id"]


@pytest.mark.parametrize("mode", ["lost_receipt", "auth_rejected", "version_rejected"])
def test_unknown_or_auth_version_denial_does_not_enable_changed_input_replay(tmp_path, mode):
    args = {"operation_type": "TrimClip", "clip_id": "second", "source_start_seconds": 1}
    seen = invoke(tmp_path, steps=[{"tool": "preview_typed_edit", "args": args},
                                 {"tool": "preview_typed_edit", "args": {**args, "source_start_seconds": 2}}],
                  lost_receipt=mode == "lost_receipt", failure=mode != "lost_receipt", failure_status=403 if mode == "auth_rejected" else 409)
    first = seen["results"][0]["result"]["details"]
    assert first["correction_allowed"] is False
    assert first["status"] == ("result_unknown" if mode == "lost_receipt" else "rejected")
    assert seen["results"][1]["result"]["isError"] is True
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1


def test_fine_reads_expose_real_units_and_truthful_truncation(tmp_path):
    workspace = {"task": {"id": "task-current", "current_timeline_version_id": "version-current", "timeline_sequence": 4},
                 "montage": {"clips": [{"id": f"clip-{index}", "asset_id": "asset", "segment_id": "segment",
                                        "cue_id": "cue", "source_start_seconds": 2, "source_end_seconds": 5.6,
                                        "timeline_start_seconds": 10, "timeline_end_seconds": 13, "crop_x": .4,
                                        "playback_rate": 1.2, "transition_in_seconds": .5, "source_reference": "/private/source"}
                                       for index in range(30)], "cues": [{"id": "cue", "text": "文" * 3000, "narration_segment_id": "narration"}]},
                 "narration": {"segments": [{"id": "narration", "text": "声" * 4500, "duration_seconds": 3,
                                              "voice_profile_id": "voice", "sample_rate": 24000}]}}
    observed = invoke(tmp_path, workspace=workspace, steps=[{"tool": "read_current_task"},
                {"tool": "read_task_object", "args": {"kind": "clips", "offset": 24, "limit": 6}}])
    facts = observed["results"][0]["result"]["details"]
    assert facts["clips_truncated"] and facts["clip_count"] == 30
    clip = facts["clips"][0]
    assert clip["source_start_seconds"] == 2 and clip["crop_x"] == .4 and clip["playback_rate"] == pytest.approx(1.2)
    assert clip["cue_text_length"] == 3000 and clip["cue_text_truncated"]
    narration = facts["narration_segments"][0]
    assert narration["duration_seconds"] == 3 and narration["voice_profile_id"] == "voice"
    assert narration["text_truncated"] and narration["text_length"] == 4500
    assert "/private/" not in json.dumps(facts)
    page = observed["results"][1]["result"]["details"]
    assert page["total_count"] == 30 and page["items"][0]["id"] == "clip-24"


def complete_workspace():
    """Actual response shapes, beyond both former summary cutoffs."""
    tracks = [{"id": f"audio-track-{track}", "role": "music", "name": f"音乐{track}", "gain_db": -3,
               "muted": False, "locked": False, "duck_under_voice": True,
               "clips": [{"id": f"audio-{track}-{clip}", "name": "原音", "source_ref": "workspace://audio/source",
                          "source_start_ms": 100, "source_end_ms": 1100, "timeline_start_ms": clip * 1000,
                          "playback_rate": 1, "gain_db": -2, "editor_gain_points": [{"offset_ms": 0, "gain_db": -2}]}
                         for clip in range(30)]} for track in range(10)]
    return {"task": {"id": "task-current", "current_timeline_version_id": "version-current", "timeline_sequence": 4,
                     "confirmed_script_id": "script-current"},
            "montage": {"clips": [], "cues": [], "audio_timeline": {"format_version": 1, "tracks": tracks},
                        "graphic_overlays": [{"id": f"overlay-{index}", "kind": "text", "text": f"标题{index}",
                                              "start_seconds": 0, "end_seconds": 3, "x": .5, "y": .2,
                                              "width": .8, "font_size": 64, "color": "#FFD166", "private_path": "/private/text"}
                                             for index in range(10)]},
            "subtitle_tracks": [{"id": f"subtitle-{index}", "language": "zh-CN", "status": "valid",
                                 "timeline_version_id": "version-current", "updated_at": "2026-10-13T10:00:00Z",
                                 "style": {"font_size": 48, "primary_color": "#FFFFFF"},
                                 "cues": [{"id": f"cue-{index}-{cue}", "text": "字幕", "start_seconds": cue,
                                           "end_seconds": cue + 1, "source_cue_id": f"source-{cue}"} for cue in range(30)]}
                                for index in range(10)],
            "narration": {"segments": [{"id": "narration", "source_section_id": "section-29", "text": "旁白",
                                        "duration_seconds": 1, "source_sha256": "a" * 64, "sample_rate": 24000}]},
            "scripts": [{"id": "script-current", "task_id": "task-current", "selection_status": "confirmed",
                         "updated_at": "2026-10-13T10:00:00Z",
                         "sections": [{"id": f"section-{index}", "title": "正文", "content": "文案", "visual_prompt": "画面",
                                       "evidence_reference_ids": ["knowledge:source:3"]} for index in range(30)]}]}


def test_object_directory_and_reads_reach_audio_and_overlay_tails(tmp_path):
    seen = invoke(tmp_path, workspace=complete_workspace(), steps=[
        {"tool": "read_current_task"},
        {"tool": "read_task_object", "args": {"kind": "audio_tracks", "offset": 8, "limit": 2}},
        {"tool": "read_task_object", "args": {"kind": "audio_clips", "parent_id": "audio-track-9", "offset": 24, "limit": 6}},
        {"tool": "read_task_object", "args": {"kind": "audio_clips", "object_id": "audio-9-29"}},
        {"tool": "read_task_object", "args": {"kind": "graphic_overlays", "object_id": "overlay-9"}},
    ])
    facts, tracks, clips, clip, overlay = [item["result"]["details"] for item in seen["results"]]
    counts = {item["kind"]: item["total_count"] for item in facts["object_catalog"]["collections"]}
    assert counts["audio_tracks"] == counts["graphic_overlays"] == 10 and counts["audio_clips"] == 300
    assert facts["audio_tracks_truncated"] and facts["graphic_overlays_truncated"]
    assert tracks["total_count"] == 10 and [item["id"] for item in tracks["items"]] == ["audio-track-8", "audio-track-9"]
    assert tracks["items"][1]["role"] == "music" and tracks["items"][1]["object_ref"] == "audio:audio-track-9"
    assert tracks["items"][1]["clip_count"] == 30 and tracks["items"][1]["clips_truncated"]
    assert clips["collection_total_count"] == 300 and clips["total_count"] == 30 and not clips["has_more"]
    assert [item["id"] for item in clips["items"]] == [f"audio-9-{index}" for index in range(24, 30)]
    last = clip["items"][0]
    assert last["audio_track_id"] == "audio-track-9" and last["duration_ms"] == 1000
    assert last["units"]["time"] == "milliseconds" and last["editor_gain_points"] == [{"offset_ms": 0, "gain_db": -2}]
    assert last["timeline_sequence"] == 4 and last["revision"] == "version-current"
    assert overlay["items"][0]["object_ref"] == "overlay:overlay-9" and overlay["items"][0]["color"] == "#FFD166"
    assert "/private/" not in json.dumps(seen["results"])


def test_fine_object_reads_include_mix_controls_and_the_existing_picture_effect_clock(tmp_path):
    workspace = complete_workspace()
    audio = workspace["montage"]["audio_timeline"]["tracks"][-1]["clips"][-1]
    audio.update(fade_in_ms=120, fade_out_ms=900, muted=False, locked=False, denoise_db=3,
        loudness_target_lufs=None, envelope_offset_ms=500, envelope_duration_ms=1500)
    workspace["montage"]["clips"] = [{"id": "picture", "source_start_seconds": 0, "source_end_seconds": 1,
        "timeline_start_seconds": 0, "timeline_end_seconds": 1, "fade_envelope_rate": 2,
        "fade_envelope_offset_frames": 15, "fade_envelope_duration_frames": 90,
        "visual_effects": {"exposure": .2, "contrast": 1.1, "saturation": .9, "temperature": -.2,
            "zoom": 1.2, "offset_x": .1, "rotation": 5, "fade_in_seconds": .5}}]
    seen = invoke(tmp_path, workspace=workspace, steps=[
        {"tool": "read_task_object", "args": {"kind": "audio_clips", "object_id": audio["id"]}},
        {"tool": "read_task_object", "args": {"kind": "audio_tracks", "object_id": "audio-track-9"}},
        {"tool": "read_task_object", "args": {"kind": "clips", "object_id": "picture"}},
    ])
    clip, track, picture = [item["result"]["details"]["items"][0] for item in seen["results"]]
    assert {key: clip[key] for key in ("gain_db", "fade_out_ms", "denoise_db", "loudness_target_lufs")} == {
        "gain_db": -2, "fade_out_ms": 900, "denoise_db": 3, "loudness_target_lufs": None}
    assert track["gain_db"] == -3 and track["duck_under_voice"] is True
    assert picture["fade_envelope_rate"] == 2 and picture["fade_envelope_offset_frames"] == 15
    assert picture["fade_envelope_duration_frames"] == 90 and picture["visual_effects"]["exposure"] == .2
    assert picture["units"]["visual_effects"]["exposure"] == "EV"
    assert picture["units"]["fade_envelope_frames"] == "30 fps effect clock"


def test_object_reads_preserve_subtitles_and_route_script_body_to_versioned_windows(tmp_path):
    seen = invoke(tmp_path, workspace=complete_workspace(), steps=[
        {"tool": "read_task_object", "args": {"kind": "subtitle_tracks", "object_id": "subtitle-9"}},
        {"tool": "read_task_object", "args": {"kind": "subtitle_cues", "parent_id": "subtitle-9", "offset": 24, "limit": 6}},
        {"tool": "read_task_object", "args": {"kind": "subtitle_cues", "object_id": "cue-9-29"}},
        {"tool": "read_task_object", "args": {"kind": "script_sections", "object_id": "section-29"}},
        {"tool": "read_task_object", "args": {"kind": "narration_segments", "object_id": "narration"}},
    ])
    track, cues, cue, script, narration = [item["result"]["details"] for item in seen["results"]]
    assert track["collection_total_count"] == 10 and track["items"][0]["cue_count"] == 30
    assert track["items"][0]["object_ref"] == "subtitle-track:subtitle-9"
    assert cues["total_count"] == 30 and cues["items"][-1]["id"] == "cue-9-29"
    assert cue["collection_total_count"] == 300 and cue["items"][0]["object_ref"] == "cue:cue-9-29"
    assert cue["items"][0]["revision_source"] == "parent_subtitle_track_updated_at"
    assert seen["results"][3]["result"]["isError"] is True
    assert "items" not in script
    assert sum(call["path"] == "/api/creation-tasks/task-current" for call in seen["calls"]) == 4
    assert narration["items"][0]["source_section_id"] == "section-29" and narration["items"][0]["revision"] == "a" * 64


@pytest.mark.parametrize("change", ["foreign_task", "stale_version", "missing_object", "missing_parent"])
def test_complete_reads_keep_task_version_and_object_boundaries(tmp_path, change):
    workspace = complete_workspace()
    args = {"kind": "audio_clips", "object_id": "audio-9-29"}
    if change == "foreign_task":
        workspace["task"]["id"] = "other-task"
    elif change == "stale_version":
        workspace["task"]["timeline_sequence"] = 5
    elif change == "missing_object":
        args["object_id"] = "unknown-audio"
    else:
        args = {"kind": "audio_clips", "parent_id": "unknown-track"}
    seen = invoke(tmp_path, workspace=workspace, steps=[{"tool": "read_task_object", "args": args}])
    assert not seen["results"][0]["ok"]
    assert all(call["method"] == "GET" for call in seen["calls"])


def test_non_script_knowledge_reads_a_current_bounded_server_page(tmp_path):
    page = {"items": [{"id": "current-source", "title": "当前资料", "version": "7", "content_hash": "a" * 64,
                       "excerpt": "真实摘要", "matched_terms": ["资料"], "relevance": 1.5}],
            "total_count": 1000, "offset": 995, "limit": 5, "next_offset": None}
    seen = invoke(tmp_path, knowledge=page, steps=[{"tool": "find_knowledge", "args": {"query": "资料", "offset": 995}}])
    facts = seen["results"][0]["result"]["details"]
    assert facts["total_count"] == 1000 and facts["matches"] == page["items"]
    assert facts["scope"] == "current_authorized_knowledge_versions"
    assert [call["path"] for call in seen["calls"]] == [
        "/api/creation-tasks/task-current", "/api/knowledge-items/search"]
    assert "offset=995" in seen["calls"][-1]["query"] and "limit=5" in seen["calls"][-1]["query"]
    assert all(call["path"] != "/api/knowledge-items" for call in seen["calls"])


def test_script_knowledge_remains_current_task_allowed_sources(tmp_path):
    seen = invoke(tmp_path, stage="script", script_context={"sources": [
        {"reference_id": "knowledge:allowed:2", "title": "剪辑资料", "text": "提高剪辑效率", "version": "2"}]},
        steps=[{"tool": "find_knowledge", "args": {"query": "剪辑"}}])
    facts = seen["results"][0]["result"]["details"]
    assert facts["scope"] == "current_task_allowed_sources" and facts["matches"][0]["id"] == "knowledge:allowed:2"
    assert [call["path"] for call in seen["calls"]] == [
        "/api/creation-tasks/task-current", "/api/creation-tasks/task-current/assistant/script-context"]


def test_observation_reads_and_enqueue_bind_actual_current_task_and_no_media(tmp_path):
    observations = {"task_id": "task-current", "timeline_version_id": "version-current", "timeline_sequence": 4,
                    "targets": [{"kind": "final"}], "observations": [], "quality_check": {"stale": False},
                    "media_policy": "local_measurements_and_text_only"}
    job = {"job_id": "observation-job", "result_kind": "observation", "status": "queued", "base_version_id": "version-current"}
    seen = invoke(tmp_path, observations=observations, observation_job=job, steps=[
        {"tool": "read_task_observations"},
        {"tool": "observe_task_output", "args": {"target": {"kind": "final"}, "sample_times_seconds": [1, 2]}}])
    assert seen["results"][0]["result"]["details"] == {**observations,"invocation_input_id":"trusted-turn"}
    result = seen["results"][1]["result"]["details"]
    assert result["media_job"]["status"] == "queued" and result["media_job"]["result_kind"] == "observation"
    post = next(call for call in seen["calls"] if call["method"] == "POST")
    assert post["body"]["expected_timeline_version_id"] == "version-current" and post["body"]["expected_timeline_sequence"] == 4
    assert post["body"]["assistant_turn_id"] == "trusted-turn" and post["body"]["target"] == {"kind": "final"}
    assert UUID(post["body"]["request_id"]).version == 5


def test_distinct_output_observations_are_not_limited_by_a_round_counter(tmp_path):
    job = {"job_id": "observation-job", "result_kind": "observation", "status": "queued"}
    steps = [{"tool": "observe_task_output", "args": {
        "target": {"kind": "final"}, "sample_times_seconds": [point],
    }} for point in (1, 2, 3, 4)]
    seen = invoke(tmp_path, observation_job=job, steps=[*steps, steps[-1]])
    assert all(result["ok"] and not result["result"].get("isError") for result in seen["results"][:4])
    duplicate = seen["results"][4]
    assert not duplicate["ok"] or duplicate["result"]["isError"]
    assert "相同观察" in json.dumps(duplicate, ensure_ascii=False)
    posts = [call for call in seen["calls"] if call["method"] == "POST"]
    assert len(posts) == 4
    assert len({call["body"]["request_id"] for call in posts}) == 4
    assert [call["body"]["sample_times_seconds"] for call in posts] == [[1], [2], [3], [4]]


def test_unknown_observation_receipt_is_queried_without_replaying(tmp_path):
    job = {"job_id": "observation-job", "result_kind": "observation", "status": "queued"}
    request = {"tool": "observe_task_output", "args": {"target": {"kind": "final"}}}
    seen = invoke(tmp_path, observation_job=job, lost_receipt=True, steps=[request, request,
        {"tool": "read_task_observation_request", "fromReceipt": True}])
    result = seen["results"][0]["result"]["details"]
    assert result["status"] == "result_unknown" and result["phase"] == "observation"
    assert result["assistant_turn_id"] == "trusted-turn" and result["tool_call_id"] == "fixture"
    assert result["base_version_id"] == "version-current" and result["base_sequence"] == 4
    blocked = seen["results"][1]
    assert not blocked["ok"] or blocked["result"]["isError"]
    assert "尚待核验" in json.dumps(blocked, ensure_ascii=False)
    assert seen["results"][2]["result"]["details"]["media_job"] == job
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1


def installed_pi():
    modules = sorted((Path.home() / ".nvm/versions/node").glob("*/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js"))
    if not modules:
        pytest.skip("Installed Pi required for the real QuickJS fixture")
    return modules[-1]


def test_real_quickjs_has_no_models_files_or_network_and_keeps_structured_preview(tmp_path):
    observed = invoke(tmp_path, pi_module=installed_pi(), code='''
text({models:typeof models,process:typeof process,require:typeof require,fetch:typeof fetch});
const result=await tools.preview_typed_edit({operation_type:'ReplaceClip',clip_id:'second',asset_id:'new',segment_id:'segment'});
text(result);
''')
    result = observed["results"][0]["result"]
    text = "\n".join(item.get("text", "") for item in result["content"])
    for name in ("models", "process", "require", "fetch"):
        assert re.search(r'"' + name + r'"\s*:\s*"undefined"', text)
    receipt = result["details"]["business_receipts"][0]
    actual_preview = receipt["result"]["preview"]
    assert {key: actual_preview[key] for key in preview()} == preview()
    assert UUID(actual_preview["request_id"]).version == 5
    assert result["details"]["limits"]["models"] is False


def test_nested_preview_survives_later_script_failure_in_host_and_projector(tmp_path):
    observed = invoke(tmp_path, pi_module=installed_pi(), code="await tools.preview_typed_edit({operation_type:'ReplaceClip',clip_id:'second',asset_id:'new',segment_id:'segment'}); throw new Error('after-preview');")
    result = observed["results"][0]["result"]
    parent = {"type": "tool_execution_end", "toolName": "codemode", "toolCallId": "parent", "result": result, "isError": True}
    lines = list(host._business_event_lines(json.dumps(parent)))
    nested = json.loads(lines[0])
    assert nested["toolName"] == "preview_typed_edit"
    projected = host._PiStreamProjector().project(lines[0])
    assert projected["type"] == "tool_end" and projected["ok"]
    final = {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "脚本后续失败"}]}}
    answer, receipt = project_event_fixture(json.dumps(parent) + "\n" + json.dumps(final) + '\n{"type":"agent_settled"}', "edit_preview")
    assert {key: receipt[key] for key in preview()} == preview() and "尚未应用" in answer
    assert "后续工具编排未完成" in answer


def test_lost_reply_keeps_original_request_id_and_only_queries(tmp_path):
    step = {"tool": "preview_typed_edit", "args": {"operation_type": "TrimClip", "clip_id": "second", "source_start_seconds": 1}}
    observed = invoke(tmp_path, steps=[step, {"tool": "read_typed_edit_request", "fromReceipt": True}, step], lost_receipt=True)
    uncertain = observed["results"][0]["result"]
    assert uncertain["isError"] and uncertain["details"]["status"] == "result_unknown"
    assert uncertain["details"]["assistant_turn_id"] == "trusted-turn"
    assert uncertain["details"]["tool_call_id"] == "fixture"
    assert uncertain["details"]["phase"] == "preview"
    request_id = uncertain["details"]["request_id"]
    assert request_id == observed["requestIdentity"]
    query = next(call for call in observed["calls"] if '/assistant/edit-requests/' in call["path"])
    assert query["method"] == "GET" and query["path"].endswith(request_id)
    returned = observed["results"][1]["result"]["details"]["preview"]
    assert returned["proposal"] == preview()["proposal"] and returned["request_id"] == request_id
    assert not observed["results"][2]["ok"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1
    repeated = invoke(tmp_path, steps=[step], lost_receipt=True)
    assert repeated["results"][0]["result"]["details"]["request_id"] == request_id


def test_business_error_with_codemode_timeout_metadata_is_not_provider_timeout():
    event = {"type": "tool_execution_end", "toolName": "codemode", "isError": True,
             "result": {"details": {"limits": {"timeout_ms": 30000}}}}
    assert host._classify_output(json.dumps(event)) == host.FAILURE_TOOL_FAILED


@pytest.mark.parametrize("code", [
    "for(let i=0;i<9;i++) await tools.read_task_object({kind:'clips'});",
    "await tools.preview_edit({});",
])
def test_real_quickjs_enforces_call_budget_and_registered_scope(tmp_path, code):
    observed = invoke(tmp_path, pi_module=installed_pi(), code=code)
    result = observed["results"][0]["result"]
    assert result["isError"]
    assert len(observed["calls"]) <= 8
    assert all(call["method"] == "GET" for call in observed["calls"])


def test_real_quickjs_unawaited_call_cannot_claim_completion(tmp_path):
    observed = invoke(tmp_path, pi_module=installed_pi(), delay_ms=200,
                      code="tools.read_task_object({kind:'clips'}); return 'finished';")
    result = observed["results"][0]["result"]
    assert result["isError"] and result["details"]["incomplete_calls"]
    assert result["content"][0]["text"].startswith("Script failed")


def test_candidates_read_real_clip_without_faking_editor_selection(tmp_path):
    workspace = {"task": {"id": "task-current", "current_timeline_version_id": "version-current", "timeline_sequence": 4},
                 "montage": {"clips": [{"id": "second", "asset_id": "old", "segment_id": "old-segment"}]}}
    observed = invoke(tmp_path, workspace=workspace, steps=[{"tool": "read_clip_candidates", "args": {"clip_id": "second"}}])
    assert observed["results"][0]["ok"]
    assert any('/montage/clips/second/candidates' in call["path"] for call in observed["calls"])


def test_multiple_frozen_profiles_are_read_by_index_without_rebinding_ids(tmp_path):
    references=[{"kind":"voice","profile_id":"voice-one","version":"v1","label":"声音甲"},
                {"kind":"person","profile_id":"person-two","version":"v7","label":"人物乙"}]
    data={"/api/assistant/profile-references/voice/voice-one":{"display_name":"声音甲","version":"v1"},
          "/api/assistant/profile-references/person/person-two":{"display_name":"人物乙","version":"v7"},
          "/api/assistant/profile-references/voice/voice-new":{"display_name":"声音丙","version":"v9"}}
    actual=invoke(tmp_path,references=references,reference_data=data,steps=[
        {"tool":"read_selected_reference","args":{"reference_index":2}},
        {"tool":"read_selected_reference","args":{"reference_index":1}},
        {"tool":"read_selected_reference","args":{"reference_index":3}},
        {"rebind":[{"kind":"voice","profile_id":"voice-new","version":"v9","label":"声音丙"}]},
        {"tool":"read_selected_reference","args":{"reference_index":1}}])
    assert [item["result"]["details"]["display_name"] for item in actual["results"][:2]] == ["人物乙","声音甲"]
    calls=[item for item in actual["calls"] if "/profile-references/" in item["path"]]
    assert [(item["path"],item["query"]) for item in calls] == [
        ("/api/assistant/profile-references/person/person-two","?version=v7"),
        ("/api/assistant/profile-references/voice/voice-one","?version=v1"),
        ("/api/assistant/profile-references/voice/voice-new","?version=v9")]
    assert actual["results"][2]["ok"] is False and len(calls) == 3
    assert actual["results"][3]["result"]["details"]["display_name"] == "声音丙"


def test_quality_tools_use_host_and_keep_original_pair_after_unknown(tmp_path):
    runner = tmp_path / 'quality-host.mjs'
    runner.write_text(r'''
import {registerObservationTools} from MODULE_OBSERVATION;
import {createBusinessRequest} from MODULE_TRANSPORT;
const tools=new Map(), calls=[];
let visible=false, changed=false, writes=0, verifies=0;
const pair={job_id:'original-quality-job',failed_request_id:'failed-phase'};
const receipt={check_id:'original-check',job_id:pair.job_id,request_id:'same-successor'};
const verifyTaskVersion=()=>{verifies++;if(changed)throw new Error('HEAD changed');};
const request=createBusinessRequest({taskId:'task',selectedTaskVersion:{timeline_version_id:'v1',timeline_sequence:1},
  verifyTaskVersion,readCredentials:()=>{throw new Error('host cannot read HTTP credentials');},
  getSignal:()=>undefined,fetchRequest:()=>{throw new Error('must not loop back through HTTP');},
  businessCall:async(operation,args)=>{
    calls.push({operation,args});
    if(operation==='read_task_quality_continuation')return {ok:true,value:{...pair,status:visible?'recorded':'unknown',receipt:visible?receipt:null}};
    if(operation==='read_task_workspace')return {ok:true,value:{task:{id:'task',current_timeline_version_id:'v1',timeline_sequence:1}}};
    if(operation==='read_task_quality_check')return {ok:true,value:{check_state:'failed',continuation:{...pair,can_request:true}}};
    if(operation==='continue_task_quality_check'){writes++;throw new Error('committed but reply lost');}
    throw new Error('unexpected operation '+operation);
  }});
const register=()=>registerObservationTools({registerTool:t=>tools.set(t.name,t)},
  {request,taskId:'task',verifyTaskVersion,readActionSource:()=>({turnId:'turn'}),typedRequestId:()=>{throw new Error('QC must use original predecessor, not new UUID');}});
register();
const first=await tools.get('continue_task_quality_check').execute('first',pair);
changed=true;
const unknown=await tools.get('continue_task_quality_check').execute('second',{job_id:'another-job',failed_request_id:'another-phase'});
visible=true;
const recovered=await tools.get('read_task_quality_continuation').execute('read',pair);
register();
const replay=await tools.get('continue_task_quality_check').execute('restart',pair);
console.log(JSON.stringify({first,unknown,recovered,replay,writes,verifies,calls,parameters:tools.get('continue_task_quality_check').parameters}));
'''.replace('MODULE_OBSERVATION', json.dumps((MODULE.parent / 'diary/observation-tools.ts').as_uri()))
        .replace('MODULE_TRANSPORT', json.dumps((MODULE.parent / 'diary/transport.ts').as_uri())))
    completed = subprocess.run([os.environ.get('MATERIAL_TOOLS_TEST_NODE', 'node'), '--experimental-strip-types', str(runner)],
        capture_output=True, text=True, check=True)
    seen = json.loads(completed.stdout.splitlines()[-1])
    assert seen['writes'] == 1 and seen['verifies'] == 1
    assert seen['first']['details']['status'] == seen['unknown']['details']['status'] == 'unknown'
    assert seen['unknown']['details']['job_id'] == 'original-quality-job'
    assert seen['unknown']['details']['failed_request_id'] == 'failed-phase'
    assert seen['recovered']['details']['receipt'] == seen['replay']['details']['receipt']
    assert seen['replay']['details']['receipt']['request_id'] == 'same-successor'
    assert set(seen['parameters']['properties']) == {'job_id', 'failed_request_id'}
    assert seen['parameters']['additionalProperties'] is False
    assert seen['calls'][-1]['operation'] == 'read_task_quality_continuation'
    assert all(call['operation'] != 'observe_task_output' for call in seen['calls'])


@pytest.mark.parametrize('status', ['recorded', 'unknown', 'rejected'])
def test_quality_continuation_projection_never_claims_check_passed(status):
    from app.assistant.result_projection import project_business_result
    details = {'kind': 'quality_continuation', 'task_id': 'task', 'job_id': 'original-job',
        'failed_request_id': 'original-failure', 'status': status, 'invocation_input_id': 'current',
        'receipt': {'check_id': 'check', 'job_id': 'original-job', 'request_id': 'successor'} if status == 'recorded' else None}
    answer, value = project_business_result({'answer': '质检通过，视频完成', 'status': 'settled', 'input_id': 'current',
        'tool_results': [{'type': 'tool_execution_end', 'toolName': 'continue_task_quality_check',
            'isError': status != 'recorded', 'result': {'details': details}}]})
    assert value == details and answer != '质检通过，视频完成'
    assert '不表示检查通过' in answer if status == 'recorded' else ('尚未知' in answer if status == 'unknown' else '拒绝' in answer)


@pytest.mark.parametrize('recovery_tool', ['read_task_quality_continuation', 'continue_task_quality_check'])
def test_quality_same_tool_instance_unlocks_only_recorded_original_pair(tmp_path, recovery_tool):
    runner = tmp_path / 'quality-next-phase.mjs'
    runner.write_text(r'''
import {registerObservationTools} from MODULE_OBSERVATION;
const tools=new Map(), writes=[], reads=[];
const first={job_id:'same-job',failed_request_id:'first-failed-phase'};
const second={job_id:'same-job',failed_request_id:'second-failed-phase'};
const unrelated={job_id:'other-job',failed_request_id:'other-failed-phase'};
let visible=false, current=first, verifies=0;
const receipt=pair=>({check_id:'same-check',job_id:pair.job_id,request_id:`successor-${pair.failed_request_id}`});
const request=async(path, options)=>{
  if(path.includes('/continuation?')){
    const query=new URL(path,'http://localhost').searchParams;
    const pair={job_id:query.get('job_id'),failed_request_id:query.get('failed_request_id')};
    reads.push(pair);
    const recorded=pair.job_id===unrelated.job_id || (pair.failed_request_id===first.failed_request_id && visible);
    return {...pair,status:recorded?'recorded':'unknown',receipt:recorded?receipt(pair):null};
  }
  if(path.endsWith('/continue')){
    const pair=JSON.parse(options.body);writes.push(pair);
    if(pair.failed_request_id===first.failed_request_id)throw new Error('committed but reply lost');
    return receipt(pair);
  }
  if(path.endsWith('/quality-check'))return {continuation:{...current,can_request:true}};
  if(path==='/api/creation-tasks/task')return {task:{id:'task'}};
  throw new Error('unexpected request '+path);
};
registerObservationTools({registerTool:t=>tools.set(t.name,t)},
  {request,taskId:'task',verifyTaskVersion:()=>{verifies++;},readActionSource:()=>({turnId:'turn'}),
   typedRequestId:()=>{throw new Error('must not create another request');}});
const continued=tools.get('continue_task_quality_check');
const initial=await continued.execute('initial',first);
current=second;
await tools.get('read_task_quality_continuation').execute('unrelated',unrelated);
const blocked=await continued.execute('still-unknown',second);
const writesWhileUnknown=writes.length;
visible=true;
const recovered=await tools.get(RECOVERY_TOOL).execute('recover',first);
const next=await continued.execute('next-phase',second);
console.log(JSON.stringify({initial,blocked,recovered,next,writesWhileUnknown,writes,reads,verifies}));
'''.replace('MODULE_OBSERVATION', json.dumps((MODULE.parent / 'diary/observation-tools.ts').as_uri()))
        .replace('RECOVERY_TOOL', json.dumps(recovery_tool)))
    completed = subprocess.run([os.environ.get('MATERIAL_TOOLS_TEST_NODE', 'node'), '--experimental-strip-types', str(runner)],
        capture_output=True, text=True, check=True)
    seen = json.loads(completed.stdout.splitlines()[-1])
    assert seen['initial']['details']['status'] == seen['blocked']['details']['status'] == 'unknown'
    assert seen['writesWhileUnknown'] == 1
    assert seen['blocked']['details']['failed_request_id'] == 'first-failed-phase'
    assert seen['recovered']['details']['status'] == 'recorded'
    assert seen['recovered']['details']['failed_request_id'] == 'first-failed-phase'
    assert seen['next']['details']['failed_request_id'] == 'second-failed-phase'
    assert seen['next']['details']['receipt']['request_id'] == 'successor-second-failed-phase'
    assert seen['writes'] == [
        {'job_id': 'same-job', 'failed_request_id': 'first-failed-phase'},
        {'job_id': 'same-job', 'failed_request_id': 'second-failed-phase'}]
    assert seen['verifies'] == 2
