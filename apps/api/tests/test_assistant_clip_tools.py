"""Run the shipped Node extension across task/material operations, without a model."""
from tool_result_test_fixtures import TOOL_OUTCOME_JS

from assistant_sdk_fixtures import project_event_fixture
import json
import os
from app.subprocess_environment import subprocess_environment
import subprocess
from pathlib import Path

import pytest
from app.schemas import CreationTaskExactEditRequest, EXACT_EDIT_FIELDS

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
TURN_ID = "11111111-1111-4111-8111-111111111111"
RUNNER = TOOL_OUTCOME_JS + r"""
import {createServer} from 'node:http';
let raw='';for await(const part of process.stdin)raw+=part;
const input=JSON.parse(raw),calls=[],tools=new Map();
const asset={id:'asset-a',updated_at:'2026-10-11T00:00:00Z',file_name:'test.mp4',user_tags:[],
 media_type:'video',use_state:'usable',analysis_status:'blocked',analysis_message:'等待分析',
 segments:[{id:'segment-a',asset_id:'asset-a',updated_at:'2026-10-11T00:00:00Z',
  user_tags:[],system_tags:['场景:室内'],description:'用户描述',transcript:'',review_status:'raw',
  usage_role:'b_roll',collection_ids:[],start_seconds:0,end_seconds:5}]};
const candidate={...structuredClone(asset),id:'asset-b',analysis_status:'ready',file_name:'候选.mp4',
 segments:[{...structuredClone(asset.segments[0]),id:'segment-b',asset_id:'asset-b',user_tags:['空镜头']}]};
const workspace={task:{id:'task-a',current_timeline_version_id:'timeline-a',timeline_sequence:1},
 montage:{clips:[{id:'clip-a',asset_id:'asset-a',segment_id:'segment-a',person_render_id:null}]}};
const server=createServer(async(req,res)=>{
 let body='';for await(const chunk of req)body+=chunk;
 calls.push({path:req.url,method:req.method,body:body?JSON.parse(body):null,token:req.headers['x-local-member-token']});
 res.setHeader('content-type','application/json');
 let reply;
 if(req.url==='/api/creation-tasks/task-a') reply=workspace;
 else if(req.url.includes('/candidates?for_automation=true')) reply=[{asset_id:'asset-b',segment_id:'segment-b',file_name:'候选.mp4',
  start_seconds:0,end_seconds:5,description:'产品空镜头',reasons:['时长满足']}];
 else if(req.url.endsWith('/edits/exact/preview')) reply={proposal:{id:'proposal-a',summary:'替换镜头',target_refs:['clip:clip-a'],
  operation:{operation_type:'ReplaceClip'},preview:{before:{detail:'原镜头'},after:{detail:'候选镜头'}},invalidated_refs:[],external_effects:[],status:'proposed'}};
 else if(req.url.includes('asset-b')) reply=candidate;
 else {
  if(req.method==='PUT'){
   const mutation=JSON.parse(body);asset.segments[0].user_tags=mutation.user_tags;
   asset.updated_at='2026-10-11T00:00:01Z';asset.segments[0].updated_at=asset.updated_at;
  }
  reply=asset;
 }
 res.end(JSON.stringify(reply));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
process.env.AI_VIDEO_DIARY_AGENT_API_BASE=`http://127.0.0.1:${server.address().port}`;
process.env.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES=JSON.stringify(input.capabilities);
try{
 const {default:register}=await import(process.argv[1]);register({registerTool:tool=>tools.set(tool.name,tool)});
 const results=[];
 for(const step of input.steps){
  if(step.changeVersion)workspace.task.timeline_sequence=2;
  try{results.push(toolOutcome(await tools.get(step.tool).execute('call',step.params||{})));}
  catch(error){results.push({ok:false,error:error.message});}
 }
 console.log(JSON.stringify({results,calls,asset,tools:[...tools.keys()],preview_parameters:tools.get('preview_typed_edit').parameters}));
}finally{await new Promise(resolve=>server.close(resolve));}
"""


