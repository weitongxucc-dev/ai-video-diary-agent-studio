"""Member memory tools with synthetic fetch receipts; no server, Pi or user data."""
import json
import os
from app.subprocess_environment import subprocess_environment
from pathlib import Path
import subprocess

import pytest


from tool_result_test_fixtures import TOOL_OUTCOME_JS

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = TOOL_OUTCOME_JS + r"""
let raw=''; for await(const part of process.stdin) raw+=part;
const input=JSON.parse(raw), tools=new Map(), calls=[];
let memory=structuredClone(input.memory), patched=false;
globalThis.fetch=async(url,init)=>{
  const method=init?.method||'GET', body=init?.body?JSON.parse(init.body):null;
  calls.push({path:new URL(url).pathname,method,body,member:new Headers(init?.headers).get('X-Local-Member-Token')});
  if(method==='PATCH') {
    if(input.failure==='patch') return {ok:false,status:409,json:async()=>({detail:'档案已变化，本次未保存'})};
    if(input.failure==='temporary') return {ok:false,status:422,json:async()=>({detail:'本轮没有明确长期记住或忘记意图'})};
    if(body.expected_revision!==memory.revision) throw new Error('wrong CAS revision');
    if(body.action==='remember') memory.style_preferences[body.key]=body.value;
    else delete memory.style_preferences[body.key];
    memory.revision++; patched=true;
    return {ok:true,json:async()=>structuredClone(memory)};
  }
  if(patched && input.failure==='readback_http') return {ok:false,status:503,json:async()=>({detail:'档案回读不可用'})};
  const result=structuredClone(memory);
  if(patched && input.failure==='readback_value') result.style_preferences[input.params.key]='另一条内容';
  if(patched && input.failure==='readback_revision') result.revision++;
  if(patched && input.failure==='readback_member') result.id='another-member-profile';
  return {ok:true,json:async()=>result};
};
const {default:register,readCreatorMemoryFacts}=await import(process.argv[1]);
register({registerTool:tool=>tools.set(tool.name,tool)});
const steps=[];
for(const operation of input.operations||[{tool:input.tool,params:input.params}]) {
  try {
    const result=operation.tool==='host_read'?await readCreatorMemoryFacts():await tools.get(operation.tool).execute('synthetic-call',operation.params||{});
    steps.push(toolOutcome(result));
  } catch(error) {steps.push({ok:false,error:error.message});}
}
console.log(JSON.stringify({steps,calls,memory,parameters:tools.get('update_preference_memory').parameters}));
"""


@pytest.fixture
def memory():
    return {"id": "member-profile", "revision": 7, "common_expressions": ["真实记录"],
            "banned_words": ["绝对保证"], "style_preferences": {"语气": "平实"}}


