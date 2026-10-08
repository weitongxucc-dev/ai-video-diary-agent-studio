import type { HomeAggregation, ScriptLibraryEntry, ScriptLibrarySource, KnowledgeGrant, KnowledgeGrantState } from "./types";
import type { VideoTemplate, AudioEditInput, CreationTaskRecording } from "./types";
import { ensureLocalMemberIdentity, localMemberTokenHeader, readLocalMemberToken } from "./local-member-auth";
import { isLocalAuthMode } from "./auth/routing";
import type { KnowledgeItemVersion } from "./source-api";
import type {
  ContentTemplates,
  CreationPlan,
  CreationPlanInput,
  CreationPlanPreview,
  WorkspaceAssistantPlanTurnResponse,
  CreationTasksResponse,
  CreationTaskWorkspace,
  CreationExecutionPreview,
  CreationExecutionStatus,
  TaskJobProgress,
  BusinessObjectKind,
  MaterialBusinessObject,
  MaterialBusinessObjects,
  MaterialUsageRecords,
  MaterialIntakeStatus,
  MaterialCopyCompleteInput,
  CreationTaskScriptsResponse,
  CreationTaskScript,
  CreationTaskScriptSection,
  CreationTaskNarration,
  CreationTaskPersonRender,
  CreationTaskMontage,
  CreationTaskExactEditInput,
  CreationTaskEditConfirmResponse,
  CreationTaskEditResult,
  CreationTaskEditPreviewResponse,
  CreationTaskAssistantTurnResponse,
  CreationTaskAssistantHistory,
  CreationTaskTopicsResponse,
  CreationTaskTimelineVersionDetail,
  CreationTaskTimelineVersionList,
  FeedbackSummary,
  KnowledgeNote,
  LocalModelCatalog,
  ModelInstallJob,
  OnboardingCompletionResponse,
  ContentVisibility,
  MaterialAsset,
  MaterialCollection,
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
  MontageClipCandidate,
  Preferences,
  PreferenceCards,
  PreferenceEvidence,
  ProductionFoundation,
  ProductionProfile,
  RuntimeHealth,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  SetupPreflightResponse,
  SetupStatus,
  SourcesStatus,
  SourceItems,
  SourceSyncRetryResult,
  SystemStatus,
  VoiceProfile,
  VoiceProfiles,
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
  return path;
}

export function getKnowledgeGrants(itemId: string): Promise<KnowledgeGrantState> {
  return request<KnowledgeGrantState>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants`);
}

export function getKnowledgeReferenceVersion(itemId: string, version: string): Promise<KnowledgeItemVersion> {
  return request<KnowledgeItemVersion>(`/api/knowledge-items/${encodeURIComponent(itemId)}/versions/${encodeURIComponent(version)}`);
}

export function grantKnowledgeToWorkspace(itemId: string, targetWorkspaceId: string): Promise<KnowledgeGrant> {
  return request<KnowledgeGrant>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants`, {
    method: "POST", body: JSON.stringify({ target_workspace_id: targetWorkspaceId }),
  });
}

export function revokeKnowledgeGrant(itemId: string, grantId: string): Promise<KnowledgeGrant> {
  return request<KnowledgeGrant>(`/api/knowledge-items/${encodeURIComponent(itemId)}/grants/${encodeURIComponent(grantId)}`, {
    method: "DELETE",
  });
}

type ServerAuthorizationProvider = () => Promise<string | null>;
let serverAuthorizationProvider: ServerAuthorizationProvider | null = null;

export function setServerAuthorizationProvider(
  provider: ServerAuthorizationProvider,
): void {
  serverAuthorizationProvider = provider;
}

