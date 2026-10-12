"""Registered tool receipts with in-process HTTP doubles; no server or model."""
import json
import os
from pathlib import Path
import subprocess

import pytest


MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = r"""
let raw = ''; for await (const part of process.stdin) raw += part;
const input = JSON.parse(raw), tools = new Map(), calls = [], results = [];
globalThis.fetch = async (url, init) => {
  const path = new URL(url).pathname, method = init?.method || 'GET';
  calls.push({path, method, body: init?.body ? JSON.parse(init.body) : null});
  if (path === '/api/creation-tasks/task-current' && method === 'GET')
    return {ok:true,json:async()=>({task:{current_timeline_version_id:'version-current',timeline_sequence:4}})};
  if (path === '/api/creation-tasks/task-current/edits/preview' && method === 'POST')
    return {ok:!input.failure,status:input.failure?409:input.status,json:async()=>input.failure?{detail:'素材修订已变化'}:input.response};
  throw new Error('Unexpected request: ' + method + ' ' + path);
};
const {default:register} = await import(process.argv[1]);
register({registerTool: tool => tools.set(tool.name, tool)});
for (let i=0; i<2; i++) {
  try { results.push({ok:true,result:await tools.get('preview_edit').execute('call-'+i,{})}); }
  catch (error) { results.push({ok:false,error:error.message}); }
}
process.stdout.write(JSON.stringify({results,calls,parameters:tools.get('preview_edit').parameters}));
"""


def invoke(tmp_path, response, *, status=202, failure=False):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(
        AI_VIDEO_DIARY_AGENT_API_BASE="http://synthetic.invalid",
        AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="synthetic-member",
        AI_VIDEO_DIARY_AGENT_STAGE="montage",
        AI_VIDEO_DIARY_AGENT_TASK_ID="task-current",
        AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1",
        AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="把选中镜头调亮一点",
        AI_VIDEO_DIARY_AGENT_TURN_ID="trusted-turn",
        AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id":"version-current","timeline_sequence":4}),
    )
    completed = subprocess.run(
        [os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types",
         "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
        input=json.dumps({"response":response,"status":status,"failure":failure}),
        text=True, capture_output=True, check=True, cwd=tmp_path, env=env, timeout=10,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("status", ["queued", "running", "interrupted", "succeeded"])
def test_media_receipt_is_not_dereferenced_as_a_proposal_or_polled(tmp_path, status):
    receipt = {
        "job_id":"job-current", "status":status, "result_kind":"edit_preview",
        "result_url":"/api/creation-tasks/task-current/media-jobs/job-current",
        "device_request_id":"request-current", "waiting_reason":"resource_pressure",
        "base_version_id":"version-current", "result":None,
        "failure_reason":None, "repair_action":None,
    }
    if status == "succeeded":
        receipt["result"] = {"proposal":{"id":"stored-result"}}
    observed = invoke(tmp_path, receipt)
    first, second = observed["results"]
    assert first["ok"]
    assert first["result"]["details"] == {"kind":"task_media_job","media_job":receipt}
    content = json.loads(first["result"]["content"][0]["text"])
    assert content["status"] == status
    assert "已安排，预览尚未完成" in content["instruction"]
    assert "proposal_id" not in content
    assert not second["ok"] and "本轮已请求修改预览" in second["error"]
    assert [(call["method"],call["path"]) for call in observed["calls"]] == [
        ("GET","/api/creation-tasks/task-current"),
        ("POST","/api/creation-tasks/task-current/edits/preview"),
    ]
    assert observed["calls"][1]["body"] == {
        "raw_command":"把选中镜头调亮一点", "assistant_turn_id":"trusted-turn",
        "context_stage":"montage", "selected_object_refs":[], "playhead_ms":None,
    }
    assert observed["parameters"]["properties"] == {}


def test_immediate_200_proposal_keeps_existing_result(tmp_path):
    preview = {"proposal":{
        "id":"proposal-current", "summary":"调亮镜头", "target_refs":["clip:current"],
        "operation":{"operation_type":"ChangeColor"},
        "preview":{"before":{"detail":"原亮度"},"after":{"detail":"较亮"}},
        "invalidated_refs":[], "external_effects":[], "status":"proposed",
    }}
    observed = invoke(tmp_path, preview, status=200)
    assert observed["results"][0]["result"]["details"] == {"kind":"edit_preview","preview":preview}
    assert json.loads(observed["results"][0]["result"]["content"][0]["text"])["proposal_id"] == "proposal-current"


def test_http_failure_is_not_a_pending_receipt_and_cannot_retry(tmp_path):
    observed = invoke(tmp_path, {}, failure=True)
    assert not observed["results"][0]["ok"]
    assert "素材修订已变化" in observed["results"][0]["error"]
    assert not observed["results"][1]["ok"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1
