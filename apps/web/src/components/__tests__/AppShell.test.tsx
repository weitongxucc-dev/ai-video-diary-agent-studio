import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask } from "@/lib/types";

let pathname = "/";
let search = "";

const apiMocks = vi.hoisted(() => ({
  listCreationTasks: vi.fn(),
}));
const routerPush = vi.fn();

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/image", () => ({
  default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} />,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ back: vi.fn(), push: routerPush }),
  useSearchParams: () => new URLSearchParams(search),
}));

vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: vi.fn() }));
vi.mock("@/lib/api", () => ({ listCreationTasks: apiMocks.listCreationTasks, getRuntimeSettings: vi.fn().mockResolvedValue({status:{llm:{configured:false}}}) }));

import { AppShell } from "@/components/AppShell";

function creationTask(overrides: Partial<CreationTask> = {}): CreationTask {
  return {
    id: "task-1",
    workspace_id: "workspace-1",
    plan_id: "plan-1",
    name: "已保存创作任务",
    scenario: "短视频",
    stage: "topic",
    stage_status: "not_started",
    status: "created",
    progress: 0,
    recovery_point: "选题：未开始",
    selected_topic_id: null,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
    stage_retryable: null,
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
    ...overrides,
    confirmed_script_id: overrides.confirmed_script_id ?? null,
    narration_cache_key: overrides.narration_cache_key ?? null,
    montage_plan_id: overrides.montage_plan_id ?? null,
    current_timeline_version_id: overrides.current_timeline_version_id ?? null,
    timeline_sequence: overrides.timeline_sequence ?? 0,
    can_undo: overrides.can_undo ?? false,
    output_ready: overrides.output_ready ?? false,
  };
}

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1440 });
  pathname = "/";
  search = "";
  apiMocks.listCreationTasks.mockReset();
  routerPush.mockReset();
  apiMocks.listCreationTasks.mockResolvedValue({ items: [], total_count: 0 });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }),
  });
});

