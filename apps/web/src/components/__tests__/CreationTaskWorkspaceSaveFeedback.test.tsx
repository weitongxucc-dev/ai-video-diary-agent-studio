import { chooseSelectValue } from "./select-interaction";
import { useState } from "react";
import * as api from "@/lib/api";
import * as taskMediaApi from "@/lib/api/task-media";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace } from "../CreationTaskWorkspace";
import type { CreationTask, CreationTaskEditPreviewResponse, CreationTaskEditConfirmResponse, CreationTaskQualityCheck, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";

// WIL-175 专项：输出页（activeView === "review"）原生保存的成功/取消/失败三种结果，
// 失败必须同时给出「没有保存成」「为什么」「接下来怎么做」，取消必须安静，
// 失败之后必须还能再次保存。剪辑（montage）与字幕封面（subtitle_cover）的既有错误行为
// 由 CreationTaskWorkspace.test.tsx 覆盖，本文件不重复。

const mocks = vi.hoisted(() => {
  // 与真实 lib/api 保持同名同形：errorDetails() 无条件执行 `cause instanceof CreationPlanApiError`，
  // 该符号若是 undefined 会直接抛 TypeError，因此即使没有用例构造它也必须存在。
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
    MockCreationPlanApiError,
    getWorkspace: vi.fn(),
    getQualityCheck: vi.fn(),
    checkQuality: vi.fn(),
  getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } }),
  getAssistantHistory: vi.fn().mockResolvedValue({ items: [] }),
  getExecution: vi.fn().mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }),
  getJobProgress: vi.fn().mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 }),
  previewExecution: vi.fn().mockResolvedValue(null),
  };
});

describe("task receipt ownership", () => {
  function finishedTaskExecution() {
    const initial = reviewWorkspace();
    return { task_id: initial.task.id, job: { id: "done-execution", kind: "creation_execution", status: "succeeded", progress: 100, failure_reason: null, impact: null, repair_action: null },
      stages: [], current_stage: null, waiting_reason: null, status_updated_at: initial.task.updated_at, video_ready: true, cover_status: "pending", cover_message: null };
  }
  it("rejects a workspace read started before a newer shared Task receipt", async () => {
    const initial = reviewWorkspace();
    const shared = { ...initial.task, visibility: "shared" as const, updated_at: "2026-10-14T00:00:01Z" };
    let resolveOld!: (value: WorkspaceState) => void;
    mocks.getWorkspace.mockReset().mockResolvedValueOnce(initial).mockReturnValueOnce(new Promise(done => { resolveOld = done; }));
    mocks.getExecution.mockResolvedValue(finishedTaskExecution());
    vi.mocked(api.setCreationTaskVisibility).mockResolvedValue({ ...initial, task: shared });
    render(<CreationTaskWorkspace initialWorkspace={initial} initialTopics={null} initialTasks={[initial.task]} />);
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "共享设置" }));
    fireEvent.click(screen.getByRole("button", { name: /^共享给团队$/ }));
    await screen.findByText("任务：团队共享");
    await act(async () => resolveOld({ ...initial, task: { ...initial.task } }));
    expect(screen.getByText("任务：团队共享")).toBeInTheDocument();
    expect(screen.queryByText("任务：仅自己可见")).not.toBeInTheDocument();
  });
  it("rejects a stale task timestamp read after sharing and continues to the newer full facts", async () => {
    vi.useFakeTimers();
    try {
      const initial = reviewWorkspace();
      const shared = { ...initial.task, visibility: "shared" as const, updated_at: "2026-10-14T00:00:01Z" };
      mocks.getExecution.mockResolvedValue({ ...finishedTaskExecution(), job: null });
      mocks.getWorkspace.mockReset().mockResolvedValueOnce(initial).mockResolvedValueOnce({ ...initial, task: { ...initial.task } })
        .mockResolvedValue({ ...initial, task: shared });
      vi.mocked(api.setCreationTaskVisibility).mockResolvedValue({ ...initial, task: shared });
      render(<CreationTaskWorkspace initialWorkspace={initial} initialTopics={null} initialTasks={[initial.task]} />);
      await act(async () => {});
      fireEvent.click(screen.getByRole("button", { name: "共享设置" }));
      fireEvent.click(screen.getByRole("button", { name: /^共享给团队$/ }));
      await act(async () => {});
      mocks.getExecution.mockResolvedValue(finishedTaskExecution());
      await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
      expect(screen.getByText("任务：团队共享")).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
      expect(mocks.getWorkspace).toHaveBeenCalledTimes(3);
      expect(screen.getByText("任务：团队共享")).toBeInTheDocument();
    } finally { vi.useRealTimers(); }
  });
  afterEach(() => mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }));
});

