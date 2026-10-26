import type { ScriptLibraryEntry, ScriptLibrarySource, ScriptLibraryEntryHeader, ScriptLibraryHeaderPage, ScriptLibraryCursor, ScriptLibrarySectionWindow } from '../types/script-library';
import type { CreationTaskScript } from '../types/creation-tasks';
import { creationPlanRequest } from './creation-transport';
export function saveScriptLibraryEntry(input: {
    source_script_id: string;
    name: string;
    category: string;
}) {
    return creationPlanRequest<ScriptLibraryEntry>("/api/production/script-library", {
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
export function reuseScriptLibraryEntry(entryId: string, input: {
    name: string;
    aspect_ratio: "9:16" | "16:9" | "1:1";
    duration_target_seconds: number;
    requirements_confirmed: boolean;
}) {
    return creationPlanRequest<{
        task_id: string;
        script: CreationTaskScript;
    }>(`/api/production/script-library/${encodeURIComponent(entryId)}/reuse`, { method: "POST", body: JSON.stringify(input) });
}
