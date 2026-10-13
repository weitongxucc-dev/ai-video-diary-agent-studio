// ── Creator Profile ──────────────────────────────────────────────
export type CreatorProfile = {
    id: string;
    revision: number;
    common_expressions: string[];
    banned_words: string[];
    style_preferences: Record<string, string>;
    created_at: string;
    updated_at: string;
};
export type CreatorProfileUpdate = {
    expected_revision: number;
    common_expressions?: string[];
    banned_words?: string[];
    style_preferences?: Record<string, string>;
};
export type CreatorProfileLearnResult = {
    profile: CreatorProfile;
    summary: {
        new_expressions: string[];
        new_banned_words: string[];
        style_changes: Record<string, string>;
    };
};
// ── Production workstation foundation ────────────────────────────
export type ProductionProfile = {
    workspace_id: string;
    workspace_name: string;
    brand_name: string;
    primary_platform: "douyin";
    aspect_ratio: "9:16";
    target_duration_min_seconds: number;
    target_duration_max_seconds: number;
    brand_template_name: string;
    brand_colors: string[];
    subtitle_style: string;
    intro_text: string;
    outro_text: string;
    background_music_path: string | null;
    voice_name: string;
    voice_reference_path: string | null;
    voice_reference_text: string;
    voice_consent_confirmed: boolean;
    voice_model_status: "pending" | "ready" | "blocked" | "failed";
    voice_model_message: string;
    source_urls: string[];
    use_obsidian: boolean;
    use_hermes: boolean;
    factuality_gate_enabled: boolean;
    cloud_enhancement_enabled: boolean;
    updated_at: string;
};
export type VoiceProfile = {
    id: string;
    workspace_id: string;
    name: string;
    reference_audio_path: string;
    reference_text: string;
    consent_confirmed: boolean;
    is_default: boolean;
    model_id: string;
    status: "pending" | "ready" | "blocked" | "failed";
    message: string;
    created_at: string;
    updated_at: string;
};
export type VoiceProfiles = {
    voices: VoiceProfile[];
    total_count: number;
};
export type EpisodeVoiceSelection = {
    episode_id: string;
    voice_profile_id: string | null;
    voice_version: string | null;
    language: "zh" | "en" | "zh-en" | null;
    authorization_id: string | null;
    authorization_version: string | null;
    pronunciation_entry_ids: string[];
    source: "episode" | "none";
};
