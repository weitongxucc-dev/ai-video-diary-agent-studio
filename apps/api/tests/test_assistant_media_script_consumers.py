"""API doubles and installed QuickJS only; never execute Python, model, or real media."""
from tool_result_test_fixtures import TOOL_OUTCOME_JS

from assistant_sdk_fixtures import project_event_fixture
import json
import os
from app.subprocess_environment import subprocess_environment
from pathlib import Path
import subprocess
from uuid import UUID

import pytest

import app.assistant_pi as _owner_app_assistant_pi
from app.schemas import MediaScriptPreviewRequest
from test_assistant_typed_tools import caps, installed_pi, preview

host = _owner_app_assistant_pi

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
SOURCE = {"source_ref": "artifact:png-actual", "artifact_id": "png-actual", "sha256": "a" * 64,
          "size_bytes": 128, "name": "panel.png", "width": 64, "height": 64, "applied": False}
SCRIPT = {"script_source": "from PIL import Image\nImage.new('RGBA',(64,64),'blue').save(outputs['panel.png'])",
          "language": "python", "purpose": "preview", "parameters": {}, "input_refs": [],
          "requested_outputs": [{"name": "panel.png", "max_bytes": 65536, "width": 64, "height": 64}]}
RUNNER = TOOL_OUTCOME_JS + r"""
let text='';for await(const part of process.stdin)text+=part;
const input=JSON.parse(text), calls=[], tools=new Map(), results=[];let reads=0;
const job=()=>{const status=input.alwaysPending||reads++===0?'queued':'succeeded';return {job_id:'script-job',status,
 result_kind:'graphic_source',base_version_id:'base',result:status==='succeeded'?input.source:null,
 failure_reason:null,repair_action:null,waiting_reason:null};};
globalThis.fetch=async(url,init)=>{
 const path=new URL(url).pathname,body=init?.body?JSON.parse(init.body):null;
 calls.push({path,method:init?.method||'GET',body,token:init?.headers.get('X-Local-Member-Token')});
 let value;
 if(path.endsWith('/media-scripts/preview')){if(input.lost)throw new TypeError('lost script response');value=job();}
 else if(path.includes('/media-jobs/'))value=job();
 else if(path.endsWith('/graphics/sources'))value=Array.from({length:30},(_,i)=>({...input.source,artifact_id:'source-'+i,path:'/private/not-exposed'}));
 else if(path.includes('/assistant/edit-requests/'))value={request_id:path.split('/').pop(),phase:'script',status:'recorded',result:job(),submitted_input:input.script};
 else if(path.endsWith('/edits/exact/preview'))value=input.preview;
 else if(path.endsWith('/assistant/media-script-capabilities'))value=input.media;
 else value={task:{id:'task',current_timeline_version_id:'base',timeline_sequence:2}};
 return {ok:true,status:200,json:async()=>value};
};
process.env.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=JSON.stringify(input.edit);
process.env.AI_VIDEO_DIARY_AGENT_MEDIA_SCRIPT_CAPABILITIES=JSON.stringify(input.media);
const {default:register}=await import(process.argv[2]);
await register({registerTool:t=>tools.set(t.name,t),getAllTools:()=>[...tools.values()],getSettings:()=>({}),appendEntry:()=>{}});
for(const step of input.steps){
 try{const args=step.fromReceipt?{request_id:results[0].result.details.request_id}:step.args||{};
 results.push(toolOutcome(await tools.get(step.tool).execute('call-'+results.length,args)));}
 catch(error){results.push({ok:false,error:error.message});}
}
if(input.code){
 const ctx={tools:[...tools.values()],sessionManager:{getBranch:()=>[]},executeTool:async(name,args,options)=>{
  try{const result=await tools.get(name).execute('nested-'+calls.length,args,options.signal);
   return {toolCall:{id:'nested-'+calls.length},isError:Boolean(result.isError),result};}
  catch(error){return {toolCall:{id:'error-'+calls.length},isError:true,result:{content:[{type:'text',text:error.message}]}};}
 }};
 results.push(toolOutcome(await tools.get('codemode').execute('script-parent',{code:input.code},undefined,undefined,ctx)));
}
console.log(JSON.stringify({calls,results,parameters:tools.get('preview_media_script').parameters,
 description:tools.get('preview_media_script').description}));
"""


