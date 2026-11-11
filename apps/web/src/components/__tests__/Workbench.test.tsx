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

const navigation = vi.hoisted(() => ({ path: "/tasks/a", push: vi.fn() }));
const composerMount = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useSearchParams: () => new URLSearchParams(), useRouter: () => ({ push: navigation.push }) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "member-epoch" }) }));
vi.mock("../AssistantConversationProvider", () => ({ useAssistantSelection: () => null }));
vi.mock("../AppShell", async importOriginal => {
  const original = await importOriginal<typeof import("../AppShell")>();
  return { ...original, AppShellFrame: ({ children, conversation, workspaceTabs, inspector }: AppShellProps) => <>{workspaceTabs}{conversation}<main>{children}</main>{inspector}</> };
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
  vi.clearAllMocks(); navigation.path = "/tasks/a";
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
});
afterEach(cleanup);

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