export class ApiRequestError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const serverAuthorization = await getServerAuthorization();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
      ...(serverAuthorization ? { Authorization: serverAuthorization } : {}),
      // 无令牌时 localMemberTokenHeader() 返回 {}，保持 header 缺席 = 后端方案 A 匿名放行。
      ...localMemberTokenHeader(),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new ApiRequestError(response.status, message || `Request failed: ${response.status}`);
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
        // 无令牌时不注入，缺席即匿名放行（方案 A）。
        ...localMemberTokenHeader(),
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
  // 精确标签筛选，由服务端在分页前生效
  tag?: string;
  // 媒体类型筛选，省略或 all 与不带这个参数等价，表示不限类型
  mediaType?: string;
}): Promise<MaterialLibrary> {
  const query = new URLSearchParams();
  if (input?.limit) query.set("limit", String(input.limit));
  if (input?.offset) query.set("offset", String(input.offset));
  if (input?.query) query.set("q", input.query);
  if (input?.reviewStatus && input.reviewStatus !== "all")
    query.set("review_status", input.reviewStatus);
  if (input?.tag) query.set("tag", input.tag);
  if (input?.mediaType && input.mediaType !== "all")
    query.set("media_type", input.mediaType);
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

export function getMaterialIntakeStatus(): Promise<MaterialIntakeStatus> {
  return request("/api/production/materials/intake");
}

export function confirmMaterialCopyComplete(input: MaterialCopyCompleteInput): Promise<{ confirmed: true; intake: MaterialIntakeStatus }> {
  return request("/api/production/materials/intake/copy-complete", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function bindMaterialIntake(expectedDirectory: string): Promise<MaterialIntakeStatus> {
  return request("/api/production/materials/intake/bind", {
    method: "POST", body: JSON.stringify({ expected_directory: expectedDirectory }),
  });
}

export function scanMaterialIntake(): Promise<MaterialLibrary> {
  return request("/api/production/materials/intake/scan", { method: "POST" });
}

export function retryMaterialIntake(assetId: string): Promise<MaterialAsset> {
  return request(`/api/production/materials/${encodeURIComponent(assetId)}/intake/retry`, { method: "POST" });
}

export function updateMaterialAssetTags(assetId: string, userTags: string[]): Promise<MaterialAsset> {
  return request(`/api/production/materials/${encodeURIComponent(assetId)}/tags`, {
    method: "PUT", body: JSON.stringify({ user_tags: userTags }),
  });
}

export function updateMaterialAssetTagsBatch(assetIds: string[], add: string[], remove: string[]): Promise<{ assets: MaterialAsset[] }> {
  return request("/api/production/materials/tags/batch", {
    method: "POST", body: JSON.stringify({ asset_ids: assetIds, add, remove }),
  });
}

/** Save each named material's own text and the batch background it arrived with. */
export type MaterialAssetNote = {
  asset_id: string;
  user_description: string;
  upload_context: string;
};

export function updateMaterialNotes(items: MaterialAssetNote[]): Promise<{ assets: MaterialAsset[] }> {
  return request("/api/production/materials/notes", {
    method: "PUT", body: JSON.stringify({ items }),
  });
}

export async function transcribeMaterialVoiceNote(audio: Blob, signal?: AbortSignal): Promise<string> {
  if (!audio.size) throw new Error("没有录到声音，请重录或直接输入说明。");
  const mediaType = audio.type.split(";")[0].trim().toLowerCase();
  const suffix = ({ "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav" } as Record<string, string>)[mediaType];
  if (!suffix) throw new Error("当前录音格式无法转写，请直接输入说明或换支持录音的浏览器。");
  const body = new FormData();
  body.append("audio", audio, `素材口述录音.${suffix}`);
  body.append("local_only", "true");
  const response = await fetch(apiUrl("/api/voice/transcribe"), {
    method: "POST", body, signal, cache: "no-store", headers: { ...localMemberTokenHeader() },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "转写失败，请重试或直接输入说明。");
  if (typeof payload.transcript !== "string" || !payload.transcript.trim()) {
    throw new Error("录音未转成可用文字，请重试或直接输入说明。");
  }
  return payload.transcript.trim();
}

/** 当前工作区已有的产品/菜品/人物名称条目，供用户选择关联。 */
export function getMaterialBusinessObjects(): Promise<MaterialBusinessObjects> {
  return request<MaterialBusinessObjects>("/api/production/business-objects");
}

/** 新增一条名称条目。不会自动关联到任何素材，仍需用户明确选择保存。 */
export function createMaterialBusinessObject(input: {
  kind: BusinessObjectKind;
  name: string;
}): Promise<MaterialBusinessObject> {
  return request<MaterialBusinessObject>("/api/production/business-objects", {
    method: "POST", body: JSON.stringify(input),
  });
}

/** 这条素材当前已保存的名称条目。 */
export function getAssetBusinessObjects(assetId: string): Promise<MaterialBusinessObjects> {
  return request<MaterialBusinessObjects>(
    `/api/production/materials/${encodeURIComponent(assetId)}/business-objects`,
  );
}

/**
 * 把这条素材的名称关联换成用户选中的那几条。只写这一个素材的关联，
 * 不改它的说明、公共背景、片段描述或人工纠正；空数组清空关联。
 */
export function saveAssetBusinessObjects(
  assetId: string,
  businessObjectIds: string[],
): Promise<MaterialBusinessObjects> {
  return request<MaterialBusinessObjects>(
    `/api/production/materials/${encodeURIComponent(assetId)}/business-objects`,
    { method: "PUT", body: JSON.stringify({ business_object_ids: businessObjectIds }) },
  );
}

/**
 * 这一个片段被哪条成片用过。只读既有渲染写入的记录，不改任何计数。
 * 返回的 timeline_version_id 是任务当前版本，不代表当次使用时的版本。
 */
export function getAssetSegmentUsageRecords(
  assetId: string,
  segmentId: string,
): Promise<MaterialUsageRecords> {
  return request<MaterialUsageRecords>(
    `/api/production/materials/${encodeURIComponent(assetId)}/usage-records` +
      `?segment_id=${encodeURIComponent(segmentId)}`,
  );
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
  },
): Promise<MaterialAsset> {
  return request<MaterialAsset>(`/api/production/materials/${assetId}/review`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function setMaterialVisibility(
  assetId: string,
  visibility: ContentVisibility,
): Promise<MaterialAsset> {
  return request<MaterialAsset>(
    `/api/production/materials/${encodeURIComponent(assetId)}/visibility`,
    { method: "PUT", body: JSON.stringify({ visibility }) },
  );
}

export function setCreationTaskVisibility(
  taskId: string,
  visibility: ContentVisibility,
): Promise<CreationTaskWorkspace> {
  return request<CreationTaskWorkspace>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/visibility`,
    { method: "PUT", body: JSON.stringify({ visibility }) },
  );
}

export function setCreationPlanVisibility(
  planId: string,
  visibility: ContentVisibility,
): Promise<CreationPlan> {
  return request<CreationPlan>(
    `/api/creation-plans/${encodeURIComponent(planId)}/visibility`,
    { method: "PUT", body: JSON.stringify({ visibility }) },
  );
}

export function setVideoTemplateVisibility(
  templateId: string,
  visibility: ContentVisibility,
): Promise<{ item: VideoTemplate }> {
  return request<{ item: VideoTemplate }>(
    `/api/video-templates/${encodeURIComponent(templateId)}/visibility`,
    { method: "PUT", body: JSON.stringify({ visibility }) },
  );
}

export function updateMaterialUseState(assetId: string, useState: MaterialAsset["use_state"]): Promise<MaterialAsset> {
  return request<MaterialAsset>(`/api/production/materials/${assetId}/use-state`, {
    method: "PUT", body: JSON.stringify({ use_state: useState }),
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

export function previewCreationPlan(input: CreationPlanInput): Promise<CreationPlanPreview> {
  return creationPlanRequest<CreationPlanPreview>("/api/creation-plans/preview", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function sendWorkspaceAssistantPlanTurn(message: string, conversationId: string): Promise<WorkspaceAssistantPlanTurnResponse> {
  return creationPlanRequest<WorkspaceAssistantPlanTurnResponse>(
    "/api/assistant/plan/turn",
    { method: "POST", body: JSON.stringify({ message, conversation_id: conversationId }) },
  );
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

/**
 * 首页整机概览。只带当前身份的成员令牌（无令牌即匿名，服务端按方案 A 放行）。
 * 不在服务端预取 owner 私有任务再由前端隐藏。
 */
export function getHomeDashboard(days: 7 | 30): Promise<HomeAggregation> {
  return creationPlanRequest<HomeAggregation>(`/api/production/home?days=${days}`);
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

export function listCreationTaskTimelineVersions(taskId: string): Promise<CreationTaskTimelineVersionList> {
  return creationPlanRequest<CreationTaskTimelineVersionList>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/versions`,
  );
}

export function getCreationTaskTimelineVersion(
  taskId: string,
  versionId: string,
): Promise<CreationTaskTimelineVersionDetail> {
  return creationPlanRequest<CreationTaskTimelineVersionDetail>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/versions/${encodeURIComponent(versionId)}`,
  );
}

export function previewCreationExecution(taskId: string): Promise<CreationExecutionPreview> {
  return creationPlanRequest<CreationExecutionPreview>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/preview`,
  );
}

export function getCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
  return creationPlanRequest<CreationExecutionStatus>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution`,
  );
}

/** 任务作业进度：只读后台已落库的作业；重开应用读到同一批记录。 */
export function getTaskJobProgress(taskId: string): Promise<TaskJobProgress> {
  return request<TaskJobProgress>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/job-runs`,
  );
}

export function startCreationExecution(taskId: string, preview: CreationExecutionPreview): Promise<CreationExecutionStatus> {
  return creationPlanRequest<CreationExecutionStatus>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/start`,
    { method: "POST", body: JSON.stringify({
      script_id: preview.script_id,
      input_digest: preview.input_digest,
      requirements_confirmed: true,
    }) },
  );
}

export function resumeCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
  return creationPlanRequest<CreationExecutionStatus>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/resume`,
    { method: "POST" },
  );
}

