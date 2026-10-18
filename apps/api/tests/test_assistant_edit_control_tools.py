"""Registered editing controls with synthetic HTTP receipts; no service or model."""
import json
import os
from pathlib import Path
import subprocess

import pytest


MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = r"""
let raw='';for await(const part of process.stdin)raw+=part;
const input=JSON.parse(raw),tools=new Map(),calls=[],steps=[];
let controlReads=0,savedParent=input.parent;
globalThis.fetch=async(url,init)=>{
 const parsed=new URL(url),path=parsed.pathname,method=init?.method||'GET',body=init?.body?JSON.parse(init.body):null;
 calls.push({path,search:parsed.search,method,body,member:new Headers(init?.headers).get('X-Local-Member-Token')});
 if(path==='/api/creation-requests'&&method==='POST'){
  await new Promise(resolve=>setTimeout(resolve,25));
  if(input.failure==='parent_create')return{ok:false,status:503,json:async()=>({detail:'目标保存失败'})};
  savedParent={id:'request-parent',revision:7,goal:body.goal,target_count:body.target_count,intent:body.intent,clarification_questions:[],current_window:null};
  return{ok:true,json:async()=>structuredClone(savedParent)};
 }
 if(path==='/api/creation-requests/request-parent'&&method==='GET')return{ok:true,json:async()=>structuredClone(savedParent)};
 if(path==='/api/creation-requests/request-parent/windows'&&method==='POST')return{ok:true,json:async()=>({...structuredClone(savedParent),revision:savedParent.revision+1,current_window:{status:'proposed',entries:body.goals.map((goal,index)=>({ordinal:index+1,plan:{...goal,blockers:[],confirmable:true}}))}})};
 if(path==='/api/creation-tasks/task-current/edit-controls'&&method==='GET'){
  controlReads++;
  if(input.failure==='read_http')return{ok:false,status:503,json:async()=>({detail:'编辑控制读取失败'})};
  const context=structuredClone(input.context);
  if(input.failure==='wrong_task')context.task_id='foreign-task';
  if(controlReads>1){
   if(input.failure==='version')context.timeline_sequence++;
   if(input.failure==='removed')context.proposals.shift();
   if(input.failure==='steps')context.proposals[0].steps[0].command='不同的修改';
   if(input.failure==='reordered')context.proposals.reverse();
  }
  return{ok:true,json:async()=>context};
 }
 if(path==='/api/creation-tasks/task-current'&&method==='GET')return{ok:true,json:async()=>({task:{current_timeline_version_id:'version-current',timeline_sequence:input.failure==='task_version'?5:4}})};
 if(method==='POST'){
  if(input.failure==='post')return{ok:false,status:409,json:async()=>({detail:'当前编辑基础已变化，未提交操作'})};
  if(input.failure==='partial_undo')return{ok:false,status:409,json:async()=>({detail:'上一编辑包含多项修改，不能只撤销调色；未撤销整个编辑'})};
  if(path.endsWith('/selection/preview'))return{ok:true,json:async()=>structuredClone(input.preview)};
  return{ok:true,json:async()=>structuredClone(input.receipt)};
 }
 throw new Error('Unexpected synthetic request: '+method+' '+path);
};
const {default:register}=await import(process.argv[1]);register({registerTool:tool=>tools.set(tool.name,tool)});
async function execute(operation){try{return{ok:true,result:await tools.get(operation.tool).execute('synthetic-call',operation.params||{})};}catch(error){return{ok:false,error:error.message};}}
if(input.parallel)steps.push(...await Promise.all(input.operations.map(execute)));
else for(const operation of input.operations)steps.push(await execute(operation));
const parameters=Object.fromEntries(['read_edit_control_context','preview_edit_selection','discard_edit_proposal','undo_last_edit','redo_last_edit','prepare_creation_window'].map(name=>[name,tools.get(name).parameters]));
console.log(JSON.stringify({steps,calls,parameters}));
"""


@pytest.fixture
def context():
    return {"task_id": "task-current", "timeline_version_id": "version-current", "timeline_sequence": 4,
            "can_undo": True, "can_redo": True, "proposals": [
                {"id": "proposal-current", "summary": "画面和音乐修改", "operation_type": "EditCompoundTimeline", "steps": [
                    {"index": 0, "command": "画面亮一点", "enabled": True},
                    {"index": 1, "command": "音乐小一点", "enabled": True},
                    {"index": 2, "command": "字幕放下面", "enabled": False},
                ]},
                {"id": "proposal-second", "summary": "替换镜头", "operation_type": "ReplaceClip", "steps": []},
            ]}