def invoke(tmp_path, steps, *, instruction="给当前镜头加上空镜头标签"):
    env = subprocess_environment({key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ})
    env.update(AI_VIDEO_DIARY_AGENT_TASK_ID="task-a", AI_VIDEO_DIARY_AGENT_STAGE="montage",
               AI_VIDEO_DIARY_AGENT_SELECTED_REFS='["clip:clip-a"]',
               AI_VIDEO_DIARY_AGENT_TASK_READ_READY="1", AI_VIDEO_DIARY_AGENT_TURN_ID=TURN_ID,
               AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION=instruction,
               AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="fixture-member",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION='{"timeline_version_id":"timeline-a","timeline_sequence":1}',
               AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION=json.dumps({"scope":"segment","assetId":"asset-a", "segmentId":"segment-a",
                   "expectedUpdatedAt":"2026-10-11T00:00:00Z", "expectedSegmentUpdatedAt":"2026-10-11T00:00:00Z"}))
    completed = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
                               input=json.dumps({"steps":steps,"capabilities":{
                                   "schema":CreationTaskExactEditRequest.model_json_schema(), "supported_operations":list(EXACT_EDIT_FIELDS),
                                   "operations":[{"operation_type":operation,"available":True} for operation in EXACT_EDIT_FIELDS]}}),
                               text=True, capture_output=True, check=True,
                               cwd=tmp_path, env=env, timeout=10)
    return json.loads(completed.stdout)


def test_task_clip_tags_read_write_readback_while_analysis_blocked(tmp_path):
    out = invoke(tmp_path, [{"tool":"read_selected_material"},
                           {"tool":"update_selected_material_tags", "params":{"action":"add", "tags":["空镜头"]}}])
    assert all(item["ok"] for item in out["results"])
    assert out["asset"]["analysis_status"] == "blocked"
    assert out["asset"]["user_tags"] == []
    assert out["asset"]["segments"][0]["user_tags"] == ["空镜头"]
    assert out["asset"]["segments"][0]["system_tags"] == ["场景:室内"]
    assert out["results"][-1]["result"]["details"]["changed"]
    assert all(call["token"] == "fixture-member" for call in out["calls"])


def test_task_change_after_read_prevents_material_write(tmp_path):
    out = invoke(tmp_path, [{"tool":"read_selected_material"},
                           {"tool":"update_selected_material_tags", "params":{"action":"add", "tags":["空镜头"]}, "changeVersion":True}])
    assert out["results"][0]["ok"] and not out["results"][1]["ok"]
    assert "任务版本已变化" in out["results"][1]["error"]
    assert not any(call["method"] == "PUT" for call in out["calls"])


def test_preview_exposes_choice_but_no_model_rewrite_of_host_instruction_or_selection(tmp_path):
    params = {"operation_type":"ReplaceClip", "clip_id":"clip-a", "asset_id":"asset-b", "segment_id":"segment-b"}
    out = invoke(tmp_path, [{"tool":"read_clip_candidates", "params":{"clip_id":"clip-a"}},
                           {"tool":"preview_typed_edit", "params":params}], instruction="把当前镜头换成读到的候选，保持时长和旁白不变")
    assert all(item['ok'] for item in out['results'])
    candidate = out['results'][0]['result']['details']['matches'][0]
    assert candidate['asset_id'] == params['asset_id'] and candidate['segment_id'] == params['segment_id']
    assert candidate['file_name'] == '候选.mp4' and candidate['user_tags'] == ['空镜头']
    assert any('/montage/clips/clip-a/candidates?' in call['path'] for call in out['calls'])
    submitted = next(call['body'] for call in out['calls'] if call['path'].endswith('/edits/exact/preview'))
    CreationTaskExactEditRequest.model_validate(submitted)
    assert {key:submitted[key] for key in params} == params
    assert submitted['assistant_turn_id'] == TURN_ID
    assert submitted['expected_timeline_version_id'] == 'timeline-a' and submitted['expected_timeline_sequence'] == 1
    assert not ({'raw_command','selected_object_refs','assistant_turn_id','request_id','expected_timeline_sequence'}
                & out['preview_parameters']['properties'].keys())
    assert 'preview_edit' not in out['tools']
    assert not any(call['path'].endswith('/edits/preview') for call in out['calls'])


