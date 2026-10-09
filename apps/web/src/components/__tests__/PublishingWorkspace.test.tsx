import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listCreationTasks: vi.fn(), listPublishingPlatforms: vi.fn(), listPublishingAccounts: vi.fn(),
  connectPublishingAccount: vi.fn(), verifyPublishingAccount: vi.fn(),
  createPlatformPublishJob: vi.fn(), listPlatformPublishJobs: vi.fn(), getCreationTaskTimelineVersion: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));

import { PublishingWorkspace } from "@/components/PublishingWorkspace";
import type { CreationTask, PlatformBrowserAccount, PlatformPublishJob } from "@/lib/types";

const task = {
  id: "task-1", workspace_id: "local", plan_id: "plan-1", visibility: "private" as const,
  name: "新品讲解视频", scenario: "产品介绍", stage: "review" as const,
  stage_status: "completed" as const, status: "completed" as const, progress: 100,
  recovery_point: "review", selected_topic_id: "topic-1", confirmed_script_id: "script-1",
  narration_cache_key: null, montage_plan_id: "montage-1", current_timeline_version_id: "timeline-v4",
  timeline_sequence: 4, can_undo: true, output_ready: true, stage_reason: null,
  stage_impact: null, stage_repair_action: null, stage_retryable: null,
  created_at: "2026-08-30T08:00:00Z", updated_at: "2026-08-30T08:30:00Z",
};
const task2 = { ...task, id: "task-2", name: "另一条新品视频", current_timeline_version_id: "timeline-v2", timeline_sequence: 2 };
const unreadyTask = { ...task, id: "task-3", name: "仍在制作的任务", current_timeline_version_id: null, timeline_sequence: 0, output_ready: false, stage: "script" as const, stage_status: "waiting_for_user" as const };
const platforms = { items: [
  { platform: "douyin", label: "抖音", browser_connect: true, browser_publish: true, verified: false, status: "candidate_unverified", requirement: "创作者页面仍待验收" },
  { platform: "wechat", label: "微信视频号", browser_connect: false, browser_publish: false, verified: false, status: "not_connected", requirement: "尚未接通" },
  { platform: "xiaohongshu", label: "小红书", browser_connect: false, browser_publish: false, verified: false, status: "not_connected", requirement: "尚未接通" },
] };

afterEach(() => vi.clearAllMocks());

function versionDetail(taskId = "task-1", versionId = "timeline-v4") {
  const sequence = Number(versionId.match(/\d+$/)?.[0] ?? 4);
  return { task_id: taskId, version: { id: versionId, sequence, parent_version_id: null, created_at: "2026-10-09T00:00:00Z", is_current: true, shot_count: 1, subtitle_track_count: 1, narration_segment_count: 1 }, shots: [], narration_segments: [], subtitle_tracks: [], cover_asset: null, artifacts: [{ kind: "video", status: "available", url: "/fixture/video.mp4", reason: null }] };
}

function setup({ accounts = { items: [] }, jobs = { items: [] }, tasks = [task] }: {
  accounts?: { items: PlatformBrowserAccount[] };
  jobs?: { items: PlatformPublishJob[] };
  tasks?: CreationTask[];
} = {}) {
  api.listCreationTasks.mockResolvedValue({ items: tasks, total_count: tasks.length });
  api.listPublishingPlatforms.mockResolvedValue(platforms);
  api.listPublishingAccounts.mockResolvedValue(accounts);
  api.listPlatformPublishJobs.mockResolvedValue(jobs);
  api.verifyPublishingAccount.mockResolvedValue({ platform: "douyin", connected: true, account_ref: "creator-001" });
  api.getCreationTaskTimelineVersion.mockImplementation((taskId: string, versionId: string) => Promise.resolve(versionDetail(taskId, versionId)));
  render(<PublishingWorkspace />);
}

