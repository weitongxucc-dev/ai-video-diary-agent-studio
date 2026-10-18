import test from 'node:test';
import assert from 'node:assert/strict';
import {createBusinessRequest} from '../diary/transport.ts';
import {registerScriptTools} from '../diary/script-tools.ts';
import {createTaskContext} from '../diary/task-context.ts';

function rejectedTool(detail) {
  const tools = {}, calls = [], source = {turnId: 'trusted-turn', inputId: 'input-one'};
  const request = createBusinessRequest({apiBase: 'https://api.invalid', selectedTaskVersion: null,
    verifyTaskVersion() {}, readCredentials: () => ({}), readActionSource: () => source,
    getSignal: () => undefined, fetchRequest: async (url, init) => {
      calls.push({url, init});
      if (url.endsWith('/script-context')) return Response.json({creation_intent: {kind: 'goal_delegation'}});
      if (url.endsWith('/script-draft')) return Response.json({detail}, {status: 502});
      throw new Error('unexpected write after rejected draft');
    }});
  registerScriptTools({registerTool: tool => {tools[tool.name] = tool;}}, {request, taskId: 'task',
    readActionSource: () => source, isTaskRead: () => true, hasPreview: () => false,
    markPreview() {throw new Error('rejection is not a saved preview');},
    getScriptDraftUpdatedAt: () => null, setScriptDraftUpdatedAt() {throw new Error('rejection cannot advance draft CAS');}});
  return {tool: tools.preview_script_draft, calls, source};
}
const draft = {title: '设备介绍', sections: [{title: '现场', content: '多人协作。', visual_prompt: '展示现场',
  evidence_reference_ids: ['knowledge:real:1']}]};

test('invalid review exposes only safe metadata and stops the same input from submitting again', async () => {
  const secret = 'private-external-error-source-or-credential';
  const state = rejectedTool({error: 'script_review_invalid', reason_code: 'issue_reference', section_index: 3,
    retryable: false, reason: secret, repair_action: secret, response: secret});
  const result = await state.tool.execute('first', draft);
  assert.equal(result.isError, true);
  assert.deepEqual({...result.details.failure, reason: undefined, repair_action: undefined}, {
    error: 'script_review_invalid', reason_code: 'issue_reference', section_index: 3, retryable: false,
    reason: undefined, repair_action: undefined});
  assert.match(result.content[0].text, /尚不能判定正文/);
  assert.equal(JSON.stringify(result).includes(secret), false);
  await state.tool.execute('again', draft);
  assert.equal(state.calls.filter(call => call.url.endsWith('/script-draft')).length, 1);
  state.source.inputId = 'new-consumed-input';
  await state.tool.execute('new-input', draft);
  assert.equal(state.calls.filter(call => call.url.endsWith('/script-draft')).length, 2);
});

for (const [error, reason] of [['script_fact_unsupported', 'unsupported_fact'], ['script_goal_conflict', 'goal_conflict']]) {
  test(`${error} identifies the rejected section and permits a corrected candidate`, async () => {
    const state = rejectedTool({error, reason_code: reason, section_index: 2, retryable: false});
    const result = await state.tool.execute('first', draft);
    assert.equal(result.details.failure.error, error);
    assert.equal(result.details.failure.reason_code, reason);
    assert.equal(result.details.failure.section_index, 2);
    assert.match(result.content[0].text, /第2段正文/);
    await state.tool.execute('corrected', {...draft, title: '修订介绍'});
    assert.equal(state.calls.filter(call => call.url.endsWith('/script-draft')).length, 2);
  });
}

test('untrusted diagnostic fields do not enter tool results', async () => {
  const secret = 'private-external-metadata';
  const state = rejectedTool({error: 'script_review_invalid', reason_code: secret, section_index: secret,
    retryable: secret, reason: secret, repair_action: secret});
  const result = await state.tool.execute('first', draft);
  assert.equal(result.details.failure.reason_code, 'unavailable');
  assert.equal(result.details.failure.section_index, null);
  assert.equal(result.details.failure.retryable, false);
  assert.equal(JSON.stringify(result).includes(secret), false);
});

test('script schema separates spoken text, visual instructions and complete evidence references', () => {
  const {tool} = rejectedTool({});
  const fields = tool.parameters.properties.sections.items.properties;
  assert.match(fields.content.description, /仅实际朗读/);
  assert.match(fields.visual_prompt.description, /不代表已经生成/);
  assert.match(fields.evidence_reference_ids.description, /reference_id 完整引用/);
});

test('current task exposes authorized voice candidates without credentials or a selection write', async () => {
  const paths = [];
  const context = createTaskContext({taskId: 'task', stage: 'narration', selectedRefs: [], playheadMs: null,
    initialTaskRead: false, initialScriptDraftUpdatedAt: null, verifyTaskVersion() {},
    request: async (path, init) => {
      assert.equal(init, undefined);
      paths.push(path);
      if (path.endsWith('/execution')) return {task_id: 'task', job: null};
      return {task: {id: 'task', stage: 'narration'}, plan: {selected_voice: null}, available_voices: [
        {profile_id: 'real-voice', version: 'fixed-v2', person_display_name: '我的声音', language: 'zh-en',
          runnable: true, runnable_reason: null, reference_wav_path: 'private-reference-path'}]};
    }});
  const facts = await context.readCurrentTaskFacts();
  assert.deepEqual(facts.available_voices, [{profile_id: 'real-voice', version: 'fixed-v2', display_name: '我的声音',
    language: 'zh-en', runnable: true, runnable_reason: null}]);
  assert.equal(facts.available_voice_count, 1);
  assert.equal(facts.available_voices_truncated, false);
  assert.equal(JSON.stringify(facts).includes('private-reference-path'), false);
  assert.deepEqual(paths, ['/api/creation-tasks/task', '/api/creation-tasks/task/execution']);
});
