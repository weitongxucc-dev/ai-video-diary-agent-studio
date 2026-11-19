import type { AssistantConversationTurn, CreationTaskEditConfirmResponse } from "@/lib/types";
import { assistantTaskTurnResult } from "@/components/useAssistantConversation";

export function savedAssistantAudioResult(turn: AssistantConversationTurn) {
  const edit = assistantTaskTurnResult(turn)?.response?.edit_request;
  if (edit?.phase !== "apply" || edit.status !== "recorded") return null;
  const saved = edit.result as CreationTaskEditConfirmResponse | null | undefined;
  const operation = saved?.operation;
  if (operation?.operation_type !== "EditAudioTimeline" || operation.status !== "applied"
    || operation.task_id !== edit.task_id || !operation.result_version_id) return null;
  const payload = operation.payload;
  const changed = payload && typeof payload === "object" && "edit" in payload ? payload.edit : null;
  // A retained object with a single metadata edit has one precise destination.
  // Structural edits can remove an object or produce more than one result.
  const targetId = changed && typeof changed === "object" && "action" in changed
    && (changed.action === "update" || changed.action === "track")
    && "target_id" in changed && typeof changed.target_id === "string" ? changed.target_id : null;
  return {
    taskId: operation.task_id, workspaceId: operation.workspace_id, versionId: operation.result_version_id,
    baseVersionId: operation.base_version_id, operationId: operation.id, requestId: edit.request_id, targetId,
  };
}

export function completedAudioReplyMatchesReceipt(turn: AssistantConversationTurn) {
  if (turn.status !== "completed") return false;
  const completion = turn.result?.completed_local_edit;
  if (!completion || typeof completion !== "object" || !("scope" in completion)
    || !["single_narration_gain_edit", "single_music_gain_edit"].includes(String(completion.scope))) return false;
  const audio = savedAssistantAudioResult(turn);
  return Boolean(audio?.targetId && "task_id" in completion && completion.task_id === audio.taskId
    && "request_id" in completion && completion.request_id === audio.requestId
    && "operation_id" in completion && completion.operation_id === audio.operationId
    && "base_version_id" in completion && completion.base_version_id === audio.baseVersionId
    && "result_version_id" in completion && completion.result_version_id === audio.versionId
    && "target_id" in completion && completion.target_id === audio.targetId
    && "gain_before_db" in completion && typeof completion.gain_before_db === "number"
    && "gain_after_db" in completion && typeof completion.gain_after_db === "number"
    && "summary" in completion && typeof completion.summary === "string"
    && turn.assistant_text.trim() !== "" && completion.summary.trim() === turn.assistant_text.trim());
}
