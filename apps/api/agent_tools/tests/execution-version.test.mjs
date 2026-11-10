import test from 'node:test';
import assert from 'node:assert/strict';
import {createExecutionVersionFollower} from '../diary/execution-version.ts';
import {registerExecutionTools} from '../diary/execution-tools.ts';

const old = {timeline_version_id:'edited',timeline_sequence:3};
const next = {timeline_version_id:'delivered',timeline_sequence:4};
function status() {return {task_id:'task',job:{id:'job',task_id:'task',status:'succeeded'},video_ready:true,
  output_version:{job_id:'job',base_version:{...old},result_version:{...next}}};}

test('the submitted original job may advance its exact base only after adoption',()=>{
  const selected={...old};const follower=createExecutionVersionFollower('task',selected);
  follower.observe(status());assert.deepEqual(selected,old);
  follower.submitted({...status(),job:{id:'job',task_id:'task',status:'queued'},video_ready:false});
  follower.observe(status());assert.deepEqual(selected,next);
});

test('foreign jobs, intervening edits, unadopted output and wrong clocks never advance',()=>{
  for (const mutate of [s=>{s.job.id='foreign';},s=>{s.video_ready=false;},s=>{s.job.status='failed';},
    s=>{s.task_id='foreign';},s=>{s.output_version.base_version.timeline_sequence=2;},
    s=>{s.output_version.result_version.timeline_sequence=5;},s=>{delete s.output_version;}]) {
    const selected={...old};const follower=createExecutionVersionFollower('task',selected);
    follower.submitted({...status(),job:{id:'job',task_id:'task',status:'queued'},video_ready:false});
    const value=status();mutate(value);follower.observe(value);assert.deepEqual(selected,old);
  }
  const selected={...old};const follower=createExecutionVersionFollower('task',selected);
  follower.submitted({...status(),video_ready:false});selected.timeline_version_id='human-edit';
  follower.observe(status());assert.equal(selected.timeline_version_id,'human-edit');
});

test('foreground submission and original-job read use the same mutable version fence',async()=>{
  const selected={...old};const tools=new Map();let created=false,submitted=false;
  registerExecutionTools({registerTool:tool=>tools.set(tool.name,tool)}, {
    taskId:'task',selectedTaskVersion:selected,isTaskRead:()=>true,hasCreationAction:()=>created,
    markCreationAction:()=>{created=true;},hasEditControlWrite:()=>false,hasPreview:()=>false,
    readActionSource:()=>({turnId:'turn'}),getSignal:()=>undefined,
    readCurrentTaskFacts:async()=>({}),typedRequestId:()=> 'request',
    request:async(path,init)=>{
      if(path.endsWith('/execution/preview')) return {requirements:{can_continue:true},input_digest:'input',script_id:null};
      if(init?.method==='POST') {submitted=true;return {...status(),job:{id:'job',task_id:'task',status:'queued'},video_ready:false};}
      if(path.includes('job_id=')) {assert.equal(submitted,true);return status();}
      return {task_id:'task',job:null,video_ready:false};
    },
  });
  await tools.get('start_creation_execution').execute('submit',{});
  assert.deepEqual(selected,old);
  await tools.get('read_creation_execution').execute('read',{job_id:'job'});
  assert.deepEqual(selected,next);
});
