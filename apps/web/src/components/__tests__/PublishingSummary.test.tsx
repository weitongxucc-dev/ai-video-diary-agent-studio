import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { CreationTask, PlatformPublishJob } from "@/lib/types";
import { PublishingRouteView } from "../PublishingSummary";
import { WorkbenchAssistantTargetContext, WorkspacePageContext } from "../workbench/context";

const read = vi.hoisted(() => vi.fn<(taskId: string) => Promise<{ items: PlatformPublishJob[] }>>());
vi.mock("@/lib/api/publishing", () => ({ listPlatformPublishJobs: read }));
vi.mock("../PublishingWorkspace", async importOriginal => ({
  ...await importOriginal<typeof import("../PublishingWorkspace")>(),
  PublishingWorkspace: () => <p>完整发布管理</p>,
}));

const taskA = { id: "work-a", name: "作品 A", current_timeline_version_id: "timeline-a", timeline_sequence: 7 } as CreationTask;
const taskB = { ...taskA, id: "work-b", name: "作品 B", current_timeline_version_id: "timeline-b" };
const failure: PlatformPublishJob = {
  id: "job-a", task_id: taskA.id, status: "failed", progress: 0, queue_state: "not_submitted",
  queue_wait_reason: null, failure_reason: "publish_queue_unavailable", impact: "发布尚未提交到平台。", repair_action: null,
  events: [{ type: "platform_publish_prepared", created_at: "2026-11-26T09:00:00Z",
    payload: { platform: "douyin", account_ref: "account-a", timeline_version_id: "timeline-a" } }],
};

function view(task: CreationTask, auxiliary = true, active = true) {
  return <WorkspacePageContext.Provider value={{ id: "/history", href: "/history?mode=panel", active,
    auxiliary, updateShell: vi.fn(), viewState: new Map() }}>
    <WorkbenchAssistantTargetContext.Provider value={{ task }}>
      <PublishingRouteView />
    </WorkbenchAssistantTargetContext.Provider>
  </WorkspacePageContext.Provider>;
}

beforeEach(() => read.mockReset());

it("shows the stored platform, account and version for a failed job without reporting a platform receipt", async () => {
  read.mockResolvedValue({ items: [failure] });
  render(view(taskA));
  const summary = within(screen.getByRole("region", { name: "当前作品发布摘要" }));
  expect(await summary.findByText("发布失败")).toBeVisible();
  expect(summary.getByText("抖音")).toBeVisible();
  expect(summary.getByText("account-a")).toBeVisible();
  expect(summary.getByText("V7")).toBeVisible();
  expect(summary.getByText("发布尚未提交到平台。")).toBeVisible();
  expect(summary.queryByText("平台返回编号")).not.toBeInTheDocument();
  expect(summary.queryByRole("button")).not.toBeInTheDocument();
  expect(read).toHaveBeenCalledExactlyOnceWith(taskA.id);
});

it("retries errors, distinguishes empty records and ignores a late response for the prior work", async () => {
  let resolveOld!: (value: { items: PlatformPublishJob[] }) => void;
  read.mockRejectedValueOnce(new Error("Forbidden"))
    .mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }))
    .mockResolvedValue({ items: [] });
  const screenView = render(view(taskA));
  expect(await screen.findByRole("alert")).toHaveTextContent("当前结果未知");
  expect(screen.queryByText("当前作品没有已保存的发布记录。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(screen.getByRole("status")).toHaveTextContent("正在读取发布记录");
  screenView.rerender(view(taskB));
  expect(await screen.findByText("当前作品没有已保存的发布记录。")).toBeVisible();
  await act(async () => resolveOld({ items: [failure] }));
  expect(screen.getByRole("heading", { name: "作品 B" })).toBeVisible();
  expect(screen.queryByText("account-a")).not.toBeInTheDocument();
  expect(read.mock.calls).toEqual([[taskA.id], [taskA.id], [taskB.id]]);
});

it("keeps full management on the original page and does not mount it behind the work", () => {
  const screenView = render(view(taskA, false, false));
  expect(screen.queryByText("完整发布管理")).not.toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
  screenView.rerender(view(taskA, false));
  expect(screen.getByText("完整发布管理")).toBeVisible();
  expect(read).not.toHaveBeenCalled();
});
