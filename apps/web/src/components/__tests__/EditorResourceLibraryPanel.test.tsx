import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EditorResourceLibraryPanel } from "../EditorResourceLibraryPanel";

const readScope = vi.hoisted(() => vi.fn());
vi.mock("@/lib/preflight-draft", () => ({ readPreflightDraftScope: readScope }));

beforeEach(() => {
  sessionStorage.clear();
  readScope.mockReset().mockResolvedValue({ workspaceId: "space-a", memberId: "member-a" });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ categories: [], resources: [] }) }));
});

afterEach(() => vi.restoreAllMocks());

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

  it("shows an audio preview error only for the open resource version and allows a fresh preview", async () => {
    const glass = { id: "openfilm:sound-glass-pulse", category_id: "sfx", name: "玻璃脉冲",
      version: 1, status: "connected", resource_type: "audio", provenance: [],
      runtime_requirements: { engine: "EditAudioTimeline" },
      preview: { kind: "audio", thumbnail_ref: "/preview/glass?version=1" },
    };
    const resources = [glass, { ...glass, version: 2,
      preview: { ...glass.preview, thumbnail_ref: "/preview/glass?version=2" } }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      categories: [], resources,
    }) }));
    render(<EditorResourceLibraryPanel materials={null} initialCategory="sfx" renderResourceAction={() => null} />);
    const first = (await screen.findByText("可用 · v1")).closest("article")!;
    const second = screen.getByText("可用 · v2").closest("article")!;

    fireEvent.click(within(first).getByRole("button", { name: "查看预览" }));
    const failingAudio = within(first).getByLabelText("预览：玻璃脉冲");
    expect(failingAudio).toHaveAttribute("src", glass.preview.thumbnail_ref);
    fireEvent.error(failingAudio);
    expect(within(first).getByRole("status")).toHaveTextContent("暂时无法试听此版本");
    expect(within(first).getByRole("button", { name: "添加到作品" })).toBeEnabled();

    fireEvent.click(within(second).getByRole("button", { name: "查看预览" }));
    expect(within(first).queryByRole("status")).not.toBeInTheDocument();
    const playableAudio = within(second).getByLabelText("预览：玻璃脉冲");
    expect(playableAudio).toHaveAttribute("src", "/preview/glass?version=2");
    fireEvent.canPlay(playableAudio);
    expect(within(second).getByRole("status")).toBeEmptyDOMElement();

    fireEvent.click(within(first).getByRole("button", { name: "查看预览" }));
    expect(within(first).getByRole("status")).toBeEmptyDOMElement();
    expect(within(first).getByLabelText("预览：玻璃脉冲")).not.toBe(failingAudio);
  });

  it("distinguishes usable methods, external references, and planned skills without offering execution", async () => {
    const method = { id: "skill:cut-as-code-video-cut", category_id: "skills", name: "cut-as-code-video-cut", version: 1,
      status: "connected", resource_type: "skill", preview: { kind: "method_document" },
      provenance: [{ repository: "https://github.com/WhiteTowerAI/cut-as-code", revision: "a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4", path: "skills/video-cut/SKILL.md", license: "MIT License" }],
      entrypoints: ["read_creation_skill"], runtime_requirements: {
        execution_mode: "method-only", runnable: false, manifest_version: "2026.11.13-adapter.1",
        capabilities: { inputs: ["授权的镜头"], outputs: ["编辑候选"], permissions: ["读取当前任务和授权对象"] },
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      categories: [{ id: "skills", status: "connected" }],
      resources: [method,
        { ...method, id: "skill:ffmpeg-skill", name: "ffmpeg-skill", status: "catalog_only",
          runtime_requirements: { ...method.runtime_requirements, status_reason: "当前版本没有接入外部执行器" } },
        { ...method, id: "skill:future", name: "未来技能", status: "planned", runtime_requirements: {} },
      ],
    }) }));
    const resourceAction = vi.fn();
    render(<EditorResourceLibraryPanel materials={null} initialCategory="skills" renderResourceAction={resourceAction} />);
    const available = within(await screen.findByRole("article", { name: method.name }));
    expect(available.getByText("可用方法")).toBeVisible();
    expect(available.getByText("授权的镜头")).not.toBeVisible();
    fireEvent.click(available.getByText("查看详情"));
    expect(available.getByText("版本 2026.11.13-adapter.1")).toBeVisible();
    expect(available.getByText("授权的镜头")).toBeVisible();
    expect(available.getByText("编辑候选")).toBeVisible();
    expect(available.getByText("读取当前任务和授权对象")).toBeVisible();
    expect(available.getByText(/a96c13e6eaa72e6882e78df08ee4c04fa7be3bc4.*MIT License/)).toBeVisible();
    const reference = within(screen.getByRole("article", { name: "ffmpeg-skill" }));
    expect(reference.getByText("仅供参考")).toBeVisible();
    fireEvent.click(reference.getByText("查看详情"));
    expect(reference.getByText("当前版本没有接入外部执行器")).toBeVisible();
    expect(within(screen.getByRole("article", { name: "未来技能" })).getByText("计划中")).toBeVisible();
    expect(screen.queryByRole("button", { name: /运行|执行|添加到作品/ })).not.toBeInTheDocument();
    expect(resourceAction).not.toHaveBeenCalled();
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

