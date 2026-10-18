"""Direct TypeScript tool protocols; no model, media, or production data."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


TOOLS = Path(__file__).parents[1] / "agent_tools" / "diary"


def run_tools(script, scenario=""):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the shipped tools")
    result = subprocess.run([node, "--experimental-strip-types", "--input-type=module", "-e", script,
        TOOLS.as_uri() + "/", scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


VOICE_RUNNER = r"""
import assert from 'node:assert/strict';
const {registerWorkspaceTools}=await import(process.argv[1]+'workspace-tools.ts');
const {registerRecipeTools}=await import(process.argv[1]+'recipe-tools.ts');
const tools={}, calls=[];
let page={items:[],total_count:0}, failure=false;
const request=async(path,init)=>{calls.push({path,method:init?.method||'GET'});if(failure)throw new Error('read unavailable');return page;};
registerWorkspaceTools({registerTool:t=>tools[t.name]=t},{request,taskId:undefined,stage:'home',selectedRefs:[],
  selectedProfile:null,selectedKnowledge:null,isTaskRead:()=>{throw new Error('no task');},verifyTaskVersion:()=>{},
  getEditCapabilities:()=>null,readMaterialSelection:()=>null});
registerRecipeTools({registerTool:t=>tools[t.name]=t},{request,taskId:undefined,
  readMediaScriptToolCapabilities:()=>null,getScriptToolCapabilities:()=>null,getDeclaredCapabilities:()=>null});
const tool=tools.read_voice_profiles;
const voice=(id)=>({id,version:'current-version',display_name:'声音',language:'zh-en',use_state:'available',runnable:true,
  runnable_reason:null,reference_audio_path:'/private/sample.wav',reference_text:'private transcript',
  authorization:{evidence:'/private/license.pdf'},reference:{asset_id:'private'},workspace_id:'private'});
