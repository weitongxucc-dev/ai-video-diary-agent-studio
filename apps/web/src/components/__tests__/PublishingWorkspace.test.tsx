import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listCreationTasks: vi.fn(), listPublishingPlatforms: vi.fn(), listPublishingAccounts: vi.fn(),
  connectPublishingAccount: vi.fn(), verifyPublishingAccount: vi.fn(),
  createPlatformPublishJob: vi.fn(), listPlatformPublishJobs: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));

import { PublishingWorkspace } from "@/components/PublishingWorkspace";
import type { PlatformBrowserAccount, PlatformPublishJob } from "@/lib/types";

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
const platforms = { items: [
  { platform: "douyin", label: "抖音", browser_connect: true, browser_publish: true, verified: false, status: "candidate_unverified", requirement: "创作者页面仍待验收" },
  { platform: "wechat", label: "微信视频号", browser_connect: false, browser_publish: false, verified: false, status: "not_connected", requirement: "尚未接通" },
  { platform: "xiaohongshu", label: "小红书", browser_connect: false, browser_publish: false, verified: false, status: "not_connected", requirement: "尚未接通" },
] };

afterEach(() => vi.clearAllMocks());

function setup({ accounts = { items: [] }, jobs = { items: [] } }: {
  accounts?: { items: PlatformBrowserAccount[] };
  jobs?: { items: PlatformPublishJob[] };
} = {}) {
  api.listCreationTasks.mockResolvedValue({ items: [task], total_count: 1 });
  api.listPublishingPlatforms.mockResolvedValue(platforms);
  api.listPublishingAccounts.mockResolvedValue(accounts);
  api.listPlatformPublishJobs.mockResolvedValue(jobs);
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

  it("shows the exact account, current version and copy before creating a durable job", async () => {
    const accounts: { items: PlatformBrowserAccount[] } = { items: [{ workspace_id: "local", user_id: "member-1", platform: "douyin", account_ref: "creator-001", verified_at: "2026-10-09T00:00:00Z" }] };
    api.createPlatformPublishJob.mockResolvedValue({ id: "publish-1", task_id: "task-1", status: "queued", progress: 0, queue_state: "waiting", queue_wait_reason: null, failure_reason: null, impact: null, repair_action: null, events: [] });
    setup({ accounts });
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

  it("renders unknown publication outcomes as blocked from automatic retry", async () => {
    const jobs: { items: PlatformPublishJob[] } = { items: [{ id: "publish-unknown", task_id: "task-1", status: "interrupted", progress: 0, queue_state: "failed", queue_wait_reason: null, failure_reason: "publish_outcome_unknown", impact: "平台动作可能已经提交；系统不会自动重发。", repair_action: "先在平台账号后台核对结果。", events: [{ type: "platform_publish_unknown", payload: {}, created_at: "2026-10-09T00:00:00Z" }] }] };
    setup({ jobs });
    expect(await screen.findByText("结果待核对（不会自动重发）")).toBeInTheDocument();
    expect(screen.getByText("平台动作可能已经提交；系统不会自动重发。")).toBeInTheDocument();
    expect(screen.getByText("处理：先在平台账号后台核对结果。")).toBeInTheDocument();
  });
});
