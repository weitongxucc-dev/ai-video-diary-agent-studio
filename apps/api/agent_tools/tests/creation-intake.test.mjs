import test from 'node:test';
import assert from 'node:assert/strict';
import {registerCreationPlans} from '../diary/creation-plans.ts';
import {registerScriptTools} from '../diary/script-tools.ts';
import {registerExecutionTools} from '../diary/execution-tools.ts';
import {registerWorkspaceTools} from '../diary/workspace-tools.ts';

function registry() {
  const tools = {};
  return {tools, pi: {registerTool: tool => {tools[tool.name] = tool;}}};
}

function creationReader(source, request) {
  const {tools, pi} = registry();
  let previewed = false;
  registerCreationPlans(pi, {request, readActionSource: () => source,
    hasPreview: () => previewed, markPreview: () => {previewed = true;}});
  return {tools, previewed: () => previewed};
}

for (const binding of [undefined, '']) {
  test(`empty first lookup is neutral and permits saving with the original key (${String(binding)})`, async () => {
    const source = {turnId: 'first-read-turn', inputId: 'first-read-input', creationRequestId: binding}, calls = [];
    let saved = null;
    const reader = creationReader(source, async (path, init) => {
      calls.push({path, method: init?.method || 'GET', body: init?.body ? JSON.parse(init.body) : null});
      if (init?.method === 'POST') {
        saved = {id: 'actual-request', revision: 1, goal: '真实目标', target_count: 1, confirmed_count: 0,
          intent: {kind: 'goal_delegation'}, requirements: {}, clarification_questions: [], current_window: null};
      }
      return saved;
    });
    const empty = await reader.tools.read_creation_request.execute('read', {});
    assert.equal(empty.details.lookup_status, 'not_found');
    assert.equal(empty.details.request_key, source.inputId);
    assert.match(empty.details.instruction, /不表示提交未发生/);
    assert.equal(Object.hasOwn(empty.details, 'submission_effect'), false);
    assert.equal(Object.hasOwn(empty.details, 'preview'), false);
    assert.equal(reader.previewed(), false);
    assert.equal(calls.length, 1);
    assert.equal(new URL(calls[0].path, 'http://test').searchParams.get('assistant_turn_id'), source.turnId);
    await reader.tools.preview_creation_plan.execute('save', {goal: '真实目标', intent: {kind: 'goal_delegation'}});
    assert.equal(calls[1].method, 'POST');
    assert.equal(calls[1].body.request_key, source.inputId);
    assert.equal(calls[1].body.assistant_turn_id, source.turnId);
    assert.equal(reader.previewed(), true);
    const readBack = await reader.tools.read_creation_request.execute('read-again', {});
    assert.equal(readBack.details.preview.request.id, saved.id);
  });
}

test('null after an unknown submission does not clear reconciliation or permit another POST', async () => {
  const source = {turnId: 'unknown-turn', inputId: 'unknown-input'}, calls = [];
  const reader = creationReader(source, async (path, init) => {
    calls.push({path, method: init?.method || 'GET'});
    if (init?.method === 'POST') throw new TypeError('response lost after submit');
    return null;
  });
  const preview = () => reader.tools.preview_creation_plan.execute('save', {goal: '目标', intent: {kind: 'goal_delegation'}});
  await assert.rejects(preview(), /response lost/);
  await assert.rejects(reader.tools.read_creation_request.execute('reconcile', {}), /不能证明请求未写入/);
  await assert.rejects(preview(), /先read_creation_request/);
  await assert.rejects(reader.tools.prepare_creation_window.execute('prepare', {goals: [{goal: '目标'}]}), /先read_creation_request/);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(reader.previewed(), false);
});

test('null for an existing bound request does not permit creating a replacement identity', async () => {
  const source = {turnId: 'bound-turn', creationRequestId: 'original-request'}, calls = [];
  const reader = creationReader(source, async (path, init) => {calls.push({path, method: init?.method || 'GET'}); return null;});
  await assert.rejects(reader.tools.read_creation_request.execute('read', {}), /不能证明请求未写入/);
  await assert.rejects(reader.tools.preview_creation_plan.execute('save', {goal: '目标', intent: {kind: 'goal_delegation'}}), /先read_creation_request/);
  assert.deepEqual(calls, [{path: '/api/creation-requests/original-request', method: 'GET'}]);
});

