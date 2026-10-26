import type { ContentVisibility } from './common';
import type { NarrationCue } from './production';
import type { CreationPlanPreview, CreationPlanVoiceReference, CreationPlanPronunciationReference } from './creation-plans';
import type { GraphicOverlay, CreationTaskMontageClip, AudioTimeline } from './timeline';
export type CreationTask = {
    id: string;
    workspace_id: string;
    plan_id: string;
    visibility: ContentVisibility;
    name: string;
    scenario: string;
    stage: "topic" | "script" | "narration" | "montage" | "review";
    stage_status: "not_started" | "running" | "waiting_for_user" | "completed" | "failed";
    status: "created" | "running" | "waiting_for_user" | "completed" | "failed";
    progress: number;
    recovery_point: string;
    selected_topic_id: string | null;
    confirmed_script_id: string | null;
    narration_cache_key: string | null;
    montage_plan_id: string | null;
    current_timeline_version_id: string | null;
    timeline_sequence: number;
    can_undo: boolean;
    can_redo?: boolean;
    can_revise_script?: boolean;
    output_ready: boolean;
    stage_reason: string | null;
    stage_impact: string | null;
    stage_repair_action: string | null;
    stage_retryable: boolean | null;
    created_at: string;
    updated_at: string;
};
export type CreationTasksResponse = {
    items: CreationTask[];
    total_count: number;
};
export type CreationTaskTopicRoutePreflight = {
    provider_kind: string;
    execution_location: "cloud" | "local";
    model_id: string;
    endpoint_identity: string;
    connection_version: string;
    availability: "unconfigured" | "unverified" | "failed" | "succeeded";
    checked_at: string;
};
export type CreationTaskTopicEgressPreflight = {
    cloud_egress: boolean;
    source_count: number;
    text_excerpt_count: number;
    reason: string | null;
    impact: string | null;
    repair_action: string | null;
};
export type CreationTaskTopicGeneration = {
    id: string;
    workspace_id: string;
    task_id: string;
    plan_id: string;
    status: "running" | "succeeded" | "failed";
    route_snapshot: CreationTaskTopicRoutePreflight;
    prompt_schema_version: string;
    input_references: Record<string, unknown>[];
    reason: string | null;
    impact: string | null;
    repair_action: string | null;
    retryable: boolean | null;
    started_at: string;
    finished_at: string | null;
};
export type CreationTaskTopicCandidate = {
    id: string;
    run_id: string;
    title: string;
    angle: string;
    audience: string;
    outline: string[];
    score: number;
    selection_status: "unselected" | "confirmed";
    created_at: string;
};
export type CreationTaskTopicEvidenceReference = {
    id: string;
    candidate_id: string;
    kind: "knowledge" | "source";
    reference_id: string;
    version_or_identity: string;
    title: string;
    authorization_or_license: string;
    source_url: string | null;
    created_at: string;
};
export type CreationTaskTopicsResponse = {
    task: CreationTask;
    candidates: CreationTaskTopicCandidate[];
    evidence_references: CreationTaskTopicEvidenceReference[];
    selected_topic_id: string | null;
    latest_run: CreationTaskTopicGeneration | null;
    route_preflight: CreationTaskTopicRoutePreflight;
    egress_preflight: CreationTaskTopicEgressPreflight;
};
export type CreationTaskScriptSection = {
    id: string;
    title: string;
    content: string;
    visual_prompt: string;
    evidence_reference_ids: string[];
};
export type CreationTaskScript = {
    draft_complete: boolean;
    id: string;
    task_id: string;
    run_id: string;
    topic_id: string;
    title: string;
    language: "zh-CN" | "en-US" | "zh-CN/en-US";
    sections_total_count: number;
    selection_status: "draft" | "confirmed";
    created_at: string;
    updated_at: string;
    confirmed_at: string | null;
    reused_from_entry_id?: string | null;
    source_reply_reference?: {
        conversation_id: string;
        turn_id: string;
        response_digest: string;
    } | null;
} & ({ sections_loaded: true; sections: CreationTaskScriptSection[] }
    | { sections_loaded: false; sections: null });
