"""Registered tool receipts with in-process HTTP doubles; no server or model."""
from tool_result_test_fixtures import TOOL_OUTCOME_JS
import json
import os
from pathlib import Path
import subprocess
from uuid import UUID

import pytest
from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS


MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
TURN_ID = "11111111-1111-4111-8111-111111111111"
PARAMS = {"operation_type": "ChangeClipSpeed", "clip_id": "current", "playback_rate": 0.7}
ORIGINAL = "把选中镜头速度改为0.7倍，保留时间槽和旁白"
RUNNER = TOOL_OUTCOME_JS + r"""
let raw = ''; for await (const part of process.stdin) raw += part;
const input = JSON.parse(raw), tools = new Map(), calls = [], results = [];
globalThis.fetch = async (url, init) => {
  const path = new URL(url).pathname, method = init?.method || 'GET';
  calls.push({path, method, body: init?.body ? JSON.parse(init.body) : null});
  if (path === '/api/creation-tasks/task-current' && method === 'GET')
    return {ok:true,json:async()=>({task:{id:'task-current',current_timeline_version_id:'version-current',timeline_sequence:4}})};
  if (path === '/api/creation-tasks/task-current/edits/exact/preview' && method === 'POST') {
    if (input.lost) throw new TypeError('fixture response lost');
    return {ok:!input.failure,status:input.failure?409:input.status,json:async()=>input.failure?{detail:'素材修订已变化'}:input.response};
  }
  throw new Error('Unexpected request: ' + method + ' ' + path);
};
process.env.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=JSON.stringify(input.capabilities);
const {default:register} = await import(process.argv[1]);
register({registerTool: tool => tools.set(tool.name, tool)});
for (let i=0; i<2; i++) {
  try { results.push(toolOutcome(await tools.get('preview_typed_edit').execute('call-'+i,input.params))); }
  catch (error) { results.push({ok:false,error:error.message}); }
}
process.stdout.write(JSON.stringify({results,calls,parameters:tools.get('preview_typed_edit').parameters,legacyRegistered:tools.has('preview_edit')}));
"""


def invoke(tmp_path, response, *, status=202, failure=False, lost=False, params=None, instruction=ORIGINAL):
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env.update(
        AI_VIDEO_DIARY_AGENT_API_BASE="http://synthetic.invalid",
        AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="synthetic-member",
        AI_VIDEO_DIARY_AGENT_STAGE="montage",
        AI_VIDEO_DIARY_AGENT_TASK_ID="task-current",
        AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1",
        AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION=instruction,
        AI_VIDEO_DIARY_AGENT_TURN_ID=TURN_ID,
        AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id":"version-current","timeline_sequence":4}),
    )
    completed = subprocess.run(
        [os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types",
         "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
        input=json.dumps({"response":response,"status":status,"failure":failure,"lost":lost,"params":PARAMS if params is None else params,
            "capabilities":{"schema":CreationTaskExactEditRequest.model_json_schema(), "supported_operations":list(EXACT_EDIT_FIELDS),
                "operations":[{"operation_type":operation,"available":True} for operation in EXACT_EDIT_FIELDS]}}),
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
    details = first["result"]["details"]
    assert details["kind"] == "task_media_job"
    assert {key:details["media_job"][key] for key in receipt} == receipt
    content = json.loads(first["result"]["content"][0]["text"])
    assert details == {**content, "invocation_input_id": TURN_ID, "input_id": None, "input_turn_id": None}
    assert details == first["result"]["structuredContent"]
    assert content["media_job"]["status"] == status
    assert "proposal_id" not in content
    assert not second["ok"] and "不重复未知操作" in second["error"]
    assert [(call["method"],call["path"]) for call in observed["calls"]] == [
        ("GET","/api/creation-tasks/task-current"),
        ("POST","/api/creation-tasks/task-current/edits/exact/preview"),
    ]
    body = observed["calls"][1]["body"]
    CreationTaskExactEditRequest.model_validate(body)
    assert body == {**PARAMS, "assistant_turn_id":TURN_ID, "request_id":details["media_job"]["request_id"],
        "expected_timeline_version_id":"version-current", "expected_timeline_sequence":4}
    assert UUID(body["request_id"]).version == 5
    assert details["media_job"]["request_query_url"].endswith(f'{body["request_id"]}?phase=preview')
    assert not ({"raw_command","assistant_turn_id","request_id","expected_timeline_version_id","expected_timeline_sequence"}
        & observed["parameters"]["properties"].keys())
    assert not observed["legacyRegistered"]


def test_immediate_200_proposal_keeps_existing_result(tmp_path):
    preview = {"proposal":{
        "id":"proposal-current", "summary":"镜头改为0.7倍速", "target_refs":["clip:current"],
        "operation":{"operation_type":"ChangeClipSpeed"},
        "preview":{"before":{"detail":"原速度"},"after":{"detail":"0.7倍速"}},
        "invalidated_refs":[], "external_effects":[], "status":"proposed",
    }}
    observed = invoke(tmp_path, preview, status=200)
    details = observed["results"][0]["result"]["details"]
    assert details["kind"] == "edit_preview"
    assert {key:details["preview"][key] for key in preview} == preview
    content = json.loads(observed["results"][0]["result"]["content"][0]["text"])
    assert details == {**content, "invocation_input_id": TURN_ID, "input_id": None, "input_turn_id": None}
    assert details == observed["results"][0]["result"]["structuredContent"]
    assert not observed["results"][1]["ok"] and "相同预览" in observed["results"][1]["error"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1


def test_http_failure_is_not_a_pending_receipt_and_cannot_retry(tmp_path):
    observed = invoke(tmp_path, {}, failure=True)
    result = observed["results"][0]["result"]
    assert result["isError"]
    assert result["details"]["kind"] == "edit_request_status" and result["details"]["status"] == "rejected"
    assert result["details"]["failure_reason"]["text"] == "素材修订已变化"
    assert "media_job" not in result["details"]
    assert not observed["results"][1]["ok"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1


def test_lost_reply_keeps_original_request_and_version_without_reposting(tmp_path):
    observed = invoke(tmp_path, {}, lost=True)
    result = observed["results"][0]["result"]
    locator = result["details"]
    post = next(call["body"] for call in observed["calls"] if call["method"] == "POST")
    assert result["isError"] and locator["status"] == "result_unknown"
    assert locator["request_id"] == post["request_id"] and locator["assistant_turn_id"] == TURN_ID
    assert locator["base_version_id"] == "version-current" and locator["base_sequence"] == 4
    assert locator["query_url"].endswith(f'{post["request_id"]}?phase=preview')
    assert not observed["results"][1]["ok"]
    assert sum(call["method"] == "POST" for call in observed["calls"]) == 1


@pytest.mark.parametrize("field", ["raw_command", "assistant_turn_id"])
def test_model_cannot_replace_the_trusted_instruction_binding(tmp_path, field):
    observed = invoke(tmp_path, {}, params={**PARAMS, field:"forged"})
    assert not observed["results"][0]["ok"] and "可信宿主字段" in observed["results"][0]["error"]
    assert observed["calls"] == []


def test_missing_original_instruction_prevents_submission(tmp_path):
    observed = invoke(tmp_path, {}, instruction="")
    assert not observed["results"][0]["ok"] and "本轮原句或身份未绑定" in observed["results"][0]["error"]
    assert observed["calls"] == []
