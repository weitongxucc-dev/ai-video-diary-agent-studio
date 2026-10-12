"""Execution-time frozen references with synthetic HTTP, without model calls."""
import json
import os
from pathlib import Path
import subprocess

import pytest

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = r"""
let raw=''; for await(const part of process.stdin) raw+=part;
const input=JSON.parse(raw), calls=[], tools=new Map(); let reads=0;
globalThis.fetch=async(url,init)=>{
  calls.push({url,method:init?.method||'GET'});
  const body=url.endsWith('/execution')?{task_id:'task-real',job:null,current_stage:null,waiting_reason:null,status_updated_at:null}:
    url.includes('/profile-references/')?{kind:'voice',profile_id:'voice-real',version:'v-fixed',display_name:'测试声音'}:
    {task:{id:'task-real',current_timeline_version_id:++reads===1?'timeline-fixed':'timeline-changed',timeline_sequence:reads===1?3:4},montage:{clips:[]}};
  return {ok:true,json:async()=>body};
};
const {default:register}=await import(process.argv[1]);register({registerTool:t=>tools.set(t.name,t)});
try {
  if(input.mode==='task') {await tools.get('read_current_task').execute();await tools.get('generate_creation_task_topics').execute();}
  else await tools.get(input.mode==='knowledge'?'read_selected_knowledge':'read_selected_profile').execute('id',{kind:'person',profile_id:'model-fake',version:'model-fake'});
  console.log(JSON.stringify({ok:true,calls}));
}catch(error){console.log(JSON.stringify({ok:false,error:error.message,calls}));}
"""


def run_tool(tmp_path, mode, *, selected=True):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env["AI_VIDEO_DIARY_AGENT_API_BASE"] = "http://synthetic.invalid"
    if mode == "task":
        env.update(AI_VIDEO_DIARY_AGENT_TASK_ID="task-real", AI_VIDEO_DIARY_AGENT_STAGE="topic",
                   AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id":"timeline-fixed", "timeline_sequence":3}))
    elif selected and mode == "knowledge":
        env["AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE"] = json.dumps({"knowledge_item_id":"knowledge-real", "version":"1", "locator":"第1段"})
    elif selected:
        env["AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE"] = json.dumps({"kind":"voice", "profile_id":"voice-real", "version":"v-fixed"})
    completed = subprocess.run([os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", "--input-type=module", "-e", RUNNER, MODULE.as_uri()], input=json.dumps({"mode":mode}), text=True, capture_output=True, check=True, cwd=tmp_path, env=env, timeout=10)
    return json.loads(completed.stdout)


def test_profile_tool_reads_only_frozen_reference_not_model_ids(tmp_path):
    result = run_tool(tmp_path, "profile")
    assert result["ok"]
    assert result["calls"] == [{"url":"http://synthetic.invalid/api/assistant/profile-references/voice/voice-real?version=v-fixed", "method":"GET"}]


def test_profile_tool_without_reference_never_guesses(tmp_path):
    result = run_tool(tmp_path, "profile", selected=False)
    assert not result["ok"] and "请先明确引用" in result["error"]
    assert result["calls"] == []


def test_task_version_changed_after_read_blocks_domain_operation(tmp_path):
    result = run_tool(tmp_path, "task")
    assert not result["ok"] and "版本已变化" in result["error"]
    assert len(result["calls"]) == 3
    assert result["calls"][1]["url"].endswith("/task-real/execution")
    assert all(call["method"] == "GET" for call in result["calls"])


def test_knowledge_tool_reads_only_fixed_identity_and_locator(tmp_path):
    result = run_tool(tmp_path, "knowledge")
    assert result["ok"]
    assert result["calls"] == [{"url":"http://synthetic.invalid/api/assistant/knowledge-references/knowledge-real?version=1&locator=%E7%AC%AC1%E6%AE%B5", "method":"GET"}]


def test_knowledge_tool_without_reference_does_not_read(tmp_path):
    result = run_tool(tmp_path, "knowledge", selected=False)
    assert not result["ok"] and "请先明确引用" in result["error"]
    assert result["calls"] == []
