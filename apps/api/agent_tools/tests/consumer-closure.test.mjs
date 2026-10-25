// Actual nested packaged-resource layout, Node module resolver, and optional
// installed Pi/Jiti extension loader. No account settings or model session.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readdir, mkdir, copyFile, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {execFile, spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const execute = promisify(execFile);
const sourceRoot = fileURLToPath(new URL('../', import.meta.url));

async function resourceTree(t) {
  const root = await mkdtemp(join(tmpdir(), 'yaohe-r6-resources-'));
  const tools = join(root, 'Resources', 'agent-tools');
  await mkdir(tools, {recursive: true});
  // This is the same **/*.ts filter declared by electron-builder. Directory
  // structure is preserved; the test never flattens imports into one file.
  async function copyTree(source, target) {
    for (const entry of await readdir(source, {withFileTypes: true})) {
      if (entry.isDirectory()) {
        await mkdir(join(target, entry.name), {recursive: true});
        await copyTree(join(source, entry.name), join(target, entry.name));
      } else if (entry.isFile() && entry.name.endsWith('.ts')) {
        await copyFile(join(source, entry.name), join(target, entry.name));
      }
    }
  }
  await copyTree(sourceRoot, tools);
  t.after(() => rm(root, {recursive: true, force: true}));
  return {root, extension: join(tools, 'diary_tools.ts'), environment: {
    PATH: process.env.PATH, HOME: root, TMPDIR: root, PI_CODING_AGENT_DIR: join(root, 'agent'), PI_OFFLINE: '1',
  }};
}