describe("editing proposal input ownership", () => {
  function editorWorkspace() {
    const data = reviewWorkspace();
    data.task = { ...data.task, stage: "montage", output_ready: false };
    data.montage = { ...data.montage!, status: "ready", output_url: null, clips: [0, 1].map(index => ({
      id: `clip-${index + 1}`, cue_id: `cue-${index + 1}`, asset_id: "asset-1", segment_id: `segment-${index + 1}`,
      source_reference: "workspace://synthetic.mp4", source_start_seconds: 0, source_end_seconds: 6,
      timeline_start_seconds: index * 6, timeline_end_seconds: (index + 1) * 6,
      match_score: 1, match_reasons: [], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false,
    })) };
    return data;
  }
  function preview(data: WorkspaceState): CreationTaskEditPreviewResponse {
    return { intent: { id: "intent-delete", workspace_id: "local", task_id: data.task.id,
      raw_command: "删除当前镜头", context_stage: "montage", selected_object_refs: ["clip:clip-1"], playhead_ms: null,
      intent_type: "exact_montage_edit", scope: {}, constraints: {}, confidence: 1, status: "interpreted", created_at: data.task.created_at },
      proposal: { id: "proposal-delete", workspace_id: "local", task_id: data.task.id, intent_id: "intent-delete",
        summary: "删除所选镜头并联动时轴", target_refs: ["clip:clip-1"], operation: { operation_type: "DeleteClip", payload: { clip_id: "clip-1" } },
        invalidated_refs: [], external_effects: [], preview: { before: { ref: "clip:clip-1", label: "当前镜头", detail: "6秒" }, after: { ref: "timeline:next", label: "删除后", detail: "保留其他镜头" } },
        status: "proposed", created_at: data.task.created_at, updated_at: data.task.updated_at } };
  }
  function saved(data: WorkspaceState, candidate: CreationTaskEditPreviewResponse): CreationTaskEditConfirmResponse {
    return { operation: { id: "delete-operation", workspace_id: "local", task_id: data.task.id, proposal_id: candidate.proposal.id,
      operation_type: "DeleteClip", payload: { clip_id: "clip-1" }, base_version_id: "timeline-version-1", result_version_id: "timeline-version-2",
      status: "applied", failure_reason: null, created_at: data.task.created_at, applied_at: data.task.updated_at }, montage: data.montage! };
  }
  async function begin(origin: "exact" | "input", compound = false) {
    const data = editorWorkspace();
    const candidate = preview(data);
    if (compound) candidate.proposal.operation = { operation_type: "EditCompoundTimeline", payload: {
      selection_enabled: true, steps: [{ index: 0, command: "删除当前镜头", enabled: true }, { index: 1, command: "复制下一段", enabled: true }],
    } };
    mocks.getWorkspace.mockResolvedValue(data);
    vi.mocked(api.previewCreationTaskEdit).mockResolvedValue(candidate);
    vi.mocked(api.previewCreationTaskExactEdit).mockResolvedValue(candidate);
    const view = render(<CreationTaskWorkspace initialWorkspace={data} initialTopics={null} initialTasks={[data.task]} />);
    const input = await screen.findByRole("textbox", { name: "局部修改要求" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "删除当前镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "展开镜头列表" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    if (origin === "exact") {
      fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
      fireEvent.click(screen.getByText("更多剪辑"));
      fireEvent.click(screen.getByRole("button", { name: "删除镜头…" }));
    } else {
      fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    }
    await screen.findByRole("button", { name: compound ? "应用到草稿" : "确认删除" });
    return { view, input, data, candidate };
  }

  it("keeps an unrelated draft even when its text equals an exact button proposal", async () => {
    const { data, candidate } = await begin("exact");
    vi.mocked(api.confirmCreationTaskEdit).mockResolvedValue(saved(data, candidate));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "修改提案" })).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "局部修改要求" })).toHaveValue("删除当前镜头");
    expect(vi.mocked(api.updateAssistantConversation).mock.calls.some(([, patch]) => patch.draft_text === "")).toBe(false);
  });

  it("clears the actual preview input after adoption when it remains unchanged", async () => {
    const { data, candidate } = await begin("input");
    vi.mocked(api.confirmCreationTaskEdit).mockResolvedValue(saved(data, candidate));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "局部修改要求" })).toHaveValue(""));
  });

  it("does not clear later input retyped to the same text while adoption waits", async () => {
    const { data, candidate } = await begin("input");
    let resolve!: (value: CreationTaskEditConfirmResponse) => void;
    vi.mocked(api.confirmCreationTaskEdit).mockReturnValue(new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "修改提案" })).getByRole("button", { name: "关闭卡片" }));
    const input = screen.getByRole("textbox", { name: "局部修改要求" });
    fireEvent.change(input, { target: { value: "后来输入" } });
    fireEvent.change(input, { target: { value: "删除当前镜头" } });
    await act(async () => resolve(saved(data, candidate)));
    expect(input).toHaveValue("删除当前镜头");
  });

  it("gives an unmounted task no draft write permission on its late adoption result", async () => {
    const { view, data, candidate } = await begin("input");
    let resolve!: (value: CreationTaskEditConfirmResponse) => void;
    vi.mocked(api.confirmCreationTaskEdit).mockReturnValue(new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    view.unmount();
    vi.mocked(api.updateAssistantConversation).mockClear();
    await act(async () => { resolve(saved(data, candidate)); await new Promise(done => setTimeout(done, 520)); });
    expect(vi.mocked(api.updateAssistantConversation).mock.calls.some(([, patch]) => patch.draft_text === "")).toBe(false);
  });

  it("does not rewrite later chat input when a compound selection preview returns", async () => {
    const { candidate } = await begin("input", true);
    let resolve!: (value: CreationTaskEditPreviewResponse) => void;
    vi.mocked(api.previewCompoundSelection).mockReturnValue(new Promise(done => { resolve = done; }));
    fireEvent.click(screen.getByRole("checkbox", { name: "保留修改2：复制下一段" }));
    fireEvent.click(screen.getByRole("button", { name: "重看所选" }));
    const input = screen.getByRole("textbox", { name: "局部修改要求", hidden: true });
    fireEvent.change(input, { target: { value: "后来输入的聊天草稿" } });
    await act(async () => resolve({ ...candidate, intent: { ...candidate.intent, raw_command: "旧请求编译后的命令" } }));
    expect(input).toHaveValue("后来输入的聊天草稿");
  });
});

