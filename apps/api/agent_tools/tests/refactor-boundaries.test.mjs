import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import * as publicTools from '../diary_tools.ts';
import {createDiaryTools} from '../diary_tools.ts';
import {createBusinessRequest, BusinessRequestError, RequestNotSubmitted} from '../diary/transport.ts';
import {createToolCallScope} from '../diary/registration.ts';
import {verifyTaskVersion} from '../diary/trusted-context.ts';
import {modelParameters, typedEditSchema} from '../diary/schemas.ts';
import {createMaterialSelection} from '../materials/selection.ts';

test('public exports and session registration do not initialize business services', async () => {
  assert.deepEqual(Object.keys(publicTools).sort(), [
    'default', 'createDiaryExtension', 'createDiaryTools', 'typedRequestId', 'readTaskEditCapabilities', 'readMediaScriptCapabilities',
    'readMediaScriptToolCapabilities', 'readCreatorMemoryFacts', 'readCurrentTaskFacts',
  ].sort());
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; throw new Error('unexpected business request'); };
  try {
    const first = new Map(), second = new Map();
    await createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID: 'task_first'}).register({registerTool: tool => first.set(tool.name, tool)});
    await createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID: 'task_second'}).register({registerTool: tool => second.set(tool.name, tool)});
    assert.equal(calls, 0);
    assert.deepEqual([...first.keys()], [...second.keys()]);
    assert.ok(!first.has('preview_edit') && !first.has('apply_edit'));
    assert.ok(first.has('read_typed_edit_request') && first.has('read_task_media_job'));
  } finally { globalThis.fetch = originalFetch; }
});

test('version rejection stays before POST and uses the trusted identity headers', async () => {
  const calls = [];
  const selectedTaskVersion = {timeline_version_id: 'original', timeline_sequence: 2};
  const request = createBusinessRequest({
    apiBase: 'http://fixture.invalid', taskId: 'task_bound', selectedTaskVersion,
    verifyTaskVersion: workspace => verifyTaskVersion(workspace, selectedTaskVersion),
    readCredentials: () => ({authorization: 'Bearer synthetic', cookie: 'fixture=synthetic', localMemberToken: 'synthetic-member'}),
    getSignal: () => undefined,
    fetchRequest: async (url, init) => {
      calls.push({url, method: init.method || 'GET', headers: Object.fromEntries(init.headers)});
      return Response.json({task: {current_timeline_version_id: 'new', timeline_sequence: 3}});
    },
  });
  await assert.rejects(request('/api/creation-tasks/task_bound/edits/exact/preview', {method: 'POST', body: '{}'}), RequestNotSubmitted);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'GET');
  assert.equal(calls[0].headers['x-local-member-token'], 'synthetic-member');
  assert.equal(calls[0].headers.authorization, 'Bearer synthetic');
  assert.equal(calls[0].headers.cookie, 'fixture=synthetic');
});

test('section writes retain original dependencies across unrelated HEAD movement and leave actual conflicts to their owner', async () => {
  const version = {timeline_version_id: 'original', timeline_sequence: 2};
  const payload = {section_id: 'section', base_version_id: 'original', expected_segment_version: 'section-v1',
    dependency_digest: 'original-dependencies', request_id: 'original-request'};
  const calls = [];
  let targetChanged = false;
  const request = createBusinessRequest({apiBase: 'http://fixture.invalid', taskId: 'task', selectedTaskVersion: version,
    verifyTaskVersion: workspace => verifyTaskVersion(workspace, version), readCredentials: () => ({}), getSignal: () => undefined,
    fetchRequest: async (url, init) => {
      const path = new URL(url).pathname;
      calls.push(path);
      assert.equal(init.method, 'POST');
      assert.deepEqual(JSON.parse(init.body), payload);
      return targetChanged ? Response.json({detail: {reason: '目标段依赖已变化', submission_effect: 'rejected'}}, {status: 409})
        : Response.json({request_id: payload.request_id, status: 'queued'});
    }});
  await request('/api/creation-tasks/task/assistant/narration-semantic-review', {method: 'POST', body: JSON.stringify(payload)});
  await request('/api/creation-tasks/task/segments/section/revision', {method: 'POST', body: JSON.stringify(payload)});
  targetChanged = true;
  await assert.rejects(request('/api/creation-tasks/task/segments/section/revision', {method: 'POST', body: JSON.stringify(payload)}),
    error => error instanceof BusinessRequestError && error.message === '目标段依赖已变化');
  assert.deepEqual(version, {timeline_version_id: 'original', timeline_sequence: 2});
  assert.ok(calls.every(path => path !== '/api/creation-tasks/task'));
});

