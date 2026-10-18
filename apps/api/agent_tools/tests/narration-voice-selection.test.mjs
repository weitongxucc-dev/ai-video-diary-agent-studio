import test from 'node:test';
import assert from 'node:assert/strict';
import {registerExecutionTools} from '../diary/execution-tools.ts';

function setup({lost = false} = {}) {
  const tools = {}, calls = [];
  let written = false, productionActions = 0;
  const requestId = '87fcab2d-bb01-5f26-8d09-324b940240ab';
  const source = {turnId: 'trusted-turn', inputId: 'trusted-input'};
  const selection = {profile_id: 'voice', version: 'fixed-version'};
  const receipt = {request_id: requestId, task_id: 'task', selected_voice: selection,
    plan_updated_at: 'after-plan', task_updated_at: 'after-task'};
  const facts = () => ({task_id: 'task', stage: 'narration', stage_status: 'not_started',
    updated_at: written ? receipt.task_updated_at : 'before-task', confirmed_script_id: 'actual-script',
    plan: {id: 'plan', updated_at: written ? receipt.plan_updated_at : 'before-plan', selected_voice: written ? selection : null},
    available_voices: [{...selection, runnable: true}]});
  const request = async (path, init) => {
    calls.push({path, body: init?.body ? JSON.parse(init.body) : null});
    if (path.endsWith('/narration/voice')) {
      written = true;
      if (lost) throw new TypeError('connection lost after commit');
      return receipt;
    }
    if (path.includes('/narration/voice/requests/')) return receipt;
    if (path.endsWith('/execution/preview')) return {script_id: 'actual-script', input_digest: 'fixed-input', requirements: {can_continue: true}};
    if (path.endsWith('/execution')) return {job: null};
    if (path.includes('/execution/start?')) return {task_id: 'task', job: {id: 'job', status: 'queued'}};
    throw new Error('unexpected path ' + path);
  };
  registerExecutionTools({registerTool: tool => {tools[tool.name] = tool;}}, {request, taskId: 'task', isTaskRead: () => true,
    hasCreationAction: () => productionActions > 0, markCreationAction: () => {productionActions++;},
    hasEditControlWrite: () => false, hasPreview: () => false, readActionSource: () => source,
    getSignal: () => undefined, typedRequestId: (turn, call) => {assert.equal(turn, source.turnId); return requestId;},
    readCurrentTaskFacts: async () => facts()});
  return {tools, calls, source, requestId, receipt, productionActions: () => productionActions};
}

test('fixed voice selection carries trusted turn and both revisions, then production starts in the same input', async () => {
  const state = setup();
  const result = await state.tools.select_narration_voice.execute('call', {voice_profile_id: 'voice', voice_version: 'fixed-version'});
  assert.equal(result.details.kind, 'voice_selected');
  assert.equal(result.details.current_task_facts.plan.selected_voice.version, 'fixed-version');
  assert.deepEqual(state.calls[0].body, {voice_profile_id: 'voice', voice_version: 'fixed-version', request_id: state.requestId,
    assistant_turn_id: 'trusted-turn', expected_plan_updated_at: 'before-plan', expected_task_updated_at: 'before-task'});
  assert.equal(state.productionActions(), 0);
  const produced = await state.tools.start_creation_execution.execute('start', {});
  assert.equal(produced.details.status.job.id, 'job');
  assert.equal(state.productionActions(), 1);
});

test('a lost response is queried by its original UUID and is not posted again', async () => {
  const state = setup({lost: true});
  const unknown = await state.tools.select_narration_voice.execute('call', {voice_profile_id: 'voice', voice_version: 'fixed-version'});
  assert.equal(unknown.details.kind, 'voice_selection_unknown');
  assert.equal(unknown.details.request_id, state.requestId);
  await assert.rejects(state.tools.select_narration_voice.execute('new-call', {voice_profile_id: 'voice', voice_version: 'fixed-version'}), /先查询原请求/);
  const read = await state.tools.read_narration_voice_selection.execute('read', {request_id: state.requestId});
  assert.deepEqual(read.details.receipt, state.receipt);
  assert.equal(read.details.current_task_facts.plan.selected_voice.profile_id, 'voice');
  assert.equal(state.calls.filter(call => call.body).length, 1);
  assert.equal(state.productionActions(), 0);
});

test('voice not listed as a runnable fixed candidate is never posted', async () => {
  const state = setup();
  await assert.rejects(state.tools.select_narration_voice.execute('call', {voice_profile_id: 'invented', voice_version: 'fixed-version'}), /候选/);
  assert.equal(state.calls.length, 0);
});