test('rebinding tools cannot turn an empty lookup into proof that the old input was never submitted', async () => {
  const source = {turnId: 'restored-turn', inputId: 'restored-input'}, calls = [];
  const request = async (path, init) => {
    calls.push({path, method: init?.method || 'GET'});
    if (init?.method === 'POST') throw new TypeError('original result unknown');
    return null;
  };
  const first = creationReader(source, request);
  await assert.rejects(first.tools.preview_creation_plan.execute('save', {goal: '目标', intent: {kind: 'goal_delegation'}}));
  const restored = await creationReader(source, request).tools.read_creation_request.execute('read-restored', {});
  assert.equal(restored.details.lookup_status, 'not_found');
  assert.equal(restored.details.request_key, source.inputId);
  assert.equal(JSON.stringify(restored).includes('not_created'), false);
  assert.match(restored.details.instruction, /曾提交而结果未知.*原身份对账/);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});

function workspaceReader(request) {
  const {tools, pi} = registry();
  registerWorkspaceTools(pi, {request, taskId: undefined, stage: 'home', selectedRefs: [], selectedProfile: null,
    selectedKnowledge: null, isTaskRead: () => false, verifyTaskVersion() {}, getEditCapabilities: () => null,
    readMaterialSelection: () => null});
  return tools;
}

test('template discovery reaches later catalog pages and only returns bounded metadata', async()=>{
  const tools=workspaceReader(async()=>({items:Array.from({length:66},(_,i)=>({
    id:`template-${i}`,name:i===65?'季度图表':`组件${i}`,version:1,origin:'built_in',
    settings:{kind:'visual_component',visual:{category:i===65?'data':'title',source:'PRIVATE_FULL_SOURCE'}}}))}));
  const first=(await tools.read_video_templates.execute('first',{})).details;
  assert.equal(first.templates.length,50);
  assert.equal(first.next_offset,50);
  const last=(await tools.read_video_templates.execute('next',{offset:first.next_offset})).details;
  assert.equal(last.templates.length,16);
  assert.equal(last.templates.at(-1).id,'template-65');
  assert.equal(last.truncated,false);
  assert.equal(JSON.stringify(last).includes('PRIVATE_FULL_SOURCE'),false);
  const found=(await tools.read_video_templates.execute('search',{query:'季度',category:'data'})).details;
  assert.deepEqual(found.templates.map(item=>item.id),['template-65']);
});

for (const [returned, total] of [[0, 0], [8, 8], [19, 19], [3, 50]]) {
  test(`recent workspace summaries remain bounded and report the actual count (${returned}/${total})`, async () => {
    const calls = [];
    const tools = workspaceReader(async path => {
      calls.push(path);
      return {total_count: total, items: Array.from({length: returned}, (_, index) => ({
        id: `task-${index}`, name: `作品${index}`, stage: 'review', status: 'completed', output_path: '/private/never-project'}))};
    });
    const result = await tools.read_workspace_context.execute('read', {});
    assert.deepEqual(calls, ['/api/creation-tasks']);
    assert.equal(result.details.tasks.length, Math.min(returned, 8));
    assert.equal(result.details.total_count, total);
    assert.equal(result.details.truncated, total > Math.min(returned, 8));
    assert.deepEqual(JSON.parse(result.content[0].text), result.details);
    assert.equal(JSON.stringify(result).includes('never-project'), false);
  });
}

test('content candidates preserve missing evidence rather than inventing it from system tags', async () => {
  const tools = workspaceReader(async path => {
    assert.ok(path.startsWith('/api/production/materials/search?'));
    assert.match(path, /purpose=automatic&include_eligibility=true/);
    return {qualification_message: '当前身份可读取的素材中有1项受自动用途限制。', total_count: 1, truncated: false, matches: [{asset_ref: 'asset:real', segment_ref: 'segment:real', score: .1, reasons: ['仅用途符合'],
      match_tier: 'structural', evidence_excerpt: '', needs_confirmation: true,
      system_tags: ['未经人工核实的机器描述'], start_seconds: 0, end_seconds: 5, duration_seconds: 5, time_range: '0–5'}]};
  });
  const result = await tools.find_materials.execute('find', {query: '现场操作'});
  assert.equal(result.details.matches[0].evidence_excerpt, '');
  assert.equal(result.details.matches[0].needs_confirmation, true);
  assert.match(result.content[0].text, /受自动用途限制/);
  assert.equal(JSON.stringify(result).includes('未经人工核实的机器描述'), false);
  assert.match(tools.find_materials.description, /find_knowledge/);
});

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
