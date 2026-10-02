import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationPlan, CreationTask, CreationTaskTopicsResponse } from "@/lib/types";

const mocks = vi.hoisted(() => {
  class MockCreationPlanApiError extends Error {
    constructor(
      public status: number,
      public code: string | null,
      message: string,
      public impact: string | null = null,
      public repairAction: string | null = null,
      public reason: string | null = null,
    ) {
      super(message);
    }
  }
  return {
    getCreationTaskTopics: vi.fn(),
    generateCreationTaskTopics: vi.fn(),
    confirmCreationTaskTopic: vi.fn(),
    refresh: vi.fn(),
    MockCreationPlanApiError,
  };
});

vi.mock("next/link", () => ({ default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, creationTasks, inspector, workspaceStatus }: { children: React.ReactNode; creationTasks?: CreationTask[]; inspector: React.ReactNode; workspaceStatus: { label: string } }) => <><p data-testid="workspace-status">{workspaceStatus.label}</p><p data-testid="sidebar-task-states">{creationTasks?.map((task) => `${task.id}:${task.stage_status}`).join(",")}</p><aside>{inspector}</aside><main>{children}</main></>,
}));
vi.mock("@/lib/api", () => ({
  getCreationTaskTopics: mocks.getCreationTaskTopics,
  generateCreationTaskTopics: mocks.generateCreationTaskTopics,
  confirmCreationTaskTopic: mocks.confirmCreationTaskTopic,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
}));

import { CreationTaskTopicWorkspace } from "@/components/CreationTaskTopicWorkspace";

function topics(overrides: Partial<CreationTaskTopicsResponse> = {}): CreationTaskTopicsResponse {
  return {
    task: {
      id: "task-1", workspace_id: "workspace-1", plan_id: "plan-1", name: "产品观察", scenario: "制作产品观察视频", stage: "topic", stage_status: "not_started", status: "created", progress: 10, recovery_point: "topic", selected_topic_id: null, confirmed_script_id: null, narration_cache_key: null, montage_plan_id: null, current_timeline_version_id: null, timeline_sequence: 0, can_undo: false, output_ready: false, stage_reason: null, stage_impact: null, stage_repair_action: null, stage_retryable: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
    },
    candidates: [],
    evidence_references: [],
    selected_topic_id: null,
    latest_run: null,
    route_preflight: { provider_kind: "openai-compatible", execution_location: "cloud", model_id: "local-text-model", endpoint_identity: "https://example.invalid", connection_version: "v1", availability: "unverified", checked_at: "2026-08-29T00:00:00Z" },
    egress_preflight: { cloud_egress: true, source_count: 2, text_excerpt_count: 2, reason: null, impact: null, repair_action: null },
    ...overrides,
  };
}

function plan(overrides: Partial<CreationPlan> = {}): CreationPlan {
  const initial = topics();
  return {
    id: "plan-1", workspace_id: "workspace-1", status: "confirmed", goal: "制作产品观察视频", name: "产品观察", target_count: 1, language: "zh-CN", aspect_ratio: "9:16", duration_target_seconds: 60,
    knowledge_references: [], source_references: [], selected_voice: null, pronunciation_references: [], approved_asset_count: 2, approved_clip_count: 3, material_rule_summary: "已审核素材", material_matches: [{ asset_ref: "asset:asset-1", segment_ref: "segment:segment-1", score: 0.9, reasons: ["已审核"] }], model_snapshot: [], confirmation_points: [], blockers: [], confirmable: true, preflight_fingerprint: "f".repeat(64), creation_task: initial.task,
    created_at: "2026-08-29T00:00:00Z", confirmed_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
    ...overrides,
  };
}

function renderWorkspace(initialTopics = topics(), planOverrides: Partial<CreationPlan> = {}) {
  const currentPlan = plan(planOverrides);
  return render(<CreationTaskTopicWorkspace creationTasks={[initialTopics?.task ?? currentPlan.creation_task]} initialError={null} initialTopics={initialTopics} plan={currentPlan} />);
}

beforeEach(() => {
  mocks.getCreationTaskTopics.mockReset();
  mocks.generateCreationTaskTopics.mockReset();
  mocks.confirmCreationTaskTopic.mockReset();
  mocks.refresh.mockReset();
});

