import type { CreationTaskSubtitleTrack, CreationTaskCoverAsset } from './creation-tasks';
import type { EditExecutionReadiness } from './task-edits';
export type CreationTaskTimelineVersionSummary = {
    id: string;
    sequence: number;
    parent_version_id: string | null;
    created_at: string;
    is_current: boolean;
    shot_count: number;
    subtitle_track_count: number;
    narration_segment_count: number;
    operation_id?: string | null;
    operation_type?: string | null;
    target_refs?: string[];
    source_request_id?: string | null;
};
export type CreationTaskTimelineVersionList = {
    task_id: string;
    current_version_id: string | null;
    items: CreationTaskTimelineVersionSummary[];
    total_count?: number;
    truncated?: boolean;
    next_before_sequence?: number | null;
};
export type TimelineRestoreReceipt = {
    task_id: string;
    request_id: string;
    action: "undo" | "redo";
    target_operation_id: string | null;
    status: "applied" | "refused" | "result_unknown";
    operation_id: string | null;
    result_version_id: string | null;
    execution_readiness?: EditExecutionReadiness | null;
    failure: {reason: string; impact?: string; repair_action?: string; submission_effect?: string} | null;
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