describe("AppShell", () => {
  it("keeps conversation visible and opens object details only in a dismissible card", () => {
    const view = render(<AppShell conversation={<p>任务对话</p>} inspector={<p>镜头属性</p>} inspectorTitle="镜头详情" inspectorDismissKey="one"><button data-open-details>镜头一</button></AppShell>);
    expect(screen.getByRole("region", { name: "创作助手" })).toHaveTextContent("任务对话");
    expect(screen.queryByText("镜头属性")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "镜头一" }));
    expect(screen.getByRole("dialog", { name: "镜头详情" })).toHaveTextContent("镜头属性");
    view.rerender(<AppShell conversation={<p>任务对话</p>} inspector={<p>镜头属性</p>} inspectorTitle="镜头详情" inspectorDismissKey="two"><p>预览修改</p></AppShell>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("任务对话")).toBeVisible();
  });

  it("retains editor panel widths and collapse state after remount, within bounds", () => {
    localStorage.clear();
    const first = render(<AppShell editorLayout><p>剪辑</p></AppShell>);
    const divider = screen.getByRole("separator", { name: "调整导航栏宽度" });
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(divider).toHaveAttribute("aria-valuenow", "240");
    for (let i = 0; i < 10; i++) fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(divider).toHaveAttribute("aria-valuenow", "192");
    fireEvent.click(screen.getByRole("button", { name: "收起导航栏" }));
    first.unmount();
    render(<AppShell editorLayout><p>剪辑</p></AppShell>);
    expect(screen.queryByRole("separator", { name: "调整导航栏宽度" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开导航栏" }));
    expect(screen.getByRole("separator", { name: "调整导航栏宽度" })).toHaveAttribute("aria-valuenow", "192");
    localStorage.clear();
  });

  it("keeps the seven product entries and CreationTask links in the left rail", () => {
    render(
      <AppShell
        creationTasks={[
          creationTask({ id: "task-1", name: "产品演示视频", plan_id: "plan-1" }),
          creationTask({ id: "task-2", name: "旅行日记", plan_id: "plan-2" }),
          creationTask({ id: "task-3", name: "人物访谈", plan_id: "plan-3" }),
        ]}
      >
        <p>页面内容</p>
      </AppShell>,
    );

    for (const label of ["首页", "素材库", "内容资料", "人物声音", "视频模板", "定时任务", "发布记录"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "人物声音" })).toHaveAttribute("href", "/profiles");
    expect(screen.getByRole("link", { name: "新建视频" })).toHaveAttribute("href", "/?new=1");
    expect(screen.getByText("创作任务")).toBeInTheDocument();
    expect(screen.getByText("产品演示视频")).toBeInTheDocument();
    expect(screen.getAllByText("等待开始")).not.toHaveLength(0);
    expect(screen.getByRole("link", { name: /产品演示视频/ })).toHaveAttribute("href", "/tasks/task-1");
  });

  it("projects each persisted task stage with its user-facing status", () => {
    render(
      <AppShell creationTasks={[
        creationTask({ id: "not-started", name: "等待任务", stage_status: "not_started" }),
        creationTask({ id: "running", name: "生成任务", stage_status: "running" }),
        creationTask({ id: "waiting", name: "确认任务", stage_status: "waiting_for_user" }),
        creationTask({ id: "completed", name: "完成任务", stage_status: "completed" }),
        creationTask({ id: "failed", name: "处理任务", stage_status: "failed" }),
      ]}>
        <p>页面内容</p>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: /等待任务等待开始/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /生成任务选题处理中/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /确认任务等待确认/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /完成任务已完成/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /处理任务需要处理/ })).toBeInTheDocument();
  });

  it("shows only persisted CreationTasks returned by the task list", async () => {
    apiMocks.listCreationTasks.mockResolvedValue({
      items: [creationTask({ id: "task-2", plan_id: "plan-2", name: "持久化创作任务" })],
      total_count: 1,
    });
    render(<AppShell><p>页面内容</p></AppShell>);

    expect(await screen.findByRole("link", { name: /持久化创作任务/ })).toHaveAttribute("href", "/tasks/task-2");
    expect(screen.queryByText("共存旧创作任务")).not.toBeInTheDocument();
  });

  it("keeps the server-provided task when the creation-task projection request fails", async () => {
    apiMocks.listCreationTasks.mockRejectedValue(new Error("本地服务未接通"));
    render(<AppShell creationTasks={[creationTask({ id: "task-3", name: "当前详情任务", plan_id: "plan-3" })]}><p>页面内容</p></AppShell>);

    await waitFor(() => expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("link", { name: /当前详情任务/ })).toHaveAttribute("href", "/tasks/task-3");
  });

  it("replaces an older task snapshot with the latest list response for the same id", async () => {
    apiMocks.listCreationTasks.mockResolvedValue({
      items: [creationTask({ id: "task-1", name: "当前详情任务", stage_status: "completed", status: "completed" })],
      total_count: 1,
    });
    render(<AppShell creationTasks={[creationTask({ id: "task-1", name: "当前详情任务", stage_status: "not_started" })]}><p>页面内容</p></AppShell>);

    expect(await screen.findByRole("link", { name: /当前详情任务已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /当前详情任务等待开始/ })).not.toBeInTheDocument();
  });

  it("marks only the automatic-tasks entry active for its query route", () => {
    pathname = "/production";
    search = "view=automations";
    render(<AppShell><p>页面内容</p></AppShell>);
    expect(screen.getByRole("link", { name: "定时任务" })).toHaveClass("is-active");
    expect(screen.getByRole("link", { name: "发布记录" })).not.toHaveClass("is-active");
  });

  it("turns both search affordances into one real page and task command palette", () => {
    render(<AppShell creationTasks={[creationTask({ id: "task-search", name: "品牌口播任务" })]}><p>页面内容</p></AppShell>);

    fireEvent.click(screen.getAllByRole("button", { name: "搜索" })[0]);
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索页面和创作任务" }), { target: { value: "品牌口播" } });
    fireEvent.click(screen.getByRole("option", { name: /品牌口播任务/ }));

    expect(routerPush).toHaveBeenCalledWith("/tasks/task-search");
    expect(screen.queryByRole("dialog", { name: "搜索页面和创作任务" })).not.toBeInTheDocument();
  });

  it("places a collapsible quick editor above playback controls without opening the details card", async () => {
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const top = this.hasAttribute("data-editor-transport") ? 560 : this.hasAttribute("data-editor-player") ? 72 : 0;
      const bottom = this.hasAttribute("data-editor-player") ? 536 : 768;
      return { top, bottom, left:0, right:812, width:812, height:bottom - top, x:0, y:top, toJSON:() => ({}) };
    });
    try {
      render(<AppShell editorLayout inspector={<p>完整属性</p>} inspectorTitle="镜头详情" quickInspectorLabel="镜头 1 · 0:00–0:03" quickInspectorKey="task:version:clip" quickInspector={openDetails => <button onClick={openDetails} type="button">打开候选</button>}>
        <div data-editor-player /><div data-editor-transport />
      </AppShell>);
      const panel = await screen.findByRole("region", { name:"镜头 1 · 0:00–0:03" });
      expect(panel).toHaveStyle({ width:"480px", height:"400px", top:"8px", right:"8px" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name:"收起镜头编辑" }));
      expect(screen.queryByRole("region", { name:"镜头 1 · 0:00–0:03" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name:"展开" }));
      fireEvent.click(screen.getByRole("button", { name:"打开候选" }));
      expect(screen.getByRole("dialog", { name:"镜头详情" })).toHaveTextContent("完整属性");
    } finally { rect.mockRestore(); }
  });

  it("starts collapsed when the editor center is narrow and lets the user expand it", async () => {
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const top = this.hasAttribute("data-editor-transport") ? 560 : 0;
      return { top, bottom:768, left:0, right:484, width:484, height:768 - top, x:0, y:top, toJSON:() => ({}) };
    });
    try {
      render(<AppShell editorLayout quickInspectorKey="narrow-clip" quickInspectorLabel="镜头 1" quickInspector={() => <p>快速属性</p>}><div data-editor-player /><div data-editor-transport /></AppShell>);
      expect(screen.queryByRole("region", { name:"镜头 1" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name:"展开" }));
      const panel = await screen.findByRole("region", { name:"镜头 1" });
      expect(panel).toHaveStyle({ width:"400px", height:"400px", top:"8px", right:"8px" });
      fireEvent(window, new Event("resize"));
      await waitFor(() => expect(screen.getByRole("region", { name:"镜头 1" })).toBeInTheDocument());
    } finally { rect.mockRestore(); }
  });
});
