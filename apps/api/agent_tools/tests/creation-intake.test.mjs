import test from 'node:test';
import assert from 'node:assert/strict';
import {registerCreationPlans} from '../diary/creation-plans.ts';
import {registerScriptTools} from '../diary/script-tools.ts';
import {registerExecutionTools} from '../diary/execution-tools.ts';

function registry() {
  const tools = {};
  return {tools, pi: {registerTool: tool => {tools[tool.name] = tool;}}};
}

for (const kind of ['discussion', 'goal_delegation', 'final_script']) {
  test(`creation tools preserve ${kind} intent and per-delivery requirements`, async () => {
    const {tools, pi} = registry(), calls = [];
    const intent = {kind, ...(kind === 'final_script' ? {final_script: {title: '原稿', text: '准确正文。'}} : {})};
    let current = null;
    async function request(path, init) {
      path = new URL(path, 'http://test').pathname;
      const body = init?.body ? JSON.parse(init.body) : null;
      calls.push({path, body});
      if (path === '/api/creation-requests') {
        current = {id: 'request', revision: 1, goal: '真实目标', target_count: body.target_count, intent,
          clarification_questions: [], current_window: null};
      } else if (path.endsWith('/windows')) {
        current = {...current, revision: 2, current_window: {id: 'window', status: 'proposed', input_fingerprint: 'a'.repeat(64),
          entries: body.goals.map((item, index) => ({ordinal: index + 1, task_id: `actual-task-${index}`,
            plan: {...item, ...item.requirements, blockers: [], confirmable: true}, task: null}))}};
      } else if (path.endsWith('/confirm')) {
        current = {...current, revision: 3, current_window: {...current.current_window, status: 'confirmed',
          entries: current.current_window.entries.map((item, index) => ({...item, task: {id: `actual-task-${index}`}}))}};
      } else throw new Error(`unexpected request ${path}`);
      return current;
    }
    registerCreationPlans(pi, {request, readActionSource: () => ({turnId: 'trusted-turn', inputId: 'trusted-input'}),
      hasPreview: () => false, markPreview() {}});
    await tools.preview_creation_plan.execute('save', {goal: '真实目标', intent, target_count: kind === 'final_script' ? 1 : 2});
    const goals = [{goal: '同一主题', requirements: {language: 'zh-CN'}},
      {goal: '同一主题', requirements: {language: 'en-US', duration_target_seconds: 30, selected_voice: null}}];
    const deliveries = kind === 'final_script' ? goals.slice(0, 1) : goals;
    const result = await tools.prepare_creation_window.execute('prepare', {goals: deliveries});
    assert.equal(calls[0].body.request_key, 'trusted-input');
    assert.equal(calls[0].body.assistant_turn_id, 'trusted-turn');
    assert.equal(calls[0].body.presenter_share_target, 0);
    assert.deepEqual(calls[0].body.intent, intent);
    assert.deepEqual(calls[1].body.goals, deliveries);
    assert.equal(calls.filter(call => call.path.endsWith('/confirm')).length, kind === 'discussion' ? 0 : 1);
    assert.equal(result.details.preview.request.current_window.status, kind === 'discussion' ? 'proposed' : 'confirmed');
    assert.equal(tools.prepare_creation_window.parameters.properties.goals.items.properties.requirements.additionalProperties, false);
  });
}

