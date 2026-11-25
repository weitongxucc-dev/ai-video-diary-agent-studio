import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreationPlanApiError, creationPlanRequest } from "../api/creation-transport";
import {
  confirmTimelineCandidateAdoption, prepareTimelineCandidateAdoption, readTimelineCandidate, readTimelineCandidatePreview,
  saveTimelineCandidateCrop, submitTimelineCandidatePreview,
} from "../api/timeline-candidates";
import type { TimelineCandidateDetail, TimelineCandidatePreview } from "../api/timeline-candidates";

vi.mock("../api/creation-transport", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/creation-transport")>(),
  creationPlanRequest: vi.fn(),
}));

const request = vi.mocked(creationPlanRequest);
const a = { taskId: "work", candidateId: "A" };
const b = { taskId: "work", candidateId: "B" };
const aPath = "/api/creation-tasks/work/timeline/candidates/A";
const bPath = "/api/creation-tasks/work/timeline/candidates/B";

function candidate(id: string, versionId: string, cropX: number): TimelineCandidateDetail {
  return {
    id, workspace_id: "local", task_id: "work", base_version_id: "v12", head_version_id: versionId,
    version: {
      id: versionId, parent_version_id: "v12", sequence: 13,
      clips: [{ id: "clip", fit_mode: "fill", crop_x: cropX, crop_y: .5 }],
    },
  };
}

const crop = { operation_type: "ChangeCrop", clip_id: "clip", fit_mode: "fill", crop_x: .45, crop_y: .5 } as const;