@pytest.mark.parametrize('field', ['raw_command','assistant_turn_id','selected_object_refs','expected_timeline_sequence'])
def test_typed_preview_rejects_model_rewrite_of_host_source_or_selection(tmp_path, field):
    params = {'operation_type':'ReplaceClip','clip_id':'clip-a','asset_id':'asset-b','segment_id':'segment-b',field:'forged'}
    out = invoke(tmp_path, [{'tool':'read_clip_candidates'}, {'tool':'preview_typed_edit','params':params}])
    assert out['results'][0]['ok'] and not out['results'][1]['ok']
    assert '可信宿主字段' in out['results'][1]['error']
    assert not any(call['method'] == 'POST' for call in out['calls'])


def test_candidate_read_does_not_allow_a_typed_write_after_task_version_changes(tmp_path):
    params = {'operation_type':'ReplaceClip','clip_id':'clip-a','asset_id':'asset-b','segment_id':'segment-b'}
    out = invoke(tmp_path, [{'tool':'read_clip_candidates'}, {'tool':'preview_typed_edit','params':params,'changeVersion':True}])
    assert out['results'][0]['ok']
    result = out['results'][1]['result']
    assert result['isError'] and result['details']['status'] == 'not_submitted'
    assert result['details']['base_version_id'] == 'timeline-a' and result['details']['base_sequence'] == 1
    assert not any(call['method'] == 'POST' for call in out['calls'])


@pytest.mark.parametrize('failed', [False, True])
def test_task_tag_reply_comes_from_verified_write_not_model_claims(failed):
    from app.assistant_pi import AssistantUnavailable
    details = {'scope':'segment','asset_ref':'asset:opaque-a','segment_ref':'segment:opaque-s',
               'user_tags':['空镜头'],'system_tags':['用途:b_roll'],'action':'add','changed':True}
    events = [{'type':'tool_execution_end','toolName':'update_selected_material_tags','isError':failed,'result':{'details':details}},
              {'type':'message_end','message':{'role':'assistant','stopReason':'stop','content':[{'type':'text','text':'已保存 opaque-a 用途:b_roll'}]}},
              {'type':'agent_settled'}]
    output = '\n'.join(json.dumps(event) for event in events)
    if failed:
        with pytest.raises(AssistantUnavailable):
            project_event_fixture(output)
    else:
        reply,preview = project_event_fixture(output)
        assert '空镜头' in reply and '已保存并回读' in reply
        assert 'opaque-a' not in reply and 'b_roll' not in reply and preview is None


@pytest.mark.parametrize("candidate_read", [True, False])
def test_legacy_replacement_fields_are_retired_even_with_candidates(tmp_path, candidate_read):
    steps = [{"tool":"read_clip_candidates"}] if candidate_read else []
    steps += [{"tool":"preview_edit", "params":{"replacement_asset_id":"asset-b", "replacement_segment_id":"segment-b"}}]
    steps += [{"tool":"preview_typed_edit", "params":{"operation_type":"ReplaceClip", "clip_id":"clip-a",
        "replacement_asset_id":"asset-b", "replacement_segment_id":"segment-b"}}]
    out = invoke(tmp_path, steps, instruction="帮我换一个镜头")
    assert 'preview_edit' not in out['tools']
    assert not out['results'][-2]['ok']
    assert not out["results"][-1]["ok"]
    assert "未知字段" in out["results"][-1]["error"]
    assert not [call for call in out["calls"] if call["method"] == 'POST']
