import test from 'node:test';
import assert from 'node:assert/strict';
import {registerDirectEdit} from '../diary/direct-edit-tools.ts';

function setup() {
  const tools = {}, calls = [], version = {timeline_version_id: 'base', timeline_sequence: 7};
  const requestId = '16ef9b98-bc7b-5108-aea2-675cf5bcac6f';
  const operation = {id: 'operation', task_id: 'task', status: 'applied', base_version_id: 'base', result_version_id: 'edited'};
  let attempts = 0, verified = 0;
  const declaration = {schema: {properties: {operation_type: {type: 'string'}, overlay_action: {type: 'string'},
    overlay_id: {type: 'string'}, graphic_overlay: {type: 'object'}, cue_id: {type: 'string'}, text: {type: 'string'}},
    required: ['operation_type']}, direct_apply: {endpoint: '/api/creation-tasks/task/edits/exact', operations: [
      {operation_type: 'EditGraphicOverlay', available: true,
        parameter_fields: ['overlay_action', 'overlay_id', 'graphic_overlay'], required_fields: ['overlay_action']},
      {operation_type: 'UpdateSubtitleCue', available: true, parameter_fields: ['cue_id', 'text'], required_fields: ['cue_id', 'text']},
    ]}};
  const request = async (path, init) => {
    calls.push({path, method: init?.method || 'GET', body: init?.body ? JSON.parse(init.body) : null});
    if (init?.method === 'POST') return {operation};
    if (path.includes('/assistant/edit-requests/')) return {status: 'recorded', result_status_code: 200,
      request_id: requestId, phase: 'apply', submitted_input: {assistant_turn_id: 'turn',
        expected_timeline_version_id: 'base', expected_timeline_sequence: 7}, result: {operation}};
    if (path === '/api/creation-tasks/task') return {task: {id: 'task', current_timeline_version_id: 'edited', timeline_sequence: 8}};
    throw new Error('unexpected path: ' + path);
  };
  registerDirectEdit({registerTool: tool => {tools[tool.name] = tool;}}, {request, taskId: 'task', selectedTaskVersion: version,
    isTaskRead: () => true, readActionSource: () => ({turnId: 'turn', userInstruction: '把片头字号改为44'}),
    typedRequestId: () => requestId, getEditCapabilities: () => declaration, getDeclaredCapabilities: () => declaration,
    hasCreationAction: () => false, hasEditControlWrite: () => attempts > 0,
    markDirectEditAttempt: () => {attempts++;}, verifyDirectEdit: () => {verified++;}});
  return {tool: tools.apply_typed_edit, calls, version, attempts: () => attempts, verified: () => verified};
}

const overlay = {id: 'opening-tag-text-overlay', kind: 'text', text: '片头', start_seconds: 0, end_seconds: 3,
  x: 0.5, y: 0.12, width: 0.8, font_size: 44, font_family: 'sans-serif', color: '#fff2aa'};

test('missing or mismatched update IDs do not submit or consume the write; correction succeeds once', async () => {
  const {id, ...withoutId} = overlay;
  for (const fields of [
    {overlay_id: id, graphic_overlay: withoutId},
    {graphic_overlay: overlay},
    {graphic_overlay: withoutId},
    {overlay_id: id, graphic_overlay: {...overlay, id: 'other-layer'}},
  ]) {
    const state = setup(), params = {operation_type: 'EditGraphicOverlay', overlay_action: 'update', ...fields};
    const original = structuredClone(params);
    await assert.rejects(state.tool.execute('invalid', params), {message:
      '更新图文叠加层必须显式提供完全一致的 overlay_id 与 graphic_overlay.id；请读取现有完整图层，仅修改用户指定字段后重试，未提交'});
    assert.deepEqual(state.calls, []);
    assert.equal(state.attempts(), 0);
    assert.deepEqual(params, original);
    assert.deepEqual(state.version, {timeline_version_id: 'base', timeline_sequence: 7});
    const corrected = {operation_type: 'EditGraphicOverlay', overlay_action: 'update', overlay_id: id, graphic_overlay: overlay};
    const result = await state.tool.execute('corrected', corrected);
    assert.equal(result.details.kind, 'edit_applied');
    assert.equal(state.attempts(), 1);
    assert.equal(state.verified(), 1);
    assert.deepEqual(state.calls[0].body, {...corrected, assistant_turn_id: 'turn', request_id: result.details.request_id,
      expected_timeline_version_id: 'base', expected_timeline_sequence: 7});
    await assert.rejects(state.tool.execute('repeat', corrected), /本次输入已有执行或编辑动作/);
    assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
  }
});

test('add without an ID and another declared edit retain their original submission behavior', async () => {
  const {id, ...withoutId} = overlay;
  for (const params of [
    {operation_type: 'EditGraphicOverlay', overlay_action: 'add', graphic_overlay: withoutId},
    {operation_type: 'UpdateSubtitleCue', cue_id: 'cue', text: '新字幕'},
  ]) {
    const state = setup();
    const result = await state.tool.execute('call', params);
    assert.equal(result.details.kind, 'edit_applied');
    assert.equal(state.attempts(), 1);
    assert.equal(state.verified(), 1);
    assert.deepEqual(state.calls[0].body, {...params, assistant_turn_id: 'turn', request_id: result.details.request_id,
      expected_timeline_version_id: 'base', expected_timeline_sequence: 7});
    assert.equal(state.calls.filter(call => call.method === 'POST').length, 1);
  }
});
