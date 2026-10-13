import type { EpisodeStatus } from './episodes';
import type { ProductionProfile } from './profiles';
export type ProductionReadiness = {
    ready: boolean;
    blockers: {
        code: string;
        message: string;
    }[];
    asset_count: number;
    approved_asset_count: number;
    approved_segment_count: number;
    total_duration_seconds: number;
};
export type ProductionBatch = {
    id: string;
    workspace_id: string;
    name: string;
    target_count: number;
    status: "created" | "blocked" | "running" | "waiting_for_user" | "interrupted" | "ready" | "reviewed" | "failed";
    episode_ids: string[];
    items: {
        episode_id: string;
        status: "queued" | "running" | "waiting_for_user" | "rendered" | "failed" | "interrupted";
        plan_id: string | null;
        aspect_ratio: "9:16";
        narration_status: "ready" | "unavailable";
        subtitle_status: "ready" | "not_generated" | "unavailable";
        output_url: string | null;
        subtitle_url: string | null;
        message: string;
        attempts: number;
        updated_at: string;
    }[];
    blocker_codes: string[];
    message: string;
    rendered_count: number;
    waiting_review_count: number;
    failed_count: number;
    started_at: string | null;
    finished_at: string | null;
    created_at: string;
    updated_at: string;
};
export type ProductionFoundation = {
    profile: ProductionProfile;
    readiness: ProductionReadiness;
    recent_batches: ProductionBatch[];
    recent_montage_plans: MontagePlan[];
    eligible_episodes: {
        id: string;
        title: string;
        status: EpisodeStatus;
    }[];
};
export type NarrationCue = {
    id: string;
    source_section_id: string;
    narration_segment_id: string;
    text: string;
    duration_seconds: number;
};
export type MontageClip = {
    id: string;
    cue_id: string;
    asset_id: string;
    segment_id: string;
    source_path: string;
    source_start_seconds: number;
    source_end_seconds: number;
    timeline_start_seconds: number;
    timeline_end_seconds: number;
    match_score: number;
    match_reasons: string[];
    needs_confirmation: boolean;
    role: "a_roll" | "b_roll";
    locked: boolean;
    manually_selected: boolean;
};
export type MontageClipCandidate = {
    asset_id: string;
    segment_id: string;
    file_name: string;
    start_seconds: number;
    end_seconds: number;
    description: string;
    score: number;
    reasons: string[];
};
export type MontagePlan = {
    id: string;
    workspace_id: string;
    batch_id: string;
    episode_id: string;
    script_title: string;
    status: "waiting_for_user" | "ready" | "rendering" | "rendered" | "failed";
    cues: NarrationCue[];
    clips: MontageClip[];
    duration_seconds: number;
    low_confidence_clip_count: number;
    aspect_ratio: "9:16";
    narration_status: "ready" | "unavailable";
    subtitle_status: "ready" | "not_generated" | "unavailable";
    output_url: string | null;
    subtitle_url: string | null;
    message: string;
    created_at: string;
    updated_at: string;
};