test('copied extension dynamically imports its nested modules and performs authenticated HTTP reads', async t => {
  const tree = await resourceTree(t), calls = [];
  const server = createServer((request, response) => {
    calls.push({path: request.url, method: request.method, token: request.headers['x-local-member-token']});
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({id: 'synthetic-profile', revision: 2, common_expressions: [], banned_words: [], style_preferences: {tone: 'plain'}}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e',
    'const {readCreatorMemoryFacts}=await import(process.argv[1]); console.log(JSON.stringify(await readCreatorMemoryFacts()));',
    pathToFileURL(tree.extension).href], {cwd: tree.root, env: {...tree.environment,
    AI_VIDEO_DIARY_AGENT_API_BASE: `http://127.0.0.1:${server.address().port}`,
    AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN: 'synthetic-member'}, timeout: 10000});
  assert.deepEqual(JSON.parse(stdout).style_preferences, {tone: 'plain'});
  assert.deepEqual(calls, [{path: '/api/assistant/memory', method: 'GET', token: 'synthetic-member'}]);
});

test('workspace resume uses the original input after empty lookup and host selection without recreating tasks', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', `
import assert from 'node:assert/strict';
const {registerCreationPlans}=await import(process.argv[1]);
const {createBusinessRequest,BusinessResultUnknown}=await import(process.argv[2]);
const source={turnId:'turn:current',inputId:'input:current'};
const calls=[],tools=new Map();
const candidates=[{request_id:'request:original',title:'原中英文视频组'}];
const readActionSource=()=>source;
const request=createBusinessRequest({apiBase:'http://synthetic.invalid',taskId:undefined,selectedTaskVersion:null,
 verifyTaskVersion(){},readCredentials:()=>({localMemberToken:'synthetic-member'}),readActionSource,getSignal:()=>undefined,
 fetchRequest:async(url,init)=>{
  const parsed=new URL(url);calls.push({url:parsed,init});
  assert.equal(init.headers.get('X-Assistant-Input-ID'),'input:current');
  assert.equal(init.headers.get('X-Local-Member-Token'),'synthetic-member');
  assert.equal(parsed.searchParams.get('assistant_turn_id'),'turn:current');
  if(calls.length===1){assert.equal(parsed.pathname,'/api/assistant/creation-request');return new Response('null');}
  assert.equal(parsed.pathname,'/api/assistant/creation-request/resume');
  assert.equal(init.method,'POST');assert.equal(init.body,undefined);
  if(calls.length===2){
   assert.deepEqual([...parsed.searchParams.keys()],['assistant_turn_id']);
   return Response.json({status:'needs_selection',request_id:null,tasks:[],candidates});
  }
  assert.equal(parsed.searchParams.get('creation_request_id'),'request:original');
  if(calls.length===3)return Response.json({status:'resumed',request_id:'request:original',tasks:[{task_id:'existing-cn',status:'resumed'},{task_id:'existing-en',status:'resumed'}]});
  throw new Error('synthetic response lost');
 }});
registerCreationPlans({registerTool:tool=>tools.set(tool.name,tool)},
 {request,readActionSource,hasPreview:()=>false,markPreview:()=>{throw new Error('resume cannot create a preview')}});
const resume=tools.get('resume_creation_request');
assert.deepEqual(Object.keys(resume.parameters.properties),['creation_request_id']);
assert.equal(resume.parameters.additionalProperties,false);
assert.equal((await tools.get('read_creation_request').execute()).details.lookup_status,'not_found');
const selection=await resume.execute('select',{});
assert.equal(selection.details.kind,'creation_request_resume');
assert.deepEqual(selection.details.receipt.candidates,candidates);
const accepted=await resume.execute('continue',{creation_request_id:'request:original'});
assert.deepEqual(accepted.details.receipt.tasks.map(item=>item.task_id),['existing-cn','existing-en']);
assert.equal(accepted.details.preview,undefined);
await assert.rejects(()=>resume.execute('unknown',{creation_request_id:'request:original'}),
 error=>error instanceof BusinessResultUnknown&&error.businessFailure.submission_effect==='unknown');
assert.equal(calls.length,4,'no hidden lookup, creation or automatic retry');
console.log('workspace resume: PASS');`,
    pathToFileURL(join(tree.root, 'Resources', 'agent-tools', 'diary', 'creation-plans.ts')).href,
    pathToFileURL(join(tree.root, 'Resources', 'agent-tools', 'diary', 'transport.ts')).href],
    {cwd: tree.root, env: tree.environment, timeout: 10000});
  assert.match(stdout, /workspace resume: PASS/);
});

test('installed Pi loader registers the copied extension without a model session', {skip: !process.env.PI_TEST_LOADER_MODULE}, async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--input-type=module', '-e',
    `globalThis.fetch=async()=>{throw new Error('network forbidden during extension loading')};
const {loadExtensions}=await import(process.argv[1]);
const loaded=await loadExtensions([process.argv[2]],process.cwd());
console.log(JSON.stringify({errors:loaded.errors,paths:loaded.extensions.map(e=>e.resolvedPath),tools:loaded.extensions.flatMap(e=>[...e.tools.keys()])}));`,
    process.env.PI_TEST_LOADER_MODULE, tree.extension], {cwd: tree.root,
    env: {...tree.environment, AI_VIDEO_DIARY_AGENT_TASK_ID: 'task_synthetic'}, timeout: 15000});
  const result = JSON.parse(stdout);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.paths, [tree.extension]);
  assert.ok(result.tools.includes('read_current_task') && result.tools.includes('read_typed_edit_request'));
  assert.ok(result.tools.includes('read_selected_material') && result.tools.includes('read_media_script_capabilities'));
});

test('background host projects bounded facts over stdio and never falls back to HTTP', {timeout: 10000}, async t => {
  const tree = await resourceTree(t);
  const host = fileURLToPath(new URL('../../agent_runtime/host.mjs', import.meta.url));
  const child = spawn(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e',
    `globalThis.fetch=()=>{throw new Error('HTTP forbidden in background host')};await import(process.argv[1]);`, pathToFileURL(host).href],
    {cwd: tree.root, env: {...tree.environment, AI_VIDEO_DIARY_AGENT_API_BASE: 'http://127.0.0.1:1'}});
  t.after(() => child.kill());
  let output = '', errors = '', buffer = '', calls = 0;
  child.stderr.on('data', value => { errors += value; });
  child.stdout.on('data', value => {
    output += value;
    buffer += value;
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      const event = JSON.parse(line);
      if (event.type !== 'diary_business_call') continue;
      calls++;
      assert.equal(event.operation, 'read_creator_memory');
      assert.deepEqual(event.arguments, {});
      assert.deepEqual(Object.keys(event).sort(), ['arguments', 'call_id', 'operation', 'tool_call_id', 'type']);
      child.stdin.write(JSON.stringify({type: 'diary_business_result', call_id: event.call_id, ok: true,
        value: {id: 'synthetic-memory', revision: 2, common_expressions: Array(25).fill('x'.repeat(350)),
          banned_words: [], style_preferences: {tone: 'plain'}}}) + '\n');
    }
  });
  child.stdin.write(JSON.stringify({factMode: 'memory', extensionPath: tree.extension, businessEnabled: true, controlEnabled: true}) + '\n');
  const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
  assert.equal(code, 0, errors);
  assert.equal(calls, 1);
  const facts = output.trim().split('\n').map(line => JSON.parse(line)).find(event => event.type === 'diary_preflight_result').value;
  assert.equal(facts.common_expressions.length, 24);
  assert.equal(facts.common_expressions[0].length, 300);
  assert.equal(facts.truncated, true);

  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', `
const {createBusinessRequest,RequestNotSubmitted,BusinessRequestError}=await import(process.argv[1]);
let calls=0;globalThis.fetch=()=>{throw new Error('HTTP forbidden')};
const request=createBusinessRequest({apiBase:'http://127.0.0.1:1',taskId:'task_synthetic',selectedTaskVersion:null,
 verifyTaskVersion(){},readCredentials:()=>{throw new Error('credentials forbidden')},getSignal:()=>undefined,
 fetchRequest:globalThis.fetch,businessCall:async(operation,args)=>{calls++;
  if(operation!=='save_assistant_script_draft'||args.payload.request_id!=='original-request')throw new Error('business request changed');
  return {ok:false,failure:{status_code:422,error:'script_review_invalid',reason_code:'issue_shape',retryable:false,section_index:null,submission_effect:'rejected'}};}});
let rejected=false,unsupported=false;
try{await request('/api/creation-tasks/task_synthetic/assistant/script-draft',{method:'POST',body:JSON.stringify({request_id:'original-request'})});}
catch(error){rejected=error instanceof BusinessRequestError&&error.httpStatus===422&&error.scriptFailure?.reason_code==='issue_shape';}
try{await request('https://arbitrary.invalid/api/assistant/memory');}catch(error){unsupported=error instanceof RequestNotSubmitted;}
if(!rejected||!unsupported||calls!==1)throw new Error('host failure classification or finite operation boundary lost');
console.log('host receipt: PASS');`, pathToFileURL(join(tree.root, 'Resources', 'agent-tools', 'diary', 'transport.ts')).href],
    {cwd: tree.root, env: tree.environment, timeout: 10000});
  assert.match(stdout, /host receipt: PASS/);
});

test('background exact edits, original receipts, voice and media pages use typed host calls', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', `
import assert from 'node:assert/strict';
const {createBusinessRequest}=await import(process.argv[1]);
const calls=[];
const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials(){throw new Error('credentials forbidden')},getSignal:()=>undefined,
 fetchRequest(){throw new Error('HTTP forbidden')},businessCall:async(operation,args)=>{calls.push({operation,args});return {ok:true,value:{saved:true}}}});
