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

// The creative path submits actual text and its evidence, rather than deriving
// a fixed speed adjustment from a phrase such as “紧凑一点”.
test('local rewrite binds complete section, input and semantic comparison to the original request', async () => {
  const {createTypedEditTools}=await import('../diary/typed-edit-tools.ts');
  const {createHash}=await import('node:crypto');
  const tools={}, calls=[];
  const before='适用于干燥地面，最高可达每分钟30米。多余的重复说明。';
  const after='适用于干燥地面，最高可达每分钟30米。';
  const request=async (path,init)=>{
    const body=init?.body?JSON.parse(init.body):null; calls.push({path,body});
    if (path.endsWith('/context')) return {script_id:'script',base_version_id:'v1',dependency_digest:'dependencies',section_id:'section',segment_version:'s1',text:before,adjacent_sections:[]};
    if (path.endsWith('/narration-semantic-review')) return {semantic_review_ref:'review'};
    return {request_id:'request',status:'queued',job:{id:'real-job'}};
  };
  const typed=createTypedEditTools({request,taskId:'task',selectedTaskVersion:{timeline_version_id:'v1',timeline_sequence:1},
    isTaskRead:()=>true,readActionSource:()=>({turnId:'turn',inputId:'input',userInstruction:'第1段紧凑一点但保留卖点'}),
    typedRequestId:()=> 'request',getEditCapabilities:()=>null,hasCreationAction:()=>false,hasEditControlWrite:()=>false,markPreview:()=>{}});
  typed.registerNarrationRevision({registerTool:tool=>{tools[tool.name]=tool;}});
  const params={segment_id:'section',replacement_text:after,protected_rules:[{id:'claim',kind:'qualified_claim',
    source_text:before,required_text:'适用于干燥地面，最高可达每分钟30米',source_reference:'source:claim'}],
    conclusion:'preserved',findings:[{rule_id:'claim',before:'适用于干燥地面，最高可达每分钟30米',
      after:'适用于干燥地面，最高可达每分钟30米',assessment:'preserved',reason:'适用条件、上限限定和数值都保留'}],adoption_mode:'apply'};
  await assert.rejects(tools.revise_narration_segment.execute('call',params),/完整上下文/);
  await tools.read_narration_segment_context.execute('read',{segment_id:'section'});
  const result=await tools.revise_narration_segment.execute('call',params);
  assert.equal(result.details.revision.job.id,'real-job');
  const review=calls[3].body,revision=calls[4].body;
  assert.equal(review.source_text_sha256,createHash('sha256').update(before).digest('hex'));
  assert.equal(review.replacement_text_sha256,createHash('sha256').update(after).digest('hex'));
  assert.equal(review.conclusion,'preserved');
  assert.deepEqual(review.findings,params.findings);
  assert.equal(revision.section_id,'section'); assert.equal(revision.input_id,'input');
  assert.equal(revision.replacement_text,after); assert.equal(revision.semantic_review_ref,'review');
  assert.equal(review.dependency_digest,'dependencies'); assert.equal(revision.dependency_digest,review.dependency_digest);
  await assert.rejects(tools.revise_narration_segment.execute('call',params),/原修改已尝试提交/);
  assert.equal(calls.length,5);
});