def invoke(tmp_path, steps=(), *, available=True, lost=False, pending=False, code=None):
    media = {"task_id": "task", "schema": MediaScriptPreviewRequest.model_json_schema(), "available": available,
             "reason": None if available else "工具链或专用预算缺档", "runtime": None, "limits": None}
    runner = tmp_path / "script-consumer.mjs"
    runner.write_text(RUNNER)
    env = subprocess_environment({key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ})
    env.update(AI_VIDEO_DIARY_AGENT_TASK_ID="task", AI_VIDEO_DIARY_AGENT_API_BASE="https://fixture.invalid",
               AI_VIDEO_DIARY_AGENT_STAGE="script", AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1",
               AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="fixture-member", AI_VIDEO_DIARY_AGENT_TURN_ID="trusted-turn",
               AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="创建图片来源并准备图层提案，不采用",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id": "base", "timeline_sequence": 2}),
               AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS="preview_media_script,read_task_media_job,preview_typed_edit")
    if code:
        env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] = str(installed_pi())
    result = subprocess.run([os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", str(runner), MODULE.as_uri()],
                            input=json.dumps({"steps": steps, "media": media, "edit": caps(), "source": SOURCE,
                                              "script": SCRIPT, "preview": preview(), "lost": lost, "alwaysPending": pending, "code": code}),
                            env=env, cwd=tmp_path, text=True, capture_output=True, check=True, timeout=35)
    return json.loads(result.stdout)


def test_actual_script_schema_and_trusted_submit_binding(tmp_path):
    seen = invoke(tmp_path, [{"tool": "preview_media_script", "args": SCRIPT}])
    schema = seen["parameters"]
    actual = MediaScriptPreviewRequest.model_json_schema()
    assert schema["properties"]["requested_outputs"] == actual["properties"]["requested_outputs"]
    assert schema["$defs"] == actual["$defs"]
    assert not {"request_id", "assistant_turn_id", "expected_timeline_version_id", "runtime_paths"} & schema["properties"].keys()
    post = seen["calls"][-1]
    assert post["path"] == "/api/creation-tasks/task/media-scripts/preview" and post["token"] == "fixture-member"
    assert post["body"]["assistant_turn_id"] == "trusted-turn" and post["body"]["expected_timeline_sequence"] == 2
    assert UUID(post["body"]["request_id"]).version == 5
    details = seen["results"][0]["result"]["details"]
    assert details["kind"] == "task_media_job" and details["media_job"]["result_kind"] == "graphic_source"
    assert details["media_job"]["request_query_url"].endswith("?phase=script")
    description = seen["description"]
    assert "parameters是API parameters参数对象" in description
    assert "inputs[name]按input_refs的name" in description and "Image.open(inputs[name])" in description
    assert "outputs[name]按requested_outputs的name映射宿主提供的可信输出文件路径字符串" in description
    assert "image.save(outputs['name.png'])" in description
    assert "不要将outputs[name]替换成bytes/BytesIO，不自行新增路径或输出名称" in description


@pytest.mark.parametrize("field", ["runtime_paths", "request_id", "expected_timeline_sequence", "mystery"])
def test_model_cannot_inject_host_or_runtime_fields(tmp_path, field):
    seen = invoke(tmp_path, [{"tool": "preview_media_script", "args": {**SCRIPT, field: "forged"}}])
    assert not seen["results"][0]["ok"] and seen["calls"] == []


def test_missing_runtime_is_explicit_and_never_submitted(tmp_path):
    seen = invoke(tmp_path, [{"tool": "preview_media_script", "args": SCRIPT}], available=False)
    assert "缺档" in seen["results"][0]["error"] and seen["calls"] == []


def test_sources_are_bounded_without_filesystem_paths(tmp_path):
    seen = invoke(tmp_path, [{"tool": "read_task_graphic_sources"}])
    details = seen["results"][0]["result"]["details"]
    assert len(details["items"]) == 24 and details["total_count"] == 30 and details["truncated"]
    assert "/private/" not in json.dumps(details)


def test_unknown_script_request_keeps_original_phase_and_only_queries(tmp_path):
    seen = invoke(tmp_path, [{"tool": "preview_media_script", "args": SCRIPT},
                {"tool": "read_media_script_request", "fromReceipt": True}, {"tool": "preview_media_script", "args": SCRIPT}], lost=True)
    uncertain = seen["results"][0]["result"]["details"]
    assert uncertain["phase"] == "script" and uncertain["status"] == "result_unknown"
    assert seen["results"][1]["result"]["details"]["media_job"]["request_id"] == uncertain["request_id"]
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1


def test_finite_wait_preserves_last_real_pending(tmp_path):
    seen = invoke(tmp_path, [{"tool": "read_task_media_job", "args": {"job_id": "script-job", "wait_seconds": 1}}], pending=True)
    details = seen["results"][0]["result"]["details"]
    assert details["media_job"]["status"] == "queued" and details["wait"]["deadline_reached"]
    assert len(seen["calls"]) <= 2 and all(call["method"] == "GET" for call in seen["calls"])


def test_installed_quickjs_composes_real_tool_receipts_into_overlay_candidate(tmp_path):
    code = """const job = await tools.preview_media_script(SCRIPT);
const source = await tools.read_task_media_job({job_id:job.media_job.job_id,wait_seconds:2});
text(await tools.preview_typed_edit({operation_type:'EditGraphicOverlay',overlay_action:'add',graphic_overlay:{kind:'image',name:'panel',source_ref:source.media_job.result.source_ref,start_seconds:0,end_seconds:1}}));
""".replace("SCRIPT", json.dumps(SCRIPT))
    seen = invoke(tmp_path, code=code)
    parent = seen["results"][0]["result"]
    assert not parent.get("isError")
    overlay = next(call for call in seen["calls"] if call["path"].endswith("/edits/exact/preview"))
    assert overlay["body"]["graphic_overlay"]["source_ref"] == SOURCE["source_ref"]
    assert not any(call["path"].endswith("/confirm") for call in seen["calls"])
    source_receipt = parent["details"]["business_receipts"][1]["result"]["media_job"]
    assert source_receipt["status"] == "succeeded" and source_receipt["result"]["applied"] is False


@pytest.mark.parametrize("stage_kind", ["script_draft", "topic_generation", "edit_preview"])
def test_source_success_is_not_parsed_as_proposal_or_applied_edit(stage_kind):
    job = {"job_id": "script-job", "result_kind": "graphic_source", "status": "succeeded", "result": SOURCE}
    output = "\n".join(json.dumps(event) for event in [
        {"type": "tool_execution_end", "toolName": "read_task_media_job", "isError": False,
         "result": {"details": {"kind": "task_media_job", "media_job": job}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "fake applied"}]}},
        {"type": "agent_settled"}])
    answer, result = project_event_fixture(output, stage_kind)
    assert "PNG来源已生成" in answer and "尚未应用" in answer and result == {"kind": "media_job", "media_job": job}