const base='/api/creation-tasks/task';
const payload={request_id:'original',operation_type:'DuplicateClip',clip_id:'clip',expected_timeline_version_id:'base',expected_timeline_sequence:3};
await request(base+'/edits/exact',{method:'POST',body:JSON.stringify(payload)});
await request(base+'/edits/exact/preview',{method:'POST',body:JSON.stringify(payload)});
await request(base+'/assistant/edit-requests/original?phase=apply');
await request(base+'/assistant/edit-requests/original?phase=preview');
await request(base+'/narration/voice',{method:'POST',body:JSON.stringify({request_id:'voice-original',voice_profile_id:'voice'})});
await request(base+'/narration/voice/requests/voice-original');
await request(base+'/media-jobs?limit=2&cursor=job-original');
await request(base+'/edits/exact/preview?draft=true',{method:'POST',body:JSON.stringify(payload)});
assert.deepEqual(calls.map(c=>c.operation),['apply_task_edit','preview_task_edit','read_task_edit_request','read_task_edit_request','select_narration_voice','read_narration_voice_request','read_task_media_jobs','preview_task_edit']);
assert.deepEqual(calls[0].args.payload,payload);assert.deepEqual(calls[1].args.payload,payload);
assert.deepEqual(calls[2].args,{task_id:'task',request_id:'original',phase:'apply'});
assert.deepEqual(calls[6].args,{task_id:'task',cursor:'job-original',limit:2});
assert.equal(calls[7].args.draft_only,true);
console.log('common host calls: PASS');`, pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/common host calls: PASS/);
});

test('script window tool binds continuation versions without reading the workspace or retrying conflicts', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types','--input-type=module','-e', `
import assert from 'node:assert/strict';
const {createBusinessRequest,RequestNotSubmitted}=await import(process.argv[1]);
const {registerScriptTools}=await import(process.argv[2]);
const calls=[],tools=new Map();let version='v1';
const request=createBusinessRequest({taskId:'task:original',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials(){throw new Error('credentials forbidden')},getSignal:()=>undefined,
 fetchRequest(){throw new Error('HTTP forbidden')},businessCall:async(operation,args)=>{
  calls.push({operation,args});assert.equal(operation,'read_script_sections');
  if(args.expected_updated_at && args.expected_updated_at!==version)return {ok:false,failure:{status_code:409,reason:'stale',submission_effect:'not_submitted'}};
  const after=args.after_ordinal||0, end=Math.min(45,after+(args.limit||20));
  return {ok:true,value:{task_id:args.task_id,script_id:args.script_id,updated_at:version,total_count:45,
   items:Array.from({length:end-after},(_,i)=>({ordinal:after+i+1,section:{id:'s'+(after+i+1)},digest:'digest'})),
   next_after_ordinal:end<45?end:null,truncated:end<45}};
 }});
