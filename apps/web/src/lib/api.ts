import type {
  ContentTemplates,
  CreationPlan,
  CreationPlanInput,
  CreationPlanPreview,
  CreationTasksResponse,
  CreationTaskWorkspace,
  CreationTaskScriptsResponse,
  CreationTaskScriptSection,
  CreationTaskNarration,
  CreationTaskMontage,
  CreationTaskTopicsResponse,
  Episode,
  EditPlan,
  EditPlanSegmentDecisionAction,
  DraftImport,
  DraftPackage,
  DraftStatus,
  EpisodeHistory,
  EpisodeStatus,
  EpisodeVoiceSelection,
  FeedbackTargetType,
  FeedbackValue,
  FeedbackSummary,
  KnowledgeNote,
  LocalModelCatalog,
  ModelInstallJob,
  OnboardingCompletionResponse,
  PendingVideo,
  MaterialAsset,
  MaterialCollection,
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
  MontagePlan,
  MontageClipCandidate,
  Preferences,
  PreferenceCards,
  PreferenceEvidence,
  ProcessedVideo,
  ProductionChain,
  ProductionBatch,
  ProductionFoundation,
  ProductionProfile,
  PublishFeedback,
  RetakeReport,
  RuntimeHealth,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  ScriptPackage,
  SetupPreflightResponse,
  SetupStatus,
  SourcesStatus,
  SourceFileStates,
  SourceItems,
  SourceSyncRetryResult,
  SystemStatus,
  TopicEvidenceList,
  VideoProductionMode,
  VoiceProfile,
  VoiceProfiles,
  VoiceTranscribeResult,
  WorkflowStatus,
} from "./types";

/**
 * Browser requests use the relative Next.js `/api` route so middleware can
 * proxy them to the bundled Python server. Server Components and Server
 * Actions run in Node, where a relative URL is invalid; they use the same
 * runtime-selected API port directly instead.
 */
const serverApiBase = process.env.API_PORT
  ? `http://127.0.0.1:${process.env.API_PORT}`
  : (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000");

export const API_BASE =
  typeof window === "undefined"
    ? serverApiBase
    : (process.env.NEXT_PUBLIC_API_BASE ?? "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

type ServerAuthorizationProvider = () => Promise<string | null>;
let serverAuthorizationProvider: ServerAuthorizationProvider | null = null;

export function setServerAuthorizationProvider(
  provider: ServerAuthorizationProvider,
): void {
  serverAuthorizationProvider = provider;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const serverAuthorization = await getServerAuthorization();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
      ...(serverAuthorization ? { Authorization: serverAuthorization } : {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export class CreationPlanApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string | null,
    message: string,
    public readonly impact: string | null = null,
    public readonly repairAction: string | null = null,
    public readonly reason: string | null = null,
  ) {
    super(message);
    this.name = "CreationPlanApiError";
  }
}

const creationPlanErrorTitles: Record<string, string> = {
  creation_plan_preflight_stale: "预检结果已变化",
  creation_plan_confirmation_rejected: "无法确认创建创作任务",
  content_sources_missing: "缺少可用于选题的资料",
  capability_unavailable: "当前文字模型不可用",
  creation_task_topic_stage_conflict: "当前创作任务不能重复开始选题",
  creation_task_topic_confirmation_conflict: "当前选题不能被确认",
  confirmed_topic_missing: "请先确认选题",
  creation_task_script_stage_conflict: "当前任务不能生成文案",
  creation_task_script_confirmation_conflict: "当前文案不能被确认",
  creation_task_narration_conflict: "当前任务不能生成配音",
  voice_identity_missing: "缺少已授权的声音版本",
  voice_reference_wav_unavailable: "授权声音的 WAV 不可用",
  narration_generation_failed: "中文配音生成失败",
  creation_task_montage_conflict: "当前任务不能生成故事板",
  montage_plan_failed: "故事板生成失败",
  provider_output_invalid: "文字模型返回的选题无法使用",
};

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

async function creationPlanRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    const serverAuthorization = await getServerAuthorization();
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
        ...(serverAuthorization ? { Authorization: serverAuthorization } : {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new CreationPlanApiError(0, null, "本地服务未接通。");
  }

  const text = await response.text();
  const payload = text ? parseJson(text) : null;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail: unknown }).detail
      : payload;
    const details = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    const code = typeof details.error === "string" ? details.error : null;
    const reason = typeof details.reason === "string" ? details.reason : null;
    throw new CreationPlanApiError(
      response.status,
      code,
      code && creationPlanErrorTitles[code]
        ? creationPlanErrorTitles[code]
        : typeof detail === "string"
          ? detail
          : typeof details.detail === "string"
            ? details.detail
            : typeof payload === "string"
              ? payload
              : `请求失败：${response.status}`,
      typeof details.impact === "string" ? details.impact : null,
      typeof details.repair_action === "string" ? details.repair_action : null,
      reason,
    );
  }

  return payload as T;
}

