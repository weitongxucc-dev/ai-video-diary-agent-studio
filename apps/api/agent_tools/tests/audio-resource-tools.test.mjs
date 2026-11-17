import test from 'node:test';
import assert from 'node:assert/strict';
import {registerAudioResourceTools} from '../diary/audio-resource-tools.ts';
import {BusinessRequestError,createBusinessRequest} from '../diary/transport.ts';

function reader(request) {
  let tool;
  registerAudioResourceTools({registerTool:value=>{tool=value;}},{request,taskId:'task'});
  return tool;
}

// Public TaskMediaJobResponse shape: task identity is enforced by the
// task-scoped host read, not repeated as a response field.
function mediaReceipt(overrides={}) {
  return {operation:'audio_import',target_refs:[],input_digest:'actual-digest',
    created_at:'2026-11-17T04:25:26Z',job_id:'original',status:'queued',
    result_kind:'audio_source',result_url:'/api/creation-tasks/task/media-jobs/original',
    result:null,...overrides};
}

test('queued, running, failed and recovered audio receipts query only the original Job',async()=>{
  const calls=[];
  const response=mediaReceipt();
  const request=async(path,init)=>{calls.push({path,init});return structuredClone(response);};
  assert.equal((await reader(request).execute('first',{job_id:'original'})).details.source_ref,undefined);
  response.status='running';
  assert.equal((await reader(request).execute('running',{job_id:'original'})).details.source_ref,undefined);
  response.status='failed';
  assert.equal((await reader(request).execute('second',{job_id:'original'})).details.source_ref,undefined);
  response.status='succeeded';response.result={source_ref:'artifact://actual',path:'/private/secret'};
  const result=await reader(request).execute('third',{job_id:'original'});
  assert.equal(result.details.source_ref,'artifact://actual');
  assert.equal(result.details.adopted,false);
  assert.ok(!JSON.stringify(result).includes('/private/'));
  assert.ok(calls.every(call=>call.path.endsWith('/media-jobs/original')&&!call.init));
});

test('foreign Job or false success cannot supply an adoptable source',async()=>{
  for(const job of [
    mediaReceipt({job_id:'other',status:'succeeded',result:{source_ref:'foreign'}}),
    mediaReceipt({result_kind:'graphic_source',status:'succeeded'}),
    mediaReceipt({status:'succeeded',result:null}),
  ]) await assert.rejects(reader(async()=>job).execute('read',{job_id:'original'}));
});

test('task-scoped host rejection cannot become a source or trigger another request',async()=>{
  const calls=[];
  const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion:()=>{},
    readCredentials:()=>({}),getSignal:()=>undefined,getToolCallId:()=> 'call',
    fetchRequest:()=>{throw Error('self HTTP forbidden');},
    businessCall:async(operation,args)=>{
      calls.push({operation,args});
      return {ok:false,failure:{status_code:404,reason:'Job does not belong to current task'}};
    }});
  await assert.rejects(reader(request).execute('read',{job_id:'foreign'}));
  assert.deepEqual(calls,[{operation:'read_task_media_job',args:{task_id:'task',job_id:'foreign'}}]);
});

test('lost POST response across tool instances queries the stable original request without reposting',async()=>{
  const calls=[];let saved;
  const request=async(path,init)=>{
    calls.push({path,method:init?.method??'GET'});
    if(init?.method==='POST'){
      const input=JSON.parse(init.body);
      saved={request_id:input.request_id,task_id:'task',status:'recorded',job_id:'original-job'};
      assert.equal(input.assistant_turn_id,'turn');
      throw Error('private network body');
    }
    if(!saved)throw new BusinessRequestError('not found',404);
    return saved;
  };
  const make=()=>{const tools={};registerAudioResourceTools({registerTool:t=>{tools[t.name]=t;}},
    {request,taskId:'task',readActionSource:()=>({turnId:'turn',inputId:'input'})});return tools;};
  const args={resource_id:'music',version:1};
  const unknown=await make().import_audio_resource.execute('call-1',args);
  assert.equal(unknown.details.status,'unknown');
  assert.ok(!JSON.stringify(unknown).includes('private'));
  const recovered=await make().import_audio_resource.execute('call-2',args);
  assert.equal(recovered.details.job_id,'original-job');
  assert.equal(calls.filter(c=>c.method==='POST').length,1);
});

test('resource operations cross the existing host business pipe, never self HTTP',async()=>{
  const calls=[];
  const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion:()=>{},
    readCredentials:()=>({}),getSignal:()=>undefined,getToolCallId:()=> 'call',
    fetchRequest:()=>{throw Error('self HTTP forbidden');},
    businessCall:async(operation,args)=>{calls.push({operation,args});return {ok:true,value:{}};}});
  await request('/api/creation-tasks/task/audio/resources',{method:'POST',body:JSON.stringify({resource_id:'music',version:1,request_id:'original',assistant_turn_id:'turn'})});
  await request('/api/creation-tasks/task/audio/resource-requests/original');
  await request('/api/creation-tasks/task/audio/resources/music/history?version=1');
  assert.deepEqual(calls.map(c=>c.operation),['import_audio_resource','read_audio_resource_request','read_audio_resource_history']);
  assert.equal(calls[0].args.payload.assistant_turn_id,'turn');
  assert.equal(calls[2].args.version,1);
});