def call(tmp_path, context, operations, *, failure=None, task=True, ready=True, version=True, turn=True, parent=None, parallel=False):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(AI_VIDEO_DIARY_AGENT_API_BASE="http://synthetic.invalid",
               AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="synthetic-member",
               AI_VIDEO_DIARY_AGENT_STAGE="montage" if task else "",
               AI_VIDEO_DIARY_AGENT_TASK_ID="task-current" if task else "",
               AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1" if ready else "",
               AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION="只撤销调色" if failure == "partial_undo" else "请撤销上一编辑或取消当前提案")
    if version:
        env["AI_VIDEO_DIARY_AGENT_TASK_VERSION"] = json.dumps({"timeline_version_id": "version-current", "timeline_sequence": 4})
    if turn:
        env["AI_VIDEO_DIARY_AGENT_TURN_ID"] = "trusted-turn"
    if parent is not None:
        env["AI_VIDEO_DIARY_AGENT_CREATION_REQUEST_ID"] = "request-parent"
    preview = {"proposal": {"id": "proposal-new", "summary": "保留画面和字幕", "operation": {"operation_type": "EditCompoundTimeline"},
               "preview": {"before": {"detail": "当前草稿"}, "after": {"detail": "新的保留项"}}, "status": "proposed"}}
    receipt = {"task_id": "task-current", "timeline_version_id": "version-after", "status": "applied", "sentinel": "actual-http-receipt"}
    completed = subprocess.run(
        [os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
        input=json.dumps({"context": context, "operations": operations, "failure": failure, "preview": preview,
                          "receipt": receipt, "parent": parent, "parallel": parallel}, ensure_ascii=False),
        text=True, capture_output=True, check=True, cwd=tmp_path, env=env, timeout=10,
    )
    return json.loads(completed.stdout)


READ = {"tool": "read_edit_control_context"}


def posts(out):
    return [item for item in out["calls"] if item["method"] == "POST"]


def test_public_control_schema_exposes_only_indices_and_actual_steps(tmp_path, context):
    out = call(tmp_path, context, [READ])
    assert out["steps"][0]["result"]["details"]["context"] == context
    public = json.loads(out["steps"][0]["result"]["content"][0]["text"])
    assert public["proposals"][0]["proposal_index"] == 1
    assert "id" not in public["proposals"][0]
    for name in ("read_edit_control_context", "undo_last_edit", "redo_last_edit"):
        assert out["parameters"][name]["properties"] == {}
        assert out["parameters"][name]["additionalProperties"] is False
    assert set(out["parameters"]["preview_edit_selection"]["properties"]) == {"proposal_index", "selection"}
    assert set(out["parameters"]["discard_edit_proposal"]["properties"]) == {"proposal_index"}
    assert all(item["member"] == "synthetic-member" for item in out["calls"])


def test_selection_uses_same_read_proposal_and_returns_unapplied_actual_preview(tmp_path, context):
    out = call(tmp_path, context, [READ, {"tool": "preview_edit_selection", "params": {"proposal_index": 1, "selection": [0, 2]}}])
    assert all(step["ok"] for step in out["steps"])
    assert posts(out) == [{"path": "/api/creation-tasks/task-current/edits/proposal-current/selection/preview", "search": "?draft=true",
                           "method": "POST", "body": {"included_step_indices": [0, 2]}, "member": "synthetic-member"}]
    result = out["steps"][1]["result"]
    assert result["details"]["kind"] == "edit_preview"
    assert result["details"]["preview"]["proposal"]["status"] == "proposed"
    assert "尚未应用" in result["content"][0]["text"]


@pytest.mark.parametrize("index", [0, -1, 3, 9, 1.5])
def test_proposal_indices_must_come_from_actual_bounded_read(tmp_path, context, index):
    out = call(tmp_path, context, [READ, {"tool": "discard_edit_proposal", "params": {"proposal_index": index}}])
    assert not out["steps"][1]["ok"] and not posts(out)


@pytest.mark.parametrize("selection", [[], [99], [-1], ["0"], [0.5], [0, 0]])
def test_selection_cannot_invent_or_repeat_steps(tmp_path, context, selection):
    out = call(tmp_path, context, [READ, {"tool": "preview_edit_selection", "params": {"proposal_index": 1, "selection": selection}}])
    assert not out["steps"][1]["ok"] and not posts(out)


def test_plain_proposal_does_not_claim_partial_selection(tmp_path, context):
    out = call(tmp_path, context, [READ, {"tool": "preview_edit_selection", "params": {"proposal_index": 2, "selection": [0]}}])
    assert "不支持部分选择" in out["steps"][1]["error"] and not posts(out)


@pytest.mark.parametrize("tool,params", [("preview_edit_selection", {"proposal_index": 1, "selection": [0]}),
                                        ("discard_edit_proposal", {"proposal_index": 1}), ("undo_last_edit", {}), ("redo_last_edit", {})])
def test_controls_require_this_turn_read_not_history(tmp_path, context, tool, params):
    out = call(tmp_path, context, [{"tool": tool, "params": params}])
    assert not out["steps"][0]["ok"] and not posts(out)


@pytest.mark.parametrize("failure", ["version", "task_version", "removed", "steps"])
def test_selection_rechecks_version_and_proposal_before_post(tmp_path, context, failure):
    out = call(tmp_path, context, [READ, {"tool": "preview_edit_selection", "params": {"proposal_index": 1, "selection": [0]}}], failure=failure)
    assert not out["steps"][1]["ok"] and not posts(out)


def test_reordered_list_does_not_rebind_read_index_to_another_proposal(tmp_path, context):
    out = call(tmp_path, context, [READ, {"tool": "discard_edit_proposal", "params": {"proposal_index": 1}}], failure="reordered")
    assert out["steps"][1]["ok"]
    assert posts(out)[0]["path"].endswith("/proposal-current/reject")


@pytest.mark.parametrize("tool,action", [("undo_last_edit", "undo"), ("redo_last_edit", "redo"), ("discard_edit_proposal", "reject")])
def test_history_identity_and_result_are_host_bound_actual_receipts(tmp_path, context, tool, action):
    out = call(tmp_path, context, [READ, {"tool": tool, "params": {"proposal_index": 1, "task_id": "model-other", "assistant_turn_id": "model-turn"}}])
    assert out["steps"][1]["ok"]
    assert posts(out)[0]["body"] == {"assistant_turn_id": "trusted-turn", "expected_timeline_version_id": "version-current", "expected_timeline_sequence": 4}
    details = out["steps"][1]["result"]["details"]
    assert details == {"invocation_input_id": "trusted-turn", "input_id": None, "input_turn_id": None,
                       "kind": "edit_control", "action": action, "task_id": "task-current",
                       "result": {"task_id": "task-current", "timeline_version_id": "version-after", "status": "applied", "sentinel": "actual-http-receipt"}}
    model_text = out["steps"][1]["result"]["content"][0]["text"]
    assert "receipt_returned" in model_text
    assert "actual-http-receipt" not in model_text and "version-after" not in model_text


@pytest.mark.parametrize("failure", [None, "post", "partial_undo"])
def test_history_control_is_attempted_once_even_after_failure(tmp_path, context, failure):
    out = call(tmp_path, context, [READ, {"tool": "undo_last_edit"}, {"tool": "redo_last_edit"},
                                 {"tool": "discard_edit_proposal", "params": {"proposal_index": 1}}], failure=failure)
    assert len(posts(out)) == 1
    assert not out["steps"][2]["ok"] and not out["steps"][3]["ok"]
    if failure == "partial_undo":
        assert not out["steps"][1]["ok"]
        assert "不能只撤销调色" in out["steps"][1]["error"]


@pytest.mark.parametrize("failure", [None, "post"])
def test_selection_shares_single_preview_limit(tmp_path, context, failure):
    selected = {"tool": "preview_edit_selection", "params": {"proposal_index": 1, "selection": [0]}}
    out = call(tmp_path, context, [READ, selected, selected], failure=failure)
    assert len(posts(out)) == 1 and not out["steps"][2]["ok"]


@pytest.mark.parametrize("setting", ["task", "ready", "version"])
def test_control_read_requires_trusted_task_and_base(tmp_path, context, setting):
    out = call(tmp_path, context, [READ], **{setting: False})
    assert not out["steps"][0]["ok"] and not out["calls"]


@pytest.mark.parametrize("failure", ["read_http", "wrong_task"])
def test_bad_control_read_cannot_authorize_an_operation(tmp_path, context, failure):
    out = call(tmp_path, context, [READ, {"tool": "undo_last_edit"}], failure=failure)
    assert not out["steps"][0]["ok"] and not out["steps"][1]["ok"] and not posts(out)


def test_no_undo_and_unverifiable_turn_do_not_post(tmp_path, context):
    context["can_undo"] = False
    out = call(tmp_path, context, [READ, {"tool": "undo_last_edit"}])
    assert "没有可撤销" in out["steps"][1]["error"] and not posts(out)
    out = call(tmp_path, context, [READ, {"tool": "discard_edit_proposal", "params": {"proposal_index": 1}}], turn=False)
    assert "原始要求不可核验" in out["steps"][1]["error"] and not posts(out)


@pytest.mark.parametrize("blocked", [None, "questions", "window", "wrong_id"])
def test_prepare_window_loads_only_host_bound_saved_parent(tmp_path, context, blocked):
    parent = {"id": "request-parent", "revision": 7, "target_count": 100, "clarification_questions": [], "current_window": None}
    if blocked == "questions":
        parent["clarification_questions"] = ["关系尚未明确"]
    elif blocked == "window":
        parent["current_window"] = {"status": "proposed"}
    elif blocked == "wrong_id":
        parent["id"] = "foreign-request"
    out = call(tmp_path, context, [{"tool": "prepare_creation_window", "params": {"goals": [{"goal": "第一条明确目标"}], "request_id": "model-other"}}], task=False, parent=parent)
    assert out["calls"][0]["path"] == "/api/creation-requests/request-parent"
    assert "request_id" not in out["parameters"]["prepare_creation_window"]["properties"]
    if blocked:
        assert not out["steps"][0]["ok"] and not posts(out)
    else:
        assert out["steps"][0]["ok"]
        assert posts(out)[0]["path"] == "/api/creation-requests/request-parent/windows"
        assert posts(out)[0]["body"] == {"expected_revision": 7, "goals": [{"goal": "第一条明确目标"}]}
        assert out["steps"][0]["result"]["details"]["preview"]["request"]["target_count"] == 100


@pytest.mark.parametrize("failure", [None, "parent_create"])
def test_parallel_prepare_waits_for_same_create_promise_without_polling_or_recreating(tmp_path, context, failure):
    create = {"tool": "preview_creation_plan", "params": {"goal": "一万条明确目标", "target_count": 10000,
        "output_relation": "different_topics", "intent": {"kind": "discussion"}}}
    prepare = {"tool": "prepare_creation_window", "params": {"goals": [{"goal": "第一条"}, {"goal": "第二条"}, {"goal": "第三条"}]}}
    out = call(tmp_path, context, [create, prepare], task=False, parallel=True, failure=failure)
    assert len([item for item in posts(out) if item["path"] == "/api/creation-requests"]) == 1
    assert not any(item["method"] == "GET" for item in out["calls"])
    if failure:
        assert not out["steps"][0]["ok"] and not out["steps"][1]["ok"]
        assert "完整目标保存失败" in out["steps"][1]["error"]
        assert len(posts(out)) == 1
    else:
        assert all(step["ok"] for step in out["steps"])
        assert [item["path"] for item in posts(out)] == ["/api/creation-requests", "/api/creation-requests/request-parent/windows"]
        request = out["steps"][1]["result"]["details"]["preview"]["request"]
        assert request["target_count"] == 10000 and len(request["current_window"]["entries"]) == 3


@pytest.mark.parametrize("failure", [None, "parent_create"])
def test_parallel_create_is_claimed_before_await_and_never_submits_twice(tmp_path, context, failure):
    create = {"tool": "preview_creation_plan", "params": {"goal": "完整目标", "target_count": 100, "intent": {"kind": "discussion"}}}
    out = call(tmp_path, context, [create, create], task=False, parallel=True, failure=failure)
    assert len(posts(out)) == 1
    assert not out["steps"][1]["ok"]
