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
  AppShell: ({ children, creationTasks, inspector }: { children: React.ReactNode; creationTasks?: CreationTask[]; inspector?: React.ReactNode }) => <><p data-testid="shell-task-states">{creationTasks?.map((task) => `${task.id}:${task.stage_status}`).join(",")}</p><aside data-testid="run-board">{inspector}</aside><main>{children}</main></>,
}));
vi.mock("@/components/HomeCommandComposer", () => ({ HomeCommandComposer: () => <div>目标输入</div> }));
vi.mock("@/lib/api.server", () => ({
  getProductionFoundation: mocks.getProductionFoundation,
  listCreationTasks: mocks.listCreationTasks,
}));

import HomePage from "@/app/page";

function task(stageStatus: CreationTask["stage_status"]): CreationTask {
  return {
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

describe("HomePage CreationTask projection", () => {
  it("shows each persisted task state once and counts mixed statuses truthfully", async () => {
    mocks.getProductionFoundation.mockResolvedValue(foundation);
    mocks.listCreationTasks.mockResolvedValue({
      items: (["not_started", "running", "waiting_for_user", "completed", "failed"] satisfies CreationTask["stage_status"][]).map(task),
      total_count: 5,
    });

    render(await HomePage());

    expect(screen.getByRole("link", { name: /not_started 任务选题：未开始等待开始/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /running 任务选题：处理中选题处理中/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /waiting_for_user 任务选题：等待确认等待确认/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /completed 任务选题：已完成已完成/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /failed 任务选题：需要处理需要处理/ })).toBeInTheDocument();

    const runBoard = within(screen.getByTestId("run-board"));
    expect(runBoard.getByText("等待开始").parentElement).toHaveTextContent("等待开始1项任务");
    expect(runBoard.getByText("进行中或待确认").parentElement).toHaveTextContent("进行中或待确认2项任务");
    expect(runBoard.getByText("需要处理").parentElement).toHaveTextContent("需要处理1项任务");
    expect(runBoard.getByText("已完成").parentElement).toHaveTextContent("已完成1项任务");
    expect(screen.getAllByRole("heading", { level: 2, name: "创作任务" })).toHaveLength(1);
  });
});
