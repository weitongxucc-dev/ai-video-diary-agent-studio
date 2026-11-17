import assert from 'node:assert/strict';
import test from 'node:test';
import {registerEditControls} from '../diary/edit-controls.ts';
import {registerTaskReads} from '../diary/task-reads.ts';
import {createTaskContext} from '../diary/task-context.ts';
import {createBusinessRequest, BusinessResultUnknown} from '../diary/transport.ts';
import {verifyTaskVersion} from '../diary/trusted-context.ts';

function setup({action='undo',loseReply=false,changeProof=()=>{}}={}) {
  const tools=new Map(),calls=[], selected={timeline_version_id:'base',timeline_sequence:3};
  const current={...selected},source={turnId:'turn',inputId:'input',userInstruction:'撤销指定原修改'};
  let wrote=false,receipt;
  const verify=workspace=>verifyTaskVersion(workspace,selected);
  const request=createBusinessRequest({taskId:'task',selectedTaskVersion:selected,verifyTaskVersion:verify,
    readCredentials:()=>({}),getSignal:()=>undefined,getToolCallId:()=> 'tool-call',
    fetchRequest:()=>assert.fail('no self HTTP'),businessCall:async(operation,args)=>{
      calls.push({operation,args});
      if(operation==='read_timeline_history')return {ok:true,value:{task_id:'task',items:[{operation_id:'original',operation_type:'EditAudioTimeline'}]}};
      if(operation==='read_edit_control_context')return {ok:true,value:{task_id:'task',
        timeline_version_id:current.timeline_version_id,timeline_sequence:current.timeline_sequence,
        can_undo:true,can_redo:false,proposals:[]}};
      if(operation==='restore_timeline') {
        assert.equal(args.action,action);
        Object.assign(current,{timeline_version_id:'own-result',timeline_sequence:4});
        receipt={task_id:'task',request_id:args.payload.request_id,action,status:'applied',target_operation_id:'original',
          operation_id:'restore-operation',result_version_id:'own-result',owned_version_transition:{scope:'same_turn_restore',
            task_id:'task',request_id:args.payload.request_id,operation_id:'restore-operation',action,
            assistant_turn_id:'turn',input_id:'input',from:{timeline_version_id:'base',timeline_sequence:3},to:{...current}}};
        changeProof(receipt);
        if(loseReply)throw new BusinessResultUnknown({reason:'lost reply',submission_effect:'unknown',request_id:receipt.request_id});
        return {ok:true,value:{restore_request:receipt}};
      }
      if(operation==='read_timeline_restore_request')return {ok:true,value:{...receipt,request_id:args.request_id}};
      if(operation==='read_task_workspace')return {ok:true,value:{task:{id:'task',stage:'montage',
        current_timeline_version_id:current.timeline_version_id,timeline_sequence:current.timeline_sequence}}};
      if(operation==='read_creation_execution')return {ok:true,value:{task_id:'task',job:null}};
      assert.fail(`unexpected host operation ${operation}`);
    }});
  const context=createTaskContext({request,taskId:'task',selectedRefs:[],playheadMs:null,
    verifyTaskVersion:verify,initialTaskRead:true,initialScriptDraftUpdatedAt:null});
  const registrar={registerTool:tool=>tools.set(tool.name,tool)};
  registerEditControls(registrar,{request,taskId:'task',selectedTaskVersion:selected,verifyTaskVersion:verify,
    ...context,readActionSource:()=>source,typedRequestId:()=> 'original-request',
    hasPreview:()=>false,markPreview(){},hasCreationAction:()=>false,
    hasEditControlWrite:()=>wrote,markEditControlWrite(){wrote=true;},releaseNotSubmittedEditControl(){wrote=false;}});
  registerTaskReads(registrar,{request,taskId:'task',...context,verifyTaskVersion:verify,readTaskEditCapabilities:()=>({})});
  return {tools,calls,selected,current,source,receipt:()=>receipt};
}

