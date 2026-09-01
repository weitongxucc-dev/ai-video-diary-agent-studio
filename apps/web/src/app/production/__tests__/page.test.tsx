import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  getProductionFoundation: vi.fn(),
  listCreationTasks: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, creationTasks, inspector }: { children: React.ReactNode; creationTasks?: CreationTask[]; inspector?: React.ReactNode }) => <><p data-testid="shell-task-states">{creationTasks?.map((task) => `${task.id}:${task.stage_status}`).join(",")}</p><aside data-testid="inspector">{inspector}</aside><main>{children}</main></>,
}));
vi.mock("@/components/CreateProductionTaskForm", () => ({ CreateProductionTaskForm: () => null }));
vi.mock("@/lib/api.server", () => ({
  getProductionFoundation: mocks.getProductionFoundation,
  listCreationTasks: mocks.listCreationTasks,
}));

import ProductionPage from "@/app/production/page";

function task(stageStatus: CreationTask["stage_status"]): CreationTask {
  return {
    id: `task-${stageStatus}`,
    workspace_id: "workspace-1",
    plan_id: `plan-${stageStatus}`,
    name: `任务 ${stageStatus}`,
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
} as unknown as ProductionFoundation;

describe("ProductionPage default CreationTask projection", () => {
  it("keeps each task stage distinct in the default list and inspector counts", async () => {
    mocks.getProductionFoundation.mockResolvedValue(foundation);
    mocks.listCreationTasks.mockResolvedValue({
      items: (["not_started", "running", "waiting_for_user", "completed", "failed"] satisfies CreationTask["stage_status"][]).map(task),
      total_count: 5,
    });

    render(await ProductionPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByTestId("shell-task-states")).toHaveTextContent("task-not_started:not_started");
    expect(screen.getAllByText("选题生成中")).not.toHaveLength(0);
    expect(screen.getAllByText("等待确认")).not.toHaveLength(0);
    expect(screen.getAllByText("选题已完成")).not.toHaveLength(0);
    expect(screen.getAllByText("需要处理")).not.toHaveLength(0);
    expect(screen.getByRole("link", { name: "打开 任务 completed" })).toHaveAttribute("href", "/tasks/task-completed");

    const inspector = within(screen.getByTestId("inspector"));
    for (const label of ["等待开始", "选题生成中", "等待确认", "选题已完成", "需要处理"]) {
      expect(inspector.getByText(label).parentElement).toHaveTextContent(`${label}1`);
    }
  });
});