async function getServerAuthorization(): Promise<string | null> {
  if (typeof window !== "undefined" || !serverAuthorizationProvider)
    return null;
  return serverAuthorizationProvider();
}

export function getTodayEpisode(): Promise<Episode> {
  return request<Episode>("/api/episodes/today");
}

export function getSystemStatus(): Promise<SystemStatus> {
  return request<SystemStatus>("/api/system/status");
}

export function getWorkflowStatus(): Promise<WorkflowStatus> {
  return request<WorkflowStatus>("/api/workflow/status");
}

export function getProductionFoundation(): Promise<ProductionFoundation> {
  return request<ProductionFoundation>("/api/production/foundation");
}

export function getProductionMaterials(input?: {
  limit?: number;
  offset?: number;
  query?: string;
  reviewStatus?: string;
}): Promise<MaterialLibrary> {
  const query = new URLSearchParams();
  if (input?.limit) query.set("limit", String(input.limit));
  if (input?.offset) query.set("offset", String(input.offset));
  if (input?.query) query.set("q", input.query);
  if (input?.reviewStatus && input.reviewStatus !== "all")
    query.set("review_status", input.reviewStatus);
  const suffix = query.size ? `?${query.toString()}` : "";
  return request<MaterialLibrary>(`/api/production/materials${suffix}`);
}

export function getMaterialLibraryInsights(): Promise<MaterialLibraryInsights> {
  return request<MaterialLibraryInsights>("/api/production/materials/insights");
}

export function getMaterialCollections(): Promise<MaterialCollections> {
  return request<MaterialCollections>("/api/production/material-collections");
}

