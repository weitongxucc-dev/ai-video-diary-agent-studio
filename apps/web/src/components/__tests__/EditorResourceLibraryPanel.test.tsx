import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditorResourceLibraryPanel } from "../EditorResourceLibraryPanel";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [] }) }));
});

describe("EditorResourceLibraryPanel", () => {
  it("keeps planned resources explicit and never renders an adoption action", async () => {
    render(<EditorResourceLibraryPanel materials={null} initialCategory="filters" />);
    expect(await screen.findByText("滤镜资源计划中")).toBeInTheDocument();
    expect(screen.getByText("滤镜应用能力计划中，当前不能采用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /采用/ })).not.toBeInTheDocument();
  });

  it("shows connected catalog resources with provenance and an editor adoption entry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      categories: [{ id: "stickers", status: "connected" }],
      resources: [{ id: "builtin:stickers", category_id: "stickers", name: "文本与贴纸叠加层", version: 1, status: "connected", resource_type: "overlay", preview: { kind: "overlay" }, provenance: [{ repository: "曜核/AI视频日记", revision: "main", path: "apps/api/app/graphic_overlays.py", license: "internal" }], entrypoints: ["preview_typed_edit", "apply_typed_edit"] }],
    }) }));
    render(<EditorResourceLibraryPanel materials={null} initialCategory="stickers"  />);
    expect(await screen.findByText("文本与贴纸叠加层")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看预览" }));
    expect(screen.getByRole("region", { name: "文本与贴纸叠加层资源预览" })).toHaveTextContent("来源、版本与回滚");
    expect(screen.getByText(/apps\/api\/app\/graphic_overlays\.py/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加到作品" })).not.toBeInTheDocument();
  });

  it("shows an imported skill as a connected, provenance-backed resource", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      categories: [{ id: "skills", status: "connected" }],
      resources: [{ id: "skill:cut-as-code-video-cut", category_id: "skills", name: "cut-as-code-video-cut", version: 1, status: "connected", resource_type: "skill", preview: { kind: "method_document" }, provenance: [{ repository: "https://github.com/WhiteTowerAI/cut-as-code", revision: "a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4", path: "skills/video-cut/SKILL.md", license: "MIT License (repository and documentation)" }], entrypoints: ["read_creation_skill"] }],
    }) }));
    render(<EditorResourceLibraryPanel materials={null} initialCategory="skills" />);
    expect(await screen.findByText("cut-as-code-video-cut")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看预览" }));
    expect(screen.getByText(/a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4/)).toBeInTheDocument();
    expect(screen.getByText(/MIT License/)).toBeInTheDocument();
  });

  it("shows a retryable error instead of treating a catalog failure as empty", async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ categories: [], resources: [] }) });
    vi.stubGlobal("fetch", fetcher);
    render(<EditorResourceLibraryPanel materials={null} initialCategory="skills" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("不会把它当成空目录");
    fireEvent.click(screen.getByRole("button", { name: "重新读取资源目录" }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});

it("renders one video player and never offers an unadapted transition for adoption", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [{ id: "glitch", category_id: "transitions", name: "信号故障", version: 2, status: "catalog_only", resource_type: "css_transition", preview: { kind: "video", thumbnail_ref: "/preview" }, provenance: [], entrypoints: [] }] }) }));
  const { container } = render(<EditorResourceLibraryPanel materials={null} initialCategory="transitions" />);
  await screen.findByText("信号故障");
  expect(container.querySelectorAll("video")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "查看预览" }));
  expect(container.querySelectorAll("video")).toHaveLength(1);
  expect(screen.getByText("可浏览，采用能力尚未接通")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "添加到作品" })).not.toBeInTheDocument();
});

it("passes the selected audio ID and fixed version to the real adoption form", async () => {
  const resource = { id: "music:one", category_id: "music", name: "环境钢琴", version: 3, status: "connected", resource_type: "music", preview: { kind: "audio" }, runtime_requirements: { engine: "EditAudioTimeline" }, provenance: [], entrypoints: ["apply_typed_edit"] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [resource] }) }));
  const action = vi.fn(() => <p>真实采用表单</p>);
  render(<EditorResourceLibraryPanel materials={null} initialCategory="music" renderResourceAction={action} />);
  fireEvent.click(await screen.findByRole("button", { name: "添加到作品" }));
  expect(action).toHaveBeenCalledWith(resource);
  expect(screen.getByText("真实采用表单")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索音乐" }), { target: { value: "不匹配" } });
  expect(screen.getByText("没有匹配的资源，请调整搜索或类型筛选。")).toBeInTheDocument();
});

it("offers the connected original CSS adapter with its fixed template version", async () => {
  const resource = { id: "openfilm:transition-glitch", category_id: "transitions", name: "信号故障", version: 2, status: "connected", resource_type: "css_transition", preview: { kind: "html" }, runtime_requirements: { engine: "openfilm-html-v1", template_id: "builtin_visual_openfilm-transition-glitch", template_version: 1 }, provenance: [], entrypoints: ["apply_typed_edit"] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [resource] }) }));
  const action = vi.fn(() => <p>真实动画采用</p>);
  render(<EditorResourceLibraryPanel materials={null} initialCategory="transitions" renderResourceAction={action} />);
  fireEvent.click(await screen.findByRole("button", { name: "添加到作品" }));
  expect(action).toHaveBeenCalledWith(resource);
  expect(screen.getByText("真实动画采用")).toBeInTheDocument();
});

it("shows the actual SVG sticker preview, attribution and its adoption entry", async () => {
  const resource = { id: "fluent:sticker-red-heart", category_id: "stickers", name: "爱心 · Fluent", version: 1,
    status: "connected", resource_type: "svg_sticker", preview: { kind: "image", thumbnail_ref: "/api/editing-resources/fluent:sticker-red-heart/preview?version=1" },
    runtime_requirements: { engine: "openfilm-html-v1", template_id: "builtin_visual_fluent-sticker-red-heart", template_version: 1, attribution: "Microsoft Fluent Emoji · MIT" },
    provenance: [], entrypoints: ["apply_typed_edit"] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [resource] }) }));
  const action = vi.fn(() => <p>贴纸采用表单</p>);
  render(<EditorResourceLibraryPanel materials={null} initialCategory="stickers" renderResourceAction={action} />);
  expect(await screen.findByRole("img", { name: "爱心 · Fluent" })).toHaveAttribute("src", resource.preview.thumbnail_ref);
  fireEvent.click(screen.getByRole("button", { name: "查看预览" }));
  expect(screen.getByRole("img", { name: "预览：爱心 · Fluent" })).toHaveAttribute("src", resource.preview.thumbnail_ref);
  expect(screen.getByText("Microsoft Fluent Emoji · MIT")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "添加到作品" }));
  expect(action).toHaveBeenCalledWith(resource);
});
