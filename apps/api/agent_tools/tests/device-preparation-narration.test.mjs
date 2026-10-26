import assert from 'node:assert/strict';
import test from 'node:test';
import {randomUUID} from 'node:crypto';
import {registerExecutionTools} from '../diary/execution-tools.ts';
import {BusinessResultUnknown} from '../diary/transport.ts';

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
