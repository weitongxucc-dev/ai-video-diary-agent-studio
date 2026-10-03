import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationPlan, CreationPlanPreview } from "@/lib/types";

const mocks = vi.hoisted(() => {
  class MockCreationPlanApiError extends Error {
    constructor(
      public readonly status: number,
      public readonly code: string | null,
      message: string,
      public readonly impact: string | null = null,
      public readonly repairAction: string | null = null,
    ) {
      super(message);
    }
  }
  return {
    previewCreationPlan: vi.fn(),
    confirmCreationPlan: vi.fn(),
    CreationPlanApiError: MockCreationPlanApiError,
    assignLocation: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  previewCreationPlan: mocks.previewCreationPlan,
  confirmCreationPlan: mocks.confirmCreationPlan,
  CreationPlanApiError: mocks.CreationPlanApiError,
}));

import { CreateProductionTaskForm } from "@/components/CreateProductionTaskForm";

function preview(overrides: Partial<CreationPlanPreview> = {}): CreationPlanPreview {
  return {
    goal: "制作产品观察视频",
    name: "产品观察",
    target_count: 1,
    language: "zh-CN",
    aspect_ratio: "9:16",
    duration_target_seconds: 60,
    knowledge_references: [{ id: "knowledge-1", version: "v1", title: "产品资料", authorization: "已授权", ownership: "工作区" }],
    source_references: [{ id: "source-1", connector_id: "connector-1", source_label: "信息源", title: "行业观察", license: "允许使用" }],
    selected_voice: { profile_id: "voice-1", version: "v2", person_profile_id: "person-1", person_version: "v3", person_authorization_id: "person-auth", person_authorization_version: "v1", authorization_id: "voice-auth", authorization_version: "v4", language: "zh", model_id: "voice-local" },
    pronunciation_references: [{ id: "pronunciation-1", version: "v1", scope: "workspace", scope_reference: null, language: "zh" }],
    approved_asset_count: 3,
    approved_clip_count: 5,
    material_rule_summary: "仅计入已审核的 B-roll 镜头。",
    material_matches: [{ asset_ref: "asset:asset-1", segment_ref: "segment:segment-1", score: 0.86, reasons: ["用途匹配补充画面", "标签匹配产品"] }],
    model_snapshot: [{ id: "voice-local", capability: "voice_generation", state: "ready", ready: true, message: "本地声音模型已就绪。" }],
    confirmation_points: ["确认资料与来源事实"],
    blockers: [],
    confirmable: true,
    preflight_fingerprint: "f".repeat(64),
    ...overrides,
  };
}

function renderForm() {
  return render(<CreateProductionTaskForm assignLocation={mocks.assignLocation} autoFocus initialGoal="制作产品观察视频" />);
}

describe("CreateProductionTaskForm", () => {
  beforeEach(() => {
    mocks.previewCreationPlan.mockReset();
    mocks.confirmCreationPlan.mockReset();
    mocks.assignLocation.mockReset();
  });

  it("performs one read-only preflight, shows returned facts, and does not confirm before the user acts", async () => {
    mocks.previewCreationPlan.mockResolvedValue(preview());
    renderForm();
    expect(screen.getByText("画幅：9:16（当前本地服务支持的画幅）")).toBeInTheDocument();

    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);

    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenCalledWith({
      goal: "制作产品观察视频",
      target_count: 1,
      language: "zh-CN",
      aspect_ratio: "9:16",
      duration_target_seconds: 60,
      presenter_share_target: 20,
      lipsync_quality: "high",
    }));
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
    expect(await screen.findByText("产品资料")).toBeInTheDocument();
    expect(screen.getByText("预检画幅：9:16")).toBeInTheDocument();
    expect(screen.getAllByText("补充画面", { exact: false })).toHaveLength(2);
    expect(screen.getByText("匹配 86%")).toBeInTheDocument();
    expect(screen.getByText("用途匹配补充画面；标签匹配产品")).toBeInTheDocument();
    expect(screen.getByText("声音生成 · 已就绪")).toBeInTheDocument();
  });

  it("keeps confirmation disabled when the returned preflight has a blocking issue", async () => {
    mocks.previewCreationPlan.mockResolvedValue(preview({
      confirmable: false,
      blockers: [{ reason: "content_sources_missing", impact: "缺少可用于选题的资料。", repair_action: "先保存资料或信息源内容。", stage: "topic", blocks_confirmation: true }],
    }));
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);

    expect(await screen.findByText("缺少可用于选题的资料。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并创建任务" })).toBeDisabled();
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
  });

  it("sends the explicitly selected low-precision model and invalidates the old preflight", async () => {
    mocks.previewCreationPlan.mockResolvedValue(preview());
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("radio", { name: /低精度/ }));
    expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ lipsync_quality: "fast" })));
  });

  it("shows the server repair action after a stale confirm and stays on the form", async () => {
    mocks.previewCreationPlan
      .mockResolvedValueOnce(preview())
      .mockResolvedValueOnce(preview({ preflight_fingerprint: "r".repeat(64) }));
    mocks.confirmCreationPlan.mockRejectedValue(new mocks.CreationPlanApiError(409, "creation_plan_preflight_stale", "预检结果已变化", "未创建任务。", "重新执行内容预检并确认最新方案。"));
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("预检结果已变化");
    expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查可用内容" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "重新检查可用内容" }));
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: "确认并创建任务" })).toBeEnabled();
    expect(mocks.assignLocation).not.toHaveBeenCalled();
  });

  it("confirms the reviewed fingerprint once and opens the persisted plan detail", async () => {
    const checked = preview();
    mocks.previewCreationPlan.mockResolvedValue(checked);
    mocks.confirmCreationPlan.mockResolvedValue({
      ...checked,
      id: "plan-1",
      workspace_id: "workspace-1",
      status: "confirmed",
      creation_task: { id: "task-1", workspace_id: "workspace-1", plan_id: "plan-1", name: "产品观察", scenario: checked.goal, stage: "topic", stage_status: "not_started", status: "created", progress: 0, recovery_point: "topic", selected_topic_id: null, confirmed_script_id: null, narration_cache_key: null, montage_plan_id: null, current_timeline_version_id: null, timeline_sequence: 0, can_undo: false, output_ready: false, stage_reason: null, stage_impact: null, stage_repair_action: null, stage_retryable: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
      created_at: "2026-08-29T00:00:00Z",
      confirmed_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T00:00:00Z",
    } satisfies CreationPlan);
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));

    await waitFor(() => expect(mocks.confirmCreationPlan).toHaveBeenCalledWith({
      goal: "制作产品观察视频",
      target_count: 1,
      language: "zh-CN",
      aspect_ratio: "9:16",
      duration_target_seconds: 60,
      presenter_share_target: 20,
      lipsync_quality: "high",
      preflight_fingerprint: "f".repeat(64),
    }));
    expect(mocks.assignLocation).toHaveBeenCalledWith("/tasks/task-1?toast=success&toast_message=%E5%88%9B%E4%BD%9C%E4%BB%BB%E5%8A%A1%E5%B7%B2%E5%88%9B%E5%BB%BA%EF%BC%8C%E9%80%89%E9%A2%98%E5%B0%9A%E6%9C%AA%E5%BC%80%E5%A7%8B%E3%80%82");
  });
});