// 忠实复刻真实 AppShell：inspector 放在 detailsOpen 门后（AppShell.tsx L222/L555），
// 默认不渲染，必须由用户点「查看详情」才出现。不得无条件渲染 inspector——
// 否则会凭空造出一个真实产品里看不见的 alert，把「输出页无提示」这个缺陷掩盖掉。
vi.mock("@/components/AppShell", () => ({
  AppShell: ({ children, conversation, inspector, quickInspector, quickInspectorLabel, toolbar, toolbarActions, inspectorTitle = "任务详情" }: { children: React.ReactNode; conversation?: React.ReactNode; toolbar?: React.ReactNode; toolbarActions?: React.ReactNode; inspector?: React.ReactNode; quickInspector?: (openDetails: () => void) => React.ReactNode; quickInspectorLabel?: string; inspectorTitle?: string }) => {
    const [detailsOpen, setDetailsOpen] = useState(false);
    return (
      <><header>{toolbar}{toolbarActions}</header><section>{conversation}</section><main>{children}</main>{quickInspector ? <section aria-label={quickInspectorLabel}>{quickInspector(() => setDetailsOpen(true))}</section> : null}{inspector && !detailsOpen ? <button onClick={() => setDetailsOpen(true)} type="button">查看详情</button> : null}{detailsOpen && inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}</>
    );
  },
}));