describe("CreationTaskTopicWorkspace", () => {
  it("discloses cloud text egress before the single start action", () => {
    renderWorkspace();

    expect(screen.getAllByText(/云端文字模型/)).not.toHaveLength(0);
    expect(screen.getByText("将使用云端文字模型，并只发送 2 条已授权文字摘录。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始选题" })).toBeEnabled();
  });

  it("keeps generation disabled for an unconfigured or blocked route", () => {
    renderWorkspace(topics({
      route_preflight: { ...topics().route_preflight, availability: "unconfigured" },
      egress_preflight: { cloud_egress: true, source_count: 0, text_excerpt_count: 0, reason: "capability_unavailable", impact: "没有调用文字模型。", repair_action: "先配置文字模型。" },
    }));

    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("没有调用文字模型。");
    expect(screen.queryByText("capability_unavailable")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始选题" })).toBeDisabled();
  });

  it("keeps the sole action disabled while selection is generating", () => {
    renderWorkspace(topics({ task: { ...topics().task, stage_status: "running", status: "running" } }));

    expect(screen.getAllByText("生成中")).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: "生成中" })).toBeDisabled();
  });

  it("keeps every creation stage named in the task overview", () => {
    renderWorkspace();

    const stageList = screen.getByRole("list", { name: "制作阶段" });
    expect(stageList.querySelectorAll("li")).toHaveLength(9);
    ["资料准备", "选题", "文案", "配音", "人物与口型", "视频剪辑", "字幕与封面", "检查与输出", "发布与数据"].forEach((stage) => {
      expect(stageList).toHaveTextContent(stage);
    });
    expect(stageList).toHaveTextContent("等待开始");
  });

  it("keeps the return link as one accessible task-list action", () => {
    renderWorkspace();

    expect(screen.getByRole("link", { name: "返回任务列表" })).toHaveAttribute("href", "/production");
  });

  it("shows persisted candidates and their selected evidence, then confirms exactly one candidate", async () => {
    const waiting = topics({
      task: { ...topics().task, stage_status: "waiting_for_user", status: "waiting_for_user" },
      candidates: [
        { id: "topic-1", run_id: "generated-1", title: "新角度", angle: "反常识观察", audience: "产品从业者", outline: ["现象", "证据", "结论"], score: 92, selection_status: "unselected", created_at: "2026-08-29T00:00:00Z" },
        { id: "topic-2", run_id: "generated-1", title: "第二角度", angle: "实操", audience: "创作者", outline: ["问题", "方法", "建议"], score: 85, selection_status: "unselected", created_at: "2026-08-29T00:00:00Z" },
      ],
      evidence_references: [
        { id: "evidence-1", candidate_id: "topic-2", kind: "knowledge", reference_id: "knowledge-1", version_or_identity: "v1", title: "已授权资料", authorization_or_license: "允许使用", source_url: "https://example.com/source", created_at: "2026-08-29T00:00:00Z" },
      ],
    });
    const confirmed = topics({
      ...waiting,
      task: { ...waiting.task, stage_status: "completed", status: "completed", selected_topic_id: "topic-2" },
      selected_topic_id: "topic-2",
      candidates: waiting.candidates.map((item) => item.id === "topic-2" ? { ...item, selection_status: "confirmed" } : item),
    });
    mocks.confirmCreationTaskTopic.mockResolvedValue(confirmed);
    renderWorkspace(waiting);

    expect(screen.getByRole("button", { name: "确认这个选题" })).toBeDisabled();
    const selection = screen.getByRole("button", { name: "选择此选题：第二角度" });
    expect(selection.closest("article")?.querySelectorAll("button")).toHaveLength(1);
    expect(selection).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(selection);
    expect(selection).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("已授权资料")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认这个选题" }));

    await waitFor(() => expect(mocks.confirmCreationTaskTopic).toHaveBeenCalledWith("task-1", "topic-2"));
    expect((await screen.findAllByText("选题已完成")).length).toBeGreaterThan(0);
    expect(screen.getByText("下一步：生成文案（尚未接入）")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-task-states")).toHaveTextContent("task-1:completed");
    expect(mocks.refresh).toHaveBeenCalledOnce();
  });

  it("maps raw authorization and license values before they reach the workspace", () => {
    const waiting = topics({
      task: { ...topics().task, stage_status: "waiting_for_user", status: "waiting_for_user" },
      candidates: [{ id: "topic-1", run_id: "generated-1", title: "新角度", angle: "反常识观察", audience: "产品从业者", outline: ["现象"], score: 92, selection_status: "unselected", created_at: "2026-08-29T00:00:00Z" }],
      evidence_references: [{ id: "evidence-1", candidate_id: "topic-1", kind: "knowledge", reference_id: "knowledge-1", version_or_identity: "v1", title: "资料证据", authorization_or_license: "unverified", source_url: null, created_at: "2026-08-29T00:00:00Z" }],
    });
    renderWorkspace(waiting, {
      knowledge_references: [{ id: "knowledge-1", version: "v1", title: "知识资料", authorization: "authorized", ownership: "workspace" }],
      source_references: [{ id: "source-1", connector_id: "connector-1", source_label: "网站", title: "信息资料", license: "unknown" }],
    });

    fireEvent.click(screen.getByRole("button", { name: "选择此选题：新角度" }));
    expect(screen.getAllByText(/授权情况待确认/)).not.toHaveLength(0);
    expect(screen.queryByText(/authorized|unknown|unverified/)).not.toBeInTheDocument();

    renderWorkspace(topics(), {
      knowledge_references: [{ id: "knowledge-1", version: "v1", title: "知识资料", authorization: "authorized", ownership: "workspace" }],
      source_references: [{ id: "source-1", connector_id: "connector-1", source_label: "网站", title: "信息资料", license: "unknown" }],
    });
    expect(screen.getAllByText(/已确认授权/)).not.toHaveLength(0);
    expect(screen.getAllByText(/许可待确认/)).not.toHaveLength(0);
    expect(screen.queryByText(/authorized|unknown|unverified/)).not.toBeInTheDocument();
  });

  it("keeps mutation details visible when task status cannot be re-read", async () => {
    mocks.generateCreationTaskTopics.mockRejectedValue(new mocks.MockCreationPlanApiError(
      503,
      "capability_unavailable",
      "当前文字模型不可用",
      "没有生成选题候选。",
      "配置文字模型后重新开始选题。",
      "capability_unavailable",
    ));
    mocks.getCreationTaskTopics.mockRejectedValue(new mocks.MockCreationPlanApiError(0, null, "本地服务未接通。"));
    renderWorkspace();

    fireEvent.click(screen.getByRole("button", { name: "开始选题" }));

    await waitFor(() => expect(screen.getAllByRole("alert").some((alert) => alert.textContent?.includes("任务状态重新读取失败"))).toBe(true));
    expect(screen.getAllByText("没有生成选题候选。")).not.toHaveLength(0);
    expect(screen.getAllByText("处理方式：配置文字模型后重新开始选题。")).not.toHaveLength(0);
    expect(screen.getByTestId("workspace-status")).toHaveTextContent("选题状态重新读取失败");
    expect(mocks.getCreationTaskTopics).toHaveBeenCalledWith("task-1");
  });

  it("offers a real retry after a failed stage and does not expose internal identifiers", async () => {
    const failed = topics({
      task: { ...topics().task, stage_status: "failed", status: "failed", stage_reason: "文字模型连接失败", stage_impact: "没有生成候选。", stage_repair_action: "检查模型连接后重试。", stage_retryable: true },
    });
    mocks.generateCreationTaskTopics.mockResolvedValue(topics({ task: { ...failed.task, stage_status: "waiting_for_user", status: "waiting_for_user" } }));
    renderWorkspace(failed);

    expect(screen.getByRole("alert")).toHaveTextContent("文字模型连接失败");
    fireEvent.click(screen.getByRole("button", { name: "重新生成选题" }));
    await waitFor(() => expect(mocks.generateCreationTaskTopics).toHaveBeenCalledWith("task-1"));
    expect(screen.queryByText(/workspace-1|generated-1|openai-compatible/)).not.toBeInTheDocument();
  });

  it("stops at the completed topic and does not offer a fake next action", () => {
    renderWorkspace(topics({
      task: { ...topics().task, stage_status: "completed", status: "completed", selected_topic_id: "topic-1" },
      selected_topic_id: "topic-1",
      candidates: [{ id: "topic-1", run_id: "generated-1", title: "已确认方向", angle: "观察", audience: "创作者", outline: ["现象"], score: 90, selection_status: "confirmed", created_at: "2026-08-29T00:00:00Z" }],
    }));

    expect(screen.getByText("下一步：生成文案（尚未接入）")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /文案|开始选题|确认这个选题/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("workspace-status")).toHaveTextContent("选题已完成");
  });
});
