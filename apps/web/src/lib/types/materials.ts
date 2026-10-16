import type { ContentVisibility } from './common';
export type MaterialSegmentMatch = {
    asset_ref: string;
    segment_ref: string;
    score: number;
    reasons: string[];
};
export type MaterialAssistantSelection = ({
    scope: "asset";
    asset_id: string;
} | {
    scope: "segment";
    asset_id: string;
    segment_id: string;
}) & {
    expected_updated_at?: string;
    expected_segment_updated_at?: string;
};
export type MaterialAssistantResult = {
    scope: "asset" | "segment";
    asset_ref: string;
    segment_ref: string | null;
    user_tags: string[];
    system_tags?: string[];
    action?: "replace" | "add" | "remove";
    changed?: boolean;
    previous_user_tags?: string[];
};
export type MaterialAssistantTurnResponse = {
    reply: string;
    provider: string;
    model: string;
    material_result: MaterialAssistantResult | null;
    turn_id?: string | null;
    turn_status?: "running" | "completed" | "stopped";
    draft_version?: number | null;
};
export type MaterialSegment = {
    id: string;
    asset_id: string;
    start_seconds: number;
    end_seconds: number;
    description: string;
    transcript: string;
    visual_status: "pending" | "ready" | "blocked" | "failed";
    visual_model_id: string;
    visual_sample_count: number;
    understanding?: {
        status: "ready" | "blocked" | "failed";
        fingerprint: string;
        model_id: string;
        sample_times: number[];
        summary: string;
        facts: {
            kind: "object" | "action" | "scene";
            text: string;
            label?: string | null;
            sample_times: number[];
        }[];
        unknowns: string[];
        ocr_status: "ready" | "blocked" | "failed";
        ocr_model_id: string;
        ocr_frames: {
            id: string;
            timestamp_seconds: number;
            width: number;
            height: number;
            observations: {
                text: string;
                confidence: number;
                box: number[];
                retrieval_eligible: boolean;
            }[];
        }[];
        message: string;
    } | null;
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
    suggested_usage_role: "unassigned" | "a_roll" | "b_roll";
    usage_role_confidence: number;
    usage_role_reason: string;
    review_status: "raw" | "approved" | "rejected";
    system_tags: string[];
    user_tags: string[];
    collection_ids: string[];
    usage_count: number;
    last_used_at: string | null;
    created_at: string;
    updated_at: string;
};
export type MaterialFileRevision = {
    size_bytes: string;
    mtime_ns: string;
    ctime_ns: string;
    inode: string;
};
export type MaterialPendingCopy = {
    relative_path: string;
    expected_revision: MaterialFileRevision;
};
export type MaterialCopyCompleteInput = MaterialPendingCopy & {
    confirmation_source: "manual" | "app_import";
};
export type MaterialIntakeStatus = {
    bound_to_current_workspace: boolean;
    binding_available: boolean;
    configured_directory: string | null;
    bound_directory: string | null;
    directory_state: "ready" | "missing" | "unmounted" | "invalid" | "read_only" | "changed" | "unknown";
    directory_message: string;
    repair_action: string | null;
    scan_interval_seconds: number;
    pending_files: MaterialPendingCopy[];
    file_counts: {
        observing: number;
        running: number;
        ready: number;
        failed: number;
        blocked_disk: number;
    };
};
export type MaterialAsset = {
    id: string;
    workspace_id: string;
    visibility: ContentVisibility;
    file_name: string;
    path: string;
    size_bytes: number;
    modified_at: string;
    media_type: "video" | "audio" | "image";
    source_url: string | null;
    duration_seconds: number | null;
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
    suggested_usage_role: "unassigned" | "a_roll" | "b_roll";
    usage_role_confidence: number;
    usage_role_reason: string;
    review_status: "raw" | "approved" | "rejected";
    use_state: "available" | "reference_only" | "excluded" | "not_for_automation" | "not_for_publish";
    analysis_status: "pending" | "ready" | "blocked" | "failed";
    analysis_message: string;
    user_tags: string[];
    // The user's own words: what this one material is, and the batch background
    // it arrived with. Text, never facts the search or filter may score on.
    user_description: string;
    upload_context: string;
    speech_analysis?: {
        status: "ready" | "blocked" | "failed";
        source_fingerprint: string;
        model_id: string;
        observations: {
            start_ms: number;
            end_ms: number;
            text: string;
        }[];
        message: string;
    } | null;
    segments: MaterialSegment[];
    created_at: string;
    updated_at: string;
};
export type MaterialLibrary = {
    assets: MaterialAsset[];
    total_count: number;
    limit: number;
    offset: number;
    has_more: boolean;
};
/** 名称条目的类型：产品、菜品、人物。人物名称只用于素材分类，不是出镜形象。 */
export type BusinessObjectKind = "product" | "dish" | "person";
/**
 * 一条工作区自有的名称条目。用户自己怎么称呼这个东西，不是系统识别出的画面
 * 事实；与出镜形象/声音档案（PersonProfile）无关，关联不会创建形象或声音档案。
 */
