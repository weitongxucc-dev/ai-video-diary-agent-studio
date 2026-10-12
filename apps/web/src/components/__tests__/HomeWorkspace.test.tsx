import { chooseSelectValue } from "./select-interaction";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask, HomeAggregation, MaterialAsset, ProductionFoundation } from "@/lib/types";
import { HomeWorkspace, TrendChart } from "@/components/HomeWorkspace";
import { home100LayoutTasks } from "./home-100-layout-data";
import { ASSISTANT_MATERIAL_REFERENCE_EVENT } from "@/lib/assistant-reference";

const routerPush = vi.fn();
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: routerPush, refresh: vi.fn() }) }));
const task: CreationTask = {
  visibility: "private", id: "task-one", workspace_id: "workspace-one", plan_id: "plan-one", name: "设备演示",
  scenario: "演示", stage: "montage", stage_status: "waiting_for_user", status: "waiting_for_user",
  progress: 68, recovery_point: "检查画面", selected_topic_id: null, confirmed_script_id: null,
  narration_cache_key: null, montage_plan_id: null, current_timeline_version_id: null,
  timeline_sequence: 0, can_undo: false, output_ready: false, stage_reason: "需要确认镜头",
  stage_impact: null, stage_repair_action: null, stage_retryable: null,
  created_at: "2026-10-09T08:00:00Z", updated_at: "2026-10-09T08:00:00Z",
};
const meta = { scope: "caller_visible" as const, source: "production_repo.list_assets", as_of: "2026-10-10T00:00:00Z" };
const home: HomeAggregation = {
  days: 7,
  counts: [{ key: "visible_assets", label: "可见素材", value: 12, meta }, { key: "knowledge_sources", label: "知识来源", value: 4, meta }],
  trend: { days: 7, points: [{ date: "2026-10-09", count: 3 }], meta },
  device_queue: { counts: { "job.running": 1, "job.succeeded": 4 }, records_total: 5, running: 1, queued: null, meta: { ...meta, scope: "device_anonymous" } },
  storage: { directory_state: "ready", bound_to_current_workspace: true, bound_to_another_workspace: false, configured_path_changed: false, capacity: null, meta },
  entries: {
    queue: [{ task_id: "task-one", title: "设备演示", stage: "montage", status: "running", updated_at: task.updated_at }],
    todos: [{ task_id: "task-one", title: "设备演示", stage: "montage", reason: "需要确认镜头", repair_action: "打开任务确认" }],
    works: [{ task_id: "task-one", title: "设备演示", updated_at: task.updated_at }], meta,
  },
  unknown: [{ key: "materials.by_industry", reason: "行业分类仍待定义，未统计" }, { key: "voices", reason: "声音库统计暂不能读取" }],
};
const live = (data: HomeAggregation | null = home) => ({ data, loading: false, error: null, days: 7 as const, onDays: vi.fn(), onRetry: vi.fn() });
const asset: MaterialAsset = {
  id: "asset-real", file_name: "真实画面.jpg", media_type: "image", user_tags: ["门店"], user_description: "原始图片",
  updated_at: "2026-10-09T08:00:00Z",
} as MaterialAsset;
const overview = (name: string) => fireEvent.click(screen.getByRole("tab", { name: new RegExp(name) }));
beforeEach(() => routerPush.mockReset());

