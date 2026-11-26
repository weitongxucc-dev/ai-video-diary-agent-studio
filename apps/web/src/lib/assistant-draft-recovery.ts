import type { AssistantConversation } from "@/lib/types";

const draftLimit = 4000;
const maxWriteIds = 16;
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export type DraftRecovery = {
  id: string;
  version: number;
  text: string;
  writeIds: string[];
};

function key(conversation: AssistantConversation) {
  // This key is derived only after the API has authorized the conversation.
  return `ai-video-diary.assistant-draft-recovery.v1:${JSON.stringify([
    conversation.workspace_id, conversation.scope_kind, conversation.scope_id, conversation.id,
  ])}`;
}

export function readDraftRecovery(conversation: AssistantConversation): DraftRecovery | undefined {
  try {
    const raw = localStorage.getItem(key(conversation));
    if (!raw || raw.length > 6 * draftLimit + 1024) return;
    const value = JSON.parse(raw);
    if (typeof value?.id === "string" && uuid.test(value.id)
        && Number.isSafeInteger(value.version) && value.version >= 0
        && typeof value.text === "string" && value.text.length <= draftLimit
        && Array.isArray(value.writeIds) && value.writeIds.length <= maxWriteIds
        && value.writeIds.every((id: unknown) => typeof id === "string" && uuid.test(id))) return value;
  } catch {
    // Server-side draft saving also works when browser storage is unavailable.
  }
}

function storeDraftRecovery(conversation: AssistantConversation, value: DraftRecovery | undefined) {
  try {
    if (value) localStorage.setItem(key(conversation), JSON.stringify(value));
    else localStorage.removeItem(key(conversation));
  } catch {
    // Keep the input and continue through the existing server-side save.
  }
}

export function rememberDraft(conversation: AssistantConversation, version: number, text: string, previous?: DraftRecovery) {
  if (text.length > draftLimit) return;
  const pending: DraftRecovery = {
    id: crypto.randomUUID(), version, text,
    writeIds: previous?.version === version ? previous.writeIds : [],
  };
  storeDraftRecovery(conversation, pending);
  return pending;
}

export function updateDraftRecovery(conversation: AssistantConversation, previous: DraftRecovery, next?: DraftRecovery) {
  if (readDraftRecovery(conversation)?.id !== previous.id) return false;
  storeDraftRecovery(conversation, next);
  return true;
}

export function recordDraftWrite(pending: DraftRecovery, writeId: string): DraftRecovery {
  return { ...pending, writeIds: [...pending.writeIds, writeId].slice(-maxWriteIds) };
}
