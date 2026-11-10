import type {ActionSource, TaskVersion} from './contracts.ts';

export function readAgentContext(environment: Record<string, string | undefined>) {
  const apiBase = environment.AI_VIDEO_DIARY_AGENT_API_BASE;
  const taskId = environment.AI_VIDEO_DIARY_AGENT_TASK_ID;
  const stage = environment.AI_VIDEO_DIARY_AGENT_STAGE;
  const selectedRefs = JSON.parse(environment.AI_VIDEO_DIARY_AGENT_SELECTED_REFS || "[]");
  const selectedReferences = JSON.parse(environment.AI_VIDEO_DIARY_AGENT_SELECTED_REFERENCES || "[]");
  const playheadMs = environment.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS
    ? Number(environment.AI_VIDEO_DIARY_AGENT_PLAYHEAD_MS)
    : null;
  const selectedProfile = environment.AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_PROFILE_REFERENCE) : null;
  const selectedKnowledge = environment.AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_KNOWLEDGE_REFERENCE) : null;
  const selectedTaskVersion = environment.AI_VIDEO_DIARY_AGENT_TASK_VERSION ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_TASK_VERSION) : null;

  return {
    apiBase, taskId, stage, selectedRefs, selectedReferences, playheadMs, selectedProfile, selectedKnowledge, selectedTaskVersion,
    initialTaskRead: environment.AI_VIDEO_DIARY_AGENT_TASK_READ_READY === '1',
    initialScriptDraftUpdatedAt: environment.AI_VIDEO_DIARY_AGENT_SCRIPT_DRAFT_UPDATED_AT || null,
    initialEditCapabilities: environment.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_EDIT_CAPABILITIES) : null,
    initialMediaScriptCapabilities: environment.AI_VIDEO_DIARY_AGENT_MEDIA_SCRIPT_CAPABILITIES ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_MEDIA_SCRIPT_CAPABILITIES) : null,
    initialScriptToolCapabilities: environment.AI_VIDEO_DIARY_AGENT_SCRIPT_TOOL_CAPABILITIES ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_SCRIPT_TOOL_CAPABILITIES) : null,
    readMaterialSelection: () => environment.AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION ? JSON.parse(environment.AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION) : null,
    readCredentials: () => ({authorization: environment.AI_VIDEO_DIARY_AGENT_AUTHORIZATION,
      cookie: environment.AI_VIDEO_DIARY_AGENT_COOKIE, localMemberToken: environment.AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN}),
    readActionSource: (): ActionSource => ({turnId: environment.AI_VIDEO_DIARY_AGENT_TURN_ID,
      inputId: environment.AI_VIDEO_DIARY_AGENT_INPUT_ID || undefined, inputTurnId: environment.AI_VIDEO_DIARY_AGENT_INPUT_TURN_ID || undefined,
      userInstruction: environment.AI_VIDEO_DIARY_AGENT_USER_INSTRUCTION, creationRequestId: environment.AI_VIDEO_DIARY_AGENT_CREATION_REQUEST_ID}),
  };
}

export function verifyTaskVersion(workspace: {task?: {current_timeline_version_id?: string | null; timeline_sequence?: number};
  owned_version_transition?: {scope:string;from:TaskVersion;to:TaskVersion}}, selectedTaskVersion: TaskVersion | null, allowOwnedTransitions=false) {
  const transition=workspace.owned_version_transition;
  if (allowOwnedTransitions && selectedTaskVersion && transition?.scope==='same_goal_owned_changes'
      && transition.from.timeline_version_id===selectedTaskVersion.timeline_version_id
      && transition.from.timeline_sequence===selectedTaskVersion.timeline_sequence
      && transition.to.timeline_version_id===workspace.task?.current_timeline_version_id
      && transition.to.timeline_sequence===workspace.task?.timeline_sequence) {
    // Only the task host may prove this goal's actual edit/render lineage.
    // Every subsequent write rechecks that lineage, identity and original input.
    Object.assign(selectedTaskVersion,transition.to);
  }
  if (selectedTaskVersion && (workspace.task?.current_timeline_version_id !== selectedTaskVersion.timeline_version_id || workspace.task?.timeline_sequence !== selectedTaskVersion.timeline_sequence)) throw new Error("任务版本已变化，请重新查看选中对象后发送；未提交操作");
}