const scenario=process.argv[2];
if(scenario==='safe_projection'){
  page={items:[voice('voice'),{...voice('blocked'),runnable:false,runnable_reason:'authorization_expired'},
    {...voice('unknown'),runnable:false,runnable_reason:'/private/failed.wav'}],total_count:3};
  const result=await tool.execute('read',{});
  assert.deepEqual(result.details.voices[0],{profile_id:'voice',version:'current-version',display_name:'声音',
    language:'zh-en',use_state:'available',runnable:true,reason:null});
  assert.equal(result.details.voices[1].reason,'authorization_expired');
  assert.equal(result.details.voices[2].reason,'声音暂不可用于制作，请在人物与声音页面检查来源和使用限制。');
  assert.ok(!JSON.stringify(result).includes('private'));
  assert.deepEqual(JSON.parse(result.content[0].text),result.details);
}else if(scenario==='pagination'){
  page={items:Array.from({length:25},(_,i)=>voice('voice-'+i)),total_count:25};
  const first=(await tool.execute('first',{})).details;
  const last=(await tool.execute('last',{offset:24})).details;
  const past=(await tool.execute('past',{offset:25})).details;
  assert.equal(first.voices.length,24);assert.equal(first.total_count,25);assert.equal(first.truncated,true);
  assert.equal(last.voices[0].profile_id,'voice-24');assert.equal(last.offset,24);assert.equal(last.truncated,true);
  assert.deepEqual(past.voices,[]);assert.equal(past.total_count,25);
}else if(scenario==='empty_failure_invalid'){
  assert.deepEqual((await tool.execute('empty',{})).details,{voices:[],offset:0,total_count:0,truncated:false});
  failure=true;await assert.rejects(tool.execute('failed',{}),/read unavailable/);failure=false;
  page={items:null,total_count:0};await assert.rejects(tool.execute('invalid',{}),/声音列表不可读/);
  page={items:[{...voice('voice'),version:null}],total_count:1};await assert.rejects(tool.execute('invalid-item',{}),/身份或状态不可读/);
}else if(scenario==='input_boundary'){
  for(const params of [{offset:-1},{offset:1.5},{workspace_id:'other'},{user_id:'other'}])await assert.rejects(tool.execute('bad',params));
  assert.equal(calls.length,0);assert.equal(tool.parameters.additionalProperties,false);
}
assert.ok(calls.every(c=>c.path==='/api/media/voice-profiles'&&c.method==='GET'));
assert.match(tools.list_media_script_tools.description,/只列当前成员保存的可复用图片脚本/);
assert.match(tools.list_media_script_tools.description,/空列表不代表内建配音/);
console.log(JSON.stringify({scenario,calls:calls.length}));
"""


@pytest.mark.parametrize("scenario", ["safe_projection", "pagination", "empty_failure_invalid", "input_boundary"])
def test_voice_discovery_without_task_is_bounded_safe_and_read_only(scenario):
    assert run_tools(VOICE_RUNNER, scenario)["scenario"] == scenario


REVISION_RUNNER = r"""
import assert from 'node:assert/strict';
const {registerCreationPlans}=await import(process.argv[1]+'creation-plans.ts');
const {BusinessRequestError}=await import(process.argv[1]+'transport.ts');
const tools={}, calls=[], source={turnId:'trusted-turn',inputId:'trusted-input'};
const scenario=process.argv[2];
let current=null, previewed=0, windowCount=0, confirms=0, tasks=0, fail=scenario, rejectConfirm=scenario==='replace';
const clone=x=>structuredClone(x);
async function request(path,init){
  const url=new URL(path,'http://test'), body=init?.body?JSON.parse(init.body):null, method=init?.method||'GET';
  calls.push({path:url.pathname,method,body,turn:url.searchParams.get('assistant_turn_id')});
  if(url.pathname==='/api/creation-requests'){
    if(fail==='rejected_create'){fail=null;throw new BusinessRequestError('invalid delivery',422);}
    current={id:'request',revision:1,goal:body.goal,target_count:body.target_count,intent:body.intent,
      requirements:{language:body.language,aspect_ratio:body.aspect_ratio,duration_target_seconds:body.duration_target_seconds,
        presenter_share_target:body.presenter_share_target,lipsync_quality:body.lipsync_quality,
        selected_voice:body.selected_voice??null,selected_person:null,target_count:1,goal:body.goal},
      confirmed_count:0,clarification_questions:[],current_window:null};
    if(fail==='lost_create'||fail==='lost_create_null'){fail=null;throw new TypeError('response lost after create');}
    return clone(current);
  }
  if(method==='GET'){
    if(url.pathname==='/api/assistant/creation-request'){
      assert.equal(url.searchParams.get('assistant_turn_id'),'trusted-turn');
      return scenario==='lost_create_null'?null:clone(current);
    }
    return clone(current);
  }
  if(body.expected_revision!==current.revision)throw new BusinessRequestError('stale revision',409);
  if(method==='PATCH'){
    current={...current,revision:current.revision+1,current_window:null,requirements:body.requirements??current.requirements};
    return clone(current);
  }
  if(url.pathname.endsWith('/windows')||url.pathname.endsWith('/replan')){
    assert.equal(url.searchParams.get('assistant_turn_id'),source.turnId);
    current={...current,revision:current.revision+1,current_window:{id:'window-'+(++windowCount),status:'proposed',
      input_fingerprint:String(windowCount).repeat(64),entries:body.goals.map((item,i)=>({ordinal:i+1,task_id:'task-'+windowCount+'-'+i,
        task:null,plan:{...current.requirements,...item,...item.requirements,name:item.name||item.goal,blockers:[],confirmable:true}}))}};
    if(fail==='lost_plan'){fail=null;throw new TypeError('response lost after plan');}
    return clone(current);
  }
  if(url.pathname.endsWith('/confirm')){
    confirms++;
    if(rejectConfirm){rejectConfirm=false;throw new BusinessRequestError('selected input changed',409);}
    assert.ok(url.pathname.includes(current.current_window.id));
    current={...current,revision:current.revision+1,confirmed_count:current.confirmed_count+current.current_window.entries.length,
      current_window:{...current.current_window,status:'confirmed',entries:current.current_window.entries.map(x=>({...x,task:{id:x.task_id}}))}};
    tasks+=current.current_window.entries.length;
    if(fail==='lost_confirm'){fail=null;throw new TypeError('response lost after confirm');}
    return clone(current);
  }
  throw new Error('unexpected path '+path);
}
registerCreationPlans({registerTool:t=>tools[t.name]=t},{request,readActionSource:()=>source,
  hasPreview:()=>previewed>0,markPreview:()=>previewed++});
const goals=[{goal:'中文产品介绍',requirements:{language:'zh-CN',duration_target_seconds:30}},
  {goal:'English introduction',requirements:{language:'en-US',duration_target_seconds:30}}];
const create=()=>tools.preview_creation_plan.execute('create',{goal:'中英文各一条',target_count:2,
  duration_target_seconds:30,intent:{kind:scenario==='discussion'?'discussion':'goal_delegation'}});
