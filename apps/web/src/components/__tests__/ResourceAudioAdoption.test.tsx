import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResourceAudioAdoption } from "../resource-library/ResourceAudioAdoption";

const api = vi.hoisted(() => ({ read: vi.fn(), history: vi.fn(), confirm: vi.fn(), undo: vi.fn(), import: vi.fn(), preview: vi.fn(), resolve: vi.fn(), readJob: vi.fn() }));
vi.mock("@/lib/api/creation-tasks", () => ({ getCreationTaskWorkspace: api.read, confirmCreationTaskEdit: api.confirm, undoCreationTaskTimeline: api.undo }));
vi.mock("@/lib/api/creation-transport", () => ({ creationPlanRequest: api.history }));
vi.mock("@/lib/api/task-media", () => ({ importCreationTaskResourceAudio: api.import, previewCreationTaskAudio: api.preview, resolveCreationTaskMediaResponse: api.resolve, getCreationTaskMediaJob: api.readJob }));
const resource = { id: "openfilm:music", version: 2, name: "钢琴", category_id: "music" };
const workspace = { task: { id: "task", name: "新作品", current_timeline_version_id: "v1", timeline_sequence: 1 }, montage: { duration_seconds: 20, audio_timeline: { tracks: [{ id: "music", name: "配乐", role: "music", locked: false }] } } };
const glass = { id: "openfilm:sound-glass-pulse", version: 1, name: "玻璃脉冲", category_id: "sfx" };
const adopted = { ...workspace, task: { ...workspace.task, current_timeline_version_id: "v2", timeline_sequence: 2, can_undo: true } };
const glassAdoption = { timeline_version_id: "v2", sequence: 2, transition: "added", operation_type: "EditAudioTimeline", operation_id: "glass-op" };
const glassHistory = { completeness: "complete", current_resource_present: true, timeline_history: [glassAdoption] };
beforeEach(() => {
  vi.resetAllMocks();
  api.read.mockResolvedValue(workspace);
  api.import.mockResolvedValue({ source_ref: "artifacts/task/music", duration_ms: 45000 });
  api.resolve.mockImplementation(async (_task, response) => response);
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
  expect(api.import).toHaveBeenCalledWith("task", "openfilm:music", 2);
  expect(api.resolve).toHaveBeenCalledWith("task", expect.anything(), "audio_source", expect.objectContaining({ baseVersionId: null }));
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
  api.resolve.mockImplementationOnce(async (_task, _response, _kind, options) => { options.onUpdate(job); throw new Error("设备暂不可用"); });
  render(<ResourceAudioAdoption taskId="task" resource={resource} playheadMs={0} />);
  await screen.findByText("新作品");
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByText("设备暂不可用");
  expect(api.confirm).not.toHaveBeenCalled();
  api.readJob.mockResolvedValue({ source_ref: "artifacts/task/music", duration_ms: 1000 });
  fireEvent.click(screen.getByRole("button", { name: "读取导入结果" }));
  await screen.findByRole("button", { name: "确认加入音轨" });
  expect(api.import).toHaveBeenCalledTimes(1);
  expect(api.readJob).toHaveBeenCalledWith("task", "original", expect.any(AbortSignal));
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
