import type { KnowledgeGrant, KnowledgeGrantState } from '../types/knowledge';
import type { KnowledgeItemVersion } from "../source-api";
import type { KnowledgeNote } from '../types/sources';
import { request } from './transport';
export function getKnowledgeGrants(itemId: string): Promise<KnowledgeGrantState> {
    return request<KnowledgeGrantState>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants`);
}
export function getKnowledgeReferenceVersion(itemId: string, version: string): Promise<KnowledgeItemVersion> {
    return request<KnowledgeItemVersion>(`/api/knowledge-items/${encodeURIComponent(itemId)}/versions/${encodeURIComponent(version)}`);
}
export function grantKnowledgeToWorkspace(itemId: string, targetWorkspaceId: string): Promise<KnowledgeGrant> {
    return request<KnowledgeGrant>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants`, {
        method: "POST", body: JSON.stringify({ target_workspace_id: targetWorkspaceId }),
    });
}
export function revokeKnowledgeGrant(itemId: string, grantId: string): Promise<KnowledgeGrant> {
    return request<KnowledgeGrant>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants/${encodeURIComponent(grantId)}`, {
        method: "DELETE",
    });
}
export function getKnowledgeNotes(query?: string): Promise<KnowledgeNote[]> {
    const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
    return request<KnowledgeNote[]>(`/api/knowledge/notes${suffix}`);
}