registerScriptTools({registerTool:tool=>tools.set(tool.name,tool)},{request,taskId:'task:original'});
const tool=tools.get('read_script_sections');
const first=(await tool.execute('first',{script_id:'script:original'})).details.page;
const second=(await tool.execute('second',{script_id:'script:original',after_ordinal:first.next_after_ordinal,expected_updated_at:first.updated_at})).details.page;
const third=(await tool.execute('third',{script_id:'script:original',after_ordinal:second.next_after_ordinal,expected_updated_at:first.updated_at,limit:5})).details.page;
assert.deepEqual([first.items.length,second.items.length,third.items.length],[20,20,5]);
assert.equal(third.items.at(-1).ordinal,45);assert.equal(third.next_after_ordinal,null);
assert.deepEqual(calls[0].args,{task_id:'task:original',script_id:'script:original',after_ordinal:0});
await assert.rejects(()=>tool.execute('missing-version',{script_id:'script:original',after_ordinal:20}));
await assert.rejects(()=>tool.execute('too-large',{script_id:'script:original',limit:21}));
assert.equal(calls.length,3);
version='v2';
await assert.rejects(()=>tool.execute('stale',{script_id:'script:original',after_ordinal:20,expected_updated_at:'v1'}),error=>error instanceof RequestNotSubmitted&&error.httpStatus===409);
assert.equal(calls.length,4);assert.equal(calls.at(-1).args.expected_updated_at,'v1');
console.log('versioned script window host: PASS');`,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','script-tools.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/versioned script window host: PASS/);
});

test('device preparation correction distinguishes unsubmitted validation from an uncertain conflict', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath,['--experimental-strip-types','--input-type=module','-e',`
import assert from 'node:assert/strict';
const {createBusinessRequest}=await import(process.argv[1]);
const {registerExecutionTools}=await import(process.argv[2]);
const one='11111111-1111-4111-8111-111111111111',two='22222222-2222-4222-8222-222222222222';
for(const failure of [{status_code:422,reason:'invalid',submission_effect:'rejected'},
 {status_code:409,reason:'not submitted',submission_effect:'not_submitted'},
 {status_code:409,reason:'conflict',submission_effect:'rejected'}]){
 const tools=new Map(),calls=[];
 const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion(){},
  readCredentials(){throw Error('credentials forbidden')},getSignal:()=>undefined,fetchRequest(){throw Error('HTTP forbidden')},
  businessCall:async(operation,args)=>{calls.push({operation,args});return calls.length===1?{ok:false,failure}:
   {ok:true,value:{id:'work',state:'waiting',plan_id:'plan'}}}});
 registerExecutionTools({registerTool:tool=>tools.set(tool.name,tool)},{request,taskId:'task',
  readActionSource:()=>({turnId:'turn',inputId:'input'}),typedRequestId:()=>calls.length===0?one:two});
 const tool=tools.get('prepare_device_resources');
 const shape={width_px:16,height_px:16,frame_rate:1,duration_seconds:1,clip_count:1,audio_input_count:1,sample_rate:8000,channels:1};
 await assert.rejects(()=>tool.execute('odd',{shape:{...shape,width_px:17}}));
 assert.equal(calls.length,0);
 const first=await tool.execute('first',{shape});assert.equal(first.isError,true);
 const correction=()=>tool.execute('correct',{shape:{...shape,width_px:32}});
 if(failure.status_code===422 || failure.submission_effect==='not_submitted'){
  const result=await correction();assert.equal(result.details.receipt.id,'work');
  assert.equal(calls.length,2);assert.equal(calls[0].args.payload.request_id,one);assert.equal(calls[1].args.payload.request_id,two);
 }else{await assert.rejects(correction);assert.equal(calls.length,1);}
}
console.log('device preparation correction: PASS');`,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','execution-tools.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/device preparation correction: PASS/);
});

test('device preparation tools use the original key after an unknown result and never invent run or normal work', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath,['--experimental-strip-types','--input-type=module','-e',`
import assert from 'node:assert/strict';
const {createBusinessRequest,RequestNotSubmitted}=await import(process.argv[1]);
const {registerExecutionTools}=await import(process.argv[2]);
const {createToolCallScope}=await import(process.argv[3]);
const tools=new Map(),calls=[];let first=true;
const original='11111111-1111-4111-8111-111111111111',changed='22222222-2222-4222-8222-222222222222';
const source={turnId:'turn:original',inputId:'input:original',inputTurnId:'turn:original'};
const request=createBusinessRequest({taskId:'task:original',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials(){throw new Error('credentials forbidden')},getSignal:()=>undefined,
 fetchRequest(){throw new Error('HTTP forbidden')},businessCall:async(operation,args)=>{
  calls.push({operation,args});assert.equal('actor' in args,false);assert.equal('workspace_id' in args,false);
  if(operation==='prepare_device_resources'){
   if(first){first=false;throw new Error('lost response')}
   return {ok:true,value:{id:args.payload.request_id===changed?'work:next':'work:original',plan_id:'plan:original',state:'waiting',wait_reason:null}};
  }
  if(operation==='read_device_preparation')return {ok:true,value:{id:args.request_id,plan_id:'plan:original',state:'done',wait_reason:null}};
  if(operation==='finalize_device_preparation')return {ok:true,value:{id:args.request_id,state:'done',profile_published:true,profile_id:'profile',revision:'v1',normal_work_started:false}};
  throw Error('unsupported operation');
 }});
