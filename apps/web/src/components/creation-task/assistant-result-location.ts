import type { AssistantConversationTurn, CreationExecutionStatus, CreationTask, CreationTaskAssistantTurnResponse,
  CreationTaskEditConfirmResponse, CreationTaskEditOperation, CreationTaskTimelineVersionDetail, CreationTaskWorkspace } from "@/lib/types";
import { assistantTaskTurnResult, assistantTurnScriptWindowRequest } from "../useAssistantConversation";

export type TimelineEditTarget =
  | { kind: "shot"; id: string }
  | { kind: "subtitle"; id: string; trackId?: string }
  | { kind: "title"; id: string; replay: { startSeconds: number; endSeconds: number } };

type ResultLocation =
  | { stage: "script"; scriptId: string; updatedAt?: string | null }
  | { stage: "narration"; versionId: string; sectionId?: string; segmentId?: string }
  | { stage: "montage"; versionId: string; workspaceId?: string; target?: TimelineEditTarget };

export function timelineEditTarget(operation: CreationTaskEditOperation): TimelineEditTarget | undefined {
  const payload = operation.payload;
  if (operation.operation_type === "UpdateSubtitleCue" && payload && "cue_id" in payload && typeof payload.cue_id === "string") {
    return { kind: "subtitle", id: payload.cue_id, trackId: "track_id" in payload ? payload.track_id : undefined };
  }
  if ((operation.operation_type === "ChangeCrop" || operation.operation_type === "TrimClip"
    || operation.operation_type === "ReplaceClip" || operation.operation_type === "SetClipLock")
    && payload && "clip_id" in payload && typeof payload.clip_id === "string") {
    return { kind: "shot", id: payload.clip_id };
  }
}

export function timelineEditTargetLabel(target: TimelineEditTarget) {
  if (target.kind === "title") return "定位修改的标题";
  return target.kind === "shot" ? "定位修改的镜头" : "定位修改的字幕";
}

export function savedTitleEditTarget(base: CreationTaskWorkspace, result: CreationTaskEditConfirmResponse): TimelineEditTarget | undefined {
  const { operation, montage } = result;
  if (operation.operation_type !== "EditGraphicOverlay" || !montage
    || operation.task_id !== base.task.id || operation.workspace_id !== base.task.workspace_id
    || operation.status !== "applied" || !operation.result_version_id
    || operation.base_version_id !== base.task.current_timeline_version_id) return;
  const moved = montage.graphic_overlays?.flatMap(overlay => {
    if (overlay.kind !== "text" && overlay.kind !== "template") return [];
    const previous = base.montage?.graphic_overlays?.find(item => item.id === overlay.id && item.kind === overlay.kind);
    if (!previous || previous.start_seconds === overlay.start_seconds) return [];
    const first = Math.min(previous.start_seconds, overlay.start_seconds);
    const last = Math.max(previous.start_seconds, overlay.start_seconds);
    return [{ kind: "title" as const, id: overlay.id,
      replay: { startSeconds: Math.max(0, first - 1), endSeconds: Math.min(montage.duration_seconds, last + 1) } }];
  }) ?? [];
  if (moved.length === 1) return moved[0];
}

export function assistantSavedEdit(taskId: string, result: CreationTaskAssistantTurnResponse) {
  const receipt = result.edit_request;
  if (receipt?.task_id !== taskId || receipt.phase !== "apply" || receipt.status !== "recorded") return;
  const saved = receipt.result as CreationTaskEditConfirmResponse | undefined;
  const operation = saved?.operation;
  if (saved && operation?.task_id === taskId && operation.status === "applied" && operation.result_version_id) {
    return { ...saved, operation: { ...operation, result_version_id: operation.result_version_id } };
  }
}

type RenderVersion = { timeline_version_id: string; timeline_sequence: number };
// The existing execution endpoint supplies this only for a verified publication.
type RenderExecution = CreationExecutionStatus & {
  output_version?: { job_id: string; base_version: RenderVersion; result_version: RenderVersion };
};

export function titlePublicationCandidate(task: CreationTask, execution: RenderExecution | null, editVersionId: string) {
  const output = execution?.output_version;
  if (execution?.task_id === task.id && execution.video_ready && execution.job?.status === "succeeded"
    && output?.job_id === execution.job.id && output.base_version.timeline_version_id === editVersionId
    && output.result_version.timeline_version_id === task.current_timeline_version_id
    && output.result_version.timeline_sequence === task.timeline_sequence
    && output.base_version.timeline_sequence < output.result_version.timeline_sequence) {
    return output.result_version;
  }
}

export function matchesTitlePublication(detail: CreationTaskTimelineVersionDetail, taskId: string, editVersionId: string, candidate: RenderVersion) {
  return detail.task_id === taskId && detail.version.id === candidate.timeline_version_id
    && detail.version.sequence === candidate.timeline_sequence && detail.version.is_current
    && detail.version.parent_version_id === editVersionId && detail.version.operation_id === null
    && detail.artifacts.some(artifact => artifact.kind === "video" && artifact.status === "available" && artifact.url);
}

export function savedAssistantResultLocation(turn: AssistantConversationTurn, taskId: string): ResultLocation | null {
  const result = assistantTaskTurnResult(turn);
  const response = result?.response;
  if (!response) return null;
  const window = assistantTurnScriptWindowRequest(turn, taskId);
  if (window?.script_id && (window.status === "saved" || window.status === "confirmed")) {
    return { stage: "script", scriptId: window.script_id, updatedAt: window.updated_at };
  }
  const draft = response.script_draft;
  if (draft?.task_id === taskId && draft.id && draft.updated_at) {
    return { stage: "script", scriptId: draft.id, updatedAt: draft.updated_at };
  }

  const revision = response.segment_revision?.revision;
  if (revision?.task_id === taskId && revision.status === "succeeded" && revision.adoption.status === "current"
    && revision.adoption.current_timeline_version_id) {
    return { stage: "narration", versionId: revision.adoption.current_timeline_version_id, sectionId: revision.section_id };
  }
  const saved = assistantSavedEdit(taskId, response);
  if (saved) {
    const { operation } = saved;
    if (operation.operation_type === "RegenerateNarrationSegment"
      || !operation.operation_type && saved.narration?.task.id === taskId && saved.narration.audio_reference) {
      const payload = operation.payload;
      return { stage: "narration", versionId: operation.result_version_id,
        segmentId: payload && "narration_segment_id" in payload ? payload.narration_segment_id : undefined };
    }
    return { stage: "montage", versionId: operation.result_version_id,
      workspaceId: operation.workspace_id, target: timelineEditTarget(operation) };
  }
  const restore = response.timeline_restore?.receipt;
  if (restore?.task_id === taskId && restore.status === "applied" && restore.result_version_id) {
    return { stage: "montage", versionId: restore.result_version_id };
  }
  const storyboard = response.storyboard_regeneration;
  if (storyboard?.kind === "storyboard_regeneration_receipt" && storyboard.receipt.task_id === taskId
    && storyboard.receipt.status === "succeeded" && storyboard.receipt.result?.timeline_version_id) {
    return { stage: "montage", versionId: storyboard.receipt.result.timeline_version_id };
  }
  const continuation = result.continuation;
  return continuation?.task_id === taskId && continuation.status === "confirmed" && continuation.result_version_id
    ? { stage: "montage", versionId: continuation.result_version_id } : null;
}
