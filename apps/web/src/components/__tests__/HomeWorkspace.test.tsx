import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask, HomeAggregation, ProductionFoundation } from "@/lib/types";
import { HomeWorkspace, TrendChart, type HomeDashboardExample } from "@/components/HomeWorkspace";

const routerPush = vi.fn();
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: routerPush, refresh: vi.fn() }) }));

const task: CreationTask = {
  visibility: "private",
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
  it("groups real tasks before secondary metrics without inventing unavailable aggregates", () => {
    render(<HomeWorkspace foundation={null} tasks={[task]} />);
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("可见素材");
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("作品");
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("声音");
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("任务");
    expect(screen.getAllByText("—")).toHaveLength(7);
    expect(screen.getByText("尚无接收日聚合数据")).toBeInTheDocument();
    expect(screen.getByText("素材尚无可信行业分类")).toBeInTheDocument();
    expect(screen.getAllByText("标签统计未载入")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "待处理" })).toHaveTextContent("设备演示");
    expect(screen.getByRole("region", { name: "进行中" })).toHaveTextContent("没有进行中的任务");
    expect(screen.getByRole("region", { name: "最近完成" })).toHaveTextContent("还没有已完成任务");
    expect(screen.getByText("还没有可查看的本地成片")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /设备演示/ }));
    expect(screen.getByRole("dialog", { name: "任务卡片" })).toHaveTextContent("需要确认镜头");
  });

  it("shows local rendered output as an output, without claiming platform publishing", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, output_ready: true }]} />);
    expect(screen.getByText("当前工作区的本地成片，未代表平台发布")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /设备演示.*本地成片可查看/ })).toHaveAttribute("href", "/tasks/task-one");
    expect(screen.getAllByText("—")).toHaveLength(7);
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
    const metrics = screen.getByRole("region", { name: "工作空间七项概览" });
    expect(metrics).toHaveTextContent("12");
    expect(metrics).toHaveTextContent("7");
    expect(metrics).toHaveTextContent("3");
    expect(metrics).toHaveTextContent("4");
    expect(metrics).toHaveTextContent("1");
    const sevenCircles = screen.getByRole("img", { name: "素材接收趋势" }).querySelectorAll("circle");
    expect([...sevenCircles].reduce((sum, circle) => sum + Number(circle.getAttribute("aria-label")?.match(/接收 (\d+)/)?.[1] ?? 0), 0)).toBe(sample.assets.length);
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");
    const chart = screen.getByRole("img", { name: "素材接收趋势" });
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
    expect(screen.getByText("这段时间没有素材接收记录")).toBeInTheDocument();
    zero.rerender(<TrendChart values={points(1)} />);
    expect(screen.getByRole("img", { name: "素材接收趋势" }).querySelector("circle")).toHaveAttribute("cx", "143");
    zero.rerender(<TrendChart values={points(7)} />);
    const circles = screen.getByRole("img", { name: "素材接收趋势" }).querySelectorAll("circle");
    expect(circles).toHaveLength(7);
    expect(Number(circles[6].getAttribute("cx"))).toBe(274);
  });

  it("derives every example count and chart from one visible asset set", () => {
    const member = { ...sample, scopeLabel: "成员演示 · 我的及共享内容", assets: sample.assets.slice(0, 5), knowledgeSources: sample.knowledgeSources.slice(0, 2), works: sample.works.slice(0, 1) };
    render(<HomeWorkspace foundation={null} tasks={[]} dashboard={member} />);
    const metrics = screen.getByRole("region", { name: "工作空间七项概览" });
    expect(metrics).toHaveTextContent("5可见素材");
    expect(metrics).toHaveTextContent("5分析可用");
    expect(metrics).toHaveTextContent("0待分析");
    expect(metrics).toHaveTextContent("2知识来源");
    expect(metrics).toHaveTextContent("1作品");
    expect(screen.getByRole("button", { name: /设备5/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /门店4/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /门店4/ }));
    expect(screen.getByRole("dialog", { name: "示例素材筛选" })).toHaveTextContent("4 条对应示例素材");
  });

  it("shows real tag counts from the server insights and links each tag to a precise material filter", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} insights={{
      asset_count: 5,
      segment_count: 40,
      tags: [
        { tag: "门店", asset_count: 3, segment_count: 12, duration_seconds: 60, usage_count: 0 },
        { tag: "操作", asset_count: 1, segment_count: 28, duration_seconds: 20, usage_count: 0 },
      ],
      recommended_gaps: [],
    }} />);
    const cloud = screen.getByRole("heading", { name: "内容标签词云" }).closest("section") as HTMLElement;
    // 数字取素材数量 asset_count，不是片段数量 segment_count
    expect(within(cloud).getByRole("link", { name: /门店/ })).toHaveTextContent("门店3");
    expect(within(cloud).getByRole("link", { name: /操作/ })).toHaveTextContent("操作1");
    expect(within(cloud).getByRole("link", { name: /操作/ })).toHaveAttribute("href", "/library?tag=%E6%93%8D%E4%BD%9C");
  });

  it("keeps the tag card empty when the server reports no tags instead of borrowing segment counts", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} insights={{ asset_count: 0, segment_count: 9, tags: [], recommended_gaps: [] }} />);
    const cloud = screen.getByRole("heading", { name: "内容标签词云" }).closest("section") as HTMLElement;
    expect(within(cloud).getAllByText("当前工作区还没有素材标签")).toHaveLength(2);
    expect(within(cloud).queryByRole("link")).not.toBeInTheDocument();
  });

  it("hides taxonomy tags that no material carries, so a zero count cannot pose as a real tag", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} insights={{
      asset_count: 2,
      segment_count: 0,
      tags: [
        { tag: "门店", asset_count: 2, segment_count: 0, duration_seconds: 0, usage_count: 0 },
        // 服务端为默认分类表补的零值行：一个素材都没有，不能出现在词云里
        { tag: "设备", asset_count: 0, segment_count: 6, duration_seconds: 30, usage_count: 0 },
      ],
      recommended_gaps: [],
    }} />);
    const cloud = screen.getByRole("heading", { name: "内容标签词云" }).closest("section") as HTMLElement;
    expect(within(cloud).getByRole("link", { name: /门店/ })).toHaveTextContent("门店2");
    expect(within(cloud).queryByRole("link", { name: /设备/ })).not.toBeInTheDocument();
  });

  it("says the read failed when the insights could not be loaded, and does not blame an empty workspace", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} insights={null} />);
    const cloud = screen.getByRole("heading", { name: "内容标签词云" }).closest("section") as HTMLElement;
    expect(within(cloud).getAllByText("标签统计读取失败，请刷新重试")).toHaveLength(2);
    expect(within(cloud).queryByText("当前工作区还没有素材标签")).not.toBeInTheDocument();
  });

  const meta = { scope: "device_anonymous" as const, source: "material_asset", as_of: "2026-10-04T00:00:00Z" };
  const home: HomeAggregation = {
    days: 7,
    counts: [
      { key: "visible_assets", label: "可见素材", value: 12, meta: { ...meta, source: "production_repo.list_assets（既有可见性判据）" } },
      { key: "analysis_ready", label: "分析可用", value: 9, meta: { ...meta, source: "production_repo.list_assets（既有可见性判据）" } },
      { key: "knowledge_sources", label: "知识来源", value: 4, meta: { ...meta, source: "source_repository.list_knowledge_items_readable_by" } },
    ],
    trend: { days: 7, points: points(7), meta },
    device_queue: { counts: { "job.running": 1, "job.queued": 2, "intake.observing": 1, "job.succeeded": 4, "intake.blocked_disk": 2 }, records_total: 10, running: 2, queued: 3, meta },
    storage: {
      directory_state: "ready", bound_to_current_workspace: true,
      bound_to_another_workspace: false, configured_path_changed: false,
      capacity: { total_bytes: 512 * 1024 ** 3, used_bytes: 128 * 1024 ** 3, free_bytes: 384 * 1024 ** 3 },
      meta: { ...meta, source: "production_repo.material_ingestion_directory_state + psutil.disk_usage(已绑定目录)" },
    },
    entries: {
      queue: [{ task_id: "task-one", title: "设备演示", stage: "视频剪辑", status: "处理中", updated_at: "2026-10-04T00:00:00Z" }],
      todos: [{ task_id: "task-two", title: "门店介绍", stage: "画面确认", reason: "需要确认镜头", repair_action: "打开任务确认" }],
      works: [{ task_id: "task-three", title: "开场演示", updated_at: "2026-10-03T00:00:00Z" }],
      meta: { scope: "caller_visible", source: "creation_task", as_of: "2026-10-04T00:00:00Z" },
    },
    unknown: [{ key: "materials.by_industry", reason: "行业分类仍待定义，未统计" }],
  };

  it("renders the real anonymous overview, trend, storage and enterable entries", () => {
    render(<HomeWorkspace foundation={null} live={{ data: home, loading: false, error: null, days: 7, onDays: vi.fn(), onRetry: vi.fn() }} tasks={[]} />);
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("12");
    // 服务端没给的计数不补 0：显示未知，而不是 0。
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("未知");
    expect(screen.getByRole("img", { name: /素材接收趋势/ })).toBeInTheDocument();
    const queue = screen.getByRole("heading", { name: "我的任务" }).closest("section") as HTMLElement;
    expect(within(queue).getByRole("button", { name: /设备演示/ })).toBeInTheDocument();
    const todos = screen.getByRole("heading", { name: "待处理" }).closest("section") as HTMLElement;
    expect(within(todos).getByRole("link", { name: /门店介绍/ })).toHaveAttribute("href", "/tasks/task-two");
    const works = screen.getByRole("heading", { name: "近期成果" }).closest("section") as HTMLElement;
    expect(within(works).getByRole("link", { name: /开场演示/ })).toHaveAttribute("href", "/tasks/task-three");
    const storage = screen.getByRole("heading", { name: "存储" }).closest("section") as HTMLElement;
    expect(storage).toHaveTextContent("128.0 GB");
    expect(storage).toHaveTextContent("数据盘可用");
    expect(storage).toHaveTextContent("整机匿名汇总");
    expect(storage).toHaveTextContent("数据盘目录状态与容量");
    // 整机匿名运行只有数量，不含归属或名称。
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).not.toHaveTextContent("门店介绍");
    // 没有真实载体的统计项按服务端给出的原因如实显示。
    const unknown = screen.getByRole("heading", { name: "没有真实数据的项" }).closest("section") as HTMLElement;
    expect(unknown).toHaveTextContent("行业分类仍待定义，未统计");
    // unknown.key 是技术键，界面上给中文说法。
    expect(unknown).toHaveTextContent("素材行业分布");
    // 整机状态：处理中与排队分开显示，技术状态键不铺到界面。
    const stage = screen.getByText("整机任务与处理阶段").closest("details") as HTMLElement;
    expect(stage).toHaveTextContent("处理中 2 · 排队 3");
    expect(stage).toHaveTextContent("素材待确认 1");
    // 历史完成数单列，不混进处理中/排队。
    expect(stage).toHaveTextContent("任务已完成 4");
    expect(stage).not.toHaveTextContent("job.running");
    // 每个数字的口径与更新时间可查，且给的是真实来源不是内部短键。
    const details = screen.getByText("每个数字的口径与更新时间");
    expect(details.closest("details")).toHaveTextContent("素材库（按既有可见性判据统计）");
    expect(details.closest("details")).toHaveTextContent("知识库（当前身份可读的条目）");
    // 被数据盘阻塞的素材不是运行也不是排队，只在分布里出现。
    expect(stage).toHaveTextContent("素材被数据盘阻塞 2");
  });

  it("keeps a real retry action when a later read fails, and shows loading before any data", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<HomeWorkspace foundation={null} live={{ data: null, loading: true, error: null, days: 7, onDays: vi.fn(), onRetry }} tasks={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取整机概览…");
    rerender(<HomeWorkspace foundation={null} live={{ data: home, loading: false, error: "读取失败：503", days: 7, onDays: vi.fn(), onRetry }} tasks={[]} />);
    // 已有数据时不白屏：旧数据仍在，并给出可重试的错误。
    expect(screen.getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("12");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(onRetry).toHaveBeenCalled();
  });
});


it("does not warn about optional brand information but retains other dependency warnings", () => {
  const foundation = { readiness: { ready: false, blockers: [{ code: "brand_profile_missing", message: "品牌" }] } } as ProductionFoundation;
  const view = render(<HomeWorkspace foundation={foundation} tasks={[]} />);
  expect(screen.queryByText("品牌未设置")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "查看原因" })).not.toBeInTheDocument();
  view.rerender(<HomeWorkspace foundation={{ ...foundation, readiness: { ...foundation.readiness, blockers: [...foundation.readiness.blockers, { code: "voice_model_not_ready", message: "声音模型" }] } }} tasks={[]} />);
  expect(screen.getByRole("link", { name: "查看原因" })).toHaveAttribute("href", "/health");
});