export type MaterialBusinessObject = {
    id: string;
    kind: BusinessObjectKind;
    name: string;
};
export type MaterialBusinessObjects = {
    items: MaterialBusinessObject[];
    total_count: number;
};
/**
 * 一条使用记录回查结果：某个片段被哪条成片用过。
 * 使用记录本身没有时间线版本外键，一个任务的多个快照也可能都含同一片段，
 * 所以 timeline_version_id 只是该任务的**当前**版本（resolution="current"），
 * 不代表当次使用时的版本；无法指认时 resolution="unknown"，界面须写明版本未记录。
 */
export type MaterialUsageRecordTask = {
    id: string | null;
    name: string | null;
    stage: string | null;
    status: string | null;
    current_timeline_version_id: string | null;
};
export type MaterialUsageRecord = {
    plan_id: string;
    clip_id: string;
    used_at: string;
    task: MaterialUsageRecordTask | null;
    role: string | null;
    manually_selected: boolean | null;
    timeline_version_id: string | null;
    resolution: "current" | "unknown";
};
export type MaterialUsageRecords = {
    items: MaterialUsageRecord[];
    total_count: number;
};
export type MaterialCollection = {
    id: string;
    workspace_id: string;
    name: string;
    parent_id: string | null;
    created_at: string;
    updated_at: string;
};
export type MaterialCollections = {
    collections: MaterialCollection[];
    total_count: number;
};
export type MaterialTagStat = {
    tag: string;
    // 素材数量：同一素材内同名标签只计一次（资产用户标签 + 片段系统/用户标签）
    asset_count: number;
    // 片段数量：保留原语义，不作为素材数量的替代
    segment_count: number;
    duration_seconds: number;
    usage_count: number;
};
export type MaterialLibraryInsights = {
    asset_count: number;
    segment_count: number;
    tags: MaterialTagStat[];
    recommended_gaps: string[];
};
export type MaterialAssetNote = {
    asset_id: string;
    user_description: string;
    upload_context: string;
};

export type DiscoveryCandidate = {
  id: string;
  file_name: string;
  location: string;
  media_type: "video" | "audio" | "image";
  size_bytes: number;
  duration_seconds: number | null;
  state: "candidate" | "receiving" | "received" | "failed";
  asset_id: string | null;
  error: string | null;
  excluded: boolean;
  duplicate_asset_id?: string | null;
};
export type MaterialDiscovery = {
  id: string;
  state: "scanning" | "paused" | "cancelled" | "completed";
  exclusions: string[];
  visited_count: number;
  skipped_count: number;
  issues: { location: string; message: string }[];
  updated_at: string;
  candidates: DiscoveryCandidate[];
  total_count: number;
  offset: number;
  has_more: boolean;
};
export type DiscoveryRoots = {
  roots: { id: string; label: string; display_path: string; available: boolean }[];
  exclusions: string[];
};
