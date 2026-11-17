import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ResourceVisualAdoption } from "../resource-library/ResourceVisualAdoption";
import type { CatalogResource } from "../EditorResourceLibraryPanel";

const api = vi.hoisted(() => ({ read: vi.fn(), template: vi.fn(), preview: vi.fn(), resolve: vi.fn(), confirm: vi.fn(), undo: vi.fn() }));
vi.mock("@/lib/api/creation-tasks", () => ({ getCreationTaskWorkspace: api.read, previewCreationTaskExactEdit: api.preview, confirmCreationTaskEdit: api.confirm, undoCreationTaskTimeline: api.undo }));
vi.mock("@/lib/api/task-media", () => ({ resolveCreationTaskMediaResponse: api.resolve }));
vi.mock("@/lib/video-template-api", () => ({ readVideoTemplateVersion: api.template }));
vi.mock("../VisualTemplatePreview", () => ({ VisualTemplatePreview: () => null }));
const resource: CatalogResource = { id: "openfilm:transition-glitch", category_id: "transitions", version: 2, name: "信号故障", status: "connected", resource_type: "css_transition", preview: { kind: "html" }, provenance: [], entrypoints: [], runtime_requirements: { engine: "openfilm-html-v1", template_id: "builtin_visual_openfilm-transition-glitch", template_version: 1 } };
const visual = { engine: "openfilm-html-v1", category: "reaction", width: 384, height: 216, duration_seconds: .6, source: "original-css-host", values: {}, bindings: [], dependencies: [], provenance: [{ path: "transition/glitch/overlay.css", source_sha256: "original-digest" }] };
const workspace = { task: { current_timeline_version_id: "v1", timeline_sequence: 1 }, montage: { duration_seconds: 4, aspect_ratio: "9:16" } };
beforeEach(() => {
  vi.resetAllMocks();
  api.read.mockResolvedValue(workspace);
  api.template.mockResolvedValue({ id: "builtin_visual_openfilm-transition-glitch", version: 1, settings: { visual } });
  api.preview.mockResolvedValue({ proposal: { id: "p1", summary: "加入信号故障" } });
  api.resolve.mockImplementation(async (_task, response) => response);
  api.confirm.mockResolvedValue({ operation: { id: "op", status: "applied" } });
  api.undo.mockResolvedValue({});
});

it("adopts the fixed CSS template at the selected time and canvas ratio through preview/confirm, then undoes its operation", async () => {
  render(<ResourceVisualAdoption taskId="task" resource={resource} playheadMs={1000} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "准备采用" })).toBeEnabled());
  expect(api.template).toHaveBeenCalledWith("builtin_visual_openfilm-transition-glitch", 1);
  expect(api.preview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  const confirm = await screen.findByRole("button", { name: "确认加入动画" });
  expect(api.preview).toHaveBeenCalledWith("task", { operation_type: "EditGraphicOverlay", overlay_action: "add", graphic_overlay: expect.objectContaining({
    name: "信号故障 · v2", template_id: "builtin_visual_openfilm-transition-glitch", template_version: 1,
    start_seconds: 1, end_seconds: 1.6, width: 1, x: .5, y: .5,
    definition: { ...visual, height: 683 },
  }) }, { draftOnly: true });
  expect(api.confirm).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  fireEvent.click(await screen.findByRole("button", { name: "撤销本次采用" }));
  await waitFor(() => expect(api.undo).toHaveBeenCalledWith("task", expect.objectContaining({ target_operation_id: "op", expected_timeline_version_id: "v1", expected_timeline_sequence: 1 })));
});

it("invalidates an old proposal when position changes and rejects an interval beyond the work", async () => {
  render(<ResourceVisualAdoption taskId="task" resource={resource} playheadMs={1000} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "准备采用" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  await screen.findByRole("button", { name: "确认加入动画" });
  fireEvent.change(screen.getByLabelText("开始位置（秒）"), { target: { value: "3.8" } });
  expect(screen.queryByRole("button", { name: "确认加入动画" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "准备采用" })).toBeDisabled();
  expect(api.confirm).not.toHaveBeenCalled();
});

it("requires a new preparation after the task head changes", async () => {
  render(<ResourceVisualAdoption taskId="task" resource={resource} playheadMs={1000} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "准备采用" })).toBeEnabled());
  api.read.mockResolvedValue({ ...workspace, task: { current_timeline_version_id: "v2", timeline_sequence: 2 } });
  fireEvent.click(screen.getByRole("button", { name: "准备采用" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("作品已更新");
  expect(api.preview).not.toHaveBeenCalled();
});

it("adopts a sticker with editable duration and placement while preserving its square source", async () => {
  const sticker: CatalogResource = { ...resource, id: "fluent:sticker-red-heart", name: "爱心 · Fluent", version: 1,
    category_id: "stickers", resource_type: "svg_sticker", preview: { kind: "image", duration_seconds: 3 },
    runtime_requirements: { engine: "openfilm-html-v1", template_id: "builtin_visual_fluent-sticker-red-heart", template_version: 1, width: .22, x: .75, y: .25 } };
  const square = { ...visual, width: 320, height: 320, duration_seconds: 3, source: "original-svg-with-license" };
  api.template.mockResolvedValue({ id: "builtin_visual_fluent-sticker-red-heart", version: 1, settings: { visual: square } });
  render(<ResourceVisualAdoption taskId="task" resource={sticker} playheadMs={1000} />);
  const prepare = screen.getByRole("button", { name: "准备采用" });
  await waitFor(() => expect(prepare).toBeEnabled());
  expect(screen.getByLabelText("宽度（画面 %）")).toHaveValue(22);
  fireEvent.change(screen.getByLabelText("宽度（画面 %）"), { target: { value: "28" } });
  expect(screen.getByLabelText("宽度（画面 %）")).toHaveValue(28);
  fireEvent.change(screen.getByLabelText("显示时长（秒）"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("宽度（画面 %）"), { target: { value: "30" } });
  fireEvent.change(screen.getByLabelText("水平位置（%）"), { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("垂直位置（%）"), { target: { value: "20" } });
  expect(api.preview).not.toHaveBeenCalled();
  fireEvent.click(prepare);
  expect(screen.getByLabelText("宽度（画面 %）")).toBeDisabled();
  await screen.findByRole("button", { name: "确认加入贴纸" });
  expect(api.preview).toHaveBeenCalledWith("task", expect.objectContaining({ graphic_overlay: expect.objectContaining({
    template_id: "builtin_visual_fluent-sticker-red-heart", start_seconds: 1, end_seconds: 3,
    width: .3, x: .6, y: .2, definition: square,
  }) }), { draftOnly: true });
  fireEvent.change(screen.getByLabelText("宽度（画面 %）"), { target: { value: "105" } });
  expect(screen.queryByRole("button", { name: "确认加入贴纸" })).not.toBeInTheDocument();
  expect(prepare).toBeDisabled();
  fireEvent.change(screen.getByLabelText("宽度（画面 %）"), { target: { value: "25" } });
  fireEvent.click(prepare);
  fireEvent.click(await screen.findByRole("button", { name: "确认加入贴纸" }));
  expect(await screen.findByRole("status")).toHaveTextContent("已加入贴纸图层");
  expect(api.confirm).toHaveBeenCalledWith("task", "p1");
});
