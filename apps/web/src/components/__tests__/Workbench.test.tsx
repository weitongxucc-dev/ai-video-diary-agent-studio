import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Workbench } from "../workbench/Workbench";
import { WorkbenchTargetProvider } from "../workbench/WorkbenchTarget";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { useWorkspacePage, useWorkspaceViewState } from "../workbench/context";
import { WorkspaceRoute } from "../workbench/WorkspaceRoute";
import { AppShell, type AppShellProps } from "../AppShell";
import type { HomeCommandComposerProps } from "../HomeCommandComposer";
import type { CreationTask } from "@/lib/types";

const navigation = vi.hoisted(() => ({ path: "/tasks/a", search: "", push: vi.fn() }));
const composerMount = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useSearchParams: () => new URLSearchParams(navigation.search), useRouter: () => ({ push: navigation.push }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "member-epoch" }) }));
vi.mock("../AssistantConversationProvider", () => ({ useAssistantSelection: () => null }));
vi.mock("../AppShell", async importOriginal => {
  const original = await importOriginal<typeof import("../AppShell")>();
  return { ...original, AppShellFrame: ({ children, conversation, workspaceTabs, inspector, autoFocus }: AppShellProps) => <div data-shell-auto-focus={String(Boolean(autoFocus))}>{workspaceTabs}{conversation}<main>{children}</main>{inspector}</div> };
});
vi.mock("../HomeCommandComposer", () => ({ HomeCommandComposer: ({ task }: HomeCommandComposerProps) => {
  const [draft, setDraft] = useState(() => { composerMount(); return ""; });
  return <><output aria-label="发送目标">{task?.id ?? "home"}</output><input aria-label="持续对话草稿" value={draft} onChange={event => setDraft(event.target.value)} /></>;
} }));

function Inspector() { return createPortal(<><output aria-label="检查器页面">{useWorkspacePage()?.id}</output><WorkspaceVideo aria-label="检查器预览" src="/inspector.mp4" /></>, document.body); }

function Page({ id }: { id: string }) {
  const [draft, setDraft] = useWorkspaceViewState("test.draft", "");
  const task = id.startsWith("/tasks/") ? { id: id.split("/").at(-1), name: `作品 ${id.at(-1)}` } as CreationTask : null;
  return <AppShell inspector={<Inspector />} assistantContext={task ? { task } : undefined}><input aria-label={`页面草稿 ${id}`} value={draft} onChange={event => setDraft(event.target.value)} /><WorkspaceVideo aria-label={`预览 ${id}`} src={`${id}/preview.mp4`} /></AppShell>;
}
function route() {
  const path = navigation.path;
  return <WorkspaceRoute label={path === "/library" ? "素材库" : `作品 ${path.at(-1)}`}><Page id={path} /></WorkspaceRoute>;
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
  view.rerender(fixture(<WorkspaceRoute label="新视频草稿"><Page id="draft" /></WorkspaceRoute>));
  expect(await screen.findByRole("tab", { name: "新视频草稿" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "新视频草稿" }).closest("[data-shell-auto-focus]")).toHaveAttribute("data-shell-auto-focus", "true");
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
