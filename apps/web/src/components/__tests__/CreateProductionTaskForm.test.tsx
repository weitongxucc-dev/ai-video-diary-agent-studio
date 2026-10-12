import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    getKnowledgeItem: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  previewCreationPlan: mocks.previewCreationPlan,
  confirmCreationPlan: mocks.confirmCreationPlan,
  CreationPlanApiError: mocks.CreationPlanApiError,
}));
vi.mock("@/lib/source-api", () => ({ getKnowledgeItem: mocks.getKnowledgeItem }));

vi.mock("@/lib/preflight-draft", async (importOriginal) => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: vi.fn().mockResolvedValue({ memberId: "local:member-a", workspaceId: "workspace-a" }) }));

const profileMocks=vi.hoisted(()=>({listVoiceProfiles:vi.fn(),listPersonProfiles:vi.fn(),getDefaultVoice:vi.fn()}));
vi.mock("@/lib/media-profiles-api",()=>profileMocks);
beforeEach(()=>{profileMocks.listVoiceProfiles.mockResolvedValue({items:[]});profileMocks.listPersonProfiles.mockResolvedValue({items:[]});profileMocks.getDefaultVoice.mockResolvedValue(null);});

import { Blockers, CreateProductionTaskForm, PreflightFacts } from "@/components/CreateProductionTaskForm";

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
    material_matches: [{ asset_ref: "asset:asset-1", segment_ref: "segment:segment-1", score: 0.86, reasons: ["用途匹配 b_roll", "标签匹配产品"], match_tier: "text" } as CreationPlanPreview["material_matches"][number]],
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
    mocks.getKnowledgeItem.mockReset();
  });

  it("pins a selected knowledge version in the pending plan and cancels it explicitly", async () => {
    mocks.getKnowledgeItem.mockResolvedValue({ id: "knowledge-1", title: "设备资料", version: "2" });
    mocks.previewCreationPlan.mockResolvedValue(preview());
    render(<CreateProductionTaskForm assignLocation={mocks.assignLocation} initialGoal="制作产品观察视频" initialKnowledgePin={{ knowledge_item_id: "knowledge-1", version: "2" }} />);
    expect(await screen.findByText("设备资料 · 固定版本 2")).toBeInTheDocument();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ selected_knowledge: { knowledge_item_id: "knowledge-1", version: "2" } })));
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消本次资料引用" }));
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenCalledTimes(2));
    expect(mocks.previewCreationPlan.mock.lastCall![0]).not.toHaveProperty("selected_knowledge");
  });

  it("reads the chosen historical knowledge title without silently upgrading the pin", async () => {
    mocks.getKnowledgeItem.mockResolvedValue({ id: "knowledge-1", title: "新资料", version: "3", versions: [{ version: "2", title: "原来的资料" }] });
    mocks.previewCreationPlan.mockResolvedValue(preview());
    render(<CreateProductionTaskForm initialGoal="制作产品观察视频" initialKnowledgePin={{ knowledge_item_id: "knowledge-1", version: "2" }} />);
    expect(await screen.findByText("原来的资料 · 固定版本 2")).toBeInTheDocument();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenCalledWith(expect.objectContaining({ selected_knowledge: { knowledge_item_id: "knowledge-1", version: "2" } })));
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
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
    expect(screen.getByText("主题候选")).toBeInTheDocument();
    expect(screen.queryByText("匹配 86%")).not.toBeInTheDocument();
    expect(screen.getByText("用途匹配 补充画面；标签匹配产品")).toBeInTheDocument();
    expect(screen.getByText("声音生成 · 安装检查通过")).toBeInTheDocument();
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
      visibility: "private",
      status: "confirmed",
      creation_task: { id: "task-1", workspace_id: "workspace-1", plan_id: "plan-1", visibility: "private", name: "产品观察", scenario: checked.goal, stage: "topic", stage_status: "not_started", status: "created", progress: 0, recovery_point: "topic", selected_topic_id: null, confirmed_script_id: null, narration_cache_key: null, montage_plan_id: null, current_timeline_version_id: null, timeline_sequence: 0, can_undo: false, output_ready: false, stage_reason: null, stage_impact: null, stage_repair_action: null, stage_retryable: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z" },
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

describe("preflight candidate and stage facts", () => {
  beforeEach(() => {
    mocks.previewCreationPlan.mockReset();
    mocks.confirmCreationPlan.mockReset();
    mocks.assignLocation.mockReset();
  });

  it.each([["text", "主题候选"], ["partial", "词语候选"], ["structural", "结构候选"]])("shows returned %s tier without converting a heuristic score into a percentage", (tier, label) => {
    const item = { asset_ref: "asset:1", segment_ref: "segment:1", score: 1.7, reasons: ["实际检索依据"], match_tier: tier };
    render(<PreflightFacts preview={preview({ material_matches: [item] })} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText(/匹配 \d+%/)).not.toBeInTheDocument();
    const diagnostic = screen.getByText(/启发分值：1.7/);
    expect(diagnostic).not.toBeVisible();
    fireEvent.click(screen.getByText("候选诊断"));
    expect(diagnostic).toBeVisible();
    expect(diagnostic).toHaveTextContent("不是适配概率");
    expect(screen.getByText("实际检索依据")).toBeVisible();
  });

  it("does not invent a tier when the response omitted it and preserves the old count definition in diagnostics", () => {
    render(<PreflightFacts preview={preview({ material_matches: [{ asset_ref: "asset:1", segment_ref: "segment:1", score: .8, reasons: [] }] })} />);
    expect(screen.getByText("候选层级未提供")).toBeInTheDocument();
    const count = screen.getByText(/本次可自动选用/);
    expect(count).not.toBeVisible();
    fireEvent.click(screen.getByText("素材计数与筛选口径"));
    expect(count).toBeVisible();
    expect(count).toHaveTextContent("3 个素材 · 5 个补充片段");
    expect(count).toHaveTextContent("检索候选与可用计数分别核验");
  });

  it("retains actual model errors and outbound facts inside collapsed diagnostics", () => {
    render(<PreflightFacts preview={preview({ selected_voice: null, model_snapshot: [{ id: "model-raw-id", capability: "text_generation", state: "unavailable", ready: false, message: "连接失败；调用将向客户配置的服务发送文案。" }] })} />);
    expect(screen.getByText("文字生成 · 不可用")).toBeVisible();
    expect(screen.getByText("连接失败；调用将向客户配置的服务发送文案。")).not.toBeVisible();
    expect(screen.queryByText(/已核验声音及人物授权/)).not.toBeInTheDocument();
    const raw = screen.getByText(/model-raw-id/);
    expect(raw).not.toBeVisible();
    fireEvent.click(screen.getByText("模型诊断"));
    expect(raw).toBeVisible();
  });

  it("allows creation for a later-stage blocker and names the phase instead of promising unrestricted continuation", async () => {
    mocks.previewCreationPlan.mockResolvedValue(preview({ blockers: [{ reason: "voice_model_unavailable", impact: "声音模型未就绪。", repair_action: "检查所选模型。", stage: "narration", blocks_confirmation: false }] }));
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    expect(await screen.findByText("受影响阶段：配音")).toBeInTheDocument();
    expect(screen.getByText("不阻止创建；进入配音前需处理")).toBeInTheDocument();
    expect(screen.queryByText("可以继续", { exact: true })).not.toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认并创建任务" });
    expect(confirm).toBeEnabled();
    expect(confirm).toHaveAccessibleDescription("当前允许创建任务；后续阶段的限制见待处理事项。");
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
  });

  it("shows a creation-blocking issue's stage and keeps confirmation disabled with the nearby scope reason", async () => {
    mocks.previewCreationPlan.mockResolvedValue(preview({ confirmable: false, blockers: [{ reason: "template_material_matches_missing", impact: "模板缺少新主题镜头。", repair_action: "补充素材后重新检查。", stage: "video_editing", blocks_confirmation: true }] }));
    renderForm();
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    expect(await screen.findByText("受影响阶段：视频剪辑")).toBeInTheDocument();
    expect(screen.getByText("阻止创建任务")).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "确认并创建任务" });
    expect(confirm).toBeDisabled();
    expect(confirm).toHaveAccessibleDescription(/当前不能创建任务/);
    fireEvent.click(confirm);
    expect(mocks.confirmCreationPlan).not.toHaveBeenCalled();
  });

  it("names the API's person-lipsync phase and preserves the raw reason", () => {
    render(<Blockers preview={preview({ blockers: [{ reason: "person_render_unavailable", impact: "口型不可用。", repair_action: "修复所选环境。", stage: "avatar_lipsync", blocks_confirmation: false }] })} />);
    expect(screen.getByText("受影响阶段：人物与口型")).toBeInTheDocument();
    fireEvent.click(screen.getByText("阻断诊断"));
    expect(screen.getByText("person_render_unavailable · avatar_lipsync")).toBeVisible();
  });

  it("explains the disabled check for an empty goal", () => {
    render(<CreateProductionTaskForm />);
    const check = screen.getByRole("button", { name: "检查可用内容" });
    expect(check).toBeDisabled();
    expect(check).toHaveAccessibleDescription("先填写制作目标，再检查。");
  });

  it.each(["success", "failure"])("ignores a late %s from the previous template's duration check", async outcome => {
    let resolve!: (value: CreationPlanPreview) => void;
    let reject!: (cause: Error) => void;
    mocks.previewCreationPlan.mockReturnValueOnce(new Promise((finish, fail) => { resolve = finish; reject = fail; }));
    const template = { id: "template-old", version: 1, visibility: "private" as const, name: "旧模板", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "old-version", created_at: "2026-10-09", settings: { language: "zh-CN" as const, aspect_ratio: "9:16" as const, duration_target_seconds: 60, voice: null, style_instruction: "", fit_mode: "fit" as const, subtitle_style: {} } };
    const view = render(<CreateProductionTaskForm template={template} initialGoal="产品观察" />);
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await waitFor(() => expect(mocks.previewCreationPlan).toHaveBeenCalledTimes(1));
    view.rerender(<CreateProductionTaskForm template={{ ...template, id: "template-new", settings: { ...template.settings, duration_target_seconds: 90 } }} initialGoal="产品观察" />);
    await act(async () => { if (outcome === "success") resolve(preview()); else reject(new Error("旧模板检查错误")); });
    expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "目标时长（秒）" })).toHaveValue(90);
    mocks.previewCreationPlan.mockResolvedValueOnce(preview({ duration_target_seconds: 90 }));
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    expect(await screen.findByText("预检目标时长：90 秒")).toBeInTheDocument();
    expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template-new", duration_target_seconds: 90 }));
  });

  it("sends an explicit 30 seconds over the template's 60-second default to preview and confirm", async () => {
    const template = { id: "template-60", version: 1, visibility: "private" as const, name: "默认60秒", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "old-version", created_at: "2026-10-09", settings: { language: "zh-CN" as const, aspect_ratio: "9:16" as const, duration_target_seconds: 60, voice: null, style_instruction: "", fit_mode: "fit" as const, subtitle_style: {} } };
    mocks.previewCreationPlan.mockResolvedValue(preview({ duration_target_seconds: 30 }));
    mocks.confirmCreationPlan.mockResolvedValue({ creation_task: { id: "task-30" } });
    const view = render(<CreateProductionTaskForm template={template} initialGoal="产品观察" assignLocation={mocks.assignLocation} />);
    const duration = screen.getByRole("spinbutton", { name: "目标时长（秒）" });
    expect(duration).toBeEnabled();
    expect(duration).toHaveValue(60);
    fireEvent.change(duration, { target: { value: "30" } });
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    expect(await screen.findByText("预检目标时长：30 秒")).toBeInTheDocument();
    expect(mocks.previewCreationPlan).toHaveBeenLastCalledWith(expect.objectContaining({ template_id: "template-60", duration_target_seconds: 30 }));
    fireEvent.change(duration, { target: { value: "45" } });
    expect(screen.queryByRole("button", { name: "确认并创建任务" })).not.toBeInTheDocument();
    fireEvent.change(duration, { target: { value: "30" } });
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);
    await screen.findByRole("button", { name: "确认并创建任务" });
    fireEvent.click(screen.getByRole("button", { name: "确认并创建任务" }));
    await waitFor(() => expect(mocks.confirmCreationPlan).toHaveBeenCalledWith(expect.objectContaining({ duration_target_seconds: 30, preflight_fingerprint: "f".repeat(64) })));
    view.rerender(<CreateProductionTaskForm template={{ ...template, id: "template-90", settings: { ...template.settings, duration_target_seconds: 90 } }} initialGoal="产品观察" />);
    expect(screen.getByRole("spinbutton", { name: "目标时长（秒）" })).toHaveValue(90);
  });
});

