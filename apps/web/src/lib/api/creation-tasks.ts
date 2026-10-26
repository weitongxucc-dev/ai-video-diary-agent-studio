import type { CreationTaskScriptGenerationInput, CreationTaskScriptGenerationReceipt } from "../types/creation-tasks";
import type { CreationTaskStoryboardRegenerationInput, CreationTaskStoryboardRegenerationReceipt } from '../types/creation-tasks';
import type { TaskMediaJob } from '../types/task-edits';
import type { CreationTaskNarrationVoiceSelectionRequest, CreationTaskNarrationVoiceSelectionResponse } from '../types/creation-tasks';
import { ensureLocalMemberIdentity, readLocalMemberToken } from "../local-member-auth";
import { isLocalAuthMode } from "../auth/routing";
import type { CreationTasksResponse, CreationTaskWorkspace, CreationExecutionPreview, CreationExecutionStatus, TaskJobProgress, CreationTaskScriptsResponse, CreationTaskScript, CreationTaskScriptSection, CreationTaskScriptSectionWindow, CreationTaskScriptWindowStartInput, CreationTaskScriptSectionsPatchInput, CreationTaskScriptWindowSaved, CreationTaskScriptWindowUnsettled, CreationTaskScriptConfirmSavedInput, CreationTaskScriptConfirmed, CreationTaskScriptWindowResult, CreationTaskScriptRequestLocator, CreationTaskPersonRender, CreationTaskTopicsResponse } from '../types/creation-tasks';
import type { CreationTaskExactEditInput, CreationTaskEditConfirmResponse, CreationTaskEditResult, CreationTaskEditPreviewResponse } from '../types/task-edits';
import type { CreationTaskAssistantTurnResponse, CreationTaskAssistantHistory, AssistantStreamEvent } from '../types/assistant';
import type { CreationTaskTimelineVersionDetail, CreationTaskTimelineVersionList } from '../types/timeline-history';
import type { ContentVisibility } from '../types/common';
import type { MontageClipCandidate } from '../types/production';
import { request } from './transport';
import { creationPlanRequest, assistantStreamRequest } from './creation-transport';
export function setCreationTaskVisibility(taskId: string, visibility: ContentVisibility): Promise<CreationTaskWorkspace> {
    return request<CreationTaskWorkspace>(`/api/creation-tasks/${encodeURIComponent(taskId)}/visibility`, { method: "PUT", body: JSON.stringify({ visibility }) });
}
export function getCreationTaskEditPreview(taskId: string, proposalId: string): Promise<CreationTaskEditPreviewResponse> {
    return creationPlanRequest<CreationTaskEditPreviewResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/${encodeURIComponent(proposalId)}`);
}
export function listCreationTasks(): Promise<CreationTasksResponse> {
    return creationPlanRequest<CreationTasksResponse>("/api/creation-tasks");
}
export function getCreationTaskScriptEditSources(taskId: string, scriptId: string): Promise<{
    task_id: string; script_id: string; sources: { reference_id: string; title: string }[];
}> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/sources`);
}
export function getCreationTaskTopics(taskId: string): Promise<CreationTaskTopicsResponse> {
    return creationPlanRequest<CreationTaskTopicsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics`);
}
export function generateCreationTaskTopics(taskId: string): Promise<CreationTaskTopicsResponse> {
    return creationPlanRequest<CreationTaskTopicsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/generate`, { method: "POST", body: JSON.stringify({}) });
}
export function confirmCreationTaskTopic(taskId: string, topicId: string, input: {
    title?: string;
} = {}): Promise<CreationTaskTopicsResponse> {
    return creationPlanRequest<CreationTaskTopicsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/topics/${encodeURIComponent(topicId)}/confirm`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskWorkspace(taskId: string): Promise<CreationTaskWorkspace> {
    return creationPlanRequest<CreationTaskWorkspace>(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
}
export function listCreationTaskTimelineVersions(taskId: string, beforeSequence?: number): Promise<CreationTaskTimelineVersionList> {
    const query = new URLSearchParams({limit: '20'});
    if (beforeSequence !== undefined) query.set('before_sequence', String(beforeSequence));
    return creationPlanRequest<CreationTaskTimelineVersionList>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/versions?${query}`);
}
export function getCreationTaskTimelineVersion(taskId: string, versionId: string): Promise<CreationTaskTimelineVersionDetail> {
    return creationPlanRequest<CreationTaskTimelineVersionDetail>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/versions/${encodeURIComponent(versionId)}`);
}
export function previewCreationExecution(taskId: string): Promise<CreationExecutionPreview> {
    return creationPlanRequest<CreationExecutionPreview>(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/preview`);
}
export function getCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
    return creationPlanRequest<CreationExecutionStatus>(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution`);
}
/** 任务作业进度：只读后台已落库的作业；重开应用读到同一批记录。 */
export function getTaskJobProgress(taskId: string): Promise<TaskJobProgress> {
    return request<TaskJobProgress>(`/api/creation-tasks/${encodeURIComponent(taskId)}/job-runs`);
}
export function startCreationExecution(taskId: string, preview: CreationExecutionPreview): Promise<CreationExecutionStatus> {
    return creationPlanRequest<CreationExecutionStatus>(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/start`, { method: "POST", body: JSON.stringify({
            script_id: preview.script_id,
            input_digest: preview.input_digest,
            requirements_confirmed: true,
        }) });
}
export function resumeCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
    return creationPlanRequest<CreationExecutionStatus>(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/resume`, { method: "POST" });
}
export function cancelCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
    return creationPlanRequest<CreationExecutionStatus>(`/api/creation-tasks/${encodeURIComponent(taskId)}/execution/cancel`, { method: "POST" });
}
export function getCreationTaskScripts(taskId: string): Promise<CreationTaskScriptsResponse> {
    return creationPlanRequest<CreationTaskScriptsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts`);
}
export function getCreationTaskScriptSections(taskId: string, scriptId: string, input: {
    after_ordinal?: number;
    expected_updated_at?: string;
} = {}): Promise<CreationTaskScriptSectionWindow> {
    const query = new URLSearchParams();
    if (input.after_ordinal !== undefined) query.set("after_ordinal", String(input.after_ordinal));
    if (input.expected_updated_at !== undefined) query.set("expected_updated_at", input.expected_updated_at);
    return creationPlanRequest<CreationTaskScriptSectionWindow>(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/sections${query.size ? `?${query}` : ""}`);
}
export function startCreationTaskScriptWindow(taskId: string, input: CreationTaskScriptWindowStartInput): Promise<CreationTaskScriptWindowSaved | CreationTaskScriptWindowUnsettled> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/windows`, { method: "POST", body: JSON.stringify(input) });
}
export function patchCreationTaskScriptSections(taskId: string, scriptId: string, input: CreationTaskScriptSectionsPatchInput): Promise<CreationTaskScriptWindowSaved | CreationTaskScriptWindowUnsettled> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/sections`, { method: "PATCH", body: JSON.stringify(input) });
}
export function confirmSavedCreationTaskScript(taskId: string, scriptId: string, input: CreationTaskScriptConfirmSavedInput): Promise<CreationTaskScriptConfirmed | CreationTaskScriptWindowUnsettled> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/confirm-saved`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskScriptWindowRequest(taskId: string, requestId: string, locator: CreationTaskScriptRequestLocator, options: { include_input?: boolean } = {}): Promise<CreationTaskScriptWindowResult> {
    const query = new URLSearchParams({ operation: locator.operation });
    if (options.include_input === true) query.set("include_input", "true");
    if (locator.operation !== "start") query.set("script_id", locator.script_id);
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/script-window-requests/${encodeURIComponent(requestId)}?${query}`);
}
export function generateCreationTaskScript(taskId: string, input: CreationTaskScriptGenerationInput): Promise<CreationTaskScriptGenerationReceipt> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/generate`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskScriptGenerationRequest(taskId: string, requestId: string): Promise<CreationTaskScriptGenerationReceipt> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/generation-requests/${encodeURIComponent(requestId)}`);
}
export function confirmCreationTaskScript(taskId: string, scriptId: string, input: {
    title: string;
    sections: CreationTaskScriptSection[];
    expected_updated_at: string;
    edit_mode?: "replace_all";
}): Promise<CreationTaskScriptsResponse> {
    return creationPlanRequest<CreationTaskScriptsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/confirm`, { method: "POST", body: JSON.stringify(input) });
}
export function transferAssistantReplyToScript(taskId: string, input: {
    conversation_id: string;
    turn_id: string;
    request_id: string;
    plan_conversion_request_id?: string;
    expected_plan_updated_at: string;
    expected_task_updated_at: string;
    replace_existing_script_id?: string;
    expected_script_updated_at?: string;
}): Promise<CreationTaskScriptsResponse> {
    return creationPlanRequest<CreationTaskScriptsResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/reply-transfer`, { method: "POST", body: JSON.stringify(input) });
}
export function getAssistantScriptContext(taskId: string): Promise<{
    task_id: string;
    sources: {
        reference_id: string;
        title: string;
        text: string;
    }[];
    draft: (Pick<CreationTaskScript, "id" | "title" | "updated_at" | "sections_total_count"> & {
        sections: null;
        sections_loaded: false;
    }) | null;
    reply_origin: {
        conversation_id: string;
        turn_id: string;
        request_id: string;
    } | null;
}> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-context`);
}
export function saveAssistantScriptDraft(taskId: string, input: {
    title: string;
    sections: CreationTaskScriptSection[];
    expected_updated_at?: string;
    edit_mode?: "replace_all";
}): Promise<Extract<CreationTaskScript, { sections_loaded: true }>> {
    return creationPlanRequest<Extract<CreationTaskScript, { sections_loaded: true }>>(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/script-draft`, { method: "POST", body: JSON.stringify(input) });
}
export function selectCreationTaskNarrationVoice(taskId: string, input: CreationTaskNarrationVoiceSelectionRequest): Promise<CreationTaskNarrationVoiceSelectionResponse> {
    return creationPlanRequest<CreationTaskNarrationVoiceSelectionResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/narration/voice`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskNarrationVoiceSelection(taskId: string, requestId: string): Promise<CreationTaskNarrationVoiceSelectionResponse> {
    return creationPlanRequest<CreationTaskNarrationVoiceSelectionResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/narration/voice/requests/${encodeURIComponent(requestId)}`);
}
export function generateCreationTaskPersonRender(taskId: string, input: {
    narration_segment_id: string;
    person_profile_id: string;
    person_version: string;
    motion: "natural";
    expression: "neutral";
}): Promise<CreationTaskPersonRender> {
    return creationPlanRequest<CreationTaskPersonRender>(`/api/creation-tasks/${encodeURIComponent(taskId)}/person-renders/generate`, { method: "POST", body: JSON.stringify(input) });
}
export function applyCreationTaskExactEdit(taskId: string, input: CreationTaskExactEditInput): Promise<CreationTaskEditResult> {
    return creationPlanRequest<CreationTaskEditResult>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/exact`, { method: "POST", body: JSON.stringify(input) });
}
export function previewCreationTaskExactEdit(taskId: string, input: CreationTaskExactEditInput): Promise<CreationTaskEditPreviewResponse | TaskMediaJob> {
    return creationPlanRequest<CreationTaskEditPreviewResponse | TaskMediaJob>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/exact/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskMontageClipCandidates(taskId: string, clipId: string): Promise<MontageClipCandidate[]> {
    return creationPlanRequest<MontageClipCandidate[]>(`/api/creation-tasks/${encodeURIComponent(taskId)}/montage/clips/${encodeURIComponent(clipId)}/candidates`);
}
export async function getCreationTaskQualityCheck(taskId: string): Promise<import("../types").CreationTaskQualityCheck> {
    // Quality reads require a trusted identity, unlike anonymous task reads.
    // In local mode, AppShell's first-browser identity initialization may still be pending.
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken())
        await ensureLocalMemberIdentity();
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/quality-check`);
}
export function checkCreationTaskQuality(taskId: string): Promise<import("../types").CreationTaskQualityCheck> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/quality-check`, { method: "POST", body: JSON.stringify({}) });
}
export function continueCreationTaskQuality(taskId: string, input: { job_id: string; failed_request_id: string }): Promise<{ check_id: string; job_id: string; request_id: string }> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/quality-check/continue`, { method: "POST", body: JSON.stringify(input) });
}
export function previewCreationTaskEdit(taskId: string, input: {
    raw_command: string;
    context_stage: string;
    selected_object_refs: string[];
    playhead_ms: number | null;
}): Promise<CreationTaskEditPreviewResponse | TaskMediaJob> {
    return creationPlanRequest<CreationTaskEditPreviewResponse | TaskMediaJob>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}
export function sendCreationTaskAssistantTurn(taskId: string, input: {
    conversation_id: string;
    message: string;
    client_request_id: string;
    expected_draft_version: number;
    expected_reference_version?: number;
    expected_timeline_version_id?: string | null;
    expected_timeline_sequence?: number;
    selected_profile_reference?: {
        kind: "person" | "voice";
        profile_id: string;
        version: string;
    } | null;
    selected_knowledge_reference?: {
        knowledge_item_id: string;
        version: string;
        locator?: string;
    } | null;
    context_stage: string;
    selected_object_refs: string[];
    playhead_ms: number | null;
}, onEvent?: (event: AssistantStreamEvent) => void): Promise<CreationTaskAssistantTurnResponse> {
    if (onEvent)
        return assistantStreamRequest<CreationTaskAssistantTurnResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/turn`, input, onEvent);
    return creationPlanRequest<CreationTaskAssistantTurnResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/turn`, { method: "POST", body: JSON.stringify(input) });
}
export function getCreationTaskAssistantHistory(taskId: string): Promise<CreationTaskAssistantHistory> {
    return creationPlanRequest<CreationTaskAssistantHistory>(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/history`);
}
export function previewCompoundSelection(taskId: string, proposalId: string, selection: number[] | string): Promise<CreationTaskEditPreviewResponse | TaskMediaJob> {
    return creationPlanRequest<CreationTaskEditPreviewResponse | TaskMediaJob>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/${encodeURIComponent(proposalId)}/selection/preview?draft=true`, { method: "POST", body: JSON.stringify(typeof selection === "string" ? { raw_command: selection } : { included_step_indices: selection }) });
}
export function confirmCreationTaskEdit(taskId: string, proposalId: string): Promise<CreationTaskEditResult> {
    return creationPlanRequest<CreationTaskEditResult>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/${encodeURIComponent(proposalId)}/confirm`, { method: "POST", body: JSON.stringify({}) });
}
type TimelineRestoreInput = {
    request_id: string;
    target_operation_id?: string;
    expected_timeline_version_id: string | null;
    expected_timeline_sequence: number;
};
export function readCreationTaskTimelineRestore(taskId: string, requestId: string): Promise<import('../types/timeline-history').TimelineRestoreReceipt> {
    return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/restore-requests/${encodeURIComponent(requestId)}`);
}
export function undoCreationTaskTimeline(taskId: string, input: TimelineRestoreInput): Promise<CreationTaskEditConfirmResponse> {
    return creationPlanRequest<CreationTaskEditConfirmResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/undo`, { method: "POST", body: JSON.stringify(input) });
}
export function initializeCreationTaskTimeline(taskId: string): Promise<{
    version_id: string;
}> {
    return creationPlanRequest<{
        version_id: string;
    }>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/initialize`, { method: "POST", body: JSON.stringify({}) });
}
export function redoCreationTaskTimeline(taskId: string, input: TimelineRestoreInput): Promise<CreationTaskEditConfirmResponse> {
    return creationPlanRequest<CreationTaskEditConfirmResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/redo`, { method: "POST", body: JSON.stringify(input) });
}