export function createMaterialCollection(input: {
  name: string;
  parent_id?: string | null;
}): Promise<MaterialCollection> {
  return request<MaterialCollection>("/api/production/material-collections", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function scanProductionMaterials(): Promise<MaterialLibrary> {
  return request<MaterialLibrary>("/api/production/materials/scan", {
    method: "POST",
  });
}

export function analyzeProductionMaterial(
  assetId: string,
): Promise<MaterialAsset> {
  return request<MaterialAsset>(
    `/api/production/materials/${assetId}/analyze`,
    {
      method: "POST",
    },
  );
}

export function reviewProductionMaterial(
  assetId: string,
  input: {
    review_status: "raw" | "approved" | "rejected";
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
    rights_confirmed: boolean;
    brand_safe: boolean;
  },
): Promise<MaterialAsset> {
  return request<MaterialAsset>(`/api/production/materials/${assetId}/review`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function updateProductionMaterialSegment(
  assetId: string,
  segmentId: string,
  input: {
    description: string;
    transcript: string;
    review_status: "raw" | "approved" | "rejected";
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
    user_tags?: string[];
    collection_ids?: string[];
  },
): Promise<MaterialAsset> {
  return request<MaterialAsset>(
    `/api/production/materials/${assetId}/segments/${segmentId}`,
    {
      method: "PUT",
      body: JSON.stringify(input),
    },
  );
}

export function updateProductionProfile(
  input: Omit<
    ProductionProfile,
    | "workspace_id"
    | "primary_platform"
    | "aspect_ratio"
    | "voice_model_status"
    | "voice_model_message"
    | "updated_at"
  >,
): Promise<ProductionProfile> {
  return request<ProductionProfile>("/api/production/profile", {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function getVoiceProfiles(): Promise<VoiceProfiles> {
  return request<VoiceProfiles>("/api/production/voices");
}

export function createVoiceProfile(input: {
  name: string;
  reference_audio_path: string;
  reference_text: string;
  consent_confirmed: boolean;
  is_default: boolean;
}): Promise<VoiceProfile> {
  return request<VoiceProfile>("/api/production/voices", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateVoiceProfile(
  voiceProfileId: string,
  input: {
    name: string;
    reference_audio_path: string;
    reference_text: string;
    consent_confirmed: boolean;
    is_default: boolean;
  },
): Promise<VoiceProfile> {
  return request<VoiceProfile>(`/api/production/voices/${voiceProfileId}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function selectEpisodeVoice(
  episodeId: string,
  input: { voice_profile_id: string | null; voice_version: string | null },
): Promise<EpisodeVoiceSelection> {
  return request<EpisodeVoiceSelection>(
    `/api/production/episodes/${episodeId}/voice`,
    {
      method: "PUT",
      body: JSON.stringify(input),
    },
  );
}

export function previewCreationPlan(input: CreationPlanInput): Promise<CreationPlanPreview> {
  return creationPlanRequest<CreationPlanPreview>("/api/creation-plans/preview", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function confirmCreationPlan(
  input: CreationPlanInput & { preflight_fingerprint: string },
): Promise<CreationPlan> {
  return creationPlanRequest<CreationPlan>("/api/creation-plans/confirm", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getCreationPlan(planId: string): Promise<CreationPlan> {
  return creationPlanRequest<CreationPlan>(`/api/creation-plans/${encodeURIComponent(planId)}`);
}

export function listCreationTasks(): Promise<CreationTasksResponse> {
  return creationPlanRequest<CreationTasksResponse>("/api/creation-tasks");
}

export function getCreationTaskTopics(taskId: string): Promise<CreationTaskTopicsResponse> {
  return creationPlanRequest<CreationTaskTopicsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/topics`,
  );
}

export function generateCreationTaskTopics(taskId: string): Promise<CreationTaskTopicsResponse> {
  return creationPlanRequest<CreationTaskTopicsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/topics/generate`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function confirmCreationTaskTopic(
  taskId: string,
  topicId: string,
): Promise<CreationTaskTopicsResponse> {
  return creationPlanRequest<CreationTaskTopicsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/topics/${encodeURIComponent(topicId)}/confirm`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function getCreationTaskWorkspace(taskId: string): Promise<CreationTaskWorkspace> {
  return creationPlanRequest<CreationTaskWorkspace>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}`,
  );
}

export function getCreationTaskScripts(taskId: string): Promise<CreationTaskScriptsResponse> {
  return creationPlanRequest<CreationTaskScriptsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/scripts`,
  );
}

export function generateCreationTaskScript(taskId: string): Promise<CreationTaskScriptsResponse> {
  return creationPlanRequest<CreationTaskScriptsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/generate`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function confirmCreationTaskScript(
  taskId: string,
  scriptId: string,
  input: { title: string; sections: CreationTaskScriptSection[] },
): Promise<CreationTaskScriptsResponse> {
  return creationPlanRequest<CreationTaskScriptsResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/scripts/${encodeURIComponent(scriptId)}/confirm`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function generateCreationTaskNarration(taskId: string): Promise<CreationTaskNarration> {
  return creationPlanRequest<CreationTaskNarration>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/narration/generate`,
    { method: "POST" },
  );
}

export function createCreationTaskMontagePlan(taskId: string): Promise<CreationTaskMontage> {
  return creationPlanRequest<CreationTaskMontage>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/plan`,
    { method: "POST" },
  );
}

export function updateCreationTaskMontageClip(
  taskId: string,
  clipId: string,
  input: {
    asset_id?: string;
    segment_id?: string;
    source_start_seconds?: number;
    position?: number;
    locked?: boolean;
    confirmed?: boolean;
  },
): Promise<CreationTaskMontage> {
  return creationPlanRequest<CreationTaskMontage>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/clips/${encodeURIComponent(clipId)}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

export function getCreationTaskMontageClipCandidates(
  taskId: string,
  clipId: string,
): Promise<MontageClipCandidate[]> {
  return creationPlanRequest<MontageClipCandidate[]>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/clips/${encodeURIComponent(clipId)}/candidates`,
  );
}

export function renderCreationTaskMontage(taskId: string): Promise<CreationTaskMontage> {
  return creationPlanRequest<CreationTaskMontage>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/render`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function runProductionBatch(batchId: string): Promise<ProductionBatch> {
  return request<ProductionBatch>(`/api/production/batches/${batchId}/run`, {
    method: "POST",
  });
}

export function getProductionBatch(batchId: string): Promise<ProductionBatch> {
  return request<ProductionBatch>(`/api/production/batches/${batchId}`);
}

export function getMontagePlan(planId: string): Promise<MontagePlan> {
  return request<MontagePlan>(`/api/production/montage-plans/${planId}`);
}

export function getMontagePlans(
  batchId: string,
): Promise<{ plans: MontagePlan[]; total_count: number }> {
  return request<{ plans: MontagePlan[]; total_count: number }>(
    `/api/production/batches/${batchId}/montage-plans`,
  );
}

export function getMontageClipCandidates(
  planId: string,
  clipId: string,
): Promise<MontageClipCandidate[]> {
  return request<MontageClipCandidate[]>(
    `/api/production/montage-plans/${planId}/clips/${clipId}/candidates`,
  );
}

export function updateMontageClip(
  planId: string,
  clipId: string,
  input: {
    asset_id?: string;
    segment_id?: string;
    source_start_seconds?: number;
    locked?: boolean;
    confirmed?: boolean;
  },
): Promise<MontagePlan> {
  return request<MontagePlan>(
    `/api/production/montage-plans/${planId}/clips/${clipId}`,
    { method: "PATCH", body: JSON.stringify(input) },
  );
}

export function reorderMontageClips(
  planId: string,
  clipIds: string[],
): Promise<MontagePlan> {
  return request<MontagePlan>(
    `/api/production/montage-plans/${planId}/clips/order`,
    {
      method: "PUT",
      body: JSON.stringify({ clip_ids: clipIds }),
    },
  );
}

export function createMontagePlan(
  batchId: string,
  input: { episode_id: string },
): Promise<MontagePlan> {
  return request<MontagePlan>(
    `/api/production/batches/${batchId}/montage-plans`,
    {
      method: "POST",
      body: JSON.stringify(input),
    },
  );
}

export function confirmMontageClips(
  planId: string,
  clipIds: string[],
): Promise<MontagePlan> {
  return request<MontagePlan>(
    `/api/production/montage-plans/${planId}/confirm`,
    {
      method: "POST",
      body: JSON.stringify({ clip_ids: clipIds }),
    },
  );
}

export function renderMontagePlan(
  planId: string,
): Promise<MontagePlan> {
  return request<MontagePlan>(
    `/api/production/montage-plans/${planId}/render`,
    {
      method: "POST",
      body: JSON.stringify({}),
    },
  );
}

export function getRuntimeSettings(): Promise<RuntimeSettings> {
  return request<RuntimeSettings>("/api/settings/runtime");
}

export function getRuntimeHealth(): Promise<RuntimeHealth> {
  return request<RuntimeHealth>("/api/runtime/health");
}

export function getSetupStatus(): Promise<SetupStatus> {
  return request<SetupStatus>("/api/is_setup");
}

export function getSetupPreflight(): Promise<SetupPreflightResponse> {
  return request<SetupPreflightResponse>("/api/setup/preflight");
}

export function completeOnboarding(): Promise<OnboardingCompletionResponse> {
  return request<OnboardingCompletionResponse>("/api/onboarding/complete", {
    method: "POST",
  });
}

export function updateRuntimeSettings(
  payload: RuntimeSettingsUpdate,
): Promise<RuntimeSettings> {
  return request<RuntimeSettings>("/api/settings/runtime", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getLocalModels(): Promise<LocalModelCatalog> {
  return request<LocalModelCatalog>("/api/models");
}

export function installLocalModel(modelId: string): Promise<ModelInstallJob> {
  return request<ModelInstallJob>(
    `/api/models/${encodeURIComponent(modelId)}/install`,
    {
      method: "POST",
      body: JSON.stringify({ license_accepted: true }),
    },
  );
}

export function getModelInstallJob(jobId: string): Promise<ModelInstallJob> {
  return request<ModelInstallJob>(
    `/api/model-install-jobs/${encodeURIComponent(jobId)}`,
  );
}

export function getKnowledgeNotes(query?: string): Promise<KnowledgeNote[]> {
  const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
  return request<KnowledgeNote[]>(`/api/knowledge/notes${suffix}`);
}

export function getContentTemplates(): Promise<ContentTemplates> {
  return request<ContentTemplates>("/api/content-templates");
}

export function selectEpisodeContentTemplate(
  episodeId: string,
  templateId: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/content-template`, {
    method: "POST",
    body: JSON.stringify({ template_id: templateId }),
  });
}

export function getSourceItems(): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/items");
}

export function getSourcesStatus(): Promise<SourcesStatus> {
  return request<SourcesStatus>("/api/sources/sync-status");
}

export function syncSourceItems(): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/sync", { method: "POST" });
}

export function retrySourceSync(
  sourceKey: string,
): Promise<SourceSyncRetryResult> {
  return request<SourceSyncRetryResult>(
    `/api/sources/${encodeURIComponent(sourceKey)}/retry`,
    {
      method: "POST",
    },
  );
}

export function ingestManualLink(url: string): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/links", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function getTopicEvidence(
  episodeId: string,
): Promise<TopicEvidenceList> {
  return request<TopicEvidenceList>(
    `/api/episodes/${episodeId}/topic-evidence`,
  );
}

export function getFeedbackSummary(): Promise<FeedbackSummary> {
  return request<FeedbackSummary>("/api/feedback/summary");
}

export function getPreferences(): Promise<Preferences> {
  return request<Preferences>("/api/preferences");
}

export function getPreferenceCards(): Promise<PreferenceCards> {
  return request<PreferenceCards>("/api/preferences/cards");
}

export function getPreferenceEvidence(
  preferenceId: string,
): Promise<PreferenceEvidence> {
  return request<PreferenceEvidence>(
    `/api/preferences/cards/${encodeURIComponent(preferenceId)}/evidence`,
  );
}

export function disablePreferenceCard(
  preferenceId: string,
): Promise<Preferences> {
  return request<Preferences>("/api/preferences/disable", {
    method: "PATCH",
    body: JSON.stringify({ signal: preferenceId }),
  });
}

export function restorePreferenceCard(
  preferenceId: string,
): Promise<Preferences> {
  return request<Preferences>("/api/preferences/restore", {
    method: "PATCH",
    body: JSON.stringify({ signal: preferenceId }),
  });
}

export function getDraftStatus(): Promise<DraftStatus> {
  return request<DraftStatus>("/api/drafts/status");
}

export function createDraftPackage(
  videoFileName?: string,
): Promise<DraftPackage> {
  return request<DraftPackage>("/api/drafts/package", {
    method: "POST",
    body: JSON.stringify({ video_file_name: videoFileName ?? null }),
  });
}

export function importDraftPackage(packageName?: string): Promise<DraftImport> {
  return request<DraftImport>("/api/drafts/import", {
    method: "POST",
    body: JSON.stringify({ package_name: packageName ?? null }),
  });
}

export function getPendingVideos(): Promise<PendingVideo[]> {
  return request<PendingVideo[]>("/api/videos/pending");
}

export function getProductionChain(
  episodeId: string,
): Promise<ProductionChain> {
  return request<ProductionChain>(
    `/api/episodes/${episodeId}/production-chain`,
  );
}

export function getSourceFileStates(
  episodeId: string,
): Promise<SourceFileStates> {
  return request<SourceFileStates>(
    `/api/episodes/${episodeId}/source-file-states`,
  );
}

export function archiveSources(
  episodeId: string,
  fileNames: string[],
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/archive-sources`, {
    method: "POST",
    body: JSON.stringify({ file_names: fileNames }),
  });
}

export function getEpisodeRetakeReport(
  episodeId: string,
): Promise<RetakeReport> {
  return request<RetakeReport>(`/api/episodes/${episodeId}/retake-report`);
}

export function getEpisodeEditPlan(episodeId: string): Promise<EditPlan> {
  return request<EditPlan>(`/api/episodes/${episodeId}/edit-plan`);
}

export function updateEditPlanSegmentDecision(
  episodeId: string,
  segmentId: string,
  action: EditPlanSegmentDecisionAction,
  reason?: string,
): Promise<EditPlan> {
  return request<EditPlan>(
    `/api/episodes/${episodeId}/edit-plan/segments/${segmentId}/decision`,
    {
      method: "POST",
      body: JSON.stringify({ action, reason: reason ?? null }),
    },
  );
}

export function revertEditPlanSegmentDecision(
  episodeId: string,
  segmentId: string,
): Promise<EditPlan> {
  return request<EditPlan>(
    `/api/episodes/${episodeId}/edit-plan/segments/${segmentId}/revert`,
    { method: "POST" },
  );
}

export function getLatestRetakeReport(): Promise<RetakeReport> {
  return request<RetakeReport>("/api/retake-reports/latest");
}

export function archiveEpisodeVideoSources(
  episodeId: string,
  fileNames: string[],
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/archive-sources`, {
    method: "POST",
    body: JSON.stringify({ file_names: fileNames }),
  });
}

export function setEpisodeProductionMode(
  episodeId: string,
  mode: VideoProductionMode,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/production-mode`, {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export function processVideo(fileName: string): Promise<ProcessedVideo> {
  return request<ProcessedVideo>("/api/videos/process", {
    method: "POST",
    body: JSON.stringify({ file_name: fileName }),
  });
}

export function approveScript(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/approve-script`, {
    method: "POST",
  });
}

export function uploadEpisodeVideo(
  episodeId: string,
  fileName: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/upload-video`, {
    method: "POST",
    body: JSON.stringify({ file_name: fileName }),
  });
}

export function processEpisodeVideo(
  episodeId: string,
  fileName?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/process-video`, {
    method: "POST",
    body: JSON.stringify({ file_name: fileName ?? null }),
  });
}

export function processAdHocEpisodeVideo(
  episodeId: string,
  fileName: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/process-ad-hoc-video`, {
    method: "POST",
    body: JSON.stringify({ file_name: fileName }),
  });
}

export function processAdHocEpisodeVideoBatch(
  episodeId: string,
  fileNames: string[],
): Promise<Episode> {
  return request<Episode>(
    `/api/episodes/${episodeId}/process-ad-hoc-video-batch`,
    {
      method: "POST",
      body: JSON.stringify({ file_names: fileNames }),
    },
  );
}

export function createEpisodeDraft(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/create-draft`, {
    method: "POST",
  });
}

export function markEpisodePublished(
  episodeId: string,
  payload: {
    publish_url?: string | null;
    platform?: string | null;
    play_count?: number | null;
    like_count?: number | null;
    comment_count?: number | null;
    notes?: string | null;
    published_at?: string | null;
  },
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/mark-published`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updatePublishMetrics(
  episodeId: string,
  payload: {
    publish_url?: string | null;
    platform?: string | null;
    play_count?: number | null;
    like_count?: number | null;
    comment_count?: number | null;
    notes?: string | null;
  },
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/publish-metrics`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getEpisodeHistory(
  status?: EpisodeStatus,
): Promise<EpisodeHistory> {
  const params = new URLSearchParams();
  if (status) {
    params.set("status", status);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return request<EpisodeHistory>(`/api/episodes/history${suffix}`);
}

// TODO(backend): dedicated publish-feedback endpoint is not implemented.
// The Episode response already carries the latest publish fields, so
// callers can read them from the Episode directly.
export function getPublishFeedback(
  episodeId: string,
): Promise<PublishFeedback> {
  return Promise.reject(
    new Error(`publish-feedback 端点暂未实现（${episodeId}）`),
  );
}

export function createEpisode(): Promise<Episode> {
  return request<Episode>("/api/episodes", { method: "POST" });
}

export function generateTopics(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/generate-topics`, {
    method: "POST",
  });
}

export function regenerateTopics(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/regenerate-topics`, {
    method: "POST",
  });
}

export function approveTopic(
  episodeId: string,
  topicId: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/approve-topic`, {
    method: "POST",
    body: JSON.stringify({ topic_id: topicId }),
  });
}

export function generateScript(episodeId: string): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/generate-script`, {
    method: "POST",
  });
}

export function updateEpisodeScript(
  episodeId: string,
  script: ScriptPackage,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script`, {
    method: "POST",
    body: JSON.stringify({ script, reason: reason ?? null }),
  });
}

export function regenerateEpisodeScript(
  episodeId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script/regenerate`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export function regenerateEpisodeScriptSection(
  episodeId: string,
  sectionId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(
    `/api/episodes/${episodeId}/script/sections/${sectionId}/regenerate`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    },
  );
}

export function revertEpisodeScriptVersion(
  episodeId: string,
  versionId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/script/revert`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId, reason: reason ?? null }),
  });
}

export function sendFeedback(
  episodeId: string,
  targetType: FeedbackTargetType,
  targetId: string,
  value: FeedbackValue,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      target_type: targetType,
      target_id: targetId,
      value,
      reason,
    }),
  });
}

