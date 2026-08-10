import type {
  ContentTemplates,
  Episode,
  EditPlan,
  EditPlanSegmentDecisionAction,
  DraftImport,
  DraftPackage,
  DraftStatus,
  EpisodeHistory,
  EpisodeIntakeMode,
  EpisodeStatus,
  FeedbackTargetType,
  FeedbackValue,
  FeedbackSummary,
  KnowledgeNote,
  PendingVideo,
  Preferences,
  PreferenceCard,
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
  SetupStatus,
  SourcesStatus,
  SourceFileStates,
  SourceItems,
  SystemStatus,
  TopicEvidenceList,
  VideoProductionMode,
  VoiceCommandResponse,
  WorkflowStatus,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
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

// TODO(backend): retry-single-source endpoint is not implemented yet.
// Until it is, callers should use syncSourceItems() or surface a friendly toast.
export function retrySourceSync(sourceKey: string): Promise<SourceItems> {
  return Promise.reject(
    new Error(
      `重试单来源暂未实现（${sourceKey}），请改用 /api/sources/sync`,
    ),
  );
}

export function ingestManualLink(url: string): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/links", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

// TODO(backend): topic-evidence endpoint is not implemented yet.
// Until it is, callers should omit evidence and the page should render
// without the per-topic freshness breakdown.
export function getTopicEvidence(episodeId: string): Promise<TopicEvidenceList> {
  return Promise.reject(
    new Error(
      `topic-evidence 端点暂未实现（episode=${episodeId}）`,
    ),
  );
}

export function getFeedbackSummary(): Promise<FeedbackSummary> {
  return request<FeedbackSummary>("/api/feedback/summary");
}

export function getPreferences(): Promise<Preferences> {
  return request<Preferences>("/api/preferences");
}

export function getPreferenceCards(): Promise<PreferenceCards> {
  return request<Preferences>("/api/preferences").then((prefs) => {
    const cards: PreferenceCard[] = prefs.preferences.map((item) => ({
      id: `${item.label}::${item.signal}`,
      label: item.label,
      signal: item.signal,
      category: item.label,
      confidence: item.confidence,
      evidence_count: item.evidence_count,
      enabled: item.enabled,
      latest_evidence_at: null,
    }));
    return {
      total_count: cards.length,
      enabled_count: cards.filter((card) => card.enabled).length,
      disabled_count: cards.filter((card) => !card.enabled).length,
      cards,
    };
  });
}

// TODO(backend): preference evidence endpoint is not implemented yet.
export function getPreferenceEvidence(
  preferenceId: string,
): Promise<PreferenceEvidence> {
  return Promise.reject(
    new Error(`偏好证据端点暂未实现（${preferenceId}）`),
  );
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

// TODO(backend): production-mode endpoint is not implemented yet.
// Today the production mode is derived from how videos are bound to the
// episode, not a separate user setting.
export function setEpisodeProductionMode(
  episodeId: string,
  mode: VideoProductionMode,
): Promise<Episode> {
  return Promise.reject(
    new Error(
      `production-mode 端点暂未实现（episode=${episodeId}, mode=${mode}）`,
    ),
  );
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
  // Backend does not expose a dedicated history endpoint; reuse the episode
  // list (which is paginated and filterable) and adapt to the history shape
  // consumed by the page.
  const params = new URLSearchParams();
  if (status) {
    params.set("status", status);
  }
  const query = params.toString();
  const suffix = query ? `?${query}` : "";
  return request<{
    total: number;
    episodes: Array<{
      id: string;
      day_number: number;
      episode_date: string;
      status: EpisodeStatus;
      intake_mode: EpisodeIntakeMode;
      selected_topic_id: string | null;
      publish_url: string | null;
      platform: string | null;
      draft_package_name: string | null;
      published_at: string | null;
      created_at: string;
      updated_at: string;
    }>;
  }>(`/api/episodes${suffix}`).then((response): EpisodeHistory => {
    const items = response.episodes.map((ep) => ({
      id: ep.id,
      day_number: ep.day_number,
      episode_date: ep.episode_date,
      status: ep.status,
      intake_mode: ep.intake_mode,
      topic_title: null,
      script_title: ep.draft_package_name,
      publish_url: ep.publish_url,
      platform: ep.platform,
      play_count: null,
      like_count: null,
      comment_count: null,
      draft_package_path: null,
      video_dedup_report_path: null,
      published_at: ep.published_at,
      created_at: ep.created_at,
      updated_at: ep.updated_at,
    }));
    return {
      total_count: response.total,
      filtered_count: items.length,
      status: status ?? null,
      items,
    };
  });
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