export type CreationTaskScriptSectionWindow = {
    task_id: string;
    script_id: string;
    updated_at: string;
    title: string;
    language: CreationTaskScript["language"];
    selection_status: CreationTaskScript["selection_status"];
    total_count: number;
    items: { ordinal: number; section: CreationTaskScriptSection; digest: string }[];
    next_after_ordinal: number | null;
    truncated: boolean;
};
export type CreationTaskScriptWindowStartInput = {
    request_id: string;
    title: string;
    sections: CreationTaskScriptSection[];
    assistant_turn_id?: string;
};
export type CreationTaskScriptSectionsPatchInput = {
    request_id: string;
    expected_updated_at: string;
    sections: CreationTaskScriptSection[];
    insert_after_section_id?: string | null;
    assistant_turn_id?: string;
};
export type CreationTaskScriptWindowSaved = {
    task_id: string;
    script_id: string;
    request_id: string;
    status: "saved";
    updated_at: string;
    total_count: number;
    affected_section_ids: string[];
};
export type CreationTaskScriptConfirmSavedInput = {
    request_id: string;
    expected_updated_at: string;
};
export type CreationTaskScriptConfirmed = {
    task_id: string;
    script_id: string;
    request_id: string;
    status: "confirmed";
    updated_at: string;
    total_count: number;
};
export type CreationTaskScriptWindowUnsettled = {
    task_id: string;
    script_id?: string;
    request_id: string;
    operation: "start" | "patch" | "confirm";
    status: "unknown" | "failed";
    submission_effect: "unknown";
    message: string;
    submitted_input_available: boolean;
    submitted_input?: CreationTaskScriptWindowStartInput | CreationTaskScriptSectionsPatchInput | CreationTaskScriptConfirmSavedInput;
    error_detail?: unknown;
};
export type CreationTaskScriptWindowResult = CreationTaskScriptWindowSaved | CreationTaskScriptConfirmed | CreationTaskScriptWindowUnsettled;
export type CreationTaskScriptRequestLocator = { operation: "start" } | { operation: "patch" | "confirm"; script_id: string };
export type CreationTaskScriptsResponse = {
    task: CreationTask;
    scripts: CreationTaskScript[];
    latest_run: {
        id: string;
        status: "running" | "succeeded" | "failed";
        route_snapshot: CreationTaskTopicRoutePreflight;
        reason: string | null;
        impact: string | null;
        repair_action: string | null;
        retryable: boolean | null;
        started_at: string;
        finished_at: string | null;
    } | null;
    route_preflight: CreationTaskTopicRoutePreflight;
};
export type CreationTaskScriptGenerationInput = { request_id: string };
export type CreationTaskScriptGenerationReceipt = {
    request_id: string;
    task_id: string;
    conversation_id: string;
    turn_id: string;
    turn_status: import("./assistant").AssistantConversationTurn["status"];
    run_id: string;
    run_status: NonNullable<CreationTaskScriptsResponse["latest_run"]>["status"];
    script_id: string | null;
    status: "running" | "stopping" | "draft_ready" | "failed" | "stopped" | "incomplete" | "unknown";
    stop_requested: boolean;
    result_stale: boolean;
    script_updated_at: string | null;
    requested_effect: "draft";
};
export type CreationTaskNarrationVoiceSelectionRequest = {
    request_id: string;
    voice_profile_id: string;
    voice_version: string;
    expected_plan_updated_at: string;
    expected_task_updated_at: string;
};
export type CreationTaskNarrationVoiceSelectionResponse = {
    request_id: string;
    workspace_id: string;
    member_id: string;
    task_id: string;
    plan_id: string;
    status: "selected";
    recorded_at: string;
    expected_plan_updated_at: string;
    expected_task_updated_at: string;
    plan_updated_at: string;
    task_updated_at: string;
    previous_voice: CreationPlanVoiceReference | null;
    selected_voice: CreationPlanVoiceReference;
    pronunciation_references: CreationPlanPronunciationReference[];
    confirmed_script_id: string | null;
};
export type CreationTaskNarrationSegment = {
    id: string;
    source_section_id: string;
    text: string;
    audio_reference: string | null;
    audio_url: string | null;
    duration_seconds: number | null;
    sample_rate: number | null;
    model_id: string | null;
    voice_profile_id: string | null;
    voice_version: string | null;
    language: "zh" | "en" | "zh-en";
    pronunciation_version: string;
    status: "pending" | "ready" | "generating" | "failed";
    source_kind: "generated" | "recorded";
    source_artifact_ref: string | null;
    source_sha256: string | null;
    source_script_digest: string | null;
};
export type CreationTaskNarration = {
    task: CreationTask;
    script_id: string;
    audio_reference: string;
    duration_seconds: number;
    sample_rate: number | null;
    model_id: string | null;
    voice_profile_id: string | null;
    source_kind: "generated" | "recorded";
    source_artifact_ref: string | null;
    source_sha256: string | null;
    source_script_digest: string | null;
    language: "zh" | "en" | "zh-en";
    pronunciation_version: string;
    segments: CreationTaskNarrationSegment[];
};
export type CreationTaskPersonRender = {
    id: string;
    task_id: string;
    person_profile_id: string;
    person_version: string;
    voice_profile_id: string;
    voice_version: string;
    narration_segment_id: string;
    visual_reference: string;
    model_id: string;
    motion: "natural";
    expression: "neutral";
    status: "pending" | "running" | "ready" | "failed";
    duration_seconds: number | null;
    alignment_evidence: string | null;
    media_url: string | null;
    failure_reason: string | null;
    impact: string | null;
    repair_action: string | null;
    created_at: string;
    updated_at: string;
};
export type CreationTaskPersonRenderCapability = {
    available: boolean;
    model_id: string | null;
    message: string;
    repair_action: string | null;
};
export type CreationTaskCapability = {
    available: boolean;
    model_id: string | null;
    message: string;
    repair_action: string | null;
};
export type CreationTaskSubtitleStyle = {
    stroke_width?: number;
    stroke_color?: string;
    background_color?: string;
    background_opacity?: number;
    enabled?: boolean;
    name: string;
    font_family: string;
    font_size: number;
    primary_color: string;
    emphasis_color: string;
    position: "top" | "middle" | "bottom";
    bilingual_order: "source_then_translation" | "translation_then_source";
};
export type CreationTaskSubtitleCue = {
    id: string;
    source_cue_id: string | null;
    text: string;
    start_seconds: number;
    end_seconds: number;
    speaker: string | null;
    line_breaks: number[];
    emphasis: string[];
};
export type CreationTaskSubtitleTrack = {
    id: string;
    workspace_id: string;
    task_id: string;
    timeline_version_id: string;
    language: "zh-CN" | "en-US";
    source_language: "zh-CN" | "en-US" | null;
    style: CreationTaskSubtitleStyle;
    cues: CreationTaskSubtitleCue[];
    generation_method: "timeline" | "translation";
    model_id: string | null;
    artifact_ref: string | null;
    transform_id: string | null;
    status: "valid" | "failed" | "unavailable" | "superseded";
    failure_reason: string | null;
    impact: string | null;
    repair_action: string | null;
    created_at: string;
    updated_at: string;
};
export type CreationTaskCoverLayout = {
    title_position: "center" | "bottom";
    text_align: "left" | "center";
};
export type CreationTaskCoverSafeArea = {
    top: number;
    right: number;
    bottom: number;
    left: number;
};
export type CreationTaskCoverAsset = {
    id: string;
    workspace_id: string;
    task_id: string;
    timeline_version_id: string;
    source_video_ref: string;
    frame_time_seconds: number;
    title: string;
    safe_area: CreationTaskCoverSafeArea;
    layout: CreationTaskCoverLayout;
    generation_method: "video_frame" | "image_model";
    model_or_engine: string;
    artifact_ref: string | null;
    transform_id: string | null;
    status: "valid" | "failed" | "unavailable" | "superseded";
    failure_reason: string | null;
    impact: string | null;
    repair_action: string | null;
    created_at: string;
    updated_at: string;
};
export type CreationTaskVoiceOption = {
    profile_id: string;
    version: string;
    person_display_name: string;
    language: "zh" | "en" | "zh-en";
    model_id: string;
    runnable: boolean;
    runnable_reason: string | null;
};
export type CreationTaskQualityContinuation = {
    job_id: string;
    failed_request_id: string | null;
    phase: string;
    completed_phases: string[];
    can_request: boolean;
    reason_code: "actor_unavailable" | "runtime_unavailable" | "original_actor_required" | "source_changed" | "policy_unavailable" | "algorithm_changed" | "request_in_flight" | "failed_request_unavailable" | null;
    release_check: "on_submit";
};
export type CreationTaskQualityCheck = {
    continuation: CreationTaskQualityContinuation | null;
    check_state: "none" | "checking" | "failed" | "done";
    result_status: "clean" | "issues_found" | "unavailable" | null;
    stale: boolean;
    check_id: string | null;
    timeline_version_id: string | null;
    render_ref: string | null;
    checked_at: string | null;
    error: string | null;
    findings: {
        kind: "black_frame" | "truncation" | "clipping" | "timestamp_offset" | "delivery_duration";
        start_seconds: number;
        end_seconds: number;
        duration_seconds: number;
        detail: string;
        clip_ids: string[];
    }[];
    unavailable: {
        kind: string;
        reason: string;
        status: "unavailable";
    }[];
    clipping_summary: {
        event_count: number;
        total_clipped_seconds: number;
        first_start_seconds: number;
        last_end_seconds: number;
        full_scale_duty: number | null;
        event_list_limit: number;
        events: {
            start_seconds: number;
            end_seconds: number;
            duration_seconds: number;
        }[];
        events_truncated: boolean;
        truncated_event_count: number;
        findings_event_limit: number;
        findings_truncated: boolean;
        findings_truncated_count: number;
    } | null;
    black_frame_summary: {
        event_count: number;
        total_black_seconds: number;
        first_start_seconds: number;
        last_end_seconds: number;
        event_list_limit: number;
        events: {
            start_seconds: number;
            end_seconds: number;
            duration_seconds: number;
        }[];
        events_truncated: boolean;
        truncated_event_count: number;
        findings_event_limit: number;
        findings_truncated: boolean;
        findings_truncated_count: number;
    } | null;
    validation_scope: "deterministic_media_errors_only";
};
export type CreationTaskMontage = {
    graphic_overlays?: GraphicOverlay[];
    aspect_ratio?: "9:16" | "16:9" | "1:1";
    presenter_share_target?: 0 | 20 | 40 | 60 | 80 | 100;
    lipsync_quality?: "high" | "fast";
    presenter_share_actual?: number;
    audio_timeline?: AudioTimeline | null;
    id: string;
    task_id: string;
    script_id: string;
    script_title: string;
    status: "waiting_for_user" | "ready" | "rendering" | "rendered" | "failed";
    cues: NarrationCue[];
    clips: CreationTaskMontageClip[];
    duration_seconds: number;
    low_confidence_clip_count: number;
    narration_reference: string;
    output_url: string | null;
    subtitle_url: string | null;
    message: string;
    created_at: string;
    updated_at: string;
};
export type CreationTaskWorkspace = {
    task: CreationTask;
    plan: CreationPlanPreview;
    selected_topic: CreationTaskTopicCandidate | null;
    topic_evidence: CreationTaskTopicEvidenceReference[];
    scripts: CreationTaskScript[];
    available_voices: CreationTaskVoiceOption[];
    narration: CreationTaskNarration | null;
    person_renders: CreationTaskPersonRender[];
    portrait_available?: boolean;
    person_render_capability: CreationTaskPersonRenderCapability;
    montage: CreationTaskMontage | null;
    subtitle_tracks: CreationTaskSubtitleTrack[];
    cover_asset: CreationTaskCoverAsset | null;
    subtitle_translation_capability: CreationTaskCapability;
    cover_frame_capability: CreationTaskCapability;
    image_generation_capability: CreationTaskCapability;
};
export type CreationExecutionPreview = {
    task_id: string;
    script_id: string;
    script_title: string;
    script_updated_at: string;
    script_sections_total_count: number;
    script_sections_loaded: false;
    plan_id: string;
    narration_source: {
        source_kind: "generated" | "recorded";
        source_artifact_ref: string | null;
        duration_seconds: number;
    } | null;
    requirements: {
        aspect_ratio: string;
        duration_target_seconds: number;
        presenter_share_target: number;
        lipsync_quality: "high" | "fast";
        selected_voice: string | null;
        can_continue: boolean;
        continuation_blocker: string | null;
        template_settings: Record<string, unknown>;
    };
    input_digest: string;
};
export type SegmentVisualDecisionLocator = {
  job_id: string; section_id: string; phase: "segment_video"; section_title: string; adopted: boolean;
  decision_digest: string | null; detail_url: string;
};
export type SegmentVisualDecisionPage = { items: SegmentVisualDecisionLocator[]; total_count: number; truncated: boolean };
type SegmentVisualCandidateDecision = {
  task_id: string; job_id: string; section_id: string; decision_digest: string;
  status: "waiting_for_user" | "adopted" | "stale"; adopted: boolean;
  audio: { artifact_reference: string; persisted: boolean };
  usage: { settled: boolean; used_seconds: number; used_output_bytes: number };
  candidate_clips: { id: string; label: string; preview_url: string; needs_confirmation: boolean;
    source_start_seconds: number; source_end_seconds: number; timeline_start_seconds: number; timeline_end_seconds: number }[];
  candidate_total_count: number; candidate_offset: number; next_offset: number | null;
  required_confirmation_ids: string[]; confirmed_clip_ids: string[];
  device_request_id: string | null;
};
export type SegmentVisualPlanningConflict = {
  task_id: string; job_id: string; section_id: string;
  status: "planning_conflict"; can_adopt: false; reason: string;
  decision_digest: null; adopted: false;
  audio: { artifact_reference: string; persisted: boolean };
  usage: SegmentVisualCandidateDecision["usage"];
  candidate_clips: []; candidate_total_count: 0; candidate_offset: number; next_offset: null;
  required_confirmation_ids: []; confirmed_clip_ids: []; device_request_id: null;
  recovery: { action: "resume_original"; requires_revalidation: true; detail: string };
};
export type SegmentVisualPlanningRecovery = Omit<SegmentVisualPlanningConflict, "status" | "adopted"> & {
  status: "adopted"; adopted: true;
};
export type SegmentVisualDecision = SegmentVisualCandidateDecision | SegmentVisualPlanningConflict | SegmentVisualPlanningRecovery;
export type SegmentVisualDecisionAdoption = {
  decision: SegmentVisualDecision; queue: { request_id: string; replayed: boolean } | null; execution: CreationExecutionStatus;
};

