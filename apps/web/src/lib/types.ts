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

export type EpisodeIntakeMode =
  | "planned_single"
  | "planned_batch"
  | "ad_hoc_single"
  | "ad_hoc_batch";

export type VideoProductionMode =
  | "bilibili"
  | "youtube"
  | "shorts"
  | "douyin"
  | "xiaohongshu";

export type ProductionMode = VideoProductionMode;

export type ProductionChainStatus =
  | "pending"
  | "processing"
  | "completed"
  | "failed"
  | "skipped";

export type ProductionChainStep = {
  stage: string;
  status: ProductionChainStatus;
  output?: string | null;
};

export type ProductionChain = {
  episode_id: string;
  mode: ProductionMode;
  steps: ProductionChainStep[];
  updated_at: string | null;
};

export type SourceFileState =
  | "pending"
  | "processing"
  | "processed"
  | "archived"
  | "skipped";

export type SourceFileStateEntry = {
  file_name: string;
  state: SourceFileState;
  output_path: string | null;
  processed_at: string | null;
  archived_at: string | null;
};

export type SourceFileStates = {
  episode_id: string;
  files: SourceFileStateEntry[];
};

export type FeedbackValue = "up" | "down";

export type FeedbackTargetType = "topic" | "script_section";

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

export type PublishFeedback = {
  publish_url: string | null;
  platform: string | null;
  play_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  notes: string | null;
  published_at: string | null;
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
  platform: string | null;
  notes: string | null;
  play_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  published_at: string | null;
  publish_feedback: PublishFeedback | null;
  feedback_events: FeedbackEvent[];
  production_mode: ProductionMode | null;
  archived_source_file_names: string[];
  created_at: string;
  updated_at: string;
};

