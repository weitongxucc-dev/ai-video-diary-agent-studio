import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Workbench } from "../workbench/Workbench";
import { WorkbenchTargetProvider } from "../workbench/WorkbenchTarget";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { useWorkspacePage, useWorkspaceViewState, WorkspacePanelContext } from "../workbench/context";
import { WorkspaceRoute } from "../workbench/WorkspaceRoute";
import { AppShell, type AppShellProps } from "../AppShell";
import type { HomeCommandComposerProps } from "../HomeCommandComposer";
import type { CreationTask } from "@/lib/types";

const navigation = vi.hoisted(() => ({ path: "/tasks/a", search: "", push: vi.fn() }));
const composerMount = vi.hoisted(() => vi.fn());
const workDetailsOpened = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useSearchParams: () => new URLSearchParams(navigation.search), useRouter: () => ({ push: navigation.push }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "member-epoch" }) }));
vi.mock("../AssistantConversationProvider", () => ({ useAssistantSelection: () => null }));
vi.mock("../AppShell", async importOriginal => {
  const original = await importOriginal<typeof import("../AppShell")>();
  return { ...original, AppShellFrame: ({ children, conversation, workspaceTabs, inspector, resourcePanel }: AppShellProps) => {
    const [resourceInspectorOpen, setResourceInspectorOpen] = useState(false);
    return <WorkspacePanelContext.Provider value={{ resourceInspectorOpen, openResourceInspector: () => setResourceInspectorOpen(true), inspectorOpen: false, resourceOpen: Boolean(resourcePanel), conversationFocused: false, revealTaskPanel: () => {} }}>
      {workspaceTabs}{resourcePanel ? <aside aria-label="作品资源">{resourcePanel.content}{resourceInspectorOpen ? <p>已打开资源详情</p> : null}</aside> : null}{conversation}<main onClick={event => {
        if (event.target instanceof Element && event.target.closest("[data-open-details]")) workDetailsOpened();
      }}>{children}</main>{inspector}
    </WorkspacePanelContext.Provider>;
  } };
});
vi.mock("../HomeCommandComposer", () => ({ HomeCommandComposer: ({ task }: HomeCommandComposerProps) => {
  const [draft, setDraft] = useState(() => { composerMount(); return ""; });
  return <><output aria-label="发送目标">{task?.id ?? "home"}</output><input aria-label="持续对话草稿" value={draft} onChange={event => setDraft(event.target.value)} /></>;
} }));

function Inspector() { return createPortal(<><output aria-label="检查器页面">{useWorkspacePage()?.id}</output><WorkspaceVideo aria-label="检查器预览" src="/inspector.mp4" /></>, document.body); }