export function cancelCreationExecution(taskId: string): Promise<CreationExecutionStatus> {
  return creationPlanRequest<CreationExecutionStatus>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/execution/cancel`,
    { method: "POST" },
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

export function selectCreationTaskNarrationVoice(
  taskId: string,
  input: { voice_profile_id: string; voice_version: string },
): Promise<CreationTaskWorkspace> {
  return creationPlanRequest<CreationTaskWorkspace>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/narration/voice`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function generateCreationTaskPersonRender(
  taskId: string,
  input: {
    narration_segment_id: string;
    person_profile_id: string;
    person_version: string;
    motion: "natural";
    expression: "neutral";
  },
): Promise<CreationTaskPersonRender> {
  return creationPlanRequest<CreationTaskPersonRender>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/person-renders/generate`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function createCreationTaskMontagePlan(taskId: string): Promise<CreationTaskMontage> {
  return creationPlanRequest<CreationTaskMontage>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/montage/plan`,
    { method: "POST" },
  );
}

export function applyCreationTaskExactEdit(
  taskId: string,
  input: CreationTaskExactEditInput,
): Promise<CreationTaskEditResult> {
  return creationPlanRequest<CreationTaskEditResult>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/edits/exact`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function previewCreationTaskExactEdit(
  taskId: string,
  input: CreationTaskExactEditInput,
): Promise<CreationTaskEditPreviewResponse> {
  return creationPlanRequest<CreationTaskEditPreviewResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/edits/exact/preview?draft=true`,
    { method: "POST", body: JSON.stringify(input) },
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

export async function getCreationTaskQualityCheck(taskId: string): Promise<import("./types").CreationTaskQualityCheck> {
  // Quality reads require a trusted identity, unlike anonymous task reads.
  // In local mode, AppShell's first-browser identity initialization may still be pending.
  if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken()) await ensureLocalMemberIdentity();
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/quality-check`);
}

export function checkCreationTaskQuality(taskId: string): Promise<import("./types").CreationTaskQualityCheck> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/quality-check`,
    { method: "POST", body: JSON.stringify({}) });
}