it("restores separate category filters across A → B → A and a refresh, without sharing workspaces", async () => {
  const panel = <EditorResourceLibraryPanel materials={null} initialCategory="stickers" />;
  const search = (name: string) => screen.getByRole("searchbox", { name: `搜索${name}` });
  const filter = () => screen.getByRole("combobox", { name: "资源类型筛选" });
  const view = render(panel);
  await waitFor(() => expect(search("贴纸")).toBeEnabled());
  fireEvent.change(search("贴纸"), { target: { value: "爱心" } });
  fireEvent.change(filter(), { target: { value: "image" } });
  fireEvent.click(screen.getByRole("button", { name: /^特效/ }));
  expect(search("特效")).toHaveValue("");
  expect(filter()).toHaveValue("all");
  fireEvent.change(search("特效"), { target: { value: "淡入" } });
  fireEvent.change(filter(), { target: { value: "video" } });
  fireEvent.click(screen.getByRole("button", { name: /^贴纸/ }));
  expect(search("贴纸")).toHaveValue("爱心");
  expect(filter()).toHaveValue("image");

  view.unmount();
  const refreshed = render(panel);
  await waitFor(() => expect(search("贴纸")).toHaveValue("爱心"));
  expect(filter()).toHaveValue("image");
  fireEvent.click(screen.getByRole("button", { name: /^特效/ }));
  expect(search("特效")).toHaveValue("淡入");
  expect(filter()).toHaveValue("video");

  refreshed.unmount();
  readScope.mockResolvedValue({ workspaceId: "space-b", memberId: "member-a" });
  render(panel);
  await waitFor(() => expect(search("贴纸")).toBeEnabled());
  expect(search("贴纸")).toHaveValue("");
  expect(filter()).toHaveValue("all");
});

it("keeps category filters in memory when both storage reads and writes throw", async () => {
  const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  render(<EditorResourceLibraryPanel materials={null} initialCategory="stickers" />);
  const search = () => screen.getByRole("searchbox", { name: "搜索贴纸" });
  await waitFor(() => expect(search()).toBeEnabled());
  fireEvent.change(search(), { target: { value: "爱心" } });
  expect(read).toHaveBeenCalled();
  expect(write).toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^特效/ }));
  fireEvent.click(screen.getByRole("button", { name: /^贴纸/ }));
  expect(search()).toHaveValue("爱心");
});

it("shows failed catalog entries as failed and withholds the adoption action", async () => {
  const resource = { id: "sfx:failed", category_id: "sfx", name: "损坏的音效", version: 1, status: "failed",
    resource_type: "sound", preview: { kind: "audio" }, runtime_requirements: { engine: "EditAudioTimeline" },
    provenance: [], entrypoints: ["apply_typed_edit"] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    categories: [{ id: "sfx", status: "failed" }], resources: [resource],
  }) }));
  render(<EditorResourceLibraryPanel materials={null} initialCategory="sfx" renderResourceAction={() => <p>采用表单</p>} />);
  expect(await screen.findByText("失败 · v1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "音效失败" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("资源失败，暂不可采用")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "添加到作品" })).not.toBeInTheDocument();
});
