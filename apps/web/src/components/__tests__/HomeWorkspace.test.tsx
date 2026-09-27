import { fireEvent, render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask } from "@/lib/types";
import { HomeWorkspace, type HomeDashboardExample } from "@/components/HomeWorkspace";

const routerPush = vi.fn();
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: routerPush, refresh: vi.fn() }) }));

const task: CreationTask = {
  id: "task-one", workspace_id: "workspace-one", plan_id: "plan-one", name: "设备演示",
  scenario: "演示", stage: "montage", stage_status: "waiting_for_user", status: "waiting_for_user",
  progress: 68, recovery_point: "检查画面", selected_topic_id: null, confirmed_script_id: null,
  narration_cache_key: null, montage_plan_id: null, current_timeline_version_id: null,
  timeline_sequence: 0, can_undo: false, output_ready: false, stage_reason: "需要确认镜头",
  stage_impact: null, stage_repair_action: null, stage_retryable: null,
  created_at: "2026-09-27T08:00:00Z", updated_at: "2026-09-27T08:00:00Z",
};
const sample: HomeDashboardExample = {
  scopeLabel: "主账号演示 · 本机与成员汇总", example: true,
  counts: [
    { label: "可见素材", value: 12 }, { label: "分析可用", value: 8 },
    { label: "待分析", value: 3 }, { label: "知识来源", value: 4 },
    { label: "作品记录", value: 2 },
  ],
  trend: { sevenDays: [1, 0, 3, 2, 1, 4, 1], thirtyDays: [4, 2, 1, 5, 3, 2, 6] },
  industries: [{ label: "设备", count: 7 }, { label: "餐饮", count: 5 }],
  tags: [{ label: "门店", count: 4 }, { label: "操作", count: 2 }],
  queue: [{ id: "queue-one", title: "产品演示", stage: "制作中", detail: "示例队列" }],
  works: [{ id: "work-one", title: "门店介绍", status: "本地成片" }],
};

beforeEach(() => routerPush.mockReset());
describe("HomeWorkspace", () => {
  it("keeps the v4 dashboard structure without inventing unavailable production aggregates", () => {
    render(<HomeWorkspace foundation={null} tasks={[task]} />);
    expect(screen.getByRole("region", { name: "工作空间五项概览" })).toHaveTextContent("可见素材");
    expect(screen.getByRole("region", { name: "工作空间五项概览" })).toHaveTextContent("作品记录");
    expect(screen.getAllByText("—")).toHaveLength(5);
    expect(screen.getByText("尚无接收日聚合数据")).toBeInTheDocument();
    expect(screen.getByText("素材尚无可信行业分类")).toBeInTheDocument();
    expect(screen.getByText("尚无按素材去重的标签统计")).toBeInTheDocument();
    expect(screen.getByText("当前工作区任务；整机队列待接入")).toBeInTheDocument();
    expect(screen.getByText("还没有可查看的本地成片")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /设备演示/ }));
    expect(screen.getByRole("dialog", { name: "任务卡片" })).toHaveTextContent("需要确认镜头");
  });

  it("shows local rendered output as an output, without claiming platform publishing", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, output_ready: true }]} />);
    expect(screen.getByText("当前工作区的本地成片，未代表平台发布")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /设备演示.*本地成片可查看/ })).toHaveAttribute("href", "/tasks/task-one");
    expect(screen.getAllByText("—")).toHaveLength(5);
  });

  it("labels isolated sample figures and lets users inspect ranges and tag choices", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} dashboard={sample} />);
    expect(screen.getByText("示例数据 · 不连接真实服务")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "工作空间五项概览" })).toHaveTextContent("12");
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /门店4/ }));
    expect(screen.getByText("示例已选择：门店")).toBeInTheDocument();
  });
});
