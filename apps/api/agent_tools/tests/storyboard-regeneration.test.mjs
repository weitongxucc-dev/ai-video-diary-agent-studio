import test from 'node:test';
import assert from 'node:assert/strict';
import {registerExecutionTools} from '../diary/execution-tools.ts';
import {BusinessRequestError} from '../diary/transport.ts';

function setup({failure, changedInput = false, wrongReceipt = false, timeline = true} = {}) {
  const tools = {}, calls = [], source = {turnId: 'trusted-turn', inputId: 'trusted-input'};
  const requestId = 'f2ec069d-d8f4-586e-9efa-c7716e14ebc1';
  let actions = 0, status = 'queued';
  const receipt = () => ({request_id: requestId, task_id: wrongReceipt ? 'other-task' : 'task', job_id: 'original-job',
    status, stage: 'montage_plan', result: status === 'succeeded' ? {request_id: requestId,
      timeline_version_id: 'new-version', timeline_sequence: 8, montage_plan_id: 'plan'} : null});
  const request = async (path, init) => {
    calls.push({path, method: init?.method || 'GET', body: init?.body ? JSON.parse(init.body) : null});
    if (path.endsWith('/execution/preview')) return {task_id: 'task', script_id: 'adopted-script', input_digest: 'a'.repeat(64),
      requirements: {can_continue: false}}; // Ordinary confirmation does not redefine explicit regeneration.
    if (path.endsWith('/montage/plan')) {
      if (failure === 'lost') throw new TypeError('lost after job registration');
      if (failure === 'busy') throw new BusinessRequestError('原制作安排尚未结束；先取消并回读终态', 409);
      return receipt();
    }
    if (path.includes('/montage/plan/requests/')) return receipt();
    throw new Error('unexpected path: ' + path);
  };
  registerExecutionTools({registerTool: tool => {tools[tool.name] = tool;}}, {request, taskId: 'task', isTaskRead: () => true,
    hasCreationAction: () => actions > 0, markCreationAction: () => {actions++;}, hasEditControlWrite: () => false,
    hasPreview: () => false, readActionSource: () => source, getSignal: () => undefined,
    typedRequestId: (turn, call) => {assert.equal(turn, 'trusted-turn'); assert.equal(call, 'call'); return requestId;},
    readCurrentTaskFacts: async () => {
      if (changedInput) source.inputId = 'new-input';
      return {task_id: 'task', confirmed_script_id: 'adopted-script', current_timeline_version_id: timeline ? 'base' : null,
        timeline_sequence: 7};
    }});
  return {tools, calls, requestId, actions: () => actions, succeed: () => {status = 'succeeded';}, receipt};
}

test('explicit regeneration binds adopted input and timeline; receipt is a queued storyboard only', async () => {
  const state = setup();
  const result = await state.tools.regenerate_storyboard.execute('call', {});
  assert.equal(result.details.kind, 'storyboard_regeneration_receipt');
  assert.equal(result.details.receipt.status, 'queued');
  assert.deepEqual(state.calls.find(call => call.method === 'POST').body, {request_id: state.requestId,
    assistant_turn_id: 'trusted-turn', script_id: 'adopted-script', input_digest: 'a'.repeat(64),
    expected_timeline_version_id: 'base', expected_timeline_sequence: 7});
  assert.equal(state.actions(), 1);
  const start = await state.tools.start_creation_execution.execute('start', {});
  assert.equal(start.isError, true);
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
});

test('unknown result queries the original UUID and never resubmits, starts, cancels or renders', async () => {
  const state = setup({failure: 'lost'});
  const result = await state.tools.regenerate_storyboard.execute('call', {});
  assert.equal(result.details.kind, 'storyboard_regeneration_unknown');
  assert.equal(result.details.request_id, state.requestId);
  await assert.rejects(state.tools.regenerate_storyboard.execute('new-call', {}), /先查询原请求/);
  state.succeed();
  const read = await state.tools.read_storyboard_regeneration.execute('query', {request_id: state.requestId});
  assert.deepEqual(read.details.receipt, state.receipt());
  assert.equal(state.calls.at(-1).path, result.details.query_url);
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
});

test('an active original production is rejected with its actual reason and is never cancelled automatically', async () => {
  const state = setup({failure: 'busy'});
  const result = await state.tools.regenerate_storyboard.execute('call', {});
  assert.equal(result.details.kind, 'storyboard_regeneration_rejected');
  assert.match(result.details.failure.reason, /先取消/);
  assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
  assert.ok(!state.calls.some(call => call.path.includes('/execution/cancel')));
});

test('changed input and missing timeline are not submitted', async () => {
  for (const options of [{changedInput: true}, {timeline: false}]) {
    const state = setup(options);
    await assert.rejects(state.tools.regenerate_storyboard.execute('call', {}));
    assert.equal(state.calls.filter(call => call.method === 'POST').length, 0);
    assert.equal(state.actions(), 0);
  }
});

test('a receipt from a different task is rejected without claiming completion', async () => {
  const state = setup({wrongReceipt: true});
  await assert.rejects(state.tools.regenerate_storyboard.execute('call', {}), /先查询原请求/);
  await assert.rejects(state.tools.read_storyboard_regeneration.execute('read', {request_id: state.requestId}), /身份不一致/);
});
