import { afterEach, expect, it, vi } from "vitest";
import { cancelCreationTaskMediaJob, getCreationTaskMediaJob, listenCreationTaskAudio, resolveCreationTaskMediaResponse, TaskMediaJobError, listCreationTaskMediaJobs, taskMediaJobMatchesContext, taskMediaJobLabel } from "../api";
import type { TaskMediaJob } from "../types";
import { LOCAL_MEMBER_TOKEN_HEADER, LOCAL_MEMBER_TOKEN_STORAGE_KEY } from "../local-member-auth";

const queued: TaskMediaJob = { operation: "audio_listen", target_refs: [], input_digest: "opaque-digest", created_at: "2026-10-11T10:00:00Z", job_id: "job-one", status: "queued", result_kind: "audio_listen",
  result_url: "/api/creation-tasks/task-one/media-jobs/job-one", device_request_id: "device-one",
  waiting_reason: "等待设备空闲", base_version_id: "version-one", result: null, failure_reason: null, repair_action: null };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); window.localStorage.clear(); });

it("keeps the 202 receipt and only returns playable media after an authorized persistent read", async () => {
  vi.useFakeTimers();
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "member-token");
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(queued), { status: 202 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, status: "succeeded", result: { audio_url: "/actual.wav" } })));
  vi.stubGlobal("fetch", fetch);
  const receipt = await listenCreationTaskAudio("task-one", { base_version_id: "version-one", start_seconds: 0, end_seconds: 1, solo_track_ids: [] });
  expect(receipt).toEqual(queued);
  const update = vi.fn();
  const result = resolveCreationTaskMediaResponse("task-one", receipt, "audio_listen", { signal: new AbortController().signal, baseVersionId: "version-one", onUpdate: update });
  expect(update).toHaveBeenCalledWith(queued);
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(2500);
  expect(await result).toEqual({ audio_url: "/actual.wav" });
  expect(fetch).toHaveBeenLastCalledWith(queued.result_url, expect.objectContaining({ headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "member-token" }), cache: "no-store" }));
});

it.each(["failed", "cancelled", "stale"] as const)("rejects %s without a media result", async status => {
  const response = { ...queued, status, failure_reason: "actual-reason" };
  await expect(resolveCreationTaskMediaResponse("task-one", response, "audio_listen", {
    signal: new AbortController().signal, baseVersionId: "version-one", onUpdate: vi.fn(),
  })).rejects.toMatchObject({ job: response } satisfies Partial<TaskMediaJobError>);
});

it("stops reading when the selection is invalidated instead of cancelling or resubmitting the server job", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const controller = new AbortController();
  const result = resolveCreationTaskMediaResponse("task-one", queued, "audio_listen", { signal: controller.signal, baseVersionId: "version-one", onUpdate: vi.fn() });
  const rejected = expect(result).rejects.toMatchObject({ name: "AbortError" });
  controller.abort(); await rejected;
  await vi.advanceTimersByTimeAsync(5000);
  expect(fetch).not.toHaveBeenCalled();
});

it("does not consume a different base or result kind", async () => {
  const options = { signal: new AbortController().signal, baseVersionId: "new-version", onUpdate: vi.fn() };
  await expect(resolveCreationTaskMediaResponse("task-one", queued, "audio_listen", options)).rejects.toThrow("本次输入不一致");
  await expect(resolveCreationTaskMediaResponse("task-one", queued, "waveform", { ...options, baseVersionId: "version-one" })).rejects.toThrow("本次输入不一致");
  expect(options.onUpdate).not.toHaveBeenCalled();
});

it("reads and cancels only the fixed task job route", async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ ...queued, status: "cancelled" })));
  vi.stubGlobal("fetch", fetch);
  await getCreationTaskMediaJob("task/a", "job/b");
  await cancelCreationTaskMediaJob("task/a", "job/b");
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/creation-tasks/task%2Fa/media-jobs/job%2Fb", expect.anything());
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/creation-tasks/task%2Fa/media-jobs/job%2Fb/cancel", expect.objectContaining({ method: "POST", body: "{}" }));
});

it("uses bounded member-authorized recovery pages with the actual JobRun cursor", async () => {
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "current-member");
  const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [queued], next_cursor: "job-next" })));
  vi.stubGlobal("fetch", fetch);
  const page = await listCreationTaskMediaJobs("task-one", "job/previous");
  expect(page).toEqual({ items: [queued], next_cursor: "job-next" });
  expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task-one/media-jobs?limit=20&cursor=job%2Fprevious", expect.objectContaining({ headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "current-member" }) }));
});

it("matches recovery only to its frozen operation, base and trusted selection", () => {
  expect(taskMediaJobMatchesContext(queued, "version-one", ["clip:current"])).toBe(true);
  expect(taskMediaJobMatchesContext({ ...queued, target_refs: ["clip:other"] }, "version-one", ["clip:other"])).toBe(false);
  expect(taskMediaJobMatchesContext(queued, "new-version", [])).toBe(false);
  const edit = { ...queued, result_kind: "edit_preview" as const, operation: "compound_selection_preview", target_refs: ["clip:one", "subtitle-track:one"] };
  expect(taskMediaJobMatchesContext(edit, "version-one", ["subtitle-track:one", "clip:one"])).toBe(true);
  expect(taskMediaJobMatchesContext(edit, "version-one", ["clip:two"])).toBe(false);
  expect(taskMediaJobMatchesContext({ ...edit, operation: "audio_listen" }, "version-one", edit.target_refs)).toBe(false);
  expect(taskMediaJobMatchesContext({ ...queued, status: "stale" }, "version-one", [])).toBe(false);
});

it("does not return recovery results after an API member-boundary rejection", async () => {
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "other-member");
  const fetch = vi.fn(async () => new Response(JSON.stringify({ detail: "任务媒体作业不存在" }), { status: 404 }));
  vi.stubGlobal("fetch", fetch);
  await expect(getCreationTaskMediaJob("task-one", queued.job_id)).rejects.toMatchObject({ status: 404 });
  expect(fetch).toHaveBeenCalledWith(queued.result_url, expect.objectContaining({ headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "other-member" }) }));
});

it("uses ordinary device waiting text and preserves the original diagnostic reason", () => {
  const pressure = { ...queued, waiting_reason: "resource_pressure" };
  expect(taskMediaJobLabel(pressure)).toBe("等待处理：本机资源暂时不足，等待恢复。");
  expect(taskMediaJobLabel({ ...queued, waiting_reason: "resource_busy" })).toContain("本机资源余量不足");
  const unknown = { ...queued, waiting_reason: "new_internal_wait_code" };
  expect(taskMediaJobLabel(unknown)).toBe("已安排，等待设备空闲");
  expect(pressure.waiting_reason).toBe("resource_pressure");
  expect(unknown.waiting_reason).toBe("new_internal_wait_code");
});

it("offers recovery only for queued, running, interrupted or succeeded jobs", () => {
  for (const status of ["queued", "running", "interrupted", "succeeded"] as const) {
    expect(taskMediaJobMatchesContext({ ...queued, status }, "version-one", [])).toBe(true);
  }
  for (const status of ["failed", "cancelled", "stale"] as const) {
    expect(taskMediaJobMatchesContext({ ...queued, status }, "version-one", [])).toBe(false);
    const edit = { ...queued, status, operation: "command_edit_preview", result_kind: "edit_preview" as const, target_refs: ["clip:one"] };
    expect(taskMediaJobMatchesContext(edit, "version-one", ["clip:one"])).toBe(false);
  }
});