export type CreationExecutionStatus = {
  visual_decisions?: SegmentVisualDecisionPage;
    task_id: string;
    job: {
        id: string;
        kind: string;
        status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
        progress: number;
        failure_reason: string | null;
        impact: string | null;
        repair_action: string | null;
    } | null;
    stages: {
        stage: string;
        status: string;
        reason?: string;
        at: string;
    }[];
    current_stage: "narration" | "person_render" | "montage_plan" | "render" | "cover" | null;
    waiting_reason: string | null;
    status_updated_at: string | null;
    video_ready: boolean;
    cover_status: "ready" | "pending";
    cover_message: string | null;
};
/** 一条后台作业在任务页上的真实读数。估时没有可信依据时为 null。 */
export type JobProgress = {
    id: string;
    kind: string;
    status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
    progress: number;
    attempt: number;
    started_at: string | null;
    finished_at: string | null;
    updated_at: string;
    elapsed_seconds: number | null;
    failure_reason: string | null;
    impact: string | null;
    repair_action: string | null;
    wait_estimate_seconds: number | null;
    processing_estimate_seconds: number | null;
    estimate_basis: string | null;
};
export type TaskJobProgress = {
    task_id: string;
    current: JobProgress | null;
    items: JobProgress[];
    total_count: number;
};

