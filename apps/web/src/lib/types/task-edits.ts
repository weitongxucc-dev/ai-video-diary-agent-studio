import type { CreationTaskNarration, CreationTaskSubtitleStyle, CreationTaskMontage } from './creation-tasks';
import type { TextOverlay, ImageOverlay, ArrowOverlay, CreationTaskRecording } from './timeline';
export type CreationTaskEditIntent = {
    id: string;
    workspace_id: string;
    task_id: string;
    raw_command: string;
    context_stage: string;
    selected_object_refs: string[];
    playhead_ms: number | null;
    intent_type: string;
    scope: Record<string, unknown>;
    constraints: Record<string, unknown>;
    confidence: number;
    status: string;
    created_at: string;
};
export type CreationTaskExactEditInput = {
    operation_type: "ChangeCrop";
    clip_id: string;
    fit_mode: "fill" | "fit";
    crop_x: number;
    crop_y: number;
} | {
    operation_type: "ReplaceClip";
    clip_id: string;
    asset_id: string;
    segment_id: string;
    source_start_seconds: number;
}
// InsertClip 在锚点镜头之后插入一个片段。clip_id 是锚点，必填：后端
// ExactPreviewUseCase 按操作分派，InsertClip 由 linked 用例校验锚点；
// MetadataEditUseCase 单独校验 ReplaceClip 等精确镜头操作的必填参数。
// 不带 duration：既有 25 个操作都没有该参数，时长继承源片段 span。
// 落点不是播放头——既有 SplitClip 就是 plan.clips.insert(index + 1, ...)，
// 插在锚点后面是这个编辑器一贯的做法。字面量名与后端 schemas.py 的三处
// Literal 逐字一致，拼错即 422。
 | {
    operation_type: "InsertClip";
    clip_id: string;
    asset_id: string;
    segment_id: string;
    source_start_seconds: number;
} | {
    operation_type: "DeleteClip" | "DuplicateClip";
    clip_id: string;
} | {
    operation_type: "ChangeClipSpeed" | "ChangeLinkedSpeed";
    clip_id: string;
    playback_rate: number;
} | {
    operation_type: "SplitClip";
    clip_id: string;
    split_time_seconds: number;
} | {
    operation_type: "TrimClip";
    clip_id: string;
    source_start_seconds: number;
} | {
    operation_type: "ReorderClip";
    clip_id: string;
    position: number;
} | {
    operation_type: "SetClipLock";
    clip_id: string;
    locked: boolean;
} | {
    operation_type: "ConfirmClip";
    clip_id: string;
} | {
    operation_type: "RegenerateNarrationSegment";
    narration_segment_id: string;
    delivery_instruction?: string;
} | {
    operation_type: "SplitSubtitleCue";
    track_id: string;
    cue_id: string;
    split_offset: number;
    split_time_seconds: number;
} | {
    operation_type: "MergeSubtitleCue" | "DeleteSubtitleCue";
    track_id: string;
    cue_id: string;
} | {
    operation_type: "EditGraphicOverlay";
    overlay_action: "add" | "update" | "delete";
    graphic_overlay?: (Omit<TextOverlay, "id"> | Omit<ImageOverlay, "id"> | Omit<ArrowOverlay, "id"> | Omit<import('./timeline').VisualTemplateOverlay, "id">) & {
        id?: string;
    };
    overlay_id?: string;
} | {
    operation_type: "SetClipTransition";
    clip_id: string;
    transition_seconds: number;
} | {
    operation_type: "ChangeCanvas";
    aspect_ratio: "9:16" | "16:9" | "1:1";
    fit_mode?: "fill" | "fit";
} | {
    operation_type: "ReplaceSubtitleText";
    track_id: string;
    find_text: string;
    replacement_text: string;
} | {
    operation_type: "UpdateSubtitleCue";
    track_id?: string;
    cue_id: string;
    text: string;
    start_seconds?: number;
    end_seconds?: number;
    line_breaks?: number[];
    emphasis?: string[];
    style?: CreationTaskSubtitleStyle;
} | {
    operation_type: "ConfigureSubtitleTrack";
    track_id?: string;
    subtitle_display?: "none" | "zh" | "en" | "zh-en";
    target_language?: "en-US";
    style?: Partial<CreationTaskSubtitleStyle>;
} | {
    operation_type: "GenerateCoverFromFrame";
    frame_time_seconds: number;
    title: string;
    title_position?: "center" | "bottom";
} | {
    operation_type: "ChangePersonSegment";
    clip_id: string;
} | {
    operation_type: "RegeneratePersonRender";
    clip_id: string;
};
export type CreationTaskExactEditPayload = CreationTaskExactEditInput extends infer Input ? Input extends {
    operation_type: string;
} ? Omit<Input, "operation_type"> : never : never;
export type EditPreviewItem = {
    image_url?: string | null;
    video_url?: string | null;
    audio_url?: string | null;
    ref: string;
    label: string;
    detail: string;
};
export type CreationTaskEditProposal = {
    id: string;
    workspace_id: string;
    task_id: string;
    intent_id: string;
    summary: string;
    target_refs: string[];
    operation: {
        operation_type: CreationTaskExactEditInput["operation_type"] | "EditAudioTimeline" | "EditCompoundTimeline" | "ReorderTimelineItems" | "RemoveSilence" | "AdjustVisualEffects" | "AlignSubtitleTiming" | "ChangePresenterShare";
        payload: Record<string, unknown>;
    };
    invalidated_refs: string[];
    preview: {
        before: EditPreviewItem;
        after: EditPreviewItem;
        text_changes?: {
            cue_id: string;
            before: string;
            after: string;
            occurrences: number;
        }[];
    };
    external_effects: string[];
    status: "proposed" | "confirmed" | "rejected" | "expired";
    created_at: string;
    updated_at: string;
};
export type CreationTaskEditPreviewResponse = {
    intent: CreationTaskEditIntent;
    proposal: CreationTaskEditProposal;
};
export type TaskMediaResultMap = {
    graphic_source: {
        source_ref: string;
        artifact_id: string;
        sha256: string;
        size_bytes: number;
        name: string;
        width: number;
        height: number;
        applied: false;
    };
    edit_preview: CreationTaskEditPreviewResponse;
    audio_source: {
        source_ref: string;
        name: string;
        duration_ms: number;
    };
    recording: CreationTaskRecording;
    audio_listen: {
        audio_url: string;
    };
    waveform: {
        peaks: number[];
        seconds_per_peak: number;
    };
};
export type ResourceAdmissionCheck = {
    actual: number | null;
    minimum: number | null;
    maximum: number | null;
    passed: boolean | null;
} & ({
    metric: "cpu_percent";
    unit: "percent";
} | {
    metric: "available_memory_bytes" | "swap_used_bytes" | "disk_free_bytes";
    unit: "bytes";
} | {
    metric: "disk_io_bytes_per_second";
    unit: "bytes_per_second";
});
export type ResourceAdmissionReceipt = {
    observation_id: string;
    device_request_id: string;
    task_id: string;
    job_id: string;
    input_digest: string;
    base_version_id: string | null;
    attempt_number: number;
    last_grant_generation: number | null;
    phase: "admission";
    observation_kind: "first_unknown" | "first_refused";
    checked_at: string;
    observed_interval_seconds: number | null;
    context: "pending_request" | "history";
    admitted: null | false;
    checks: ResourceAdmissionCheck[];
    summary: string;
    repair_action: string;
};
export type TaskMediaJob = {
    operation: string;
    target_refs: string[];
    input_digest: string;
    created_at: string;
    job_id: string;
    status: "queued" | "running" | "interrupted" | "succeeded" | "failed" | "cancelled" | "stale";
    result_kind: keyof TaskMediaResultMap;
    result_url: string;
    device_request_id: string | null;
    waiting_reason: string | null;
    base_version_id: string | null;
    result: TaskMediaResultMap[keyof TaskMediaResultMap] | null;
    failure_reason: string | null;
    repair_action: string | null;
    resource_wait: ResourceAdmissionReceipt | null;
};
export type TaskMediaJobPage = {
    items: TaskMediaJob[];
    next_cursor: string | null;
};
export type CreationTaskEditOperation = {
    id: string;
    workspace_id: string;
    task_id: string;
    proposal_id: string;
    operation_type: CreationTaskExactEditInput["operation_type"] | "EditAudioTimeline" | "EditCompoundTimeline" | "ReorderTimelineItems" | "RemoveSilence" | "AdjustVisualEffects" | "AlignSubtitleTiming" | "ChangePresenterShare";
    payload: CreationTaskExactEditPayload;
    base_version_id: string | null;
    result_version_id: string | null;
    status: "running" | "applied" | "failed";
    failure_reason: string | null;
    created_at: string;
    applied_at: string | null;
};
export type EditExecutionReadiness = {
    status: "authorization_ready" | "not_ready";
    reason_code: string | null;
    device_admission: "not_checked";
};
export type CreationTaskEditConfirmResponse = {
    operation: CreationTaskEditOperation;
    montage: CreationTaskMontage | null;
    narration?: CreationTaskNarration | null;
    execution_readiness?: EditExecutionReadiness | null;
};
export type CreationTaskEditQueuedResponse = {
    status: "queued";
    job_id: string;
    request_id: string;
};
export type CreationTaskEditResult = CreationTaskEditConfirmResponse | CreationTaskEditQueuedResponse;

