import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useContext, useState } from "react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask } from "@/lib/types";

let pathname = "/";
let search = "";

const apiMocks = vi.hoisted(() => ({
  listLocalMembers: vi.fn().mockResolvedValue({members:[{member_id:"member-a",name:"测试创作者",role:"owner",created_at:"2026-10-10"}]}),
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
vi.mock("@/lib/api", () => ({
  listLocalMembers: apiMocks.listLocalMembers,
  listCreationTasks: apiMocks.listCreationTasks,
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }),
  getCurrentAssistantConversation: vi.fn(async (scope: import("@/lib/types").AssistantConversationScope) => ({
    id: `conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "workspace-1", ...scope,
    title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
  } satisfies import("@/lib/types").AssistantConversation)),
  listAssistantConversations: vi.fn().mockResolvedValue({ items: [] }),
  getAssistantCapabilities: vi.fn().mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    runtime: { owner: "pi-coding-agent-sdk", version: "1.3.0", status: "ready", reason: "已就绪" },
    pi_tui_commands: { available: false, reason: "终端命令不在此运行", commands: [] },
    extension_commands: { available: false, reason: "未接通" }, skills: { available: true, reason: "按需加载", names: ["local-edit"] }, prompt_templates: { available: true, reason: "按需加载", names: ["review-result"] }, business_tools: {},
  } satisfies import("@/lib/types").AssistantCapabilities),
}));


import { AppShell } from "@/components/AppShell";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { MediaVisibilityContext } from "@/components/ui/media-visibility";

function creationTask(overrides: Partial<CreationTask> = {}): CreationTask {
  return {
    visibility: "private",
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

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(document, "hidden");
});

describe("AppShell", () => {

  it("keeps workspace labels in the device settings title and restores its default", () => {
    const view = render(<AppShell workspaceStatus={{ label: "需要处理" }}><p>当前工作区</p></AppShell>);
    const settings = screen.getByRole("link", { name: "设备设置" });
    expect(settings).toHaveAttribute("href", "/settings");
    expect(settings).toHaveAttribute("title", "设备设置 · 需要处理");
    view.rerender(<AppShell workspaceStatus={{ label: "工作区已就绪" }}><p>当前工作区</p></AppShell>);
    expect(settings).toHaveAttribute("title", "设备设置 · 工作区已就绪");
    view.rerender(<AppShell><p>当前工作区</p></AppShell>);
    expect(settings).toHaveAttribute("title", "设备设置");
  });

  it("shows the approved Yaohe brand while retaining home navigation", () => {
    render(<AppShell><p>当前工作区</p></AppShell>);
    const home = screen.getByRole("link", { name: "曜核AI剪辑，返回首页" });
    expect(home).toHaveAttribute("href", "/");
    expect(within(home).getByText("曜核AI剪辑")).toBeInTheDocument();
    expect(home.querySelector("img")).toHaveAttribute("src", "/yaohe-icon.png");
    expect(screen.queryByText("智能视频日记")).not.toBeInTheDocument();
  });

  it("keeps conversation visible and opens object properties in a dismissible dock", () => {
    const view = render(<AppShell conversation={<p>任务对话</p>} inspector={<p>镜头属性</p>} inspectorTitle="镜头详情" inspectorDismissKey="one"><button data-open-details>镜头一</button></AppShell>);
    expect(screen.getByRole("region", { name: "创作助手" })).toHaveTextContent("任务对话");
    expect(screen.getByText("镜头属性")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "镜头一" }));
    expect(screen.getByRole("complementary", { name: "镜头详情" })).toHaveTextContent("镜头属性");
    view.rerender(<AppShell conversation={<p>任务对话</p>} inspector={<p>镜头属性</p>} inspectorTitle="镜头详情" inspectorDismissKey="two"><p>预览修改</p></AppShell>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("任务对话")).toBeVisible();
  });

  it("opens one quick card, transfers its confirmation to details, and retains conversation", () => {
    const quick = (openDetails: () => void) => <><p>快捷参数</p><button onClick={openDetails}>转到完整详情</button></>;
    const props = { editorLayout: true, conversation: <p>已有对话草稿</p>, inspector: <p>完整镜头属性</p>, inspectorTitle: "镜头详情", quickInspector: quick, quickInspectorKey: "clip-1", quickInspectorLabel: "镜头快捷编辑" };
    const view = render(<AppShell {...props}><p>剪辑</p></AppShell>);
    fireEvent.click(screen.getByRole("button", { name: "编辑镜头参数" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.getByRole("dialog", { name: "镜头快捷编辑" })).toHaveTextContent("快捷参数");
    view.rerender(<AppShell {...props} inspectorConfirmationKey="preview-1" inspectorConfirmation={<button>采用参数提案</button>}><p>剪辑</p></AppShell>);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "采用参数提案" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "转到完整详情" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "镜头详情" })).toHaveTextContent("完整镜头属性");
    expect(screen.getByRole("complementary",{name:"镜头详情"})).toHaveTextContent("采用参数提案");
    expect(screen.getByText("已有对话草稿")).toBeVisible();
  });

  it("retains editor panel widths and collapse state after remount, within bounds", () => {
    localStorage.clear();
    const first = render(<AppShell editorLayout><p>剪辑</p></AppShell>);
    const divider = screen.getByRole("separator", { name: "调整导航栏宽度" });
    fireEvent.click(divider);
    expect(divider).toHaveFocus();
    fireEvent.keyDown(divider, { key: "ArrowRight" });
    expect(divider).toHaveAttribute("aria-valuenow", "216");
    fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(divider).toHaveAttribute("aria-valuenow", "200");
    for (let i = 0; i < 10; i++) fireEvent.keyDown(divider, { key: "ArrowLeft" });
    expect(divider).toHaveAttribute("aria-valuenow", "160");
    fireEvent.click(screen.getByRole("button", { name: "收起导航栏" }));
    first.unmount();
    render(<AppShell editorLayout><p>剪辑</p></AppShell>);
    expect(screen.queryByRole("separator", { name: "调整导航栏宽度" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开导航栏" }));
    expect(screen.getByRole("separator", { name: "调整导航栏宽度" })).toHaveAttribute("aria-valuenow", "160");
    localStorage.clear();
  });

  it("collapses and restores all three columns, and keeps the collapsed panels mounted", () => {
    localStorage.clear();
    const view = render(<AppShell editorLayout conversation={<p>对话</p>}><p>剪辑</p></AppShell>);
    const shell = view.container.firstElementChild as HTMLElement;
    const grid = view.container.querySelector('[class*="contentGrid"]') as HTMLElement;
    expect(shell).toHaveStyle({ "--editor-left": "200px", "--editor-right": "380px" });
    fireEvent.click(screen.getByRole("button", { name: "收起导航栏" }));
    expect(shell).toHaveStyle({ "--editor-left": "56px" });
    fireEvent.click(screen.getByRole("button", { name: "收起对话栏" }));
    expect(shell).toHaveStyle({ "--editor-right": "44px" });
    expect(grid.className.split(" ")).toContainEqual(expect.stringContaining("chatCollapsed"));
    fireEvent.click(screen.getByRole("button", { name: "布局" }));
    fireEvent.click(screen.getByRole("button", { name: "作品区" }));
    expect(grid.className.split(" ")).toContainEqual(expect.stringContaining("workspaceCollapsed"));
    // 三栏全收起后仍能逐个找回，且收起不卸载组件
    expect(screen.getByRole("button", { name: "展开导航栏" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开对话栏" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "布局" })).toBeInTheDocument();
    expect(screen.getByText("剪辑")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "布局" }));
    fireEvent.click(screen.getByRole("button", { name: "作品区" }));
    fireEvent.click(screen.getByRole("button", { name: "展开对话栏" }));
    expect(shell).toHaveStyle({ "--editor-right": "380px" });
    expect(grid.className).not.toContain("chatCollapsed");
    fireEvent.click(screen.getByRole("button", { name: "展开导航栏" }));
    expect(shell).toHaveStyle({ "--editor-left": "200px" });
    expect(screen.getByRole("separator", { name: "调整对话栏宽度" })).toHaveAttribute("aria-valuenow", "380");
    view.unmount();
    localStorage.clear();
  });

  it("restores the conversation width that was set before collapsing", () => {
    localStorage.clear();
    const view = render(<AppShell editorLayout conversation={<p>对话</p>}><p>剪辑</p></AppShell>);
    const shell = view.container.firstElementChild as HTMLElement;
    fireEvent.keyDown(screen.getByRole("separator", { name: "调整对话栏宽度" }), { key: "ArrowRight" });
    fireEvent.click(screen.getByRole("button", { name: "收起对话栏" }));
    expect(shell).toHaveStyle({ "--editor-right": "44px" });
    fireEvent.click(screen.getByRole("button", { name: "展开对话栏" }));
    expect(screen.getByRole("separator", { name: "调整对话栏宽度" })).toHaveAttribute("aria-valuenow", "396");
    view.unmount();
    localStorage.clear();
  });

  it("restores default widths with Home without losing mounted drafts", () => {
    localStorage.clear();
    localStorage.setItem("video-editor-left-width", "260");
    localStorage.setItem("video-workspace-chat-width", "460");
    const view = render(<AppShell conversation={<input aria-label="对话草稿" defaultValue="待发送"/>}><p>作品</p></AppShell>);
    fireEvent.keyDown(screen.getByRole("separator", {name:"调整导航栏宽度"}), {key:"Home"});
    fireEvent.keyDown(screen.getByRole("separator", {name:"调整对话栏宽度"}), {key:"Home"});
    expect(localStorage.getItem("video-workspace-chat-width")).toBeNull();
    expect(screen.getByLabelText("对话草稿")).toHaveValue("待发送");
    view.unmount();
    const restored = render(<AppShell><p>作品</p></AppShell>);
    expect(restored.container.firstElementChild).toHaveStyle({"--editor-left":"200px", "--editor-right":"380px"});
    restored.unmount(); localStorage.clear();
  });

  it("keeps the conversation and workspace collapse state after remount", () => {
    localStorage.clear();
    const first = render(<AppShell editorLayout conversation={<p>对话</p>}><p>剪辑</p></AppShell>);
    fireEvent.click(screen.getByRole("button", { name: "收起对话栏" }));
    fireEvent.click(screen.getByRole("button", { name: "布局" }));
    fireEvent.click(screen.getByRole("button", { name: "作品区" }));
    first.unmount();
    const second = render(<AppShell editorLayout conversation={<p>对话</p>}><p>剪辑</p></AppShell>);
    expect(screen.getByRole("button", { name: "展开对话栏" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "布局" })).toBeInTheDocument();
    const grid = second.container.querySelector('[class*="contentGrid"]') as HTMLElement;
    expect(grid.className.split(" ")).toContainEqual(expect.stringContaining("chatCollapsed"));
    expect(grid.className.split(" ")).toContainEqual(expect.stringContaining("workspaceCollapsed"));
    second.unmount();
    localStorage.clear();
  });

  it("uses approved desktop widths for a fresh workspace and keeps a previously saved layout", () => {
    localStorage.clear();
    const first = render(<AppShell conversation={<p>对话</p>}><p>首页</p></AppShell>);
    expect(first.container.firstElementChild).toHaveStyle({ "--editor-left": "200px", "--editor-right": "380px" });
    first.unmount();
    localStorage.setItem("video-editor-left-width", "224");
    localStorage.setItem("video-workspace-chat-width", "350");
    const saved = render(<AppShell conversation={<p>对话</p>}><p>首页</p></AppShell>);
    expect(saved.container.firstElementChild).toHaveStyle({ "--editor-left": "224px", "--editor-right": "350px" });
    saved.unmount();
    localStorage.clear();
  });

  it("keeps the six product entries and CreationTask links in the left rail", () => {
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

    for (const label of ["首页", "素材库", "知识库", "人物与声音", "视频模板", "发布与数据"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("link", { name: "成员与空间" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "设备设置" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("button", { name: "创作者与空间菜单" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "人物与声音" })).toHaveAttribute("href", "/profiles");
    expect(screen.getByRole("link", { name: "新建视频" })).toHaveAttribute("href", "/?new=1");
    expect(screen.getByText("创作任务")).toBeInTheDocument();
    expect(screen.getByText("产品演示视频")).toBeInTheDocument();
    expect(screen.queryByText("等待开始")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /产品演示视频/ })).toHaveAttribute("href", "/tasks/task-1");
    expect(screen.getByRole("link", { name: "定时任务" })).toHaveAttribute("href", "/production?view=automations");
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

    expect(screen.getByRole("link", { name: /等待任务.*选题待开始/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /生成任务.*正在选题/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /确认任务.*选题待确认/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /完成任务.*已完成/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /处理任务.*选题未生成/ })).toBeInTheDocument();
    for (const name of ["等待任务", "生成任务", "确认任务", "完成任务", "处理任务"]) {
      const row = screen.getByRole("link", { name: new RegExp(name) });
      expect(row.querySelector('svg[data-task-stage="topic"]')).toHaveClass("lucide-lightbulb");
      expect(row.querySelector("small")).toBeVisible();
      expect(row.querySelectorAll(".is-spinning")).toHaveLength(name === "生成任务" ? 1 : 0);
    }
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
    expect(await screen.findByRole("alert")).toHaveTextContent("已有任务仍可打开");
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取创作任务" })).toBeInTheDocument();
  });

  it("replaces an older task snapshot with the latest list response for the same id", async () => {
    apiMocks.listCreationTasks.mockResolvedValue({
      items: [creationTask({ id: "task-1", name: "当前详情任务", stage_status: "completed", status: "completed" })],
      total_count: 1,
    });
    render(<AppShell creationTasks={[creationTask({ id: "task-1", name: "当前详情任务", stage_status: "not_started" })]}><p>页面内容</p></AppShell>);

    expect(await screen.findByRole("link", { name: /当前详情任务.*已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /当前详情任务.*选题待开始/ })).not.toBeInTheDocument();
  });

  it("refreshes a background running task across navigation and stops after its persisted failure", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    const running = creationTask({ status: "running", stage_status: "running" });
    const failed = creationTask({ status: "failed", stage_status: "failed", updated_at: "2026-10-10T02:34:00Z" });
    apiMocks.listCreationTasks.mockResolvedValue({ items: [running], total_count: 1 });
    const view = render(<AppShell creationTasks={[running]} conversation={<p>对话</p>}><p>任务页</p></AppShell>);
    await act(async () => {});
    pathname = "/production";
    search = "view=automations";
    view.rerender(<AppShell conversation={<p>对话</p>}><p>定时页</p></AppShell>);
    await act(async () => {});
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("link", { name: /已保存创作任务.*正在选题/ })).toBeInTheDocument();
    apiMocks.listCreationTasks.mockResolvedValue({ items: [failed], total_count: 1 });
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.getByRole("link", { name: /已保存创作任务.*选题未生成/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /正在选题/ })).not.toBeInTheDocument();
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(3);
  });

  it("pauses running-task refresh while hidden and never overlaps background reads", async () => {
    vi.useFakeTimers();
    let hidden = true;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    const running = creationTask({ status: "running", stage_status: "running" });
    apiMocks.listCreationTasks.mockResolvedValue({ items: [running], total_count: 1 });
    render(<AppShell conversation={<p>对话</p>}><p>定时页</p></AppShell>);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(1);
    let resolve!: (value: { items: CreationTask[]; total_count: number }) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise(finish => { resolve = finish; }));
    hidden = false;
    fireEvent(document, new Event("visibilitychange"));
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(2);
    await act(async () => resolve({ items: [creationTask({ status: "failed", stage_status: "failed" })], total_count: 1 }));
    expect(screen.getByRole("link", { name: /选题未生成/ })).toBeInTheDocument();
  });

  it("retains the last task fact with a read error and retries only while it is running", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    const running = creationTask({ status: "running", stage_status: "running" });
    apiMocks.listCreationTasks.mockResolvedValueOnce({ items: [running], total_count: 1 })
      .mockRejectedValueOnce(new Error("本地任务状态读取失败"))
      .mockResolvedValue({ items: [creationTask({ status: "failed", stage_status: "failed" })], total_count: 1 });
    render(<AppShell conversation={<p>对话</p>}><p>页面</p></AppShell>);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.getByRole("alert")).toHaveTextContent("已有任务仍可打开");
    expect(screen.getByRole("link", { name: /正在选题/ })).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /选题未生成/ })).toBeInTheDocument();
  });

  it("does not restore an older running page snapshot after the server has reported failure", async () => {
    const failed = creationTask({ status: "failed", stage_status: "failed", updated_at: "2026-10-10T02:34:00Z" });
    apiMocks.listCreationTasks.mockResolvedValue({ items: [failed], total_count: 1 });
    const view = render(<AppShell conversation={<p>对话</p>}><p>页面</p></AppShell>);
    expect(await screen.findByRole("link", { name: /选题未生成/ })).toBeInTheDocument();
    view.rerender(<AppShell creationTasks={[creationTask({ status: "running", stage_status: "running" })]} conversation={<p>对话</p>}><p>页面</p></AppShell>);
    expect(screen.getByRole("link", { name: /选题未生成/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /正在选题/ })).not.toBeInTheDocument();
  });

  it("labels the connected automations URL without a permanent primary navigation entry", () => {
    pathname = "/production";
    search = "view=automations";
    render(<AppShell><p>页面内容</p></AppShell>);
    expect(screen.getByRole("link", { name: "定时任务" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "定时任务" })).toHaveAttribute("href", "/production?view=automations");
    expect(screen.getByRole("link", { name: "发布与数据" })).not.toHaveClass("is-active");
  });

  it("keeps scheduled tasks discoverable in search with the confirmed execution boundary", () => {
    render(<AppShell conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    const palette = screen.getByRole("dialog", { name: "搜索页面和创作任务" });
    const preview = within(palette).getByRole("option", { name: /定时任务/ });
    expect(preview).toHaveTextContent("安排到期时间，查看待确认步骤与执行记录");
    expect(within(palette).queryByRole("option", { name: /定时定义预览/ })).not.toBeInTheDocument();
    fireEvent.click(preview);
    expect(routerPush).toHaveBeenCalledWith("/production?view=automations");
  });

  it("shows loading rather than an empty list until the actual read returns empty", async () => {
    let resolve!: (value: { items: CreationTask[]; total_count: number }) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise(finish => { resolve = finish; }));
    render(<AppShell conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取创作任务…");
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
    await act(async () => resolve({ items: [], total_count: 0 }));
    expect(screen.getByText("还没有创作任务")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps known server tasks visible during a slow refresh", async () => {
    let resolve!: (value: { items: CreationTask[]; total_count: number }) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise(finish => { resolve = finish; }));
    render(<AppShell creationTasks={[creationTask()]} conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    expect(screen.getByRole("link", { name: /已保存创作任务/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在更新创作任务…");
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
    await act(async () => resolve({ items: [creationTask({ stage_status: "completed" })], total_count: 1 }));
    expect(screen.getByRole("link", { name: /已保存创作任务.*已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("distinguishes failure from empty and retries the original task read", async () => {
    apiMocks.listCreationTasks.mockRejectedValueOnce(new Error("合成读取故障"));
    render(<AppShell conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取创作任务");
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
    apiMocks.listCreationTasks.mockResolvedValueOnce({ items: [creationTask()], total_count: 1 });
    fireEvent.click(screen.getByRole("button", { name: "重新读取创作任务" }));
    expect(await screen.findByRole("link", { name: /已保存创作任务/ })).toBeInTheDocument();
    expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("preserves known tasks throughout failed and slow retries", async () => {
    apiMocks.listCreationTasks.mockRejectedValueOnce(new Error("合成读取故障"));
    render(<AppShell creationTasks={[creationTask()]} conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    await screen.findByRole("alert");
    let resolve!: (value: { items: CreationTask[]; total_count: number }) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise(finish => { resolve = finish; }));
    fireEvent.click(screen.getByRole("button", { name: "重新读取创作任务" }));
    expect(screen.getByRole("link", { name: /已保存创作任务/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在更新创作任务");
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
    await act(async () => resolve({ items: [creationTask({ stage_status: "completed" })], total_count: 1 }));
    expect(screen.getByRole("link", { name: /已保存创作任务.*已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["success", "failure"])("retains task facts while the in-flight complete collection read settles with %s", async outcome => {
    let resolve!: (value: { items: CreationTask[]; total_count: number }) => void;
    let reject!: (cause: Error) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise((finish, fail) => { resolve = finish; reject = fail; }));
    const initial = [creationTask({ name: "旧快照任务" })];
    const view = render(<AppShell creationTasks={initial} conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    const updated = [creationTask({ name: "新快照任务", stage_status: "completed", status: "completed" })];
    view.rerender(<AppShell creationTasks={updated} conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    await act(async () => {
      if (outcome === "success") resolve({ items: initial, total_count: 1 });
      else reject(new Error("过期读取故障"));
    });
    expect(screen.getByRole("link", { name: /新快照任务.*已完成/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /旧快照任务/ })).not.toBeInTheDocument();
    if (outcome === "failure") expect(screen.getByRole("alert")).toBeInTheDocument();
    else expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("还没有创作任务")).not.toBeInTheDocument();
  });

  it("does not let a late failed read replace a newer complete empty GET snapshot", async () => {
    let reject!: (cause: Error) => void;
    apiMocks.listCreationTasks.mockReturnValueOnce(new Promise((_finish, fail) => { reject = fail; }))
      .mockResolvedValueOnce({ items: [], total_count: 0 });
    const view = render(<AppShell conversation={<p>对话</p>}><p>页面内容</p></AppShell>);
    pathname = "/history";
    view.rerender(<AppShell conversation={<p>对话</p>}><p>素材</p></AppShell>);
    await screen.findByText("还没有创作任务");
    await act(async () => reject(new Error("过期读取故障")));
    expect(screen.getByText("还没有创作任务")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it.each(["remaining", "empty"])("removes stale task membership after a complete %s GET", async result => {
    let finish!: (value: { items: CreationTask[]; total_count: number }) => void;
    const first = creationTask({ id: "task-a", name: "旧可读任务" });
    const second = creationTask({ id: "task-b", name: "保留任务" });
    apiMocks.listCreationTasks.mockResolvedValueOnce({ items: [first, second], total_count: 2 })
      .mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const view = render(<AppShell conversation={<p>对话</p>}><p>首页</p></AppShell>);
    await screen.findByRole("link", { name: /旧可读任务/ });
    pathname = "/history";
    view.rerender(<AppShell conversation={<p>对话</p>}><p>素材</p></AppShell>);
    await waitFor(() => expect(apiMocks.listCreationTasks).toHaveBeenCalledTimes(2));
    await act(async () => finish({ items: result === "empty" ? [] : [second], total_count: result === "empty" ? 0 : 1 }));
    expect(screen.queryByRole("link", { name: /旧可读任务/ })).not.toBeInTheDocument();
    if (result === "empty") expect(screen.getByText("还没有创作任务")).toBeInTheDocument();
    else expect(screen.getByRole("link", { name: /保留任务/ })).toBeInTheDocument();
    view.rerender(<AppShell creationTasks={[first]} conversation={<p>对话</p>}><p>素材</p></AppShell>);
    expect(screen.queryByRole("link", { name: /旧可读任务/ })).not.toBeInTheDocument();
  });

  it("applies finite task facts without replacing full membership or hiding its read error", async () => {
    const first = creationTask({ id: "task-a", name: "任务 A" });
    const second = creationTask({ id: "task-b", name: "任务 B" });
    apiMocks.listCreationTasks.mockResolvedValueOnce({ items: [first, second], total_count: 2 })
      .mockRejectedValueOnce(new Error("完整列表读取失败"));
    const view = render(<AppShell conversation={<p>对话</p>}><p>首页</p></AppShell>);
    await screen.findByRole("link", { name: /任务 B/ });
    pathname = "/history";
    view.rerender(<AppShell conversation={<p>对话</p>}><p>素材</p></AppShell>);
    await screen.findByRole("alert");
    view.rerender(<AppShell creationTasks={[{ ...first, name: "任务 A 新事实", updated_at: "2026-10-14T01:00:00Z" }]} conversation={<p>对话</p>}><p>素材</p></AppShell>);
    expect(screen.getByRole("link", { name: /任务 A 新事实/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /任务 B/ })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    view.rerender(<AppShell creationTasks={[]} conversation={<p>对话</p>}><p>素材</p></AppShell>);
    expect(screen.getByRole("link", { name: /任务 B/ })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
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

  describe("standard object overlays", () => {
    it("does not open details or parameters when a shot is selected", async () => {
      render(<AppShell editorLayout quickInspector={() => <input aria-label="快速草稿" />} quickInspectorLabel="镜头参数" quickInspectorKey="one" inspector={<input aria-label="镜头草稿" defaultValue="保留" />} inspectorTitle="镜头详情" conversation={<p>任务对话</p>}><video /><button data-editor-transport>播放</button></AppShell>);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", {name:"编辑镜头参数"}));
      expect(screen.queryByRole("dialog", {name:"镜头详情"})).not.toBeInTheDocument();
      expect(screen.getByRole("dialog", {name:"镜头参数"})).toHaveAttribute("aria-modal","true");
      fireEvent.click(screen.getByRole("button", {name:"关闭卡片"}));
      await waitFor(() => expect(screen.queryByRole("dialog", {name:"镜头参数"})).not.toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", {name:"查看详情"}));
      fireEvent.change(screen.getByLabelText("镜头草稿"), {target:{value:"新草稿"}});
      fireEvent.click(screen.getByRole("button", {name:"关闭属性面板"}));
      fireEvent.click(screen.getByRole("button", {name:"查看详情"}));
      expect(screen.getByLabelText("镜头草稿")).toHaveValue("新草稿");
      expect(screen.getByRole("button", {name:"播放"})).toBeInTheDocument();
    });
    it("keeps the quick dialog as the proposal parent while preserving its draft on close", async () => {
      function ConfirmationSurface() {
        const [confirmationOpen, setConfirmationOpen] = useState(false);
        return <AppShell editorLayout quickInspector={() => <><input aria-label="快速草稿" defaultValue="待保存" /><button onClick={() => setConfirmationOpen(true)} type="button">确认草稿</button></>} quickInspectorLabel="镜头参数" quickInspectorKey="one" inspector={<input aria-label="详情草稿" defaultValue="详情" />} inspectorTitle="镜头详情" inspectorConfirmationKey={confirmationOpen ? "proposal-1" : undefined} inspectorConfirmation={confirmationOpen ? <HomeCardDialog kind="config" title="选择应用到草稿" onClose={() => setConfirmationOpen(false)}><button data-card-cancel type="button">返回修改</button><button type="button">应用到草稿</button></HomeCardDialog> : null} conversation={<p>任务对话</p>}><video /></AppShell>;
      }
      render(<ConfirmationSurface />);
      fireEvent.click(screen.getByRole("button", { name: "编辑镜头参数" }));
      const quick = screen.getByRole("dialog", { name: "镜头参数" });
      fireEvent.change(screen.getByLabelText("快速草稿"), { target: { value: "保留快速草稿" } });
      fireEvent.click(screen.getByRole("button", { name: "确认草稿" }));
      expect(screen.queryByRole("dialog", { name: "镜头详情" })).not.toBeInTheDocument();
      expect(quick).toBeInTheDocument();
      expect(screen.getByRole("dialog", { name: "选择应用到草稿" })).toHaveAttribute("aria-modal", "true");
      fireEvent.click(within(screen.getByRole("dialog", { name: "选择应用到草稿" })).getByRole("button", { name: "关闭卡片" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "选择应用到草稿" })).not.toBeInTheDocument());
      expect(screen.getByRole("dialog", { name: "镜头参数" })).toBe(quick);
      expect(screen.getByLabelText("快速草稿")).toHaveValue("保留快速草稿");
    });
    it("uses the 1200 responsive default without overwriting remembered width", () => {
      localStorage.clear();
      Object.defineProperty(window,"innerWidth",{configurable:true,value:1199});
      const view=render(<AppShell conversation={<p>对话</p>}><p>首页</p></AppShell>);
      expect(view.container.firstElementChild).toHaveStyle({"--editor-right":"300px"});
      Object.defineProperty(window,"innerWidth",{configurable:true,value:1200});
      fireEvent(window,new Event("resize"));
      expect(view.container.firstElementChild).toHaveStyle({"--editor-right":"380px"});
      expect(localStorage.getItem("video-workspace-chat-width")).toBeNull();
      fireEvent.keyDown(screen.getByRole("separator",{name:"调整对话栏宽度"}),{key:"ArrowRight"});
      Object.defineProperty(window,"innerWidth",{configurable:true,value:1199});
      fireEvent(window,new Event("resize"));
      expect(view.container.firstElementChild).toHaveStyle({"--editor-right":"396px"});
      view.unmount();localStorage.clear();
    });
  });
});

describe("hidden workspace media", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

  it("allows standalone page media after the work area was collapsed", () => {
    function Media() {
      const visible = useContext(MediaVisibilityContext);
      return <video aria-label="页面试听" data-media-visible={visible} controls src="/preview.mp4"/>;
    }
    const view = render(<AppShell editorLayout conversation={<p>对话</p>}><Media/></AppShell>);
    const video = screen.getByLabelText("页面试听") as HTMLVideoElement;
    const pause = vi.spyOn(video, "pause").mockImplementation(() => {});
    fireEvent.click(screen.getByRole("button", {name:"布局"}));
    fireEvent.click(screen.getByRole("button", {name:"作品区"}));
    expect(video).toHaveAttribute("data-media-visible", "false");
    expect(pause).toHaveBeenCalledOnce();
    pathname = "/settings";
    view.rerender(<AppShell editorLayout conversation={<p>对话</p>}><Media/></AppShell>);
    expect(screen.getByLabelText("页面试听")).toBe(video);
    expect(video).toHaveAttribute("data-media-visible", "true");
    pause.mockClear();
    fireEvent.play(video);
    expect(pause).not.toHaveBeenCalled();
  });

  it("pauses a collapsed conversation and preserves its draft and media position on return", () => {
    render(<AppShell conversation={<><input aria-label="未发送草稿" defaultValue="保留这个要求"/><audio aria-label="对话试听" controls src="/chat.wav"/></>}><p>作品</p></AppShell>);
    const audio = screen.getByLabelText("对话试听") as HTMLAudioElement;
    const pause = vi.spyOn(audio, "pause").mockImplementation(() => {});
    audio.currentTime = 7;
    fireEvent.play(audio);
    expect(pause).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name:"收起对话栏" }));
    expect(pause).toHaveBeenCalledOnce();
    fireEvent.play(audio);
    expect(pause).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name:"展开对话栏" }));
    expect(screen.getByLabelText("对话试听")).toBe(audio);
    expect(audio.currentTime).toBe(7);
    expect(screen.getByLabelText("未发送草稿")).toHaveValue("保留这个要求");
  });

  it.each(["resource", "inspector"])("pauses the conversation when a narrow %s pane replaces it", kind => {
    vi.stubGlobal("ResizeObserver", class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(node: Element) { if (node.hasAttribute("data-editor-toolbar-parent")) this.callback([{ target:node, contentRect:{width:900} } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      unobserve() {} disconnect() {}
    });
    function Subject() {
      const [resource, setResource] = useState(false);
      return <AppShell editorLayout resourceOpen={resource} conversation={<audio aria-label="对话试听" src="/chat.wav"/>} inspector={<p>媒体属性</p>} inspectorTitle="媒体属性">
        <button onClick={() => setResource(true)}>打开资源</button><button data-open-details>打开属性</button><button onClick={() => setResource(false)}>返回作品</button>
      </AppShell>;
    }
    render(<Subject/>);
    const audio=screen.getByLabelText("对话试听") as HTMLAudioElement;
    const pause=vi.spyOn(audio,"pause").mockImplementation(()=>{});
    fireEvent.click(screen.getByRole("button",{name:kind === "resource" ? "打开资源" : "打开属性"}));
    expect(audio.closest("section[aria-label='创作助手']")).toHaveAttribute("hidden");
    expect(pause).toHaveBeenCalledOnce();
    fireEvent.play(audio);
    expect(pause).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button",{name:kind === "resource" ? "返回作品" : "关闭属性面板"}));
    expect(screen.getByRole("region",{name:"创作助手"})).toContainElement(audio);
    expect(pause).toHaveBeenCalledTimes(2);
  });

  it("pauses the collapsed work area and blocks late native playback without resetting its media", () => {
    render(<AppShell editorLayout conversation={<p>对话</p>}><video aria-label="剪辑成片" controls src="/output.mp4"/></AppShell>);
    const video=screen.getByLabelText("剪辑成片") as HTMLVideoElement;
    const pause=vi.spyOn(video,"pause").mockImplementation(()=>{});
    video.currentTime=42;
    fireEvent.click(screen.getByRole("button",{name:"布局"}));
    fireEvent.click(screen.getByRole("button",{name:"作品区"}));
    expect(pause).toHaveBeenCalledOnce();
    fireEvent.play(video);
    expect(pause).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button",{name:"布局"}));
    fireEvent.click(screen.getByRole("button",{name:"作品区"}));
    expect(screen.getByLabelText("剪辑成片")).toBe(video);
    expect(video.currentTime).toBe(42);
    expect(pause).toHaveBeenCalledTimes(2);
  });
});
