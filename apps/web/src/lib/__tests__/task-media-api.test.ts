import { afterEach, expect, it, vi } from "vitest";
import { getCreationTaskAudioWaveform, cancelCreationTaskMediaJob, getCreationTaskMediaJob, listenCreationTaskAudio, resolveCreationTaskMediaResponse, TaskMediaJobError, listCreationTaskMediaJobs, taskMediaJobMatchesContext, taskMediaJobLabel, taskMediaResourceCheckLabel } from "../api";
import type { ResourceAdmissionReceipt, TaskMediaJob } from "../types";
import { LOCAL_MEMBER_TOKEN_HEADER, LOCAL_MEMBER_TOKEN_STORAGE_KEY } from "../local-member-auth";

const queued: TaskMediaJob = { operation: "audio_listen", target_refs: [], input_digest: "opaque-digest", created_at: "2026-10-11T10:00:00Z", job_id: "job-one", status: "queued", result_kind: "audio_listen",
  result_url: "/api/creation-tasks/task-one/media-jobs/job-one", device_request_id: "device-one",
  waiting_reason: "等待设备空闲", base_version_id: "version-one", result: null, failure_reason: null, repair_action: null, resource_wait: null };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); window.localStorage.clear(); });

const resourceReceipt: ResourceAdmissionReceipt = {
  observation_id: "observation-one", device_request_id: "device-one", task_id: "task-one", job_id: "job-one",
  input_digest: "opaque-digest", base_version_id: "version-one", attempt_number: 1, last_grant_generation: null,
  phase: "admission", observation_kind: "first_refused", checked_at: "2026-10-13T01:02:03Z",
  observed_interval_seconds: 0.1, context: "pending_request", admitted: false,
  checks: [{metric: "cpu_percent", unit: "percent", actual: 95.6, minimum: null, maximum: 91, passed: false}],
  summary: "首次检查时 CPU 使用率超过门限。", repair_action: "请等待设备资源恢复后查询原请求。",
};

it("keeps the same-request first observation through authorized job, list and cancellation reads", async () => {
  const pending = {...queued, waiting_reason: "resource_busy", resource_wait: resourceReceipt};
  const history = {...pending, status: "cancelled", waiting_reason: null,
    resource_wait: {...resourceReceipt, context: "history"}};
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(pending)))
    .mockResolvedValueOnce(new Response(JSON.stringify({items: [pending], next_cursor: null})))
    .mockResolvedValueOnce(new Response(JSON.stringify(history)));
  vi.stubGlobal("fetch", fetch);
  expect(await getCreationTaskMediaJob("task-one", "job-one")).toEqual(pending);
  expect(await listCreationTaskMediaJobs("task-one")).toEqual({items: [pending], next_cursor: null});
  expect(await cancelCreationTaskMediaJob("task-one", "job-one")).toEqual(history);
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(resourceReceipt.last_grant_generation).toBeNull();
  expect(resourceReceipt.checked_at).toBe("2026-10-13T01:02:03Z");
});

it("formats only recorded metric values and thresholds, keeping unknown distinct from zero", () => {
  expect(taskMediaResourceCheckLabel({metric: "cpu_percent", unit: "percent", actual: 95.6,
    minimum: null, maximum: 91, passed: false})).toBe("CPU 使用率：实测 95.6%；不高于 91%；检查未通过。");
  expect(taskMediaResourceCheckLabel({metric: "available_memory_bytes", unit: "bytes", actual: null,
    minimum: 3221225472, maximum: null, passed: null})).toBe("可用内存：实测值未知；至少 3,221,225,472 字节；检查结果未知。");
  expect(taskMediaResourceCheckLabel({metric: "swap_used_bytes", unit: "bytes", actual: 0,
    minimum: null, maximum: 0, passed: true})).toBe("交换空间使用量：实测 0 字节；不高于 0 字节；符合门限。");
  expect(taskMediaResourceCheckLabel({metric: "disk_free_bytes", unit: "bytes", actual: 100,
    minimum: 200, maximum: null, passed: false})).toBe("磁盘剩余空间：实测 100 字节；至少 200 字节；检查未通过。");
  expect(taskMediaResourceCheckLabel({metric: "disk_io_bytes_per_second", unit: "bytes_per_second", actual: 12.5,
    minimum: null, maximum: 10, passed: false})).toBe("磁盘读写速度：实测 12.5 字节/秒；不高于 10 字节/秒；检查未通过。");
});

