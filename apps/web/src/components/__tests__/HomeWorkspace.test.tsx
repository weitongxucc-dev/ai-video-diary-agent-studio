import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask } from "@/lib/types";
import { HomeWorkspace, TrendChart, type HomeDashboardExample } from "@/components/HomeWorkspace";

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
const points = (count: number) => Array.from({ length: count }, (_, index) => ({ date: "2026-09-" + String(index + 1).padStart(2, "0"), count: index % 5 }));
const sample: HomeDashboardExample = {
  scopeLabel: "主账号演示 · 本机与成员汇总", example: true,
  asOfDate: "2026-09-27",
  assets: Array.from({ length: 12 }, (_, index) => ({ id: "asset-" + index, title: "示例素材 " + (index + 1), receivedDate: "2026-09-" + String(21 + index % 7), analysisStatus: (index < 7 ? "ready" : index < 10 ? "pending" : "failed") as "ready" | "pending" | "failed", industry: index < 7 ? "设备" : "餐饮", tags: index < 4 ? ["门店", "门店"] : index < 6 ? ["操作"] : [] })),
  knowledgeSources: Array.from({ length: 4 }, (_, index) => ({ id: "source-" + index })),
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

  it.each([
    ["running", "视频剪辑：处理中 · 视频剪辑处理中"],
    ["waiting_for_user", "视频剪辑：等待确认 · 等待确认"],
    ["failed", "视频剪辑：需要处理 · 需要处理"],
  ] as const)("shows the same real %s stage in the list and reopened task card", (stageStatus, presentation) => {
    const current = {
      ...task, stage_status: stageStatus,
      stage_reason: stageStatus === "failed" ? "输出目录不可写" : null,
      stage_impact: stageStatus === "failed" ? "尚未生成新成片" : null,
      stage_repair_action: stageStatus === "failed" ? "选择可写目录后重试" : null,
    };
    const view = render(<HomeWorkspace foundation={null} tasks={[current]} />);
    const row = screen.getByRole("button", { name: /设备演示/ });
    expect(row).toHaveTextContent(presentation);
    fireEvent.click(row);
    const card = screen.getByRole("dialog", { name: "任务卡片" });
    expect(card).toHaveTextContent(presentation);
    expect(card).not.toHaveTextContent("68%");
    expect(within(card).queryByText("进度")).not.toBeInTheDocument();
    expect(card.querySelector(".home-task-card__progress")).toBeNull();
    if (stageStatus === "failed") {
      expect(card).toHaveTextContent("输出目录不可写");
      expect(card).toHaveTextContent("尚未生成新成片");
      expect(card).toHaveTextContent("选择可写目录后重试");
    }
    expect(within(card).getByRole("link", { name: "打开任务" })).toHaveAttribute("href", "/tasks/task-one");
    fireEvent.click(within(card).getByRole("button", { name: "对话修改" }));
    expect(routerPush).toHaveBeenCalledWith("/tasks/task-one");
    fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
    view.rerender(<HomeWorkspace foundation={null} tasks={[{ ...current, updated_at: "2026-10-03T09:00:00Z" }]} />);
    fireEvent.click(screen.getByRole("button", { name: /设备演示/ }));
    const reopened = screen.getByRole("dialog", { name: "任务卡片" });
    expect(reopened).toHaveTextContent(presentation);
    expect(reopened).toHaveTextContent("2026/10/03");
  });

  it("labels isolated sample figures and lets users inspect ranges and tag choices", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} dashboard={sample} />);
    expect(screen.getByText("示例数据 · 不连接真实服务")).toBeInTheDocument();
    const metrics = screen.getByRole("region", { name: "工作空间五项概览" });
    expect(metrics).toHaveTextContent("12");
    expect(metrics).toHaveTextContent("7");
    expect(metrics).toHaveTextContent("3");
    expect(metrics).toHaveTextContent("4");
    expect(metrics).toHaveTextContent("1");
    const sevenCircles = screen.getByRole("img", { name: "示例素材接收趋势" }).querySelectorAll("circle");
    expect([...sevenCircles].reduce((sum, circle) => sum + Number(circle.getAttribute("aria-label")?.match(/接收 (\d+)/)?.[1] ?? 0), 0)).toBe(sample.assets.length);
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");
    const chart = screen.getByRole("img", { name: "示例素材接收趋势" });
    const circles = chart.querySelectorAll("circle");
    expect(circles).toHaveLength(30);
    expect(Number(circles[0].getAttribute("cx"))).toBe(12);
    expect(Number(circles[29].getAttribute("cx"))).toBe(274);
    expect([...circles].reduce((sum, circle) => sum + Number(circle.getAttribute("aria-label")?.match(/接收 (\d+)/)?.[1] ?? 0), 0)).toBe(sample.assets.length);
    expect(screen.getByText("2026-09-27")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /门店4/ }));
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("4 条对应示例素材");
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("示例素材 1");
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).not.toHaveTextContent("示例素材 12");
    fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
    fireEvent.keyDown(screen.getByRole("button", { name: "查看设备示例素材" }), { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("7 条对应示例素材");
    fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
    fireEvent.click(screen.getByRole("button", { name: /餐饮5/ }));
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("5 条对应示例素材");
  });

  it("keeps all 0, 1 and 7 trend points inside the chart", () => {
    const zero = render(<TrendChart values={[]} />);
    expect(screen.getByText("这段示例期间没有素材接收记录")).toBeInTheDocument();
    zero.rerender(<TrendChart values={points(1)} />);
    expect(screen.getByRole("img", { name: "示例素材接收趋势" }).querySelector("circle")).toHaveAttribute("cx", "143");
    zero.rerender(<TrendChart values={points(7)} />);
    const circles = screen.getByRole("img", { name: "示例素材接收趋势" }).querySelectorAll("circle");
    expect(circles).toHaveLength(7);
    expect(Number(circles[6].getAttribute("cx"))).toBe(274);
  });

  it("derives every example count and chart from one visible asset set", () => {
    const member = { ...sample, scopeLabel: "成员演示 · 我的及共享内容", assets: sample.assets.slice(0, 5), knowledgeSources: sample.knowledgeSources.slice(0, 2), works: sample.works.slice(0, 1) };
    render(<HomeWorkspace foundation={null} tasks={[]} dashboard={member} />);
    const metrics = screen.getByRole("region", { name: "工作空间五项概览" });
    expect(metrics).toHaveTextContent("5可见素材");
    expect(metrics).toHaveTextContent("5分析可用");
    expect(metrics).toHaveTextContent("0待分析");
    expect(metrics).toHaveTextContent("2知识来源");
    expect(metrics).toHaveTextContent("1作品记录");
    expect(screen.getByRole("button", { name: /设备5/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /门店4/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /门店4/ }));
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("4 条对应示例素材");
  });
});
