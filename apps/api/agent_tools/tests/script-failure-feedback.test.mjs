import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
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
    readCurrentTaskFacts: async () => {throw new Error('rejection cannot refresh saved task facts');},
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
  assert.match(result.content[0].text, /不得建议等待后重交/);
  assert.match(result.content[0].text, /草稿未保存/);
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
    assert.match(result.content[0].text, /本次未保存送审稿的第2段/);
    assert.doesNotMatch(result.details.failure.reason, /正文/);
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

const locationFor = (text, field, section, fragment) => ({candidate_section_index: section,
  candidate_field: field, candidate_fragment_index: fragment,
  candidate_field_digest: createHash('sha256').update(text, 'utf8').digest('hex')});

for (const [field, section, text, fragment, target] of [
  ['title', 0, '总标题😀', 0, '总标题第1个片段（第1–4个Unicode字符）'],
  ['title', 1, '段落标题😀', 0, '第1段标题第1个片段（第1–5个Unicode字符）'],
  ['content', 1, '😀'.repeat(200) + '无据叙述', 1, '第1段正文第2个片段（第201–204个Unicode字符）'],
  ['visual_prompt', 1, '😀'.repeat(401), 2, '第1段画面描述第3个片段（第401–401个Unicode字符）'],
  ['content', 1, '文'.repeat(4000), 19, '第1段正文第20个片段（第3801–4000个Unicode字符）'],
  ['visual_prompt', 1, '图'.repeat(1000), 4, '第1段画面描述第5个片段（第801–1000个Unicode字符）'],
]) {
  test(`host location for ${section}/${field}/${fragment} matches this unsaved submission by Unicode code points`, async () => {
    const submitted = structuredClone(draft);
    if (section === 0) submitted.title = text;
    else submitted.sections[0][field] = text;
    const location = locationFor(text, field, section, fragment);
    const state = rejectedTool({error: 'script_fact_unsupported', reason_code: 'unsupported_fact', section_index: 1,
      retryable: false, ...location, reason: 'private-model-explanation', repair_action: 'private-response-body'});
    const result = await state.tool.execute('located', submitted);
    assert.equal(result.isError, true);
    for (const [key, value] of Object.entries(location)) assert.equal(result.details.failure[key], value);
    assert.ok(result.details.failure.reason.includes(target));
    assert.match(result.details.failure.reason, /本次未保存送审稿/);
    assert.match(result.details.failure.repair_action, /不是已保存旧稿/);
    assert.equal(JSON.stringify(result).includes('private-model-explanation'), false);
    assert.equal(JSON.stringify(result).includes('private-response-body'), false);
    assert.equal(state.calls.filter(call => call.url.endsWith('/script-draft')).length, 1);
  });
}

for (const invalid of [
  {candidate_section_index: true}, {candidate_section_index: -1}, {candidate_section_index: 21},
  {candidate_section_index: 2}, {candidate_section_index: 0, candidate_field: 'content'},
  {candidate_field: 'unknown-field'}, {candidate_fragment_index: -1}, {candidate_fragment_index: 20},
  {candidate_field: 'title', candidate_fragment_index: 1}, {candidate_field: 'visual_prompt', candidate_fragment_index: 5},
  {candidate_field_digest: 'private-path-or-credential'}, {candidate_fragment_index: null},
  {section_index: true},
]) {
  test(`invalid host locator is omitted atomically: ${JSON.stringify(invalid)}`, async () => {
    const location = {...locationFor(draft.sections[0].content, 'content', 1, 0), ...invalid};
    const state = rejectedTool({error: 'script_goal_conflict', reason_code: 'goal_conflict', section_index: 1,
      retryable: false, ...location});
    const result = await state.tool.execute('invalid-locator', draft);
    assert.equal(Object.keys(result.details.failure).some(key => key.startsWith('candidate_')), false);
    assert.doesNotMatch(result.details.failure.reason, /正文|个片段/);
  });
}

test('old field digest or a fragment outside the actual submitted text cannot target a revised draft', async () => {
  for (const location of [locationFor('old rejected text', 'content', 1, 0),
    locationFor(draft.sections[0].content, 'content', 1, 1)]) {
    const state = rejectedTool({error: 'script_fact_unsupported', reason_code: 'unsupported_fact', section_index: 1,
      retryable: false, ...location});
    const result = await state.tool.execute('stale', draft);
    assert.equal(Object.keys(result.details.failure).some(key => key.startsWith('candidate_')), false);
    assert.doesNotMatch(result.details.failure.reason, /个片段/);
  }
});

test('an invalid review cannot attach a candidate locator or permit the same input to retry', async () => {
  const location = locationFor(draft.sections[0].content, 'content', 1, 0);
  const state = rejectedTool({error: 'script_review_invalid', reason_code: 'issue_reference', section_index: 1,
    retryable: false, ...location});
  const result = await state.tool.execute('invalid-review', draft);
  assert.equal(Object.keys(result.details.failure).some(key => key.startsWith('candidate_')), false);
  await state.tool.execute('blocked', draft);
  assert.equal(state.calls.filter(call => call.url.endsWith('/script-draft')).length, 1);
});

test('script schema separates spoken text, visual instructions and complete evidence references', () => {
  const {tool} = rejectedTool({});
  const fields = tool.parameters.properties.sections.items.properties;
  assert.match(fields.content.description, /仅实际朗读/);
  assert.match(tool.description, /content全部会朗读/);
  assert.match(tool.description, /字数及时长检查/);
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