export function previewCreationTaskEdit(
  taskId: string,
  input: {
    raw_command: string;
    context_stage: string;
    selected_object_refs: string[];
    playhead_ms: number | null;
  },
): Promise<CreationTaskEditPreviewResponse> {
  return creationPlanRequest<CreationTaskEditPreviewResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/edits/preview?draft=true`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function sendCreationTaskAssistantTurn(
  taskId: string,
  input: {
    message: string;
    context_stage: string;
    selected_object_refs: string[];
    playhead_ms: number | null;
  },
): Promise<CreationTaskAssistantTurnResponse> {
  return creationPlanRequest<CreationTaskAssistantTurnResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/turn`,
    { method: "POST", body: JSON.stringify(input) },
  );
}

export function getCreationTaskAssistantHistory(taskId: string): Promise<CreationTaskAssistantHistory> {
  return creationPlanRequest<CreationTaskAssistantHistory>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/history`,
  );
}

export function previewCompoundSelection(taskId:string,proposalId:string,selection:number[]|string):Promise<CreationTaskEditPreviewResponse>{
  return creationPlanRequest<CreationTaskEditPreviewResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/edits/${encodeURIComponent(proposalId)}/selection/preview?draft=true`,
    {method:"POST",body:JSON.stringify(typeof selection==="string"?{raw_command:selection}:{included_step_indices:selection})});
}

