import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResourceAudioAdoption } from "../resource-library/ResourceAudioAdoption";

const api = vi.hoisted(() => ({ read: vi.fn(), history: vi.fn(), confirm: vi.fn(), undo: vi.fn(), import: vi.fn(), preview: vi.fn(), resolve: vi.fn(), readJob: vi.fn(), readRequest: vi.fn(), readRequestId: vi.fn() }));
vi.mock("@/lib/api/creation-tasks", () => ({ getCreationTaskWorkspace: api.read, confirmCreationTaskEdit: api.confirm, undoCreationTaskTimeline: api.undo }));
vi.mock("@/lib/api/creation-transport", () => ({ creationPlanRequest: api.history }));
vi.mock("@/lib/api/task-media", () => ({ importCreationTaskResourceAudio: api.import, readCreationTaskResourceAudioRequest: api.readRequest, readCreationTaskResourceAudioRequestId: api.readRequestId, previewCreationTaskAudio: api.preview, resolveCreationTaskMediaResponse: api.resolve, getCreationTaskMediaJob: api.readJob }));
const resource = { id: "openfilm:music", version: 2, name: "钢琴", category_id: "music" };
const workspace = { task: { id: "task", name: "新作品", current_timeline_version_id: "v1", timeline_sequence: 1 }, montage: { duration_seconds: 20, audio_timeline: { tracks: [{ id: "music", name: "配乐", role: "music", locked: false }] } } };
const glass = { id: "openfilm:sound-glass-pulse", version: 1, name: "玻璃脉冲", category_id: "sfx" };
const adopted = { ...workspace, task: { ...workspace.task, current_timeline_version_id: "v2", timeline_sequence: 2, can_undo: true } };
const glassAdoption = { timeline_version_id: "v2", sequence: 2, transition: "added", operation_type: "EditAudioTimeline", operation_id: "glass-op" };
const noHistory = { completeness: "complete", current_resource_present: false, timeline_history: [], requests: [] };
const glassHistory = { ...noHistory, current_resource_present: true, timeline_history: [glassAdoption] };
const initialId = "a0c244c9-ab13-5601-8cbc-bcb450830ca2";
const retryId = "c8c5c234-d671-545f-b8f6-043ea47c276b";
const otherDeviceId = "47f71d55-cb7f-5681-9dda-95ea2713c6bd";
const requestKey = `ai-video-diary.resource-audio-request.v2:${initialId}`;
const otherDeviceKey = `ai-video-diary.resource-audio-request.v2:${otherDeviceId}`;
const submittedInput = { resource_id: resource.id, version: resource.version };
const imported = { source_ref: "artifacts/task/music", duration_ms: 45000 };
const importJob = { job_id: "resource-import", status: "succeeded", result: imported };
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  api.read.mockResolvedValue(workspace);
  api.history.mockResolvedValue(noHistory);
  api.readRequestId.mockImplementation(async (_task, _resource, _version, retryOf) => ({
    request_id: retryOf ? retryId : initialId,
    original_request_id: null,
  }));
  api.import.mockResolvedValue(importJob);
  api.resolve.mockImplementation(async (_task, response, kind) => kind === "audio_source" ? response.result : response);
  api.preview.mockResolvedValue({ proposal: { id: "proposal", summary: "加入钢琴" } });
  api.confirm.mockResolvedValue({ operation: { id: "op", status: "applied", result_version_id: "v2" } });
  api.undo.mockResolvedValue({});
});

it("recovers the fixed resource's adoption on reopening and retries the same original undo request", async () => {
  api.read.mockResolvedValue(adopted);
  api.history.mockResolvedValue(glassHistory);
  api.undo.mockRejectedValueOnce(new Error("本地服务未接通。"));
  render(<ResourceAudioAdoption taskId="task" resource={glass} playheadMs={0} />);
  const undo = await screen.findByRole("button", { name: "撤销本次采用" });
  expect(screen.getByText("已加入当前作品音轨。")).toBeVisible();
  expect(screen.getByRole("button", { name: "准备采用" })).toBeVisible();
  expect(api.history).toHaveBeenCalledWith("/api/creation-tasks/task/audio/resources/openfilm%3Asound-glass-pulse/history?version=1", { signal: expect.any(AbortSignal) });
  expect(api.import).not.toHaveBeenCalled();

  fireEvent.click(undo);
  expect(await screen.findByRole("alert")).toHaveTextContent("本地服务未接通");
  expect(api.undo).toHaveBeenCalledWith("task", {
    request_id: expect.any(String), target_operation_id: "glass-op",
    expected_timeline_version_id: "v2", expected_timeline_sequence: 2,
  });

  api.read.mockResolvedValue(workspace);
  fireEvent.click(undo);
  await screen.findByText(/已撤销本次音轨采用/);
  expect(api.undo).toHaveBeenCalledTimes(2);
  expect(api.undo.mock.calls[1]).toEqual(api.undo.mock.calls[0]);
});

