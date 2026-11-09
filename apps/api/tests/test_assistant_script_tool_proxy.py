"""Recipe proxies consume actual DTOs; HTTP doubles, no producer/runtime execution."""
from tool_result_test_fixtures import TOOL_OUTCOME_JS

from assistant_sdk_fixtures import project_event_fixture
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID

import pytest
import app.assistant_pi as _owner_app_assistant_pi
from app.schemas import (MediaScriptToolSaveRequest, MediaScriptToolUpdateRequest,
                         MediaScriptToolRevokeRequest, MediaScriptToolPreviewRequest)
from test_assistant_typed_tools import installed_pi

host = _owner_app_assistant_pi

@pytest.fixture(autouse=True)
def receipt_projection_native_guard(request, monkeypatch):
    if request.node.originalname not in {'test_metadata_receipt_keeps_the_actual_generated_source',
            'test_actual_query_receipt_replaces_the_prior_unknown_save'}:
        return
    def forbidden(*args,**kwargs):
        pytest.fail('pure receipt projection must not invoke SDK, media or Popen')
    monkeypatch.setattr(subprocess,'Popen',forbidden)
    import _media_preview_test_fixtures as helpers
    monkeypatch.setattr(helpers,'write_synthetic_video',forbidden)


MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
CAPS = {"schemas": {name: cls.model_json_schema() for name, cls in (
    ("save", MediaScriptToolSaveRequest), ("update", MediaScriptToolUpdateRequest),
    ("revoke", MediaScriptToolRevokeRequest), ("invoke", MediaScriptToolPreviewRequest))},
    "invocation_phase": "script", "result_kind": "graphic_source"}
TOOL = {"tool_id": "tool-real", "version": 1, "active": True, "name": "Panel", "description": "draw panel",
        "parameter_schema": {"type": "object", "properties": {"color": {"type": "string"}}, "additionalProperties": False},
        "definition": {"script_source": "PRIVATE-CODE-NOT-LOADED", "input_slots": [],
                       "requested_outputs": [{"name": "panel.png", "width": 64, "height": 64, "max_bytes": 65536}]}}
SAVE = {"producer_job_id": "producer-real", "name": "Panel", "description": "draw panel", "parameter_schema": TOOL["parameter_schema"]}
SOURCE = {"source_ref":"artifacts/orange.png", "artifact_id":"orange", "sha256":"a" * 64,
          "size_bytes":128, "name":"orange.png", "width":64, "height":64, "applied":False}
RUNNER = TOOL_OUTCOME_JS + r"""
let data='';for await(const part of process.stdin)data+=part;
const input=JSON.parse(data),tools=new Map(),calls=[],results=[];
globalThis.fetch=async(url,init)=>{
 const path=new URL(url).pathname,method=init?.method||'GET',body=init?.body?JSON.parse(init.body):null;
 calls.push({path,query:new URL(url).search,method,body,token:init?.headers.get('X-Local-Member-Token')});
 if(input.lost&&method!=='GET')throw new TypeError('fixture response lost');
 if(input.denied&&path==='/api/media-script-tools/tool-real')return {ok:false,status:403,json:async()=>({detail:'当前成员不可访问该工具'})};
 let value;
 if(path==='/api/media-script-tools')value={items:[input.tool],truncated:false};
 else if(path==='/api/media-script-tools/capabilities')value=input.caps;
 else if(path.endsWith('/preview'))value={job_id:'new-job',status:'queued',result_kind:'graphic_source',result:null,base_version_id:'new-base'};
 else if(path.endsWith('/graphics/sources'))value=[input.source];
 else if(path.includes('/media-jobs/'))value={job_id:'new-job',status:'succeeded',result_kind:'graphic_source',result:input.source,base_version_id:'new-base'};
 else if(path==='/api/creation-tasks/new-task')value={task:{id:'new-task',current_timeline_version_id:'new-base',timeline_sequence:5}};
 else value=input.tool;
 return {ok:true,status:200,json:async()=>value};
};
process.env.AI_VIDEO_DIARY_AGENT_SCRIPT_TOOL_CAPABILITIES=JSON.stringify(input.caps);
const {default:register}=await import(process.argv[2]);await register({registerTool:tool=>tools.set(tool.name,tool),
 getAllTools:()=>[...tools.values()],getSettings:()=>({}),appendEntry:()=>{}});
for(const step of input.steps){
 try{results.push(toolOutcome(await tools.get(step.name).execute(step.call||'call-'+results.length,step.args||{})));}
 catch(error){results.push({ok:false,error:error.message});}
}
if(input.code){
 const ctx={tools:[...tools.values()],sessionManager:{getBranch:()=>[]},executeTool:async(name,args,options)=>{
  try{const result=await tools.get(name).execute('nested-'+calls.length,args,options.signal);
   return {toolCall:{id:'nested-'+calls.length},isError:Boolean(result.isError),result};}
  catch(error){return {toolCall:{id:'failed-'+calls.length},isError:true,result:{content:[{type:'text',text:error.message}]}};}
 }};
 results.push(toolOutcome(await tools.get('codemode').execute('parent',{code:input.code},undefined,undefined,ctx)));
}
console.log(JSON.stringify({calls,results,parameters:tools.get('save_media_script_tool').parameters}));
"""