it("keeps the template identity through preview and confirmation", async () => {
  const template = { id: "template_one", version: 3, name: "我的模板", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "v1", created_at: "2026-09-12", settings: { language: "zh-CN" as const, aspect_ratio: "9:16" as const, duration_target_seconds: 30, voice: null, style_instruction: "短句", fit_mode: "fit" as const, subtitle_style: {} } };
  mocks.previewCreationPlan.mockResolvedValue(preview());
  mocks.confirmCreationPlan.mockResolvedValue({ creation_task: { id: "new-task" } });
  render(<CreateProductionTaskForm template={template} initialGoal="新的主题" assignLocation={mocks.assignLocation} />);
  expect(screen.getByText("画幅：9:16（继承所选模板）")).toBeInTheDocument();
  fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
  await screen.findByRole("button", { name: "确认并创建任务" });
  expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template_one", template_version: 3, goal: "新的主题", duration_target_seconds: 30, aspect_ratio: "9:16" }));
  expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ lipsync_quality: "fast" }));
  fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
  await waitFor(() => expect(mocks.confirmCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template_one", template_version: 3 })));
});

it("inherits landscape ratio through the form and preflight, then replaces it when the template changes", async () => {
  const template = { id: "template_landscape", version: 1, name: "横版模板", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "v1", created_at: "2026-10-03", settings: { language: "zh-CN" as const, aspect_ratio: "16:9" as const, duration_target_seconds: 30, voice: null, style_instruction: "短句", fit_mode: "fit" as const, subtitle_style: {} } };
  mocks.previewCreationPlan.mockReset();
  mocks.previewCreationPlan.mockResolvedValueOnce(preview({ aspect_ratio: "16:9" })).mockResolvedValueOnce(preview());
  const view = render(<CreateProductionTaskForm template={template} initialGoal="新的主题" />);
  expect(screen.getByText("画幅：16:9（继承所选模板）")).toBeInTheDocument();
  fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
  expect(await screen.findByText("预检画幅：16:9")).toBeInTheDocument();
  expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template_landscape", aspect_ratio: "16:9" }));

  const portrait = { ...template, id: "template_portrait", settings: { ...template.settings, aspect_ratio: "9:16" as const } };
  view.rerender(<CreateProductionTaskForm template={portrait} initialGoal="新的主题" />);
  expect(screen.getByText("画幅：9:16（继承所选模板）")).toBeInTheDocument();
  expect(screen.queryByText("预检画幅：16:9")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
  fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
  expect(await screen.findByText("预检画幅：9:16")).toBeInTheDocument();
  expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template_portrait", aspect_ratio: "9:16" }));

  view.rerender(<CreateProductionTaskForm initialGoal="新的主题" />);
  expect(screen.getByText("画幅：9:16（当前本地服务支持的画幅）")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
});