export type EpisodeHistoryItem = {
  id: string;
  day_number: number;
  episode_date: string;
  status: EpisodeStatus;
  intake_mode: EpisodeIntakeMode;
  topic_title: string | null;
  script_title: string | null;
  publish_url: string | null;
  platform: string | null;
  play_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  draft_package_path: string | null;
  video_dedup_report_path: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EpisodeHistory = {
  total_count: number;
  filtered_count: number;
  status: EpisodeStatus | null;
  items: EpisodeHistoryItem[];
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

export type VideoTemplateSettings = {
  language: CreationPlanInput["language"];
  aspect_ratio: "9:16";
  duration_target_seconds: number;
  voice: { profile_id: string; version: string } | null;
  script_structure?: string[];
  script_section_count?: number | null;
  style_instruction: string;
  fit_mode: "fill" | "fit";
  subtitle_style: Record<string, unknown>;
};

export type VideoTemplate = {
  id: string;
  version: number;
  name: string;
  origin: "built_in" | "user";
  settings: VideoTemplateSettings;
  source_task_id: string | null;
  source_timeline_version_id: string | null;
  created_at: string;
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

export type ToastKind = "success" | "error" | "info";

export type SetupStatus = {
  configured: boolean;
  missing: string[];
  next_step: string;
};

export type SetupPreflightCheckStatus =
  | "ready"
  | "missing"
  | "invalid"
  | "degraded"
  | "not_applicable";

export type SetupPreflightOverall = "ready" | "blocked";

export type SetupPreflightCheckItem = {
  id: string;
  label: string;
  status: SetupPreflightCheckStatus;
  summary: string;
  impact: string;
  repair_action: string;
  settings_target: string | null;
  required: boolean;
};

export type SetupPreflightResponse = {
  overall: SetupPreflightOverall;
  checks: SetupPreflightCheckItem[];
  generated_at: string;
};

export type OnboardingCompletionResponse = {
  workspace_id: string;
  completed: boolean;
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

export type SourceSyncStatus = {
  source_type: string;
  configured: boolean;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  error_message: string | null;
};

export type SourcesStatus = {
  statuses: SourceSyncStatus[];
};

export type SourceSyncRetryResult = {
  source_type: string;
  status: string;
  message: string;
};

export type SourceEvidenceFreshness = "fresh" | "aging" | "stale" | "unknown";

export type TopicEvidence = {
  source_key: string;
  source_type: string;
  title: string;
  ref: string;
  excerpt: string;
  freshness: SourceEvidenceFreshness;
  freshness_label: string;
  participated_topics: number;
  last_used_at: string | null;
};

export type TopicEvidenceList = {
  episode_id: string;
  total: number;
  items: TopicEvidence[];
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
  enabled: boolean;
};

export type Preferences = {
  total_feedback_count: number;
  preferences: PreferenceItem[];
};

export type PreferenceCard = {
  id: string;
  label: string;
  signal: string;
  category: string;
  confidence: number;
  evidence_count: number;
  enabled: boolean;
  latest_evidence_at: string | null;
};

export type PreferenceCards = {
  total_count: number;
  enabled_count: number;
  disabled_count: number;
  cards: PreferenceCard[];
};

export type PreferenceEvidenceItem = {
  feedback_event_id: string;
  episode_id: string | null;
  episode_date: string | null;
  target_type: string;
  target_id: string;
  value: "up" | "down";
  reason: string | null;
  created_at: string;
};

export type PreferenceEvidence = {
  preference_id: string;
  total: number;
  items: PreferenceEvidenceItem[];
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
  understanding_runtime_python_path?: string;
  native_ocr_worker_path?: string;
  asr_provider?: string;
  local_whisper_backend?: string;
  local_whisper_cli_path?: string;
  llm_provider?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  llm_model?: string;
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
  model_root_path?: string;
  temporary_root_path?: string;
  topic_source_urls?: string[];
};

export type LocalModelStatus = {
  model: {
    id: string;
    capability: string;
    display_name: string;
    repository: string;
    revision: string;
    license_name: string;
    license_url: string;
    approximate_download_bytes: number;
  };
  state:
    | "missing_runtime"
    | "missing_worker"
    | "not_installed"
    | "invalid"
    | "ready";
  ready: boolean;
  message: string;
  model_path: string;
  runtime_python_path: string | null;
  worker_path: string | null;
};

export type LocalModelCatalog = {
  models: LocalModelStatus[];
};

export type ModelInstallJob = {
  id: string;
  model_id: string;
  display_name: string;
  status: "queued" | "running" | "ready" | "failed" | "interrupted";
  phase: string;
  message: string;
  bytes_downloaded: number;
  bytes_total: number;
  progress: number;
  license_name: string;
  license_url: string;
  license_accepted_at: string;
  created_at: string;
  updated_at: string;
  model_root_path: string;
  external_volume: string | null;
  free_bytes: number;
  required_bytes: number;
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

export type WorkflowStatus = {
  framework: string;
  node_names: string[];
  human_review_nodes: string[];
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

// ── Creator Profile ──────────────────────────────────────────────

export type CreatorProfile = {
  id: string;
  common_expressions: string[];
  banned_words: string[];
  style_preferences: Record<string, string>;
  created_at: string;
  updated_at: string;
};

export type CreatorProfileUpdate = {
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

export type TemplateTrackKind =
  | "title_card"
  | "chapter_labels"
  | "keyword_emphasis"
  | "background_music";

export type TemplateTrackAvailability = "available" | "absent" | "unknown";

export type TemplateTrackSource =
  | "package-metadata"
  | "package-fallback-empty"
  | "package-fallback-absent"
  | "settings"
  | "none";

export type TemplateTrackEntry = {
  kind: TemplateTrackKind;
  label: string;
  description: string;
  availability: TemplateTrackAvailability;
  detail: string;
  impact: string;
  fixHref?: string;
  source: TemplateTrackSource;
  raw: string | null;
};

export type TemplateTrackSnapshot = {
  source: "episode+settings" | "episode" | "settings" | "package" | "none";
  tracks: TemplateTrackEntry[];
  readyCount: number;
  totalCount: number;
  lastPackageImportedAt: string | null;
  contentTemplateId: string | null;
  backgroundMusicConfigured: boolean;
  packageMetadata: string[] | null;
  packageImportStatus: string | null;
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

export type ProductionReadiness = {
  ready: boolean;
  blockers: { code: string; message: string }[];
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
  status:
    | "created"
    | "blocked"
    | "running"
    | "waiting_for_user"
    | "interrupted"
    | "ready"
    | "reviewed"
    | "failed";
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
  eligible_episodes: { id: string; title: string; status: EpisodeStatus }[];
};

export type CreationPlanInput = {
  template_id?: string | null;
  template_version?: number | null;
  goal: string;
  name?: string;
  target_count: number;
  language: "zh-CN" | "en-US" | "zh-CN/en-US";
  aspect_ratio: "9:16" | "16:9" | "1:1";
  duration_target_seconds: number;
};

export type CreationPlanKnowledgeReference = {
  id: string;
  version: string;
  title: string;
  authorization: string;
  ownership: string;
};

export type CreationPlanSourceReference = {
  id: string;
  connector_id: string;
  source_label: string;
  title: string;
  license: string;
};

export type CreationPlanVoiceReference = {
  profile_id: string;
  version: string;
  person_profile_id: string;
  person_version: string;
  person_authorization_id: string;
  person_authorization_version: string;
  authorization_id: string;
  authorization_version: string;
  language: string;
  model_id: string;
};

export type CreationPlanPronunciationReference = {
  id: string;
  version: string;
  scope: "workspace" | "person" | "task";
  scope_reference: string | null;
  language: "zh" | "en" | "zh-en";
};

export type CreationPlanModelStatus = {
  id: string;
  capability: string;
  state: string;
  ready: boolean;
  message: string;
};

export type CreationPlanBlocker = {
  reason: string;
  impact: string;
  repair_action: string;
  stage: string;
  blocks_confirmation: boolean;
};

export type MaterialSegmentMatch = {
  asset_ref: string;
  segment_ref: string;
  score: number;
  reasons: string[];
};

export type CreationTask = {
  id: string;
  workspace_id: string;
  plan_id: string;
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
  id: string;
  task_id: string;
  run_id: string;
  topic_id: string;
  title: string;
  language: "zh-CN" | "en-US" | "zh-CN/en-US";
  sections: CreationTaskScriptSection[];
  selection_status: "draft" | "confirmed";
  created_at: string;
  updated_at: string;
  confirmed_at: string | null;
};

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

export type CreationTaskNarrationSegment = {
  id: string;
  source_section_id: string;
  text: string;
  audio_reference: string | null;
  audio_url: string | null;
  duration_seconds: number | null;
  sample_rate: number | null;
  model_id: string;
  voice_profile_id: string;
  voice_version: string;
  language: "zh" | "en" | "zh-en";
  pronunciation_version: string;
  status: "pending" | "ready" | "generating" | "failed";
};

export type CreationTaskNarration = {
  task: CreationTask;
  script_id: string;
  audio_reference: string;
  duration_seconds: number;
  sample_rate: number;
  model_id: string;
  voice_profile_id: string;
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

export type VisualEffects = { fade_in_seconds?: number; fade_out_seconds?: number; zoom?: number; rotation?: number; offset_x?: number; offset_y?: number; beauty_strength?: number; skin_exposure?: number; exposure: number; contrast: number; saturation: number; temperature: number };
export type VisualEditInput = { base_version_id: string | null; clip_ids: string[]; effects: VisualEffects; raw_command?: string };

export type TextOverlay = {id:string;text:string;start_seconds:number;end_seconds:number;x:number;y:number;width:number;font_size:number;font_family:string;color:string};

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
  id: string; name: string; source_ref: string; source_start_ms: number; source_end_ms: number; timeline_start_ms: number;
  gain_db: number; fade_in_ms: number; fade_out_ms: number; muted: boolean; locked: boolean; denoise_db: number; loudness_target_lufs?: number | null;
  gain_points: { offset_ms: number; gain_db: number }[]; editor_gain_points?: { offset_ms: number; gain_db: number }[]; envelope_offset_ms: number; envelope_duration_ms: number | null;
};
export type TimelineAudioTrack = {
  id: string; name: string; role: "narration" | "original" | "music" | "effect";
  gain_db: number; muted: boolean; locked: boolean; duck_under_voice: boolean; clips: TimelineAudioClip[];
};
export type AudioTimeline = { format_version: 1; tracks: TimelineAudioTrack[] };
export type AudioEditInput = { base_version_id: string | null; action: "add" | "update" | "delete" | "split" | "duplicate" | "track";
  target_id: string; values: Record<string, unknown>; raw_command?: string };

export type CreationTaskMontage = {
  graphic_overlays?:TextOverlay[];
  aspect_ratio?: "9:16" | "16:9" | "1:1";
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

export type CreationTaskExactEditInput =
  | { operation_type: "ChangeCrop"; clip_id: string; fit_mode: "fill" | "fit"; crop_x: number; crop_y: number }
  | { operation_type: "ReplaceClip"; clip_id: string; asset_id: string; segment_id: string; source_start_seconds: number }
  | { operation_type: "DeleteClip" | "DuplicateClip"; clip_id: string }
  | { operation_type: "ChangeClipSpeed" | "ChangeLinkedSpeed"; clip_id: string; playback_rate: number }
  | { operation_type: "SplitClip"; clip_id: string; split_time_seconds: number }
  | { operation_type: "TrimClip"; clip_id: string; source_start_seconds: number }
  | { operation_type: "ReorderClip"; clip_id: string; position: number }
  | { operation_type: "SetClipLock"; clip_id: string; locked: boolean }
  | { operation_type: "ConfirmClip"; clip_id: string }
  | { operation_type: "RegenerateNarrationSegment"; narration_segment_id: string; delivery_instruction?: string }
  | { operation_type: "SplitSubtitleCue"; track_id: string; cue_id: string; split_offset: number; split_time_seconds: number }
  | { operation_type: "MergeSubtitleCue" | "DeleteSubtitleCue"; track_id: string; cue_id: string }
  | { operation_type: "EditGraphicOverlay"; overlay_action: "add"|"update"|"delete"; graphic_overlay?: Omit<TextOverlay,"id"> & {id?:string}; overlay_id?:string }
  | { operation_type: "SetClipTransition"; clip_id: string; transition_seconds: number }
  | { operation_type: "ChangeCanvas"; aspect_ratio: "9:16" | "16:9" | "1:1"; fit_mode?: "fill" | "fit" }
  | { operation_type: "ReplaceSubtitleText"; track_id: string; find_text: string; replacement_text: string }
  | { operation_type: "UpdateSubtitleCue"; track_id?: string; cue_id: string; text: string; start_seconds?: number; end_seconds?: number; line_breaks?: number[]; emphasis?: string[]; style?: CreationTaskSubtitleStyle }
  | { operation_type: "ConfigureSubtitleTrack"; track_id?: string; subtitle_display?: "none" | "zh" | "en" | "zh-en"; target_language?: "en-US"; style?: Partial<CreationTaskSubtitleStyle> }
  | { operation_type: "GenerateCoverFromFrame"; frame_time_seconds: number; title: string }
  | { operation_type: "ChangePersonSegment"; clip_id: string }
  | { operation_type: "RegeneratePersonRender"; clip_id: string };

type CreationTaskExactEditPayload = CreationTaskExactEditInput extends infer Input
  ? Input extends { operation_type: string }
    ? Omit<Input, "operation_type">
    : never
  : never;

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
    operation_type: CreationTaskExactEditInput["operation_type"] | "EditAudioTimeline" | "AdjustVisualEffects";
    payload: Record<string, unknown>;
  };
  invalidated_refs: string[];
  preview: {
    before: EditPreviewItem;
    after: EditPreviewItem;
    text_changes?: { cue_id: string; before: string; after: string; occurrences: number }[];
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

export type CreationTaskEditOperation = {
  id: string;
  workspace_id: string;
  task_id: string;
  proposal_id: string;
  operation_type: CreationTaskExactEditInput["operation_type"] | "EditAudioTimeline" | "AdjustVisualEffects";
  payload: CreationTaskExactEditPayload;
  base_version_id: string | null;
  result_version_id: string | null;
  status: "running" | "applied" | "failed";
  failure_reason: string | null;
  created_at: string;
  applied_at: string | null;
};

export type CreationTaskEditConfirmResponse = {
  operation: CreationTaskEditOperation;
  montage: CreationTaskMontage;
  narration?: CreationTaskNarration | null;
};

export type CreationPlanPreview = CreationPlanInput & {
  template_settings?: VideoTemplateSettings;
  name: string;
  knowledge_references: CreationPlanKnowledgeReference[];
  source_references: CreationPlanSourceReference[];
  selected_voice: CreationPlanVoiceReference | null;
  pronunciation_references: CreationPlanPronunciationReference[];
  approved_asset_count: number;
  approved_clip_count: number;
  material_rule_summary: string;
  material_matches: MaterialSegmentMatch[];
  model_snapshot: CreationPlanModelStatus[];
  confirmation_points: string[];
  blockers: CreationPlanBlocker[];
  confirmable: boolean;
  preflight_fingerprint: string;
};

export type CreationPlan = CreationPlanPreview & {
  id: string;
  workspace_id: string;
  status: "confirmed";
  creation_task: CreationTask;
  created_at: string;
  confirmed_at: string;
  updated_at: string;
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
    status: "ready" | "blocked" | "failed"; fingerprint: string; model_id: string;
    sample_times: number[]; summary: string;
    facts: { kind: "object" | "action" | "scene"; text: string; sample_times: number[] }[];
    unknowns: string[];
    ocr_status: "ready" | "blocked" | "failed"; ocr_model_id: string;
    ocr_frames: { id: string; timestamp_seconds: number; width: number; height: number;
      observations: { text: string; confidence: number; box: number[]; retrieval_eligible: boolean }[] }[];
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

export type MaterialAsset = {
  id: string;
  workspace_id: string;
  file_name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  duration_seconds: number | null;
  usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
  suggested_usage_role: "unassigned" | "a_roll" | "b_roll";
  usage_role_confidence: number;
  usage_role_reason: string;
  review_status: "raw" | "approved" | "rejected";
  rights_confirmed: boolean;
  brand_safe: boolean;
  analysis_status: "pending" | "ready" | "blocked" | "failed";
  analysis_message: string;
  speech_analysis?: {
    status: "ready" | "blocked" | "failed";
    source_fingerprint: string;
    model_id: string;
    observations: { start_ms: number; end_ms: number; text: string }[];
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
