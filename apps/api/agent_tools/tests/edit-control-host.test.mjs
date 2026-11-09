import assert from 'node:assert/strict';
import test from 'node:test';
import {randomUUID} from 'node:crypto';
import {registerEditControls} from '../diary/edit-controls.ts';
import {createBusinessRequest, RequestNotSubmitted, BusinessResultUnknown} from '../diary/transport.ts';

function context() {
  return {task_id:'task',timeline_version_id:'version',timeline_sequence:3,can_undo:true,can_redo:false,
    proposals:[{id:'proposal:current',summary:'画面和声音',operation_type:'EditCompoundTimeline',steps:[
      {index:0,command:'画面亮一点',enabled:true},{index:1,command:'声音小一点',enabled:true}]}]};
}

function setup(dispatch) {
  const tools=new Map(),calls=[];
  const version={timeline_version_id:'version',timeline_sequence:3};
  let wrote=false,preview=false;
  const verifyTaskVersion=value=>{
    assert.equal(value.task.current_timeline_version_id,version.timeline_version_id);
    assert.equal(value.task.timeline_sequence,version.timeline_sequence);
  };
  const request=createBusinessRequest({taskId:'task',selectedTaskVersion:version,verifyTaskVersion,
    readCredentials(){throw new Error('host tool must not initialize HTTP credentials');},
    fetchRequest(){throw new Error('host tool must not use an HTTP fallback');},getSignal:()=>undefined,
    businessCall:async(operation,args)=>{
      calls.push({operation,args});
      if(operation==='read_task_workspace')return {ok:true,value:{task:{id:'task',current_timeline_version_id:'version',timeline_sequence:3}}};
      return dispatch(operation,args);
    }});
  registerEditControls({registerTool:tool=>tools.set(tool.name,tool)},{request,taskId:'task',selectedTaskVersion:version,
    isTaskRead:()=>true,markTaskRead(){},verifyTaskVersion,typedRequestId:()=>randomUUID(),
    readActionSource:()=>({turnId:'trusted-turn',inputId:'trusted-input',userInstruction:'取消这份修改方案'}),
    hasPreview:()=>preview,markPreview(){preview=true;},hasCreationAction:()=>false,
    hasEditControlWrite:()=>wrote,markEditControlWrite(){wrote=true;},releaseNotSubmittedEditControl(){wrote=false;}});
  return {tools,request,calls};
}

test('edit control read, bounded selection and discard use actual typed host operations',async()=>{
  const app=setup(async(operation,args)=>{
    if(operation==='read_edit_control_context')return {ok:true,value:context()};
    if(operation==='preview_edit_selection')return {ok:true,value:{proposal:{summary:'仅保留画面',
      preview:{before:{detail:'原参数'},after:{detail:'草稿参数'}}}}};
    assert.equal(operation,'discard_edit_proposal');
    return {ok:true,value:{task_id:'task',proposal_id:args.proposal_id,status:'rejected',timeline_version_id:'version'}};
  });
  const read=await app.tools.get('read_edit_control_context').execute('read',{});
  assert.deepEqual(read.details.context,context());
  const selection=await app.tools.get('preview_edit_selection').execute('select',{proposal_index:1,selection:[0]});
  assert.equal(selection.details.kind,'edit_preview');
  const selected=app.calls.find(item=>item.operation==='preview_edit_selection');
  assert.deepEqual(selected.args,{task_id:'task',proposal_id:'proposal:current',payload:{included_step_indices:[0]},draft_only:true});
  await app.tools.get('discard_edit_proposal').execute('discard',{proposal_index:1});
  const discarded=app.calls.find(item=>item.operation==='discard_edit_proposal');
  assert.deepEqual(discarded.args,{task_id:'task',proposal_id:'proposal:current',payload:{assistant_turn_id:'trusted-turn',
    expected_timeline_version_id:'version',expected_timeline_sequence:3}});
});

test('unsupported edit-control paths and ambiguous query forms stay unsubmitted',async()=>{
  const app=setup(async()=>{throw new Error('unsupported calls must not reach the host');});
  for(const path of ['/api/creation-tasks/task/edit-controls?other=1',
    '/api/creation-tasks/task/edits/proposal/selection/preview?draft=true&draft=false',
    '/api/creation-tasks/task/edits/proposal/selection/preview?draft=maybe',
    '/api/creation-tasks/task/edits/proposal/reject?draft=true',
    '/api/creation-tasks/task/edits/proposal/anything']) {
    await assert.rejects(()=>app.request(path,path.includes('/edits/')?{method:'POST',body:'{}'}:undefined),
      error=>error instanceof RequestNotSubmitted && error.businessFailure.submission_effect==='not_submitted');
  }
  assert.deepEqual(app.calls,[]);
});

test('unknown discard response keeps the original attempted control and never repeats its write',async()=>{
  const app=setup(async operation=>operation==='read_edit_control_context'?{ok:true,value:context()}:
    {ok:false,failure:{status_code:503,submission_effect:'unknown',reason:'原取消结果待查询'}});
  await app.tools.get('read_edit_control_context').execute('read',{});
  await assert.rejects(()=>app.tools.get('discard_edit_proposal').execute('discard',{proposal_index:1}),BusinessResultUnknown);
  await assert.rejects(()=>app.tools.get('discard_edit_proposal').execute('repeat',{proposal_index:1}),/已提交一次/);
  assert.equal(app.calls.filter(item=>item.operation==='discard_edit_proposal').length,1);
});
