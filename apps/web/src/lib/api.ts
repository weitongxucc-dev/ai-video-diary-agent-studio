import type {
  ContentTemplates,
  Episode,
  EditPlan,
  EditPlanSegmentDecisionAction,
  DraftImport,
  DraftPackage,
  DraftStatus,
  EpisodeHistory,
  EpisodeStatus,
  FeedbackTargetType,
  FeedbackValue,
  FeedbackSummary,
  KnowledgeNote,
  OnboardingCompletionResponse,
  PendingVideo,
  Preferences,
  PreferenceCards,
  PreferenceEvidence,
  ProcessedVideo,
  ProductionChain,
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
  VoiceCommandResponse,
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
  typeof window === "undefined" ? serverApiBase : (process.env.NEXT_PUBLIC_API_BASE ?? "");

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

async function getServerAuthorization(): Promise<string | null> {
  if (typeof window !== "undefined" || !serverAuthorizationProvider) return null;
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

export function previewVoiceCommand(
  episodeId: string,
  transcript: string,
): Promise<VoiceCommandResponse> {
  return request<VoiceCommandResponse>(
    `/api/episodes/${episodeId}/voice-command/preview`,
    {
      method: "POST",
      body: JSON.stringify({ transcript }),
    },
  );
}

export function executeVoiceCommand(
  episodeId: string,
  transcript: string,
): Promise<VoiceCommandResponse> {
  return request<VoiceCommandResponse>(
    `/api/episodes/${episodeId}/voice-command/execute`,
    {
      method: "POST",
      body: JSON.stringify({ transcript }),
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

export function retrySourceSync(sourceKey: string): Promise<SourceSyncRetryResult> {
  return request<SourceSyncRetryResult>(`/api/sources/${encodeURIComponent(sourceKey)}/retry`, {
    method: "POST",
  });
}

export function ingestManualLink(url: string): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/links", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function getTopicEvidence(episodeId: string): Promise<TopicEvidenceList> {
  return request<TopicEvidenceList>(`/api/episodes/${episodeId}/topic-evidence`);
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
  return request<PreferenceEvidence>(`/api/preferences/cards/${encodeURIComponent(preferenceId)}/evidence`);
}

export function disablePreferenceCard(
  preferenceId: string,
): Promise<Preferences> {
  return request<Preferences>(
    "/api/preferences/disable",
    {
      method: "PATCH",
      body: JSON.stringify({ signal: preferenceId }),
    },
  );
}

export function restorePreferenceCard(
  preferenceId: string,
): Promise<Preferences> {
  return request<Preferences>(
    "/api/preferences/restore",
    {
      method: "PATCH",
      body: JSON.stringify({ signal: preferenceId }),
    },
  );
}

export function getDraftStatus(): Promise<DraftStatus> {
  return request<DraftStatus>("/api/drafts/status");
}

export function createDraftPackage(videoFileName?: string): Promise<DraftPackage> {
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

export function getProductionChain(episodeId: string): Promise<ProductionChain> {
  return request<ProductionChain>(`/api/episodes/${episodeId}/production-chain`);
}

export function getSourceFileStates(episodeId: string): Promise<SourceFileStates> {
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

export function getEpisodeRetakeReport(episodeId: string): Promise<RetakeReport> {
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
  return request<Episode>(`/api/episodes/${episodeId}/process-ad-hoc-video-batch`, {
    method: "POST",
    body: JSON.stringify({ file_names: fileNames }),
  });
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

export function getEpisodeHistory(status?: EpisodeStatus): Promise<EpisodeHistory> {
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
export function getPublishFeedback(episodeId: string): Promise<PublishFeedback> {
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
    transcript: typeof payload.transcript === "string" ? payload.transcript : "",
    confidence:
      typeof payload.confidence === "number" && Number.isFinite(payload.confidence)
        ? payload.confidence
        : 0,
    segments: Array.isArray(payload.segments)
      ? payload.segments
          .map((segment) => {
            if (!segment || typeof segment !== "object") {
              return null;
            }
            const s = segment as { start?: unknown; end?: unknown; text?: unknown };
            return {
              start: typeof s.start === "number" ? s.start : 0,
              end: typeof s.end === "number" ? s.end : 0,
              text: typeof s.text === "string" ? s.text : "",
            };
          })
          .filter((segment): segment is NonNullable<typeof segment> => segment !== null)
      : [],
  };
}
