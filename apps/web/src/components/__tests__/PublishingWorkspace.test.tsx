import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listCreationTasks: vi.fn() }));
vi.mock("@/lib/api", () => api);
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</>,
}));

import { PublishingWorkspace } from "@/components/PublishingWorkspace";

const task = {
  id: "task-1", workspace_id: "local", plan_id: "plan-1", name: "新品讲解视频", scenario: "产品介绍",
  stage: "narration" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const,
  progress: 45, recovery_point: "script_confirmed", selected_topic_id: "topic-1", confirmed_script_id: "script-1",
  narration_cache_key: null, montage_plan_id: null, output_ready: false,
  stage_reason: "缺少已授权声音", stage_impact: "暂时不能生成配音", stage_repair_action: "登记声音档案后重试", stage_retryable: true,
  created_at: "2026-08-30T08:00:00Z", updated_at: "2026-08-30T08:30:00Z",
};

afterEach(() => vi.clearAllMocks());

describe("PublishingWorkspace", () => {
  it("renders real task progress and blocker instead of invented publication metrics", async () => {
    api.listCreationTasks.mockResolvedValue({ items: [task], total_count: 1 });
    render(<PublishingWorkspace />);
    expect((await screen.findAllByText("新品讲解视频")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("需要处理").length).toBeGreaterThan(0);
    expect(screen.getByText("缺少已授权声音")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开创作任务/ })).toHaveAttribute("href", "/tasks/task-1");
    expect(screen.getByText(/不显示虚构播放量、点赞量或发布成功数/)).toBeInTheDocument();
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("filters by real output readiness", async () => {
    api.listCreationTasks.mockResolvedValue({ items: [task], total_count: 1 });
    render(<PublishingWorkspace />);
    await screen.findAllByText("新品讲解视频");
    fireEvent.click(screen.getByRole("button", { name: "成片就绪" }));
    expect(screen.getByText("当前筛选下没有记录")).toBeInTheDocument();
  });
});
