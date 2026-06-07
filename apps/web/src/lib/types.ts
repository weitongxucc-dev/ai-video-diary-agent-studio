export type EpisodeStatus =
  | "created"
  | "sources_ingesting"
  | "topics_ready"
  | "topic_approved"
  | "script_ready"
  | "script_approved"
  | "waiting_video"
  | "video_processing"
  | "draft_ready"
  | "published"
  | "failed";

export type VideoJobStatus =
  | "idle"
  | "processing"
  | "drafting"
  | "completed"
  | "failed"
  | "interrupted";

export type EpisodeIntakeMode = "planned" | "ad_hoc_video" | "ad_hoc_video_batch";

export type FeedbackValue = "up" | "down";

export type FeedbackTargetType = "topic" | "script_section" | "published_episode";

export type ScriptVersionSource =
  | "ai_generated"
  | "manual_edit"
  | "full_regeneration"
  | "section_regeneration"
  | "revert";

export type SourceRef = {
  source_type: string;
  title: string;
  ref: string;
};

export type TopicCandidate = {
  id: string;
  title: string;
  core_viewpoint: string;
  reason: string;
  audience: string;
  outline: string[];
  score: number;
  source_refs: SourceRef[];
  feedback: FeedbackValue | null;
};

export type ScriptSection = {
  id: string;
  title: string;
  estimated_seconds: number;
  content: string;
  feedback: FeedbackValue | null;
};

export type ScriptPackage = {
  id: string;
  hook: string;
  sections: ScriptSection[];
  title_options: string[];
  cover_copy: string;
  publish_copy: string;
  comment_prompt: string;
};

export type ScriptVersion = {
  id: string;
  source: ScriptVersionSource;
  script: ScriptPackage;
  changed_section_ids: string[];
  reason: string | null;
  active: boolean;
  created_at: string;
};

export type FeedbackEvent = {
  episode_id: string | null;
  id: string;
  target_type: FeedbackTargetType;
  target_id: string;
  value: FeedbackValue;
  reason: string | null;
  active: boolean;
  before_snapshot: Record<string, unknown> | null;
  after_snapshot: Record<string, unknown> | null;
  reverted_at: string | null;
  revert_reason: string | null;
  created_at: string;
};

export type Episode = {
  id: string;
  day_number: number;
  episode_date: string;
  status: EpisodeStatus;
  intake_mode: EpisodeIntakeMode;
  content_template_id: string;
  topics: TopicCandidate[];
  selected_topic_id: string | null;
  script: ScriptPackage | null;
  script_versions: ScriptVersion[];
  video_source_file_name: string | null;
  video_source_file_names: string[];
  video_processed_file_name: string | null;
  video_processed_path: string | null;
  video_edit_plan_path: string | null;
  video_dedup_report_path: string | null;
  video_job_status: VideoJobStatus;
  video_job_message: string | null;
  video_job_error: string | null;
  video_job_started_at: string | null;
  video_job_finished_at: string | null;
  draft_package_name: string | null;
  draft_package_path: string | null;
  publish_url: string | null;
  play_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  published_at: string | null;
  feedback_events: FeedbackEvent[];
  created_at: string;
  updated_at: string;
};

export type SystemStatus = {
  llm: {
    provider: string;
    configured: boolean;
    model: string | null;
    base_url: string;
  };
  obsidian: {
    configured: boolean;
    exists: boolean;
    path: string | null;
    markdown_count: number;
  };
  product_hunt: {
    configured: boolean;
    api_url: string;
  };
  external_sources: {
    configured: boolean;
    urls: string[];
  };
  hermes_radar: {
    configured: boolean;
    exists: boolean;
    path: string | null;
    latest_report: string | null;
    total_count: number;
    product_hunt_count: number;
    watcha_count: number;
  };
  video: {
    inbox_path: string;
    output_path: string;
    inbox_exists: boolean;
    output_exists: boolean;
    pending_count: number;
    ffmpeg_available: boolean;
  };
  storage: {
    database_path: string;
    database_exists: boolean;
  };
  asr: {
    provider: string;
    configured: boolean;
    region: string;
    engine_model_type: string;
    max_local_audio_mb: number;
    local_whisper_configured: boolean;
    local_whisper_python_path: string | null;
    local_whisper_model_path: string | null;
  };
};

export type ContentTemplate = {
  template_id: string;
  name: string;
  scenario: string;
  recommended_duration: string;
  topic_structure: string[];
  script_structure: string[];
  material_requirements: string[];
  jianying_style: string;
  subtitle_style: string;
  title_rules: string[];
  cover_copy_rules: string[];
  publish_copy_rules: string[];
  default_feedback_dimensions: string[];
};

export type ContentTemplates = {
  templates: ContentTemplate[];
};

export type RuntimeHealthStatus = "ready" | "missing" | "degraded" | "invalid";

export type RuntimeHealthItem = {
  id: string;
  label: string;
  status: RuntimeHealthStatus;
  reason: string;
  impact: string;
  repair_action: string;
  last_checked_at: string;
};

export type RuntimeHealth = {
  summary: {
    total: number;
    ready: number;
    missing: number;
    degraded: number;
    invalid: number;
  };
  items: RuntimeHealthItem[];
};

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
  remove: { start: number; end: number };
  keep: { start: number; end: number };
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

export type KnowledgeNote = {
  title: string;
  ref: string;
  excerpt: string;
  modified_at: string;
  tags: string[];
  wikilinks: string[];
  outlinks: string[];
};

export type SourceItem = {
  source_type: string;
  title: string;
  ref: string;
  excerpt: string;
};

export type SourceItems = {
  items: SourceItem[];
  total_count: number;
  source_counts: Record<string, number>;
};

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
};

export type Preferences = {
  total_feedback_count: number;
  preferences: PreferenceItem[];
};

export type RuntimeSettingItem = {
  name: string;
  configured: boolean;
  value: string | null;
  secret: boolean;
};

export type RuntimeSettings = {
  settings: RuntimeSettingItem[];
  status: SystemStatus;
};

export type RuntimeSettingsUpdate = {
  deepseek_api_key?: string;
  deepseek_model?: string;
  product_hunt_token?: string;
  tencentcloud_secret_id?: string;
  tencentcloud_secret_key?: string;
  tencentcloud_asr_region?: string;
  tencentcloud_asr_engine_model_type?: string;
  local_whisper_python_path?: string;
  local_whisper_model_path?: string;
  local_whisper_language?: string;
  obsidian_vault_path?: string;
  hermes_radar_path?: string;
  video_inbox_path?: string;
  video_output_path?: string;
  draft_output_path?: string;
  jianying_draft_root_path?: string;
  database_path?: string;
  topic_source_urls?: string[];
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

export type WorkflowStatus = {
  framework: string;
  node_names: string[];
  human_review_nodes: string[];
};

export type VoiceCommandOperation = {
  kind: string;
  label: string;
  params: Record<string, unknown>;
};

export type VoiceCommandResponse = {
  status: "ready" | "blocked" | "unknown" | "executed";
  intent: string;
  summary: string;
  can_execute: boolean;
  next_url: string | null;
  operations: VoiceCommandOperation[];
  result_episode: Episode | null;
  result_edit_plan: EditPlan | null;
};
