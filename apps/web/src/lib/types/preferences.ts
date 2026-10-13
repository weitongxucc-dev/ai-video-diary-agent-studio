import type { FeedbackEvent } from './episodes';
export type FeedbackEventResponse = FeedbackEvent;
export type FeedbackSummary = {
    total_count: number;
    up_count: number;
    down_count: number;
    recent_events: FeedbackEventResponse[];
};
export type PreferenceItem = {
    label: string;
    signal: string;
    evidence_count: number;
    confidence: number;
    enabled: boolean;
};
export type Preferences = {
    total_feedback_count: number;
    preferences: PreferenceItem[];
};
export type PreferenceCard = {
    id: string;
    label: string;
    signal: string;
    category: string;
    confidence: number;
    evidence_count: number;
    enabled: boolean;
    latest_evidence_at: string | null;
};
export type PreferenceCards = {
    total_count: number;
    enabled_count: number;
    disabled_count: number;
    cards: PreferenceCard[];
};
export type PreferenceEvidenceItem = {
    feedback_event_id: string;
    episode_id: string | null;
    episode_date: string | null;
    target_type: string;
    target_id: string;
    value: "up" | "down";
    reason: string | null;
    created_at: string;
};
export type PreferenceEvidence = {
    preference_id: string;
    total: number;
    items: PreferenceEvidenceItem[];
};
