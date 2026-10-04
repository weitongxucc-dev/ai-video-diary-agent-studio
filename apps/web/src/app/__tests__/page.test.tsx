import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HomeAggregation, ProductionFoundation } from "@/lib/types";

// Page-level test: render the REAL AppShell and HomeWorkspace; mock only the
// Next.js navigation boundary and the API service boundaries. The AppShell
// slot contract (conversation slot, panel behavior) is covered by
// components/__tests__/AppShell.test.tsx and is not re-implemented here.

let pathname = "/";
let search = "";

const apiMocks = vi.hoisted(() => ({
  getHomeDashboard: vi.fn(),
  getMaterialLibraryInsights: vi.fn(),
  getProductionFoundation: vi.fn(),
  listCreationTasks: vi.fn(),
}));
const identityState = vi.hoisted(() => ({ token: "tok-a" as string | null, ready: true }));
const routerPush = vi.fn();

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("next/image", () => ({
  default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} />,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ back: vi.fn(), refresh: vi.fn(), push: routerPush }),
  useSearchParams: () => new URLSearchParams(search),
}));
// 身份钩子照既有约定返回 token/ready/refresh；页面只消费它，不自己读身份。
vi.mock("@/hooks/useLocalMemberIdentity", () => ({
  useLocalMemberIdentity: () => ({ token: identityState.token, ready: identityState.ready, role: "owner", error: null, refresh: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  getHomeDashboard: apiMocks.getHomeDashboard,
  getMaterialLibraryInsights: apiMocks.getMaterialLibraryInsights,
  getProductionFoundation: apiMocks.getProductionFoundation,
  confirmCreationPlan: vi.fn(),
  confirmCreationTaskEdit: vi.fn(),
  CreationPlanApiError: class CreationPlanApiError extends Error {},
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }),
  listCreationTasks: apiMocks.listCreationTasks,
  previewCreationPlan: vi.fn(),
  sendCreationTaskAssistantTurn: vi.fn(),
  sendWorkspaceAssistantPlanTurn: vi.fn(),
  undoCreationTaskTimeline: vi.fn(),
}));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: vi.fn() }));

import HomePage from "@/app/page";

const meta = { scope: "caller_visible" as const, source: "production_repo.list_assets（既有可见性判据）", as_of: "2026-10-04T00:00:00Z" };

const home: HomeAggregation = {
  days: 7,
  counts: [
    { key: "visible_assets", label: "可见素材", value: 12, meta },
    { key: "analysis_ready", label: "分析可用", value: 9, meta },
    { key: "analysis_pending", label: "待分析", value: 3, meta },
    { key: "knowledge_sources", label: "知识来源", value: 4, meta: { ...meta, source: "source_repository.list_knowledge_items_readable_by" } },
    { key: "voices", label: "声音", value: 2, meta: { ...meta, scope: "workspace", source: "production_repo.list_versioned_voice_profiles" } },
    { key: "tasks", label: "任务", value: 5, meta: { ...meta, source: "production_repo.list_creation_tasks" } },
    { key: "works", label: "作品", value: 1, meta: { ...meta, source: "production_repo.list_creation_tasks.output_ready" } },
  ],
  trend: { days: 7, points: [], meta },
  device_queue: { counts: { "job.running": 1, "job.queued": 2 }, records_total: 3, running: 1, queued: 2, meta: { ...meta, scope: "device_anonymous", source: "production_repo.job_runs" } },
  storage: {
    directory_state: "ready", bound_to_current_workspace: true, bound_to_another_workspace: false, configured_path_changed: false,
    capacity: { total_bytes: 512 * 1024 ** 3, used_bytes: 128 * 1024 ** 3, free_bytes: 384 * 1024 ** 3 },
    meta: { ...meta, source: "production_repo.material_ingestion_directory_state + psutil.disk_usage(已绑定目录)" },
  },
  entries: {
    queue: [{ task_id: "task-waiting_for_user", title: "waiting_for_user 任务", stage: "topic", status: "waiting_for_user", updated_at: "2026-10-04T00:00:00Z" }],
    todos: [],
    works: [],
    meta,
  },
  unknown: [],
};

const foundation = {
  readiness: {
    ready: true,
    asset_count: 3,
    approved_asset_count: 2,
    approved_segment_count: 4,
    blockers: [],
  },
  recent_batches: [],
  recent_montage_plans: [],
  eligible_episodes: [],
} as unknown as ProductionFoundation;

describe("HomePage workspace overview", () => {
  beforeEach(() => {
    pathname = "/";
    search = "";
    identityState.token = "tok-a";
    identityState.ready = true;
    routerPush.mockReset();
    apiMocks.getHomeDashboard.mockReset();
    apiMocks.getHomeDashboard.mockResolvedValue(home);
    apiMocks.listCreationTasks.mockReset();
    apiMocks.listCreationTasks.mockResolvedValue({ items: [], total_count: 0 });
    apiMocks.getProductionFoundation.mockReset();
    apiMocks.getProductionFoundation.mockResolvedValue(foundation);
    apiMocks.getMaterialLibraryInsights.mockReset();
    apiMocks.getMaterialLibraryInsights.mockResolvedValue({
      asset_count: 0, segment_count: 0, tags: [], recommended_gaps: [],
    });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }),
    });
  });

  it("renders the real shell assistant region and the seven real overview metrics", async () => {
    render(<HomePage />);

    // Real AppShell provides the assistant region; real composer is inside it.
    const assistant = await screen.findByRole("region", { name: "创作助手" });
    expect(within(assistant).getByRole("textbox", { name: "制作指令" })).toHaveAttribute("placeholder", "想做什么视频？");

    // 概览读服务端聚合：七项都是真实数值，不是占位横线。
    const metrics = await screen.findByRole("region", { name: "工作空间七项概览" });
    for (const label of ["可见素材", "分析可用", "待分析", "知识来源", "声音", "任务", "作品"]) {
      expect(within(metrics).getByText(label)).toBeInTheDocument();
    }
    expect(metrics).toHaveTextContent("12可见素材");
    expect(metrics).toHaveTextContent("2声音");
    expect(within(metrics).queryByText("—")).not.toBeInTheDocument();
    expect(apiMocks.getHomeDashboard).toHaveBeenCalledWith(7);
  });

  it("keeps the ?new=1 creation entry and sends queued tasks to their real route", async () => {
    search = "new=1";
    render(<HomePage />);

    const assistant = await screen.findByRole("region", { name: "创作助手" });
    const input = within(assistant).getByRole("textbox", { name: "制作指令" }) as HTMLTextAreaElement;
    // 既有创作入口：带 ?new=1 打开首页时输入框直接获得焦点。
    await waitFor(() => expect(document.activeElement).toBe(input));

    // 首页队列按真实 task_id 进入既有任务路由，不用依赖另一份任务列表。
    const queue = (await screen.findByRole("heading", { name: "我的任务" })).closest("section") as HTMLElement;
    fireEvent.click(within(queue).getByRole("button", { name: /waiting_for_user 任务/ }));
    expect(routerPush).toHaveBeenCalledWith("/tasks/task-waiting_for_user");
  });
});
