import type { AssistantConversation } from "@/lib/types";

const prefix = "ai-video-diary.assistant-draft-receipt.v1:";
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const receiptLifetime = 10_000;

export type DraftWriteReceipt = {
  id: string;
  version: number;
  after?: string;
  expiresAt: number;
};

function key(conversation: AssistantConversation) {
  return prefix + JSON.stringify([
    conversation.workspace_id, conversation.scope_kind, conversation.scope_id, conversation.id,
  ]);
}

export function readDraftWriteReceipt(conversation: AssistantConversation): DraftWriteReceipt | undefined {
  try {
    const name = key(conversation);
    const raw = localStorage.getItem(name);
    if (!raw || raw.length > 512) return;
    const value = JSON.parse(raw);
    if (typeof value?.id !== "string" || !uuid.test(value.id)
        || !Number.isSafeInteger(value.version) || value.version < 0
        || !Number.isSafeInteger(value.expiresAt)
        || (value.after !== undefined && (typeof value.after !== "string" || !uuid.test(value.after)))) return;
    if (value.expiresAt <= Date.now()) localStorage.removeItem(name);
    else return value;
  } catch {
    // The persisted conversation is available without a local receipt.
  }
}

export function rememberDraftWrite(conversation: AssistantConversation, version: number, after?: string): DraftWriteReceipt {
  const receipt = { id: crypto.randomUUID(), version, after, expiresAt: Date.now() + receiptLifetime };
  try {
    localStorage.setItem(key(conversation), JSON.stringify(receipt));
  } catch {
    // The request can still be delivered and saved by the server.
  }
  return receipt;
}

export function forgetDraftWrite(conversation: AssistantConversation, receipt: DraftWriteReceipt) {
  try {
    if (readDraftWriteReceipt(conversation)?.id === receipt.id) localStorage.removeItem(key(conversation));
  } catch {
    // A receipt left behind expires without retaining draft text.
  }
}

export function awaitsDraftWrite(conversation: AssistantConversation, receipt: DraftWriteReceipt) {
  return conversation.draft_version === receipt.version || (
    conversation.draft_version === receipt.version + 1 && Boolean(receipt.after)
    && conversation.draft_write_id === receipt.after
  );
}