const scoped=createToolCallScope(()=>source).bind({registerTool:tool=>tools.set(tool.name,tool)});
registerExecutionTools(scoped,{request,taskId:'task:original',readActionSource:()=>source,
 typedRequestId:(_turn,call)=>call==='first'?original:changed});
const shape={width_px:16,height_px:16,frame_rate:1,duration_seconds:1,clip_count:1,audio_input_count:1,sample_rate:8000,channels:1};
const initial=await tools.get('prepare_device_resources').execute('first',{shape});
assert.equal(initial.isError,true);assert.equal(initial.details.preparation_key,original);
await assert.rejects(()=>tools.get('prepare_device_resources').execute('changed',{shape,request_id:changed}));
await assert.rejects(()=>tools.get('prepare_device_resources').execute('changed-shape',{shape:{...shape,width_px:32}}));
assert.equal(calls.length,1);
const queued=await tools.get('prepare_device_resources').execute('second',{shape:Object.fromEntries(Object.entries(shape).reverse())});
assert.equal(queued.details.receipt.id,'work:original');assert.equal(queued.details.input_id,'input:original');
assert.equal(calls[0].args.payload.request_id,original);assert.equal(calls[1].args.payload.request_id,original);
await assert.rejects(()=>tools.get('prepare_device_resources').execute('duplicate-known',{shape,request_id:changed}));
assert.equal(calls.length,2);
await tools.get('prepare_device_resources').execute('again',{shape});
assert.equal(calls[2].operation,'read_device_preparation');
await tools.get('read_device_preparation').execute('read',{request_id:'work:original'});
await assert.rejects(()=>tools.get('prepare_device_resources').execute('done-not-released',{shape,request_id:changed}));
assert.equal(calls.length,4);
const finalized=await tools.get('finalize_device_preparation').execute('final',{request_id:'work:original'});
assert.equal(finalized.details.receipt.normal_work_started,false);assert.equal(finalized.details.receipt.profile_published,true);
assert.deepEqual(calls.map(c=>c.operation),['prepare_device_resources','prepare_device_resources','read_device_preparation','read_device_preparation','finalize_device_preparation']);
await assert.rejects(()=>request('/api/device/resource-preparations/plan/run',{method:'POST',body:'{}'}),RequestNotSubmitted);
assert.equal(calls.length,5);
const next=await tools.get('prepare_device_resources').execute('new-after-finalize',{shape,request_id:changed});
assert.equal(next.details.receipt.id,'work:next');assert.equal(calls.at(-1).args.payload.request_id,changed);
await assert.rejects(()=>tools.get('prepare_device_resources').execute('active-next-key',{shape,request_id:original}));
assert.equal(calls.length,6);
console.log('device preparation host: PASS');`,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','execution-tools.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','registration.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/device preparation host: PASS/);
});

test('execution capability tool reads the bound task through the host without execution or admission', async t => {
  const tree = await resourceTree(t);
  const {stdout} = await execute(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', `
