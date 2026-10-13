export type PendingVideo = {
    file_name: string;
    path: string;
    size_bytes: number;
    modified_at: string;
};
export type ProcessedVideo = {
    file_name: string;
    output_path: string;
    source_file_name: string;
    edit_plan_path: string | null;
    source_file_names: string[];
    dedup_report_path: string | null;
};
export type RetakeCandidate = {
    remove: {
        start: number;
        end: number;
    };
    keep: {
        start: number;
        end: number;
    };
    confidence: number;
    reason: string;
};
export type RetakeReport = {
    status: "applied" | "no_changes" | "skipped" | "failed";
    source_video: string;
    output_video: string | null;
    auto_removed: RetakeCandidate[];
    review_only: RetakeCandidate[];
    message: string | null;
};
export type EditPlanVideo = {
    file_name: string;
    path: string;
    url: string;
};
export type EditPlanSegmentDecisionAction = "keep" | "remove" | "review";
export type EditPlanUserDecision = {
    action: EditPlanSegmentDecisionAction;
    reason: string | null;
    created_at: string;
};
export type EditPlanSegment = {
    id: string;
    label: string;
    start: number;
    end: number;
    duration: number;
    action: "keep" | "cut_silence" | "remove_retake" | "manual_review";
    status: "kept" | "auto_removed" | "manual_review";
    reason: string;
    confidence: number | null;
    source: string;
    user_decision: EditPlanUserDecision | null;
};
export type EditPlanDecision = {
    segment_id: string;
    title: string;
    command: "keep_segment" | "cut_silence" | "remove_retake" | "manual_review";
    range: string;
    confidence: number | null;
    reason: string;
};
export type EditPlanMarker = {
    segment_id: string;
    label: string;
    time: string;
    memo: string;
};
export type EditPlanJianying = {
    status: "not_ready" | "waiting_confirmation" | "draft_ready";
    package_name: string | null;
    package_path: string | null;
    auto_imported: boolean;
    message: string;
};
export type EditPlan = {
    episode_id: string;
    available: boolean;
    status: "missing" | "processing" | "failed" | "ready";
    message: string;
    strategy: string | null;
    duration_seconds: number;
    source_files: string[];
    source_video: EditPlanVideo | null;
    output_video: EditPlanVideo | null;
    segments: EditPlanSegment[];
    decisions: EditPlanDecision[];
    markers: EditPlanMarker[];
    jianying: EditPlanJianying;
};
export type DraftToolStatus = {
    available: boolean;
    tool_name: string | null;
    checked_modules: string[];
};
export type DraftAsset = {
    file_name: string;
    path: string;
    size_bytes: number;
    modified_at: string;
    import_status: string | null;
    import_message: string | null;
    draft_path: string | null;
    template_tracks: string[] | null;
};
export type DraftEnvironment = {
    output_path: string;
    output_exists: boolean;
    processed_video_count: number;
    package_count: number;
    draft_tool: DraftToolStatus;
    jianying_root_path: string;
    jianying_root_exists: boolean;
    jianying_10_7_detected: boolean;
    editable_draft_supported: boolean;
    jianying_blocker: string | null;
};
export type DraftStatus = {
    environment: DraftEnvironment;
    processed_videos: DraftAsset[];
    packages: DraftAsset[];
};
export type DraftPackage = {
    package_name: string;
    path: string;
    manifest_path: string;
    video_path: string;
    subtitle_path: string | null;
    cover_path: string | null;
    status: string;
};
export type DraftImport = {
    package_name: string;
    status: string;
    message: string;
    draft_path: string | null;
    manifest_path: string;
};
export type VoiceRecorderState = "idle" | "recording" | "transcribing" | "done";
export type VoiceTranscribeSegment = {
    start: number;
    end: number;
    text: string;
};
export type VoiceTranscribeResult = {
    transcript: string;
    confidence: number;
    segments: VoiceTranscribeSegment[];
};
