import type { AssistantConversationTurn, CreationTaskEditConfirmResponse } from "@/lib/types";
import { assistantTaskTurnResult, assistantTurnScriptWindowRequest } from "../useAssistantConversation";

type ResultLocation =
  | { stage: "script"; scriptId: string; updatedAt?: string | null }
  | { stage: "narration"; versionId: string; sectionId?: string; segmentId?: string }
  | { stage: "montage"; versionId: string };

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
  const edit = response.edit_request;
  if (edit?.task_id === taskId && edit.phase === "apply" && edit.status === "recorded") {
    const saved = edit.result as CreationTaskEditConfirmResponse | null | undefined;
    const operation = saved?.operation;
    if (operation?.task_id === taskId && operation.status === "applied" && operation.result_version_id) {
      if (operation.operation_type === "RegenerateNarrationSegment"
        || !operation.operation_type && saved?.narration?.task.id === taskId && saved.narration.audio_reference) {
        const payload = operation.payload;
        return { stage: "narration", versionId: operation.result_version_id,
          segmentId: payload && "narration_segment_id" in payload ? payload.narration_segment_id : undefined };
      }
      return { stage: "montage", versionId: operation.result_version_id };
    }
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
