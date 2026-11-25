import type { CreationTaskExactEditInput } from "../types/task-edits";
import { CreationPlanApiError, creationPlanRequest } from "./creation-transport";
import { API_BASE, authorizedFetch } from "./transport";

type CropEdit = Extract<CreationTaskExactEditInput, { operation_type: "ChangeCrop" }>;

export type TimelineCandidateTarget = {
  taskId: string;
  candidateId: string;
};

export type TimelineCandidateVersion = {
  id: string;
  parent_version_id: string | null;
  sequence: number;
  clips: (Pick<CropEdit, "fit_mode" | "crop_x" | "crop_y"> & { id: string })[];
};

export type TimelineCandidateDetail = {
  id: string;
  workspace_id: string;
  task_id: string;
  base_version_id: string;
  head_version_id: string;
  version: TimelineCandidateVersion;
};

export type TimelineCandidateSummary = Omit<TimelineCandidateDetail, "version">;

export type TimelineCandidatePreview = {
  task_id: string;
  candidate_id: string;
  version_id: string;
  clip_id: string;
  job_id: string;
  status: "queued" | "running" | "interrupted" | "succeeded" | "failed" | "cancelled" | "stale";
  result_url: string;
  video_url: string | null;
  silent: true;
  width_px: number;
  height_px: number;
  timeline_start_seconds: number;
  timeline_end_seconds: number;
  source_start_seconds: number;
  source_end_seconds: number;
  waiting_reason: string | null;
  failure_reason: string | null;
};

function candidateCollectionPath(taskId: string) {
  return `/api/creation-tasks/${encodeURIComponent(taskId)}/timeline/candidates`;
}

function candidatePath({ taskId, candidateId }: TimelineCandidateTarget) {
  return `${candidateCollectionPath(taskId)}/${encodeURIComponent(candidateId)}`;
}

function requireTarget(target: TimelineCandidateTarget, candidate: TimelineCandidateDetail) {
  if (candidate.task_id !== target.taskId || candidate.id !== target.candidateId) {
    throw new Error("候选版本与所选作品或候选不一致。");
  }
}

function candidatePreviewPath(target: TimelineCandidateTarget, versionId: string, jobId: string) {
  return `${candidatePath(target)}/versions/${encodeURIComponent(versionId)}/previews/${encodeURIComponent(jobId)}`;
}

function requirePreview(
  target: TimelineCandidateTarget, versionId: string, clipId: string,
  preview: TimelineCandidatePreview, jobId: string,
) {
  const path = candidatePreviewPath(target, versionId, jobId);
  if (preview.task_id !== target.taskId || preview.candidate_id !== target.candidateId
      || preview.version_id !== versionId || preview.clip_id !== clipId
      || preview.job_id !== jobId || preview.silent !== true || preview.result_url !== path
      || preview.video_url !== (preview.status === "succeeded" ? `${path}/video` : null)) {
    throw new Error("预览回执与所选候选、版本或镜头不一致。");
  }
}

export async function readTimelineCandidate(target: TimelineCandidateTarget, signal?: AbortSignal) {
  const candidate = await creationPlanRequest<TimelineCandidateDetail>(candidatePath(target), { signal });
  requireTarget(target, candidate);
  if (candidate.version.id !== candidate.head_version_id) {
    throw new Error("候选当前版本暂不可核对，请重新读取。");
  }
  return candidate;
}

export async function listTimelineCandidates(taskId: string, signal?: AbortSignal) {
  const items: TimelineCandidateSummary[] = [];
  let cursor: string | null = null;
  do {
    const page: { items: TimelineCandidateSummary[]; next_after_id: string | null } = await creationPlanRequest(
      `${candidateCollectionPath(taskId)}?limit=50${cursor ? `&after_id=${encodeURIComponent(cursor)}` : ""}`,
      { signal },
    );
    if (page.items.some((item) => item.task_id !== taskId) || page.next_after_id && page.next_after_id === cursor) {
      throw new Error("候选列表与当前作品不一致。");
    }
    items.push(...page.items);
    cursor = page.next_after_id;
  } while (cursor);
  return items;
}

export async function forkTimelineCandidate(
  target: TimelineCandidateTarget, base: Pick<TimelineCandidateVersion, "id" | "sequence">,
) {
  const candidate = await creationPlanRequest<TimelineCandidateDetail>(candidateCollectionPath(target.taskId), {
    method: "POST",
    body: JSON.stringify({
      candidate_id: target.candidateId,
      expected_timeline_version_id: base.id,
      expected_timeline_sequence: base.sequence,
    }),
  });
  requireTarget(target, candidate);
  if (candidate.base_version_id !== base.id || candidate.version.id !== candidate.head_version_id) {
    throw new Error("新候选与所选基线不一致。");
  }
  return candidate;
}