def _completed_source_codemode(call_id="source-read"):
    job = {"job_id": "script-job", "result_kind": "graphic_source", "status": "succeeded", "result": SOURCE}
    return {"type": "tool_execution_end", "toolName": "codemode", "toolCallId": call_id, "isError": False,
        "result": {"details": {"calls": [{"name": "read_task_media_job", "status": "ok"}], "incomplete_calls": False,
            "business_receipts": [{"tool_name": "read_task_media_job", "tool_call_id": call_id + "/1", "is_error": False,
                "result": {"kind": "task_media_job", "media_job": job}}]}}}


def _syntax_before_calls():
    return {"type": "tool_execution_end", "toolName": "codemode", "isError": True,
        "result": {"content": [{"type": "text", "text": "Script failed\nSyntaxError: invalid redefinition of parameter name\nNo tool calls were made."}],
            "details": {"calls": [], "business_receipts": [], "incomplete_calls": False}}}


def _settled_events(*events):
    return "\n".join(json.dumps(event) for event in [*events,
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "model claims completion"}]}},
        {"type": "agent_settled"}])


def test_recovered_pre_call_syntax_error_does_not_mislabel_the_actual_source_receipt():
    answer, result = project_event_fixture(_settled_events(_syntax_before_calls(), _completed_source_codemode()))
    assert "后续工具编排未完成" not in answer
    assert "PNG来源已生成" in answer and "尚未应用" in answer
    assert result["media_job"]["result"] == SOURCE


