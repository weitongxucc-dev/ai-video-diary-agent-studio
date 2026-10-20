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
