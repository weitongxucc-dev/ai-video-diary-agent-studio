import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

// Page-level test: render the REAL AppShell and HomeWorkspace; mock only the
// Next.js navigation boundary and the API service boundaries. The AppShell
// slot contract (conversation slot, panel behavior) is covered by
// components/__tests__/AppShell.test.tsx and is not re-implemented here.

let pathname = "/";

const serverMocks = vi.hoisted(() => ({
  getMaterialLibraryInsights: vi.fn(),
  getProductionFoundation: vi.fn(),
  listCreationTasks: vi.fn(),
}));
const clientMocks = vi.hoisted(() => ({
  listCreationTasks: vi.fn(),
}));
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
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/lib/api.server", () => ({
  getMaterialLibraryInsights: serverMocks.getMaterialLibraryInsights,
  getProductionFoundation: serverMocks.getProductionFoundation,
  listCreationTasks: serverMocks.listCreationTasks,
}));
vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  confirmCreationPlan: vi.fn(),
  confirmCreationTaskEdit: vi.fn(),
  CreationPlanApiError: class CreationPlanApiError extends Error {},
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }),
  listCreationTasks: clientMocks.listCreationTasks,
  previewCreationPlan: vi.fn(),
  sendCreationTaskAssistantTurn: vi.fn(),
  sendWorkspaceAssistantPlanTurn: vi.fn(),
  undoCreationTaskTimeline: vi.fn(),
}));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: vi.fn() }));

import HomePage from "@/app/page";

function task(stageStatus: CreationTask["stage_status"]): CreationTask {
  return {
    visibility: "private",
    id: `task-${stageStatus}`,
    workspace_id: "workspace-1",
    plan_id: `plan-${stageStatus}`,
    name: `${stageStatus} 任务`,
    scenario: "短视频",
    stage: "topic",
    stage_status: stageStatus,
    status: stageStatus === "not_started" ? "created" : stageStatus,
    progress: 0,
    recovery_point: "topic",
    selected_topic_id: null,
    confirmed_script_id: null,
    narration_cache_key: null,
    montage_plan_id: null,
    current_timeline_version_id: null,
    timeline_sequence: 0,
    can_undo: false,
    output_ready: false,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
    stage_retryable: null,
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
  };
}

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
    routerPush.mockReset();
    serverMocks.getProductionFoundation.mockReset();
    serverMocks.listCreationTasks.mockReset();
    serverMocks.getMaterialLibraryInsights.mockReset();
    serverMocks.getMaterialLibraryInsights.mockResolvedValue({
      asset_count: 0,
      segment_count: 0,
      tags: [],
      recommended_gaps: [],
    });
    clientMocks.listCreationTasks.mockReset();
    clientMocks.listCreationTasks.mockResolvedValue({ items: [], total_count: 0 });
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }),
    });
  });

  it("renders the real shell assistant region and overview stats", async () => {
    serverMocks.getProductionFoundation.mockResolvedValue(foundation);
    serverMocks.listCreationTasks.mockResolvedValue({
      items: (["not_started", "running", "waiting_for_user", "completed", "failed"] satisfies CreationTask["stage_status"][]).map(task),
      total_count: 5,
    });

    render(await HomePage({}));

    // Real AppShell provides the assistant region; real composer is inside it.
    const assistant = screen.getByRole("region", { name: "创作助手" });
    expect(within(assistant).getByRole("textbox", { name: "制作指令" })).toHaveAttribute("placeholder", "想做什么视频？");

    // Page-level stats area renders its five metrics (no dashboard data yet).
    const metrics = screen.getByRole("region", { name: "工作空间五项概览" });
    for (const label of ["可见素材", "分析可用", "待分析", "知识来源", "作品记录"]) {
      expect(within(metrics).getByText(label)).toBeInTheDocument();
    }
  });

  it("opens task details as a card and issues real navigation from 对话修改", async () => {
    serverMocks.getProductionFoundation.mockResolvedValue(foundation);
    serverMocks.listCreationTasks.mockResolvedValue({
      items: (["not_started", "running", "waiting_for_user", "completed", "failed"] satisfies CreationTask["stage_status"][]).map(task),
      total_count: 5,
    });

    render(await HomePage({}));

    const stageCard = screen.getByRole("heading", { name: "任务与处理阶段" }).closest("section");
    expect(stageCard).not.toBeNull();
    expect(within(stageCard as HTMLElement).getAllByRole("button", { name: /任务/ })).toHaveLength(5);

    expect(screen.queryByRole("dialog", { name: "任务卡片" })).not.toBeInTheDocument();
    fireEvent.click(within(stageCard as HTMLElement).getByRole("button", { name: /waiting_for_user 任务/ }));

    const card = within(screen.getByRole("dialog", { name: "任务卡片" }));
    expect(card.getByRole("link", { name: /打开任务/ })).toHaveAttribute("href", "/tasks/task-waiting_for_user");

    // Real navigation intent from the real button, no fake page switch.
    fireEvent.click(card.getByRole("button", { name: "对话修改" }));
    expect(routerPush).toHaveBeenCalledWith("/tasks/task-waiting_for_user");

    fireEvent.click(card.getByRole("button", { name: "关闭卡片" }));
    expect(screen.queryByRole("dialog", { name: "任务卡片" })).not.toBeInTheDocument();
  });
});