it("leaves newer work intact when the recovered adoption is no longer the current version", async () => {
  api.read.mockResolvedValue(adopted);
  api.history.mockResolvedValue(glassHistory);
  render(<ResourceAudioAdoption taskId="task" resource={glass} playheadMs={0} />);
  const undo = await screen.findByRole("button", { name: "撤销本次采用" });
  api.read.mockResolvedValue({ ...adopted, task: { ...adopted.task, current_timeline_version_id: "v3", timeline_sequence: 3 } });
  fireEvent.click(undo);
  expect(await screen.findByRole("alert")).toHaveTextContent("作品已更新");
  expect(api.undo).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "撤销本次采用" })).not.toBeInTheDocument();
});

it.each([
  { ...glassHistory, timeline_history: [{ ...glassAdoption, transition: "retained" }] },
  { ...glassHistory, timeline_history: [{ ...glassAdoption, timeline_version_id: "old-head" }] },
  { ...glassHistory, completeness: "partial" },
])("does not offer targeted undo from history without a complete, matching addition", async history => {
  api.read.mockResolvedValue(adopted);
  api.history.mockResolvedValue(history);
  render(<ResourceAudioAdoption taskId="task" resource={glass} playheadMs={0} />);
  await screen.findByText("新作品");
  expect(screen.queryByRole("button", { name: "撤销本次采用" })).not.toBeInTheDocument();
  expect(screen.queryByText("已加入当前作品音轨。")).not.toBeInTheDocument();
});

it("imports the chosen fixed resource, previews trimmed placement, confirms explicitly and undoes only its operation", async () => {
  const updated = vi.fn();
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={5000} onTaskUpdated={updated} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.readRequestId).toHaveBeenCalledWith("task", "openfilm:music", 2, undefined, expect.any(AbortSignal));
  expect(api.import).toHaveBeenCalledWith("task", "openfilm:music", 2, initialId, undefined);
  expect(sessionStorage.getItem(requestKey)).toBe(api.import.mock.calls[0][3]);
  expect(api.resolve).toHaveBeenCalledWith("task", expect.anything(), "audio_source", expect.objectContaining({ baseVersionId: null, onUpdate: expect.any(Function) }));
  expect(api.preview).toHaveBeenCalledWith("task", expect.objectContaining({ base_version_id: "v1", target_id: "music", values: expect.objectContaining({ source_ref: "artifacts/task/music", source_end_ms: 15000, timeline_start_ms: 5000, gain_db: -18 }) }));
  expect(api.confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认加入音轨" }));
  await screen.findByRole("button", { name: "撤销本次采用" });
  expect(api.confirm).toHaveBeenCalledWith("task", "proposal");
  api.read.mockResolvedValue({ ...workspace, task: { ...workspace.task, current_timeline_version_id: "rendered-v3", timeline_sequence: 3 } });
  fireEvent.click(screen.getByRole("button", { name: "撤销本次采用" }));
  await waitFor(() => expect(api.undo).toHaveBeenCalledWith("task", expect.objectContaining({ target_operation_id: "op", expected_timeline_version_id: "rendered-v3", expected_timeline_sequence: 3 })));
  await screen.findByText(/已撤销本次音轨采用/);
});