export type CreationTaskSegment = {
    task_id: string;
    section_id: string;
    ordinal: number;
    script_id: string;
    base_version_id: string | null;
    segment_version: string;
    title: string;
    text: string;
    source_text_sha256: string;
    source_references: string[];
    locked_body_digest: string | null;
    adjacent_sections: { id: string; title: string; content: string }[];
    narration_segment_id: string | null;
    audio: { kind: "audio"; artifact_ref: string | null; status: string; duration_seconds: number | null } | null;
    video: { kind: "video"; artifact_ref: string; sha256: string; segment_version: string; duration_seconds: number; url: string } | null;
    historical_video?: CreationTaskSegment['video'];
    adopted: boolean;
    adoption?: CreationTaskSegmentRevision['adoption'];
};
export type CreationTaskSegmentRevision = {
    task_id: string;
    request_id: string;
    input_id: string;
    section_id: string;
    job_id: string;
    status: string;
    queue_state: string;
    waiting_reason: string | null;
    semantic_review_ref: string;
    rules_check: { status: string };
    result: Record<string, unknown> | null;
    usage_source: string;
    adoption: {
        status: "not_produced" | "candidate" | "current" | "superseded" | "invalidated";
        current_timeline_version_id: string | null;
        current_timeline_sequence: number | null;
        current_segment_version: string | null;
        result_segment_version: string | null;
        reason: string | null;
    };
    recovery: {action: "resume_original" | "wait" | "query_original" | "none"; reason_code: string; requires_revalidation: boolean};
};
export type CreationTaskSegments = {
    task_id: string;
    total_count: number;
    available_video_count: number;
    segments: CreationTaskSegment[];
    offset: number;
    truncated: boolean;
    revision_requests: {
        items: Pick<CreationTaskSegmentRevision, "request_id" | "input_id" | "section_id" | "job_id" | "status" | "result" | "waiting_reason">[];
        total_count: number;
        truncated: boolean;
    };
};
export type CreationTaskBudget = {
    task_id: string;
    authorization_coverage: "tts_pcm_only" | "scope_unbound";
    scopes: {
        authorization: {
            scope_id: string;
            revision: number;
            limits: { execution_seconds: number | null; generated_bytes: number | null };
            policy_kind: "local_goal" | "explicit_total";
            revoked: boolean;
            target_task_ids: string[];
            source_ref: string;
        };
        usage: {
            execution_seconds: number;
            generated_bytes: number;
            reserved_seconds: number;
            reserved_bytes: number;
            unknown_unit_count: number;
            unit_count: number;
        };
        remaining: { execution_seconds: number | null; generated_bytes: number | null };
        status: "available" | "revoked" | "usage_unknown" | "exhausted";
        coverage: "tts_pcm_only";
    }[];
    status: string;
    waiting_reason: string | null;
    resources: {
        settled_execution_seconds: number;
        settled_generated_bytes: number;
        total_execution_seconds: number | null;
        total_generated_bytes: number | null;
        coverage: string;
        receipt_count: number;
    };
    actual_cost: { status: string; amount: number | null; currency: string | null; supplier_usage_refs: string[]; pricing_or_invoice_refs: string[] };
    estimates: { eta_seconds: number | null; cost: number | null };
};

export type CreationTaskNativeArtifact = {
    artifact_id: string;
    source_ref: string;
    logical_ref: string;
    sha256: string;
    size_bytes: number;
    media_type: string;
    role: string;
    preview_url: string;
    adopted: boolean;
};
export type CreationTaskNativeOperations = {
    task_id: string;
    items: { job_id: string; request_id: string; operation: string; status: string;
        process_state: string; release_state: string; artifacts: CreationTaskNativeArtifact[];
        failure_reason: string | null; waiting_reason: string | null; repair_action: string | null }[];
    total_count: number;
    truncated: boolean;
    offset: number;
};

export type CreationTaskStoryboardRegenerationInput = {
    request_id: string;
    script_id: string;
    input_digest: string;
    expected_timeline_version_id: string;
    expected_timeline_sequence: number;
};

export type CreationTaskStoryboardRegenerationReceipt = {
    request_id: string;
    task_id: string;
    job_id: string;
    status: NonNullable<CreationExecutionStatus["job"]>["status"];
    stage: "montage_plan";
    script_id: string;
    narration_cache_key: string;
    base_timeline_version_id: string;
    base_timeline_sequence: number;
    result: Record<string, unknown> | null;
    failure_reason: string | null;
    result_url: string;
};
