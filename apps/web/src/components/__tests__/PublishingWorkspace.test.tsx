import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function setup({ accounts = { items: [] }, jobs = { items: [] }, tasks = [task], versionRead, prepare = true }: {
  accounts?: { items: PlatformBrowserAccount[] };
  jobs?: { items: PlatformPublishJob[] };
  tasks?: CreationTask[];
  prepare?: boolean;
  versionRead?: (taskId: string, versionId: string) => Promise<ReturnType<typeof versionDetail>>;
} = {}) {
  api.listCreationTasks.mockResolvedValue({ items: tasks, total_count: tasks.length });
  api.listPublishingPlatforms.mockResolvedValue(platforms);
  api.listPublishingAccounts.mockResolvedValue(accounts);
  api.listPlatformPublishJobs.mockResolvedValue(jobs);
  api.verifyPublishingAccount.mockResolvedValue({ platform: "douyin", connected: true, account_ref: "creator-001" });
  api.getCreationTaskTimelineVersion.mockImplementation(versionRead ?? ((taskId: string, versionId: string) => Promise.resolve(versionDetail(taskId, versionId))));
  render(<PublishingWorkspace />);
  if (prepare) fireEvent.click(screen.getByRole("button", {name:"准备发布"}));
}

async function verifyAccount() {
  const button=await screen.findByRole("button",{name:"重新核验当前抖音账号"});
  await waitFor(()=>expect(button).toBeEnabled());
  fireEvent.click(button);
}

