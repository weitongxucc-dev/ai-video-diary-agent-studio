import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";

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
const secondTask = { ...task, id: "task-2", name: "会员日复盘视频", updated_at: "2026-08-30T09:00:00Z" };

afterEach(() => vi.clearAllMocks());

describe("PublishingWorkspace", () => {
  it("renders real task progress with blocker reason and repair instead of invented publication metrics", async () => {
    api.listCreationTasks.mockResolvedValue({ items: [task], total_count: 1 });
    render(<PublishingWorkspace />);
    expect((await screen.findAllByText("新品讲解视频")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("需要处理").length).toBeGreaterThan(0);
    expect(screen.getByText("暂时不能生成配音")).toBeInTheDocument();
    expect(screen.getByText("原因：缺少已授权声音")).toBeInTheDocument();
    expect(screen.getByText("处理方式：登记声音档案后重试")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开任务/ })).toHaveAttribute("href", "/tasks/task-1");
    expect(screen.getByText(/暂不支持发布/)).toBeInTheDocument();
    expect(screen.queryByText("已发布", { exact: true })).not.toBeInTheDocument();
  });

  it("filters by real output readiness", async () => {
    api.listCreationTasks.mockResolvedValue({ items: [task], total_count: 1 });
    render(<PublishingWorkspace />);
    await screen.findAllByText("新品讲解视频");
    fireEvent.click(screen.getByRole("button", { name: "成片就绪" }));
    expect(screen.getByText("当前筛选下没有记录")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /打开任务/ })).not.toBeInTheDocument();
    expect(screen.queryByText("新品讲解视频")).not.toBeInTheDocument();
  });

  it("keeps old records visible but marked stale when a refresh fails", async () => {
    api.listCreationTasks.mockResolvedValueOnce({ items: [task], total_count: 1 })
      .mockRejectedValueOnce(new Error("本地服务未响应"));
    render(<PublishingWorkspace />);
    await screen.findAllByText("新品讲解视频");
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(screen.getByText("读取失败，以下统计和记录可能不是最新")).toBeInTheDocument();
    expect(screen.getByText(/本地服务未响应/)).toBeInTheDocument();
    expect(screen.getAllByText("新品讲解视频").length).toBeGreaterThan(0);
    expect(screen.getByText("详情可能不是最新")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取" })).toBeInTheDocument();
  });

  it("recovers after a failed refresh and replaces the stale records", async () => {
    api.listCreationTasks.mockResolvedValueOnce({ items: [task], total_count: 1 })
      .mockRejectedValueOnce(new Error("本地服务未响应"))
      .mockResolvedValueOnce({ items: [secondTask], total_count: 1 });
    render(<PublishingWorkspace />);
    await screen.findAllByText("新品讲解视频");
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect((await screen.findAllByText("会员日复盘视频")).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.queryByText("新品讲解视频")).not.toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("详情可能不是最新")).not.toBeInTheDocument();
  });

  it("falls back to the next record when the selected task disappears after refresh", async () => {
    api.listCreationTasks.mockResolvedValueOnce({ items: [task, secondTask], total_count: 2 })
      .mockResolvedValueOnce({ items: [secondTask], total_count: 1 });
    render(<PublishingWorkspace />);
    await screen.findAllByText("会员日复盘视频");
    expect(screen.getByRole("link", { name: /打开任务/ })).toHaveAttribute("href", "/tasks/task-1");
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.queryByText("新品讲解视频")).not.toBeInTheDocument());
    expect(screen.getAllByText("会员日复盘视频").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /打开任务/ })).toHaveAttribute("href", "/tasks/task-2");
  });

  it("discards an outdated refresh response so it cannot overwrite newer state", async () => {
    let resolveOutdated!: (response: { items: typeof task[]; total_count: number }) => void;
    api.listCreationTasks
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOutdated = resolve; }))
      .mockImplementationOnce(() => Promise.resolve({ items: [secondTask], total_count: 1 }));
    render(<StrictMode><PublishingWorkspace /></StrictMode>);
    expect((await screen.findAllByText("会员日复盘视频")).length).toBeGreaterThan(0);
    await act(async () => { resolveOutdated({ items: [task], total_count: 1 }); });
    expect(screen.queryByText("新品讲解视频")).not.toBeInTheDocument();
    expect(screen.getAllByText("会员日复盘视频").length).toBeGreaterThan(0);
  });

  it("keeps failed state primary when an output is ready and points to repair first", async () => {
    const failedWithOutput = { ...task, stage_status: "failed" as const, output_ready: true, stage_repair_action: "重新运行成片检查" };
    api.listCreationTasks.mockResolvedValue({ items: [failedWithOutput], total_count: 1 });
    render(<PublishingWorkspace />);
    expect((await screen.findAllByText("新品讲解视频")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("运行失败").length).toBeGreaterThan(1);
    expect(screen.getByText(/重新运行成片检查；已有成片可先检查保存/)).toBeInTheDocument();
    expect(screen.queryByText(/检查并保存本地成片/)).not.toBeInTheDocument();
    expect(screen.getByText("已生成，可检查")).toBeInTheDocument();
    expect(screen.getByText("原因：缺少已授权声音")).toBeInTheDocument();
  });

});