for(const action of ['undo','redo'])test(`own ${action} permits same-round task/object reads but retains the history budget`,async()=>{
  const app=setup({action});
  await app.tools.get('read_edit_history').execute('history',{});
  const result=await app.tools.get(`${action}_last_edit`).execute('restore',{target_operation_id:'original'});
  assert.equal(result.isError,false);
  assert.deepEqual(app.selected,{timeline_version_id:'own-result',timeline_sequence:4});
  await app.tools.get('read_current_task').execute('task',{});
  await app.tools.get('read_task_object').execute('object',{kind:'audio_tracks'});
  assert.deepEqual(app.calls.filter(c=>c.operation==='read_task_workspace').at(-1).args.expected_task_version,app.selected);
  await assert.rejects(()=>app.tools.get(`${action}_last_edit`).execute('repeat',{target_operation_id:'original'}),/已提交一次/);
  Object.assign(app.current,{timeline_version_id:'external-result',timeline_sequence:5});
  await assert.rejects(()=>app.tools.get('read_current_task').execute('external',{}),/版本已变化/);
  await assert.rejects(()=>app.tools.get('read_task_object').execute('external-object',{kind:'audio_tracks'}),/版本已变化/);
});

test('lost restore reply advances only after reading its same-round original request, without reposting',async()=>{
  const app=setup({loseReply:true});
  await app.tools.get('read_edit_history').execute('history',{});
  const unknown=await app.tools.get('undo_last_edit').execute('restore',{target_operation_id:'original'});
  assert.equal(unknown.isError,true);
  assert.equal(app.selected.timeline_version_id,'base');
  await app.tools.get('read_timeline_restore_request').execute('recover',{request_id:'original-request'});
  await app.tools.get('read_current_task').execute('read',{});
  assert.equal(app.selected.timeline_version_id,'own-result');
  assert.equal(app.calls.filter(c=>c.operation==='restore_timeline').length,1);
});

test('ordinary music redo may omit the internal target after history, retaining the one-action budget',async()=>{
  const app=setup({action:'redo'});
  app.source.userInstruction='恢复刚才撤销的钢琴音乐，原声不变。';
  await app.tools.get('read_edit_control_context').execute('controls',{});
  await app.tools.get('read_edit_history').execute('history',{});
  const result=await app.tools.get('redo_last_edit').execute('restore',{});
  assert.equal(result.isError,false);
  const posted=app.calls.find(call=>call.operation==='restore_timeline');
  assert.equal(posted.args.payload.target_operation_id,undefined);
  assert.equal(result.details.receipt.target_operation_id,'original');
  await app.tools.get('read_current_task').execute('read',{});
  await assert.rejects(()=>app.tools.get('redo_last_edit').execute('repeat',{}),/已提交一次/);
  assert.equal(app.calls.filter(call=>call.operation==='restore_timeline').length,1);
});

for(const [name,changeProof] of [
  ['missing proof',r=>{delete r.owned_version_transition;}],
  ['foreign task',r=>{r.owned_version_transition.task_id='other';}],
  ['foreign request',r=>{r.owned_version_transition.request_id='other';}],
  ['foreign turn',r=>{r.owned_version_transition.assistant_turn_id='other';}],
  ['foreign input',r=>{r.owned_version_transition.input_id='other';}],
  ['unrelated base',r=>{r.owned_version_transition.from.timeline_version_id='external';}],
  ['wrong sequence',r=>{r.owned_version_transition.to.timeline_sequence=5;}],
  ['foreign operation',r=>{r.owned_version_transition.operation_id='other';}],
  ['foreign result',r=>{r.owned_version_transition.to.timeline_version_id='other';}],
  ['refusal',r=>{r.status='refused';}],
])test(`${name} cannot advance the task fence`,async()=>{
  const app=setup({changeProof});
  await app.tools.get('read_edit_history').execute('history',{});
  await app.tools.get('undo_last_edit').execute('restore',{target_operation_id:'original'});
  assert.equal(app.selected.timeline_version_id,'base');
  await assert.rejects(()=>app.tools.get('read_current_task').execute('read',{}),/版本已变化/);
});

test('querying a request this runtime did not submit cannot advance its fence',async()=>{
  const app=setup({loseReply:true});
  await app.tools.get('read_edit_history').execute('history',{});
  await app.tools.get('undo_last_edit').execute('restore',{target_operation_id:'original'});
  await app.tools.get('read_timeline_restore_request').execute('unrelated-query',{request_id:'another-request'});
  assert.equal(app.selected.timeline_version_id,'base');
});
