import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResourceAudioAdoption } from "../resource-library/ResourceAudioAdoption";

const api = vi.hoisted(() => ({ read: vi.fn(), confirm: vi.fn(), undo: vi.fn(), import: vi.fn(), preview: vi.fn(), resolve: vi.fn(), readJob: vi.fn() }));
vi.mock("@/lib/api/creation-tasks", () => ({ getCreationTaskWorkspace: api.read, confirmCreationTaskEdit: api.confirm, undoCreationTaskTimeline: api.undo }));
vi.mock("@/lib/api/task-media", () => ({ importCreationTaskResourceAudio: api.import, previewCreationTaskAudio: api.preview, resolveCreationTaskMediaResponse: api.resolve, getCreationTaskMediaJob: api.readJob }));
const resource = { id: "openfilm:music", version: 2, name: "钢琴", category_id: "music" };
const workspace = { task: { id: "task", name: "新作品", current_timeline_version_id: "v1", timeline_sequence: 1 }, montage: { duration_seconds: 20, audio_timeline: { tracks: [{ id: "music", name: "配乐", role: "music", locked: false }] } } };
beforeEach(() => {
  vi.resetAllMocks();
  api.read.mockResolvedValue(workspace);
  api.import.mockResolvedValue({ source_ref: "artifacts/task/music", duration_ms: 45000 });
  api.resolve.mockImplementation(async (_task, response) => response);
  api.preview.mockResolvedValue({ proposal: { id: "proposal", summary: "加入钢琴" } });
  api.confirm.mockResolvedValue({ operation: { id: "op", status: "applied", result_version_id: "v2" } });
  api.undo.mockResolvedValue({});
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