export type CreationTaskClipMetadataInput = Extract<CreationTaskExactEditInput, {
    operation_type: "ChangeCrop" | "ReplaceClip" | "SplitClip" | "TrimClip" | "ReorderClip" | "SetClipLock" | "ConfirmClip";
}> | (Extract<CreationTaskExactEditInput, { operation_type: "ChangeClipSpeed" | "ChangeLinkedSpeed" }> & {
    operation_type: "ChangeClipSpeed";
});

/** 保存输入在原请求结果核实前保持不变。 */
export type CreationTaskSubtitleSaveInput = Extract<CreationTaskExactEditInput, { operation_type: "UpdateSubtitleCue" }> & {
    request_id: string;
    expected_timeline_version_id: string | null;
    expected_timeline_sequence: number;
};
export type CreationTaskMetadataSaveInput = (CreationTaskClipMetadataInput | Extract<CreationTaskExactEditInput, {
    operation_type: "UpdateSubtitleCue" | "EditGraphicOverlay";
}>) & Pick<CreationTaskSubtitleSaveInput, "request_id" | "expected_timeline_version_id" | "expected_timeline_sequence">;
export type CreationTaskEditRequestReceipt = {
    request_id: string;
    task_id: string;
    phase: "apply";
    input_digest: string;
    status: "unknown" | "recorded";
    submitted_input: CreationTaskMetadataSaveInput;
    result_status_code: number | null;
    result: CreationTaskEditResult | { detail: unknown } | null;
};