describe("HomeWorkspace v3.2 real facts and overview linkage", () => {
  it("keeps the stage glyph stable across state changes in board and list views", () => {
    const states = ["not_started", "running", "waiting_for_user", "completed", "failed"] as const;
    render(<HomeWorkspace foundation={null} tasks={states.map(status => ({...task, id:status, name:status, stage:"narration", stage_status:status, status: status === "not_started" ? "created" : status}))} />);
    const check = () => {
      for (const status of states) {
        const row = screen.getByRole("button", {name:new RegExp(`^${status}`)});
        expect(row.querySelector('[data-task-stage="narration"]')).toHaveClass("lucide-mic");
        expect(row.querySelectorAll(".is-spinning")).toHaveLength(status === "running" ? 1 : 0);
      }
      expect(screen.getByText("配音待确认")).toBeVisible();
      expect(screen.getByText("配音未完成")).toBeVisible();
    };
    check();
    fireEvent.click(screen.getByRole("button", {name:"切换为列表"}));
    check();
  });
  it("shows a selected default trend, zero coordinates, grouped range/time and one metadata entry", () => {
    const zero={...home,trend:{...home.trend,points:[{date:"2026-10-09",count:0},{date:"2026-10-10",count:0}]}};
    render(<HomeWorkspace foundation={null} tasks={[]} live={live(zero)} />);
    expect(screen.getByRole("tab",{name:"素材接收趋势"})).toHaveAttribute("aria-selected","true");
    const chart=screen.getByRole("img",{name:"素材接收趋势"});
    expect(chart.querySelector("[data-zero-line]")).toBeInTheDocument();
    expect(chart.querySelectorAll("circle")).toHaveLength(2);
    const ranges=screen.getByLabelText("趋势范围");
    expect(ranges.parentElement?.querySelector("time")).toHaveAttribute("dateTime",meta.as_of);
    expect(screen.getAllByRole("button",{name:"趋势统计口径"})).toHaveLength(1);
    expect(screen.getByRole("textbox",{name:"搜索任务"}).closest("label")).toHaveAttribute("data-input-group");
    fireEvent.click(screen.getByRole("button",{name:"趋势统计口径"}));
    expect(screen.getByRole("dialog",{name:"趋势统计口径"})).toHaveTextContent("单位：条");
  });

  it("keeps 100 layout-only tasks in bounded columns and paginates the already-read list", () => {
    const {container}=render(<HomeWorkspace foundation={null} tasks={home100LayoutTasks} />);
    expect(screen.getByRole("region",{name:"待处理"}).querySelector('[class*="taskColumn"]')).toHaveAttribute("tabIndex","0");
    expect(screen.getByRole("region",{name:"待处理"}).querySelectorAll("button")).toHaveLength(40);
    expect(screen.getByRole("region",{name:"进行中"}).querySelectorAll("button")).toHaveLength(30);
    expect(screen.getByRole("region",{name:"最近完成"}).querySelectorAll("button")).toHaveLength(30);
    expect(container.querySelectorAll('[class*="taskColumn"]')).toHaveLength(3);
    fireEvent.click(screen.getByRole("button",{name:"切换为列表"}));
    expect(container.querySelector('[aria-label="创作任务列表"]')?.querySelectorAll("button")).toHaveLength(25);
    fireEvent.click(screen.getByRole("button",{name:"下一页"}));
    expect(screen.getByRole("navigation",{name:"已读取任务分页"})).toHaveTextContent("2 / 4 · 已读 100 项");
    fireEvent.change(screen.getByRole("textbox",{name:"搜索任务"}),{target:{value:"布局样例 100"}});
    expect(screen.getByRole("button",{name:/布局样例 100/})).toBeInTheDocument();
    expect(screen.queryByRole("navigation",{name:"已读取任务分页"})).not.toBeInTheDocument();
  });

  it("weights cloud words by exact frequency, translates known enum labels, and retains the raw query", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} insights={{asset_count:100,segment_count:0,recommended_gaps:[],tags:[
      {tag:"用途:a_roll",asset_count:80,segment_count:80,duration_seconds:0,usage_count:0},
      {tag:"用途:b_roll",asset_count:2,segment_count:2,duration_seconds:0,usage_count:0},
    ]}} />);
    overview("内容标签与行业");
    const high=screen.getByRole("link",{name:/用途:人物主镜头/}),low=screen.getByRole("link",{name:/用途:补充画面/});
    expect(high).toHaveAttribute("href","/library?tag="+encodeURIComponent("用途:a_roll"));
    expect(high).toHaveAttribute("data-frequency","80");
    expect(Number.parseFloat(high.style.fontSize)).toBeGreaterThan(Number.parseFloat(low.style.fontSize));
    expect(high.parentElement?.className).toContain("cloudCore");
    expect(screen.queryByText(/用途:a_roll/)).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("tab",{name:"内容标签与行业"}),{key:"ArrowRight"});
    expect(screen.getByRole("tab",{name:"设备与存储"})).toHaveFocus();
    expect(screen.getByRole("tab",{name:"设备与存储"})).toHaveAttribute("aria-selected","true");
  });
  it("keeps one row status and next action, and folds raw diagnostics in the named task detail", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, stage: "topic", stage_status: "failed", status: "failed",
      stage_reason: "provider_output_invalid", stage_repair_action: "核对资料引用后重试。" }]} live={live()} recentAssets={[]} />);
    const row = screen.getByRole("button", { name: /设备演示/ });
    expect(row).toHaveTextContent("选题未生成");
    expect(row.textContent?.match(/选题未生成/g)).toHaveLength(1);
    expect(row).toHaveTextContent("查看选题");
    expect(row).not.toHaveTextContent("核对资料");
    expect(screen.queryByText("provider_output_invalid")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /设备演示/ }));
    const card = screen.getByRole("dialog", { name: "设备演示" });
    expect(within(card).getByText("选题没有生成成功")).toBeVisible();
    expect(card).toHaveTextContent("核对资料引用后重试。");
    expect(within(card).getByText("provider_output_invalid")).not.toBeVisible();
    expect(within(card).getByText("原因与任务记录").closest("details")).not.toHaveAttribute("open");
    expect(within(card).getAllByRole("link")).toHaveLength(1);
    expect(within(card).getByRole("link", { name:"查看选题" })).toHaveAttribute("href", "/tasks/task-one");
  });
  it("merges tasks once, trusts the current stage, and keeps previous output separate from a current failure", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, stage_status: "failed", status: "failed", output_ready: true, stage_reason: "新版本检查失败", stage_repair_action: "选择可写位置后重试" }]} live={live()} recentAssets={[]} />);
    expect(screen.getAllByText("设备演示")).toHaveLength(1);
    expect(screen.getByRole("region", { name: "待处理" })).not.toHaveTextContent("新版本检查失败");
    expect(screen.getByRole("region", { name: "进行中" })).not.toHaveTextContent("设备演示");
    expect(screen.getByRole("region", { name: "最近完成" })).not.toHaveTextContent("设备演示");
    fireEvent.click(screen.getByRole("button", { name: /设备演示/ }));
    const card = screen.getByRole("dialog", { name: "设备演示" });
    expect(card).toHaveTextContent("选择可写位置后重试");
    expect(within(card).getByRole("link", { name: "查看视频剪辑" })).toHaveAttribute("href", "/tasks/task-one");
    expect(card).not.toHaveTextContent("68%");
    expect(within(card).getByText("已有本地成片可查看")).toBeVisible();
  });

  it("does not infer failure or waiting status from a todo that omits the task state", async () => {
    render(<HomeWorkspace foundation={null} tasks={[]} live={live({ ...home, entries: { ...home.entries, queue: [], works: [] } })} />);
    const pending = screen.getByRole("region", { name: "待处理" });
    expect(pending).toHaveTextContent("待处理");
    expect(pending).not.toHaveTextContent("需要确认镜头");
    expect(pending).not.toHaveTextContent("等待确认");
    await chooseSelectValue(screen.getByRole("combobox", { name: "任务状态" }), "failed");
    expect(screen.getByText("没有匹配的任务")).toBeInTheDocument();
  });

  it("does not treat a created task or a ready previous output as running or completed", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, status: "created", stage_status: "running", output_ready: true }]} live={live({ ...home, entries: { ...home.entries, todos: [], works: [] } })} />);
    expect(screen.getByRole("region", { name: "待处理" })).toHaveTextContent("剪辑待开始");
    expect(screen.getByRole("region", { name: "进行中" })).not.toHaveTextContent("设备演示");
    expect(screen.getAllByText("设备演示")).toHaveLength(1);
  });

  it("shows a completed step without claiming a produced video", () => {
    render(<HomeWorkspace foundation={null} tasks={[{ ...task, stage_status: "completed", status: "completed", output_ready: false }]} />);
    expect(screen.getByRole("region", { name: "最近完成" })).toHaveTextContent("查看已完成步骤（尚无成片）");
    expect(screen.queryByText("已有本地成片可查看")).not.toBeInTheDocument();
  });

  it("keeps task search, filters, list mode, trend range and DOM nodes when overviews switch", async () => {
    const state = live();
    const { container } = render(<HomeWorkspace foundation={null} tasks={[task]} live={state} recentAssets={[asset]} />);
    const taskBody = container.querySelector("#home-task-body")!;
    const recentBody = container.querySelector("#home-recent-body")!;
    const trendBody = container.querySelector("#home-trend-body")!;
    expect(taskBody).not.toHaveAttribute("hidden");
    expect(recentBody).not.toHaveAttribute("hidden");
    expect(trendBody).not.toHaveAttribute("hidden");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务" }), { target: { value: "设备" } });
    await chooseSelectValue(screen.getByRole("combobox", { name: "任务状态" }), "waiting_for_user");
    fireEvent.click(screen.getByRole("button", { name: "切换为列表" }));
    const row = screen.getByRole("button", { name: /设备演示/ });
    overview("素材接收趋势");
    expect(taskBody).toHaveAttribute("hidden");
    expect(recentBody).toHaveAttribute("hidden");
    expect(trendBody).not.toHaveAttribute("hidden");
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    expect(state.onDays).toHaveBeenCalledOnce();
    overview("设备与存储");
    expect(trendBody).toHaveAttribute("hidden");
    expect(container.querySelector("#home-device-body")).not.toHaveAttribute("hidden");
    expect(state.onRetry).not.toHaveBeenCalled();
    overview("内容标签与行业");
    expect(container.querySelector("#home-device-body")).toHaveAttribute("hidden");
    fireEvent.click(screen.getByRole("button", { name: "最近素材" }));
    expect(taskBody).not.toHaveAttribute("hidden");
    expect(recentBody).not.toHaveAttribute("hidden");
    expect(container.querySelector("#home-task-body")).toBe(taskBody);
    expect(screen.getByRole("button", { name: /设备演示/ })).toBe(row);
    expect(screen.getByRole("textbox", { name: "搜索任务" })).toHaveValue("设备");
    expect(screen.getByRole("combobox", { name: "任务状态" })).toHaveAttribute("data-selected-value", "waiting_for_user");
    expect(screen.getByRole("button", { name: "切换为看板" })).toBeInTheDocument();
    overview("素材接收趋势");
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", {name:"返回任务"}));
    expect(taskBody).not.toHaveAttribute("hidden");
  });

  it("scrolls only the right content when opening and restoring an overview", () => {
    const { container } = render(<HomeWorkspace foundation={null} tasks={[task]} live={live()} />);
    const right = container.querySelector('[class*="content"]') as HTMLElement;
    right.scrollTop = 250;
    overview("设备与存储");
    expect(right.scrollTop).toBe(0);
    right.scrollTop = 120;
    fireEvent.click(screen.getByRole("button", { name: "创作任务" }));
    expect(right.scrollTop).toBe(0);
  });

  it("renders a single empty result and restores search focus after clearing", () => {
    render(<HomeWorkspace foundation={null} tasks={[task]} />);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索任务" }), { target: { value: "不存在" } });
    expect(screen.getAllByText("没有匹配的任务")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByRole("textbox", { name: "搜索任务" })).toHaveFocus();
    expect(screen.getByRole("button", { name: /设备演示/ })).toBeInTheDocument();
  });

  it("uses real media identity and version for the preview and conversation reference", () => {
    const listener = vi.fn();
    window.addEventListener(ASSISTANT_MATERIAL_REFERENCE_EVENT, listener);
    render(<HomeWorkspace foundation={null} tasks={[]} recentAssets={[asset]} />);
    fireEvent.click(screen.getByRole("button", { name: /真实画面.jpg/ }));
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ selection: { scope: "asset", asset_id: "asset-real", expected_updated_at: asset.updated_at }, label: asset.file_name });
    const card = screen.getByRole("dialog", { name: asset.file_name });
    expect(within(card).getByRole("img", { name: `原素材预览：${asset.file_name}` })).toHaveAttribute("src", "/api/production/materials/asset-real/media");
    fireEvent.error(within(card).getByRole("img"));
    expect(card).toHaveTextContent("预览暂不可用");
    window.removeEventListener(ASSISTANT_MATERIAL_REFERENCE_EVENT, listener);
  });

  it("preserves all count facts and their metadata behind the content overview", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} live={live()} />);
    expect(screen.queryByRole("region", { name: "工作空间七项概览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未知声音" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看内容统计范围" }));
    const card = screen.getByRole("dialog", { name: "内容总览" });
    expect(within(card).getByRole("region", { name: "工作空间七项概览" })).toHaveTextContent("12可见素材");
    expect(card).toHaveTextContent("当前身份可见");
    expect(card).toHaveTextContent("素材库（按既有可见性判据统计）");
    expect(card).toHaveTextContent("声音库统计暂不能读取");
    expect(card).toHaveTextContent("2026/10/10");
  });

  it("distinguishes unknown device values from zero, keeps historical jobs out of running, and draws no fake capacity", () => {
    render(<HomeWorkspace foundation={null} tasks={[]} live={live()} />);
    overview("设备与存储");
    const device = screen.getByRole("tabpanel", { name: "设备与存储" });
    expect(within(device).getByText("处理中").closest("div")).toHaveTextContent("处理中1");
    expect(within(device).getByText("排队").closest("div")).toHaveTextContent("排队未知");
    expect(device).not.toHaveTextContent("任务已完成 4");
    fireEvent.click(screen.getByRole("button", { name:"设备范围与诊断" }));
    expect(screen.getByRole("dialog", { name:"设备范围与诊断" })).toHaveTextContent("任务已完成 4");
    expect(device).not.toHaveTextContent("job.running");
    expect(within(device).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(device).getByRole("link", { name: "检查保存位置" })).toHaveAttribute("href", "/settings?section=paths");
  });

  it("uses asset_count for tags, preserves exact long labels, and excludes taxonomy zero rows", () => {
    const longTag = "场景:电视访谈节目现场，背景为城市夜景和窗帘";
    render(<HomeWorkspace foundation={null} tasks={[]} insights={{ asset_count: 5, segment_count: 40, tags: [
      { tag: "门店", asset_count: 3, segment_count: 12, duration_seconds: 60, usage_count: 0 },
      { tag: "设备", asset_count: 0, segment_count: 20, duration_seconds: 60, usage_count: 0 },
      { tag: longTag, asset_count: 2, segment_count: 8, duration_seconds: 60, usage_count: 0 },
    ], recommended_gaps: [] }} />);
    overview("内容标签与行业");
    expect(screen.getByRole("link", { name: /门店/ })).toHaveTextContent("门店3");
    expect(screen.queryByRole("link", { name: /设备/ })).not.toBeInTheDocument();
    const more = screen.getByText("更多标签（1）").closest("details")!;
    fireEvent.click(within(more).getByText("更多标签（1）"));
    expect(within(more).getByRole("link", { name: longTag })).toHaveAttribute("href", "/library?tag=" + encodeURIComponent(longTag));
  });

  it("offers a compact start only when all seven known counts and actual task entries are empty", () => {
    const empty = { ...home, counts: ["visible_assets", "analysis_ready", "analysis_pending", "knowledge_sources", "voices", "tasks", "works"].map(key => ({ key, label: key, value: 0, meta })), trend: { ...home.trend, points: [{ date: "2026-10-09", count: 0 }] }, entries: { ...home.entries, queue: [], todos: [], works: [] } };
    const view = render(<HomeWorkspace foundation={null} tasks={[]} live={live(empty)} recentAssets={[]} />);
    expect(screen.getByRole("region", { name: "开始创作" })).toBeInTheDocument();
    overview("素材接收趋势");
    expect(screen.getByRole("img", { name: "素材接收趋势" }).querySelector("[data-zero-line]")).toBeInTheDocument();
    expect(screen.getByText("这段时间没有素材接收记录")).toBeInTheDocument();
    view.rerender(<HomeWorkspace foundation={null} tasks={[]} live={live({ ...empty, counts: empty.counts.filter(item => item.key !== "voices") })} />);
    fireEvent.click(screen.getByRole("button", { name: "创作任务" }));
    expect(screen.queryByRole("region", { name: "开始创作" })).not.toBeInTheDocument();
  });

  it("keeps a failed read distinct from an empty workspace and hides raw diagnostics", () => {
    const state = { ...live(null), error: "HTTP 503 /private/path" };
    render(<HomeWorkspace foundation={null} tasks={[]} live={state} />);
    expect(screen.getByRole("alert")).toHaveTextContent("首页数据读取失败");
    expect(screen.getByText("HTTP 503 /private/path").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(state.onRetry).toHaveBeenCalledOnce();
    expect(screen.queryByRole("region", { name: "开始创作" })).not.toBeInTheDocument();
  });

  it.each([
    ["versioned_voice_authorization_missing", "/settings?section=voices", "添加或检查声音"],
    ["voice_model_not_ready", "/settings?section=paths", "检查本地模型"],
    ["source_pool_empty", "/sources", "管理选题来源"],
    ["approved_materials_empty", "/library", "补充或检查素材"],
  ])("retains the real %s preparation action below the primary work", (code, href, label) => {
    const foundation = { readiness: { ready: false, blockers: [{ code, message: "真实缺项" }, { code: "brand_profile_missing", message: "品牌" }] } } as ProductionFoundation;
    render(<HomeWorkspace foundation={foundation} tasks={[]} />);
    expect(screen.getByText("制作前还有 1 项需要准备。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name:"查看准备事项" }));
    expect(screen.getByRole("region", { name: "制作准备事项" })).toHaveTextContent("真实缺项");
    expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
    expect(screen.queryByText("品牌")).not.toBeInTheDocument();
  });

  it("keeps zero, one and thirty real trend points inside the plot", () => {
    const view = render(<TrendChart values={[]} />);
    expect(screen.getByText("接收趋势暂无数据")).toBeInTheDocument();
    view.rerender(<TrendChart values={[{ date: "2026-10-09", count: 1 }]} />);
    expect(screen.getByRole("img").querySelector("circle")).toHaveAttribute("cx", "143");
    view.rerender(<TrendChart values={Array.from({ length: 30 }, (_, index) => ({ date: String(index), count: index % 5 }))} />);
    const circles = screen.getByRole("img").querySelectorAll("circle");
    expect(circles).toHaveLength(30);
    expect(circles[29]).toHaveAttribute("cx", "274");
  });
});