def invoke(tmp_path, steps, *, lost=False, denied=False, code=None, tool=TOOL):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(AI_VIDEO_DIARY_AGENT_API_BASE="https://fixture.invalid", AI_VIDEO_DIARY_AGENT_TASK_ID="new-task",
               AI_VIDEO_DIARY_AGENT_STAGE="topic", AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1",
               AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="fixture-member", AI_VIDEO_DIARY_AGENT_TURN_ID="trusted-turn",
               AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="明确保存、更新或停用这个可复用工具",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id": "new-base", "timeline_sequence": 5}))
    if code:
        env["AI_VIDEO_DIARY_AGENT_PI_MODULE"] = str(installed_pi())
        env["AI_VIDEO_DIARY_AGENT_BUSINESS_TOOLS"] = "read_task_graphic_sources,list_media_script_tools,describe_media_script_tool,preview_media_script_tool,read_task_media_job"
    runner = tmp_path / "script-tool-proxy.mjs"
    runner.write_text(RUNNER)
    result = subprocess.run([os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types",
                             str(runner), MODULE.as_uri()],
                            input=json.dumps({"caps": CAPS, "tool": tool, "steps": steps, "lost": lost,
                                              "denied":denied,"code":code,"source":SOURCE}),
                            env=env, cwd=tmp_path, text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def test_save_consumes_real_schema_and_trusted_identity(tmp_path):
    seen = invoke(tmp_path, [{"name": "save_media_script_tool", "args": SAVE}])
    assert seen["parameters"]["properties"]["parameter_schema"] == CAPS["schemas"]["save"]["properties"]["parameter_schema"]
    assert "request_id" not in seen["parameters"]["properties"]
    post = seen["calls"][-1]
    assert post["path"] == "/api/creation-tasks/new-task/media-script-tools" and post["token"] == "fixture-member"
    assert post["body"]["assistant_turn_id"] == "trusted-turn" and UUID(post["body"]["request_id"]).version == 5
    assert "script_source" not in post["body"]


def test_unknown_creation_and_cas_writes_never_replay(tmp_path):
    steps = [{"name": "save_media_script_tool", "args": SAVE}, {"name": "save_media_script_tool", "args": SAVE}]
    seen = invoke(tmp_path, steps, lost=True)
    locator = seen["results"][0]["result"]["details"]
    assert locator["kind"] == "media_script_tool_status" and locator["status"] == "unknown"
    assert locator["query_url"].startswith("/api/media-script-tools/requests/")
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1
    assert not seen["results"][1]["ok"]
    seen = invoke(tmp_path, [{"name": "list_media_script_tools"},
                          {"name": "revoke_media_script_tool", "args": {"tool_id": "tool-real", "expected_version": 1}},
                          {"name": "revoke_media_script_tool", "args": {"tool_id": "tool-real", "expected_version": 1}}], lost=True)
    assert seen["results"][1]["result"]["details"]["status"] == "unknown"
    assert sum(call["method"] == "POST" for call in seen["calls"]) == 1


def test_new_session_discovers_actual_tool_and_invokes_new_task_without_old_source_or_code(tmp_path):
    seen = invoke(tmp_path, [{"name": "list_media_script_tools"},
                 {"name": "describe_media_script_tool", "args": {"tool_id": "tool-real", "version": 1}},
                 {"name": "preview_media_script_tool", "args": {"tool_id": "tool-real", "tool_version": 1,
                   "parameters": {"color": "red"}, "input_refs": []}}])
    assert "PRIVATE-CODE-NOT-LOADED" not in json.dumps(seen["results"][1])
    post = seen["calls"][-1]
    assert post["path"] == "/api/creation-tasks/new-task/media-script-tools/tool-real/preview"
    assert post["body"]["expected_timeline_version_id"] == "new-base"
    assert post["body"]["parameters"] == {"color": "red"} and post["body"]["input_refs"] == []
    assert "script_source" not in post["body"] and "requested_outputs" not in post["body"]
    assert seen["results"][-1]["result"]["details"]["media_job"]["result_kind"] == "graphic_source"


def test_unread_tool_and_changed_cas_are_rejected_before_write(tmp_path):
    seen = invoke(tmp_path, [{"name": "preview_media_script_tool", "args": {"tool_id": "invented", "tool_version": 1}},
                 {"name": "list_media_script_tools"},
                 {"name": "revoke_media_script_tool", "args": {"tool_id": "tool-real", "expected_version": 2}}])
    assert not seen["results"][0]["ok"] and not seen["results"][2]["ok"]
    assert all(call["method"] == "GET" for call in seen["calls"])


def test_explicit_id_describe_uses_authenticated_get_without_listing_and_binds_invocation(tmp_path):
    seen = invoke(tmp_path, [{"name":"describe_media_script_tool", "args":{"tool_id":"tool-real", "version":1}},
        {"name":"preview_media_script_tool", "args":{"tool_id":"tool-real", "tool_version":1,
            "parameters":{"color":"red"}, "input_refs":[]}}])
    read = seen["calls"][0]
    assert read["path"] == "/api/media-script-tools/tool-real" and read["query"] == "?version=1"
    assert read["method"] == "GET" and read["token"] == "fixture-member"
    assert not any(call["path"] == "/api/media-script-tools" for call in seen["calls"])
    metadata = seen["results"][0]["result"]
    assert metadata["structuredContent"] == metadata["details"]
    assert metadata["details"]["tool_id"] == "tool-real" and metadata["details"]["parameter_schema"] == TOOL["parameter_schema"]
    assert "PRIVATE-CODE-NOT-LOADED" not in json.dumps(metadata)
    assert seen["results"][1]["result"]["details"]["kind"] == "task_media_job"
    assert seen["calls"][-1]["body"]["tool_version"] == 1


def test_denied_describe_does_not_register_the_id_for_writes(tmp_path):
    seen = invoke(tmp_path, [{"name":"describe_media_script_tool", "args":{"tool_id":"tool-real"}},
        {"name":"preview_media_script_tool", "args":{"tool_id":"tool-real", "tool_version":1}}], denied=True)
    assert not seen["results"][0]["ok"] and "当前成员不可访问" in seen["results"][0]["error"]
    assert not seen["results"][1]["ok"]
    assert len(seen["calls"]) == 1 and seen["calls"][0]["method"] == "GET"


@pytest.mark.parametrize("step,tool", [
    ({"name":"revoke_media_script_tool", "args":{"tool_id":"tool-real", "expected_version":2}}, TOOL),
    ({"name":"preview_media_script_tool", "args":{"tool_id":"tool-real", "tool_version":1}}, {**TOOL,"active":False}),
])
def test_direct_describe_keeps_actual_cas_version_and_active_guards(tmp_path, step, tool):
    seen = invoke(tmp_path, [{"name":"describe_media_script_tool", "args":{"tool_id":"tool-real"}}, step], tool=tool)
    assert seen["results"][0]["ok"] and not seen["results"][1]["ok"]
    assert all(call["method"] == "GET" for call in seen["calls"])


def test_real_codemode_reads_image_and_tool_objects_without_json_parsing(tmp_path):
    code = """const sourcePage = await tools.read_task_graphic_sources({});
const toolPage = await tools.list_media_script_tools({});
const tool = await tools.describe_media_script_tool({tool_id:toolPage.items[0].tool_id});
const preview = await tools.preview_media_script_tool({tool_id:tool.tool_id,tool_version:tool.version,parameters:{color:'red'},input_refs:[]});
const job = await tools.read_task_media_job({job_id:preview.media_job.job_id});
text({source_ref:sourcePage.items[0].source_ref,tool_version:tool.version,status:job.media_job.status});"""
    seen = invoke(tmp_path, [], code=code)
    result = seen["results"][0]["result"]
    assert not result.get("isError")
    receipts = result["details"]["business_receipts"]
    assert len(receipts) == 5 and all(not receipt["is_error"] for receipt in receipts)
    assert receipts[0]["result"]["items"][0] == {key:value for key,value in SOURCE.items() if key != "applied"}
    assert receipts[1]["result"]["items"][0]["tool_id"] == "tool-real"
    assert receipts[2]["result"]["parameter_schema"] == TOOL["parameter_schema"]
    assert receipts[4]["result"]["media_job"]["status"] == "succeeded"
    assert receipts[4]["result"]["media_job"]["result"]["applied"] is False


@pytest.mark.parametrize("recorded", [False, True])
def test_metadata_receipt_keeps_the_actual_generated_source(recorded):
    job = {"job_id": "source-job", "status": "succeeded", "result_kind": "graphic_source",
           "result": {"source_ref": "artifacts/source.png", "artifact_id": "source", "applied": False}}
    receipt = {"kind": "media_script_tool_result" if recorded else "media_script_tool_status",
               "action": "save", "request_id": "original", "query_url": "/api/media-script-tools/requests/original",
               **({"tool": TOOL} if recorded else {"status": "unknown", "submitted_input": SAVE})}
    events = [
        {"type": "tool_execution_end", "toolName": "preview_media_script", "isError": False,
         "result": {"details": {"kind": "task_media_job", "media_job": job}}},
        {"type": "tool_execution_end", "toolName": "save_media_script_tool", "isError": not recorded,
         "result": {"details": receipt}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "model text is not the receipt"}]}},
        {"type": "agent_settled"},
    ]
    _answer, actual = project_event_fixture("\n".join(json.dumps(event) for event in events))
    assert actual == {**receipt, "related_media_job": job}


def test_actual_query_receipt_replaces_the_prior_unknown_save():
    events = [
        {"type": "tool_execution_end", "toolName": "save_media_script_tool", "isError": True,
         "result": {"details": {"kind": "media_script_tool_status", "action": "save", "status": "unknown"}}},
        {"type": "tool_execution_end", "toolName": "read_media_script_tool_creation", "isError": False,
         "result": {"details": {"kind": "media_script_tool_result", "action": "query", "tool": TOOL}}},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
         "content": [{"type": "text", "text": "actual query completed"}]}},
        {"type": "agent_settled"},
    ]
    _answer, actual = project_event_fixture("\n".join(json.dumps(event) for event in events))
    assert actual["kind"] == "media_script_tool_result" and actual["action"] == "query"