it("keeps the template identity through preview and confirmation", async () => {
  const template = { id: "template_one", version: 3, visibility: "private" as const, name: "我的模板", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "v1", created_at: "2026-09-12", settings: { language: "zh-CN" as const, aspect_ratio: "9:16" as const, duration_target_seconds: 30, voice: null, style_instruction: "短句", fit_mode: "fit" as const, subtitle_style: {} } };
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
  const template = { id: "template_landscape", version: 1, visibility: "private" as const, name: "横版模板", origin: "user" as const, source_task_id: "old", source_timeline_version_id: "v1", created_at: "2026-10-03", settings: { language: "zh-CN" as const, aspect_ratio: "16:9" as const, duration_target_seconds: 30, voice: null, style_instruction: "短句", fit_mode: "fit" as const, subtitle_style: {} } };
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

describe("fixed independent task profile pins",()=>{
 it("uses names for opaque versions while retaining exact values and version titles",async()=>{
  const voiceVersion = "v-11111111-1111-4111-8111-111111111111";
  const personVersion = "v-22222222-2222-4222-8222-222222222222";
  profileMocks.listVoiceProfiles.mockResolvedValue({items:[{id:"voice-exact",version:voiceVersion,display_name:"我的声音"}]});
  profileMocks.listPersonProfiles.mockResolvedValue({items:[{id:"person-exact",version:personVersion,display_name:"我的人物"},{id:"person-numeric",version:"2",display_name:"数字版本人物"}]});
  mocks.previewCreationPlan.mockResolvedValue(preview());renderForm();
  await inspectSelectOptions(screen.getByLabelText("任务声音"),async()=>{const voice = await screen.findByRole("option",{name:"我的声音"});expect(voice).toHaveAttribute("data-app-select-value",`voice-exact:${voiceVersion}`);expect(voice).toHaveAttribute("title",voiceVersion);});
  await inspectSelectOptions(screen.getByLabelText("任务人物"),async()=>{const person = await screen.findByRole("option",{name:"我的人物"});expect(person).toHaveAttribute("data-app-select-value",`person-exact:${personVersion}`);expect(person).toHaveAttribute("title",personVersion);expect(screen.getByRole("option",{name:"数字版本人物 · 2"})).toHaveAttribute("title","2");});
  await chooseSelectValue(screen.getByLabelText("任务声音"), `voice-exact:${voiceVersion}`);
  await chooseSelectValue(screen.getByLabelText("任务人物"), `person-exact:${personVersion}`);
  fireEvent.submit(screen.getByRole("button",{name:"检查可用内容"}).closest("form")!);
  await waitFor(()=>expect(mocks.previewCreationPlan).toHaveBeenCalledWith(expect.objectContaining({selected_voice:{profile_id:"voice-exact",version:voiceVersion},selected_person:{profile_id:"person-exact",version:personVersion}})));
 });
 it("uses the historical default voice and an independently chosen person in the same real preflight request",async()=>{
  profileMocks.listVoiceProfiles.mockResolvedValue({items:[{id:"voice",version:"v2",display_name:"最新声音"}]});profileMocks.getDefaultVoice.mockResolvedValue({id:"voice",version:"v1"});profileMocks.listPersonProfiles.mockResolvedValue({items:[{id:"person-other",version:"p3",display_name:"另一个人物"}]});mocks.previewCreationPlan.mockResolvedValue(preview());renderForm();await screen.findByText("声音固定版本未返回");expect(screen.getByLabelText("任务声音")).toHaveAttribute("data-selected-value", "voice:v1");await chooseSelectValue(screen.getByLabelText("任务人物"), "person-other:p3");fireEvent.submit(screen.getByRole("button",{name:"检查可用内容"}).closest("form")!);await waitFor(()=>expect(mocks.previewCreationPlan).toHaveBeenCalledWith(expect.objectContaining({selected_voice:{profile_id:"voice",version:"v1"},selected_person:{profile_id:"person-other",version:"p3"}})));
 });
});


describe("preflight business names and necessary diagnostics", () => {
  it("labels model readiness as installation checks and keeps the complete message in diagnostics", () => {
    const message = "模型安装记录与权重文件检查完成；内部权重路径保留用于诊断。";
    render(<PreflightFacts preview={preview({ model_snapshot: [{ id: "voice-local", capability: "voice_generation", state: "ready", ready: true, message }] })} />);
    expect(screen.getByText("声音生成 · 安装检查通过")).toBeVisible();
    expect(screen.getByText("安装检查核对运行程序与模型文件，不代表生成实测通过或设备资源许可。")).toBeVisible();
    expect(screen.getByText(message)).not.toBeVisible();
    fireEvent.click(screen.getByText("模型诊断"));expect(screen.getByText(message)).toBeVisible();
  });
  it("shows names for the exact independent pins and leaves IDs/empty proof fields in diagnostics", () => {
    render(<PreflightFacts
      preview={preview({ knowledge_references: [{ id: "knowledge-raw", version: "v1", title: "资料名称", authorization: "", ownership: "" }], selected_person: { profile_id: "independent-person", version: "p3" } })}
      voices={[{ id: "voice-1", version: "v2", display_name: "我的声音" }] as import("@/lib/media-profiles-api").VoiceProfile[]}
      people={[{ id: "independent-person", version: "p3", display_name: "所选人物" }] as import("@/lib/media-profiles-api").PersonProfile[]}
    />);
    expect(screen.getByText(/本次声音：我的声音/)).toBeVisible(); expect(screen.getByText(/本次人物：所选人物/)).toBeVisible();
    expect(screen.getByText("independent-person · p3")).not.toBeVisible(); expect(screen.getByText("knowledge-raw · v1")).not.toBeVisible();
    expect(screen.getByText("未返回证明字段 · 未返回归属字段")).not.toBeVisible();
    fireEvent.click(screen.getByText("资料引用诊断")); expect(screen.getByText("未返回证明字段 · 未返回归属字段")).toBeVisible();
  });
  it("keeps raw template voice impact available in diagnostics without using its ID as business copy", () => {
    render(<Blockers preview={preview({ blockers: [{ reason: "template_voice_unavailable", stage: "narration", impact: "模板指定声音 raw-profile · v3 不可用，未替换为其他声音。", repair_action: "恢复指定版本或选择其他模板", blocks_confirmation: true }] })} />);
    expect(screen.getByText("模板指定的固定声音版本不可用，未替换为其他声音。")).toBeVisible();
    expect(screen.getByText(/raw-profile/)).not.toBeVisible(); fireEvent.click(screen.getByText("阻断诊断")); expect(screen.getByText(/raw-profile/)).toBeVisible();
  });
});

it("shows API shot roles and zh voice in ordinary language while retaining raw diagnostics in a bilingual plan", () => {
  const facts = preview({ language: "zh-CN/en-US", pronunciation_references: [],
    material_matches: [{ asset_ref: "asset:actual", segment_ref: "segment:actual", score: .8,
      reasons: ["用途匹配 a_roll", "用途匹配 b_roll"], match_tier: "text" } as CreationPlanPreview["material_matches"][number]],
  });
  render(<PreflightFacts preview={facts} />);
  expect(screen.getByText("用途匹配 主镜头；用途匹配 补充画面")).toBeVisible();
  expect(screen.getByText(/适用语言：中文。/)).toBeVisible();
  expect(screen.queryByText(/适用语言：中英混读/)).not.toBeInTheDocument();
  const reasons = screen.getByText("原始理由：用途匹配 a_roll；用途匹配 b_roll");
  const language = screen.getByText("语言：zh");
  expect(reasons).not.toBeVisible();
  expect(language).not.toBeVisible();
  fireEvent.click(screen.getByText("候选诊断"));
  fireEvent.click(screen.getByText("声音引用诊断"));
  expect(reasons).toBeVisible();
  expect(language).toBeVisible();
  expect(facts.selected_voice?.language).toBe("zh");
  expect(facts.material_matches[0].reasons).toEqual(["用途匹配 a_roll", "用途匹配 b_roll"]);
});