test('bounded section discovery reaches the existing host owner without rebinding the whole timeline', async () => {
  const tools = new Map(), calls = [];
  const runtime = createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID: 'task',
    AI_VIDEO_DIARY_AGENT_TASK_VERSION: JSON.stringify({timeline_version_id: 'original', timeline_sequence: 2})}, undefined,
    async (operation, args) => {
      calls.push({operation, args});
      return {ok: true, value: {task_id: 'task', segments: [{section_id: 'section-25', base_version_id: 'new-head',
        segment_version: 'section-v1', dependency_digest: 'section-dependencies'}], offset: args.offset, total_count: 40, truncated: true}};
    });
  await runtime.register({registerTool: tool => tools.set(tool.name, tool)});
  const result = await tools.get('read_narration_segments').execute('read', {offset: 24, limit: 6});
  assert.deepEqual(calls, [{operation: 'read_narration_segments', args: {task_id: 'task', offset: 24, limit: 6}}]);
  assert.equal(result.details.segments[0].section_id, 'section-25');
  assert.equal(result.details.truncated, true);
});

test('section evidence stays on its read binding while historical observations remain readable after another publication', async()=>{
  const tools=new Map(),calls=[];
  const binding={reference_id:'evidence',kind:'knowledge',object_id:'knowledge',version_or_identity:'original-source-version',
    content_digest:null,binding_status:'fixed'};
  let changed=false;
  const runtime=createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID:'task',
    AI_VIDEO_DIARY_AGENT_TASK_VERSION:JSON.stringify({timeline_version_id:'old-head',timeline_sequence:1})},undefined,
    async(operation,args)=>{
      calls.push({operation,args});
      if(operation==='read_narration_segments') return {ok:true,value:{task_id:'task',segments:[{section_id:'section'}]}};
      if(operation==='read_narration_segment_context') return {ok:true,value:{task_id:'task',section_id:'section',script_id:'script',
        segment_version:'section-version',base_version_id:'new-head',text:'原段正文',source_bindings:[binding]}};
      if(operation==='read_narration_segment_sources') return {ok:true,value:{task_id:'task',section_id:'section',script_id:'script',
        segment_version:'section-version',items:[{binding:changed?{...binding,version_or_identity:'new-version'}:binding,
          status:'matched',text:changed?'不能冒充原依据的新正文':'固定历史正文',window_digest:'only-this-window'}],total_count:1,truncated:false}};
      if(operation==='read_task_observations') return {ok:true,value:{task_id:'task',timeline_version_id:'new-head',timeline_sequence:2,
        observations:[{version_id:'old-head',sha256:'original-media',stale:true}]}};
      throw new Error('unexpected '+operation);
    });
  await runtime.register({registerTool:tool=>tools.set(tool.name,tool)});
  await tools.get('read_narration_segments').execute('list',{});
  const context=await tools.get('read_narration_segment_context').execute('context',{segment_id:'section'});
  const original=await tools.get('read_narration_segment_sources').execute('source',{segment_id:'section',text_offset:12,max_chars:40});
  assert.equal(original.details.items[0].text,'固定历史正文');
  assert.equal(calls.at(-1).args.text_offset,12);assert.equal(calls.at(-1).args.max_chars,40);
  changed=true;
  const changedResult=await tools.get('read_narration_segment_sources').execute('source-again',{segment_id:'section'});
  assert.equal(changedResult.details.items[0].status,'changed');
  assert.equal(changedResult.details.items[0].text,undefined);
  assert.equal(context.details.context.source_bindings[0].version_or_identity,'original-source-version');
  const observations=await tools.get('read_task_observations').execute('observe',{});
  assert.equal(observations.details.observations[0].stale,true);
  assert.equal(observations.details.observations[0].version_id,'old-head');
  assert.ok(!calls.some(call=>call.operation==='read_task_workspace'));
});