export function confirmCreationTaskEdit(
  taskId: string,
  proposalId: string,
): Promise<CreationTaskEditResult> {
  return creationPlanRequest<CreationTaskEditResult>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/edits/${encodeURIComponent(proposalId)}/confirm`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function undoCreationTaskTimeline(
  taskId: string,
): Promise<CreationTaskEditConfirmResponse> {
  return creationPlanRequest<CreationTaskEditConfirmResponse>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/undo`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function initializeCreationTaskTimeline(taskId: string): Promise<{ version_id: string }> {
  return creationPlanRequest<{ version_id: string }>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/initialize`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export function redoCreationTaskTimeline(taskId: string): Promise<CreationTaskEditConfirmResponse> {
  return creationPlanRequest<CreationTaskEditConfirmResponse>(`/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/redo`, { method: "POST", body: JSON.stringify({}) });
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

export function listVideoTemplates(): Promise<{ items: VideoTemplate[] }> {
  return creationPlanRequest("/api/video-templates");
}

export function saveVideoTemplate(taskId: string, input: {
  name: string; style_instruction: string; template_id?: string; expected_version?: number;
}): Promise<VideoTemplate> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/templates`, {
    method: "POST", body: JSON.stringify(input),
  });
}

export function previewCreationTaskAudio(taskId: string, input: AudioEditInput): Promise<CreationTaskEditPreviewResponse> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}
export function listCreationTaskRecordings(taskId: string): Promise<{ items: CreationTaskRecording[] }> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/recordings`);
}
export async function saveCreationTaskRecording(taskId: string, recording: Blob): Promise<CreationTaskRecording> {
  const mediaType = recording.type.split(";")[0].trim().toLowerCase();
  const suffix = ({ "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav" } as Record<string, string>)[mediaType];
  if (!suffix) throw new Error("当前录音格式无法保存，请换一个支持录音的浏览器重试。");
  const body = new FormData();
  body.append("audio", recording, `本机录音.${suffix}`);
  const response = await fetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/recordings`),
    { method: "POST", body, cache: "no-store", headers: { ...localMemberTokenHeader() } });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "电脑录音未保存");
  return payload as CreationTaskRecording;
}
export function listenCreationTaskAudio(taskId: string, input: { base_version_id: string | null; start_seconds: number; end_seconds: number; solo_track_ids: string[] }): Promise<{ audio_url: string }> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/listen`, { method: "POST", body: JSON.stringify(input) });
}
export async function importCreationTaskAudio(taskId: string, file: File): Promise<{ source_ref: string; name: string; duration_ms: number }> {
  const body = new FormData(); body.append("audio", file);
  const response = await fetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/import`), { method: "POST", body, headers: { ...localMemberTokenHeader() } });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "音频导入失败");
  return payload;
}

export function previewCreationTaskVisual(taskId: string, input: import("./types").VisualEditInput): Promise<import("./types").CreationTaskEditPreviewResponse> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/visual/preview?draft=true`, { method: "POST", body: JSON.stringify(input) });
}


export async function importCreationTaskGraphic(taskId:string,file:File):Promise<import("./types").GraphicSource> {
  const body=new FormData();body.append("image",file);
  const response=await fetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/graphics/import`),{method:"POST",body,headers:{...localMemberTokenHeader()}});
  const payload=await response.json();
  if(!response.ok)throw new Error(typeof payload.detail==="string"?payload.detail:"图片导入失败");
  return payload;
}
export function getCreationTaskGraphicSources(taskId:string):Promise<import("./types").GraphicSource[]> {
  return creationPlanRequest(`/api/creation-tasks/${encodeURIComponent(taskId)}/graphics/sources`);
}

// 定时任务：任务定义管理（创建/启停/删除）。到点真实执行属后续接线，本页不触发任何自动运行。
export type ScheduledTaskType = "material_analysis" | "creation_render";
export type ScheduledTaskTriggerMode = "idle" | "scheduled";
export type ScheduledTask = {
  id: string;
  workspace_id: string;
  task_type: string;
  trigger_mode: string;
  target_ref: string;
  not_before_utc: string | null;
  window: Record<string, unknown> | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};
