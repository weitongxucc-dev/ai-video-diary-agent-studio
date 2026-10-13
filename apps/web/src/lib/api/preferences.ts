import type { FeedbackSummary, Preferences, PreferenceCards, PreferenceEvidence } from '../types/preferences';
import { request } from './transport';
export function getFeedbackSummary(): Promise<FeedbackSummary> {
    return request<FeedbackSummary>("/api/feedback/summary");
}
export function getPreferences(): Promise<Preferences> {
    return request<Preferences>("/api/preferences");
}
export function getPreferenceCards(): Promise<PreferenceCards> {
    return request<PreferenceCards>("/api/preferences/cards");
}
export function getPreferenceEvidence(preferenceId: string): Promise<PreferenceEvidence> {
    return request<PreferenceEvidence>(`/api/preferences/cards/${encodeURIComponent(preferenceId)}/evidence`);
}
export function disablePreferenceCard(preferenceId: string): Promise<Preferences> {
    return request<Preferences>("/api/preferences/disable", {
        method: "PATCH",
        body: JSON.stringify({ signal: preferenceId }),
    });
}
export function restorePreferenceCard(preferenceId: string): Promise<Preferences> {
    return request<Preferences>("/api/preferences/restore", {
        method: "PATCH",
        body: JSON.stringify({ signal: preferenceId }),
    });
}
