import type { ScriptLibraryEntry, ScriptLibrarySource } from '../types/script-library';
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
export function listScriptLibraryEntries(query = "", category = "") {
    const params = new URLSearchParams({ q: query, category });
    return creationPlanRequest<{
        items: ScriptLibraryEntry[];
        total_count: number;
    }>(`/api/production/script-library?${params}`);
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