describe("PublishingWorkspace", () => {
  it("starts with real output objects and opens preparation only on an explicit action", async () => {
    setup({tasks:[task,unreadyTask],prepare:false});
    expect(await screen.findByRole("button",{name:"检查成片 新品讲解视频"})).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("发布目标（任务与成片版本）")).not.toBeInTheDocument();
    expect(screen.queryByText("仍在制作的任务")).not.toBeInTheDocument();
    expect(api.verifyPublishingAccount).not.toHaveBeenCalled();
    expect(api.connectPublishingAccount).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button",{name:"检查成片 新品讲解视频"}));
    expect(await screen.findByRole("dialog",{name:"发布准备 · 新品讲解视频"})).toBeVisible();
    expect(await screen.findByLabelText("当前成片预览")).toHaveAttribute("src","/fixture/video.mp4");
  });

  it("filters real output names and clears the search without moving focus or creating work", async () => {
    setup({tasks:[task,task2],prepare:false});
    await screen.findByRole("button",{name:"检查成片 新品讲解视频"});
    const input=screen.getByLabelText("搜索成片任务名称");
    fireEvent.change(input,{target:{value:"另一条"}});
    expect(screen.queryByRole("button",{name:"检查成片 新品讲解视频"})).not.toBeInTheDocument();
    expect(screen.getByRole("button",{name:"检查成片 另一条新品视频"})).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"清空搜索"}));
    expect(input).toHaveFocus();
    expect(input).toHaveValue("");
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("keeps external metrics unknown and does not display local jobs as external data", async () => {
    setup({prepare:false});
    fireEvent.click(screen.getByRole("button",{name:"外部数据"}));
    expect(screen.getByText("外部数据尚未接入")).toBeVisible();
    expect(screen.getByText(/暂无平台数据来源与采集时间/)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("0 播放")).not.toBeInTheDocument();
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("reports a job read failure separately from an empty collection and supports retry", async () => {
    setup({prepare:false});
    await screen.findByRole("button",{name:"检查成片 新品讲解视频"});
    await waitFor(()=>expect(api.listPlatformPublishJobs).toHaveBeenCalledTimes(1));
    api.listPlatformPublishJobs.mockRejectedValueOnce(new Error("服务暂不可达"));
    fireEvent.click(screen.getByRole("button",{name:"发布记录"}));
    fireEvent.click(screen.getByRole("button",{name:"刷新结果"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("发布记录读取失败，当前结果未确认");
    expect(screen.queryByText("当前任务没有已保存的发布作业。")).not.toBeInTheDocument();
    api.listPlatformPublishJobs.mockResolvedValueOnce({items:[]});
    fireEvent.click(screen.getByRole("button",{name:"刷新结果"}));
    expect(await screen.findByText("当前任务没有已保存的发布作业。")).toBeVisible();
  });

  it("uses actual platform capability to block account actions when it is unavailable", async () => {
    setup();
    const browserButton=await screen.findByRole("button",{name:"打开专用浏览器"});
    await waitFor(()=>expect(browserButton).toBeEnabled());
    api.listPublishingPlatforms.mockResolvedValueOnce({items:platforms.items.map(item=>({...item,browser_connect:false,browser_publish:false,status:"not_connected"}))});
    fireEvent.click(screen.getByRole("button",{name:"刷新"}));
    await waitFor(()=>expect(browserButton).toBeDisabled());
    expect(screen.getByRole("button",{name:"重新核验当前抖音账号"})).toBeDisabled();
    expect(screen.getByText(/抖音账号接入当前不可用/)).toBeVisible();
    fireEvent.click(browserButton);
    expect(api.connectPublishingAccount).not.toHaveBeenCalled();
    expect(api.verifyPublishingAccount).not.toHaveBeenCalled();
  });

  it("states that only Douyin is a candidate and keeps the other platforms disconnected", async () => {
    setup({prepare:false});
    fireEvent.click(screen.getByRole("button",{name:"账号"}));
    expect(await screen.findByText("候选接入，未完成真实验收")).toBeInTheDocument();
    expect(screen.getByText("微信视频号")).toBeInTheDocument();
    expect(screen.getByText("小红书")).toBeInTheDocument();
    expect(screen.getAllByText("尚未接通")).toHaveLength(2);
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("opens the isolated browser via an explicit action and does not simulate account completion", async () => {
    api.connectPublishingAccount.mockResolvedValue({ platform: "douyin", state: "waiting_for_user_login", detail: "请在专用浏览器登录后回来核验。" });
    setup();
    const browserButton=await screen.findByRole("button", {name:"打开专用浏览器"});
    await waitFor(()=>expect(browserButton).toBeEnabled());
    fireEvent.click(browserButton);
    expect(await screen.findByText("请在专用浏览器登录后回来核验。")).toHaveAttribute("role","status");
    expect(api.connectPublishingAccount).toHaveBeenCalledWith("douyin");
    expect(screen.queryByText(/openid_mock/)).not.toBeInTheDocument();
  });

  it("gates editing behind a ready version and live account check, then rechecks the same target before queueing", async () => {
    const accounts: { items: PlatformBrowserAccount[] } = { items: [{ workspace_id: "local", user_id: "member-1", platform: "douyin", account_ref: "creator-001", verified_at: "2026-10-09T00:00:00Z" }] };
    api.createPlatformPublishJob.mockResolvedValue({ id: "publish-1", task_id: "task-1", status: "queued", progress: 0, queue_state: "waiting", queue_wait_reason: null, failure_reason: null, impact: null, repair_action: null, events: [] });
    setup({ accounts });
    expect(screen.queryByLabelText("发布标题")).not.toBeInTheDocument();
    await verifyAccount();
    await screen.findByLabelText("发布标题");
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "确认过的标题" } });
    fireEvent.change(screen.getByLabelText("发布描述"), { target: { value: "完整确认文案" } });
    fireEvent.change(screen.getByLabelText("话题（逗号或换行分隔）"), { target: { value: "家庭,记录" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog", {name:"确认抖音发布内容"})).toHaveTextContent("账号：creator-001");
    expect(screen.getByRole("dialog", {name:"确认抖音发布内容"})).toHaveTextContent("新品讲解视频 · 当前版本 V4");
    expect(screen.getByRole("dialog", {name:"确认抖音发布内容"})).toHaveTextContent("完整确认文案");
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    await waitFor(() => expect(api.createPlatformPublishJob).toHaveBeenCalledTimes(1));
    expect(api.createPlatformPublishJob).toHaveBeenCalledWith("task-1", expect.objectContaining({
      platform: "douyin", account_ref: "creator-001", timeline_version_id: "timeline-v4",
      title: "确认过的标题", description: "完整确认文案", topics: ["家庭", "记录"], confirmed: true,
    }));
    expect(await screen.findByText(/发布作业 publish-1 已写入任务“新品讲解视频”的本机队列/)).toBeInTheDocument();
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("shows unready tasks as explicit preparation targets and keeps temporary copy scoped to task and version", async () => {
    setup({ tasks: [task, task2, unreadyTask] });
    const target = await screen.findByLabelText("发布目标（任务与成片版本）");
    await inspectSelectOptions(target,()=>{ expect(screen.getByRole("option", {name:"仍在制作的任务 · 无当前版本 · 未就绪"})).toBeInTheDocument(); });
    expect(screen.queryByLabelText("发布标题")).not.toBeInTheDocument();

    await chooseSelectValue(target, "task-2");
    await waitFor(() => expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledWith("task-2", "timeline-v2"));
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "第二条文案" } });
    await chooseSelectValue(target, "task-1");
    await waitFor(() => expect(screen.getByLabelText("发布标题")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("发布标题"), { target: { value: "第一条文案" } });
    await chooseSelectValue(target, "task-2");
    await waitFor(() => expect(screen.getByLabelText("发布标题")).toHaveValue("第二条文案"));
    expect(screen.getByText(/仅保存在本轮页面内/)).toHaveTextContent("另一条新品视频 · V2");
  });

  it("cancels confirmation without a write when the live account changed", async () => {
    setup();
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "确认过的标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog", {name:"确认抖音发布内容"})).toBeInTheDocument();
    api.verifyPublishingAccount.mockResolvedValueOnce({ platform: "douyin", connected: true, account_ref: "creator-other" });
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    expect(await screen.findByRole("status")).toHaveTextContent("账号与确认账号不同");
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("discards an in-flight preflight after the user changes its target", async () => {
    setup({ tasks: [task, task2] });
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "任务一文案" } });
    const lateAccount = deferred<{ platform: "douyin"; connected: boolean; account_ref: string }>();
    api.verifyPublishingAccount.mockReturnValueOnce(lateAccount.promise);
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    await chooseSelectValue(screen.getByLabelText("发布目标（任务与成片版本）"), "task-2");
    lateAccount.resolve({ platform: "douyin", connected: true, account_ref: "creator-001" });
    await waitFor(() => expect(screen.getByLabelText("发布目标（任务与成片版本）")).toHaveAttribute("data-selected-value", "task-2"));
    expect(screen.queryByRole("dialog", {name:"确认抖音发布内容"})).not.toBeInTheDocument();
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("ignores an older version retry that resolves after selecting another task", async () => {
    const oldVersion = deferred<ReturnType<typeof versionDetail>>();
    let versionReads = 0;
    setup({ tasks: [task, task2], versionRead: (taskId, versionId) => {
      versionReads += 1;
      if (versionReads === 1) return Promise.reject(new Error("临时读取失败"));
      if (versionReads === 2) return oldVersion.promise;
      return Promise.resolve(versionDetail(taskId, versionId));
    } });
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "重新核对" }));
    await chooseSelectValue(screen.getByLabelText("发布目标（任务与成片版本）"), "task-2");
    await waitFor(() => expect(screen.getByText(/另一条新品视频 · V2 · 当前视频产物可用/)).toBeInTheDocument());
    oldVersion.resolve(versionDetail("task-1", "timeline-v4"));
    await waitFor(() => expect(screen.getByText(/另一条新品视频 · V2 · 当前视频产物可用/)).toBeInTheDocument());
    expect(screen.queryByText(/新品讲解视频 · V4 · 当前视频产物可用/)).not.toBeInTheDocument();
  });

  it("rereads the same selected version on refresh and ignores the older pending response", async () => {
    const olderRead = deferred<ReturnType<typeof versionDetail>>();
    const refreshedRead = deferred<ReturnType<typeof versionDetail>>();
    let versionReads = 0;
    setup({ versionRead: () => {
      versionReads += 1;
      return versionReads === 1 ? olderRead.promise : refreshedRead.promise;
    } });
    await waitFor(() => expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledTimes(2));
    expect(api.getCreationTaskTimelineVersion).toHaveBeenLastCalledWith("task-1", "timeline-v4");
    refreshedRead.resolve(versionDetail());
    await waitFor(() => expect(screen.getByText(/新品讲解视频 · V4 · 当前视频产物可用/)).toBeInTheDocument());
    olderRead.resolve({ ...versionDetail(), artifacts: [{ kind: "video", status: "missing", url: "/fixture/stale.mp4", reason: null }] });
    await waitFor(() => expect(screen.getByText(/新品讲解视频 · V4 · 当前视频产物可用/)).toBeInTheDocument());
  });

  it("keeps a pending version read when the same task is selected and a view returns are selected", async () => {
    const pendingRead = deferred<ReturnType<typeof versionDetail>>();
    setup({ versionRead: () => pendingRead.promise });
    await waitFor(() => expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: /新品讲解视频/ }));
    fireEvent.click(screen.getByRole("button", { name: "成片" }));
    fireEvent.click(screen.getByRole("button", {name:"准备发布"}));
    expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledTimes(1);
    pendingRead.resolve(versionDetail());
    await waitFor(() => expect(screen.getByText(/新品讲解视频 · V4 · 当前视频产物可用/)).toBeInTheDocument());
    expect(api.getCreationTaskTimelineVersion).toHaveBeenCalledTimes(1);
  });

  it("does not silently replace the selected account when preflight reads a different one", async () => {
    setup();
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "选定账号文案" } });
    api.verifyPublishingAccount.mockResolvedValueOnce({ platform: "douyin", connected: true, account_ref: "creator-other" });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("status")).toHaveTextContent("当前账号与本轮选择不同");
    expect(screen.queryByLabelText("发布标题")).not.toBeInTheDocument();
    expect(screen.queryByText("本轮独立核验账号：creator-other")).not.toBeInTheDocument();
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("aborts the final recheck after Escape cancels the card", async () => {
    setup();
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "待最终确认" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    await screen.findByRole("dialog", {name:"确认抖音发布内容"});
    const lateTasks = deferred<{ items: CreationTask[]; total_count: number }>();
    api.listCreationTasks.mockReturnValueOnce(lateTasks.promise);
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog", {name:"确认抖音发布内容"})).not.toBeInTheDocument();
    lateTasks.resolve({ items: [task], total_count: 1 });
    await waitFor(() => expect(api.createPlatformPublishJob).not.toHaveBeenCalled());
  });

  it("invalidates final recheck when the draft changes while its reads are pending", async () => {
    setup();
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "原文案" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    await screen.findByRole("dialog", {name:"确认抖音发布内容"});
    const lateTasks = deferred<{ items: CreationTask[]; total_count: number }>();
    api.listCreationTasks.mockReturnValueOnce(lateTasks.promise);
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    fireEvent.change(screen.getByLabelText("发布标题"), { target: { value: "修改后的文案" } });
    expect(screen.queryByRole("dialog", {name:"确认抖音发布内容"})).not.toBeInTheDocument();
    lateTasks.resolve({ items: [task], total_count: 1 });
    await waitFor(() => expect(screen.getByLabelText("发布标题")).toHaveValue("修改后的文案"));
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("keeps an already-sent request with its original task when the card closes", async () => {
    setup({ tasks: [task, task2] });
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "任务一确认文案" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    await screen.findByRole("dialog", {name:"确认抖音发布内容"});
    const sentJob = deferred<PlatformPublishJob>();
    const job: PlatformPublishJob = { id: "publish-late", task_id: "task-1", status: "queued", progress: 0, queue_state: "waiting", queue_wait_reason: null, failure_reason: null, impact: null, repair_action: null, events: [] };
    api.createPlatformPublishJob.mockReturnValueOnce(sentJob.promise);
    fireEvent.click(screen.getByRole("button", { name: "我已核对，创建发布作业" }));
    await waitFor(() => expect(api.createPlatformPublishJob).toHaveBeenCalledWith("task-1", expect.any(Object)));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(await screen.findByRole("status")).toHaveTextContent("关闭确认卡不会撤回");
    await chooseSelectValue(screen.getByLabelText("发布目标（任务与成片版本）"), "task-2");
    sentJob.resolve(job);
    await waitFor(() => expect(screen.queryByText("等待本机发布队列")).not.toBeInTheDocument());
    expect(api.createPlatformPublishJob).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/已撤回/)).not.toBeInTheDocument();
  });

  it("returns to the scoped draft without creating a job when the user cancels confirmation", async () => {
    setup();
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "暂存标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    expect(await screen.findByRole("dialog", {name:"确认抖音发布内容"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回修改" }));
    expect(screen.queryByRole("dialog", {name:"确认抖音发布内容"})).not.toBeInTheDocument();
    expect(screen.getByLabelText("发布标题")).toHaveValue("暂存标题");
    expect(screen.getByRole("dialog",{name:"发布准备 · 新品讲解视频"})).toBeVisible();
    expect(api.createPlatformPublishJob).not.toHaveBeenCalled();
  });

  it("reports a rejected create request without claiming the job was queued", async () => {
    setup();
    api.createPlatformPublishJob.mockRejectedValueOnce(new Error("当前成片或版本已变化；请重新检查并确认"));
    await verifyAccount();
    fireEvent.change(await screen.findByLabelText("发布标题"), { target: { value: "待确认标题" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并确认抖音发布" }));
    fireEvent.click(await screen.findByRole("button", { name: "我已核对，创建发布作业" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前成片或版本已变化");
    expect(screen.queryByText(/已写入本机队列/)).not.toBeInTheDocument();
  });

  it("renders unknown publication outcomes as blocked from automatic retry", async () => {
    const jobs: { items: PlatformPublishJob[] } = { items: [{ id: "publish-unknown", task_id: "task-1", status: "interrupted", progress: 0, queue_state: "failed", queue_wait_reason: null, failure_reason: "publish_outcome_unknown", impact: "平台动作可能已经提交；系统不会自动重发。", repair_action: "先在平台账号后台核对结果。", events: [{ type: "platform_publish_unknown", payload: {}, created_at: "2026-10-09T00:00:00Z" }] }] };
    setup({ jobs });
    fireEvent.click(screen.getByRole("button",{name:"发布记录"}));
    fireEvent.click(await screen.findByRole("button",{name:/结果待核对/}));
    expect(within(screen.getByRole("dialog",{name:"结果待核对（不会自动重发）"})).getByText("结果待核对（不会自动重发）")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button",{name:"发布记录"}));
    expect(await screen.findByText("等待本机发布队列")).toBeInTheDocument();
    api.listPlatformPublishJobs.mockResolvedValueOnce({ items: [succeeded] });

    fireEvent.click(screen.getByRole("button", { name: /^刷新$/ }));

    expect(await screen.findByText("平台已返回发布结果")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:/平台已返回发布结果/}));
    expect(screen.getByText("平台返回编号：remote-42")).toBeInTheDocument();
    expect(api.listPlatformPublishJobs).toHaveBeenLastCalledWith("task-1");
  });
});
