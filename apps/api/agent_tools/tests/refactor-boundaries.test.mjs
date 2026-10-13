import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import * as publicTools from '../diary_tools.ts';
import {createDiaryTools} from '../diary/runtime.ts';
import {createBusinessRequest, BusinessRequestError, RequestNotSubmitted} from '../diary/transport.ts';
import {createToolCallScope} from '../diary/registration.ts';
import {verifyTaskVersion} from '../diary/trusted-context.ts';
import {modelParameters, typedEditSchema} from '../diary/schemas.ts';
import {createMaterialSelection} from '../materials/selection.ts';

test('public exports and session registration do not initialize business services', async () => {
  assert.deepEqual(Object.keys(publicTools).sort(), [
    'default', 'typedRequestId', 'readTaskEditCapabilities', 'readMediaScriptCapabilities',
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

test('transport reports API rejection and registration keeps cancellation scopes separate', async () => {
  const scope = createToolCallScope(), tools = new Map(), signals = new Map();
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
  assert.deepEqual(results.map(result => result.structuredContent), [{path: '/a'}, {path: '/b'}]);
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
