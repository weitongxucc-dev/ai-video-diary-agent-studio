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
  registerScriptTools(pi, {...context, getScriptDraftUpdatedAt: () => null, setScriptDraftUpdatedAt() {}});
  registerExecutionTools(pi, {...context, hasCreationAction: () => false, markCreationAction() {}, hasEditControlWrite: () => false,
    readActionSource: () => ({turnId: 'trusted-turn'}), getSignal: () => undefined});
  const adopted = await tools.preview_script_draft.execute('save', {title: draft.title, sections: [section]});
  assert.equal(adopted.details.preview.adopted, true);
  assert.equal(pendingPreview, false);
  assert.deepEqual(calls.find(call => call.path.endsWith('/scripts/saved-script/confirm')).body,
    {title: draft.title, sections: draft.sections, expected_updated_at: draft.updated_at, adoption_kind: 'delegated_system', assistant_turn_id: 'trusted-turn'});
  const started = await tools.start_creation_execution.execute('start', {});
  assert.equal(started.details.status.job.id, 'actual-job');
  const start = calls.find(call => call.path.includes('/execution/start?'));
  assert.ok(start.path.includes('bound-task') && start.path.includes('assistant_turn_id=trusted-turn'));
  assert.deepEqual(start.body, {script_id: 'saved-script', input_digest: 'actual-digest', requirements_confirmed: true});
});