test('adopted script can start production in the same bound task despite initial topic stage', async () => {
  const {tools, pi} = registry(), calls = [];
  let pendingPreview = false;
  const section = {id: 'section', title: '标题', content: '真实正文', visual_prompt: '真实画面', evidence_reference_ids: ['actual-source']};
  const draft = {id: 'saved-script', title: '实际文案', sections: [section], updated_at: '2026-10-18T00:00:00Z'};
  async function request(path, init) {
    calls.push({path, body: init?.body ? JSON.parse(init.body) : null});
    if (path.endsWith('/assistant/script-context')) return {creation_intent: {kind: 'goal_delegation'}};
    if (path.endsWith('/assistant/script-draft')) return draft;
    if (path.endsWith('/scripts/saved-script/confirm')) return {task: {confirmed_script_id: draft.id},
      scripts: [{...draft, selection_status: 'confirmed', adoption: {kind: 'delegated_system'}}]};
    if (path.endsWith('/execution/preview')) return {script_id: 'saved-script', input_digest: 'actual-digest', requirements: {can_continue: true}};
    if (path.endsWith('/execution')) return {job: null};
    if (path.includes('/execution/start?')) return {task_id: 'bound-task', job: {id: 'actual-job', status: 'queued'}};
    throw new Error(`unexpected request ${path}`);
  }
  const context = {request, taskId: 'bound-task', stage: 'topic', isTaskRead: () => true,
    readActionSource: () => ({turnId: 'trusted-turn'}),
    hasPreview: () => pendingPreview, markPreview: () => {pendingPreview = true;}};
  registerScriptTools(pi, {...context, getScriptDraftUpdatedAt: () => null, setScriptDraftUpdatedAt() {},
    readCurrentTaskFacts: async () => ({task_id: 'bound-task', stage: 'narration', stage_status: 'not_started',
      confirmed_script_id: draft.id, plan: {selected_voice: null}, execution: {job: null}})});
  registerExecutionTools(pi, {...context, hasCreationAction: () => false, markCreationAction() {}, hasEditControlWrite: () => false,
    readActionSource: () => ({turnId: 'trusted-turn'}), getSignal: () => undefined});
  const adopted = await tools.preview_script_draft.execute('save', {title: draft.title, sections: [section]});
  assert.equal(adopted.details.preview.adopted, true);
  const facts = JSON.parse(adopted.content[0].text).current_task_facts;
  assert.equal(facts.stage, 'narration');
  assert.equal(facts.confirmed_script_id, draft.id);
  assert.equal(facts.plan.selected_voice, null);
  assert.equal(pendingPreview, false);
  assert.deepEqual(calls.find(call => call.path.endsWith('/scripts/saved-script/confirm')).body,
    {title: draft.title, sections: draft.sections, expected_updated_at: draft.updated_at, adoption_kind: 'delegated_system', assistant_turn_id: 'trusted-turn'});
  const started = await tools.start_creation_execution.execute('start', {});
  assert.equal(started.details.status.job.id, 'actual-job');
  const start = calls.find(call => call.path.includes('/execution/start?'));
  assert.ok(start.path.includes('bound-task') && start.path.includes('assistant_turn_id=trusted-turn'));
  assert.deepEqual(start.body, {script_id: 'saved-script', input_digest: 'actual-digest', requirements_confirmed: true});
});

for (const kind of ['manual', 'delegated_system']) {
  test(`an already adopted ${kind} revision is read back without a second confirmation`, async () => {
    const {tools, pi} = registry(), calls = [];
    const adopted = {id: 'same-script', title: '已修订', sections: [], updated_at: 'new-revision',
      selection_status: 'confirmed', adoption: {kind}};
    registerScriptTools(pi, {taskId: 'task', isTaskRead: () => true, hasPreview: () => false,
      markPreview() {throw new Error('adopted revision is not an unadopted preview');},
      readActionSource: () => ({turnId: 'turn'}), getScriptDraftUpdatedAt: () => 'old-revision', setScriptDraftUpdatedAt() {},
      readCurrentTaskFacts: async () => ({task_id: 'task', stage: 'narration', confirmed_script_id: adopted.id}),
      request: async (path, init) => {
        calls.push(path);
        if (path.endsWith('/script-context')) return {creation_intent: {kind: 'goal_delegation'}};
        if (path.endsWith('/script-draft')) return adopted;
        throw new Error('revision must not be confirmed twice');
      }});
    const result = await tools.preview_script_draft.execute('call', {title: adopted.title, sections: []});
    assert.equal(result.details.preview.adopted, true);
    assert.equal(calls.length, 2);
    assert.equal(JSON.parse(result.content[0].text).current_task_facts.stage, 'narration');
  });
}

test('a newly consumed input cannot adopt a draft checked under the previous instruction', async () => {
  const {tools, pi} = registry(), source = {turnId: 'turn', inputId: 'old-input'}, calls = [];
  registerScriptTools(pi, {taskId: 'task', isTaskRead: () => true, hasPreview: () => false, markPreview() {},
    readActionSource: () => source, getScriptDraftUpdatedAt: () => null, setScriptDraftUpdatedAt() {},
    readCurrentTaskFacts: async () => {throw new Error('old checked draft is not adopted');},
    request: async path => {
      calls.push(path);
      if (path.endsWith('/script-context')) return {creation_intent: {kind: 'goal_delegation'}};
      if (path.endsWith('/script-draft')) {source.inputId = 'new-input'; return {id: 'draft', updated_at: 'revision'};}
      throw new Error('must not adopt using changed input');
    }});
  await assert.rejects(tools.preview_script_draft.execute('call', {title: '文案', sections: []}), /要求已变化/);
  assert.equal(calls.length, 2);
});