def call(tmp_path, memory, *, tool="update_preference_memory", action="remember", key="语气",
         value="简短自然", quote="请长期记住：语气简短自然", instruction="请长期记住：语气简短自然",
         turn="trusted-turn", failure=None, operations=None, **extra):
    params = {"action": action, "key": key, "source_quote": quote, **extra}
    if value is not None:
        params["value"] = value
    env = subprocess_environment({key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ})
    env.update(AI_VIDEO_DIARY_AGENT_API_BASE="http://synthetic.invalid",
               AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="synthetic-member-token")
    if turn is not None:
        env["AI_VIDEO_DIARY_AGENT_TURN_ID"] = turn
    if instruction is not None:
        env["AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION"] = instruction
    completed = subprocess.run(
        [os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
        input=json.dumps({"memory": memory, "tool": tool, "params": params, "failure": failure,
                          **({"operations": operations} if operations is not None else {})}, ensure_ascii=False),
        capture_output=True, text=True, check=True, cwd=tmp_path, env=env, timeout=10,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("tool", ["host_read", "read_preference_memory"])
def test_reads_only_bounded_member_archive(tmp_path, memory, tool):
    memory.update(common_expressions=["长" * 350] * 40, banned_words=["词"] * 40,
                  style_preferences={f"偏好{i}": "值" * 350 for i in range(40)}, private_notes="不发送")
    out = call(tmp_path, memory, tool=tool)
    assert out["steps"][0]["ok"]
    result = out["steps"][0]["result"]
    facts = result if tool == "host_read" else result["details"]
    assert facts["truncated"]
    if tool == "host_read":
        assert facts["revision"] == 7
    else:
        assert "revision" not in facts and "id" not in facts
    assert len(facts["common_expressions"]) == len(facts["banned_words"]) == len(facts["style_preferences"]) == 24
    assert len(facts["common_expressions"][0]) == 300
    assert all(len(value) == 300 for value in facts["style_preferences"].values())
    assert "private_notes" not in json.dumps(facts) and "confidence" not in facts
    assert out["calls"] == [{"path": "/api/assistant/memory", "method": "GET", "body": None, "member": "synthetic-member-token"}]


def test_remember_uses_trusted_turn_read_revision_and_verified_result(tmp_path, memory):
    out = call(tmp_path, memory, assistant_turn_id="model-fake", expected_revision=999)
    assert out["steps"][0]["ok"]
    assert [item["method"] for item in out["calls"]] == ["GET", "PATCH", "GET"]
    body = out["calls"][1]["body"]
    assert body == {"action": "remember", "key": "语气", "value": "简短自然", "expected_revision": 7,
                    "assistant_turn_id": "trusted-turn", "source_quote": "请长期记住：语气简短自然"}
    assert out["memory"]["style_preferences"]["语气"] == "简短自然"
    assert "revision" not in out["steps"][0]["result"]["details"]
    assert "已保存" in out["steps"][0]["result"]["content"][0]["text"]
    assert out["parameters"]["additionalProperties"] is False
    assert "assistant_turn_id" not in out["parameters"]["properties"]
    assert "expected_revision" not in out["parameters"]["properties"]
    assert "source_quote" not in out["parameters"]["properties"]


def test_original_typographic_quotes_are_bound_by_host_not_retyped_by_model(tmp_path, memory):
    instruction = '请记住：语气“简短自然”。'
    out = call(tmp_path, memory, instruction=instruction, quote='请记住：语气"简短自然"。')
    assert out["steps"][0]["ok"]
    assert out["calls"][1]["body"]["source_quote"] == instruction


def test_forget_removes_only_style_key_and_reads_back(tmp_path, memory):
    out = call(tmp_path, memory, action="forget", value=None, quote="请忘记长期语气偏好", instruction="请忘记长期语气偏好")
    assert out["steps"][0]["ok"]
    assert "语气" not in out["memory"]["style_preferences"]
    assert out["memory"]["common_expressions"] == memory["common_expressions"]
    assert out["memory"]["banned_words"] == memory["banned_words"]
    assert "value" not in out["calls"][1]["body"]
    assert "已忘记" in out["steps"][0]["result"]["content"][0]["text"]


def test_named_list_key_is_an_ordinary_style_preference(tmp_path, memory):
    out = call(tmp_path, memory, key="banned_words")
    assert out["steps"][0]["ok"]
    assert out["memory"]["style_preferences"]["banned_words"] == "简短自然"
    assert out["memory"]["banned_words"] == memory["banned_words"]
    assert out["memory"]["common_expressions"] == memory["common_expressions"]


@pytest.mark.parametrize("change", [{"turn": None}, {"instruction": None}, {"instruction": ""}, {"value": None}])
def test_missing_source_or_value_never_writes(tmp_path, memory, change):
    out = call(tmp_path, memory, **change)
    assert not out["steps"][0]["ok"]
    assert out["calls"] == []
    assert out["memory"] == memory


@pytest.mark.parametrize("failure", ["patch", "temporary", "readback_http", "readback_value", "readback_revision", "readback_member"])
def test_failure_never_reports_success(tmp_path, memory, failure):
    out = call(tmp_path, memory, failure=failure)
    outcome = out["steps"][0]
    assert not outcome["ok"] and outcome["result"]["isError"] is True
    assert set(outcome["result"]["details"]) == {"failure", "invocation_input_id"}
    assert json.loads(outcome["result"]["content"][0]["text"]) == {"failure": outcome["result"]["details"]["failure"]}
    assert sum(item["method"] == "PATCH" for item in out["calls"]) == 1
    assert len(out["calls"]) == (2 if failure in {"patch", "temporary"} else 3)


@pytest.mark.parametrize("failure", [None, "patch"])
def test_one_write_attempt_per_turn_even_when_failed(tmp_path, memory, failure):
    params = {"action": "remember", "key": "语气", "value": "简短自然", "source_quote": "请长期记住：语气简短自然"}
    out = call(tmp_path, memory, failure=failure, operations=[
        {"tool": "read_preference_memory"},
        {"tool": "update_preference_memory", "params": params},
        {"tool": "update_preference_memory", "params": params},
    ])
    assert not out["steps"][-1]["ok"] and "不重复保存" in out["steps"][-1]["error"]
    assert sum(item["method"] == "PATCH" for item in out["calls"]) == 1
    assert out["calls"][1]["body"]["expected_revision"] == 7