function Page({ id }: { id: string }) {
  const [draft, setDraft] = useWorkspaceViewState("test.draft", "");
  const task = id.startsWith("/tasks/") ? { id: id.split("/").at(-1), name: `作品 ${id.at(-1)}` } as CreationTask : null;
  return <AppShell inspector={<Inspector />} assistantContext={task ? { task } : undefined}><input aria-label={`页面草稿 ${id}`} value={draft} onChange={event => setDraft(event.target.value)} /><WorkspaceVideo aria-label={`预览 ${id}`} src={`${id}/preview.mp4`} />{!task ? <button data-open-details>查看资源详情</button> : null}</AppShell>;
}
const resourceLabels: Record<string, string> = { "/library": "素材库", "/knowledge": "知识库", "/profiles": "人物与声音", "/templates": "视频模板" };
function route() {
  const path = navigation.path;
  const label = path === "/" ? navigation.search === "mode=panel" ? "首页概览" : "首页" : resourceLabels[path] ?? `作品 ${path.at(-1)}`;
  return <WorkspaceRoute label={label}><Page id={path} /></WorkspaceRoute>;
}
function fixture(children: ReactNode) { return <WorkbenchTargetProvider><Workbench>{children}</Workbench></WorkbenchTargetProvider>; }
beforeEach(() => {
  vi.clearAllMocks(); navigation.path = "/tasks/a"; navigation.search = "";
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(cleanup);

it("opens full resource management without losing the work target, drafts, or return position", async () => {
  const view = render(fixture(route()));
  const field = await screen.findByRole("textbox", { name:"页面草稿 /tasks/a" });
  fireEvent.change(field, {target:{value:"待确认字幕"}});
  const player = screen.getByLabelText("预览 /tasks/a") as HTMLVideoElement;
  player.currentTime = 12;
  fireEvent.timeUpdate(player);
  navigation.path = "/library"; navigation.search = "mode=manage";
  view.rerender(fixture(route()));
  expect(await screen.findByRole("tab", {name:"素材库"})).toHaveAttribute("aria-selected","true");
  expect(screen.queryByRole("region", {name:"作品 a"})).not.toBeInTheDocument();
  expect(screen.getByRole("region", {name:"素材库"})).toBeVisible();
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
  expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts).toContain(player);
  navigation.path = "/tasks/a"; navigation.search = "";
  view.rerender(fixture(route()));
  expect(await screen.findByRole("textbox", {name:"页面草稿 /tasks/a"})).toHaveValue("待确认字幕");
  expect(screen.getByLabelText("预览 /tasks/a")).toBe(player);
  expect(player.currentTime).toBe(12);
  expect(composerMount).toHaveBeenCalledTimes(1);
});

it("shows the four resource routes in the left panel without remounting the active work or conversation", async () => {
  const view = render(fixture(route()));
  const workDraft = await screen.findByRole("textbox", { name: "页面草稿 /tasks/a" });
  const chatDraft = screen.getByRole("textbox", { name: "持续对话草稿" });
  const player = screen.getByLabelText("预览 /tasks/a") as HTMLVideoElement;
  fireEvent.change(workDraft, { target: { value: "选中的字幕" } });
  fireEvent.change(chatDraft, { target: { value: "未发送要求" } });
  player.currentTime = 12;
  fireEvent.timeUpdate(player);
  fireEvent.play(player);

  for (const [path, title] of Object.entries(resourceLabels)) {
    navigation.path = path;
    navigation.search = "mode=panel";
    view.rerender(fixture(route()));
    expect(screen.getByRole("complementary", { name: "作品资源" })).toContainElement(await screen.findByRole("region", { name: title }));
    expect(screen.getByRole("link", { name: "完整管理" })).toHaveAttribute("href", `${path}?mode=manage`);
    expect(workDraft).toBeVisible();
    expect(workDraft).toHaveValue("选中的字幕");
    expect(chatDraft).toHaveValue("未发送要求");
    expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
    expect(screen.getByLabelText("预览 /tasks/a")).toBe(player);
    expect(player.currentTime).toBe(12);
    expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts).not.toContain(player);
  }
  const resource = screen.getByRole("complementary", { name: "作品资源" });
  fireEvent.click(within(resource).getByRole("button", { name: "查看资源详情" }));
  expect(within(resource).getByText("已打开资源详情")).toBeVisible();
  expect(workDetailsOpened).not.toHaveBeenCalled();
  expect(player).toBeVisible();
  navigation.path = "/tasks/a";
  navigation.search = "";
  view.rerender(fixture(route()));
  expect(workDraft).toBeVisible();
});

