"""Shipped tools and installed QuickJS, with HTTP doubles and no model/media calls."""
import json
import os
import re
from pathlib import Path
from uuid import UUID
import subprocess

import pytest

from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS
import app.assistant_pi as host

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = r"""
let raw=''; for await (const part of process.stdin) raw+=part;
const input=JSON.parse(raw), tools=new Map(), calls=[], results=[];
globalThis.fetch=async(url,init)=>{
 const path=new URL(url).pathname, body=init?.body?JSON.parse(init.body):null;
 calls.push({path,method:init?.method||'GET',body,token:init?.headers?.get('X-Local-Member-Token')});
 if(input.delay_ms){await new Promise(resolve=>setTimeout(resolve,input.delay_ms));if(init?.signal?.aborted)throw new Error('fixture call cancelled');}
 let value=input.workspace;
 if(path.endsWith('/assistant/edit-capabilities'))value=input.capabilities;
 if(path.includes('/candidates'))value=[];
 if(path.endsWith('/execution'))value={task_id:'task-current',job:null};
 if(path.endsWith('/edits/exact/preview')){value=input.preview;if(input.lost_receipt)throw new TypeError('fixture reply lost');}
 if(path.includes('/assistant/edit-requests/'))value={request_id:path.split('/').pop(),phase:'preview',status:'recorded',result:input.preview};
 if(path.includes('/media-jobs/'))value=input.job;
 const failed=Boolean(body&&input.failure);
 return {ok:!failed,status:failed?422:200,json:async()=>failed?{detail:'字段或版本拒绝'}:value};
};
process.env.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=JSON.stringify(input.capabilities);
const {default:register,typedRequestId}=await import(process.argv[2]);
const api={registerTool:tool=>tools.set(tool.name,tool), getAllTools:()=>[...tools.values()],
 getSettings:()=>({}),appendEntry:()=>{}};
await register(api);
for (const step of input.steps||[]) {
 try {const args=step.fromReceipt?{request_id:results[0].result.details.request_id}:(step.args||{});results.push({ok:true,result:await tools.get(step.tool).execute('fixture',args)});}
 catch(error){results.push({ok:false,error:error.message});}
}
if(input.code){
 const ctx={tools:[...tools.values()],sessionManager:{getBranch:()=>[]},executeTool:async(name,args,options)=>{
  try{const result=await tools.get(name).execute('nested',args,options.signal);
   return {toolCall:{id:'nested-'+calls.length},isError:Boolean(result.isError),result};}
  catch(error){return {toolCall:{id:'failed-'+calls.length},isError:true,result:{content:[{type:'text',text:error.message}]}};}
 }};
 results.push({ok:true,result:await tools.get('codemode').execute('script-fixture',{code:input.code},undefined,undefined,ctx)});
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


def invoke(tmp_path, *, steps=(), workspace=None, code=None, failure=False, result=None, job=None, pi_module=None, lost_receipt=False, delay_ms=0):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(AI_VIDEO_DIARY_AGENT_API_BASE="https://fixture.invalid", AI_VIDEO_DIARY_AGENT_TASK_ID="task-current",
               AI_VIDEO_DIARY_AGENT_STAGE="topic", AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="fixture-member",
               AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1", AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="换第二镜头并保留旁白",
               AI_VIDEO_DIARY_AGENT_TURN_ID="trusted-turn",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id": "version-current", "timeline_sequence": 4}),
               AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS="read_current_task,read_task_object,preview_typed_edit")
    if pi_module:
        env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] = str(pi_module)
    workspace = workspace or {"task": {"id": "task-current", "current_timeline_version_id": "version-current", "timeline_sequence": 4}}
    runner = tmp_path / "controlled-tools-fixture.mjs"
    runner.write_text(RUNNER)
    completed = subprocess.run([os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types",
                                str(runner), MODULE.as_uri()],
                               input=json.dumps({"steps": steps, "workspace": workspace, "capabilities": caps(),
                                                 "preview": result or preview(), "job": job, "failure": failure, "code": code, "lost_receipt": lost_receipt, "delay_ms": delay_ms}),
                               env=env, cwd=tmp_path, text=True, capture_output=True, timeout=45, check=True)
    return json.loads(completed.stdout)


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
    assert result["structuredContent"] == result["details"] == json.loads(result["content"][0]["text"])
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
    answer, receipt = host._last_answer_and_preview(json.dumps(parent) + "\n" + json.dumps(final) + '\n{"type":"agent_settled"}', "edit_preview")
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
