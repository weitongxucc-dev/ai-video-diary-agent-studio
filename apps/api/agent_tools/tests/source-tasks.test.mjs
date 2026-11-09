import test from 'node:test';
import assert from 'node:assert/strict';
import {registerSourceTasks} from '../diary/source-tasks.ts';
import {BusinessRequestError, BusinessResultUnknown} from '../diary/transport.ts';

function setup(request, source = {turnId: 'original-turn', inputId: 'original-input'}) {
  const tools = {};
  registerSourceTasks({registerTool: tool => tools[tool.name] = tool}, {request, readActionSource: () => source});
  return {tools, source};
}
const params = {asset_id: 'video', name: '原片', aspect_ratio: '16:9', preserve_audio: true};
const asset = {media_type: 'video', duration_seconds: 9.234, updated_at: '2026-11-09T01:00:00Z'};

test('source request keeps actual fractional duration and fixed input identity across calls', async () => {
  const writes = [];
  const {tools} = setup(async (path, init) => {
    if (path.endsWith('/input')) return null;
    if (!init) return asset;
    assert.match(path, /assistant_turn_id=original-turn/);
    const payload = JSON.parse(init.body);
    writes.push(payload);
    return {request_id: payload.request_id, status: 'created', task_id: 'persisted-task'};
  });
  const first = await tools.create_source_task.execute('call-one', params);
  const again = await tools.create_source_task.execute('different-model-call', params);
  assert.equal(first.details.receipt.task_id, 'persisted-task');
  assert.equal(again.details.receipt.request_id, first.details.receipt.request_id);
  assert.equal(writes[0].source_end_seconds, 9.234);
  assert.equal(writes[0].expected_asset_updated_at, asset.updated_at);
  assert.deepEqual(writes[1], writes[0]);
});

test('lost source response exposes original query, and reads the committed result', async () => {
  let receipt;
  const {tools} = setup(async (path, init) => {
    if (path.endsWith('/input')) return null;
    if (path.startsWith('/api/production/materials/')) return asset;
    if (init) {
      receipt = {request_id: JSON.parse(init.body).request_id, task_id: 'actual-task', status: 'created'};
      throw new TypeError('response lost after commit');
    }
    assert.equal(path, `/api/source-tasks/${receipt.request_id}`);
    return receipt;
  });
  await assert.rejects(tools.create_source_task.execute('create', params), error => {
    assert.ok(error instanceof BusinessResultUnknown);
    assert.equal(error.businessFailure.request_id, receipt.request_id);
    assert.equal(error.businessFailure.submission_effect, 'unknown');
    return true;
  });
  assert.deepEqual((await tools.read_source_task.execute('read', {})).details.receipt, receipt);
});

test('a missing receipt stays unknown and an input change during source read cannot write', async () => {
  let writes = 0;
  const source = {turnId: 'turn', inputId: 'first-input'};
  const {tools} = setup(async (path, init) => {
    if (path.endsWith('/input')) return null;
    if (init) writes++;
    if (path.startsWith('/api/source-tasks/')) throw new BusinessRequestError('not found', 404);
    source.inputId = 'changed-input';
    return asset;
  }, source);
  assert.equal((await tools.read_source_task.execute('read', {})).details.status, 'not_found');
  await assert.rejects(tools.create_source_task.execute('create', params), /输入已变化/);
  assert.equal(writes, 0);
});

test('unknown source input cannot be rebound and survives a new tool runtime', async () => {
  let frozen, posts = 0;
  const request = async (path, init) => {
    if (path.endsWith('/input')) return frozen ? {payload: frozen} : null;
    if (path.startsWith('/api/production/materials/')) return asset;
    if (init) {
      const body = JSON.parse(init.body);
      if (frozen) assert.deepEqual(body, frozen);
      frozen = body;
      posts++;
      if (posts === 1) throw new TypeError('lost original response');
      return {request_id: frozen.request_id, task_id: 'same-task'};
    }
    return {request_id: frozen.request_id, task_id: 'same-task'};
  };
  const first = setup(request);
  await assert.rejects(first.tools.create_source_task.execute('first', params), BusinessResultUnknown);
  await assert.rejects(first.tools.create_source_task.execute('changed', {...params, preserve_audio: false}), /原参数/);
  assert.equal(posts, 1);
  const reopened = setup(async (path, init) => {
    assert.ok(!path.startsWith('/api/production/materials/'), 'recovery must not substitute a new asset revision');
    return request(path, init);
  });
  assert.equal((await reopened.tools.create_source_task.execute('resume', params)).details.receipt.task_id, 'same-task');
  first.source.inputId = 'new-input';
  const original = await first.tools.read_source_task.execute('read-old', {request_id: frozen.request_id});
  assert.equal(original.details.receipt.task_id, 'same-task');
  assert.equal(original.details.receipt.request_id, frozen.request_id);
});