it("opens the home overview beside the same work and retains the work when opening the full home", async () => {
  const view = render(fixture(route()));
  const work = await screen.findByRole("region", { name: "作品 a" });
  const player = within(work).getByLabelText("预览 /tasks/a") as HTMLVideoElement;
  const draft = screen.getByRole("textbox", { name: "持续对话草稿" });
  fireEvent.change(draft, { target: { value: "未发送的作品要求" } });
  player.currentTime = 12;
  fireEvent.timeUpdate(player);
  fireEvent.play(player);

  navigation.path = "/"; navigation.search = "mode=panel";
  view.rerender(fixture(route()));
  const overview = await screen.findByRole("region", { name: "首页概览" });
  const pane = screen.getByRole("complementary", { name: "作品资源" });
  expect(pane).toContainElement(overview);
  expect(within(pane).getByRole("link", { name: "打开完整首页" })).toHaveAttribute("href", "/?mode=manage");
  expect(work).toBeVisible();
  expect(player.currentTime).toBe(12);
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
  expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts).not.toContain(player);

  navigation.search = "mode=manage";
  view.rerender(fixture(route()));
  expect(await screen.findByRole("region", { name: "首页" })).toBeVisible();
  expect(screen.queryByRole("complementary", { name: "作品资源" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
  expect(draft).toHaveValue("未发送的作品要求");

  navigation.path = "/tasks/a"; navigation.search = "";
  view.rerender(fixture(route()));
  expect(screen.getByRole("region", { name: "作品 a" })).toBe(work);
  expect(within(work).getByLabelText("预览 /tasks/a")).toBe(player);
  expect(player.currentTime).toBe(12);
  expect(composerMount).toHaveBeenCalledTimes(1);
});

it.each(["/profiles", "/templates"])("opens %s as a full workspace without an active work, including a panel URL", async path => {
  navigation.path = path;
  navigation.search = "mode=panel";
  render(fixture(route()));
  expect(await screen.findByRole("region", { name: resourceLabels[path] })).toBeVisible();
  expect(screen.queryByRole("complementary", { name: "作品资源" })).toBeNull();
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("home");
});

it.each([
  ["/profiles", "view=voices&task=a"],
  ["/templates", "return_to=%2Fproduction%3Fnew%3D1"],
])("keeps the %s repair route in full management with a work already open", async (path, search) => {
  const view = render(fixture(route()));
  const field = await screen.findByRole("textbox", { name: "页面草稿 /tasks/a" });
  navigation.path = path;
  navigation.search = search;
  view.rerender(fixture(route()));
  expect(await screen.findByRole("region", { name: resourceLabels[path] })).toBeVisible();
  expect(screen.queryByRole("complementary", { name: "作品资源" })).toBeNull();
  expect(field).not.toBeVisible();
});

it("keeps one conversation and explicit task target while browsing, isolates two task drafts, and restores a closed view", async () => {
  const view = render(fixture(route()));
  await screen.findByRole("textbox", { name: "页面草稿 /tasks/a" });
  fireEvent.change(screen.getByLabelText("持续对话草稿"), { target: { value: "还未发送的要求" } });
  fireEvent.change(screen.getByLabelText("页面草稿 /tasks/a"), { target: { value: "A 的裁剪输入" } });
  const player = screen.getByLabelText("预览 /tasks/a") as HTMLVideoElement;
  player.currentTime = 4; fireEvent.timeUpdate(player); fireEvent.play(player);
  navigation.path = "/library"; view.rerender(fixture(route()));
  await screen.findByRole("region", { name: "素材库" });
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
  expect(screen.getByLabelText("持续对话草稿")).toHaveValue("还未发送的要求");
  expect(screen.getByRole("region", { name: "作品 a" })).toBeVisible();
  expect(screen.getByRole("region", { name: "素材库" })).toBeVisible();
  expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts).not.toContain(player);
  expect(screen.getByLabelText("预览 /tasks/a")).toBe(player);
  expect(player).toHaveAttribute("src", "/tasks/a/preview.mp4");
  expect(player.currentTime).toBe(4);
  expect(screen.getByLabelText("检查器页面")).toHaveTextContent("/library");
  fireEvent.play(screen.getByLabelText("预览 /library"));
  expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts.at(-1)).toBe(player);
  fireEvent.play(player);
  fireEvent.play(screen.getByLabelText("检查器预览"));
  expect(vi.mocked(HTMLMediaElement.prototype.pause).mock.contexts.at(-1)).toBe(player);
  expect(player).toHaveAttribute("src", "/tasks/a/preview.mp4");
  view.rerender(fixture(route()));
  expect(screen.getAllByRole("region", { name: "素材库" })).toHaveLength(1);
  navigation.path = "/tasks/b"; view.rerender(fixture(route()));
  await waitFor(() => expect(screen.getByLabelText("发送目标")).toHaveTextContent("b"));
  expect(screen.getByLabelText("页面草稿 /tasks/b")).toHaveValue("");
  expect(player).not.toHaveAttribute("src");
  fireEvent.change(screen.getByLabelText("页面草稿 /tasks/b"), { target: { value: "B 的字幕输入" } });
  navigation.path = "/tasks/a"; view.rerender(fixture(route()));
  await waitFor(() => expect(screen.getByLabelText("发送目标")).toHaveTextContent("a"));
  expect(screen.getByLabelText("页面草稿 /tasks/a")).toHaveValue("A 的裁剪输入");
  fireEvent.click(screen.getByRole("button", { name: "关闭作品 a页签" }));
  expect(screen.queryByRole("tab", { name: "作品 a" })).not.toBeInTheDocument();
  navigation.path = "/library"; view.rerender(fixture(route()));
  navigation.path = "/tasks/a"; view.rerender(fixture(route()));
  await screen.findByRole("tab", { name: "作品 a" });
  expect(screen.getByLabelText("页面草稿 /tasks/a")).toHaveValue("A 的裁剪输入");
  expect(composerMount).toHaveBeenCalledTimes(1);
});

it("keeps a new draft separate from the open task and returns to the original editing state", async () => {
  const view = render(fixture(route()));
  const work = await screen.findByRole("textbox", { name: "页面草稿 /tasks/a" });
  fireEvent.change(work, { target: { value: "原剪辑位置的输入" } });
  const player = screen.getByLabelText("预览 /tasks/a") as HTMLVideoElement;
  player.currentTime = 9;
  fireEvent.timeUpdate(player);

  navigation.path = "/production";
  navigation.search = "new=1&draft_id=a7872828-c7ba-449c-a988-e5679d163a81";
  view.rerender(fixture(<WorkspaceRoute label="新建视频"><Page id="draft" /></WorkspaceRoute>));
  expect(await screen.findByRole("tab", { name: "新建视频" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("home");
  fireEvent.change(screen.getByRole("textbox", { name: "页面草稿 draft" }), { target: { value: "另一条视频目标" } });

  fireEvent.click(screen.getByRole("tab", { name: "作品 a" }));
  expect(navigation.push).toHaveBeenLastCalledWith("/tasks/a");
  navigation.path = "/tasks/a";
  navigation.search = "";
  view.rerender(fixture(route()));
  expect(await screen.findByRole("textbox", { name: "页面草稿 /tasks/a" })).toHaveValue("原剪辑位置的输入");
  expect(screen.getByLabelText("发送目标")).toHaveTextContent("a");
  expect(screen.getByLabelText("预览 /tasks/a")).toBe(player);
  expect(player.currentTime).toBe(9);
});