vi.mock("../DesktopVoiceRecorder", () => ({
  DesktopVoiceRecorder: ({ onCancel }: { onCancel: () => void }) => (
    <button onClick={onCancel} type="button">关闭录音</button>
  ),
}));

vi.mock("@/lib/auth/routing", () => ({ isLocalAuthMode: () => true }));
vi.mock("@/lib/local-member-auth", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/local-member-auth")>(),
  ensureLocalMemberIdentity: async () => ({ member_id: "member-1", role: "owner", member_token: "fixture-token" }),
}));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ role: "owner", token: "fixture-token", ready: true }) }));
vi.mock("@/lib/api", () => ({
  getCurrentAssistantConversation: vi.fn(async (scope: { scope_kind: string; scope_id: string }) => ({ id: `conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "local", ...scope, title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] })),
  createAssistantConversation: vi.fn(async (scope: { scope_kind: string; scope_id: string }) => ({ id: `new-conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "local", ...scope, title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] })),
  listAssistantConversations: vi.fn().mockResolvedValue({ items: [] }),
  getAssistantConversation: vi.fn(),
  resumeAssistantConversation: vi.fn(),
  updateAssistantConversation: vi.fn(async (id: string, update: { draft_text?: string; title?: string; expected_draft_version?: number }) => ({ id, workspace_id: "local", scope_kind: "task" as const, scope_id: "task-1", title: update.title ?? "新对话", draft_text: update.draft_text ?? "", draft_version: (update.expected_draft_version ?? 0) + (update.draft_text === undefined ? 0 : 1), created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] })),
  restoreUnpersistedAssistantTurnDraft: vi.fn(async (id: string, input: { user_message: string; expected_draft_version: number }) => ({ status: "restored" as const, conversation: { id, workspace_id: "local", scope_kind: "task" as const, scope_id: "task-1", title: "新对话", draft_text: input.user_message, draft_version: input.expected_draft_version + 1, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] } })),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantCapabilities: vi.fn().mockResolvedValue({ application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"], pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0", runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查", product_mode: "headless", reason: "未接通", commands: [] }, extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {} }),
  apiUrl: (path: string) => path,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  getCreationTaskGraphicSources: vi.fn().mockResolvedValue([]),
  getCreationPlan: vi.fn().mockRejectedValue(new Error("no plan in this fixture")),
  setCreationTaskVisibility: vi.fn(),
  setCreationPlanVisibility: vi.fn(),
  applyCreationTaskExactEdit: vi.fn(),
  confirmCreationTaskEdit: vi.fn(),
  confirmCreationTaskScript: vi.fn(),
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: vi.fn(),
  generateCreationTaskNarration: vi.fn(),
  generateCreationTaskPersonRender: vi.fn(),
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: vi.fn(),
  getCreationTaskTopics: vi.fn(),
  getCreationTaskWorkspace: mocks.getWorkspace,
  listCreationTaskRecordings: vi.fn().mockResolvedValue({ items: [] }),
  getCreationTaskQualityCheck: mocks.getQualityCheck,
  checkCreationTaskQuality: mocks.checkQuality,
  initializeCreationTaskTimeline: vi.fn(),
  getCreationTaskAssistantHistory: mocks.getAssistantHistory,
  getCreationExecution: mocks.getExecution,
  getTaskJobProgress: mocks.getJobProgress,
  previewCreationExecution: mocks.previewExecution,
  startCreationExecution: vi.fn(),
  resumeCreationExecution: vi.fn(),
  cancelCreationExecution: vi.fn(),
  getRuntimeSettings: mocks.getRuntimeSettings,
  sendCreationTaskAssistantTurn: vi.fn(),
  previewCreationTaskEdit: vi.fn(),
  previewCompoundSelection: vi.fn(),
  previewCreationTaskExactEdit: vi.fn(),
  previewCreationTaskAudio: vi.fn(),
  previewCreationTaskVisual: vi.fn(),
  resolveCreationTaskMediaResponse: vi.fn(async (_taskId, response) => response),
  TaskMediaJobError: class extends Error { job = { status: "failed" }; },
  renderCreationTaskMontage: vi.fn(),
  saveCreationTaskRecording: vi.fn(),
  selectCreationTaskNarrationVoice: vi.fn(),
  undoCreationTaskTimeline: vi.fn(),
  redoCreationTaskTimeline: vi.fn(),
  getProductionMaterials: vi.fn().mockResolvedValue({ assets: [], total_count: 0, limit: 100, offset: 0, has_more: false }),
}));