export type ScheduledTasksResponse = { tasks: ScheduledTask[]; total_count: number };
export type ScheduledTaskCreateInput = {
  task_type: ScheduledTaskType;
  trigger_mode: ScheduledTaskTriggerMode;
  target_ref: string;
  not_before_utc?: string | null;
  window?: Record<string, unknown> | null;
};
export function listScheduledTasks(): Promise<ScheduledTasksResponse> {
  return creationPlanRequest("/api/scheduled-tasks");
}
export function createScheduledTask(input: ScheduledTaskCreateInput): Promise<ScheduledTask> {
  return creationPlanRequest("/api/scheduled-tasks", { method: "POST", body: JSON.stringify(input) });
}
export function setScheduledTaskEnabled(taskId: string, enabled: boolean): Promise<ScheduledTask> {
  return creationPlanRequest(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/enabled`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}
export async function deleteScheduledTask(taskId: string): Promise<void> {
  await creationPlanRequest<ScheduledTask | null>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export type LocalMember = {
  member_id: string;
  name: string;
  role: "owner" | "member";
  created_at: string;
};

export type LocalMemberListResponse = { members: LocalMember[] };

/**
 * Local member commands fail with a JSON body `{"error": kind, "detail": message}`;
 * surface the human-readable `detail` instead of the raw body.
 */
export function membersErrorMessage(cause: unknown): string {
  if (cause instanceof Error && cause.message) {
    try {
      const payload = JSON.parse(cause.message) as { detail?: unknown };
      if (payload && typeof payload.detail === "string") return payload.detail;
    } catch {
      // not a JSON error body; fall through to the raw message
    }
    return cause.message;
  }
  return "成员操作失败";
}

export function listLocalMembers(): Promise<LocalMemberListResponse> {
  return request<LocalMemberListResponse>("/api/members");
}

export function addLocalMember(input: { name: string; role?: "owner" | "member" }): Promise<LocalMember> {
  return request<LocalMember>("/api/members", { method: "POST", body: JSON.stringify(input) });
}

export function removeLocalMember(memberId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/api/members/${encodeURIComponent(memberId)}`, { method: "DELETE" });
}

export function setLocalMemberRole(memberId: string, role: "owner" | "member"): Promise<LocalMember> {
  return request<LocalMember>(`/api/members/${encodeURIComponent(memberId)}`, { method: "PATCH", body: JSON.stringify({ role }) });
}

export function createLocalInviteCode(): Promise<{ invite_code: string }> {
  return request<{ invite_code: string }>("/api/members/invite-code", { method: "POST" });
}

/**
 * 加入空间。后端 `POST /api/members/join` 返回 `MemberWithTokenResponse`，其
 * `member_token` 是调用方唯一能拿到本人令牌的时机（main.py 的注释：token 只在
 * join 与 whoami 两处暴露）。所以这里如实返回带令牌的类型，调用方必须立刻落盘，
 * 否则后续请求会一直匿名（方案 A 下按 owner 放行，多成员形同虚设）。
 */
export function joinLocalMember(input: { code: string; name: string }): Promise<LocalMember & { member_token: string }> {
  return request<LocalMember & { member_token: string }>("/api/members/join", { method: "POST", body: JSON.stringify(input) });
}


export function saveScriptLibraryEntry(input: { source_script_id: string; name: string; category: string }) {
  return creationPlanRequest<ScriptLibraryEntry>("/api/production/script-library", {
    method: "POST", body: JSON.stringify(input),
  });
}
export function listScriptLibraryEntries(query = "", category = "") {
  const params = new URLSearchParams({ q: query, category });
  return creationPlanRequest<{ items: ScriptLibraryEntry[]; total_count: number }>(`/api/production/script-library?${params}`);
}
export function getScriptLibrarySources(entryId: string) {
  return creationPlanRequest<ScriptLibrarySource[]>(`/api/production/script-library/${encodeURIComponent(entryId)}/sources`);
}
export function reuseScriptLibraryEntry(entryId: string, input: {
  name: string; aspect_ratio: "9:16" | "16:9" | "1:1"; duration_target_seconds: number; requirements_confirmed: boolean;
}) {
  return creationPlanRequest<{ task_id: string; script: CreationTaskScript }>(
    `/api/production/script-library/${encodeURIComponent(entryId)}/reuse`, { method: "POST", body: JSON.stringify(input) },
  );
}