if(scenario==='rejected_create'){
  await assert.rejects(create(),/invalid delivery/);assert.equal(previewed,0);
  await create();assert.equal(previewed,1);
  assert.deepEqual(calls.filter(x=>x.path==='/api/creation-requests').map(x=>x.body.request_key),['trusted-input','trusted-input']);
}else if(scenario==='lost_create'||scenario==='lost_create_null'){
  await assert.rejects(create(),/response lost/);assert.equal(previewed,0);
  await assert.rejects(create(),/先read_creation_request/);
  if(scenario==='lost_create_null'){
    await assert.rejects(tools.read_creation_request.execute('reconcile',{}),/不能证明请求未写入/);
    await assert.rejects(create(),/先read_creation_request/);assert.equal(previewed,0);
  }else{
    await tools.read_creation_request.execute('reconcile',{});
    assert.equal(previewed,1);await tools.prepare_creation_window.execute('prepare',{goals});
  }
  assert.ok(calls.some(x=>x.path==='/api/assistant/creation-request'));
  assert.equal(calls.filter(x=>x.path==='/api/creation-requests'&&x.method==='POST').length,1);
}else{
  await create();
  if(scenario==='replace'){
    await assert.rejects(tools.prepare_creation_window.execute('old',{goals}),/selected input changed/);
    const count=calls.length;
    await assert.rejects(tools.prepare_creation_window.execute('blind',{goals}),/先read_creation_request/);
    assert.equal(calls.length,count);
    await tools.read_creation_request.execute('read',{});
    const revised=goals.map((g,i)=>({...g,requirements:{...g.requirements,selected_voice:{profile_id:'real-voice-'+i,version:'v2'}}}));
    await tools.prepare_creation_window.execute('revised',{goals:revised});
    assert.deepEqual(calls.find(x=>x.path.endsWith('/replan')).body.goals,revised);
    assert.equal(current.current_window.id,'window-2');
    const posted=calls.length;await tools.prepare_creation_window.execute('same',{goals:revised});assert.equal(calls.length,posted);
  }else if(scenario==='discussion'){
    await tools.prepare_creation_window.execute('first',{goals});
    const revised=[{...goals[0],goal:'修订中文'},goals[1]];
    await tools.prepare_creation_window.execute('revision',{goals:revised});
    assert.equal(windowCount,2);assert.equal(confirms,0);assert.equal(tasks,0);
    const read=await tools.read_creation_request.execute('read',{});
    const revision=JSON.parse(read.content[0].text).revision;
    await assert.rejects(tools.revise_creation_request.execute('stale',{expected_revision:revision-1,requirements:{duration_target_seconds:45}}),/stale/);
    await tools.read_creation_request.execute('read-again',{});
    await tools.revise_creation_request.execute('revise',{expected_revision:revision,requirements:{duration_target_seconds:45}});
    assert.equal(current.requirements.duration_target_seconds,45);assert.equal(current.intent.kind,'discussion');
    assert.equal(current.current_window,null);
  }else if(scenario==='lost_plan'||scenario==='lost_confirm'){
    await assert.rejects(tools.prepare_creation_window.execute('lost',{goals}),/response lost/);
    const count=calls.length;await assert.rejects(tools.prepare_creation_window.execute('blind',{goals}),/先read_creation_request/);
    assert.equal(calls.length,count);
    await tools.read_creation_request.execute('reconcile',{});
    await tools.prepare_creation_window.execute('resume',{goals});
    assert.equal(windowCount,1);assert.equal(confirms,1);
    const posted=calls.length;await tools.prepare_creation_window.execute('again',{goals});assert.equal(calls.length,posted);
  }else if(scenario==='old_input'){
    source.inputId='new-input';const count=calls.length;
    await assert.rejects(tools.prepare_creation_window.execute('old',{goals}),/输入已变化/);assert.equal(calls.length,count);
  }
}
if(['replace','lost_create','lost_plan','lost_confirm'].includes(scenario))assert.equal(tasks,2);
assert.ok(calls.filter(x=>x.method==='POST'&&x.path.endsWith('/confirm')).every(x=>x.body.assistant_turn_id==='trusted-turn'));
console.log(JSON.stringify({scenario,tasks,windowCount,confirms}));
"""


@pytest.mark.parametrize("scenario", ["replace", "discussion", "rejected_create", "lost_create", "lost_create_null", "lost_plan", "lost_confirm", "old_input"])
def test_creation_revision_and_original_request_reconciliation(scenario):
    assert run_tools(REVISION_RUNNER, scenario)["scenario"] == scenario