it("does not import against a stale timeline or undo later edits", async () => {
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  api.read.mockResolvedValue({ ...workspace, task: { ...workspace.task, current_timeline_version_id: "new", timeline_sequence: 3 } });
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("作品已更新");
  expect(api.import).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认加入音轨" }));
  api.undo.mockRejectedValue(new Error("此修改之后已有其他时间线成果，不能覆盖后来成果"));
  fireEvent.click(await screen.findByRole("button", { name: "撤销本次采用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不能覆盖后来成果");
  expect(api.undo).toHaveBeenCalledWith("task", expect.objectContaining({ target_operation_id: "op", expected_timeline_version_id: "new", expected_timeline_sequence: 3 }));
  expect(screen.queryByText(/已撤销本次音轨采用/)).not.toBeInTheDocument();
});

it("retains a failed import Job for rereading instead of submitting another import", async () => {
  const job = { job_id: "original", status: "failed" };
  api.import.mockResolvedValue(job);
  api.resolve.mockRejectedValueOnce(new Error("设备暂不可用"));
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByText("设备暂不可用");
  expect(api.confirm).not.toHaveBeenCalled();
  api.readJob.mockResolvedValue({ ...importJob, job_id: job.job_id });
  fireEvent.click(screen.getByRole("button", { name: "读取导入结果" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.import).toHaveBeenCalledTimes(1);
  expect(api.readJob).toHaveBeenCalledWith("task", "original", expect.any(AbortSignal));
});

it("only makes a new import after a confirmed non-submission and a new user action", async () => {
  api.import.mockImplementationOnce(async (_task, _resource, _version, id) => {
    expect(sessionStorage.getItem(requestKey)).toBe(id);
    throw new Error("固定音频文件不可用");
  });
  api.readRequest.mockImplementation(async (_task, id) => ({
    task_id: "task", request_id: id, submitted_input: submittedInput, status: "rejected", submission_effect: "not_submitted", job_id: null,
  }));
  const first = render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByRole("button", { name: "重新尝试导入" });
  const id = api.import.mock.calls[0][3];
  expect(api.readRequest).toHaveBeenCalledWith("task", id, expect.any(AbortSignal));
  expect(api.import).toHaveBeenCalledTimes(1);
  expect(api.preview).not.toHaveBeenCalled();

  first.unmount();
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "查询原导入请求" }));
  fireEvent.click(await screen.findByRole("button", { name: "重新尝试导入" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.import).toHaveBeenCalledTimes(2);
  expect(api.readRequestId).toHaveBeenCalledWith("task", resource.id, resource.version, id, expect.any(AbortSignal));
  expect(api.import).toHaveBeenLastCalledWith("task", resource.id, resource.version, retryId, id);
  expect(sessionStorage.getItem(requestKey)).toBe(retryId);
  expect(api.confirm).not.toHaveBeenCalled();
});

it("reopens an unknown import and reads its original Job without another POST", async () => {
  api.import.mockRejectedValueOnce(new Error("响应超时"));
  api.readRequest.mockRejectedValueOnce(Object.assign(new Error("原请求不存在"), { status: 404 }));
  const first = render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByText(/原导入结果尚未确认/);
  const id = api.import.mock.calls[0][3];
  expect(sessionStorage.getItem(requestKey)).toBe(id);
  expect(api.readRequest).toHaveBeenCalledWith("task", id, expect.any(AbortSignal));
  expect(screen.queryByRole("button", { name: "重新尝试导入" })).not.toBeInTheDocument();
  first.unmount();

  api.readRequest.mockRejectedValueOnce(new Error("网络中断"))
    .mockResolvedValueOnce({ task_id: "task", request_id: id, submitted_input: submittedInput, status: "unknown", job_id: null })
    .mockResolvedValueOnce({ task_id: "task", request_id: id, submitted_input: submittedInput, status: "recorded", job_id: importJob.job_id });
  api.readJob.mockResolvedValue(importJob);
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  for (const reads of [2, 3]) {
    fireEvent.click(await screen.findByRole("button", { name: "查询原导入请求" }));
    await waitFor(() => expect(api.readRequest).toHaveBeenCalledTimes(reads));
    await screen.findByText(/原导入结果尚未确认/);
    expect(screen.queryByRole("button", { name: "重新尝试导入" })).not.toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: "查询原导入请求" }));
  const readResult = await screen.findByRole("button", { name: "读取导入结果" });
  expect(api.readRequest.mock.calls.map(call => call[1])).toEqual([id, id, id, id]);
  expect(api.preview).not.toHaveBeenCalled();
  fireEvent.click(readResult);
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.readJob).toHaveBeenCalledWith("task", importJob.job_id, expect.any(AbortSignal));
  expect(api.import).toHaveBeenCalledTimes(1);
  expect(api.confirm).not.toHaveBeenCalled();
});

