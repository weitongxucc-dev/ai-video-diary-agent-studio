import assert from 'node:assert/strict';
import test from 'node:test';
import {randomUUID} from 'node:crypto';
import {registerExecutionTools} from '../diary/execution-tools.ts';
import {BusinessResultUnknown, createBusinessRequest} from '../diary/transport.ts';

function preparationTools(request) {
  const tools=new Map();
  registerExecutionTools({registerTool:tool=>tools.set(tool.name,tool)}, {
    request,taskId:'task',isTaskRead:()=>true,hasCreationAction:()=>false,
    markCreationAction:()=>{throw new Error('preparation must not mark normal production');},
    hasEditControlWrite:()=>false,hasPreview:()=>false,
    readActionSource:()=>({turnId:'turn',inputId:'input'}),getSignal:()=>undefined,
    readCurrentTaskFacts:async()=>{throw new Error('not needed for this preparation');},typedRequestId:()=>randomUUID(),
  });
  return tools;
}
const narrationShape={voice_profile_id:'voice',voice_version:'v1',text:'Hello.',language:'en',output_duration_seconds:1,
  pronunciation_references:[{id:'b',version:'v2'},{id:'a',version:'v1'}]};
const cpuShape={width_px:16,height_px:16,frame_rate:1,duration_seconds:1,clip_count:1,audio_input_count:1,sample_rate:8000,channels:1};

test('narration preparation sends only versioned references and retains the three-action lifecycle',async()=>{
  const calls=[];
  const tools=preparationTools(async(path,options)=>{
    calls.push({path,options});
    if(path.endsWith('/finalize'))return {id:'model-work',state:'done',profile_published:true,normal_work_started:false};
    if(options?.method==='POST')return {id:JSON.parse(options.body).kind==='narration'?'model-work':'cpu-work',state:'queued'};
    return {id:'model-work',state:'done',profile_published:false};
  });
  const prepare=tools.get('prepare_device_resources');
  const first=await prepare.execute('first',{kind:'narration',shape:narrationShape});
  const body=JSON.parse(calls[0].options.body);
  assert.equal(body.kind,'narration');assert.equal(body.request_id,first.details.preparation_key);
  assert.deepEqual(body.shape.pronunciation_references,[{id:'a',version:'v1'},{id:'b',version:'v2'}]);
  assert.equal(body.shape.task_id,undefined);
  const repeated=await prepare.execute('same',{kind:'narration',shape:{...narrationShape,
    pronunciation_references:[{version:'v1',id:'a'},{version:'v2',id:'b'}]}});
  assert.equal(repeated.details.action,'status');assert.equal(calls.length,2);
  await assert.rejects(()=>prepare.execute('active-kind-change',{kind:'cpu',shape:cpuShape,request_id:randomUUID()}));
  assert.equal(calls.length,2);
  await tools.get('finalize_device_preparation').execute('finalize',{request_id:'model-work'});
  const next=await prepare.execute('cpu-after-release',{shape:cpuShape,request_id:randomUUID()});
  assert.equal(next.details.receipt.id,'cpu-work');
  assert.deepEqual(calls.filter(c=>c.options?.method==='POST'&&!c.path.endsWith('/finalize')).map(c=>JSON.parse(c.options.body).kind),['narration','cpu']);
});

test('unknown narration preparation retries only the same key, kind and canonical references',async()=>{
  const bodies=[];
  const tools=preparationTools(async(path,options)=>{
    bodies.push(JSON.parse(options.body));
    if(bodies.length===1)throw new BusinessResultUnknown('connection lost',{});
    return {id:'model-work',state:'queued'};
  });
  const prepare=tools.get('prepare_device_resources');
  const unknown=await prepare.execute('first',{kind:'narration',shape:narrationShape});
  assert.equal(unknown.isError,true);
  await assert.rejects(()=>prepare.execute('changed-text',{kind:'narration',shape:{...narrationShape,text:'Different.'}}));
  await assert.rejects(()=>prepare.execute('changed-kind',{shape:cpuShape}));
  const restored=await prepare.execute('same',{kind:'narration',shape:{...narrationShape,
    pronunciation_references:[{version:'v1',id:'a'},{version:'v2',id:'b'}]}});
  assert.equal(restored.details.receipt.id,'model-work');
  assert.equal(bodies.length,2);assert.deepEqual(bodies[0],bodies[1]);
});

for (const kind of ['narration','montage']) test(`source ${kind} refusal is explicitly unsubmitted and permits an applicable CPU preparation`,async()=>{
  const calls=[];
  const request=createBusinessRequest({selectedTaskVersion:null,verifyTaskVersion:()=>{},
    readCredentials:()=>{throw new Error('host dispatch must not initialize HTTP credentials');},
    getSignal:()=>undefined,fetchRequest:()=>{throw new Error('host dispatch must not use network');},
    businessCall:async(operation,args)=>{
      assert.equal(operation,'prepare_device_resources');
      calls.push(args.payload);
      if(args.payload.kind!== 'cpu') return {ok:false,failure:{status_code:409,
        error:'device_preparation_not_applicable',stage:'device_preparation_preflight',
        reason:'原视频剪辑任务不需要配音或故事板准备。',submission_effect:'not_submitted'}};
      return {ok:true,value:{id:'cpu-work',state:'queued'}};
    }});
  const prepare=preparationTools(request).get('prepare_device_resources');
  const refused=await prepare.execute('inapplicable',{kind,shape:kind==='montage'?{}:narrationShape});
  assert.equal(refused.isError,true);
  assert.equal(refused.details.failure.submission_effect,'not_submitted');
  const cpu=await prepare.execute('applicable',{kind:'cpu',shape:cpuShape});
  assert.equal(cpu.details.receipt.id,'cpu-work');
  assert.deepEqual(calls.map(value=>value.kind),[kind,'cpu']);
  assert.notEqual(calls[0].request_id,calls[1].request_id);
});

test('ordinary dispatched 409 keeps the original preparation key and does not authorize changing kind',async()=>{
  const calls=[];
  const request=createBusinessRequest({selectedTaskVersion:null,verifyTaskVersion:()=>{},readCredentials:()=>({}),
    getSignal:()=>undefined,fetchRequest:()=>{throw new Error('no HTTP');},businessCall:async(operation,args)=>{
      calls.push(args.payload);
      return {ok:false,failure:{status_code:409,reason:'原准备状态尚未核实。'}};
    }});
  const prepare=preparationTools(request).get('prepare_device_resources');
  const first=await prepare.execute('first',{kind:'narration',shape:narrationShape});
  assert.equal(first.isError,true);
  assert.notEqual(first.details.failure.submission_effect,'not_submitted');
  await assert.rejects(()=>prepare.execute('changed',{kind:'cpu',shape:cpuShape}),/原准备尚未核验释放/);
  await prepare.execute('reconcile',{kind:'narration',shape:narrationShape});
  assert.equal(calls.length,2);assert.deepEqual(calls[1],calls[0]);
});