test('targeted restore uses the original operation and UUID after an unknown write', async () => {
  const tools = new Map(), calls = [];
  let originalRequest;
  const runtime = createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID:'task',AI_VIDEO_DIARY_AGENT_TURN_ID:'turn',
    AI_VIDEO_DIARY_AGENT_INPUT_ID:'input',AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION:'只撤销较早的段修改，保留后来的镜头修改',
    AI_VIDEO_DIARY_AGENT_TASK_VERSION:JSON.stringify({timeline_version_id:'read-version',timeline_sequence:2})},undefined,
    async(operation,args)=>{
      calls.push({operation,args});
      if (operation==='read_timeline_history') return {ok:true,value:{task_id:'task',items:[
        {operation_id:'first-edit',sequence:2},{operation_id:'later-edit',sequence:3}],total_count:2,truncated:false}};
      if (operation==='restore_timeline') {
        originalRequest=args.payload.request_id;
        assert.equal(args.payload.target_operation_id,'first-edit');
        assert.equal(args.payload.expected_timeline_version_id,'read-version');
        throw new Error('response lost after commit');
      }
      if (operation==='read_timeline_restore_request') return {ok:true,value:{task_id:'task',request_id:originalRequest,
        action:'undo',target_operation_id:'first-edit',status:'applied',operation_id:'inverse-first-edit',result_version_id:'composed-version'}};
      throw new Error('unexpected operation '+operation);
    });
  await runtime.register({registerTool:tool=>tools.set(tool.name,tool)});
  await tools.get('read_edit_history').execute('history',{});
  const unknown=await tools.get('undo_last_edit').execute('undo',{target_operation_id:'first-edit'});
  assert.equal(unknown.details.failure.submission_effect,'unknown');
  assert.equal(unknown.details.request_id,originalRequest);
  const repeat=await tools.get('undo_last_edit').execute('new-call',{target_operation_id:'first-edit'});
  assert.equal(repeat.isError,true);
  const read=await tools.get('read_timeline_restore_request').execute('query',{request_id:originalRequest});
  assert.equal(read.details.receipt.result_version_id,'composed-version');
  assert.equal(calls.filter(call=>call.operation==='restore_timeline').length,1);
  assert.equal(calls.at(-1).args.request_id,originalRequest);
});

test('restore control records are rejected before consuming the write slot', async () => {
  const tools = new Map(), writes = [];
  const runtime = createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID:'task', AI_VIDEO_DIARY_AGENT_TURN_ID:'turn',
    AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION:'撤销上一步修改',
    AI_VIDEO_DIARY_AGENT_TASK_VERSION:JSON.stringify({timeline_version_id:'v3',timeline_sequence:3})},undefined,
    async(operation,args)=>{
      if (operation==='read_task_workspace') return {ok:true,value:{task:{id:'task',current_timeline_version_id:'v3',timeline_sequence:3}}};
      if (operation==='read_timeline_history') return {ok:true,value:{task_id:'task',items:[
        {operation_id:'redo-record',operation_type:'RedoTimelineVersion'},
        {operation_id:'undo-record',operation_type:'UndoTimelineVersion'},
        {operation_id:'lock-edit',operation_type:'SetClipLock'}]}};
      if (operation==='restore_timeline') {
        writes.push(args.payload);
        return {ok:true,value:{restore_request:{task_id:'task',request_id:args.payload.request_id,status:'applied'}}};
      }
      throw new Error('unexpected operation '+operation);
    });
  await runtime.register({registerTool:tool=>tools.set(tool.name,tool)});
  await tools.get('read_edit_history').execute('history',{});
  for (const id of ['redo-record','undo-record']) {
    const rejected = await tools.get('undo_last_edit').execute('wrong-'+id,{target_operation_id:id});
    assert.equal(rejected.isError,true);
    assert.match(JSON.stringify(rejected),/不是原编辑/);
  }
  assert.equal(writes.length,0);
  const restored = await tools.get('undo_last_edit').execute('latest',{});
  assert.equal(restored.isError,false,JSON.stringify(restored));
  assert.equal(restored.details.receipt.status,'applied');
  assert.equal(writes.length,1);
  assert.equal(writes[0].target_operation_id,undefined);
});