// 输出页所需的最小工作区：stage=review、有 output_url、无有效字幕轨
// （defaultWorkspaceView 因此返回 "review" 而不是 "subtitle_cover"）。
function reviewWorkspace(): WorkspaceState {
  const task: CreationTask = {
    visibility: "private",
    id: "task-1",
    workspace_id: "local",
    plan_id: "plan-1",
    name: "本地真成片",
    scenario: "制作一条中文口播视频",
    stage: "review",
    stage_status: "waiting_for_user",
    status: "waiting_for_user",
    progress: 90,
    recovery_point: "montage_rendered",
    selected_topic_id: "topic-1",
    confirmed_script_id: "script-1",
    narration_cache_key: null,
    montage_plan_id: "montage-plan-1",
    current_timeline_version_id: "timeline-version-1",
    timeline_sequence: 1,
    can_undo: true,
    output_ready: true,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
    stage_retryable: false,
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
  };
  return {
    task,
    plan: {
      goal: task.scenario,
      name: task.name,
      target_count: 1,
      language: "zh-CN",
      aspect_ratio: "9:16",
      duration_target_seconds: 30,
      knowledge_references: [],
      source_references: [],
      selected_voice: null,
      pronunciation_references: [],
      approved_asset_count: 1,
      approved_clip_count: 1,
      material_rule_summary: "只用已审核素材",
      material_matches: [],
      model_snapshot: [],
      confirmation_points: [],
      blockers: [],
      confirmable: true,
      preflight_fingerprint: "f".repeat(64),
    },
    selected_topic: null,
    topic_evidence: [],
    scripts: [],
    available_voices: [],
    narration: null,
    person_renders: [],
    person_render_capability: { available: false, model_id: null, message: "本地人物口型模型未配置。", repair_action: null },
    montage: {
      id: "montage-1",
      task_id: task.id,
      script_id: "script-1",
      script_title: "成片",
      status: "rendered",
      cues: [],
      clips: [],
      duration_seconds: 12,
      low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1",
      output_url: `/api/creation-tasks/${task.id}/output`,
      subtitle_url: `/api/creation-tasks/${task.id}/output/subtitles`,
      message: "等待检查",
      created_at: task.created_at,
      updated_at: task.updated_at,
    },
    subtitle_tracks: [],
    cover_asset: null,
    subtitle_translation_capability: { available: false, model_id: null, message: "当前未配置英文翻译模型。", repair_action: null },
    cover_frame_capability: { available: true, model_id: "ffmpeg+Pillow", message: "可从当前成片选帧生成封面。", repair_action: null },
    image_generation_capability: { available: false, model_id: null, message: "尚未配置图片模型。", repair_action: null },
  };
}

function useElectronSave(saveRenderedVideo: ReturnType<typeof vi.fn>) {
  Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveRenderedVideo } });
}

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(taskMediaApi, "readCreationTaskNativeOperations").mockImplementation(async taskId => ({ task_id: taskId, items: [], total_count: 0, truncated: false, offset: 0 }));
  mocks.getWorkspace.mockResolvedValue(reviewWorkspace());
  const qualityCheck: CreationTaskQualityCheck = {
    check_state: "none", result_status: null, stale: false, check_id: null,
    timeline_version_id: "timeline-version-1", render_ref: null, checked_at: null,
    error: null, findings: [], unavailable: [], clipping_summary: null, black_frame_summary: null, continuation: null,
    validation_scope: "deterministic_media_errors_only",
  };
  mocks.getQualityCheck.mockResolvedValue(qualityCheck);
  mocks.checkQuality.mockResolvedValue({ ...qualityCheck, check_state: "checking", check_id: "quality-check-1" });
});