export async function saveTimelineCandidateCrop(
  target: TimelineCandidateTarget,
  candidate: TimelineCandidateDetail,
  edit: CropEdit,
  requestId: string,
) {
  requireTarget(target, candidate);
  const base = candidate.version;
  if (base.id !== candidate.head_version_id || !base.clips.some((clip) => clip.id === edit.clip_id)) {
    throw new Error("请重新读取候选并选择该版本中的镜头。");
  }
  const receipt = await creationPlanRequest<TimelineCandidateDetail>(`${candidatePath(target)}/edits/exact`, {
    method: "POST",
    body: JSON.stringify({
      ...edit,
      request_id: requestId,
      expected_timeline_version_id: base.id,
      expected_timeline_sequence: base.sequence,
    }),
  });
  requireTarget(target, receipt);
  if (receipt.base_version_id !== candidate.base_version_id
      || receipt.version.parent_version_id !== base.id || receipt.version.sequence !== base.sequence + 1) {
    throw new Error("保存回执与本次候选修改不一致，请重新读取候选。");
  }
  // A repeated request can return its original saved version after a later edit.
  return { savedVersion: receipt.version, headVersionId: receipt.head_version_id };
}

export async function submitTimelineCandidatePreview(
  target: TimelineCandidateTarget, candidate: TimelineCandidateDetail, clipId: string, requestId: string,
) {
  requireTarget(target, candidate);
  const version = candidate.version;
  if (version.id !== candidate.head_version_id || !version.clips.some((clip) => clip.id === clipId)) {
    throw new Error("请重新读取候选并选择该版本中的镜头。");
  }
  const preview = await creationPlanRequest<TimelineCandidatePreview>(`${candidatePath(target)}/previews`, {
    method: "POST",
    body: JSON.stringify({
      request_id: requestId,
      clip_id: clipId,
      expected_timeline_version_id: version.id,
      expected_timeline_sequence: version.sequence,
    }),
  });
  requirePreview(target, version.id, clipId, preview, preview.job_id);
  return preview;
}

export async function readTimelineCandidatePreview(
  target: TimelineCandidateTarget, original: TimelineCandidatePreview, signal?: AbortSignal,
) {
  requirePreview(target, original.version_id, original.clip_id, original, original.job_id);
  const preview = await creationPlanRequest<TimelineCandidatePreview>(
    candidatePreviewPath(target, original.version_id, original.job_id), { signal },
  );
  requirePreview(target, original.version_id, original.clip_id, preview, original.job_id);
  return preview;
}

export async function readLatestTimelineCandidatePreview(
  target: TimelineCandidateTarget, version: TimelineCandidateVersion, clipId: string, signal?: AbortSignal,
) {
  const query = new URLSearchParams({ sequence: String(version.sequence), clip_id: clipId });
  const { preview } = await creationPlanRequest<{ preview: TimelineCandidatePreview | null }>(
    `${candidatePath(target)}/versions/${encodeURIComponent(version.id)}/previews?${query}`, { signal },
  );
  if (preview) requirePreview(target, version.id, clipId, preview, preview.job_id);
  return preview;
}

export async function cancelTimelineCandidatePreview(target: TimelineCandidateTarget, original: TimelineCandidatePreview) {
  requirePreview(target, original.version_id, original.clip_id, original, original.job_id);
  const preview = await creationPlanRequest<TimelineCandidatePreview>(`${original.result_url}/cancel`, { method: "POST" });
  requirePreview(target, original.version_id, original.clip_id, preview, original.job_id);
  return preview;
}

export async function readTimelineCandidatePreviewVideo(
  target: TimelineCandidateTarget, preview: TimelineCandidatePreview, signal?: AbortSignal,
) {
  requirePreview(target, preview.version_id, preview.clip_id, preview, preview.job_id);
  if (!preview.video_url) throw new Error("候选还没有可读取的画面。");
  const response = await authorizedFetch(`${API_BASE}${preview.video_url}`, { signal });
  if (!response.ok) {
    const message = response.status === 409 ? "候选画面已过期或文件不可用，请重新读取候选。"
      : response.status === 404 || response.status === 403 ? "当前成员或版本无法读取此候选画面。"
        : "候选画面暂时不可读取。";
    throw new CreationPlanApiError(response.status, null, message);
  }
  return response.blob();
}