for (const phase of ['write','read_not_submitted','read_rejected']) test(`restore ${phase} refusal does not confuse the original write effect`, async()=>{
  const tools=new Map();let writes=0,requestId;
  const notSubmitted={ok:false,failure:{status_code:phase==='read_rejected'?404:409,
    submission_effect:phase==='read_rejected'?'rejected':'not_submitted',reason:'原回执不可读'}};
  const runtime=createDiaryTools({AI_VIDEO_DIARY_AGENT_TASK_ID:'task',AI_VIDEO_DIARY_AGENT_TURN_ID:'turn',
    AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION:'撤销原修改',AI_VIDEO_DIARY_AGENT_TASK_VERSION:JSON.stringify({timeline_version_id:'v1',timeline_sequence:1})},undefined,
    async(operation,args)=>{
      if(operation==='read_timeline_history') return {ok:true,value:{task_id:'task',items:[{operation_id:'original'}]}};
      if(operation==='restore_timeline') {
        writes++;requestId=args.payload.request_id;
        if(phase==='write' && writes===1) return notSubmitted;
        return {ok:true,value:phase!=='write'?{}:{restore_request:{task_id:'task',request_id:requestId,action:'undo',status:'applied'}}};
      }
      if(operation==='read_timeline_restore_request') return notSubmitted;
      throw new Error('unexpected '+operation);
    });
  await runtime.register({registerTool:tool=>tools.set(tool.name,tool)});
  await tools.get('read_edit_history').execute('read',{});
  const first=await tools.get('undo_last_edit').execute('first',{target_operation_id:'original'});
  assert.equal(first.details.failure.submission_effect,phase==='write'?'not_submitted':'unknown');
  assert.equal(writes,1);
  await tools.get('read_edit_history').execute('read-again',{});
  const second=await tools.get('undo_last_edit').execute('corrected',{target_operation_id:'original'});
  if(phase==='write') {assert.equal(second.details.receipt.status,'applied');assert.equal(writes,2);}
  else {assert.equal(second.isError,true);assert.equal(writes,1);}
});

test('transport reports API rejection and registration keeps cancellation scopes separate', async () => {
  const scope = createToolCallScope(() => ({})), tools = new Map(), signals = new Map();
  const request = createBusinessRequest({
    apiBase: 'http://fixture.invalid', selectedTaskVersion: null,
    verifyTaskVersion: () => {}, readCredentials: () => ({}), getSignal: scope.getSignal,
    fetchRequest: async (url, init) => {
      signals.set(new URL(url).pathname, init.signal);
      return Response.json({detail: {reason: 'denied', repair_action: 'read current member'}}, {status: 403});
    },
  });
  const pi = scope.bind({registerTool: tool => tools.set(tool.name, tool)});
  pi.registerTool({name: 'probe', execute: async (_id, {path}) => {
    await Promise.resolve();
    try { await request(path); }
    catch (error) { assert.ok(error instanceof BusinessRequestError); assert.equal(error.repairAction, 'read current member'); }
    return {content: [], details: {path}};
  }});
  const a = new AbortController(), b = new AbortController();
  const results = await Promise.all([
    tools.get('probe').execute('a', {path: '/a'}, a.signal),
    tools.get('probe').execute('b', {path: '/b'}, b.signal),
  ]);
  a.abort();
  assert.equal(signals.get('/a').aborted, true);
  assert.equal(signals.get('/b').aborted, false);
  assert.deepEqual(results.map(result => result.structuredContent), [
    {path: '/a',invocation_input_id:null}, {path: '/b',invocation_input_id:null}]);
});