afterEach(() => {
  vi.mocked(taskMediaApi.readCreationTaskNativeOperations).mockRestore();
  vi.clearAllMocks();
  mocks.previewExecution.mockResolvedValue(null);
  Reflect.deleteProperty(window, "electronAPI");
});

describe("CreationTaskWorkspace 输出页原生保存反馈", () => {
  it("保存失败时在输出页显示未保存状态、原因和可执行下一步", async () => {
    const saveRenderedVideo = vi.fn().mockResolvedValue({
      saved: false,
      error: "ENOENT: no such file or directory, open '/Volumes/素材盘/成片.mp4'",
    });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1", "timeline-version-1"));
    const alert = await screen.findByRole("alert");
    // 1) 未保存状态
    expect(alert).toHaveTextContent(/没有保存到本地/);
    // 2) 原因：桌面端返回的原始报错必须保留，不得被通用文案吞掉
    expect(alert).toHaveTextContent("ENOENT: no such file or directory");
    // 3) 可执行下一步
    expect(alert).toHaveTextContent(/再次点击「保存到本地」/);
    // 失败不得留下成功提示
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "成片质量检查状态" })).toHaveTextContent("当前成片尚未检查");
  });

  it.each([
    "本地服务没有返回可保存的 MP4（HTTP 404）。",
    "作品版本已变化，请刷新当前任务后重新下载。",
  ])("本地服务拒绝保存时保留具体原因：%s", async reason => {
    // 真实 IPC 返回形状：main.js 的 save-rendered-video 是 resolve {saved, error}，
    // 不会 reject、也不会把 API 的 body/code 结构化传回，error 就是一句人话字符串。
    // 若下一步只写「检查保存位置」，用户会对着完好的外接盘反复重试，永远查不到真正原因。
    const saveRenderedVideo = vi.fn().mockResolvedValue({
      saved: false,
      error: reason,
    });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/没有保存到本地/);
    // 桌面端返回的原始原因必须原样保留
    expect(alert).toHaveTextContent(reason);
    // 下一步必须同时覆盖「当前成片/本地服务」和「保存位置」，不能只归因磁盘
    expect(alert).toHaveTextContent(/当前成片和本地服务/);
    expect(alert).toHaveTextContent(/保存位置是否可写/);
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
  });

  it("用户取消保存时保持安静，既不报成功也不报失败", async () => {
    const saveRenderedVideo = vi.fn().mockResolvedValue({ saved: false, canceled: true });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1", "timeline-version-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存到本地" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
    // 打开「任务详情」也不该出现错误：取消是「什么都没发生」，不是「出了错但藏起来」。
    fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
    const inspector = await screen.findByRole("complementary", { name: "任务详情" });
    expect(within(inspector).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("失败后可以重新保存，成功提示只出现在真正写入之后", async () => {
    const saveRenderedVideo = vi.fn()
      .mockResolvedValueOnce({ saved: false, error: "ENOSPC: no space left on device, write '/Volumes/素材盘/成片.mp4'" })
      .mockResolvedValueOnce({ saved: true, fileName: "成片.mp4" });
    useElectronSave(saveRenderedVideo);

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await screen.findByRole("alert");
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "保存到本地" });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("已保存：成片.mp4")).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("缺少所见时间线版本时拒绝保存，不调用原生 latest 下载", async () => {
    const saveRenderedVideo = vi.fn();
    useElectronSave(saveRenderedVideo);
    const initial = reviewWorkspace();
    initial.task.current_timeline_version_id = null;
    mocks.getWorkspace.mockResolvedValue(initial);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("当前成片缺少时间线版本");
    expect(saveRenderedVideo).not.toHaveBeenCalled();
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
  });

  it("浏览器预览没有原生保存能力时说明没有保存成和正确下一步", async () => {
    Reflect.deleteProperty(window, "electronAPI");

    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={reviewWorkspace()} />);
    fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^检查与输出：/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/没有保存到本地/);
    expect(alert).toHaveTextContent("请在桌面客户端中使用原生保存功能");
    expect(screen.queryByText(/^已保存(?:：|到所选位置)/)).not.toBeInTheDocument();
  });
});