export function getSegmentVisualDecision(taskId: string, jobId: string, sectionId: string, offset = 0) {
  return creationPlanRequest<import('../types/creation-tasks').SegmentVisualDecision>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/jobs/${encodeURIComponent(jobId)}/sections/${encodeURIComponent(sectionId)}/visual-decision?offset=${offset}&limit=8`);
}
export function adoptSegmentVisualDecision(taskId: string, jobId: string, sectionId: string,
  input: { decision_digest: string; confirmed_clip_ids: string[] }) {
  return creationPlanRequest<import('../types/creation-tasks').SegmentVisualDecisionAdoption>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/jobs/${encodeURIComponent(jobId)}/sections/${encodeURIComponent(sectionId)}/visual-decision`,
    { method: 'POST', body: JSON.stringify(input) });
}

export function regenerateCreationTaskStoryboard(taskId: string, input: CreationTaskStoryboardRegenerationInput): Promise<CreationTaskStoryboardRegenerationReceipt> {
    return request(`/api/creation-tasks/${encodeURIComponent(taskId)}/montage/plan`, { method: "POST", body: JSON.stringify(input) });
}

export function readCreationTaskStoryboardRegeneration(taskId: string, requestId: string): Promise<CreationTaskStoryboardRegenerationReceipt> {
    return request(`/api/creation-tasks/${encodeURIComponent(taskId)}/montage/plan/requests/${encodeURIComponent(requestId)}`);
}