it("recognizes registered script image-source recovery without requiring editor target refs or treating it as an edit", () => {
  const graphic: TaskMediaJob = {...queued, operation: "media_script_tool_preview", result_kind: "graphic_source",
    target_refs: ["artifact:input-source"], status: "succeeded", result: {source_ref:"artifact:output",artifact_id:"output",
      sha256:"a".repeat(64),size_bytes:100,name:"panel.png",width:64,height:64,applied:false}};
  expect(taskMediaJobMatchesContext(graphic,"version-one",["clip:clip-1"])).toBe(true);
  expect(taskMediaJobMatchesContext({...graphic,base_version_id:"old"},"version-one",[])).toBe(false);
  expect(taskMediaJobMatchesContext({...graphic,status:"stale"},"version-one",[])).toBe(false);
  expect(taskMediaJobMatchesContext({...graphic,operation:"unregistered-script"},"version-one",[])).toBe(false);
});

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

it("reads a succeeded waveform receipt from the same authorized job without resubmitting waveform", async () => {
  window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "member-token");
  const receipt: TaskMediaJob = { ...queued, operation: "audio_waveform", result_kind: "waveform", status: "succeeded" };
  const waveform = { peaks: [.1, .4], seconds_per_peak: .1 };
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(receipt), { status: 202 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...receipt, result: waveform })));
  vi.stubGlobal("fetch", fetch);
  const response = await getCreationTaskAudioWaveform("task-one", "clip-one");
  const update = vi.fn();
  expect(await resolveCreationTaskMediaResponse("task-one", response, "waveform", {
    signal: new AbortController().signal, baseVersionId: "version-one", onUpdate: update,
  })).toEqual(waveform);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch).toHaveBeenLastCalledWith(receipt.result_url, expect.objectContaining({
    headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "member-token" }), cache: "no-store",
  }));
  expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ result: waveform }));
});

it.each(["succeeded", "failed", "stale"] as const)("rejects a persisted %s job without a result after one receipt read", async status => {
  const receipt: TaskMediaJob = { ...queued, status: "succeeded" };
  const persisted = { ...receipt, status, failure_reason: "actual-reason" };
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(persisted)));
  vi.stubGlobal("fetch", fetch);
  const result = resolveCreationTaskMediaResponse("task-one", receipt, "audio_listen", {
    signal: new AbortController().signal, baseVersionId: "version-one", onUpdate: vi.fn(),
  });
  if (status === "succeeded") await expect(result).rejects.toThrow("未返回可用结果");
  else await expect(result).rejects.toMatchObject({ job: persisted });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith(receipt.result_url, expect.anything());
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
  await expect(resolveCreationTaskMediaResponse("task-one", { ...queued, status: "succeeded" }, "audio_listen", {
    signal: controller.signal, baseVersionId: "version-one", onUpdate: vi.fn(),
  })).rejects.toMatchObject({ name: "AbortError" });
  expect(fetch).not.toHaveBeenCalled();
});

it("does not consume a different base or result kind", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const receipt: TaskMediaJob = { ...queued, status: "succeeded" };
  const options = { signal: new AbortController().signal, baseVersionId: "new-version", onUpdate: vi.fn() };
  await expect(resolveCreationTaskMediaResponse("task-one", receipt, "audio_listen", options)).rejects.toThrow("本次输入不一致");
  await expect(resolveCreationTaskMediaResponse("task-one", receipt, "waveform", { ...options, baseVersionId: "version-one" })).rejects.toThrow("本次输入不一致");
  expect(options.onUpdate).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
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
  expect(taskMediaJobLabel({ ...queued, waiting_reason: "resource_busy" })).toBe("等待处理：本机资源检查尚未通过，正在等待满足制作条件。");
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

it("shows the recorded refusal when an asynchronous media job fails", () => {
  expect(taskMediaJobLabel({ ...queued, status: "failed", failure_reason: "所选字体未安装" }))
    .toBe("预览未完成：所选字体未安装");
  expect(taskMediaJobLabel({ ...queued, status: "failed" })).toBe("预览未完成");
});