import assert from 'node:assert/strict';
const {createBusinessRequest,RequestNotSubmitted}=await import(process.argv[1]);
const {registerExecutionTools}=await import(process.argv[2]);
const {createToolCallScope}=await import(process.argv[3]);
const calls=[],tools=new Map();
const facts={admission:'not_evaluated',whole_task_support:'not_inferred',capabilities:[
 {purpose:'pcm_append',configured:true,verification_status:'unknown'},
 {purpose:'final_audio_mix',configured:false,verification_status:'unknown'}]};
const source={turnId:'turn:original',inputId:'input:original',inputTurnId:'turn:original'};
const request=createBusinessRequest({taskId:'task:original',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials(){throw new Error('credentials forbidden')},getSignal:()=>undefined,
 fetchRequest(){throw new Error('HTTP forbidden')},
 businessCall:async(operation,args)=>{calls.push({operation,args});return {ok:true,value:facts}}});
const scoped=createToolCallScope(()=>source).bind({registerTool:tool=>tools.set(tool.name,tool)});
registerExecutionTools(scoped,{request,taskId:'task:original',isTaskRead:()=>false,
 hasCreationAction:()=>false,markCreationAction(){throw new Error('must not start creation')}});
const result=await tools.get('read_execution_capabilities').execute('call:capabilities',{},undefined);
assert.deepEqual(calls,[{operation:'read_execution_capabilities',args:{task_id:'task:original'}}]);
assert.deepEqual(result.details.capabilities,facts);
assert.equal(result.structuredContent.input_id,'input:original');
assert.equal(result.details.capabilities.whole_task_support,'not_inferred');
for(const [path,options] of [
 ['/api/creation-tasks/task%3Aoriginal/assistant/execution-capabilities?admit=true',{}],
 ['/api/creation-tasks/task%3Aoriginal/assistant/execution-capabilities',{method:'POST',body:'{}'}]]){
 await assert.rejects(()=>request(path,options),RequestNotSubmitted);
}
assert.equal(calls.length,1);
console.log('execution capability read-only host: PASS');`,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','execution-tools.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','registration.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/execution capability read-only host: PASS/);
});

test('segment visual decision tools read the original receipt and cannot auto-adopt over the business host', async t => {
  const tree=await resourceTree(t);
  const {stdout}=await execute(process.execPath,['--experimental-strip-types','--input-type=module','-e',`