function subtitleWorkspace(): WorkspaceState {
  const workspace = reviewWorkspace();
  workspace.subtitle_tracks = [
    {
      id: "subtitle_zh", workspace_id: "local", task_id: workspace.task.id,
      timeline_version_id: "timeline-version-1", language: "zh-CN", source_language: null,
      style: { name: "default", font_family: "PingFang SC", font_size: 40, primary_color: "#fff", emphasis_color: "#ff0", position: "bottom", bilingual_order: "translation_then_source" },
      cues: [{ id: "cue_zh", source_cue_id: null, text: "新中文", start_seconds: 0.4, end_seconds: 2.8, speaker: null, line_breaks: [], emphasis: [] }],
      generation_method: "timeline", model_id: null, artifact_ref: null, transform_id: null,
      status: "valid", failure_reason: null, impact: null, repair_action: null,
      created_at: workspace.task.created_at, updated_at: workspace.task.updated_at,
    },
    {
      id: "subtitle_en", workspace_id: "local", task_id: workspace.task.id,
      timeline_version_id: "timeline-version-1", language: "en-US", source_language: "zh-CN",
      style: { name: "default", font_family: "PingFang SC", font_size: 40, primary_color: "#fff", emphasis_color: "#ff0", position: "bottom", bilingual_order: "translation_then_source" },
      cues: [{ id: "cue_en", source_cue_id: "cue_zh", text: "New English", start_seconds: 0.4, end_seconds: 2.8, speaker: null, line_breaks: [], emphasis: [] }],
      generation_method: "translation", model_id: null, artifact_ref: null, transform_id: null,
      status: "valid", failure_reason: null, impact: null, repair_action: null,
      created_at: workspace.task.created_at, updated_at: workspace.task.updated_at,
    },
  ];
  return workspace;
}

describe("CreationTaskWorkspace 字幕保存", () => {
  it("显式选择双语后传当前版本和两轨，取消无成功提示，失败可重试", async () => {
    const saveSubtitleFile = vi.fn()
      .mockResolvedValueOnce({ saved: false, canceled: true })
      .mockResolvedValueOnce({ saved: false, error: "当前字幕版本已变化" })
      .mockResolvedValueOnce({ saved: true, fileName: "task-1-zh-en.srt" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveSubtitleFile } });
    const workspace = subtitleWorkspace();
    mocks.getWorkspace.mockResolvedValue(workspace);
    render(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace} />);
    fireEvent.click(screen.getByRole("tab", { name: /^字幕与封面：/ }));
    fireEvent.click(screen.getByRole("button",{name:"字幕导出设置"}));
    await chooseSelectValue(screen.getByRole("combobox", { name: "保存字幕语言" }), "zh-en");
    fireEvent.keyDown(document.activeElement!,{key:"Escape"});
    const save = screen.getByRole("button", { name: "保存字幕" });
    fireEvent.click(save);
    await waitFor(() => expect(saveSubtitleFile).toHaveBeenCalledWith({
      taskId: "task-1", expectedVersionId: "timeline-version-1", mode: "zh-en",
      zhTrackId: "subtitle_zh", enTrackId: "subtitle_en",
    }));
    await waitFor(() => expect(save).toBeEnabled());
    expect(screen.queryByText(/字幕已保存/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "本地未提交草稿" } });
    fireEvent.click(save);
    expect(await screen.findByRole("alert")).toHaveTextContent("当前字幕版本已变化");
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("本地未提交草稿");
    expect(screen.getByRole("alert")).toHaveTextContent("当前任务与字幕草稿未修改");
    fireEvent.click(save);
    expect(await screen.findByText(/字幕已保存：task-1-zh-en.srt/)).toBeInTheDocument();
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("本地未提交草稿");
  });
});
