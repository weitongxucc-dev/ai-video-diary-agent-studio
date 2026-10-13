import type { CreationTaskSubtitleTrack, CreationTaskCoverAsset } from './creation-tasks';
export type CreationTaskTimelineVersionSummary = {
    id: string;
    sequence: number;
    parent_version_id: string | null;
    created_at: string;
    is_current: boolean;
    shot_count: number;
    subtitle_track_count: number;
    narration_segment_count: number;
};
export type CreationTaskTimelineVersionList = {
    task_id: string;
    current_version_id: string | null;
    items: CreationTaskTimelineVersionSummary[];
};
export type CreationTaskTimelineVersionShot = {
    index: number;
    id: string;
    narration_text: string | null;
    start_seconds: number;
    end_seconds: number;
    description: string | null;
};
export type CreationTaskTimelineVersionNarrationSegment = {
    index: number;
    id: string;
    text: string;
    duration_seconds: number | null;
    status: string | null;
};
export type CreationTaskTimelineVersionArtifactKind = "video" | "subtitles" | "cover";
export type CreationTaskTimelineVersionArtifact = {
    kind: CreationTaskTimelineVersionArtifactKind;
    status: "available" | "not_generated" | "missing";
    url: string | null;
    reason: string | null;
};
export type CreationTaskTimelineVersionDetail = {
    task_id: string;
    version: CreationTaskTimelineVersionSummary;
    shots: CreationTaskTimelineVersionShot[];
    narration_segments: CreationTaskTimelineVersionNarrationSegment[];
    subtitle_tracks: CreationTaskSubtitleTrack[];
    cover_asset: CreationTaskCoverAsset | null;
    artifacts: CreationTaskTimelineVersionArtifact[];
};
