import { ensureLocalMemberIdentity, readLocalMemberToken } from "../local-member-auth";
import { isLocalAuthMode } from "../auth/routing";
import type { WorkspaceAssistantPlanTurnResponse, AssistantStreamEvent, AssistantConversation, AssistantConversationScope, AssistantCapabilities, AssistantTurnTrace } from '../types/assistant';
import type { MaterialAssistantSelection, MaterialAssistantTurnResponse } from '../types/materials';
import { creationPlanRequest, assistantStreamRequest } from './creation-transport';
import type { AssistantInputReceipt } from '../types/assistant';

export async function enqueueAssistantInput(conversationId: string, input: {
    client_request_id: string; target_turn_id: string; message: string; mode: "steer" | "follow_up";
    expected_draft_version: number; expected_reference_version: number;
}): Promise<AssistantInputReceipt> {
    const result = await creationPlanRequest<{input: AssistantInputReceipt; draft_version: number}>(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/inputs`, {
        method: "POST", body: JSON.stringify(input),
    });
    return result.input;
}
export function withdrawAssistantInput(conversationId: string, inputId: string): Promise<AssistantInputReceipt> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/inputs/${encodeURIComponent(inputId)}/withdraw`, { method: "POST" });
}
export async function getCurrentAssistantConversation(scope: AssistantConversationScope): Promise<AssistantConversation> {
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken())
        await ensureLocalMemberIdentity();
    return creationPlanRequest<AssistantConversation>("/api/assistant/conversations/current", {
        method: "POST", body: JSON.stringify(scope),
    });
}
export async function createAssistantConversation(scope: AssistantConversationScope): Promise<AssistantConversation> {
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken())
        await ensureLocalMemberIdentity();
    return creationPlanRequest<AssistantConversation>("/api/assistant/conversations", {
        method: "POST", body: JSON.stringify(scope),
    });
}
export function listAssistantConversations(scope: AssistantConversationScope): Promise<{
    items: AssistantConversation[];
}> {
    const query = new URLSearchParams({ scope_kind: scope.scope_kind, scope_id: scope.scope_id });
    return creationPlanRequest(`/api/assistant/conversations?${query.toString()}`);
}
export function getAssistantConversation(conversationId: string): Promise<AssistantConversation> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}`);
}
export function getAssistantTurnTrace(turnId: string, before?: number | null): Promise<AssistantTurnTrace> {
    const query = before == null ? "" : `?before=${encodeURIComponent(String(before))}`;
    return creationPlanRequest(`/api/assistant/turns/${encodeURIComponent(turnId)}/trace${query}`);
}
export function resumeAssistantConversation(conversationId: string): Promise<AssistantConversation> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/resume`, {
        method: "POST",
    });
}
export function updateAssistantConversation(conversationId: string, update: {
    title?: string;
    draft_text?: string;
    expected_draft_version?: number;
}): Promise<AssistantConversation> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}`, {
        method: "PATCH", body: JSON.stringify(update),
    });
}
export function restoreUnpersistedAssistantTurnDraft(conversationId: string, input: {
    client_request_id: string;
    user_message: string;
    expected_draft_version: number;
}): Promise<{
    status: "restored" | "already_recovered" | "already_persisted";
    conversation: AssistantConversation;
}> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/restore-unpersisted-turn`, {
        method: "POST", body: JSON.stringify(input),
    });
}
export function stopAssistantConversationTurn(conversationId: string, turnId: string): Promise<{
    turn_id: string;
    status: "stopping";
}> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/stop`, {
        method: "POST", body: JSON.stringify({ turn_id: turnId }),
    });
}
export async function getAssistantCapabilities(): Promise<AssistantCapabilities> {
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken())
        await ensureLocalMemberIdentity();
    return creationPlanRequest("/api/assistant/capabilities");
}
export function getAssistantConversationReference(conversationId: string): Promise<import("../types").AssistantConversationReferenceState> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/reference`);
}
export function saveAssistantConversationReference(conversationId: string, selectedReference: import("../types").AssistantSavedReference | null, expectedReferenceVersion: number): Promise<import("../types").AssistantConversationReferenceState> {
    return creationPlanRequest(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/reference`, {
        method: "PATCH", body: JSON.stringify({ selected_reference: selectedReference, expected_reference_version: expectedReferenceVersion }),
    });
}
export function sendWorkspaceAssistantPlanTurn(message: string, conversationId: string, clientRequestId: string, expectedDraftVersion: number, onEvent?: (event: AssistantStreamEvent) => void, profileReference?: {
    kind: "person" | "voice";
    profile_id: string;
    version: string;
} | null, knowledgeReference?: {
    knowledge_item_id: string;
    version: string;
    locator?: string;
} | null, expectedReferenceVersion?: number): Promise<WorkspaceAssistantPlanTurnResponse> {
    const input = { message, conversation_id: conversationId, client_request_id: clientRequestId,
        expected_draft_version: expectedDraftVersion, expected_reference_version: expectedReferenceVersion, ...(profileReference ? { selected_profile_reference: profileReference } : {}),
        ...(knowledgeReference ? { selected_knowledge_reference: knowledgeReference } : {}) };
    if (onEvent)
        return assistantStreamRequest<WorkspaceAssistantPlanTurnResponse>("/api/assistant/plan/turn", input, onEvent);
    return creationPlanRequest<WorkspaceAssistantPlanTurnResponse>("/api/assistant/plan/turn", { method: "POST", body: JSON.stringify(input) });
}
export function sendMaterialAssistantTurn(message: string, conversationId: string, selection: MaterialAssistantSelection | null, clientRequestId: string, expectedDraftVersion: number, onEvent: (event: AssistantStreamEvent) => void, expectedReferenceVersion?: number): Promise<MaterialAssistantTurnResponse> {
    return assistantStreamRequest<MaterialAssistantTurnResponse>("/api/assistant/material/turn", { message, conversation_id: conversationId, selection, client_request_id: clientRequestId,
        expected_draft_version: expectedDraftVersion, expected_reference_version: expectedReferenceVersion }, onEvent);
}