export function revertFeedbackEvent(
  episodeId: string,
  feedbackEventId: string,
  reason?: string,
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/feedback/revert`, {
    method: "POST",
    body: JSON.stringify({
      feedback_event_id: feedbackEventId,
      reason: reason ?? null,
    }),
  });
}

export async function transcribeVoice(
  audioBlob: Blob,
  options: { episodeId?: string; filename?: string } = {},
): Promise<VoiceTranscribeResult> {
  if (!(audioBlob instanceof Blob)) {
    throw new Error("transcribeVoice: 需要传入 Blob 音频数据");
  }
  const formData = new FormData();
  const filename =
    options.filename ??
    (audioBlob.type.includes("webm")
      ? "voice-command.webm"
      : audioBlob.type.includes("ogg")
        ? "voice-command.ogg"
        : audioBlob.type.includes("mp4")
          ? "voice-command.m4a"
          : "voice-command.bin");
  formData.append("audio", audioBlob, filename);
  if (options.episodeId) {
    formData.append("episode_id", options.episodeId);
  }

  const response = await fetch(`${API_BASE}/api/voice/transcribe`, {
    method: "POST",
    body: formData,
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  const payload = (await response.json()) as Partial<VoiceTranscribeResult>;
  return {
    transcript:
      typeof payload.transcript === "string" ? payload.transcript : "",
    confidence:
      typeof payload.confidence === "number" &&
      Number.isFinite(payload.confidence)
        ? payload.confidence
        : 0,
    segments: Array.isArray(payload.segments)
      ? payload.segments
          .map((segment) => {
            if (!segment || typeof segment !== "object") {
              return null;
            }
            const s = segment as {
              start?: unknown;
              end?: unknown;
              text?: unknown;
            };
            return {
              start: typeof s.start === "number" ? s.start : 0,
              end: typeof s.end === "number" ? s.end : 0,
              text: typeof s.text === "string" ? s.text : "",
            };
          })
          .filter(
            (segment): segment is NonNullable<typeof segment> =>
              segment !== null,
          )
      : [],
  };
}