it("keeps an unknown request bound to its device when reopening in the same tab", async () => {
  api.import.mockRejectedValueOnce(new Error("响应超时"));
  api.readRequest.mockRejectedValueOnce(Object.assign(new Error("原请求不存在"), { status: 404 }));
  const firstDevice = render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByText(/原导入结果尚未确认/);
  expect(sessionStorage.getItem(requestKey)).toBe(initialId);
  firstDevice.unmount();

  // Only the authenticated device scope changes. The tab, work and resource stay the same.
  api.readRequestId.mockResolvedValue({ request_id: otherDeviceId, original_request_id: null });
  const secondDevice = render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const prepare = await screen.findByRole("button", { name: "准备采用" });
  await waitFor(() => expect(prepare).toBeEnabled());
  fireEvent.click(prepare);
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.import).toHaveBeenLastCalledWith("task", resource.id, resource.version, otherDeviceId, undefined);
  expect(api.readRequest).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem(otherDeviceKey)).toBe(otherDeviceId);
  expect(sessionStorage.getItem(requestKey)).toBe(initialId);
  secondDevice.unmount();

  api.readRequestId.mockResolvedValue({ request_id: initialId, original_request_id: null });
  api.readRequest.mockResolvedValue({ task_id: "task", request_id: initialId, submitted_input: submittedInput,
    status: "recorded", job_id: "original-device-job" });
  api.readJob.mockResolvedValue({ ...importJob, job_id: "original-device-job" });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const recover = await screen.findByRole("button", { name: "查询原导入请求" });
  await waitFor(() => expect(recover).toBeEnabled());
  fireEvent.click(recover);
  fireEvent.click(await screen.findByRole("button", { name: "读取导入结果" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.readRequest).toHaveBeenLastCalledWith("task", initialId, expect.any(AbortSignal));
  expect(api.readJob).toHaveBeenCalledWith("task", "original-device-job", expect.any(AbortSignal));
  expect(api.import).toHaveBeenCalledTimes(2);
  expect(sessionStorage.getItem(otherDeviceKey)).toBe(otherDeviceId);
  expect(sessionStorage.getItem(requestKey)).toBe(initialId);
});

it("does not read or submit a saved request after its authenticated device scope changes", async () => {
  sessionStorage.setItem(requestKey, "a0c244c9-ab13-4601-8cbc-bcb450830ca2");
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const readOriginal = await screen.findByRole("button", { name: "查询原导入请求" });
  await waitFor(() => expect(readOriginal).toBeEnabled());
  api.readRequestId.mockResolvedValue({ request_id: otherDeviceId, original_request_id: null });
  fireEvent.click(readOriginal);
  await screen.findByText(/设备、成员或工作区已变化/);
  expect(api.readRequest).not.toHaveBeenCalled();
  expect(api.import).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(requestKey)).toBe("a0c244c9-ab13-4601-8cbc-bcb450830ca2");
  expect(sessionStorage.getItem(otherDeviceKey)).toBeNull();
});

it("rechecks the device before replacing a rejected request's saved pointer", async () => {
  sessionStorage.setItem(requestKey, initialId);
  api.readRequest.mockResolvedValue({ task_id: "task", request_id: initialId, submitted_input: submittedInput,
    status: "rejected", submission_effect: "not_submitted", job_id: null });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const recover = await screen.findByRole("button", { name: "查询原导入请求" });
  await waitFor(() => expect(recover).toBeEnabled());
  fireEvent.click(recover);
  const retry = await screen.findByRole("button", { name: "重新尝试导入" });
  api.readRequestId.mockImplementation(async (_task, _resource, _version, retryOf) => {
    if (retryOf === undefined) return { request_id: initialId, original_request_id: initialId };
    api.readRequestId.mockResolvedValue({ request_id: otherDeviceId, original_request_id: null });
    return { request_id: retryId, original_request_id: null };
  });
  fireEvent.click(retry);
  await screen.findByText(/设备、成员或工作区已变化/);
  expect(api.import).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(requestKey)).toBe(initialId);
  expect(sessionStorage.getItem(otherDeviceKey)).toBeNull();
});

it.each(["recorded", "unknown", "rejected"] as const)("recovers an earlier client's %s request selected by the server", async status => {
  const originalId = "a0c244c9-ab13-4601-8cbc-bcb450830ca2";
  api.readRequestId.mockResolvedValue({ request_id: initialId, original_request_id: originalId });
  api.readRequest.mockResolvedValue({ request_id: originalId, task_id: "task", submitted_input: submittedInput, status,
    ...(status === "recorded" ? { job_id: importJob.job_id } : { job_id: null, submission_effect: "not_submitted" }),
  });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "准备采用" }));
  const action = { recorded: "读取导入结果", unknown: "查询原导入请求", rejected: "重新尝试导入" }[status];
  await screen.findByRole("button", { name: action });
  expect(sessionStorage.getItem(requestKey)).toBe(originalId);
  expect(api.readRequest).toHaveBeenCalledWith("task", originalId, expect.any(AbortSignal));
  expect(api.import).not.toHaveBeenCalled();
  expect(api.confirm).not.toHaveBeenCalled();
});