describe("candidate version requests", () => {
  beforeEach(() => request.mockReset());

  it("reads each current version and submits a crop only to its observed candidate", async () => {
    const first = candidate("A", "a13", .35);
    const second = candidate("B", "b13", .8);
    const saved = {
      ...first.version, id: "a14", parent_version_id: "a13", sequence: 14,
      clips: [{ ...first.version.clips[0], crop_x: .45 }],
    };
    request.mockResolvedValueOnce(first).mockResolvedValueOnce(second)
      .mockResolvedValueOnce({ ...first, head_version_id: "a14", version: saved });
    const signal = new AbortController().signal;

    const [loadedA, loadedB] = await Promise.all([readTimelineCandidate(a, signal), readTimelineCandidate(b, signal)]);
    expect(request).toHaveBeenNthCalledWith(1, aPath, { signal });
    expect(request).toHaveBeenNthCalledWith(2, bPath, { signal });
    await expect(saveTimelineCandidateCrop(a, loadedA, crop, "edit-1"))
      .resolves.toEqual({ savedVersion: saved, headVersionId: "a14" });
    expect(request).toHaveBeenNthCalledWith(3, `${aPath}/edits/exact`, {
      method: "POST",
      body: JSON.stringify({ ...crop, request_id: "edit-1", expected_timeline_version_id: "a13", expected_timeline_sequence: 13 }),
    });
    expect(loadedB.version.clips[0].crop_x).toBe(.8);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("rejects a crossed candidate response and leaves a stale save for the caller to resolve", async () => {
    const first = candidate("A", "a13", .35);
    request.mockResolvedValueOnce(first);
    await expect(readTimelineCandidate(b)).rejects.toThrow("候选版本与所选作品或候选不一致");
    await expect(saveTimelineCandidateCrop(b, first, crop, "edit-1")).rejects.toThrow("候选版本与所选作品或候选不一致");
    const conflict = new CreationPlanApiError(409, null, "候选时间线已变化");
    request.mockRejectedValueOnce(conflict);
    await expect(saveTimelineCandidateCrop(a, first, crop, "edit-1")).rejects.toBe(conflict);
    expect(request.mock.calls.map(([path]) => path)).toEqual([bPath, `${aPath}/edits/exact`]);
  });

  it("keeps a replayed save separate from a newer head", async () => {
    const first = candidate("A", "a13", .35);
    const saved = { ...first.version, id: "a14", parent_version_id: "a13", sequence: 14 };
    request.mockResolvedValueOnce({ ...first, head_version_id: "a15", version: saved });
    await expect(saveTimelineCandidateCrop(a, first, crop, "edit-1"))
      .resolves.toEqual({ savedVersion: saved, headVersionId: "a15" });
  });

  it("confirms the prepared candidate head and keeps the original request on retry", async () => {
    const second = candidate("B", "b13", .8);
    const base = { id: "v12", sequence: 12 };
    const binding = { task_id: "work", candidate_id: "B", candidate_version_id: "b13", candidate_sequence: 13,
      base_version_id: base.id, base_sequence: base.sequence, clip_id: "clip" };
    const selection = { ...binding, clip_number: 2, selection_digest: "digest",
      before: { fit_mode: "fill", crop_x: .5, crop_y: .5 }, after: { fit_mode: "fill", crop_x: .8, crop_y: .5 } };
    const result = { ...binding, result_version_id: "formal13", result_sequence: 13, operation_id: "operation" };
    const input = { expected_timeline_version_id: "b13", expected_timeline_sequence: 13,
      base_version_id: "v12", base_sequence: 12, clip_id: "clip" };
    request.mockResolvedValueOnce(selection).mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValueOnce(result).mockResolvedValueOnce({ ...result, candidate_id: "A" });

    const prepared = await prepareTimelineCandidateAdoption(b, second, base, "clip");
    expect(request).toHaveBeenCalledExactlyOnceWith(`${bPath}/adoption/prepare`, {
      method: "POST", body: JSON.stringify(input),
    });
    await expect(confirmTimelineCandidateAdoption(prepared, "same-request")).rejects.toThrow("connection lost");
    await expect(confirmTimelineCandidateAdoption(prepared, "same-request")).resolves.toEqual(result);
    expect(request.mock.calls[1]).toEqual([`${bPath}/adoption/confirm`, {
      method: "POST", body: JSON.stringify({ ...input, request_id: "same-request", selection_digest: "digest" }),
    }]);
    expect(request.mock.calls[2]).toEqual(request.mock.calls[1]);
    await expect(confirmTimelineCandidateAdoption(prepared, "same-request")).rejects.toThrow("采用回执与所选候选、基线或镜头不一致");
  });

  it("reads only the submitted candidate preview and accepts video from the same version and job", async () => {
    const path = `${aPath}/versions/a13/previews/job-a`;
    const pending: TimelineCandidatePreview = {
      task_id: "work", candidate_id: "A", version_id: "a13", clip_id: "clip", job_id: "job-a",
      status: "queued", result_url: path, video_url: null, silent: true,
      width_px: 480, height_px: 480,
      timeline_start_seconds: 1, timeline_end_seconds: 6, source_start_seconds: 0, source_end_seconds: 5,
      waiting_reason: null, failure_reason: null,
    };
    const done = { ...pending, status: "succeeded", video_url: `${path}/video` };
    request.mockResolvedValueOnce(pending).mockResolvedValueOnce(done)
      .mockResolvedValueOnce({ ...done, video_url: `${path.replace("a13", "a14")}/video` });
    await expect(submitTimelineCandidatePreview(a, candidate("A", "a13", .35), "clip", "request-a")).resolves.toEqual(pending);
    expect(request).toHaveBeenNthCalledWith(1, `${aPath}/previews`, {
      method: "POST", body: JSON.stringify({
        request_id: "request-a", clip_id: "clip", expected_timeline_version_id: "a13", expected_timeline_sequence: 13,
      }),
    });
    await expect(readTimelineCandidatePreview(b, pending)).rejects.toThrow("预览回执与所选候选、版本或镜头不一致");
    await expect(readTimelineCandidatePreview(a, pending)).resolves.toEqual(done);
    await expect(readTimelineCandidatePreview(a, pending)).rejects.toThrow("预览回执与所选候选、版本或镜头不一致");
    expect(request.mock.calls.map(([url]) => url)).toEqual([`${aPath}/previews`, path, path]);
  });
});