@pytest.mark.parametrize("failure", [
    {"content": [{"type": "text", "text": "Script failed\nError: script threw"}],
        "details": {"calls": [], "business_receipts": [], "incomplete_calls": False}},
    {"content": [{"type": "text", "text": "Script failed\nSyntaxError: after business calls"}],
        "details": {"calls": [{"status": "ok"}], "business_receipts": [], "incomplete_calls": False}},
    {"details": {"calls": [{"status": "cancelled"}], "business_receipts": [], "incomplete_calls": True}},
])
def test_later_success_does_not_erase_other_orchestration_failures(failure):
    failed = {"type": "tool_execution_end", "toolName": "codemode", "isError": True, "result": failure}
    answer, result = project_event_fixture(_settled_events(failed, _completed_source_codemode()))
    assert "后续工具编排未完成" in answer
    assert result["media_job"]["result"] == SOURCE


def test_last_syntax_error_and_empty_success_do_not_count_as_recovered_business_work():
    empty = {"type": "tool_execution_end", "toolName": "codemode", "isError": False,
        "result": {"details": {"calls": [], "business_receipts": [], "incomplete_calls": False}}}
    answer, result = project_event_fixture(_settled_events(_completed_source_codemode(), _syntax_before_calls(), empty))
    assert "后续工具编排未完成" in answer and result["media_job"]["result"] == SOURCE


def test_json_parsing_error_after_real_read_is_not_cleared_by_later_success():
    failed = {"type":"tool_execution_end", "toolName":"codemode", "isError":True,
        "result":{"content":[{"type":"text","text":"Script failed\nSyntaxError: unexpected token object"}],
            "details":{"calls":[{"name":"read_task_graphic_sources","status":"ok"}], "incomplete_calls":False,
                "business_receipts":[{"tool_name":"read_task_graphic_sources","tool_call_id":"source-page", "is_error":False,
                    "result":{"items":[SOURCE], "total_count":1}}]}}}
    answer, result = project_event_fixture(_settled_events(failed, _completed_source_codemode()))
    assert "后续工具编排未完成" in answer
    assert result["media_job"]["status"] == "succeeded" and result["media_job"]["result"] == SOURCE


def test_later_unknown_overlay_keeps_both_original_locator_and_successful_source_job():
    job = {"job_id": "script-job", "result_kind": "graphic_source", "status": "succeeded", "result": SOURCE}
    locator = {"kind": "edit_request_status", "phase": "preview", "status": "result_unknown",
               "request_id": "original-overlay-id", "query_url": "/original-overlay-query"}
    output = "\n".join(json.dumps(event) for event in [
        {"type": "tool_execution_end", "toolName": "read_task_media_job", "isError": False,
         "result": {"details": {"kind": "task_media_job", "media_job": job}}},
        {"type": "tool_execution_end", "toolName": "preview_typed_edit", "isError": True, "result": {"details": locator}},
        {"type": "tool_execution_end", "toolName": "codemode", "isError": True, "result": {"details": {}}},
        _completed_source_codemode("later-read"),
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "later failed"}]}},
        {"type": "agent_settled"}])
    answer, result = project_event_fixture(output)
    assert result == {**locator, "related_media_job": job}
    assert "不重复提交" in answer and "作业回执仍保留" in answer