it.each([
  { submitted_input: { ...submittedInput, resource_id: "different" }, status: "recorded", job_id: "wrong-job" },
  { submitted_input: { ...submittedInput, version: 1 }, status: "rejected", submission_effect: "not_submitted", job_id: null },
])("does not use an original receipt for a different resource or version", async receipt => {
  sessionStorage.setItem(requestKey, initialId);
  api.readRequest.mockResolvedValue({ task_id: "task", request_id: initialId, ...receipt });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const check = await screen.findByRole("button", { name: "查询原导入请求" });
  await waitFor(() => expect(check).toBeEnabled());
  fireEvent.click(check);
  expect(await screen.findByRole("alert")).toHaveTextContent("与当前资源或版本不一致");
  expect(screen.getByRole("button", { name: "查询原导入请求" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "重新尝试导入" })).not.toBeInTheDocument();
  expect(api.readJob).not.toHaveBeenCalled();
  expect(api.readRequestId.mock.calls.every(call => call[3] === undefined)).toBe(true);
  expect(api.import).not.toHaveBeenCalled();
});

it("uses the current device request lookup even when production history is partial", async () => {
  api.read.mockResolvedValue(adopted);
  api.history.mockResolvedValue({ ...glassHistory, completeness: "partial",
    requests: [{ request_id: retryId, status: "recorded" }] });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "准备采用" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.history).toHaveBeenCalled();
  expect(api.import).toHaveBeenCalledWith("task", resource.id, resource.version, initialId, undefined);
  expect(api.readRequest).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "撤销本次采用" })).not.toBeInTheDocument();
});

it("keeps recovered request failures query-only", async () => {
  api.readRequestId.mockResolvedValue({ request_id: initialId, original_request_id: retryId });
  api.readRequest.mockRejectedValue(Object.assign(new Error("原资源作业归属无法核验"), { status: 403 }));
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "准备采用" }));
  await screen.findByText(/原导入结果尚未确认/);
  expect(sessionStorage.getItem(requestKey)).toBe(retryId);
  expect(screen.getByRole("button", { name: "查询原导入请求" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "重新尝试导入" })).not.toBeInTheDocument();
  expect(api.import).not.toHaveBeenCalled();
});

it("requires a successful request lookup before the first submission", async () => {
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  const prepare = await screen.findByRole("button", { name: "准备采用" });
  await waitFor(() => expect(prepare).toBeEnabled());
  api.readRequestId.mockRejectedValue(Object.assign(new Error("暂时无法定位原导入请求"), { status: 404 }));
  fireEvent.click(prepare);
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法定位原导入请求");
  expect(sessionStorage.getItem(requestKey)).toBeNull();
  expect(api.import).not.toHaveBeenCalled();
});

it("shares gain between slider and exact input, invalidates old proposals, and prepares only the final value", async () => {
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  const slider = screen.getByRole("slider", { name: "音量调节（dB）" });
  const precise = screen.getByRole("spinbutton", { name: "音量（dB）" });
  expect(slider).toHaveValue("-18");
  for (const gain of [-24, -20, -12]) fireEvent.change(slider, { target: { value: String(gain) } });
  expect(precise).toHaveValue(-12);
  expect(api.import).not.toHaveBeenCalled();
  expect(api.preview).not.toHaveBeenCalled();
  fireEvent.change(precise, { target: { value: "-9" } });
  expect(slider).toHaveValue("-9");
  expect(slider).toHaveAttribute("aria-valuetext", "-9 dB");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  expect(slider).toBeDisabled();
  expect(precise).toBeDisabled();
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.preview).toHaveBeenCalledWith("task", expect.objectContaining({ values: expect.objectContaining({ gain_db: -9 }) }));
  fireEvent.change(slider, { target: { value: "-6" } });
  expect(screen.queryByRole("button", { name: "确认加入音轨" })).not.toBeInTheDocument();
  expect(api.confirm).not.toHaveBeenCalled();
  expect(api.preview).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认加入音轨" }));
  await screen.findByRole("button", { name: "撤销本次采用" });
  expect(api.preview).toHaveBeenLastCalledWith("task", expect.objectContaining({ values: expect.objectContaining({ gain_db: -6 }) }));
  expect(api.import).toHaveBeenCalledTimes(1);
  expect(api.confirm).toHaveBeenCalledWith("task", "proposal");
});

it("disables gain controls when all destination tracks are locked", async () => {
  api.read.mockResolvedValue({ ...workspace, montage: { ...workspace.montage, audio_timeline: { tracks: [{ ...workspace.montage.audio_timeline.tracks[0], locked: true }] } } });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  expect(screen.getByRole("slider", { name: "音量调节（dB）" })).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "音量（dB）" })).toBeDisabled();
  expect(api.import).not.toHaveBeenCalled();
});
