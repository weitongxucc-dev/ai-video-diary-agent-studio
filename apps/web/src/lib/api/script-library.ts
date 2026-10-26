import type { ScriptLibrarySource, ScriptLibraryEntryHeader, ScriptLibraryHeaderPage, ScriptLibraryCursor, ScriptLibrarySectionWindow, ScriptLibrarySaveInput, ScriptLibrarySaveReceipt, ScriptLibraryReuseInput, ScriptLibraryReuseReceipt } from '../types/script-library';
import { creationPlanRequest, CreationPlanApiError } from './creation-transport';
export function isScriptLibraryWriteRejected(cause: unknown) {
    return cause instanceof CreationPlanApiError && [401, 403, 404, 409, 422].includes(cause.status);
}
export function saveScriptLibraryEntry(input: ScriptLibrarySaveInput): Promise<ScriptLibrarySaveReceipt> {
    return creationPlanRequest<ScriptLibrarySaveReceipt>("/api/production/script-library", {
        method: "POST", body: JSON.stringify(input),
    });
}
export function listScriptLibraryEntries(query = "", category = "", cursor?: ScriptLibraryCursor): Promise<ScriptLibraryHeaderPage> {
    const params = new URLSearchParams({ q: query, category });
    if (cursor) {
        params.set("after_created_at", cursor.after_created_at);
        params.set("after_entry_id", cursor.after_entry_id);
    }
    return creationPlanRequest(`/api/production/script-library?${params}`);
}
export function getScriptLibraryEntry(entryId: string): Promise<ScriptLibraryEntryHeader> {
    return creationPlanRequest(`/api/production/script-library/${encodeURIComponent(entryId)}`);
}
export function getScriptLibrarySections(entryId: string, input: { after_ordinal?: number; expected_updated_at: string }): Promise<ScriptLibrarySectionWindow> {
    const params = new URLSearchParams({ expected_updated_at: input.expected_updated_at });
    if (input.after_ordinal !== undefined) params.set("after_ordinal", String(input.after_ordinal));
    return creationPlanRequest(`/api/production/script-library/${encodeURIComponent(entryId)}/sections?${params}`);
}
export function getScriptLibrarySources(entryId: string) {
    return creationPlanRequest<ScriptLibrarySource[]>(`/api/production/script-library/${encodeURIComponent(entryId)}/sources`);
}
export function reuseScriptLibraryEntry(entryId: string, input: ScriptLibraryReuseInput): Promise<ScriptLibraryReuseReceipt> {
    return creationPlanRequest<ScriptLibraryReuseReceipt>(`/api/production/script-library/${encodeURIComponent(entryId)}/reuse`, { method: "POST", body: JSON.stringify(input) });
}
export function getScriptLibraryRequest(requestId: string, operation: "save"): Promise<ScriptLibrarySaveReceipt>;
export function getScriptLibraryRequest(requestId: string, operation: "reuse"): Promise<ScriptLibraryReuseReceipt>;
export function getScriptLibraryRequest(requestId: string, operation: "save" | "reuse"): Promise<ScriptLibrarySaveReceipt | ScriptLibraryReuseReceipt> {
    return creationPlanRequest(`/api/production/script-library-requests/${encodeURIComponent(requestId)}?${new URLSearchParams({ operation })}`);
}
