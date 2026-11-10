import test from 'node:test';
import assert from 'node:assert/strict';
import {verifyTaskVersion} from '../diary/trusted-context.ts';

test('only task-host proof with exact original and result versions advances a fence',()=>{
  const old={timeline_version_id:'original',timeline_sequence:2};
  const next={timeline_version_id:'own-render',timeline_sequence:4};
  const workspace={task:{current_timeline_version_id:next.timeline_version_id,timeline_sequence:next.timeline_sequence},
    owned_version_transition:{scope:'same_goal_owned_changes',from:old,to:next}};
  assert.throws(()=>verifyTaskVersion(workspace,{...old}),/版本已变化/);
  assert.throws(()=>verifyTaskVersion(workspace,{...old,timeline_sequence:1},true),/版本已变化/);
  assert.throws(()=>verifyTaskVersion({...workspace,owned_version_transition:{...workspace.owned_version_transition,
    to:{...next,timeline_sequence:3}}},{...old},true),/版本已变化/);
  const selected={...old};verifyTaskVersion(workspace,selected,true);
  assert.deepEqual(selected,next);
  assert.deepEqual(old,{timeline_version_id:'original',timeline_sequence:2});
});
