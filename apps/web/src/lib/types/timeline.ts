export type VisualEffects = {
    fade_in_seconds?: number;
    fade_out_seconds?: number;
    zoom?: number;
    rotation?: number;
    offset_x?: number;
    offset_y?: number;
    beauty_strength?: number;
    skin_exposure?: number;
    exposure: number;
    contrast: number;
    saturation: number;
    temperature: number;
};
export type VisualEditInput = {
    base_version_id: string | null;
    clip_ids: string[];
    effects: VisualEffects;
    raw_command?: string;
};
export type TextOverlay = {
    kind?: "text";
    id: string;
    text: string;
    start_seconds: number;
    end_seconds: number;
    x: number;
    y: number;
    width: number;
    font_size: number;
    font_family: string;
    color: string;
};
export type ImageOverlay = {
    kind: "image";
    id: string;
    source_ref: string;
    name: string;
    start_seconds: number;
    end_seconds: number;
    x: number;
    y: number;
    width: number;
    opacity: number;
};
export type ArrowOverlay = {
    kind: "arrow";
    id: string;
    x: number;
    y: number;
    name: string;
    purpose: string;
    start_seconds: number;
    end_seconds: number;
    position: "left-top" | "right-top" | "right-bottom" | "left-bottom";
    color: string;
};
export type GraphicOverlay = TextOverlay | ImageOverlay | ArrowOverlay;
export type GraphicSource = {
    source_ref: string;
    artifact_id: string;
    name: string;
    width: number;
    height: number;
    sha256?: string;
    size_bytes?: number;
};
export type CreationTaskMontageClip = {
    transition_in_seconds?: number;
    fade_envelope_offset_frames?: number;
    fade_envelope_duration_frames?: number | null;
    fade_envelope_rate?: number;
    visual_effects?: VisualEffects;
    fit_mode?: "fill" | "fit";
    crop_x?: number;
    crop_y?: number;
    id: string;
    cue_id: string;
    asset_id: string;
    segment_id: string;
    source_reference: string;
    source_start_seconds: number;
    source_end_seconds: number;
    timeline_start_seconds: number;
    timeline_end_seconds: number;
    match_score: number;
    match_reasons?: string[];
    needs_confirmation: boolean;
    role: "a_roll" | "b_roll";
    locked: boolean;
    manually_selected: boolean;
    person_render_id?: string | null;
};
export type TimelineAudioClip = {
    id: string;
    name: string;
    source_ref: string;
    source_start_ms: number;
    source_end_ms: number;
    timeline_start_ms: number;
    playback_rate?: number;
    gain_db: number;
    fade_in_ms: number;
    fade_out_ms: number;
    muted: boolean;
    locked: boolean;
    denoise_db: number;
    loudness_target_lufs?: number | null;
    gain_points: {
        offset_ms: number;
        gain_db: number;
    }[];
    editor_gain_points?: {
        offset_ms: number;
        gain_db: number;
    }[];
    envelope_offset_ms: number;
    envelope_duration_ms: number | null;
};
export type TimelineAudioTrack = {
    id: string;
    name: string;
    role: "narration" | "original" | "music" | "effect";
    gain_db: number;
    muted: boolean;
    locked: boolean;
    duck_under_voice: boolean;
    clips: TimelineAudioClip[];
};
export type AudioTimeline = {
    format_version: 1;
    tracks: TimelineAudioTrack[];
};
export type AudioEditInput = {
    base_version_id: string | null;
    action: "add" | "update" | "delete" | "split" | "duplicate" | "track" | "replace_narration";
    target_id: string;
    values: Record<string, unknown>;
    raw_command?: string;
};
export type CreationTaskRecording = {
    source_ref: string;
    artifact_id: string;
    audio_url: string;
    duration_ms: number;
    applied: boolean;
    created_at?: string;
};
