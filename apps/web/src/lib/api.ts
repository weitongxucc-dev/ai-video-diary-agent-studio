import type {
  ContentTemplates,
  Episode,
  EditPlan,
  EditPlanSegmentDecisionAction,
  DraftImport,
  DraftPackage,
  DraftStatus,
  FeedbackTargetType,
  FeedbackValue,
  FeedbackSummary,
  KnowledgeNote,
  PendingVideo,
  Preferences,
  ProcessedVideo,
  RetakeReport,
  RuntimeHealth,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  ScriptPackage,
  SourceItems,
  SystemStatus,
  VoiceCommandResponse,
  WorkflowStatus,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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

export function syncSourceItems(): Promise<SourceItems> {
  return request<SourceItems>("/api/sources/sync", { method: "POST" });
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
    publish_url?: string;
    play_count?: number;
    like_count?: number;
    comment_count?: number;
  },
): Promise<Episode> {
  return request<Episode>(`/api/episodes/${episodeId}/mark-published`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
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