describe("PublishingWorkspace", () => {
  it("states that only Douyin is a candidate and keeps the other platforms disconnected", async () => {
    setup();
    expect(await screen.findByText("候选接入，未完成真实验收")).toBeInTheDocument();
    expect(screen.getByText("微信视频号")).toBeInTheDocument();
    expect(screen.getByText("小红书")).toBeInTheDocument();
    expect(screen.getByText("本机尚未完成真实抖音页面控件验收；不会把候选选择器、登录窗口或本地排队描述为平台已发布。")).toBeInTheDocument();
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("opens the isolated browser via an explicit action and does not simulate account completion", async () => {
    api.connectPublishingAccount.mockResolvedValue({ platform: "douyin", state: "waiting_for_user_login", detail: "请在专用浏览器登录后回来核验。" });
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "打开专用浏览器" }));
    expect(await screen.findByRole("status")).toHaveTextContent("请在专用浏览器登录后回来核验。");
    expect(api.connectPublishingAccount).toHaveBeenCalledWith("douyin");
    expect(screen.queryByText(/openid_mock/)).not.toBeInTheDocument();
  });

  it("gates editing behind a ready version and live account check, then rechecks the same target before queueing", async () => {
    const accounts: { items: PlatformBrowserAccount[] } = { items: [{ workspace_id: "local", user_id: "member-1", platform: "douyin", account_ref: "creator-001", verified_at: "2026-10-09T00:00:00Z" }] };
    api.createPlatformPublishJob.mockResolvedValue({ id: "publish-1", task_id: "task-1", status: "queued", progress: 0, queue_state: "waiting", queue_wait_reason: null, failure_reason: null, impact: null, repair_action: null, events: [] });
    setup({ accounts });
    expect(screen.queryByLabelText("发布标题")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "重新核验当前抖音账号" }));
    await screen.findByLabelText("发布标题");
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "确认过的标题" } });
    fireEvent.change(screen.getByLabelText("发布描述"), { target: { value: "完整确认文案" } });
    fireEvent.change(screen.getByLabelText("话题（逗号或换行分隔）"), { target: { value: "家庭,记录" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("账号：creator-001");
    expect(screen.getByRole("dialog")).toHaveTextContent("新品讲解视频 · 当前版本 V4");
    expect(screen.getByRole("dialog")).toHaveTextContent("完整确认文案");
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    await waitFor(() => expect(api.createPlatformPublishJob).toHaveBeenCalledTimes(1));
    expect(api.createPlatformPublishJob).toHaveBeenCalledWith("task-1", expect.objectContaining({
      platform: "douyin", account_ref: "creator-001", timeline_version_id: "timeline-v4",
      title: "确认过的标题", description: "完整确认文案", topics: ["家庭", "记录"], confirmed: true,
    }));
    expect(await screen.findByText(/发布作业 publish-1 已写入本机队列/)).toBeInTheDocument();
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("shows unready tasks as explicit preparation targets and keeps temporary copy scoped to task and version", async () => {
    setup({ tasks: [task, task2, unreadyTask] });
    const target = await screen.findByLabelText("发布目标（任务与成片版本）");
    expect(target).toHaveTextContent("仍在制作的任务 · 无当前版本 · 未就绪");
    expect(screen.queryByLabelText("发布标题")).not.toBeInTheDocument();

    fireEvent.change(target, { target: { value: "task-2" } });
    await waitFor(() => expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledWith("task-2", "timeline-v2"));
    fireEvent.click(await screen.findByRole("button", { name: "重新核验当前抖音账号" }));
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "第二条文案" } });
    fireEvent.change(target, { target: { value: "task-1" } });
    await waitFor(() => expect(screen.getByLabelText("发布标题")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("发布标题"), { target: { value: "第一条文案" } });
    fireEvent.change(target, { target: { value: "task-2" } });
    await waitFor(() => expect(screen.getByLabelText("发布标题")).toHaveValue("第二条文案"));
    expect(screen.getByText(/仅保存在本轮页面内/)).toHaveTextContent("另一条新品视频 · V2");
  });

  it("cancels confirmation without a write when the live account changed", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "重新核验当前抖音账号" }));
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "确认过的标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    api.verifyPublishingAccount.mockResolvedValueOnce({ platform: "douyin", connected: true, account_ref: "creator-other" });
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    expect(await screen.findByRole("status")).toHaveTextContent("任务版本或账号已变化");
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("returns to the scoped draft without creating a job when the user cancels confirmation", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "重新核验当前抖音账号" }));
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "暂存标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回修改" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("发布标题")).toHaveValue("暂存标题");
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("reports a rejected create request without claiming the job was queued", async () => {
    setup();
    api.createPlatformPublishJob.mockRejectedValueOnce(new Error("当前成片或版本已变化；请重新检查并确认"));
    fireEvent.click(await screen.findByRole("button", { name: "重新核验当前抖音账号" }));
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "待确认标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    fireEvent.click(await screen.findByRole("button", { name: "我已核对，创建发布作业" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前成片或版本已变化");
    expect(screen.queryByText(/已写入本机队列/)).not.toBeInTheDocument();
  });

  it("renders unknown publication outcomes as blocked from automatic retry", async () => {
    const jobs: { items: PlatformPublishJob[] } = { items: [{ id: "publish-unknown", task_id: "task-1", status: "interrupted", progress: 0, queue_state: "failed", queue_wait_reason: null, failure_reason: "publish_outcome_unknown", impact: "平台动作可能已经提交；系统不会自动重发。", repair_action: "先在平台账号后台核对结果。", events: [{ type: "platform_publish_unknown", payload: {}, created_at: "2026-10-09T00:00:00Z" }] }] };
    setup({ jobs });
    expect(await screen.findByText("结果待核对（不会自动重发）")).toBeInTheDocument();
    expect(screen.getByText("平台动作可能已经提交；系统不会自动重发。")).toBeInTheDocument();
    expect(screen.getByText("处理：先在平台账号后台核对结果。")).toBeInTheDocument();
  });

  it("refreshes the persisted job state and displays its terminal platform result", async () => {
    const queued: PlatformPublishJob = {
      id: "publish-refresh", task_id: "task-1", status: "queued", progress: 0, queue_state: "waiting",
      queue_wait_reason: null, failure_reason: null, impact: null, repair_action: null,
      events: [{ type: "platform_publish_prepared", payload: {}, created_at: "2026-10-09T00:00:00Z" }],
    };
    const succeeded: PlatformPublishJob = {
      ...queued, status: "succeeded", progress: 100, queue_state: "done",
      events: [{ type: "platform_publish_succeeded", payload: { remote_publish_id: "remote-42" }, created_at: "2026-10-09T00:01:00Z" }],
    };
    setup({ jobs: { items: [queued] } });
    expect(await screen.findByText("等待本机发布队列")).toBeInTheDocument();
    api.listPlatformPublishJobs.mockResolvedValueOnce({ items: [succeeded] });

    fireEvent.click(screen.getByRole("button", { name: /^刷新$/ }));

    expect(await screen.findByText("平台已返回发布结果")).toBeInTheDocument();
    expect(screen.getByText("平台返回编号：remote-42")).toBeInTheDocument();
    expect(api.listPlatformPublishJobs).toHaveBeenLastCalledWith("task-1");
  });
});
