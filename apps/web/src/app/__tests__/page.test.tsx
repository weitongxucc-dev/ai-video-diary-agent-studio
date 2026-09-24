import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  getProductionFoundation: vi.fn(),
  listCreationTasks: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, creationTasks }: { children: React.ReactNode; creationTasks?: CreationTask[] }) => <><p data-testid="shell-task-states">{creationTasks?.map((task) => `${task.id}:${task.stage_status}`).join(",")}</p><main>{children}</main></>,
}));
vi.mock("@/components/HomeCommandComposer", () => ({ HomeCommandComposer: ({ task }: { task?: CreationTask | null }) => <div>{task ? `正在修改：${task.name}` : "目标输入"}</div> }));
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

describe("HomePage three-zone workspace", () => {
  it("shows real task execution beside chat and opens details only as a card", async () => {
    mocks.getProductionFoundation.mockResolvedValue(foundation);
    mocks.listCreationTasks.mockResolvedValue({
      items: (["not_started", "running", "waiting_for_user", "completed", "failed"] satisfies CreationTask["stage_status"][]).map(task),
      total_count: 5,
    });

    render(await HomePage({}));

    expect(screen.getByRole("region", { name: "创作助手" })).toContainElement(screen.getByText("目标输入"));
    const execution = within(screen.getByRole("region", { name: "任务执行" }));
    expect(execution.getByText("任务执行")).toBeInTheDocument();
    expect(execution.getByText("待处理").parentElement).toHaveTextContent("2待处理");
    expect(execution.getByText("进行中").parentElement).toHaveTextContent("1进行中");
    expect(execution.getAllByRole("button", { name: /任务/ })).toHaveLength(5);
    expect(screen.queryByRole("dialog", { name: "任务卡片" })).not.toBeInTheDocument();
    fireEvent.click(execution.getByRole("button", { name: /waiting_for_user 任务/ }));
    const card = within(screen.getByRole("dialog", { name: "任务卡片" }));
    expect(card.getByRole("link", { name: "打开任务" })).toHaveAttribute("href", "/tasks/task-waiting_for_user");
    fireEvent.click(card.getByRole("button", { name: "对话修改" }));
    expect(screen.getByRole("region", { name: "创作助手" })).toHaveTextContent("正在修改：waiting_for_user 任务");
    fireEvent.click(screen.getByRole("button", { name: "新建视频" }));
    expect(screen.getByRole("region", { name: "创作助手" })).toHaveTextContent("目标输入");
    expect(screen.queryByRole("dialog", { name: "任务卡片" })).not.toBeInTheDocument();
  });
});