test('late action results retain their invoking input and readers do not acquire new provenance', async () => {
  let current={inputId:'old-input',inputTurnId:'original-turn'};
  const tools=new Map(), scope=createToolCallScope(()=>current);
  const pi=scope.bind({registerTool:tool=>tools.set(tool.name,tool)});
  let finish;
  const pending=new Promise(resolve=>{finish=resolve;});
  pi.registerTool({name:'apply_typed_edit',execute:async()=>{await pending;return {details:{kind:'edit_request_status'}};}});
  pi.registerTool({name:'read_typed_edit_request',execute:async()=>({details:{kind:'edit_request_status'}})});
  const submitted=tools.get('apply_typed_edit').execute('call',{});
  current={inputId:'new-input',inputTurnId:'original-turn'};
  finish();
  const actual=await submitted;
  assert.equal(actual.details.input_id,'old-input');
  assert.equal(actual.structuredContent.input_turn_id,'original-turn');
  assert.equal((await tools.get('read_typed_edit_request').execute('read',{})).details.input_id,undefined);
});

test('model schemas are cloned from the shared API declaration', () => {
  const schema = {type: 'object', properties: {task_id: {type: 'string'}, request_id: {type: 'string'},
    operation_type: {type: 'string'}, supplied_patch: {type: 'object'}}, required: ['task_id', 'operation_type']};
  const original = structuredClone(schema);
  assert.deepEqual(modelParameters(schema).required, ['operation_type']);
  const editable = typedEditSchema({schema, supported_operations: ['actual_api_operation']});
  assert.deepEqual(editable.properties.operation_type.enum, ['actual_api_operation']);
  assert.deepEqual(editable.properties.supplied_patch, schema.properties.supplied_patch);
  assert.equal(editable.properties.request_id, undefined);
  assert.deepEqual(schema, original);
});

test('selection expectations advance only through their owner after verified writes', () => {
  const original = {scope: 'segment', assetId: 'asset', segmentId: 'segment', expectedUpdatedAt: 'a', expectedSegmentUpdatedAt: 'b'};
  const selection = createMaterialSelection(original);
  selection.boundSelection().assetId = 'foreign';
  assert.equal(selection.boundSelection().assetId, 'asset');
  selection.advanceVersion({updated_at: 'c'}, {updated_at: 'd'});
  assert.equal(selection.boundSelection().expectedUpdatedAt, 'c');
  assert.equal(selection.boundSelection().expectedSegmentUpdatedAt, 'd');
  assert.deepEqual(original, {scope: 'segment', assetId: 'asset', segmentId: 'segment', expectedUpdatedAt: 'a', expectedSegmentUpdatedAt: 'b'});
});

test('new runtime modules are packaged identity inputs and do not depend on assembly', async () => {
  const require = createRequire(import.meta.url);
  const {INPUT_SOURCES} = require('../../../desktop/scripts/generate-build-identity.js');
  const input = INPUT_SOURCES.find(source => source.identityPath === 'agent-tools');
  const desktop = JSON.parse(await readFile(new URL('../../../desktop/package.json', import.meta.url), 'utf8'));
  assert.ok(desktop.build.extraResources.find(resource => resource.to === 'agent-tools').filter.includes('**/*.ts'));
  for (const directory of ['diary', 'materials']) {
    for (const name of await readdir(new URL(`../${directory}/`, import.meta.url))) {
      if (!name.endsWith('.ts')) continue;
      const source = await readFile(new URL(`../${directory}/${name}`, import.meta.url), 'utf8');
      assert.equal(input.include(`${directory}/${name}`), true);
      assert.doesNotMatch(source, /from ['"].*(?:diary_tools|\/app\/main|\.py)['"]/);
      assert.doesNotMatch(source, /process\.env/);
    }
  }
  const entry = await readFile(new URL('../diary_tools.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(entry, /let (?:previewCreated|currentTaskRead|editCapabilities|creationRequest)\b/);
  assert.doesNotMatch(entry, /async function request\(/);
});