import assert from 'node:assert/strict';
const {createBusinessRequest,RequestNotSubmitted}=await import(process.argv[1]);
const {registerExecutionTools}=await import(process.argv[2]);
const calls=[],tools=new Map();
const request=createBusinessRequest({taskId:'task',selectedTaskVersion:null,verifyTaskVersion(){},
 readCredentials(){throw new Error('credentials forbidden')},getSignal:()=>undefined,fetchRequest(){throw new Error('HTTP forbidden')},
 businessCall:async(operation,args)=>{calls.push({operation,args});return {ok:true,value:{status:'waiting_for_user',task_id:args.task_id,job_id:args.job_id,section_id:args.section_id}}}});
registerExecutionTools({registerTool:tool=>tools.set(tool.name,tool)}, {request,taskId:'task',isTaskRead:true,hasCreationAction:true,
 markCreationAction(){},json:value=>value,getTurnId:()=> 'original-turn',getInputId:()=> 'original-input'});
await tools.get('read_segment_visual_decision').execute('read',{job_id:'job:original',section_id:'section:original'});
assert.deepEqual(calls,[{operation:'read_segment_visual_decision',args:{task_id:'task',job_id:'job:original',section_id:'section:original',offset:0,limit:8}}]);
assert.equal(tools.has('adopt_segment_visual_decision'),false);
let refused=false;
try{await request('/api/creation-tasks/task/execution/jobs/job%3Aoriginal/sections/section%3Aoriginal/visual-decision',
 {method:'POST',body:JSON.stringify({decision_digest:'original',confirmed_clip_ids:['clip:original'],confirmed:true})});}
catch(error){refused=error instanceof RequestNotSubmitted;}
assert.equal(refused,true);assert.equal(calls.length,1);
console.log('segment visual read-only host: PASS');`,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','transport.ts')).href,
    pathToFileURL(join(tree.root,'Resources','agent-tools','diary','execution-tools.ts')).href],
    {cwd:tree.root,env:tree.environment,timeout:10000});
  assert.match(stdout,/segment visual read-only host: PASS/);
});
