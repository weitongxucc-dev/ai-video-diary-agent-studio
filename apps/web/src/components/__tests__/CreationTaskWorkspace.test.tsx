import { Children, isValidElement, useState, type ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { chooseSelectValue } from "./select-interaction";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreationTaskWorkspace as ActualCreationTaskWorkspace, FinalVideoQualityCheck, PreviewVideo } from "../CreationTaskWorkspace";
import type { CreationExecutionPreview, CreationExecutionStatus, CreationTask, CreationTaskTopicsResponse, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import { confirmCreationTaskTopic, generateCreationTaskTopics, generateCreationTaskScript, setCreationTaskVisibility, getCreationTaskGraphicSources, getAssistantConversation } from "@/lib/api";
import { AssistantConversationProvider } from "../AssistantConversationProvider";
import { referenceAssistantMaterial } from "@/lib/assistant-reference";
import { ScriptEditor, ConfirmedScriptView } from "../creation-task/ScriptStage";
vi.mock("@/lib/api/creation-tasks", async importOriginal => ({
  ...await importOriginal<typeof import("@/lib/api/creation-tasks")>(),
  getCreationTaskNarrationVoiceSelection: (...args: unknown[]) => mocks.getVoiceSelection(...args),
}));

const mocks = vi.hoisted(() => {
  class MockCreationPlanApiError extends Error {
    constructor(
      public readonly status: number,
      public readonly code: string | null,
      message: string,
      public readonly impact: string | null = null,
      public readonly repairAction: string | null = null,
      public readonly reason: string | null = null,
      public readonly submissionEffect: 'not_submitted'|'rejected'|'unknown'|null = null,
    ) {
      super(message);
    }
  }
  return {
    MockCreationPlanApiError,
    assistantConversations: new Map<string, import("@/lib/types").AssistantConversation>(),
    currentAssistantConversation: new Map<string, string>(),
    assistantSequence: 0,
    entryWorkspace: null as WorkspaceState | null,
    assistantActions: null as null | {
      onAssistantSend: () => Promise<void>; onConversationChange: () => void;
      onCommandChange: (value: string) => void;
      assistantState: ReturnType<typeof import("../useAssistantConversation").useAssistantConversation>;
    },
    showQuickInspector: false,
    realShell: false,
    openQuickDetails: vi.fn(),
    realMediaJobReads: false,
    withReferenceProvider: false,
    seedReference: null as import("@/lib/types").AssistantSavedReference | null,
    references: new Map<string, import("@/lib/types").AssistantConversationReferenceState>(),
    saveReference: vi.fn(),
    materialReference: null as import("@/lib/assistant-reference").AssistantMaterialReference | null,
    setMaterialReference: vi.fn(),
    materialTurn: vi.fn().mockResolvedValue({ reply:"参考已读取", material_result:null }),
    confirmEdit: vi.fn(),
    confirmScript: vi.fn(),
    saveScriptDraft: vi.fn(),
    saveLibrary: vi.fn(),
    exactEdit: vi.fn(),
    exactPreview: vi.fn(),
    createMontage: vi.fn(),
    generateNarration: vi.fn(),
    generatePersonRender: vi.fn(),
    getCandidates: vi.fn(),
    getWorkspace: vi.fn(),
    getQualityCheck: vi.fn().mockResolvedValue({ check_state: "none", result_status: null,
      stale: false, timeline_version_id: null, findings: [], unavailable: [] }),
    checkQuality: vi.fn(),
    initializeTimeline: vi.fn(),
    getAssistantHistory: vi.fn().mockResolvedValue({ items: [] }),
    getAssistantScriptContext: vi.fn().mockResolvedValue({ reply_origin: null, sources: [] }),
    getTopics: vi.fn(),
    getExecution: vi.fn().mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null }),
    getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } }),
  getJobProgress: vi.fn().mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 }),
    previewExecution: vi.fn().mockResolvedValue(null),
    startExecution: vi.fn(),
    resumeExecution: vi.fn(),
    cancelExecution: vi.fn(),
    previewEdit: vi.fn(),
    getEditPreview: vi.fn(),
    previewSelection: vi.fn(),
    askAssistant: vi.fn(),
    renderMontage: vi.fn(),
    selectVoice: vi.fn(),
    getVoiceSelection: vi.fn(),
    undoTimeline: vi.fn(),
    readTimelineRestore: vi.fn(),
    redoTimeline: vi.fn(),
    saveRecording: vi.fn(),
    listRecordings: vi.fn().mockResolvedValue({ items: [] }),
    adoptRecording: vi.fn(),
    previewAudioEdit: vi.fn(),
    getProductionMaterials: vi.fn().mockResolvedValue({ assets: [], total_count: 0, limit: 100, offset: 0, has_more: false }),
    listTimelineVersions: vi.fn().mockResolvedValue({ task_id: "task-1", current_version_id: null, items: [] }),
    setCreationTaskVisibility: vi.fn(),
    setCreationPlanVisibility: vi.fn(),
    getCreationPlan: vi.fn().mockRejectedValue(new Error("no plan loaded in this test")),
    getTimelineVersion: vi.fn(),
  };
});

vi.mock("../AssistantConversationProvider", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../AssistantConversationProvider")>();
  const setReference = (value: import("@/lib/assistant-reference").AssistantMaterialReference | null) => {
    mocks.setMaterialReference(value);
    mocks.materialReference = value;
  };
  return { ...actual, useAssistantMaterialReference: () => actual.useAssistantMaterialReference() ?? ({reference:mocks.materialReference, setReference}) };
});

vi.mock("@/components/AppShell", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/AppShell")>();
  return { AppShell: (props: import("@/components/AppShell").AppShellProps) => {
    if (mocks.realShell) return <actual.AppShell {...props} />;
    const { children, conversation, inspector, inspectorConfirmation, quickInspector, quickInspectorLabel, toolbar, toolbarActions, inspectorTitle = "任务详情" } = props;
    const findConversation = (node: import("react").ReactNode): void => { for (const child of Children.toArray(node)) {
      if (!isValidElement<{assistantState?: unknown; children?: import("react").ReactNode}>(child)) continue;
      if (child.props.assistantState) mocks.assistantActions = child.props as typeof mocks.assistantActions;
      else findConversation(child.props.children);
    } };
    findConversation(conversation);
    return <><header>{toolbar}{toolbarActions}</header><section>{conversation}</section><main>{children}</main>{mocks.showQuickInspector && quickInspector ? <section aria-label={quickInspectorLabel}>{quickInspector(mocks.openQuickDetails)}</section> : null}{inspector ? <aside aria-label={inspectorTitle}>{inspector}</aside> : null}{inspectorConfirmation}</>;
  } };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/tasks/task-1", useRouter: () => ({ push: vi.fn(), back: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ role: "owner" }) }));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));

vi.mock("../DesktopVoiceRecorder", () => ({
  DesktopVoiceRecorder: ({ onRecorded, onCancel }: { onRecorded: (blob: Blob) => void | Promise<void>; onCancel: () => void }) => {
    const [error, setError] = useState<string | null>(null);
    return <div data-testid="voice-recorder">
      <button onClick={() => void Promise.resolve(onRecorded(new Blob(["audio"], { type: "audio/webm" }))).catch(cause => setError(cause.message))} type="button">提交录音</button>
      {error ? <p role="alert">{error}</p> : null}
      <button onClick={onCancel} type="button">取消录音</button>
    </div>
  },
}));

vi.mock("@/lib/api", async importOriginal => ({ ...(await importOriginal<typeof import("@/lib/api")>()),
  getAssistantConversationReference: vi.fn(async (id: string) => {
    const value = mocks.references.get(id) ?? { selected_reference: mocks.seedReference, reference_version: 0, status: "ready" as const, reason: null };
    mocks.references.set(id, value);
    return value;
  }),
  saveAssistantConversationReference: vi.fn(async (id: string, selected_reference: import("@/lib/types").AssistantSavedReference | null, version: number) => {
    mocks.saveReference(id, selected_reference, version);
    if (version !== (mocks.references.get(id)?.reference_version ?? 0)) throw new Error("reference CAS conflict");
    const value = { selected_reference, reference_version: version + 1, status: "ready" as const, reason: null };
    mocks.references.set(id, value);
    return value;
  }),
  listCreationTaskMediaJobs: vi.fn(async (...args: Parameters<typeof import("@/lib/api").listCreationTaskMediaJobs>) => mocks.realMediaJobReads ? (await importOriginal<typeof import("@/lib/api")>()).listCreationTaskMediaJobs(...args) : { items: [], total_count: 0, has_more: false, limit: 50, offset: 0 }),
  getCurrentAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => {
    const id = mocks.currentAssistantConversation.get(`${scope.scope_kind}:${scope.scope_id}`);
    if (id) return mocks.assistantConversations.get(id)!;
    const conversation = { id: `conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "local", ...scope,
      title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] };
    mocks.assistantConversations.set(conversation.id, conversation);
    mocks.currentAssistantConversation.set(`${scope.scope_kind}:${scope.scope_id}`, conversation.id);
    return conversation;
  }),
  createAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => {
    const id = `new-conversation-${scope.scope_kind}-${scope.scope_id}-${++mocks.assistantSequence}`;
    const conversation = { id, workspace_id: "local", ...scope, title: "新对话", draft_text: "", draft_version: 0,
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [] };
    mocks.assistantConversations.set(id, conversation);
    mocks.currentAssistantConversation.set(`${scope.scope_kind}:${scope.scope_id}`, id);
    return conversation;
  }),
  listAssistantConversations: vi.fn(async (scope: { scope_kind: string; scope_id: string }) => ({ items: [...mocks.assistantConversations.values()].filter((item) => item.scope_kind === scope.scope_kind && item.scope_id === scope.scope_id) })),
  getAssistantConversation: vi.fn(async (id: string) => mocks.assistantConversations.get(id)!),
  resumeAssistantConversation: vi.fn(async (id: string) => {
    const conversation = mocks.assistantConversations.get(id)!;
    mocks.currentAssistantConversation.set(`${conversation.scope_kind}:${conversation.scope_id}`, id);
    return conversation;
  }),
  updateAssistantConversation: vi.fn(async (id: string, update: { draft_text?: string; title?: string; expected_draft_version?: number }) => {
    const conversation = mocks.assistantConversations.get(id)!;
    if (update.draft_text !== undefined && update.expected_draft_version !== conversation.draft_version) throw new Error("draft CAS conflict");
    const { expected_draft_version: _expected, ...changes } = update;
    const updated = { ...conversation, ...changes,
      draft_version: conversation.draft_version + (update.draft_text !== undefined ? 1 : 0), updated_at: "2026-10-09T00:00:01Z" };
    mocks.assistantConversations.set(id, updated);
    return updated;
  }),
  restoreUnpersistedAssistantTurnDraft: vi.fn(async (id: string, input: { client_request_id: string; user_message: string; expected_draft_version: number }) => {
    const conversation = mocks.assistantConversations.get(id)!;
    if (conversation.draft_version !== input.expected_draft_version) throw new Error("draft CAS conflict");
    const restored = { ...conversation, draft_text: input.user_message, draft_version: conversation.draft_version + 1 };
    mocks.assistantConversations.set(id, restored);
    return { status: "restored", conversation: restored };
  }),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantCapabilities: vi.fn().mockResolvedValue({ application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"], pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0", runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查", product_mode: "headless", reason: "未接通", commands: [] }, extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {} }),
  listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }),
  apiUrl: (path: string) => path,
  getCreationTaskGraphicSources: vi.fn().mockResolvedValue([]),
  applyCreationTaskExactEdit: mocks.exactEdit,
  CreationPlanApiError: mocks.MockCreationPlanApiError,
  confirmCreationTaskEdit: mocks.confirmEdit,
  confirmCreationTaskScript: mocks.confirmScript,
  sendMaterialAssistantTurn: mocks.materialTurn,
  confirmCreationTaskTopic: vi.fn(),
  createCreationTaskMontagePlan: mocks.createMontage,
  generateCreationTaskNarration: mocks.generateNarration,
  generateCreationTaskPersonRender: mocks.generatePersonRender,
  generateCreationTaskScript: vi.fn(),
  generateCreationTaskTopics: vi.fn(),
  getCreationTaskMontageClipCandidates: mocks.getCandidates,
  getCreationTaskTopics: mocks.getTopics,
  getCreationTaskWorkspace: (...args: unknown[]) => mocks.getWorkspace(...args) ?? Promise.resolve(mocks.entryWorkspace),
  getCreationTaskEditPreview: mocks.getEditPreview,
  getCreationTaskQualityCheck: mocks.getQualityCheck,
  checkCreationTaskQuality: mocks.checkQuality,
  getCreationPlan: mocks.getCreationPlan,
  getAssistantScriptContext: mocks.getAssistantScriptContext,
  saveAssistantScriptDraft: mocks.saveScriptDraft,
  saveScriptLibraryEntry: mocks.saveLibrary,
  setCreationTaskVisibility: mocks.setCreationTaskVisibility,
  setCreationPlanVisibility: mocks.setCreationPlanVisibility,
  initializeCreationTaskTimeline: mocks.initializeTimeline,
  getCreationTaskAssistantHistory: mocks.getAssistantHistory,
  getCreationExecution: mocks.getExecution,
  getTaskJobProgress: mocks.getJobProgress,
  previewCreationExecution: mocks.previewExecution,
  startCreationExecution: mocks.startExecution,
  resumeCreationExecution: mocks.resumeExecution,
  cancelCreationExecution: mocks.cancelExecution,
  getRuntimeSettings: mocks.getRuntimeSettings,
  sendCreationTaskAssistantTurn: vi.fn(async (taskId: string, input: { conversation_id: string; message: string; client_request_id: string; expected_draft_version: number }, onEvent: (event: import("@/lib/types").AssistantStreamEvent) => void) => {
    const conversation = mocks.assistantConversations.get(input.conversation_id);
    if (!conversation) throw new Error("对话夹具不存在");
    if (conversation.draft_version !== input.expected_draft_version || conversation.draft_text !== input.message) throw new Error("draft CAS conflict");
    const turnId = `task-turn-${++mocks.assistantSequence}`;
    const events: import("@/lib/types").AssistantTurnActivity["events"] = [];
    const emit = (event: import("@/lib/types").AssistantStreamEvent) => {
      onEvent(event);
      if (event.type === "text_delta" || event.type === "tool_start" || event.type === "tool_end") events.push(event);
    };
    mocks.assistantConversations.set(conversation.id, { ...conversation, draft_text: "", draft_version: input.expected_draft_version + 1 });
    emit({ type: "turn_started", turn_id: turnId, turn_state: "running", draft_version: input.expected_draft_version + 1 });
    const result = await mocks.askAssistant(taskId, input, emit);
    if (!events.some((event) => event.type === "text_delta") && typeof result.reply === "string") emit({ type: "text_delta", delta: result.reply });
    const saved = mocks.assistantConversations.get(input.conversation_id)!;
    const turn = { id: turnId, sequence: mocks.assistantSequence, user_message: input.message,
      assistant_text: events.filter((event) => event.type === "text_delta").map((event) => event.delta).join(""),
      status: "completed" as const, events, result: { kind: "task_turn", execution_action: result.execution_action ?? null,
        execution_status: result.execution_status?.job?.status ?? null, script_draft: result.script_draft?.id ?? null,
        topic_candidates: Boolean(result.topic_candidates), edit_preview: Boolean(result.edit_preview) },
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:01Z" };
    mocks.assistantConversations.set(saved.id, { ...saved, draft_text: "", draft_version: input.expected_draft_version + 1,
      turns: [...saved.turns, turn], updated_at: turn.updated_at });
    emit({ type: "turn_status", turn_id: turnId, turn_state: "completed" });
    return { ...result, turn_id: turnId, turn_status: "completed", draft_version: input.expected_draft_version + 1 };
  }),
  previewCreationTaskEdit: mocks.previewEdit,
  previewCompoundSelection: mocks.previewSelection,
  previewCreationTaskExactEdit: mocks.exactPreview,
  renderCreationTaskMontage: mocks.renderMontage,
  selectCreationTaskNarrationVoice: mocks.selectVoice,
  undoCreationTaskTimeline: mocks.undoTimeline,
  readCreationTaskTimelineRestore: mocks.readTimelineRestore,
  redoCreationTaskTimeline: mocks.redoTimeline,
  saveCreationTaskRecording: mocks.saveRecording,
  listCreationTaskRecordings: mocks.listRecordings,
  adoptCreationTaskRecording: mocks.adoptRecording,
  previewCreationTaskAudio: mocks.previewAudioEdit,
  getProductionMaterials: mocks.getProductionMaterials,
  listCreationTaskTimelineVersions: mocks.listTimelineVersions,
  getCreationTaskTimelineVersion: mocks.getTimelineVersion,
}));

const baseTask: CreationTask = {
  visibility: "private",
  id: "task-1",
  workspace_id: "local",
  plan_id: "plan-1",
  name: "本地真成片",
  scenario: "制作一条中文口播视频",
  stage: "narration",
  stage_status: "failed",
  status: "failed",
  progress: 40,
  recovery_point: "script_confirmed",
  selected_topic_id: "topic-1",
  confirmed_script_id: "script-1",
  narration_cache_key: null,
  montage_plan_id: null,
  current_timeline_version_id: null,
  timeline_sequence: 0,
  can_undo: false,
  output_ready: false,
  stage_reason: "voice_identity_missing",
  stage_impact: "当前创作任务未生成配音，未调用声音模型。",
  stage_repair_action: "重新预检并选择已授权的中文声音版本。",
  stage_retryable: true,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};


// Primary editing views stay visible; preparation/recovery is a real menu action.
function queryWorkspaceViewControl(name: string | RegExp) {
  const tab = screen.queryByRole("tab", { name });
  if (tab) return tab;
  if (!screen.queryByRole("menu", { name: "制作进度与恢复" })) fireEvent.click(screen.getByRole("button", { name: "制作进度" }));
  return screen.queryByRole("menuitem", { name });
}
function getWorkspaceViewControl(name: string | RegExp) {
  const control = queryWorkspaceViewControl(name);
  if (!control) throw new Error(`Work view is unavailable: ${name}`);
  return control;
}
describe("persisted final video quality checks", () => {
  const completed = { check_state: "done", result_status: "issues_found", stale: false,
    timeline_version_id: "timeline-version-6", check_id: "check-1", render_ref: "hash",
    checked_at: null, error: null, validation_scope: "deterministic_media_errors_only",
    findings: [{ kind: "black_frame", start_seconds: 3.25, end_seconds: 4.1,
      duration_seconds: .85, detail: "真实黑场", clip_ids: ["clip-2"] }],
    unavailable: [{ kind: "clipping", reason: "音频无法测量", status: "unavailable" }] };

  it("shows identity read failure without claiming unchecked or triggering inspection", async () => {
    mocks.getQualityCheck.mockRejectedValueOnce(new Error("身份服务不可用"));
    render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6"
      outputUrl={null} onLocate={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("身份服务不可用");
    expect(screen.getByRole("status", { name: "成片质量检查状态" })).toHaveTextContent("检查结果读取失败");
    expect(screen.queryByText("当前成片尚未检查")).not.toBeInTheDocument();
    expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    expect(mocks.checkQuality).not.toHaveBeenCalled();
  });

  it.each([
    [{ check_state: "checking", result_status: null }, "成片检查中"],
    [{ check_state: "failed", result_status: null, error: "解码失败" }, "成片检查失败"],
    [{ result_status: "unavailable", findings: [] }, "部分检测不可用"],
    [{ result_status: "clean", findings: [], unavailable: [] }, "未发现确定性媒体问题"],
    [{ stale: true }, "旧检查已失效，请检查当前成片"],
  ])("separates persisted states %s", async (overrides, label) => {
    mocks.getQualityCheck.mockResolvedValue({ ...completed, ...overrides });
    render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6"
      outputUrl="/video" onLocate={vi.fn()} />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    if (label !== "未发现确定性媒体问题") expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    if (label.includes("失效")) expect(screen.queryByText("真实黑场")).not.toBeInTheDocument();
  });

  it("shows issues and unavailable together, seeks exact interval on current player and selects its clip", async () => {
    const initial = subtitleCoverWorkspace();
    const clip = { id: "clip-1", cue_id: "cue", asset_id: "asset", segment_id: "segment",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
      needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false };
    initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
    mocks.getQualityCheck.mockResolvedValue(completed);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^检查与输出：/));
    expect(await screen.findByText("成片发现问题")).toBeInTheDocument();
    expect(screen.getByText("检测不可用：音频无法测量")).toBeInTheDocument();
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    const pause = vi.spyOn(player, "pause").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "定位 3.25–4.10 秒" }));
    expect(pause).toHaveBeenCalled();
    expect(player.currentTime).toBe(3.25);
    expect(screen.getByLabelText("局部修改要求")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "调整成片" }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    const editor = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(editor);
    expect(editor.currentTime).toBe(3.25);
    expect(editor).toBe(player);
    const reads = mocks.getQualityCheck.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name:"成片检查" }));
    const details = await screen.findByRole("dialog", { name:"成片质量检查" });
    expect(within(details).getByText("真实黑场")).toBeVisible();
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(mocks.getQualityCheck).toHaveBeenCalledTimes(reads);
    fireEvent.click(within(details).getByRole("button", { name:"定位 3.25–4.10 秒" }));
    await waitFor(()=>expect(screen.queryByRole("dialog",{name:"成片质量检查"})).not.toBeInTheDocument());
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(player.currentTime).toBe(3.25);
    pause.mockRestore();
  });

  it("posts only task identity, polls checking to done and reloads persisted result after remount", async () => {
    mocks.getQualityCheck.mockResolvedValueOnce({ ...completed, check_state: "none", result_status: null,
      findings: [], unavailable: [] }).mockResolvedValue(completed);
    mocks.checkQuality.mockResolvedValue({ ...completed, check_state: "checking", result_status: null,
      findings: [], unavailable: [] });
    const props = { taskId: "task-1", versionId: "timeline-version-6", outputUrl: "/video", onLocate: vi.fn() };
    const view = render(<FinalVideoQualityCheck {...props} />);
    expect(await screen.findByText("当前成片尚未检查")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新检查成片" }));
    expect(await screen.findByText("成片检查中")).toBeInTheDocument();
    expect(mocks.checkQuality).toHaveBeenCalledWith("task-1");
    expect(await screen.findByText("成片发现问题", {}, { timeout: 2000 })).toBeInTheDocument();
    view.unmount();
    render(<FinalVideoQualityCheck {...props} />);
    expect(await screen.findByText("真实黑场")).toBeInTheDocument();
  });

  it("discards a late response after version change and never shows an old clean result", async () => {
    let resolve!: (value: unknown) => void;
    mocks.getQualityCheck.mockReturnValueOnce(new Promise(done => { resolve = done; }))
      .mockResolvedValue({ ...completed, stale: true, timeline_version_id: "v7", findings: [], unavailable: [] });
    const view = render(<FinalVideoQualityCheck taskId="task-1" versionId="timeline-version-6" outputUrl="/video" onLocate={vi.fn()} />);
    view.rerender(<FinalVideoQualityCheck taskId="task-1" versionId="v7" outputUrl={null} onLocate={vi.fn()} />);
    expect(await screen.findByText("旧检查已失效，请检查当前成片")).toBeInTheDocument();
    await act(async () => resolve({ ...completed, result_status: "clean", findings: [], unavailable: [] }));
    expect(screen.queryByText("未发现确定性媒体问题")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^定位/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查成片" })).toBeDisabled();
  });

  it("shows clipping totals and separate evidence limits only when a current summary exists", async () => {
    const clipping_summary = {
      event_count: 2001, total_clipped_seconds: 20.01,
      first_start_seconds: 0, last_end_seconds: 40, full_scale_duty: .25,
      event_list_limit: 50, events: [{ start_seconds: 0, end_seconds: .01, duration_seconds: .01 }],
      events_truncated: true, truncated_event_count: 1951,
      findings_event_limit: 2000, findings_truncated: true, findings_truncated_count: 1,
    };
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary });
    const props = { taskId: "task-1", versionId: "timeline-version-6", outputUrl: "/video", onLocate: vi.fn() };
    const view = render(<FinalVideoQualityCheck {...props} />);
    const summary = await screen.findByLabelText("音频削平摘要");
    expect(summary).toHaveTextContent("音频削平共 2001 段，累计 20.01 秒。");
    expect(summary).toHaveTextContent("削平问题明细已限流至 2000 条，另有 1 段未逐条列出；总量包含这些事件。");
    expect(summary).toHaveTextContent("削平区间摘要仅保留 50 条，另有 1951 段未在区间摘要中列举；总量仍完整。");
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary: null });
    view.rerender(<FinalVideoQualityCheck {...props} outputUrl="/new-video" />);
    await waitFor(() => expect(screen.queryByLabelText("音频削平摘要")).not.toBeInTheDocument());
    expect(await screen.findByText("成片发现问题")).toBeInTheDocument();
    expect(screen.queryByText(/音频削平共/)).not.toBeInTheDocument();
    mocks.getQualityCheck.mockResolvedValue({ ...completed, clipping_summary, stale: true });
    view.rerender(<FinalVideoQualityCheck {...props} outputUrl="/stale-video" />);
    expect(await screen.findByText("旧检查已失效，请检查当前成片")).toBeInTheDocument();
    expect(screen.queryByLabelText("音频削平摘要")).not.toBeInTheDocument();
  });
});

function queuePreview(): CreationExecutionPreview {
  return {
    task_id: "task-1", script_id: "script-1", script_title: "真实文案",
    script_sections: [{ title: "开场", content: "这段就是实际要读的内容" }],
    plan_id: "plan-1", input_digest: "a".repeat(64),
    narration_source: null,
    requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
      presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
      can_continue: true, continuation_blocker: null, template_settings: {} },
  };
}

function queueStatus(status: NonNullable<CreationExecutionStatus["job"]>["status"] | null): CreationExecutionStatus {
  return { task_id: "task-1", job: status ? {
    id: "job-1", kind: "creation_task_execution", status, progress: 0,
    failure_reason: null, impact: null, repair_action: null,
  } : null, stages: [], current_stage: status === "running" ? "montage_plan" : null,
    waiting_reason: status === "queued" ? "设备资源正在处理其他工作。" : null,
    status_updated_at: "2026-10-07T00:00:00Z", video_ready: false, cover_status: "pending", cover_message: null };
}

function CreationTaskWorkspace(props: Parameters<typeof ActualCreationTaskWorkspace>[0]) {
  mocks.entryWorkspace = props.initialWorkspace;
  return mocks.withReferenceProvider ? <AssistantConversationProvider><ActualCreationTaskWorkspace {...props} /></AssistantConversationProvider> : <ActualCreationTaskWorkspace {...props} />;
}

// Historical business cases explicitly navigate to the view they exercise.
function renderTaskSubject(element: ReactElement<Parameters<typeof ActualCreationTaskWorkspace>[0]>) {
  const view = render(element);
  const workspace = element.props.initialWorkspace;
  const stage = workspace.task.stage === "review" && workspace.montage?.output_url
    && workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status !== "valid"
    ? "subtitle_cover" : workspace.task.stage;
  const title = { prepare: "资料准备", topic: "选题", script: "文案", narration: "配音", person: "人物与口型", montage: "视频剪辑", subtitle: "字幕与封面", subtitle_cover: "字幕与封面", review: "检查与输出", publish: "发布与数据" }[stage];
  const control = queryWorkspaceViewControl(new RegExp(`^${title}：`));
  if (control) fireEvent.click(control);
  return view;
}

function workspace(task: CreationTask, montage: WorkspaceState["montage"] = null): WorkspaceState {
  return {
    task,
    plan: {
      updated_at:task.updated_at,
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
      material_matches: [{ asset_ref: "asset:asset-1", segment_ref: "segment:segment-1", score: 0.9, reasons: ["已审核"] }],
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
    person_render_capability: {
      available: true,
      model_id: "musetalk-local",
      message: "本地人物口型模型可用。",
      repair_action: null,
    },
    montage,
    subtitle_tracks: [],
    cover_asset: null,
    subtitle_translation_capability: {
      available: false,
      model_id: null,
      message: "当前未配置英文翻译模型。",
      repair_action: "在设置中连接文字模型。",
    },
    cover_frame_capability: {
      available: true,
      model_id: "ffmpeg+Pillow",
      message: "可从当前成片选帧生成封面。",
      repair_action: null,
    },
    image_generation_capability: {
      available: false,
      model_id: null,
      message: "尚未配置图片模型。",
      repair_action: "配置图片模型后再使用 AI 生图。",
    },
  };
}

function subtitleCoverWorkspace() {
  const task = {
    ...baseTask,
    stage: "review" as const,
    stage_status: "waiting_for_user" as const,
    status: "waiting_for_user" as const,
    progress: 90,
    current_timeline_version_id: "timeline-version-6",
    timeline_sequence: 6,
    can_undo: true,
    output_ready: true,
    stage_reason: null,
    stage_impact: null,
    stage_repair_action: null,
  };
  const result = workspace(task, {
    id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
    status: "rendered", cues: [], clips: [], duration_seconds: 12,
    low_confidence_clip_count: 0, narration_reference: "narration://artifact/audio-1",
    output_url: `/api/creation-tasks/${task.id}/output`, subtitle_url: null,
    message: "成片已就绪", created_at: task.created_at, updated_at: task.updated_at,
  });
  result.subtitle_tracks = [{
    id: "subtitle-track-zh", workspace_id: "local", task_id: task.id, timeline_version_id: "timeline-version-6",
    language: "zh-CN", source_language: null,
    style: {
      name: "default", font_family: "PingFang SC", font_size: 40,
      primary_color: "#FFFFFF", emphasis_color: "#F0B35F", position: "bottom",
      bilingual_order: "source_then_translation",
    },
    cues: [{
      id: "subtitle-cue-1", source_cue_id: null, text: "真实运行不是口号",
      start_seconds: 0, end_seconds: 3.2, speaker: "主讲人", line_breaks: [], emphasis: [],
    }],
    generation_method: "timeline", model_id: null, status: "valid",
    artifact_ref: "artifact:subtitle:zh:v6", transform_id: "transform:subtitle:v6",
    failure_reason: null, impact: null, repair_action: null,
    created_at: task.created_at, updated_at: task.updated_at,
  }];
  return result;
}

function exactPreview(task: CreationTask, operationType: "EditAudioTimeline" | "EditCompoundTimeline" | "EditGraphicOverlay" | "SetClipTransition" | "ChangeCanvas" | "SplitSubtitleCue" | "MergeSubtitleCue" | "DeleteSubtitleCue" | "ReplaceSubtitleText" | "UpdateSubtitleCue" | "ConfigureSubtitleTrack" | "GenerateCoverFromFrame" | "ReplaceClip" | "InsertClip" | "ChangeCrop" | "TrimClip" | "ReorderClip" | "SetClipLock" | "ConfirmClip") {
  return {
    intent: {
      id: "intent-exact", workspace_id: "local", task_id: task.id, raw_command: "精确控件修改",
      context_stage: "subtitle_cover", selected_object_refs: [], playhead_ms: null,
      intent_type: "subtitle_cover_edit", scope: {}, constraints: {}, confidence: 1,
      status: "compiled", created_at: task.created_at,
    },
    proposal: {
      id: `proposal-${operationType}`, workspace_id: "local", task_id: task.id, intent_id: "intent-exact",
      summary: "已生成待确认的字幕与封面修改。", target_refs: ["timeline:timeline-version-6"],
      operation: { operation_type: operationType, payload: {} },
      invalidated_refs: ["artifact:subtitle:v6", "artifact:preview:v6"],
      preview: {
        before: { ref: "timeline:timeline-version-6", label: "当前版本", detail: "已保存" },
        after: { ref: "timeline:proposal", label: "待确认版本", detail: "只更新所选对象" },
      },
      external_effects: ["未确认前不写入时间线。"], status: "proposed" as const,
      created_at: task.created_at, updated_at: task.updated_at,
    },
  };
}

function workspaceWithCover() {
  const initial = subtitleCoverWorkspace();
  initial.cover_asset = {
    id: "cover-1", workspace_id: "local", task_id: initial.task.id, timeline_version_id: "timeline-version-6",
    source_video_ref: "artifact:video:v6", frame_time_seconds: 2.4, title: "别只讲概念",
    safe_area: { top: 144, right: 72, bottom: 180, left: 72 },
    layout: { title_position: "bottom", text_align: "center" }, generation_method: "video_frame",
    model_or_engine: "ffmpeg", status: "valid", artifact_ref: "artifact:cover:v6",
    transform_id: "transform:cover:v6", failure_reason: null, impact: null, repair_action: null,
    created_at: initial.task.created_at, updated_at: initial.task.updated_at,
  };
  return initial;
}

afterEach(() => {
  vi.unstubAllGlobals();
  mocks.realMediaJobReads = false;
  mocks.withReferenceProvider = false;
  mocks.seedReference = null;
  mocks.references.clear();
  mocks.materialReference = null;
  mocks.realShell = false;
  mocks.showQuickInspector = false;
  mocks.assistantConversations.clear();
  mocks.currentAssistantConversation.clear();
  mocks.assistantSequence = 0;
  mocks.assistantActions = null;
  mocks.getWorkspace.mockReset();
  mocks.readTimelineRestore.mockReset().mockImplementation(async(taskId:string,requestId:string)=>({task_id:taskId,request_id:requestId,action:'undo',status:'applied',operation_id:'restored',result_version_id:'restored-version',failure:null}));
  mocks.previewEdit.mockReset();
  mocks.previewSelection.mockReset();
  vi.clearAllMocks();
  mocks.getTopics.mockReset();
  mocks.getQualityCheck.mockReset().mockResolvedValue({ check_state: "none", result_status: null,
    stale: false, timeline_version_id: null, findings: [], unavailable: [] });
  mocks.checkQuality.mockReset();
  mocks.previewExecution.mockResolvedValue(null);
  mocks.getRuntimeSettings.mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } });
  mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
  mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 });
  mocks.getAssistantHistory.mockResolvedValue({ items: [] });
  Reflect.deleteProperty(window, "electronAPI");
});

it("stops the old trim interval when a playing preview receives a new source URL", () => {
  const frames: FrameRequestCallback[] = [];
  const animationFrame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
    frames.push(callback);
    return frames.length;
  });
  const cancelFrame = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const props = { label: "裁剪画面", muted: false, pauseOthers: vi.fn() };
  const { rerender } = render(<PreviewVideo {...props} url="/source.mp4#t=0.500,2.500" />);
  const video = screen.getByLabelText("裁剪画面") as HTMLVideoElement;
  fireEvent.play(video);
  expect(frames).toHaveLength(1);
  rerender(<PreviewVideo {...props} url="/source.mp4#t=3.000,4.000" />);
  expect(screen.getByLabelText("裁剪画面")).toBe(video);
  expect(cancelFrame).toHaveBeenCalledWith(1);
  expect(pause).toHaveBeenCalled();
  expect(video.currentTime).toBe(3);
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(3);
  pause.mockRestore();
  cancelFrame.mockRestore();
  animationFrame.mockRestore();
});

describe("CreationTaskWorkspace", () => {
  async function readyInput(label: string) {
    const input = screen.getByLabelText(label);
    await waitFor(() => expect(input).toBeEnabled());
    return input;
  }

  it("keeps generated topic candidates in the current task after its chat refresh", async () => {
    const topicTask: CreationTask = {
      ...baseTask,
      stage: "topic",
      stage_status: "waiting_for_user",
      status: "waiting_for_user",
      progress: 5,
      recovery_point: "",
      confirmed_script_id: null,
      stage_reason: null,
      stage_impact: null,
      stage_repair_action: null,
    };
    const initial = workspace(topicTask);
    const topics: CreationTaskTopicsResponse = {
      task: topicTask,
      candidates: [{ id: "topic-chat-1", run_id: "run-chat-1", title: "从三个场景讲清一个误区",
        angle: "生活中的常见误区", audience: "普通用户", outline: ["现象", "原因", "做法"],
        score: 91, selection_status: "unselected", created_at: topicTask.created_at }],
      evidence_references: [], selected_topic_id: null, latest_run: null,
      route_preflight: { provider_kind: "minimax", execution_location: "cloud", model_id: "MiniMax-M3",
        endpoint_identity: "ep", connection_version: "1", availability: "succeeded", checked_at: topicTask.created_at },
      egress_preflight: { cloud_egress: true, source_count: 1, text_excerpt_count: 1,
        reason: null, impact: null, repair_action: null },
    };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getTopics.mockResolvedValue(topics);
    mocks.askAssistant.mockResolvedValue({ reply: "选题候选已生成，请检查并明确选择。",
      provider: "minimax", model: "test", edit_preview: null, script_draft: null,
      execution_action: null, execution_status: null, topic_candidates: topics });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.change(await readyInput("任务对话要求"), {
      target: { value: "请结合当前已确认的目标生成几个选题方向" },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith(topicTask.id,
      expect.objectContaining({ context_stage: "topic" }), expect.any(Function)));
    expect(await screen.findByText("从三个场景讲清一个误区")).toBeInTheDocument();
    expect(mocks.getTopics).toHaveBeenCalledWith(topicTask.id);
  });

  it("shows task script dialogue and refreshes the saved draft after the assistant reply", async () => {
    const task = { ...baseTask, stage: "script" as const, stage_status: "not_started" as const,
      status: "running" as const, confirmed_script_id: null };
    const initial = workspace(task);
    const draft = {
      id: "draft-1", task_id: task.id, run_id: "run-1", topic_id: "topic-1",
      title: "待确认文案", language: "zh-CN" as const, selection_status: "draft" as const,
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开头", content: "真实草稿正文。",
        visual_prompt: "现场画面", evidence_reference_ids: ["knowledge:one:v1"] }],
    };
    mocks.getWorkspace.mockResolvedValue({ ...initial, scripts: [draft], task: {
      ...task, stage_status: "waiting_for_user", status: "waiting_for_user",
    } });
    mocks.askAssistant.mockResolvedValue({ reply: "草稿已保存，请检查。", provider: "minimax",
      model: "test", edit_preview: null, script_draft: draft });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByRole("region", { name: /^助手对话：/ })).toBeInTheDocument();
    fireEvent.change(await readyInput("文案要求"), { target: { value: "写一个简短开头" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "写一个简短开头", context_stage: "script",
    }), expect.any(Function)));
    expect(await screen.findByText("真实草稿正文。", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("草稿已保存，请检查。")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "确认文案" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "新对话" })).toBeEnabled();
    expect(screen.queryByText(/^处理中 · 版本/)).not.toBeInTheDocument();
  });

  it.each(["assistant", "script"])("does not let assistant A's late refresh/finally release the newer %s owner", async action => {
    const task = { ...baseTask, stage: "script" as const, confirmed_script_id: null };
    const initial = workspace(task);
    const draft = { id: "late-A", task_id: task.id, run_id: "run-A", topic_id: "topic-1",
      title: "旧A稿", language: "zh-CN" as const, selection_status: "draft" as const,
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-A", title: "旧A段落", content: "旧A回读正文", visual_prompt: "画面",
        evidence_reference_ids: ["knowledge:one:v1"] }] };
    let finishReadA!: (value: WorkspaceState) => void;
    mocks.getWorkspace.mockResolvedValueOnce(initial)
      .mockReturnValueOnce(new Promise(resolve => { finishReadA = resolve; })).mockResolvedValue(initial);
    let finishB!: () => void;
    mocks.askAssistant.mockResolvedValueOnce({ reply: "A已完成", provider: "test", model: "test",
      edit_preview: null, script_draft: draft });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledTimes(1));
    fireEvent.change(await readyInput("文案要求"), { target: { value: "A要求" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledTimes(2));
    await act(async () => {
      await mocks.assistantActions!.assistantState.createNew();
      mocks.assistantActions!.onConversationChange();
    });
    if (action === "assistant") {
      mocks.askAssistant.mockReturnValueOnce(new Promise(resolve => { finishB = () => resolve({ reply: "B完成", provider: "test", model: "test", edit_preview: null, script_draft: null }); }));
      fireEvent.change(await readyInput("文案要求"), { target: { value: "B要求" } });
      fireEvent.click(screen.getByRole("button", { name: "发送" }));
      await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledTimes(2));
    } else {
      vi.mocked(generateCreationTaskScript).mockReturnValueOnce(new Promise(resolve => { finishB = () => resolve({ task, scripts: [], latest_run: null, route_preflight: { provider_kind:"minimax", execution_location:"cloud", model_id:"MiniMax-M3", endpoint_identity:"ep", connection_version:"1", availability:"succeeded", checked_at:task.created_at } }); }));
      fireEvent.click(screen.getByRole("button", { name: "生成文案" }));
      await waitFor(() => expect(generateCreationTaskScript).toHaveBeenCalledTimes(1));
    }
    await act(async () => finishReadA({ ...initial, scripts: [draft] }));
    expect(screen.queryByText("旧A回读正文", { exact: false })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新对话" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /生成文案/ })).toBeDisabled();
    await act(async () => finishB());
    await waitFor(() => expect(screen.getByRole("button", { name: "新对话" })).toBeEnabled());
  });

  it("re-reads a cached failed topic entry and opens the actual current script for confirmation", async () => {
    const task = { ...baseTask, stage: "topic" as const, status: "failed" as const,
      stage_status: "failed" as const, stage_reason: "provider_output_invalid", confirmed_script_id: null };
    const initial = workspace(task);
    const draft = { id: "current-script", task_id: task.id, run_id: "run", topic_id: "topic",
      title: "当前已保存文案", language: "zh-CN" as const, selection_status: "draft" as const,
      created_at: task.created_at, updated_at: "2026-10-10T04:26:40Z", confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section", title: "当前段落", content: "当前API真实文案", visual_prompt: "当前画面",
        evidence_reference_ids: ["knowledge:one:v1"] }] };
    mocks.getWorkspace.mockResolvedValue({ ...initial, task: { ...task, stage: "script",
      stage_status: "waiting_for_user", status: "waiting_for_user", stage_reason: null,
      updated_at: draft.updated_at }, scripts: [draft] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText("当前API真实文案", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认文案" })).toBeEnabled();
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(1);
  });

  it("does not replace a new local script edit with a late entry workspace read", async () => {
    const task = { ...baseTask, stage: "script" as const, confirmed_script_id: null };
    const initial = workspace(task);
    const draft = { id: "local-script", task_id: task.id, run_id: "run", topic_id: "topic",
      title: "已保存稿", language: "zh-CN" as const, selection_status: "draft" as const,
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section", title: "段落", content: "原文", visual_prompt: "原画面",
        evidence_reference_ids: ["knowledge:one:v1"] }] };
    initial.scripts = [draft];
    let finish!: (value: WorkspaceState) => void;
    mocks.getWorkspace.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段画面建议" }), { target: { value: "新的未保存画面" } });
    await act(async () => finish({ ...initial, task: { ...task, updated_at: "2026-10-10T04:28:00Z" },
      scripts: [{ ...draft, updated_at: "2026-10-10T04:28:00Z", sections: [{ ...draft.sections[0], visual_prompt: "迟到API画面" }] }] }));
    expect(screen.getByRole("textbox", { name: "第 1 段画面建议" })).toHaveValue("新的未保存画面");
  });

  it.each([false, true])("preserves immediate clip selection and playhead, applying latest entry facts only without an unsaved clip draft (%s)", async dirty => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, current_timeline_version_id: "timeline-version-59", timeline_sequence: 59 };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll" as const,
      locked: false, manually_selected: false,
    };
    initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
    const latest = { ...initial, task: { ...initial.task, current_timeline_version_id: "timeline-version-63",
      timeline_sequence: 63, updated_at: "2026-10-10T04:28:00Z" }, montage: { ...initial.montage!,
        clips: [clip, { ...initial.montage!.clips[1], crop_x: .4 }] } };
    let finish!: (value: WorkspaceState) => void;
    mocks.getWorkspace.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const montageTab = getWorkspaceViewControl(/^视频剪辑：/);
    fireEvent.pointerDown(montageTab);
    fireEvent.click(montageTab);
    fireEvent.pointerDown(screen.getByRole("button", { name: "选择镜头 2" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    player.currentTime = 4;
    fireEvent.pause(player);
    if (dirty) fireEvent.change(screen.getByLabelText("裁切水平位置"), { target: { value: "0.75" } });
    await act(async () => finish(latest));
    expect(document.querySelector("[data-timeline-version]")).toHaveAttribute("data-timeline-version", dirty ? "59" : "63");
    expect(montageTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "选择镜头 2" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("裁切水平位置")).toHaveValue(dirty ? "0.75" : "0.4");
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(4);
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(1);
  });

  it("defers an entry read during an assistant action and reads current facts once after it settles", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, current_timeline_version_id: "timeline-version-59", timeline_sequence: 59 };
    const latest = { ...initial, task: { ...initial.task, current_timeline_version_id: "timeline-version-63",
      timeline_sequence: 63, updated_at: "2026-10-10T04:28:00Z" } };
    let finishEntry!: (value: WorkspaceState) => void;
    let finishAssistant!: (value: unknown) => void;
    mocks.getWorkspace.mockReturnValueOnce(new Promise(resolve => { finishEntry = resolve; })).mockResolvedValue(latest);
    mocks.askAssistant.mockReturnValueOnce(new Promise(resolve => { finishAssistant = resolve; }));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "说明当前任务状态" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledTimes(1));
    await act(async () => finishEntry(latest));
    expect(document.querySelector("[data-timeline-version]")).toHaveAttribute("data-timeline-version", "59");
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(1);
    await act(async () => finishAssistant({ reply: "状态说明", provider: "test", model: "test", edit_preview: null, script_draft: null }));
    await waitFor(() => expect(document.querySelector("[data-timeline-version]")).toHaveAttribute("data-timeline-version", "63"));
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(2);
  });

  it.each([false, true])("keeps a freshly received proposal across deferred entry refresh only for its unchanged base (%s)", async changed => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, current_timeline_version_id: "timeline-version-59", timeline_sequence: 59 };
    const latest = { ...initial, task: { ...initial.task,
      current_timeline_version_id: changed ? "timeline-version-63" : "timeline-version-59",
      timeline_sequence: changed ? 63 : 59, updated_at: "2026-10-10T04:28:00Z" } };
    const proposal = exactPreview(initial.task, "ReplaceClip");
    Object.assign(proposal.proposal.operation.payload, { base_version_id: "timeline-version-59" });
    let finishEntry!: (value: WorkspaceState) => void;
    let finishAssistant!: (value: unknown) => void;
    mocks.getWorkspace.mockReturnValueOnce(new Promise(resolve => { finishEntry = resolve; })).mockResolvedValue(latest);
    mocks.askAssistant.mockReturnValueOnce(new Promise(resolve => { finishAssistant = resolve; }));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "换一个镜头，只生成修改方案" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledTimes(1));
    await act(async () => finishEntry(latest));
    await act(async () => finishAssistant({ reply: "待确认", provider: "test", model: "test", edit_preview: proposal, script_draft: null }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(Boolean(screen.queryByRole("dialog", { name: "修改提案" }))).toBe(!changed));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("does not apply the old task entry read after navigating into another task", async () => {
    const initialA = workspace({ ...baseTask, id: "task-A", stage: "script", confirmed_script_id: null });
    const initialB = workspace({ ...baseTask, id: "task-B", name: "当前B任务", stage: "script", confirmed_script_id: null });
    let finishA!: (value: WorkspaceState) => void;
    mocks.getWorkspace.mockReturnValueOnce(new Promise(resolve => { finishA = resolve; })).mockResolvedValue(initialB);
    const view = renderTaskSubject(<CreationTaskWorkspace key="A" initialTasks={[]} initialTopics={null} initialWorkspace={initialA} />);
    view.rerender(<CreationTaskWorkspace key="B" initialTasks={[]} initialTopics={null} initialWorkspace={initialB} />);
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-B"));
    await act(async () => finishA({ ...initialA, task: { ...initialA.task, name: "迟到A任务", updated_at: "2026-10-10T04:28:00Z" } }));
    expect(screen.getByRole("heading", { name: "当前B任务" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "迟到A任务" })).not.toBeInTheDocument();
  });

  it("preserves explicit current-source choices when confirming a reply-origin draft", async () => {
    const task = { ...baseTask, stage: "script" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, confirmed_script_id: null };
    const draft: WorkspaceState["scripts"][number] = {
      id: "reply-draft", task_id: task.id, run_id: "reply-run", topic_id: "topic-1",
      title: "回复草稿", language: "zh-CN", selection_status: "draft",
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      source_reply_reference: { conversation_id: "conversation-id", turn_id: "turn-id", response_digest: "a".repeat(64) },
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "reply-section", title: "回复", content: "这是来自对话的正文。",
        visual_prompt: "", evidence_reference_ids: [] }],
    };
    const initial = workspace(task);
    initial.scripts = [draft];
    const source = { reference_id: "knowledge:current:v3", title: "当前知识资料", text: "仅供当前任务核对。" };
    mocks.getAssistantScriptContext.mockResolvedValue({ reply_origin: null, sources: [source] });
    const updated = { ...draft, updated_at: "2026-10-09T00:00:01Z", sections: [{
      ...draft.sections[0], visual_prompt: "展示资料中的实际功能。", evidence_reference_ids: [source.reference_id],
    }] };
    mocks.saveScriptDraft.mockResolvedValue(updated);
    const confirmedTask = { ...task, stage: "narration" as const, stage_status: "not_started" as const,
      status: "running" as const, confirmed_script_id: draft.id };
    const confirmedWorkspace = workspace(confirmedTask);
    confirmedWorkspace.scripts = [{ ...updated, selection_status: "confirmed", confirmed_at: updated.updated_at }];
    mocks.confirmScript.mockResolvedValue({});
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(confirmedWorkspace);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByText(/当前内容是待确认草稿/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认文案" })).toBeDisabled();
    fireEvent.click(await screen.findByRole("checkbox", { name: "当前知识资料" }));
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段画面建议" }), {
      target: { value: "展示资料中的实际功能。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mocks.saveScriptDraft).toHaveBeenCalledWith(task.id, expect.objectContaining({
      expected_updated_at: task.updated_at,
      sections: [expect.objectContaining({ visual_prompt: "展示资料中的实际功能。", evidence_reference_ids: [source.reference_id] })],
    })));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认文案" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认文案" }));
    await waitFor(() => expect(mocks.confirmScript).toHaveBeenCalledWith(task.id, draft.id, expect.objectContaining({
      expected_updated_at: updated.updated_at,
      sections: [expect.objectContaining({ visual_prompt: "展示资料中的实际功能。", evidence_reference_ids: [source.reference_id] })],
    })));
  });

  it.each([false, true])("submits empty-reference content for server review and respects its rejection (%s)", async rejected => {
    const task = { ...baseTask, stage: "script" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, confirmed_script_id: null };
    const draft: WorkspaceState["scripts"][number] = {
      id: "empty-reference-draft", task_id: task.id, run_id: "run-1", topic_id: "topic-1",
      title: "开场", language: "zh-CN", selection_status: "draft",
      created_at: task.created_at, updated_at: task.updated_at, confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开场", content: "你想从哪里开始？",
        visual_prompt: "淡入标题。", evidence_reference_ids: [] }],
    };
    const initial = { ...workspace(task), scripts: [draft] };
    mocks.getAssistantScriptContext.mockResolvedValue({ reply_origin: null, sources: [] });
    mocks.getWorkspace.mockResolvedValue(initial);
    if (rejected) mocks.confirmScript.mockRejectedValue(new mocks.MockCreationPlanApiError(
      422, "unsupported_fact", "正文包含尚无依据的事实，请修订。"));
    else mocks.confirmScript.mockResolvedValue({});
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await screen.findByText("当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。");
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段正文" }), {
      target: { value: rejected ? "这是全国第一。" : "一起开始吧。" },
    });
    expect(screen.getByRole("button", { name: "确认文案" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "确认文案" }));
    await waitFor(() => expect(mocks.confirmScript).toHaveBeenCalledWith(task.id, draft.id, {
      title: draft.title, expected_updated_at: draft.updated_at, edit_mode: "replace_all",
      sections: [{ ...draft.sections[0], content: rejected ? "这是全国第一。" : "一起开始吧。" }],
    }));
    if (rejected) {
      expect(await screen.findByText("正文包含尚无依据的事实，请修订。")).toBeInTheDocument();
      expect(screen.queryByText("文案已确认，可以生成配音。")).not.toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "第 1 段正文" })).toHaveValue("这是全国第一。");
    } else expect(await screen.findByText("文案已确认，可以生成配音。")).toBeInTheDocument();
  });

  it("keeps body and visual requirements when references are empty", async () => {
    const script: WorkspaceState["scripts"][number] = {
      id: "empty-reference-fields", task_id: "task-1", run_id: "run", topic_id: "topic", title: "开场",
      language: "zh-CN", selection_status: "draft", created_at: baseTask.created_at,
      updated_at: baseTask.updated_at, confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开场", content: "一起开始吧。",
        visual_prompt: "淡入标题。", evidence_reference_ids: [] }],
    };
    const confirm = vi.fn();
    render(<ScriptEditor taskId="task-1" script={script} busy={false} repairing={false} onConfirm={confirm} />);
    await screen.findByText("当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。");
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段正文" }), { target: { value: " " } });
    expect(screen.getByRole("button", { name: "确认文案" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段正文" }), { target: { value: "一起开始吧。" } });
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段画面建议" }), { target: { value: " " } });
    expect(screen.getByRole("button", { name: "确认文案" })).toBeDisabled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("keeps edits made during draft save and uses the returned version for the next save", async () => {
    const task = { ...baseTask, stage: "script" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, confirmed_script_id: null };
    const draft: WorkspaceState["scripts"][number] = {
      id: "late-edit-draft", task_id: task.id, run_id: "run-1", topic_id: "topic-1",
      title: "待确认文案", language: "zh-CN", selection_status: "draft",
      created_at: task.created_at, updated_at: "v1", confirmed_at: null,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开头", content: "提交时正文",
        visual_prompt: "提交时画面", evidence_reference_ids: ["source:one"] }],
    };
    const initial = workspace(task);
    initial.scripts = [draft];
    mocks.getAssistantScriptContext.mockResolvedValue({ reply_origin: null, sources: [
      { reference_id: "source:one", title: "资料一", text: "第一份资料" },
      { reference_id: "source:two", title: "资料二", text: "第二份资料" },
    ] });
    let resolveSave!: (saved: typeof draft) => void;
    mocks.saveScriptDraft.mockReturnValueOnce(new Promise((resolve) => { resolveSave = resolve; }));

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const content = await screen.findByRole("textbox", { name: "第 1 段正文" });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mocks.saveScriptDraft).toHaveBeenCalledWith(task.id, expect.objectContaining({
      expected_updated_at: "v1",
      sections: [expect.objectContaining({ content: "提交时正文", visual_prompt: "提交时画面", evidence_reference_ids: ["source:one"] })],
    })));

    fireEvent.change(content, { target: { value: "保存期间新正文" } });
    fireEvent.change(screen.getByRole("textbox", { name: "第 1 段画面建议" }), { target: { value: "保存期间新画面" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "资料二" }));
    const saved = { ...draft, updated_at: "v2" };
    await act(async () => resolveSave(saved));

    expect(screen.getByRole("textbox", { name: "第 1 段正文" })).toHaveValue("保存期间新正文");
    expect(screen.getByRole("textbox", { name: "第 1 段画面建议" })).toHaveValue("保存期间新画面");
    expect(screen.getByRole("checkbox", { name: "资料一" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "资料二" })).toBeChecked();

    mocks.saveScriptDraft.mockResolvedValueOnce({ ...draft, updated_at: "v3" });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mocks.saveScriptDraft).toHaveBeenLastCalledWith(task.id, expect.objectContaining({
      expected_updated_at: "v2",
      sections: [expect.objectContaining({ content: "保存期间新正文", visual_prompt: "保存期间新画面", evidence_reference_ids: ["source:one", "source:two"] })],
    })));
  });

  it("streams the real task action, keeps Space/Shift+Enter/IME Enter in the input, then restores focus", async () => {
    const initial = subtitleCoverWorkspace();
    const queued = queueStatus("queued");
    let resolveTurn!: (value: unknown) => void;
    const result = new Promise(resolve => { resolveTurn = resolve; });
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(queued);
    mocks.askAssistant.mockImplementation((_taskId: string, _input: unknown, onEvent: (event: unknown) => void) => {
      onEvent({ type: "text_delta", delta: "我按已确认文案提交制作。" });
      onEvent({ type: "tool_start", tool_id: 1, label: "提交连续制作" });
      onEvent({ type: "tool_end", tool_id: 1, label: "提交连续制作", ok: true, summary: "制作请求已返回，当前作业已排队。" });
      return result;
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const input = await readyInput("局部修改要求");
    fireEvent.change(input, { target: { value: "就按已确认的文案开始制作并回报当前作业状态 " } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.keyDown(input, { key: " ", code: "Space" });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    fireEvent.compositionEnd(input);
    expect(mocks.askAssistant).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "就按已确认的文案开始制作并回报当前作业状态" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("我按已确认文案提交制作。", { exact: false })).toBeInTheDocument();
    expect(mocks.askAssistant).toHaveBeenCalledWith(initial.task.id, expect.objectContaining({
      message: "就按已确认的文案开始制作并回报当前作业状态", context_stage: "subtitle_cover",
      selected_object_refs: [], playhead_ms: null,
    }), expect.any(Function));
    await act(async () => resolveTurn({ reply: "已提交并排队，尚未完成。", provider: "minimax", model: "test",
      edit_preview: null, script_draft: null, execution_action: "start", execution_status: queued,
      topic_candidates: null }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith(initial.task.id,
      expect.objectContaining({ message: "就按已确认的文案开始制作并回报当前作业状态", context_stage: "subtitle_cover" }), expect.any(Function)));
    expect(await screen.findByText(/连续制作：已排队/)).toBeInTheDocument();
    expect(screen.getByText("提交连续制作 · 已返回")).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it("restores task dialogue from persisted history after reopening", async () => {
    const initial = workspace({ ...baseTask, stage: "script", confirmed_script_id: null });
    mocks.getAssistantHistory.mockResolvedValue({ items: [
      { role: "user", message: "把开头缩短", stage: "script", script_id: null, at: baseTask.created_at },
      { role: "assistant", message: "已改好，请检查。", stage: "script", script_id: "draft-1", at: baseTask.updated_at },
    ] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(await screen.findByRole("button", { name: "历史对话" }));
    fireEvent.click(await screen.findByText("旧版只读历史"));
    expect(await screen.findByText("已改好，请检查。")).toBeInTheDocument();
    expect(mocks.getAssistantHistory).toHaveBeenCalledWith("task-1");
  });

  it("shows a saved question whose model reply was interrupted", async () => {
    const initial = workspace({ ...baseTask, stage: "script", confirmed_script_id: null });
    mocks.getAssistantHistory.mockResolvedValue({ items: [
      { role: "user", message: "把开头缩短", stage: "script", script_id: null, at: baseTask.created_at },
    ] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(await screen.findByRole("button", { name: "历史对话" }));
    fireEvent.click(await screen.findByText("旧版只读历史"));
    expect(await screen.findByText("上次没有得到完整回复。")).toBeInTheDocument();
    expect(screen.getByText("把开头缩短")).toBeInTheDocument();
  });

  it("starts only after showing the confirmed script and making requirements", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running" });
    const preview = {
      task_id: "task-1", script_id: "script-1", script_title: "真实文案",
      script_sections: [{ title: "开场", content: "这段就是实际要读的内容" }],
      plan_id: "plan-1", input_digest: "a".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
        can_continue: true, continuation_blocker: null, template_settings: {} },
    };
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    const queuedExecution = { task_id: "task-1", job: {
      id: "job-1", kind: "creation_task_execution", status: "queued", progress: 0,
      failure_reason: null, impact: null, repair_action: null,
    }, stages: [], current_stage: null, waiting_reason: "设备资源正在处理其他工作。",
    status_updated_at: "2026-09-27T11:00:00+00:00",
    video_ready: false, cover_status: "pending", cover_message: null };
    mocks.startExecution.mockResolvedValue(queuedExecution);
    mocks.getExecution.mockResolvedValueOnce({ task_id: "task-1", job: null, stages: [],
      video_ready: false, cover_status: "pending", cover_message: null }).mockResolvedValue(queuedExecution);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await productionPanel();
    await waitFor(() => expect(screen.getByText(/这段就是实际要读的内容/)).toBeInTheDocument());
    expect(screen.getByText(/露脸目标 20%/)).toBeInTheDocument();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认以上文案和要求，开始制作" }));
    await waitFor(() => expect(mocks.startExecution).toHaveBeenCalledWith("task-1", preview));
    expect(await screen.findByLabelText("制作安排状态")).toHaveTextContent("设备资源正在处理其他工作。");
    expect(mocks.generateNarration).not.toHaveBeenCalled();
  });

  it("keeps the editor player and draft mounted while reviewing production details without starting", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task.stage = "montage";
    initial.montage!.clips = [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
      needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false }];
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(queueStatus(null));
    mocks.previewExecution.mockResolvedValue(queuePreview());
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const entry = await screen.findByRole("button", { name: /查看制作详情|核对制作要求/ });
    expect(screen.queryByRole("region", { name: "连续制作" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认以上文案和要求，开始制作" })).not.toBeInTheDocument();
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    player.currentTime = 1.25;
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "未发送的剪辑草稿" } });
    fireEvent.click(entry);
    const panel = await productionPanel();
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).toHaveTextContent("画幅 9:16 · 目标时长 30 秒 · 露脸目标 20%");
    expect(within(panel).getByRole("button", { name: "确认以上文案和要求，开始制作" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "收起制作详情" }));
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(player.currentTime).toBe(1.25);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("未发送的剪辑草稿");
    await expectTaskVersion(/版本 6/);
    fireEvent.click(entry);
    expect(screen.getByRole("region", { name: "连续制作" })).toBe(panel);
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it.each([
    ["narration", "queued"], ["storyboard", "running"], ["video", "queued"],
  ] as const)("uses the same confirmed queue from %s and shows %s without claiming an artifact", async (phase, status) => {
    const task = { ...baseTask, stage: phase === "narration" ? "narration" as const : "montage" as const,
      stage_status: "not_started" as const, status: "running" as const, stage_reason: null, stage_impact: null };
    const initial = phase === "video" ? subtitleCoverWorkspace() : workspace(task);
    initial.task = task;
    if (initial.montage) initial.montage = { ...initial.montage, status: "ready", output_url: null };
    const preview = queuePreview();
    const execution = queueStatus(status);
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValueOnce(queueStatus(null)).mockResolvedValue(execution);
    mocks.startExecution.mockResolvedValue(execution);
    mocks.cancelExecution.mockResolvedValue(queueStatus("cancelled"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await productionPanel();
    const panel = await productionPanel();
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(within(panel).getByRole("region", { name:"这次制作要求" })).toHaveTextContent("目标时长");
    expect(panel).toHaveTextContent("露脸目标 20%");
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /^(单步)?生成(配音|故事板|成片)$/ })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，开始制作" }));
    await waitFor(() => expect(mocks.startExecution).toHaveBeenCalledWith("task-1", preview));
    expect(await within(panel).findByText(status === "queued" ? "已排队" : "后台制作中")).toBeInTheDocument();
    expect(screen.getByLabelText("制作安排状态")).toHaveTextContent(status === "queued" ? "设备资源正在处理其他工作。" : "安排镜头与人物口型");
    expect(panel).not.toHaveTextContent("成片已生成");
    expect(panel).not.toHaveTextContent("真实配音已生成");
    if (phase === "video") {
      expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
      fireEvent.click(within(panel).getByRole("button", { name: "停止制作" }));
      await waitFor(() => expect(mocks.cancelExecution).toHaveBeenCalledWith("task-1"));
    }
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("confirms the script and requirements again before resuming a failed queue in montage", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, stage: "montage", status: "failed", stage_status: "failed" };
    const failed = queueStatus("failed");
    failed.job!.failure_reason = "输出磁盘写入失败";
    failed.job!.repair_action = "检查输出目录的可用空间后继续制作。";
    failed.current_stage = "person_render";
    failed.stages = [{ stage: "narration", status: "completed", at: "2026-10-07T00:00:00Z" },
      { stage: "montage_plan", status: "reused", at: "2026-10-07T00:00:01Z" },
      { stage: "person_render", status: "completed", at: "2026-10-07T00:00:02Z" }];
    const recordingPreview: CreationExecutionPreview = {
      ...queuePreview(),
      narration_source: { source_kind: "recorded", source_artifact_ref: "artifact:task-1/recording-1", duration_seconds: 4.2 },
      requirements: { ...queuePreview().requirements, presenter_share_target: 0, selected_voice: null,
        can_continue: true, continuation_blocker: null },
    };
    mocks.previewExecution.mockResolvedValue(recordingPreview);
    mocks.getExecution.mockResolvedValue(failed);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.resumeExecution.mockResolvedValue(queueStatus("queued"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await screen.findByRole("region", { name: "制作状态" })).toHaveTextContent("输出磁盘写入失败");
    expect(screen.getByRole("region", { name: "制作状态" })).toHaveTextContent("人物口型");
    fireEvent.click(screen.getByRole("button", { name: /查看制作详情|核对制作要求/ }));
    const panel = await productionPanel();
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).toHaveTextContent("输出磁盘写入失败");
    expect(panel).toHaveTextContent("检查输出目录的可用空间后继续制作。");
    expect(panel).toHaveTextContent("沿用有效结果");
    expect(panel).toHaveTextContent("人物口型：已完成");
    expect(panel).not.toHaveTextContent("person_render");
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，继续制作" }));
    await waitFor(() => expect(mocks.resumeExecution).toHaveBeenCalledWith("task-1"));
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("keeps the first preflight cause and repair visible without hiding the montage or offering a legacy fallback", async () => {
    const initial = subtitleCoverWorkspace();
    initial.task = { ...initial.task, stage: "montage", status: "failed", stage_status: "failed" };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(queueStatus("failed"));
    mocks.previewExecution.mockRejectedValue(new mocks.MockCreationPlanApiError(503,
      "device_budget_unavailable", "配音预算未标定", null, "先标定本机配音资源预算，再重试。"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const budgetAlert = await screen.findByRole("alert");
    expect(budgetAlert).toHaveTextContent("这台设备还没有完成制作资源标定，暂时不能开始制作。");
    expect(within(budgetAlert).getByText("配音预算未标定")).not.toBeVisible();
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /查看制作详情|核对制作要求/ }));
    expect(screen.getByRole("button", { name: "确认以上文案和要求，继续制作" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
    mocks.previewExecution.mockResolvedValue(queuePreview());
    fireEvent.click(screen.getByRole("button", { name: "重新核对文案和制作要求" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认以上文案和要求，继续制作" })).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
  });

  it.each([true, false])("shows an exact voice version name only when available (match=%s), with raw identity in diagnostics", async matches => {
    const initial = workspace(baseTask);
    const reference = { profile_id: "voice-raw-uuid", version: "version-raw-uuid",
      person_profile_id: "person", person_version: "v1", person_authorization_id: "auth", person_authorization_version: "v1",
      authorization_id: "voice-auth", authorization_version: "v1", language: "zh", model_id: "test" };
    initial.plan.selected_voice = reference;
    initial.available_voices = [{ profile_id: reference.profile_id, version: matches ? reference.version : "other-version",
      person_display_name: "准确声音名", language: "zh", model_id: "test", runnable: true, runnable_reason: null }];
    const preview = queuePreview();
    preview.requirements.selected_voice = reference.profile_id;
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getExecution.mockResolvedValue(queueStatus(null));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const panel = await productionPanel();
    expect(within(panel).getByText(`声音：${matches ? "准确声音名" : "已选择声音参考"}`, { exact: false })).toBeInTheDocument();
    const diagnostic = within(panel).getByText("声音引用诊断").closest("details")!;
    expect(diagnostic).not.toHaveAttribute("open");
    expect(diagnostic).toHaveTextContent(reference.profile_id);
    expect(diagnostic).toHaveTextContent(reference.version);
  });

  it("shows the real uncalibrated CPU budget failure as a short conclusion with folded diagnostics and no job", async () => {
    const initial = workspace({ ...baseTask, stage_status: "not_started", status: "created",
      stage_reason: null, stage_impact: null, stage_repair_action: null });
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue(queuePreview());
    mocks.getExecution.mockResolvedValue(queueStatus(null));
    mocks.startExecution.mockRejectedValue(new mocks.MockCreationPlanApiError(503, "device_budget_unavailable",
      "CPU 预算缺少 DEVICE_CPU_RENDER_RAM_MB / DEVICE_CPU_RENDER_CPU_THREADS", null, "请完成设备预算标定。"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(await productionAction("确认以上文案和要求，开始制作"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("这台设备还没有完成制作资源标定，暂时不能开始制作。");
    expect(within(alert).getByRole("link", { name: "检查运行情况" })).toHaveAttribute("href", "/health");
    const diagnostic = within(alert).getByText("制作资源诊断").closest("details")!;
    expect(diagnostic).not.toHaveAttribute("open");
    expect(diagnostic).toHaveTextContent("DEVICE_CPU_RENDER_RAM_MB");
    expect(screen.queryByText("后台制作中")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("制作安排状态")).not.toBeInTheDocument();
  });

  it("keeps start unavailable until an authorized voice is selected", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running" });
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue({
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案",
      script_sections: [{ title: "正文", content: "实际内容" }],
      plan_id: "plan-1", input_digest: "b".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null,
        can_continue: false, continuation_blocker: "请先选择已授权声音，或保存并明确采用本人录音；本人录音仅支持纯素材成片。",
        template_settings: {} },
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await productionPanel();
    expect(await screen.findByText("请先选择已授权声音，或保存并明确采用本人录音；本人录音仅支持纯素材成片。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "暂不能开始制作" })).toBeDisabled();
    expect(mocks.startExecution).not.toHaveBeenCalled();
  });

  it("enables pure-material continuation for a currently adopted recording and shows its true source", async () => {
    const initial = workspace({ ...baseTask, stage: "narration", stage_status: "not_started", status: "running",
      narration_cache_key: "recorded-narration" });
    initial.narration = {
      task: initial.task, script_id: "script-1", audio_reference: "narration://artifact/recorded-narration",
      duration_seconds: 4.2, sample_rate: 16000, model_id: null, voice_profile_id: null, source_kind: "recorded",
      source_artifact_ref: "artifact:task-1/recording-1", source_sha256: "a".repeat(64),
      source_script_digest: "b".repeat(64), language: "zh", pronunciation_version: "",
      segments: [{ id: "segment-1", source_section_id: "section-1", text: "本人录制的段落", audio_reference: "narration://artifact/recorded-narration",
        audio_url: null, duration_seconds: 4.2, sample_rate: 16000, model_id: null, voice_profile_id: null,
        voice_version: null, language: "zh", pronunciation_version: "", status: "ready", source_kind: "recorded",
        source_artifact_ref: "artifact:task-1/recording-1", source_sha256: "a".repeat(64), source_script_digest: "b".repeat(64) }],
    };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.previewExecution.mockResolvedValue({
      ...queuePreview(), narration_source: { source_kind: "recorded", source_artifact_ref: "artifact:task-1/recording-1", duration_seconds: 4.2 },
      requirements: { ...queuePreview().requirements, presenter_share_target: 0, selected_voice: null,
        can_continue: true, continuation_blocker: null },
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(await productionAction("确认以上文案和要求，开始制作")).toBeEnabled();
    expect(screen.getByText(/配音来源：本人录音 · 已采用 · 4\.2 秒/)).toBeInTheDocument();
    expect(screen.getByText("本人录音 · 已采用")).toBeInTheDocument();
    expect(screen.getByText("artifact:task-1/recording-1")).toBeInTheDocument();
    expect(screen.queryByText("未就绪")).not.toBeInTheDocument();
  });

  it.each(["local", "delete", "cross-object"])("keeps the real shell proposal %s modal and preserves its frozen reference and posted turn", async (scope) => {
    mocks.realShell = true;
    mocks.withReferenceProvider = true;
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    const preview = exactPreview(initial.task, "ReplaceClip");
    preview.proposal.target_refs = ["clip:clip-1"];
    if (scope === "delete") Object.assign(preview.proposal.operation, { operation_type: "DeleteClip", payload: { clip_id: "clip-1" } });
    if (scope === "cross-object") preview.proposal.target_refs.push("clip:clip-2");
    preview.intent.raw_command = "换成产品特写";
    mocks.askAssistant.mockResolvedValue({ reply: "已生成镜头替换预览，请确认。", provider: "test", model: "test", edit_preview: preview });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    const editInput = await readyInput("局部修改要求");
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    await waitFor(() => expect(mocks.saveReference).toHaveBeenLastCalledWith(expect.any(String), {
      kind: "selection", items: [expect.objectContaining({kind:"shot",object_id:"clip-1",task_id:"task-1",version:"timeline-version-6"})],
    }, 0));
    fireEvent.change(editInput, { target: { value: "换成产品特写" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.askAssistant).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "换成产品特写", selected_object_refs: [], expected_reference_version: 1,
      expected_timeline_version_id: "timeline-version-6",
    }), expect.any(Function)));
    const confirmation = await screen.findByRole("dialog", { name: "修改提案" });
    expect(confirmation).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(confirmation).toContainElement(document.activeElement as HTMLElement));
    expect(screen.getByLabelText("局部修改要求")).toBe(editInput);
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "修改提案" })).not.toBeInTheDocument());
    expect(screen.getByLabelText("局部修改要求")).toBe(editInput);
    expect(Array.from(mocks.assistantConversations.values()).flatMap(value=>value.turns)).toContainEqual(expect.objectContaining({user_message:"换成产品特写",status:"completed"}));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });
  it.each([
    ...["succeeded", "failed", "cancelled"].map(status => ["person", status]),
    ...["succeeded", "failed", "cancelled", "interrupted", "wrong-task", "wrong-job"].map(status => ["narration", status]),
  ])("keeps a queued %s edit unapplied until persisted job %s is read", async (kind, status) => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    const narration = kind === "narration";
    if (narration) initial.narration = {
      task: initial.task, script_id: "script-1", audio_reference: "narration://artifact/old-audio",
      duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh", pronunciation_version: "p1",
      segments: [{ id: "narration-segment-1", source_section_id: "section-1", text: "原配音正文",
        audio_reference: "narration://artifact/old-segment", audio_url: "/api/old-segment/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts", voice_profile_id: "voice-1",
        voice_version: "v1", language: "zh", pronunciation_version: "p1", status: "ready",
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null }],
    };
    const preview = exactPreview(initial.task, "ReplaceClip");
    Object.assign(preview.proposal.operation, { operation_type: narration ? "RegenerateNarrationSegment" : "ChangePersonSegment", payload: narration ? { narration_segment_id: "narration-segment-1" } : { clip_id: "clip-1" } });
    preview.proposal.target_refs = [narration ? "narration:narration-segment-1" : "clip:clip-1"];
    mocks.askAssistant.mockResolvedValue({ reply: narration ? "配音修改待确认。" : "人物修改待确认。", provider: "minimax", model: "MiniMax-M3", edit_preview: preview });
    mocks.getWorkspace.mockResolvedValue(initial);
    let submitted = false;
    let readTerminal: (value: unknown) => void = () => {};
    const terminal = new Promise(resolve => { readTerminal = resolve; });
    mocks.getJobProgress.mockImplementation(() => submitted ? terminal : Promise.resolve({ task_id: "task-1", current: null, items: [], total_count: 0 }));
    mocks.confirmEdit.mockImplementation(async () => {
      submitted = true;
      return { status: "queued", job_id: `${kind}-edit-job`, request_id: "device-request" };
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    if (narration) {
      fireEvent.click(getWorkspaceViewControl(/^配音：/));
      fireEvent.click(screen.getByRole("button", { name: "选择配音 1" }));
    } else {
      fireEvent.click(getWorkspaceViewControl(/^检查与输出：/));
      fireEvent.click(screen.getByRole("button", { name: "调整成片" }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
      fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    }
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: narration ? "把这一段配音读得慢一点" : "这一段换成当前人物出镜" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改提案" });
    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));
    expect(await screen.findByText(narration ? "配音修改已排队，取得设备许可并完成后才更新目标配音段；当前版本和音频保持不变。" : "人物修改已排队，取得设备许可并完成后才更新目标镜头；当前版本保持不变。")).toBeInTheDocument();
    expect(screen.queryByText(/(?:人物|配音)修改已完成/)).not.toBeInTheDocument();
    await expectTaskVersion(/版本 6$/);
    const refreshCount = mocks.getWorkspace.mock.calls.length;
    const finalState = status === "succeeded" ? { ...initial, task: { ...initial.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 } } : initial;
    mocks.getWorkspace.mockResolvedValue(finalState);
    if (narration && status === "succeeded") {
      finalState.narration = { ...initial.narration!, audio_reference: "narration://artifact/new-audio",
        segments: [{ ...initial.narration!.segments[0], audio_reference: "narration://artifact/new-segment", audio_url: "/api/new-segment/audio" }] };
      finalState.montage = { ...initial.montage!, output_url: null };
    }
    const job = { id: status === "wrong-job" ? "other-edit-job" : `${kind}-edit-job`, kind: narration ? "creation_task_narration_edit" : "creation_task_person_edit", status: status.startsWith("wrong-") ? "succeeded" : status,
      progress: status === "succeeded" ? 100 : 0, attempt: 1, started_at: initial.task.created_at,
      finished_at: initial.task.updated_at, updated_at: initial.task.updated_at, elapsed_seconds: 1,
      failure_reason: status === "succeeded" ? null : narration ? "配音来源已失效" : "人物授权已失效", impact: "旧版本保持不变",
      repair_action: "修复授权后重新预览", wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null };
    await act(async () => { readTerminal({ task_id: status === "wrong-task" ? "other-task" : "task-1", current: job, items: [job], total_count: 1 }); });
    if (status === "interrupted" || status.startsWith("wrong-")) {
      expect(mocks.getWorkspace.mock.calls.length).toBe(refreshCount);
      await expectTaskVersion(/版本 6$/);
      if (status === "interrupted") {fireEvent.click(screen.getByRole("button",{name:"运行详情"}));expect(await screen.findByRole("dialog",{name:"运行详情"})).toHaveTextContent("已中断");fireEvent.click(screen.getByRole("button",{name:"关闭卡片"}));}
      expect(screen.queryByText(/配音修改已完成/)).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "选择配音 1" })).toHaveAttribute("aria-pressed", "true");
      expect(document.querySelector('audio[src="/api/old-segment/audio"]')).not.toBeNull();
      expect(mocks.generateNarration).not.toHaveBeenCalled();
      expect(mocks.generatePersonRender).not.toHaveBeenCalled();
      expect(mocks.renderMontage).not.toHaveBeenCalled();
      return;
    }
    await waitFor(() => expect(mocks.getWorkspace.mock.calls.length).toBeGreaterThan(refreshCount));
    await expectTaskVersion(status === "succeeded" ? /版本 7$/ : /版本 6$/);
    if (status === "succeeded") expect(await screen.findByText(narration ? "配音修改已完成，目标配音段和当前版本已更新；成片尚未重新生成。" : "人物修改已完成，目标镜头和当前版本已更新；成片尚未重新生成。")).toBeInTheDocument();
    else {
      expect(await screen.findByText(narration ? status === "cancelled" ? "配音修改已取消，当前版本和音频保持不变；请查看作业原因。" : "配音修改未完成，当前版本和音频保持不变；请查看作业原因。" : "人物修改未完成，当前版本保持不变；请查看作业原因。")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button",{name:"运行详情"}));expect(await screen.findByRole("dialog",{name:"运行详情"})).toHaveTextContent(narration ? "配音来源已失效" : "人物授权已失效");fireEvent.click(screen.getByRole("button",{name:"关闭卡片"}));
      expect(screen.queryByText(/(?:人物|配音)修改已完成/)).not.toBeInTheDocument();
    }
    if (narration) {
      fireEvent.click(screen.getByRole("button",{name:"运行详情"}));expect(await screen.findByRole("dialog",{name:"运行详情"})).toHaveTextContent("局部配音修改");fireEvent.click(screen.getByRole("button",{name:"关闭卡片"}));
      expect(screen.getByRole("button", { name: "选择配音 1" })).toHaveAttribute("aria-pressed", "true");
      expect(document.querySelector(`audio[src="/api/${status === "succeeded" ? "new" : "old"}-segment/audio"]`)).not.toBeNull();
    }
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.generatePersonRender).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });
  it("selects the playhead clip, keeps its position and discards a late preview for another clip", async () => {
    const initial = subtitleCoverWorkspace();
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll" as const,
      locked: false, manually_selected: false,
    };
    initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^检查与输出：/));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    player.currentTime = 3;
    fireEvent.seeked(player);
    expect(screen.getByLabelText("局部修改要求")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "调整成片" }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    const editor = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    fireEvent.loadedMetadata(editor);
    expect(editor.currentTime).toBe(3);
    expect(screen.getByRole("button", { name: "选择镜头 2" })).toHaveAttribute("aria-pressed", "true");
    let resolvePreview!: (value: ReturnType<typeof exactPreview>) => void;
    mocks.previewEdit.mockReturnValue(new Promise((resolve) => { resolvePreview = resolve; }));
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "换成另一个镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    await act(async () => resolvePreview(exactPreview(initial.task, "ReplaceClip")));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(editor.currentTime).toBe(1.5);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    editor.currentTime = 6;
    fireEvent.pause(editor);
    expect(screen.getByRole("button", { name: "选择镜头 2" })).toHaveAttribute("aria-pressed", "true");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("restores server fields and the saved authorization receipt after undo", async () => {
    const initial = workspaceWithCover();
    initial.subtitle_tracks[0].cues[0].emphasis = ["真实运行"];
    const restored = structuredClone(initial);
    restored.subtitle_tracks[0].cues[0].line_breaks = [4];
    restored.subtitle_tracks[0].cues[0].emphasis = ["不是口号"];
    restored.cover_asset!.title = "恢复后的标题";
    restored.cover_asset!.frame_time_seconds = 1.2;
    restored.cover_asset!.layout.title_position = "center";
    mocks.undoTimeline.mockResolvedValue({});
    mocks.readTimelineRestore.mockImplementationOnce(async(taskId:string,requestId:string)=>({task_id:taskId,request_id:requestId,
      action:'undo',status:'applied',operation_id:'restored',result_version_id:'restored-version',failure:null,
      execution_readiness:{status:'not_ready',reason_code:'task_authorization_expired',device_admission:'not_checked'}}));
    mocks.getWorkspace.mockResolvedValue(restored);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：已完成"));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存草稿" } });
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "未保存封面" } });
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行\n不是口号"));
    expect(screen.getByLabelText("字幕 1 强调词")).toHaveValue("不是口号");
    await waitFor(() => expect(screen.getByLabelText("封面标题")).toHaveValue("恢复后的标题"));
    expect(screen.getByLabelText("封面画面时间")).toHaveValue(1.2);
    expect(within(screen.getByRole("group", { name: "封面标题位置" })).getByRole("button", { name: "中部" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/本次保存时后台执行授权未就绪（原授权已过期）；修改已保留。/)).toBeVisible();
  });

  it.each(['not_submitted','unknown'] as const)('timeline restore %s preserves the correct retry boundary', async effect=>{
    const initial=workspaceWithCover();
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.undoTimeline.mockReset().mockRejectedValueOnce(new mocks.MockCreationPlanApiError(409,null,'原修改未完成',null,null,null,effect));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl('字幕与封面：已完成'));
    fireEvent.click(screen.getByRole('button',{name:'撤销'}));
    await screen.findByText('原修改未完成');
    if (effect==='not_submitted') {
      mocks.undoTimeline.mockResolvedValue({});
      fireEvent.click(screen.getByRole('button',{name:'撤销'}));
      await waitFor(()=>expect(mocks.undoTimeline).toHaveBeenCalledTimes(2));
      expect(mocks.undoTimeline.mock.calls[0][1].request_id).not.toBe(mocks.undoTimeline.mock.calls[1][1].request_id);
    } else {
      fireEvent.click(screen.getByRole('button',{name:'撤销'}));
      expect(mocks.undoTimeline).toHaveBeenCalledTimes(1);
      const requestId=mocks.undoTimeline.mock.calls[0][1].request_id;
      mocks.readTimelineRestore.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(404,null,'原回执未取得'));
      fireEvent.click(screen.getByRole('button',{name:'查询原撤销重做结果'}));
      await screen.findByText('原回执未取得');
      expect(mocks.readTimelineRestore).toHaveBeenCalledWith(initial.task.id,requestId);
      expect(screen.getByRole('button',{name:'查询原撤销重做结果'})).toBeInTheDocument();
      expect(mocks.undoTimeline).toHaveBeenCalledTimes(1);
    }
  });

  it.each(['not_submitted','rejected'] as const)('timeline readback %s keeps the written original UUID',async effect=>{
    const initial=workspaceWithCover();
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.undoTimeline.mockReset().mockResolvedValue({});
    mocks.readTimelineRestore.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(404,null,'读取原回执失败',null,null,null,effect));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl('字幕与封面：已完成'));
    fireEvent.click(screen.getByRole('button',{name:'撤销'}));
    await screen.findByText('读取原回执失败');
    await waitFor(()=>expect(screen.getByRole('button',{name:'查询原撤销重做结果'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button',{name:'撤销'}));
    expect(mocks.undoTimeline).toHaveBeenCalledTimes(1);
    const requestId=mocks.undoTimeline.mock.calls[0][1].request_id;
    expect(screen.getByRole('button',{name:'查询原撤销重做结果'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'查询原撤销重做结果'}));
    await waitFor(()=>expect(mocks.readTimelineRestore).toHaveBeenLastCalledWith(initial.task.id,requestId));
    expect(mocks.undoTimeline).toHaveBeenCalledTimes(1);
  });

  it("uses persisted cues when switching tracks and selecting another cue", async () => {
    const initial = subtitleCoverWorkspace();
    const chinese = initial.subtitle_tracks[0];
    chinese.cues.push({ ...chinese.cues[0], id: "subtitle-cue-2", text: "第二条字幕", line_breaks: [3] });
    initial.subtitle_tracks.push({ ...structuredClone(chinese), id: "subtitle-track-en", language: "en-US", cues: [{ ...chinese.cues[0], id: "subtitle-en-1", text: "Real work" }] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存" } });
    await chooseSelectValue(screen.getByRole("combobox",{name:"当前编辑字幕轨道"}),"subtitle-track-en");
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("Real work");
    expect(screen.getByLabelText("字幕字号")).toBeEnabled();
    fireEvent.click(screen.getByRole("tab",{name:"样式"}));expect(screen.getByText("全部字幕样式 · 中英文共用")).toBeInTheDocument();fireEvent.click(screen.getByRole("tab",{name:"字幕"}));
    await chooseSelectValue(screen.getByRole("combobox",{name:"当前编辑字幕轨道"}),"subtitle-track-zh");
    expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行不是口号");
    await chooseSelectValue(screen.getByLabelText("选择字幕"), "subtitle-cue-2");
    expect(screen.getByLabelText("字幕 2 文本")).toHaveValue("第二条\n字幕");
  });

  it("saves the persisted cover through ids and reports cancellation and failure", async () => {
    const initial = workspaceWithCover();
    const saveCoverImage = vi.fn()
      .mockResolvedValueOnce({ saved: true, fileName: "封面.png" })
      .mockResolvedValueOnce({ saved: false, canceled: true })
      .mockResolvedValueOnce({ saved: false, error: "目标目录不可写" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveCoverImage } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：已完成"));
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    await waitFor(() => expect(saveCoverImage).toHaveBeenCalledWith("task-1", "cover-1"));
    expect(await screen.findByText("封面已保存：封面.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByText("已取消保存封面。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("目标目录不可写");
  });

  it("explains the desktop requirement when cover saving is unavailable", async () => {
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspaceWithCover()} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：已完成"));
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.click(screen.getByRole("button", { name: "保存封面" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请在桌面客户端中保存封面");
    expect(screen.queryByText(/封面已保存/)).not.toBeInTheDocument();
  });

  it("seeks the subtitle preview to the selected cue", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks[0].cues.push({
      id: "subtitle-cue-2", source_cue_id: null, text: "第二句字幕", start_seconds: 4.5,
      end_seconds: 6.2, speaker: null, line_breaks: [], emphasis: [],
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    await chooseSelectValue(screen.getByLabelText("选择字幕"), "subtitle-cue-2");
    await waitFor(() => expect(player.currentTime).toBe(4.5));
    expect(screen.getByRole("button", { name: "选择中文字幕 2" })).toHaveAttribute("aria-pressed", "true");
  });

  it("saves only the current valid subtitle draft directly and leaves a later edit unsaved", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第一版修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "第一版修改" })));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "第二版修改" } });
    expect(mocks.exactEdit).toHaveBeenCalledOnce();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });
  it("blocks the doomed subtitle preview past the 96-character limit and recovers by shortening", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));

    // 超限：97 个字符，按 UpdateSubtitleCue 的 96 字上限必须拦下，且不发那条已知会 422 的请求。
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "字".repeat(97) } });
    expect(screen.getByLabelText("字幕 1 字数")).toHaveTextContent("97 / 96 字");
    expect(screen.getByRole("alert")).toHaveTextContent("超出 96 字上限");
    expect(screen.getByRole("button", { name: "保存字幕草稿" })).toBeDisabled();
    expect(mocks.exactEdit).not.toHaveBeenCalled();

    // 恢复：缩短回正好 96 个 Unicode 字符（含 6 个星芒平面字符，UTF-16 要 102 个码元），
    // 提示消失、按钮恢复可用，直接草稿提交按Unicode字符计数放行。
    const recoveredText = `${"字".repeat(90)}${"🎬".repeat(6)}`;
    expect(Array.from(recoveredText).length).toBe(96);
    expect(recoveredText.length).toBe(102);
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: recoveredText } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("字幕 1 字数")).toHaveTextContent("96 / 96 字");
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: recoveredText })));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
  });

  it("lets the user review persisted topic and script results without rerunning them", async () => {
    const initial = workspace(baseTask);
    initial.selected_topic = {
      id: "topic-1", run_id: "run-1", title: "真实选题", angle: "从一次真实失败说起",
      audience: "内容运营人员", outline: ["问题", "判断", "行动"], score: 92,
      selection_status: "confirmed", created_at: baseTask.created_at,
    };
    initial.topic_evidence = [{
      id: "evidence-1", candidate_id: "topic-1", kind: "knowledge", reference_id: "knowledge-1",
      version_or_identity: "v1", title: "已授权复盘笔记", authorization_or_license: "工作区已授权",
      source_url: null, created_at: baseTask.created_at,
    }];
    initial.scripts = [{
      id: "script-1", task_id: baseTask.id, run_id: "run-2", topic_id: "topic-1", title: "真实文案",
      language: "zh-CN", selection_status: "confirmed", created_at: baseTask.created_at,
      updated_at: baseTask.updated_at, confirmed_at: baseTask.updated_at,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开头", content: "这是已经确认的口播正文。", visual_prompt: "人物主镜头", evidence_reference_ids: ["evidence-1"] }],
    }];

    stubLoadedScriptWindows(initial.scripts);
    initial.scripts=initial.scripts.map(script=>({...script,sections_loaded:false,sections:null}));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("选题：已完成"));
    expect(screen.getByRole("heading", { name: "真实选题" })).toBeInTheDocument();
    expect(screen.getByText("已授权复盘笔记")).toBeInTheDocument();

    fireEvent.click(getWorkspaceViewControl("文案：已完成"));
    expect(screen.getByRole("heading", { name: "真实文案" })).toBeInTheDocument();
    expect(await screen.findByText("这是已经确认的口播正文。")).toBeInTheDocument();
  });

  it("does not offer a doomed generation action when no authorized voice exists", () => {
    const initial = workspace(baseTask);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.queryByRole("button", { name: "生成配音" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "添加声音" })).toHaveAttribute("href", "/profiles?view=voices&task=task-1");
    expect(screen.getByText(/你仍可采用这次本人录音/)).toBeInTheDocument();
  });

  it.each(["script", "narration"] as const)("lets the user revise and reconfirm after %s fails", async (stage) => {
    const failedTask = {
      ...baseTask,
      stage,
      confirmed_script_id: "script-1",
      can_revise_script: true,
      stage_reason: "文字模型服务拒绝了文案请求。",
      stage_impact: "未生成压缩后的文案。",
      stage_repair_action: "手动修订并重新确认文案。",
    };
    const initial = workspace(failedTask);
    initial.scripts = [{
      id: "script-1", task_id: failedTask.id, run_id: "run-2", topic_id: "topic-1", title: "原文案",
      language: "zh-CN", selection_status: "confirmed", created_at: failedTask.created_at,
      updated_at: failedTask.updated_at, confirmed_at: failedTask.updated_at,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "正文", content: "原来的正文过长。", visual_prompt: "人物主镜头", evidence_reference_ids: ["evidence-1"] }],
    }];
    const repaired = workspace({
      ...failedTask,
      stage: "narration" as const,
      stage_status: "not_started" as const,
      status: "running" as const,
      stage_reason: null,
      stage_impact: null,
      stage_repair_action: null,
    });
    repaired.scripts = initial.scripts;
    mocks.confirmScript.mockResolvedValue(repaired);
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(repaired);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const stageFailure = await screen.findByRole("alert", { name: `${stage === "script" ? "文案" : "配音"}失败` });
    expect(stageFailure).toHaveTextContent("文字模型服务拒绝了文案请求。");
    expect(stageFailure).toHaveTextContent("手动修订并重新确认文案。");
    if (stage === "narration") fireEvent.click(screen.getByRole("button", { name: "修改文案" }));
    expect(screen.getByRole("heading", { name: "修订文案" })).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("原来的正文过长。"), { target: { value: "压缩后的三十秒正文。" } });
    fireEvent.click(screen.getByRole("button", { name: "确认修订文案" }));

    await waitFor(() => expect(mocks.confirmScript).toHaveBeenCalledWith("task-1", "script-1", expect.objectContaining({
      sections: [expect.objectContaining({ content: "压缩后的三十秒正文。" })],
    })));
    expect(mocks.generateNarration).not.toHaveBeenCalled();
  });

  it.each(["recorded", "lost_response", "query_retry", "explicit_replay", "query_unavailable", "conflict"])("binds a later authorized voice through its original receipt: %s", async outcome => {
    const initial = workspace({...baseTask, stage_status:"not_started", can_revise_script:true});
    initial.available_voices = [{
      profile_id: "voice-1", version: "v1", person_display_name: "主讲人",
      language: "zh", model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      runnable: true, runnable_reason: null,
    }];
    const selected = workspace({ ...baseTask, stage_status: "not_started", status: "running", stage_reason: null, stage_impact: null, stage_repair_action: null });
    selected.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "v1",
      person_authorization_id: "person-auth", person_authorization_version: "v1",
      authorization_id: "voice-auth", authorization_version: "v1", language: "zh",
      model_id: "qwen3-tts-12hz-0.6b-base-8bit",
    };
    selected.available_voices = initial.available_voices;
    const receipt = {request_id:"voice-request", task_id:"task-1", plan_id:"plan-1", status:"selected", selected_voice:selected.plan.selected_voice};
    mocks.selectVoice.mockResolvedValue(receipt);
    mocks.getVoiceSelection.mockResolvedValue(receipt);
    if (outcome !== "recorded") mocks.selectVoice.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(0, null, "响应连接中断"));
    if (outcome === "query_retry" || outcome === "explicit_replay") mocks.getVoiceSelection.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(404, null, "尚未找到原请求"));
    if (outcome === "query_unavailable") mocks.getVoiceSelection.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(503, null, "查询暂不可用"));
    if (outcome === "conflict") {
      mocks.selectVoice.mockReset().mockRejectedValueOnce(new mocks.MockCreationPlanApiError(409, null, "声音采用的方案版本已过期"));
    }
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(selected);
    const preview = {
      task_id: "task-1", script_id: "script-1", script_title: "已确认文案",
      script_sections: [{ title: "正文", content: "实际内容" }],
      plan_id: "plan-1", input_digest: "c".repeat(64),
      narration_source: null,
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 0, lipsync_quality: "fast", selected_voice: null as string | null,
        can_continue: false, continuation_blocker: "请先选择授权声音。", template_settings: {} },
    };
    mocks.previewExecution.mockResolvedValueOnce(preview).mockResolvedValueOnce({
      ...preview, requirements: { ...preview.requirements, selected_voice: "voice-1", can_continue: true, continuation_blocker: null },
    });

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await productionPanel();expect(await screen.findByRole("button", { name: "暂不能开始制作" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button",{name:"收起制作详情"}));
    expect(screen.getByRole("button", {name:"修改文案"})).toBeEnabled();
    expect(screen.getByRole("combobox", {name:"用于生成配音的声音"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采用此声音" }));

    await waitFor(() => expect(mocks.selectVoice).toHaveBeenCalledWith("task-1", {
      voice_profile_id: "voice-1", voice_version: "v1", request_id:expect.stringMatching(/^[0-9a-f-]{36}$/),
      expected_plan_updated_at:initial.plan.updated_at, expected_task_updated_at:initial.task.updated_at,
    }));
    const originalId = mocks.selectVoice.mock.calls[0][1].request_id;
    if (outcome === "conflict") {
      await screen.findByText("声音采用的方案版本已过期");
      expect(mocks.getVoiceSelection).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", {name:"查询声音采用结果"})).not.toBeInTheDocument();
      return;
    }
    if (outcome === "query_retry" || outcome === "explicit_replay") {
      await screen.findByText("声音采用结果尚未查明，请查询原请求。");
      expect(screen.getByRole("button", {name:"采用此声音"})).toBeDisabled();
      fireEvent.click(screen.getByRole("button", {name:outcome === "query_retry" ? "查询声音采用结果" : "继续原声音采用请求"}));
    }
    if (outcome === "query_unavailable") {
      await screen.findByText("声音采用结果尚未查明，请查询原请求。");
      expect(screen.queryByRole("button", {name:"继续原声音采用请求"})).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", {name:"查询声音采用结果"}));
    }
    await screen.findByText("声音采用请求已查明，请确认文案和要求后开始连续制作。");
    expect(await productionAction("确认以上文案和要求，开始制作")).toBeEnabled();
    if (outcome === "explicit_replay") {
      expect(mocks.selectVoice).toHaveBeenCalledTimes(2);
      expect(mocks.selectVoice.mock.calls[1]).toEqual(mocks.selectVoice.mock.calls[0]);
    } else expect(mocks.selectVoice).toHaveBeenCalledOnce();
    if (outcome === "recorded") expect(mocks.getVoiceSelection).not.toHaveBeenCalled();
    else expect(mocks.getVoiceSelection).toHaveBeenCalledWith("task-1", originalId);
    if (outcome === "query_retry") expect(mocks.getVoiceSelection).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["voice_reference_unavailable", "声音参考暂不可用，请检查来源和使用范围。"],
    ["voice_language_unavailable", "声音不支持当前方案语言。"],
    ["unrecognized_private_code", "此声音暂不可用，服务未提供可显示的具体原因。"],
  ])("retains an unavailable voice and its safe reason while script revision remains available: %s", async (reason, message) => {
    const initial = workspace({...baseTask, stage_status:"not_started", can_revise_script:true});
    initial.available_voices = [{profile_id:"voice-unavailable", version:"v1", person_display_name:"已登记声音", language:"en", model_id:"model", runnable:false, runnable_reason:reason}];
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByRole("button", {name:"修改文案"})).toBeEnabled();
    expect(screen.getByRole("button", {name:"采用此声音"})).toBeDisabled();
    expect(screen.getByRole("list", {name:"暂不可用的声音"})).toHaveTextContent(`已登记声音 · v1：${message}`);
    expect(screen.queryByText("还没有可用于生成配音的授权声音")).not.toBeInTheDocument();
    expect(screen.queryByText(reason)).not.toBeInTheDocument();
    expect(screen.getByRole("link", {name:"检查声音参考"})).toHaveAttribute("href", "/profiles?view=voices&task=task-1");
    await chooseSelectValue(screen.getByRole("combobox", {name:"用于生成配音的声音"}), "voice-unavailable");
    expect(mocks.selectVoice).not.toHaveBeenCalled();
    expect(mocks.generateNarration).not.toHaveBeenCalled();
  });

  it.each(["populated", "empty", "failed"])("shows source loading before a real %s response without changing frozen references", async outcome => {
    let resolve!: (value: unknown) => void;
    let reject!: (cause: Error) => void;
    mocks.getAssistantScriptContext.mockImplementationOnce(() => new Promise((done, fail) => { resolve = done; reject = fail; }));
    const script: WorkspaceState["scripts"][number] = {
      id:"script-source-read", task_id:"task-1", run_id:"run", topic_id:"topic", title:"已有文案", language:"zh-CN",
      selection_status:"confirmed", created_at:baseTask.created_at, updated_at:baseTask.updated_at, confirmed_at:baseTask.updated_at,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections:[{id:"section-1", title:"正文", content:"原正文", visual_prompt:"原画面", evidence_reference_ids:["knowledge:fixed:v1"]}],
    };
    const confirm = vi.fn();
    render(<ScriptEditor taskId="task-1" script={script} busy={false} repairing={false} onConfirm={confirm} />);
    expect(screen.getByText("正在读取当前任务资料…")).toBeInTheDocument();
    expect(screen.queryByText("当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", {name:"第 1 段正文"}), {target:{value:"正在编辑的正文"}});
    if (outcome === "failed") {
      await act(async () => reject(new Error("当前资料读取暂不可用")));
      expect(screen.getByRole("alert")).toHaveTextContent("当前资料读取暂不可用");
      expect(screen.queryByText("当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。")).not.toBeInTheDocument();
    } else {
      await act(async () => resolve({sources:outcome === "populated" ? [{reference_id:"knowledge:fixed:v1", title:"已采用资料", text:"资料正文"}] : []}));
      if (outcome === "populated") expect(screen.getByRole("checkbox", {name:"已采用资料"})).toBeChecked();
      else expect(screen.getByText("当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。")).toBeInTheDocument();
    }
    expect(screen.queryByText("正在读取当前任务资料…")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", {name:"第 1 段正文"})).toHaveValue("正在编辑的正文");
    expect(script.sections[0].evidence_reference_ids).toEqual(["knowledge:fixed:v1"]);
    expect(confirm).not.toHaveBeenCalled();
  });

  it("does not apply a late original voice receipt to another task after switching", async () => {
    const initial = workspace(baseTask);
    initial.available_voices = [{profile_id:"voice-1", version:"v1", person_display_name:"声音", language:"zh", model_id:"model", runnable:true, runnable_reason:null}];
    mocks.selectVoice.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(0, null, "响应中断"));
    let resolveQuery!: (value: unknown) => void;
    mocks.getVoiceSelection.mockReturnValueOnce(new Promise(resolve => { resolveQuery = resolve; }));
    const view = renderTaskSubject(<CreationTaskWorkspace key="task-1" initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", {name:"采用此声音"}));
    await waitFor(() => expect(mocks.getVoiceSelection).toHaveBeenCalledOnce());
    const originalId = mocks.selectVoice.mock.calls[0][1].request_id;
    const next = workspace({...baseTask, id:"task-2", plan_id:"plan-2", name:"另一任务"});
    view.rerender(<CreationTaskWorkspace key="task-2" initialTasks={[]} initialTopics={null} initialWorkspace={next} />);
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenLastCalledWith("task-2"));
    const reads = mocks.getWorkspace.mock.calls.length;
    await act(async () => resolveQuery({request_id:originalId, task_id:"task-1", status:"selected"}));
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(reads);
    expect(screen.queryByText("声音采用请求已查明，请确认文案和要求后开始连续制作。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name:"查询声音采用结果"})).not.toBeInTheDocument();
  });

  it("sends only the task id to the native save boundary", async () => {
    const task = { ...baseTask, stage: "review" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 90, output_ready: true };
    const initial = workspace(task, {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
      status: "rendered", cues: [], clips: [], duration_seconds: 12,
      low_confidence_clip_count: 0, narration_reference: "narration://artifact/audio-1",
      output_url: `/api/creation-tasks/${task.id}/output`,
      subtitle_url: `/api/creation-tasks/${task.id}/output/subtitles`,
      message: "等待检查", created_at: task.created_at, updated_at: task.updated_at,
    });
    const saveRenderedVideo = vi.fn().mockResolvedValue({ saved: true, fileName: "成片.mp4" });
    Object.defineProperty(window, "electronAPI", { configurable: true, value: { saveRenderedVideo } });
    mocks.renderMontage.mockResolvedValue(initial.montage);
    mocks.getWorkspace.mockResolvedValue(initial);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(getWorkspaceViewControl("人物与口型：待配音")).toHaveAttribute("aria-disabled", "true");
    expect(getWorkspaceViewControl("字幕与封面：待补封面")).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText("数据监控")).not.toBeInTheDocument();
    expect(getWorkspaceViewControl("字幕与封面：待补封面")).toHaveAttribute("data-stage-state", "current");
    expect(getWorkspaceViewControl("检查与输出：待上一步")).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "保存到本地" }));

    await waitFor(() => expect(saveRenderedVideo).toHaveBeenCalledWith("task-1"));
    expect(saveRenderedVideo).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("已保存：成片.mp4")).toHaveAttribute("role", "status");
    fireEvent.click(screen.getByRole("button", { name: "调整成片" }));
    fireEvent.click(screen.getByRole("button", { name: "修改镜头" }));
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("enters the person stage from the rail and reports a pure-material plan as skipped rather than done", () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const initial = workspace(task);
    initial.plan.presenter_share_target = 0;
    initial.person_render_capability = { available: false, model_id: null, message: "本地人物口型模型未配置。", repair_action: "配置模型后重试。" };
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
        audio_reference: "narration://artifact/segment-1",
        audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
        voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
        pronunciation_version: "p1", status: "ready" as const,
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    const railItem = getWorkspaceViewControl("人物与口型：明确跳过 · 目标 0%");
    expect(railItem).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(railItem);

    expect(screen.getByText("本方案的露脸目标为 0%，自动口型阶段已跳过。")).toBeInTheDocument();
    expect(screen.getByText("0% · 实际 未返回")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "人物口型暂不可用" })).not.toBeInTheDocument();
    expect(screen.queryByText("当前口型能力")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成人物口型预览/ })).not.toBeInTheDocument();
    expect(getWorkspaceViewControl("人物与口型：明确跳过 · 目标 0%")).not.toHaveAttribute("aria-current");
  });

  it("generates lipsync for a chosen narration segment when the plan asks for on-camera presence", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const initial = workspace(task);
    initial.plan.presenter_share_target = 40;
    initial.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "person-v1",
      person_authorization_id: "person-auth-1", person_authorization_version: "v1",
      authorization_id: "voice-auth-1", authorization_version: "v1", language: "zh", model_id: "qwen3-tts-12hz-0.6b-base-8bit",
    };
    initial.plan.selected_person = { profile_id: "person-1", version: "person-v1" };
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
        audio_reference: "narration://artifact/segment-1",
        audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
        voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
        pronunciation_version: "p1", status: "ready" as const,
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };
    mocks.generatePersonRender.mockResolvedValue({ id: "person-render-1" });
    mocks.getWorkspace.mockResolvedValue(initial);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/人物与口型/));

    expect(screen.getByText("还没有生成任何口型；系统只为选中的露脸段生成口型。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择配音 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成人物口型预览" }));

    await waitFor(() => expect(mocks.generatePersonRender).toHaveBeenCalledWith("task-1", {
      narration_segment_id: "narration-segment-1",
      person_profile_id: "person-1",
      person_version: "person-v1",
      motion: "natural",
      expression: "neutral",
    }));
  });

  it("does not infer the selected person from the selected voice reference", () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const initial = workspace(task);
    initial.plan.presenter_share_target = 40;
    initial.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "person-v1",
      person_authorization_id: "person-auth-1", person_authorization_version: "v1",
      authorization_id: "voice-auth-1", authorization_version: "v1", language: "zh", model_id: "qwen3-tts",
    };
    initial.plan.selected_person = null;
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
        audio_reference: "narration://artifact/segment-1", audio_url: "/api/segment-1/audio",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
        voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
        pronunciation_version: "p1", status: "ready" as const,
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/人物与口型/));

    expect(screen.getByText("声音档案中的关联人物不能替代本任务的已选人物。请先在制作方案中选择人物版本，再为配音片段生成口型。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成人物口型预览/ })).not.toBeInTheDocument();
    expect(mocks.generatePersonRender).not.toHaveBeenCalled();
  });

  it("does not report the person stage complete while a required segment is still failed", () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const segment = (id: string) => ({
      id, source_section_id: "section-1", text: "一段真实配音", audio_reference: `narration://artifact/${id}`,
      audio_url: `/api/creation-tasks/task-1/narration/segments/${id}/audio`,
      duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
      voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
      pronunciation_version: "p1", status: "ready" as const,
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
    });
    const initial = workspace(task);
    initial.plan.presenter_share_target = 40;
    initial.plan.selected_voice = {
      profile_id: "voice-1", version: "v1", person_profile_id: "person-1", person_version: "person-v1",
      person_authorization_id: "person-auth-1", person_authorization_version: "v1",
      authorization_id: "voice-auth-1", authorization_version: "v1", language: "zh", model_id: "qwen3-tts",
    };
    initial.plan.selected_person = { profile_id: "person-1", version: "person-v1" };
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 6,
      sample_rate: 24000, model_id: "qwen3-tts", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "p1",
      segments: [segment("narration-segment-1"), segment("narration-segment-2")],
    };
    const personRender = (id: string, narrationSegmentId: string, status: "ready" | "failed") => ({
      id, task_id: task.id,
      person_profile_id: "person-1", person_version: "person-v1",
      voice_profile_id: "voice-1", voice_version: "v1",
      narration_segment_id: narrationSegmentId, visual_reference: "workspace://person.mp4",
      model_id: "musetalk-local", motion: "natural" as const, expression: "neutral" as const,
      status, duration_seconds: 3, alignment_evidence: "aligned",
      media_url: status === "ready" ? `/api/${id}` : null,
      failure_reason: status === "failed" ? "口型未完成" : null,
      impact: status === "failed" ? "这一段没有成片。" : null,
      repair_action: status === "failed" ? "重新生成这一段。" : null,
      created_at: task.created_at, updated_at: task.updated_at,
    });
    // 第一段已生成，第二段失败；成片两个镜头都引用了人物口型。
    initial.person_renders = [personRender("person-render-1", "narration-segment-1", "ready"), personRender("person-render-2", "narration-segment-2", "failed")];
    initial.montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "成片",
      status: "waiting_for_user", cues: [], duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/full-audio",
      output_url: null, subtitle_url: null, message: "等待确认",
      clips: ["person-render-1", "person-render-2"].map((personRenderId, index) => ({
        id: `clip-${index + 1}`, cue_id: `cue-${index + 1}`, asset_id: "asset-1", segment_id: `segment-${index + 1}`,
        source_reference: "asset://asset-1", source_start_seconds: 0, source_end_seconds: 3,
        timeline_start_seconds: index * 3, timeline_end_seconds: index * 3 + 3, match_score: 0.9,
        needs_confirmation: false, role: "a_roll" as const, locked: false, manually_selected: false,
        person_render_id: personRenderId,
      })),
      created_at: task.created_at, updated_at: task.updated_at,
    };

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    expect(getWorkspaceViewControl("人物与口型：已生成 1/2 段 · 仍有口型未完成")).toBeInTheDocument();
    expect(queryWorkspaceViewControl("人物与口型：已完成")).not.toBeInTheDocument();
  });

  it("does not report narration as complete while segments are still running or have failed", () => {
    const task = { ...baseTask, stage: "narration" as const, stage_status: "running" as const, status: "running" as const };
    const segment = {
      id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
      audio_reference: "narration://artifact/segment-1",
      audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
      duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const,
      pronunciation_version: "p1", status: "ready" as const,
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
    };
    const initial = workspace(task);
    initial.narration = {
      task, script_id: "script-1", audio_reference: "narration://artifact/full-audio", duration_seconds: 3,
      sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit", voice_profile_id: "voice-1",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const, pronunciation_version: "v1", segments: [segment],
    };

    const { unmount } = renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={{
      ...initial,
      narration: { ...initial.narration!, segments: [{ ...segment, status: "generating" as const }] },
    }} />);
    expect(getWorkspaceViewControl("配音：配音生成中")).toBeEnabled();
    expect(queryWorkspaceViewControl("配音：已完成")).not.toBeInTheDocument();
    unmount();

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={{
      ...initial,
      narration: { ...initial.narration!, segments: [{ ...segment, status: "failed" as const }] },
    }} />);
    expect(getWorkspaceViewControl("配音：有配音段未完成")).toBeEnabled();
    expect(getWorkspaceViewControl("配音：有配音段未完成")).toHaveAttribute("data-stage-state", "current");
  });

  it("opens the persisted subtitle and cover workspace and saves completed cue edits directly", async () => {
    const initial = subtitleCoverWorkspace();
    const refreshed = subtitleCoverWorkspace();
    refreshed.task = { ...refreshed.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 };
    refreshed.subtitle_tracks = [{
      ...refreshed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{
        ...refreshed.subtitle_tracks[0].cues[0],
        text: "真实运行不是口号",
        line_breaks: [4],
        emphasis: ["真实运行"],
      }],
    }];
    mocks.exactPreview.mockResolvedValue(exactPreview(initial.task, "UpdateSubtitleCue"));
    mocks.confirmEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: initial.montage });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));

    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();
    expect(screen.getByLabelText("剪辑成片预览")).toHaveAttribute("src", "/api/creation-tasks/task-1/output?version=timeline-version-6");
    expect(screen.queryByLabelText("当前选择")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /AI 生成封面/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    const inspector = screen.getByLabelText("当前选择");
    expect(inspector).toHaveTextContent("时间0.0–3.2 秒");
    expect(inspector).toHaveTextContent("字幕产物artifact:subtitle:zh:v6");
    expect(inspector).toHaveTextContent("运行证据transform:subtitle:v6");

    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.blur(screen.getByLabelText("字幕 1 强调词"));

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "UpdateSubtitleCue",
      track_id: "subtitle-track-zh",
      cue_id: "subtitle-cue-1",
      text: "真实运行\n不是口号",
      start_seconds: 0.4,
      end_seconds: 2.8,
      line_breaks: [4],
      emphasis: ["真实运行"],

    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("真实运行\n不是口号"));
    expect(mocks.exactEdit).toHaveBeenCalledOnce();

    mocks.previewEdit.mockResolvedValue({ ...exactPreview(refreshed.task, "UpdateSubtitleCue"), proposal: { ...exactPreview(refreshed.task, "UpdateSubtitleCue").proposal, id: "proposal-natural" } });
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "这句再简洁一点" } });
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "这句再简洁一点",
      context_stage: "subtitle_cover",
      selected_object_refs: ["cue:subtitle-cue-1", "subtitle-track:subtitle-track-zh"],
      playhead_ms: null,
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", {name:"应用到草稿"}));
    await waitFor(()=>expect(mocks.confirmEdit).toHaveBeenCalledOnce());
    expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1","proposal-natural");
  });

  // WIL-180 专项：字幕确认后服务端把任务退回 montage 并作废旧成片
  // （stage:"montage"、output_ready:false、output_url:null，见 WIL-152/evidence/02-replaced.json），
  // 字幕轨仍是 1 条 valid、can_undo:true。此时字幕页按既有门禁已不可呈现
  // （canOpenStage("subtitle_cover") 要求 output_url），但 confirmEdit 的 refresh(true) 仍保留它，
  // 结果主区没有任何视图命中 → 空白、撤销入口一起消失。
  // 正确表现是按既有可用/默认规则回到 montage 剪辑页，撤销入口可见。
  it("keeps the editable subtitle view after a versioned save invalidates the old output", async () => {
    const initial = subtitleCoverWorkspace();
    const confirmed = subtitleCoverWorkspace();
    confirmed.task = {
      ...confirmed.task,
      stage: "montage" as const,
      progress: 75,
      current_timeline_version_id: "timeline-version-7",
      can_undo: true,
      can_redo: false,
      output_ready: false,
    };
    confirmed.montage = {
      ...confirmed.montage!,
      output_url: null,
      subtitle_url: null,
      message: "字幕修改已保存，配音未改；请重新渲染。",
    };
    confirmed.subtitle_tracks = [{
      ...confirmed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{ ...confirmed.subtitle_tracks[0].cues[0], text: "真实运行\n不是口号" }],
    }];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: confirmed.montage });
    mocks.getWorkspace.mockResolvedValue(confirmed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));
    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "真实运行\n不是口号" })));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));

    // 字幕草稿仍可编辑，但失效的成片不能冒充新预览；回到剪辑阶段仍能撤销。
    expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument();
    expect(screen.queryByLabelText("剪辑成片预览")).not.toBeInTheDocument();
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    expect(screen.getByText("未出片")).toBeInTheDocument();
    expect(screen.queryByLabelText("剪辑成片预览")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled();
  });

  // 对照用例：成片仍有效（output_url 保留）时，refresh(true) 仍保留字幕页，不得乱跳。
  // 字段改动与既有用例一致，确保单跑也能产出合法精确预览。
  it("does not jump away from a still-valid view when output_url is present", async () => {
    const initial = subtitleCoverWorkspace();
    const confirmed = subtitleCoverWorkspace();
    confirmed.task = { ...confirmed.task, current_timeline_version_id: "timeline-version-7", timeline_sequence: 7 };
    confirmed.subtitle_tracks = [{
      ...confirmed.subtitle_tracks[0],
      timeline_version_id: "timeline-version-7",
      cues: [{ ...confirmed.subtitle_tracks[0].cues[0], text: "真实运行\n不是口号" }],
    }];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "operation-subtitle" }, montage: confirmed.montage });
    mocks.getWorkspace.mockResolvedValue(confirmed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));
    fireEvent.click(screen.getByRole("button", { name: "选择中文字幕 1" }));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "真实运行\n不是口号" } });
    fireEvent.change(screen.getByLabelText("字幕 1 出现时间"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("字幕 1 结束时间"), { target: { value: "2.8" } });
    fireEvent.change(screen.getByLabelText("字幕 1 强调词"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("button", { name: "保存字幕草稿" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", expect.objectContaining({ operation_type: "UpdateSubtitleCue", text: "真实运行\n不是口号" })));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));

    // 成片仍有效时不得乱跳：仍停在字幕与封面页，预览器照旧
    await waitFor(() => expect(screen.getByLabelText("字幕与封面工作区")).toBeInTheDocument());
    expect(screen.getByLabelText("剪辑成片预览")).toHaveAttribute("src", "/api/creation-tasks/task-1/output?version=timeline-version-7");
  });

  it("applies a valid transition edit when the field is committed", async () => {
    const initial=subtitleCoverWorkspace();
    const clip={id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",
      source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,
      match_reasons:[],needs_confirmation:false,role:"b_roll" as const,locked:false,manually_selected:false};
    initial.montage!.clips=[clip,{...clip,id:"clip-2",timeline_start_seconds:3,timeline_end_seconds:6}];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "transition-edit" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 2"}));
    fireEvent.click(screen.getByText("镜头过渡"));
    fireEvent.change(screen.getByLabelText("叠化时长（秒）"),{target:{value:"0.4"}});
    fireEvent.blur(screen.getByLabelText("叠化时长（秒）"));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenCalledWith("task-1",{operation_type:"SetClipTransition",clip_id:"clip-2",transition_seconds:.4}));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps selected image, inspector, playhead and natural language scope synchronized",async()=>{
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    vi.spyOn(HTMLMediaElement.prototype,"pause").mockImplementation(()=>{});
    initial.montage!.graphic_overlays=[{kind:"image",id:"overlay-logo",source_ref:"artifacts/logo.png",name:"logo.png",start_seconds:1,end_seconds:3,x:.5,y:.5,width:.3,opacity:1}];
    mocks.previewEdit.mockResolvedValue(exactPreview(initial.task,"EditGraphicOverlay"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button",{name:"图片1 · logo.png"}));
    expect(screen.getByRole("complementary",{name:"图片1"})).toBeInTheDocument();
    expect(screen.getByLabelText("图片不透明度")).toHaveValue(1);
    const player=screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    expect(player.currentTime).toBe(2);
    player.currentTime=4;fireEvent.timeUpdate(player);
    expect(screen.getByRole("complementary",{name:"图片1"})).toBeInTheDocument();
    fireEvent.change(await readyInput("局部修改要求"),{target:{value:"当前图片不透明度改为50%"}});
    fireEvent.click(screen.getByRole("button",{name: "精确预览修改"}));
    await waitFor(()=>expect(mocks.previewEdit).toHaveBeenCalledWith("task-1",expect.objectContaining({selected_object_refs:["overlay:overlay-logo"],raw_command:"当前图片不透明度改为50%"})));
    expect(await screen.findByLabelText("变更预览")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary",{name:"图片1"})).not.toBeInTheDocument();
  });

  it("requires a new preview when compound steps are excluded",async()=>{
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-1",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    const base=exactPreview(initial.task,"EditCompoundTimeline");
    const steps=[{index:0,command:"当前镜头亮一点",enabled:true},{index:1,command:"配音小一点",enabled:true}];
    const original={...base,intent:{...base.intent,raw_command:"当前镜头亮一点，同时配音小一点"},proposal:{...base.proposal,operation:{operation_type:"EditCompoundTimeline",payload:{selection_enabled:true,steps}},preview:{before:{ref:"before",label:"修改前",detail:"",video_url:"/before.mp4"},after:{ref:"after",label:"修改后",detail:"",video_url:"/after.mp4"}}}};
    mocks.previewEdit.mockResolvedValue(original);
    mocks.previewSelection.mockResolvedValue({...original,proposal:{...original.proposal,id:"selected-proposal",operation:{operation_type:"EditCompoundTimeline",payload:{selection_enabled:true,steps:[steps[0],{...steps[1],enabled:false}]}},preview:{...original.proposal.preview,after:{...original.proposal.preview.after,video_url:"/selected.mp4"}}}});
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    fireEvent.change(await readyInput("局部修改要求"),{target:{value:"当前镜头亮一点，同时配音小一点"}});
    fireEvent.click(screen.getByRole("button",{name: "精确预览修改"}));
    const checkbox=await screen.findByLabelText("保留修改2：配音小一点");
    fireEvent.click(checkbox);
    expect(screen.getByLabelText("局部修改要求")).toBeEnabled();
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeDisabled();
    expect(screen.queryByLabelText("修改后画面")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"重看所选"}));
    await waitFor(()=>expect(mocks.previewSelection).toHaveBeenCalledWith("task-1",original.proposal.id,[0]));
    await waitFor(()=>expect(screen.getByLabelText("修改后画面")).toHaveAttribute("src","/selected.mp4"));
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeEnabled();
    expect(screen.getByLabelText("保留修改2：配音小一点")).not.toBeChecked();
    fireEvent.click(within(screen.getByRole("dialog",{name:"修改提案"})).getByRole("button",{name:"取消"}));
    mocks.previewEdit.mockResolvedValue({...original,intent:{...original.intent,raw_command:"恢复第2项"},proposal:{...original.proposal,id:"restored-proposal"}});
    fireEvent.change(await readyInput("局部修改要求"),{target:{value:"恢复第2项"}});
    fireEvent.click(screen.getByRole("button",{name:"精确预览修改"}));
    await waitFor(()=>expect(mocks.previewEdit).toHaveBeenLastCalledWith("task-1",expect.objectContaining({raw_command:"恢复第2项",selected_object_refs:["clip:clip-1"]})));
    await waitFor(()=>expect(screen.getByLabelText("保留修改2：配音小一点")).toBeChecked());
    expect(screen.getByRole("button",{name:"应用到草稿"})).toBeEnabled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("submits canvas ratio and fit changes directly from their selectors", async () => {
    const initial=subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "canvas-edit" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByText("画幅设置"));
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"16:9 横屏"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"16:9"}));
    fireEvent.click(screen.getByRole("button", {name:"整片画面适配"}));
    fireEvent.click(screen.getByRole("option", {name:"完整画面"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"16:9",fit_mode:"fit"}));
    expect(screen.queryByRole("button",{name:"预览画幅"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"1:1 方形"}));
    await waitFor(()=>expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1",{operation_type:"ChangeCanvas",aspect_ratio:"1:1",fit_mode:"fit"}));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("restores the saved canvas value and shows the real API failure when a direct edit is rejected", async () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.aspect_ratio = "16:9";
    mocks.exactEdit.mockRejectedValue(new Error("valid 字幕轨必须有条目、产物和变换证据"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByText("画幅设置"));
    fireEvent.click(screen.getByRole("button", {name:"成片画幅"}));
    fireEvent.click(screen.getByRole("option", {name:"1:1 方形"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid 字幕轨必须有条目、产物和变换证据");
    fireEvent.click(within(screen.getByRole("dialog", { name: "成片画幅" })).getByRole("button", { name: "关闭卡片" }));
    fireEvent.click(screen.getByRole("button", {name:/^画幅设置/}));
    expect(screen.getByRole("button", {name:"成片画幅"})).toHaveTextContent("16:9 横屏");
    expect(screen.getByLabelText("剪辑成片预览").parentElement).toHaveAttribute("data-editor-aspect-ratio", "16:9");
    expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {operation_type:"ChangeCanvas",aspect_ratio:"1:1"});
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("sizes the editor preview and timeline clips from actual montage intervals", () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.aspect_ratio = "16:9";
    initial.montage!.duration_seconds = 10;
    const clip = { id:"clip-1", cue_id:"cue-1", asset_id:"asset-1", segment_id:"segment-1", source_reference:"workspace://clip.mp4", source_start_seconds:0, source_end_seconds:2, timeline_start_seconds:2, timeline_end_seconds:4, match_score:.9, match_reasons:[], needs_confirmation:false, role:"b_roll" as const, locked:false, manually_selected:false };
    initial.montage!.clips = [clip, { ...clip, id:"clip-2", timeline_start_seconds:5, timeline_end_seconds:10 }];
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    const player = screen.getByLabelText("剪辑成片预览").parentElement;
    expect(player).toHaveAttribute("data-editor-aspect-ratio", "16:9");
    const timeline = screen.getByLabelText("配音与字幕时间范围");
    const clips = within(timeline).getAllByRole("button", { name:/^定位镜头/ });
    expect(clips[0]).toHaveStyle({ left:"20%", width:"20%" });
    expect(clips[1]).toHaveStyle({ left:"50%", width:"50%" });
  });

  it("applies subtitle style settings directly without relying on generated preview imagery", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks = [{...initial.subtitle_tracks[0], language:"en-US", source_language:null}];
    mocks.exactEdit.mockResolvedValue({ operation: { id: "subtitle-style-edit" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    fireEvent.click(screen.getByRole("tab",{name:"样式"}));
    fireEvent.change(screen.getByLabelText("字幕字号"), {target:{value:"64"}});
    fireEvent.change(screen.getByLabelText("字幕背景不透明度"), {target:{value:"0"}});
    fireEvent.click(screen.getByRole("button", {name:"保存字幕样式"}));
    await waitFor(() => expect(mocks.exactEdit.mock.calls.some(([, input]) => input.operation_type === "ConfigureSubtitleTrack" && input.style?.font_size === 64)).toBe(true));
    expect(mocks.exactEdit.mock.calls.some(([, input]) => input.operation_type === "ConfigureSubtitleTrack" && input.style?.background_opacity === 0)).toBe(true);
    expect(screen.queryByAltText("修改后字幕样式")).not.toBeInTheDocument();
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("applies subtitle structure changes directly and disables stale delete after a draft edit", async () => {
    const initial = subtitleCoverWorkspace();
    const track = initial.subtitle_tracks[0];
    const cue = track.cues[0];
    mocks.getWorkspace.mockResolvedValue({ ...initial,
      subtitle_tracks: [{ ...track, cues: [{ ...cue, text: cue.text.slice(0, 3), end_seconds: 1 },
        { ...cue, id: "split-second", text: cue.text.slice(3), start_seconds: 1 }] }],
    });
    mocks.exactEdit.mockResolvedValue({ operation: { id: "split-subtitle" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    fireEvent.click(screen.getByText("拆分、合并与删除"));
    fireEvent.change(screen.getByLabelText("字幕拆分文字位置"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("字幕拆分时间"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "拆分字幕" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "SplitSubtitleCue", track_id: "subtitle-track-zh", cue_id: "subtitle-cue-1", split_offset: 3, split_time_seconds: 1,
    }));
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByLabelText("字幕 1 文本")).toHaveValue(cue.text.slice(0, 3)));
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "未保存草稿" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "删除字幕" })).toBeDisabled());
  });

  it("applies full-track literal replacement directly with each changed caption", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "replace-subtitle-text" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    fireEvent.click(screen.getByText("全文查找替换"));
    fireEvent.change(screen.getByLabelText("字幕查找文字"), { target: { value: "旧名字" } });
    fireEvent.change(screen.getByLabelText("字幕替换文字"), { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "应用全文替换" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceSubtitleText", track_id: "subtitle-track-zh", find_text: "旧名字", replacement_text: "新名字",
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("does not offer missing Chinese captions in an English-only task", () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_tracks = [{ ...initial.subtitle_tracks[0], language: "en-US", source_language: null }];
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    expect(screen.getByRole("switch", { name: "中文轨开关" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "英文轨开关" })).toHaveAttribute("aria-checked", "true");
  });

  it("applies hiding subtitles directly without generating a translation", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.exactEdit.mockResolvedValue({ operation: { id: "subtitle-display-edit" } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    fireEvent.click(screen.getByRole("switch", { name: "中文轨开关" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ConfigureSubtitleTrack", subtitle_display: "none",
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("toggles Chinese and English subtitle tracks independently using current workspace facts", async () => {
    const initial = subtitleCoverWorkspace();
    const english = {
      ...initial.subtitle_tracks[0],
      id: "subtitle-track-en", language: "en-US" as const, source_language: "zh-CN" as const,
      style: { ...initial.subtitle_tracks[0].style, enabled: false },
      cues: [{ ...initial.subtitle_tracks[0].cues[0], id: "subtitle-cue-en", text: "English line" }],
    };
    initial.subtitle_tracks = [...initial.subtitle_tracks, english];
    const bothEnabled = { ...initial, subtitle_tracks: initial.subtitle_tracks.map(track => track.id === english.id
      ? { ...track, style: { ...track.style, enabled: true } } : track) };
    const englishOnly = { ...bothEnabled, subtitle_tracks: bothEnabled.subtitle_tracks.map(track => track.id === "subtitle-track-zh"
      ? { ...track, style: { ...track.style, enabled: false } } : track) };
    mocks.exactEdit.mockResolvedValue({ operation: { id: "subtitle-display-edit" } });
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValueOnce(bothEnabled).mockResolvedValueOnce(englishOnly);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));

    fireEvent.click(screen.getByRole("switch", { name: "英文轨开关" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(1, "task-1", {
      operation_type: "ConfigureSubtitleTrack", subtitle_display: "zh-en",
    }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "英文轨开关" })).toHaveAttribute("aria-checked", "true"));
    expect(screen.getByRole("switch", { name: "中文轨开关" })).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("switch", { name: "中文轨开关" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(2, "task-1", {
      operation_type: "ConfigureSubtitleTrack", subtitle_display: "en",
    }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "中文轨开关" })).toHaveAttribute("aria-checked", "false"));
    expect(screen.getByRole("switch", { name: "英文轨开关" })).toHaveAttribute("aria-checked", "true");
  });

  it("applies bilingual configuration and keeps real-frame cover generation distinct from AI image success", async () => {
    const initial = subtitleCoverWorkspace();
    initial.subtitle_translation_capability = {
      available: true, model_id: "qwen-text",
      message: "英文翻译模型可用。", repair_action: null,
    };
    initial.cover_asset = {
      id: "cover-1", workspace_id: "local", task_id: initial.task.id, timeline_version_id: "timeline-version-6",
      source_video_ref: "artifact:video:v6", frame_time_seconds: 2.4, title: "别只讲概念",
      safe_area: { top: 144, right: 72, bottom: 180, left: 72 },
      layout: { title_position: "bottom", text_align: "center" }, generation_method: "video_frame",
      model_or_engine: "ffmpeg", status: "valid", artifact_ref: "artifact:cover:v6",
      transform_id: "transform:cover:v6", failure_reason: null, impact: null, repair_action: null,
      created_at: initial.task.created_at, updated_at: initial.task.updated_at,
    };
    mocks.exactEdit
      .mockResolvedValueOnce({ operation: { id: "subtitle-language-edit" } })
      .mockResolvedValueOnce({ operation: { id: "cover-generation" } });

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：已完成"));
    fireEvent.click(screen.getByRole("button", { name: "生成英文字幕" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(1, "task-1", {
      operation_type: "ConfigureSubtitleTrack",
      track_id: "subtitle-track-zh",
      target_language: "en-US",
    }));
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    await waitFor(() => expect(screen.getByRole("button", { name: "从画面生成封面" })).toBeEnabled());
    fireEvent.click(screen.getByRole("tab",{name:"字幕"}));expect(screen.getByRole("switch", { name: "中文轨开关" })).toBeEnabled();fireEvent.click(screen.getByRole("tab",{name:"封面"}));

    fireEvent.change(screen.getByLabelText("封面画面时间"), { target: { value: "4.2" } });
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "真实运行" } });
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    const positions = within(screen.getByRole("group", { name: "封面标题位置" }));
    expect(positions.getByRole("button", { name: "底部" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(positions.getByRole("button", { name: "中部" }));
    fireEvent.click(screen.getByRole("button", { name: "从画面生成封面" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenNthCalledWith(2, "task-1", {
      operation_type: "GenerateCoverFromFrame",
      frame_time_seconds: 4.2,
      title: "真实运行",
      title_position: "center",
    }));
    const inspector = screen.getByLabelText("当前选择");
    expect(inspector).toHaveTextContent("选帧能力可用 · ffmpeg+Pillow");
    expect(inspector).toHaveTextContent("AI 生图不可用 · 未配置模型");
    expect(inspector).not.toHaveTextContent("AI 生图已生成");
    expect(screen.queryByRole("button", { name: /AI 生成封面/ })).not.toBeInTheDocument();
  });

  it("applies a cover title position change and refreshes persisted layout", async () => {
    const initial = workspaceWithCover();
    const confirmed = structuredClone(initial);
    confirmed.cover_asset!.id = "cover-center";
    confirmed.cover_asset!.layout.title_position = "center";
    mocks.exactEdit.mockResolvedValue({ operation: { id: "cover-position-edit" } });
    mocks.getWorkspace.mockResolvedValue(confirmed);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：已完成"));
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    const positions = within(screen.getByRole("group", { name: "封面标题位置" }));
    fireEvent.click(positions.getByRole("button", { name: "中部" }));
    expect(screen.getByText(/选帧、标题或构图待生成/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "从画面生成封面" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith(initial.task.id, {
      operation_type: "GenerateCoverFromFrame", frame_time_seconds: initial.cover_asset!.frame_time_seconds,
      title: initial.cover_asset!.title, title_position: "center",
    }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith(initial.task.id));
    await waitFor(() => expect(screen.queryByText(/选帧、标题或构图待生成/)).not.toBeInTheDocument());
    expect(positions.getByRole("button", { name: "中部" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("当前选择")).toHaveTextContent("画面中部");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps bilingual unavailable with one visible repair instruction", async () => {
    const initial = subtitleCoverWorkspace();
    mocks.getRuntimeSettings.mockResolvedValue({ status: { llm: { configured: false, provider: "minimax", model: "MiniMax-M3" } } });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("字幕与封面：待补封面"));

    expect(screen.getByRole("switch", { name: "英文轨开关" })).toBeDisabled();
    expect(await screen.findByRole("link", { name: "连接文字模型" })).toHaveAttribute("href", "/settings");
    fireEvent.click(screen.getByRole("tab",{name:"封面"}));
    fireEvent.change(screen.getByLabelText("封面标题"), { target: { value: "真实运行" } });
    expect(screen.getByRole("button", { name: "从画面生成封面" })).toBeEnabled();
  });

  it("applies clip trimming through the exact edit endpoint and refreshes version facts", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const refreshed = workspace(
      { ...task, current_timeline_version_id: "timeline-version-3", timeline_sequence: 3 },
      { ...montage, clips: [{ ...montage.clips[0], source_start_seconds: 1.5 }], message: "镜头已更新，需要重新渲染。" },
    );
    mocks.exactEdit.mockResolvedValue({ montage: { ...montage, message: "不直接采用编辑响应" }, operation: { id: "operation-1" } });
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(refreshed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await expectTaskVersion(/版本 2$/);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const trimField = screen.getByLabelText("镜头 1 裁剪起点");
    trimField.focus(); fireEvent.change(trimField, { target: { value: "1.5" } });
    mocks.exactEdit.mockResolvedValue({ operation: { id: "trim-edit" } });
    fireEvent.keyDown(trimField, { key: "Enter" });

    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "TrimClip",
      clip_id: "clip-1",
      source_start_seconds: 1.5,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    await expectTaskVersion(/版本 3$/);
    expect(screen.getByLabelText("源素材预览")).toBeInTheDocument();
    expect(screen.queryByText("不直接采用编辑响应")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: "切开镜头" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("镜头分割位置"), { target: { value: "1.2" } });
    fireEvent.click(screen.getByRole("button", { name: "切开镜头" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "SplitClip", clip_id: "clip-1", split_time_seconds: 1.2,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "删除镜头…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "复制镜头" }));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "DuplicateClip", clip_id: "clip-1",
    }));
    fireEvent.change(screen.getByLabelText("画面倍速"), { target: { value: "1.5" } });
    fireEvent.blur(screen.getByLabelText("画面倍速"));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeClipSpeed", clip_id: "clip-1", playback_rate: 1.5,
    }));
    fireEvent.change(screen.getByLabelText("关联倍速"), { target: { value: "2" } });
    fireEvent.blur(screen.getByLabelText("关联倍速"));
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith("task-1", {
      operation_type: "ChangeLinkedSpeed", clip_id: "clip-1", playback_rate: 2,
    }));
  });

  it("applies one recorded narration replacement and refreshes its version", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      audio_timeline: { format_version: 1 as const, tracks: [{ id: "audio-narration", name: "旁白", role: "narration" as const, gain_db: 0, muted: false, locked: false, duck_under_voice: false, clips: [] }] },
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const refreshed = workspace({ ...task, current_timeline_version_id: "timeline-version-3", timeline_sequence: 3 }, {
      ...montage, narration_reference: "recording:rec-1", message: "旁白已替换，需要重新渲染。",
    });
    mocks.saveRecording.mockResolvedValue({ source_ref: "recording:rec-1", artifact_id: "artifact-rec-1", audio_url: "/api/recordings/rec-1.wav", duration_ms: 3000, applied: false });
    mocks.previewAudioEdit.mockResolvedValue({
      intent: { id: "intent-audio", workspace_id: "local", task_id: task.id, raw_command: "用本机录音替换整条旁白", context_stage: "montage", selected_object_refs: [], playhead_ms: null, intent_type: "audio_edit", scope: {}, constraints: {}, confidence: 1, status: "compiled", created_at: task.created_at },
      proposal: { id: "proposal-replace-narration", workspace_id: "local", task_id: task.id, intent_id: "intent-audio", summary: "用本机录音替换整条旁白", target_refs: ["timeline:timeline-version-2"], operation: { operation_type: "EditAudioTimeline", payload: {} }, invalidated_refs: ["artifact:audio:v2"], preview: { before: { ref: "timeline:timeline-version-2", label: "当前版本", detail: "已保存" }, after: { ref: "timeline:proposal", label: "待确认版本", detail: "替换旁白" } }, external_effects: ["未确认前不写入时间线。"], status: "proposed" as const, created_at: task.created_at, updated_at: task.updated_at },
    });
    mocks.confirmEdit.mockResolvedValue({ operation: { id: "recording-replacement" } });
    mocks.getWorkspace.mockResolvedValueOnce(initial).mockResolvedValue(refreshed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "电脑录音替换旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    await waitFor(() => expect(mocks.saveRecording).toHaveBeenCalledWith("task-1", expect.any(Blob)));
    await waitFor(() => expect(mocks.previewAudioEdit).toHaveBeenCalledWith("task-1", {
      base_version_id: "timeline-version-2",
      action: "replace_narration",
      target_id: "audio-narration",
      values: { source_ref: "recording:rec-1" },
      raw_command: "用本机录音替换整条旁白",
    }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledExactlyOnceWith("task-1", "proposal-replace-narration"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await expectTaskVersion(/版本 3$/);
    expect(screen.queryByRole("button", { name: "应用到草稿" })).not.toBeInTheDocument();
  });

  it("keeps a first task recording unapplied until the user explicitly adopts it", async () => {
    const task = { ...baseTask, stage_status: "not_started" as const, status: "running" as const };
    const initial = workspace(task);
    initial.scripts = [{
      id: "script-1", task_id: task.id, run_id: "run-1", topic_id: "topic-1", title: "已确认文案",
      language: "zh-CN", selection_status: "confirmed", created_at: task.created_at,
      updated_at: task.updated_at, confirmed_at: task.updated_at,
      draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections: [{ id: "section-1", title: "开场", content: "当前文案",
        visual_prompt: "现场", evidence_reference_ids: [] }],
    }];
    const recording = {
      source_ref: "recording:first", artifact_id: "artifact-first",
      audio_url: "/api/recordings/first.wav", duration_ms: 2400, applied: false,
    };
    mocks.listRecordings.mockResolvedValue({ items: [] });
    mocks.saveRecording.mockResolvedValue(recording);
    mocks.adoptRecording.mockResolvedValue({ source_kind: "recorded" });
    mocks.getWorkspace.mockResolvedValue(initial);

    stubLoadedScriptWindows(initial.scripts);
    initial.scripts=initial.scripts.map(script=>({...script,sections_loaded:false,sections:null}));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "录一段旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    expect(await screen.findByText("已保存 · 尚未采用")).toBeInTheDocument();
    expect(mocks.adoptRecording).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "采用这段录音" }));
    await waitFor(() => expect(mocks.adoptRecording).toHaveBeenCalledExactlyOnceWith("task-1", {
      script_id: "script-1", source_section_id: "section-1", source_ref: "recording:first",
    }));
    expect(await screen.findByText(/本机录音已采用为当前文案的配音来源/)).toBeInTheDocument();
  });

  it("surfaces an honest recording error and never previews when the upload fails", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-2",
      timeline_sequence: 2,
      can_undo: true,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const, cues: [], clips: [], duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.saveRecording.mockRejectedValue(new mocks.MockCreationPlanApiError(409, "stage_invalid", "当前任务阶段无法保存录音", null, "请回到剪辑阶段再重试。"));

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "电脑录音替换旁白" }));
    fireEvent.click(await screen.findByRole("button", { name: "提交录音" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("当前任务阶段无法保存录音");
    expect(alert).toHaveTextContent("请回到剪辑阶段再重试");
    expect(mocks.previewAudioEdit).not.toHaveBeenCalled();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
  });

  it("keeps each clip's quick draft through selection and a failed direct apply", async () => {
    mocks.showQuickInspector = true;
    const task = {
      ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, current_timeline_version_id: "timeline-version-2", timeline_sequence: 2,
      can_undo: true,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const,
      locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const, cues: [],
      clips: [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }],
      duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以渲染", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.exactEdit.mockRejectedValue(new Error("草稿暂不可用"));

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const quick = screen.getByRole("region", { name: "镜头 01 快速编辑" });
    const quickStart = within(quick).getByLabelText("镜头 1 快速裁剪起点");
    fireEvent.change(quickStart, { target: { value: "1.5" } });
    expect(screen.getByText("未应用")).toBeInTheDocument();
    expect(screen.queryByText(/版本 2$/)).not.toBeVisible();
    fireEvent.blur(quickStart);
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith(task.id, {
      operation_type: "TrimClip", clip_id: "clip-1", source_start_seconds: 1.5,
    }));
    expect(mocks.exactPreview).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("草稿暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    expect(screen.getByLabelText("镜头 2 快速裁剪起点")).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(1.5);
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1.5);
    await waitFor(() => expect(screen.getByLabelText("镜头 1 快速裁剪起点")).toHaveValue(1.5));
    fireEvent.click(within(quick).getByRole("button", { name: "换素材" }));
    fireEvent.click(within(quick).getByRole("button", { name: "查看候选" }));
    expect(mocks.openQuickDetails).toHaveBeenCalledOnce();
    mocks.undoTimeline.mockResolvedValue({});
    mocks.getWorkspace.mockResolvedValue(workspace(
      { ...task, current_timeline_version_id: "timeline-version-1", timeline_sequence: 1 },
      { ...montage, clips: [{ ...clip, source_start_seconds: 2 }, montage.clips[1]] },
    ));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(2));
  });

  it("offers an explicit version snapshot for a legacy storyboard without replacing its clips", async () => {
    const task = {
      ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, current_timeline_version_id: null,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "原故事板",
      status: "ready" as const, cues: [], clips: [{
        id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
        source_reference: "workspace://clip.mp4", source_start_seconds: 1,
        source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3,
        match_score: .9, match_reasons: ["已审核"], needs_confirmation: false,
        role: "b_roll" as const, locked: false, manually_selected: false,
      }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "保留原故事板", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.exactEdit.mockRejectedValueOnce(new mocks.MockCreationPlanApiError(
      409, "timeline_version_required", "当前故事板还没有可撤销的初始版本。",
    )).mockResolvedValue({ operation: { id: "trim-after-init" } });
    mocks.initializeTimeline.mockResolvedValue({ version_id: "timeline-version-1" });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const trimField = screen.getByLabelText("镜头 1 裁剪起点");
    trimField.focus(); fireEvent.change(trimField, { target: { value: "1.5" } });
    fireEvent.keyDown(trimField, { key: "Enter" });
    const initialize = await screen.findByRole("button", { name: "保留当前故事板并建立版本" });
    expect(mocks.initializeTimeline).not.toHaveBeenCalled();
    mocks.getWorkspace.mockResolvedValue(workspace(
      { ...task, current_timeline_version_id: "timeline-version-1", timeline_sequence: 1 }, montage,
    ));
    fireEvent.click(initialize);
    await waitFor(() => expect(mocks.initializeTimeline).toHaveBeenCalledWith(task.id));
    await expectTaskVersion(/版本 1$/);
    expect(screen.getByLabelText("镜头 1 裁剪起点")).toHaveValue(1.5);
    fireEvent.keyDown(screen.getByLabelText("镜头 1 裁剪起点"), { key: "Enter" });
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenLastCalledWith(task.id, {
      operation_type: "TrimClip", clip_id: "clip-1", source_start_seconds: 1.5,
    }));
  });

  it("uses typed operations for replace, reorder, lock, confirmation, and undo", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
      current_timeline_version_id: "timeline-version-4",
      timeline_sequence: 4,
      can_undo: true,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip-1.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["用途匹配", "时长满足"], needs_confirmation: true,
      role: "b_roll" as const, locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [
        { id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 },
        { id: "cue-2", source_section_id: "section-2", narration_segment_id: "narration-segment-2", text: "第二段旁白", duration_seconds: 3 },
      ],
      clips: [
        clip,
        { ...clip, id: "clip-2", cue_id: "cue-2", asset_id: "asset-2", segment_id: "segment-2", source_reference: "workspace://clip-2.mp4", timeline_start_seconds: 3, timeline_end_seconds: 6, needs_confirmation: false },
      ],
      duration_seconds: 6, low_confidence_clip_count: 1,
      narration_reference: "narration://artifact/audio-1", output_url: "/api/old-preview", subtitle_url: "/api/old-subtitles",
      message: "等待镜头确认", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    mocks.exactEdit.mockResolvedValue({ montage: { ...montage, message: "不直接采用 exact 响应" }, operation: { id: "operation-exact" } });
    mocks.undoTimeline.mockResolvedValue({ montage: { ...montage, message: "不直接采用 undo 响应" }, operation: { id: "operation-undo" } });
    mocks.getCandidates.mockResolvedValue([{
      asset_id: "asset-3", segment_id: "segment-3", file_name: "团队协作.mp4",
      start_seconds: 5, end_seconds: 8, description: "团队协作画面", score: .96, reasons: ["标签命中"],
    }]);
    mocks.getWorkspace.mockResolvedValue(initial);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    for (const [label, operation] of [
      ["向后移动", { operation_type: "ReorderClip", clip_id: "clip-1", position: 1 }],
      ["锁定镜头", { operation_type: "SetClipLock", clip_id: "clip-1", locked: true }],
      ["确认镜头", { operation_type: "ConfirmClip", clip_id: "clip-1" }],
    ] as const) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", operation));
      expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    }

    fireEvent.click(screen.getAllByRole("button", { name: "换素材" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    await chooseSelectValue(await screen.findByLabelText("替换为已审核镜头"), "asset-3:segment-3");
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ReplaceClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());

    // WIL-90：主轨 Insert —— clip_id 是锚点镜头，插在它之后。后端对每个操作都要求
    // clip_id，缺了直接 409，所以 payload 必须带且等于当前选中镜头。
    // 仍不带 duration（时长继承源片段 span）。
    fireEvent.click(screen.getAllByRole("button", { name: "添加片段" })[0]);
    await waitFor(() => expect(mocks.getCandidates).toHaveBeenCalledWith("task-1", "clip-1"));
    await chooseSelectValue(await screen.findByLabelText("插在当前镜头之后"), "asset-3:segment-3");
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "InsertClip", clip_id: "clip-1", asset_id: "asset-3", segment_id: "segment-3", source_start_seconds: 5,
    }));
    const insertCall = mocks.exactEdit.mock.calls.at(-1)?.[1];
    // 锚点必填：等于当前选中镜头 id，否则后端 409
    expect(insertCall?.clip_id).toBe("clip-1");
    expect(insertCall).not.toHaveProperty("duration_seconds");
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.undoTimeline).toHaveBeenCalledWith("task-1", {
      expected_timeline_version_id: "timeline-version-4", expected_timeline_sequence: 4, request_id: expect.any(String),
    }));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: "撤销" })).toBeEnabled());
    expect(screen.queryByText("不直接采用 exact 响应")).not.toBeInTheDocument();
    expect(screen.queryByText("不直接采用 undo 响应")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    await chooseSelectValue(screen.getAllByLabelText("画面显示方式")[0], "fit");
    await waitFor(() => expect(mocks.exactEdit).toHaveBeenCalledWith("task-1", {
      operation_type: "ChangeCrop", clip_id: "clip-1", fit_mode: "fit", crop_x: .5, crop_y: .5,
    }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
  });

  it("selects and previews persisted narration segments with API-owned audio URLs", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
    };
    const narration = {
      task,
      script_id: "script-1",
      audio_reference: "narration://artifact/full-audio",
      duration_seconds: 6,
      sample_rate: 24000,
      model_id: "qwen3-tts-12hz-0.6b-base-8bit",
      voice_profile_id: "voice-1",
      source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh" as const,
      pronunciation_version: "v1",
      segments: [
        {
          id: "narration-segment-1", source_section_id: "section-1", text: "第一段真实配音",
          audio_reference: "narration://artifact/segment-1", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
          source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
        },
        {
          id: "narration-segment-2", source_section_id: "section-2", text: "第二段真实配音",
          audio_reference: "narration://artifact/segment-2", audio_url: "/api/creation-tasks/task-1/narration/segments/narration-segment-2/audio",
          duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts-12hz-0.6b-base-8bit",
          voice_profile_id: "voice-1", voice_version: "v1", language: "zh" as const, pronunciation_version: "p1", status: "ready" as const,
          source_kind: "generated" as const, source_artifact_ref: null, source_sha256: null, source_script_digest: null,
        },
      ],
    };
    const initial = workspace(task);
    initial.narration = narration;
    const refreshed = workspace(task);
    refreshed.narration = {
      ...narration,
      segments: [{ ...narration.segments[0], status: "generating" as const }, narration.segments[1]],
    };
    mocks.previewEdit.mockResolvedValue({
      intent: {
        id: "intent-narration", workspace_id: "local", task_id: task.id,
        raw_command: "这句读慢一点", context_stage: "narration",
        selected_object_refs: ["narration:narration-segment-1"], playhead_ms: null,
        intent_type: "regenerate_narration_segment", scope: { narration_segment_id: "narration-segment-1" }, constraints: {},
        confidence: .97, status: "compiled", created_at: task.created_at,
      },
      proposal: {
        id: "proposal-narration", workspace_id: "local", task_id: task.id, intent_id: "intent-narration",
        summary: "只重做第一段配音。", target_refs: ["narration:narration-segment-1"],
        operation: { operation_type: "RegenerateNarrationSegment", payload: { narration_segment_id: "narration-segment-1", delivery_instruction: "读慢一点" } },
        invalidated_refs: ["narration:narration-segment-1", "artifact:preview:v4"],
        preview: {
          before: { ref: "narration:narration-segment-1", label: "当前配音", detail: "正常语速" },
          after: { ref: "narration:narration-segment-1", label: "重做配音", detail: "读慢一点" },
        },
        external_effects: ["其他配音段保持不变。"], status: "proposed",
        created_at: task.created_at, updated_at: task.updated_at,
      },
    });
    mocks.confirmEdit.mockResolvedValue({
      operation: { id: "operation-narration" },
      montage: null,
      narration: { ...narration, segments: [{ ...narration.segments[0], text: "不应直接显示" }, narration.segments[1]] },
    });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("配音：已完成"));
    expect(screen.getByText("第一段真实配音")).toBeInTheDocument();
    expect(screen.getByLabelText("试听配音 1")).toHaveAttribute("src", "/api/creation-tasks/task-1/narration/segments/narration-segment-1/audio");

    fireEvent.click(screen.getByRole("button", { name: "选择配音 1" }));
    expect(screen.getByRole("region", { name: /^助手对话：/ })).toBeInTheDocument();
    expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("第一段真实配音");
    expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("片段时长");
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "这句读慢一点" } });
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "这句读慢一点",
      context_stage: "narration",
      selected_object_refs: ["narration:narration-segment-1"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("artifact:preview:v4");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("其他配音段保持不变");

    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-narration"));
    await waitFor(() => expect(screen.getByLabelText("已选配音详情")).toHaveTextContent("正在生成"));
    expect(screen.queryByText("不应直接显示")).not.toBeInTheDocument();
  });

  it("previews and confirms a selected clip edit using only API facts", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const, status: "waiting_for_user" as const, progress: 70 };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: "/api/old-preview", subtitle_url: "/api/old-subtitles",
      message: "可以修改", created_at: task.created_at, updated_at: task.updated_at,
    };
    const preview = {
      intent: {
        id: "intent-1", workspace_id: "local", task_id: task.id,
        raw_command: "换成另一个镜头", context_stage: "montage",
        selected_object_refs: ["clip:clip-1"], playhead_ms: null,
        intent_type: "replace_clip", scope: { clip_id: "clip-1" }, constraints: {},
        confidence: .98, status: "compiled", created_at: task.created_at,
      },
      proposal: {
        id: "proposal-1", workspace_id: "local", task_id: task.id, intent_id: "intent-1",
        summary: "把镜头 1 替换为已审核的团队协作画面。",
        target_refs: ["clip:clip-1"],
        operation: { operation_type: "ReplaceClip" as const, payload: { clip_id: "clip-1", asset_id: "asset-2", segment_id: "segment-2", source_start_seconds: 5 } },
        invalidated_refs: ["artifact:preview:v1", "artifact:final:v1"],
        preview: {
          before: { ref: "segment:segment-1", label: "人物口播画面", detail: "asset-1 · 1.0 秒" },
          after: { ref: "segment:segment-2", label: "团队协作画面", detail: "asset-2 · 5.0 秒" },
        },
        external_effects: ["确认后只更新任务版本，不会立即调用模型或渲染。"],
        status: "proposed" as const, created_at: task.created_at, updated_at: task.updated_at,
      },
    };
    const refreshed = workspace(task, {
      ...montage,
      clips: [{ ...montage.clips[0], asset_id: "asset-2", segment_id: "segment-2", source_reference: "workspace://replacement.mp4", source_start_seconds: 5, source_end_seconds: 8 }],
      output_url: null,
      subtitle_url: null,
      message: "镜头已替换，需要重新渲染。",
    });
    mocks.previewEdit.mockResolvedValue(preview);
    mocks.confirmEdit.mockResolvedValue({
      operation: { id: "operation-1", status: "applied" },
      montage: { ...montage, message: "这个 confirm 响应不应直接显示" },
    });
    mocks.getWorkspace.mockResolvedValue(refreshed);

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace(task, montage)} />);
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    expect(player.currentTime).toBe(1.5);
    player.currentTime = 1.25;
    fireEvent.seeked(player);
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "换成另一个镜头" } });
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));

    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledWith("task-1", {
      raw_command: "换成另一个镜头",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-1"],
      playhead_ms: 1250,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("人物口播画面");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("团队协作画面");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("artifact:preview:v1");
    expect(screen.getByLabelText("变更预览")).toHaveTextContent("不会立即调用模型或渲染");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "返回详情" }));
    expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
    expect(mocks.confirmEdit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    await screen.findByLabelText("变更预览");
    fireEvent.click(screen.getByRole("button", { name: "应用到草稿" }));

    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-1"));
    await waitFor(() => expect(mocks.getWorkspace).toHaveBeenCalledWith("task-1"));
    expect(await screen.findByLabelText("源素材预览")).toBeInTheDocument();
    expect(screen.queryByText("这个 confirm 响应不应直接显示")).not.toBeInTheDocument();
  });

  it("prepares person commands and requires preview before confirmation", async () => {
    const task = {
      ...baseTask,
      stage: "montage" as const,
      stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const,
      progress: 70,
    };
    const clip = {
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip-1.mp4", source_start_seconds: 1, source_end_seconds: 4,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: ["已审核"], needs_confirmation: false,
      role: "b_roll" as const, locked: false, manually_selected: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [
        { id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 },
        { id: "cue-2", source_section_id: "section-2", narration_segment_id: "narration-segment-2", text: "第二段旁白", duration_seconds: 3 },
      ],
      clips: [
        clip,
        { ...clip, id: "clip-2", cue_id: "cue-2", person_render_id: "person-render-1", timeline_start_seconds: 3, timeline_end_seconds: 6 },
      ],
      duration_seconds: 6, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "可以修改", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    initial.narration = {
      task,
      script_id: "script-1",
      audio_reference: "narration://artifact/audio-1",
      duration_seconds: 6,
      sample_rate: 24000,
      model_id: "qwen3-tts",
      voice_profile_id: "voice-cc",
      source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      language: "zh",
      pronunciation_version: "v1",
      segments: [{
        id: "narration-segment-2", source_section_id: "section-2", text: "第二段旁白",
        audio_reference: "narration://artifact/segment-2", audio_url: "/api/audio-2",
        duration_seconds: 3, sample_rate: 24000, model_id: "qwen3-tts",
        voice_profile_id: "voice-cc", voice_version: "voice-v3", language: "zh",
        pronunciation_version: "v1", status: "ready",
        source_kind: "generated", source_artifact_ref: null, source_sha256: null, source_script_digest: null,
      }],
    };
    initial.person_renders = [{
      id: "person-render-1", task_id: task.id,
      person_profile_id: "person-cc", person_version: "person-v2",
      voice_profile_id: "voice-cc", voice_version: "voice-v3",
      narration_segment_id: "narration-segment-2", visual_reference: "workspace://cc.mp4",
      model_id: "musetalk-local", motion: "natural", expression: "neutral",
      status: "ready", duration_seconds: 3, alignment_evidence: "aligned",
      media_url: "/api/person-render-1", failure_reason: null, impact: null, repair_action: null,
      created_at: task.created_at, updated_at: task.updated_at,
    }];
    mocks.previewEdit
      .mockResolvedValueOnce({
        intent: {
          id: "intent-person-add", workspace_id: "local", task_id: task.id,
          raw_command: "这一段换成当前人物出镜", context_stage: "montage",
          selected_object_refs: ["clip:clip-1"], playhead_ms: null,
          intent_type: "change_person_segment", scope: { clip_id: "clip-1" }, constraints: {},
          confidence: .99, status: "compiled", created_at: task.created_at,
        },
        proposal: {
          id: "proposal-person-add", workspace_id: "local", task_id: task.id, intent_id: "intent-person-add",
          summary: "把镜头 1 换成当前人物出镜。", target_refs: ["clip:clip-1"],
          operation: { operation_type: "ChangePersonSegment", payload: { clip_id: "clip-1" } },
          invalidated_refs: ["clip:clip-1", "artifact:preview:v1"],
          preview: {
            before: { ref: "clip:clip-1", label: "当前镜头", detail: "补充画面" },
            after: { ref: "clip:clip-1", label: "人物口型", detail: "当前人物版本" },
          },
          external_effects: ["其他镜头与配音保持不变。"], status: "proposed",
          created_at: task.created_at, updated_at: task.updated_at,
        },
      })
      .mockResolvedValueOnce({
        intent: {
          id: "intent-person-regenerate", workspace_id: "local", task_id: task.id,
          raw_command: "这里只重做口型，其他都不要动", context_stage: "montage",
          selected_object_refs: ["clip:clip-2"], playhead_ms: null,
          intent_type: "regenerate_person_render", scope: { clip_id: "clip-2" }, constraints: {},
          confidence: .99, status: "compiled", created_at: task.created_at,
        },
        proposal: {
          id: "proposal-person-regenerate", workspace_id: "local", task_id: task.id, intent_id: "intent-person-regenerate",
          summary: "只重做镜头 2 的口型。", target_refs: ["clip:clip-2"],
          operation: { operation_type: "RegeneratePersonRender", payload: { clip_id: "clip-2" } },
          invalidated_refs: ["person-render:person-render-1", "artifact:preview:v1"],
          preview: {
            before: { ref: "person-render:person-render-1", label: "当前口型", detail: "person-v2" },
            after: { ref: "clip:clip-2", label: "重做口型", detail: "匹配当前配音" },
          },
          external_effects: ["其他镜头、文案和配音保持不变。"], status: "proposed",
          created_at: task.created_at, updated_at: task.updated_at,
        },
      });

    const availableView = renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    fireEvent.click(screen.getByRole("button", { name: "换成当前人物出镜" }));
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("这一段换成当前人物出镜");
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenCalledTimes(1));
    expect(mocks.previewEdit).toHaveBeenNthCalledWith(1, "task-1", {
      raw_command: "这一段换成当前人物出镜",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-1"],
      playhead_ms: null,
    });
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("把镜头 1 换成当前人物出镜");
    fireEvent.click(within(screen.getByRole("dialog", {name:"修改提案"})).getByRole("button", {name:"取消"}));

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    const details = screen.getByLabelText("已选镜头详情");
    expect(details).toHaveTextContent("人物版本person-cc · person-v2");
    expect(details).toHaveTextContent("声音版本voice-cc · voice-v3");
    expect(details).toHaveTextContent("当前模型musetalk-local");
    expect(details).not.toHaveTextContent("能力状态");
    fireEvent.click(screen.getByRole("button", { name: "重新对口型" }));
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("这里只重做口型，其他都不要动");
    expect(mocks.exactEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "精确预览修改" }));
    await waitFor(() => expect(mocks.previewEdit).toHaveBeenNthCalledWith(2, "task-1", {
      raw_command: "这里只重做口型，其他都不要动",
      context_stage: "montage",
      selected_object_refs: ["clip:clip-2"],
      playhead_ms: null,
    }));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("只重做镜头 2 的口型");

    availableView.unmount();
    mocks.previewEdit.mockClear();
    const unavailable = {
      ...initial,
      person_render_capability: {
        available: false,
        model_id: null,
        message: "本地 MuseTalk 运行环境尚未配置。",
        repair_action: "配置本地人物口型运行环境后重试。",
      },
    };
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={unavailable} />);

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    expect(screen.getByRole("button", { name: "换成当前人物出镜" })).toBeDisabled();
    const unavailableReason = screen.getByLabelText("人物口型不可用原因");
    expect(unavailableReason).toHaveTextContent("本地 MuseTalk 运行环境尚未配置。");
    expect(unavailableReason).toHaveTextContent("配置本地人物口型运行环境后重试。");

    fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
    fireEvent.click(screen.getByText("人物口型", { selector: "summary" }));
    expect(screen.getByRole("button", { name: "重新对口型" })).toBeDisabled();
    expect(mocks.previewEdit).not.toHaveBeenCalled();
  });
  it("previews original audio from the selected saved clip without immediately confirming", async () => {
    const initial=subtitleCoverWorkspace();
    initial.montage!.clips=[{id:"clip-original",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",
      source_reference:"workspace://video.mp4",source_start_seconds:2,source_end_seconds:8,
      timeline_start_seconds:0,timeline_end_seconds:3,match_score:1,match_reasons:[],needs_confirmation:false,
      role:"b_roll",locked:false,manually_selected:false}];
    const preview=exactPreview(initial.task,"EditAudioTimeline");
    preview.proposal.summary="添加原声，与现有音轨混合";
    mocks.previewEdit.mockResolvedValue(preview);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"),{target:{value:"3"}});
    expect(screen.getByRole("button",{name:"试听原声"})).toBeDisabled();
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"),{target:{value:"2"}});
    fireEvent.change(await readyInput("局部修改要求"),{target:{value:"原有未发送草稿"}});
    fireEvent.click(screen.getByRole("button",{name:"试听原声"}));
    await waitFor(()=>expect(mocks.previewEdit).toHaveBeenCalledWith("task-1",{
      raw_command:"提取当前镜头原声",context_stage:"montage",selected_object_refs:["clip:clip-original"],playhead_ms:1500}));
    await waitFor(() => expect(screen.getByLabelText("局部修改要求")).toHaveValue("原有未发送草稿"));
    expect(await screen.findByLabelText("变更预览")).toHaveTextContent("与现有音轨混合");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.exactEdit).not.toHaveBeenCalled();
  });

  it("shows a read-only materials list when the prepare stage is opened", async () => {
    mocks.getProductionMaterials.mockResolvedValue({
      assets: [
        {
          id: "asset-1",
          workspace_id: "local",
          file_name: "外景口播.mp4",
          path: "/materials/外景口播.mp4",
          size_bytes: 1024,
          modified_at: "2026-08-30T00:00:00Z",
          duration_seconds: 12.5,
          usage_role: "b_roll",
          suggested_usage_role: "b_roll",
          usage_role_confidence: 1,
          usage_role_reason: "已审核",
          review_status: "approved",
          use_state: "available",
          analysis_status: "ready",
          analysis_message: "",
          user_tags: ["外景", "口播"],
          segments: [],
          created_at: "2026-08-30T00:00:00Z",
          updated_at: "2026-08-30T00:00:00Z",
        },
      ],
      total_count: 1,
      limit: 100,
      offset: 0,
      has_more: false,
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace(baseTask)} />);
    fireEvent.click(getWorkspaceViewControl("资料准备：已完成"));
    expect(await screen.findByRole("heading", { name: "本次任务素材" })).toBeInTheDocument();
    expect(screen.getByText("外景口播.mp4")).toBeInTheDocument();
    expect(screen.getByText(/已解析/)).toBeInTheDocument();
    expect(screen.getByText("可用于创作")).toBeInTheDocument();
    expect(screen.queryByText("匹配 90%")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"查看候选素材"}));
    expect(screen.getByLabelText("候选素材预览")).toHaveAttribute("src", "/api/production/materials/asset-1/media");
    expect(mocks.getProductionMaterials).toHaveBeenCalledWith({ limit: 100, offset: 0 });
  });

  it("continues material pages and retries a failed list read without changing plan readiness", async () => {
    const asset = (id: string) => ({
      id,
      workspace_id: "local",
      file_name: id === "asset-1" ? "外景口播.mp4" : `${id}.mp4`,
      path: `/materials/${id}.mp4`,
      size_bytes: 1024,
      modified_at: "2026-08-30T00:00:00Z",
      duration_seconds: 12.5,
      usage_role: "b_roll" as const,
      suggested_usage_role: "b_roll" as const,
      usage_role_confidence: 1,
      usage_role_reason: "已审核",
      review_status: "approved" as const,
      use_state: "available" as const,
      analysis_status: "ready" as const,
      analysis_message: "",
      user_tags: ["外景"],
      segments: [],
      created_at: "2026-08-30T00:00:00Z",
      updated_at: "2026-08-30T00:00:00Z",
    });
    mocks.getProductionMaterials
      .mockRejectedValueOnce(new Error("临时读取失败"))
      .mockResolvedValueOnce({ assets: Array.from({ length: 100 }, (_, index) => asset(`other-${index}`)), total_count: 101, limit: 100, offset: 0, has_more: true })
      .mockResolvedValueOnce({ assets: [asset("asset-1")], total_count: 101, limit: 100, offset: 100, has_more: false });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspace(baseTask)} />);
    fireEvent.click(getWorkspaceViewControl("资料准备：已完成"));

    expect(await screen.findByRole("alert")).toHaveTextContent("素材列表读取失败：临时读取失败");
    expect(screen.getByText(/方案已记录 1 个检索候选/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试读取素材" }));
    await waitFor(() => expect(mocks.getProductionMaterials).toHaveBeenLastCalledWith({ limit: 100, offset: 0 }));
    fireEvent.click(await screen.findByRole("button", { name: /读取更多素材/ }));
    expect(await screen.findByText("外景口播.mp4")).toBeInTheDocument();
    expect(mocks.getProductionMaterials).toHaveBeenNthCalledWith(3, { limit: 100, offset: 100 });
    expect(screen.getByText(/方案已记录 1 个检索候选/)).toBeInTheDocument();
  });

  it("shows an honest publish placeholder and does not claim the video is published", async () => {
    const initial = subtitleCoverWorkspace();
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl("发布与数据：在发布工作区处理"));
    expect(screen.getByRole("link", {name:"打开发布工作区"})).toHaveAttribute("href", "/history");
    expect(screen.getByText(/本任务页不显示平台结果或发布数据/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回到检查与输出" })).toBeInTheDocument();
    expect(screen.getByText("未返回发布结果")).toBeInTheDocument();
  });

  it("shows the blocked topic action and its repair without claiming a generation result", async () => {
    const task: CreationTask = { ...baseTask, stage: "topic", stage_status: "not_started", status: "waiting_for_user", stage_reason: null, stage_impact: null, stage_repair_action: null };
    const initial = workspace(task);
    const topics: CreationTaskTopicsResponse = { task, candidates: [], evidence_references: [], selected_topic_id: null, latest_run: null, route_preflight: { provider_kind: "minimax", execution_location: "cloud", model_id: "MiniMax-M3", endpoint_identity: "ep", connection_version: "1", availability: "succeeded", checked_at: task.created_at }, egress_preflight: { cloud_egress: true, source_count: 1, text_excerpt_count: 1, reason: null, impact: null, repair_action: null } };
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getTopics.mockResolvedValue(topics);
    vi.mocked(generateCreationTaskTopics).mockRejectedValueOnce(new mocks.MockCreationPlanApiError(409, "automation_paused", "本次自动化已暂停。", null, "重新开启安排后继续。"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={topics} initialWorkspace={initial} />);
    fireEvent.click(screen.getByRole("button", { name: "生成选题" }));
    const blocker = await within(screen.getByRole("main")).findByRole("alert");
    expect(blocker).toHaveTextContent("重新开启安排后继续。");
    await waitFor(() => expect(screen.getByRole("button", { name: "生成选题" })).toBeEnabled());
    expect(screen.queryByText("选题已确认，下一步生成文案。")).not.toBeInTheDocument();
  });

  it("keeps the actual provider persistence code in collapsed diagnostics and shows the repair first", async () => {
    const task: CreationTask = { ...baseTask, stage: "topic", stage_status: "failed", status: "failed",
      stage_reason: "provider_output_invalid", stage_impact: "未保存无效选题。", stage_repair_action: "核对资料引用后重试。" };
    const initial = workspace(task);
    mocks.getWorkspace.mockResolvedValue(initial);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const blocker = screen.getByRole("alert", { name: "选题失败" });
    expect(within(blocker).getByText("选题没有生成成功")).toBeVisible();
    expect(blocker).toHaveTextContent("核对资料引用后重试。");
    const diagnostic = within(blocker).getByText(/provider_output_invalid/);
    expect(diagnostic.closest("details")).not.toHaveAttribute("open");
    expect(diagnostic).not.toBeVisible();
  });

  it("keeps narration failure codes and repair teaching folded while preserving a real next action", async () => {
    const task: CreationTask = { ...baseTask, stage: "narration", stage_status:"failed", status:"failed",
      can_revise_script:true, stage_reason:"narration_generation_failed",
      stage_impact:"没有保存新的音频。", stage_repair_action:"文案预计时长超过创作方案上限，请先压缩并重新确认文案。" };
    const initial=workspace(task);mocks.getWorkspace.mockResolvedValue(initial);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const issue=screen.getByRole("alert",{name:"配音失败"});
    expect(within(issue).getByText("配音没有完成")).toBeVisible();
    expect(within(issue).getByText(/narration_generation_failed/)).not.toBeVisible();
    expect(within(issue).getByText(/后台处理原文/)).not.toBeVisible();
    expect(within(issue).getByText("诊断详情").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("button",{name:"修改文案"})).toBeEnabled();
    fireEvent.click(within(issue).getByText("诊断详情"));
    expect(within(issue).getByText(/narration_generation_failed/)).toBeVisible();
    expect(within(issue).getByText(/后台处理原文/)).toHaveTextContent(task.stage_repair_action!);
  });

  it("shows one execution status with folded original steps and time", async () => {
    const initial=workspace({...baseTask,stage_status:"running",status:"running",stage_reason:null});
    mocks.getWorkspace.mockResolvedValue(initial);mocks.previewExecution.mockResolvedValue(queuePreview());
    mocks.getExecution.mockResolvedValue({...queueStatus("running"),waiting_reason:null,
      stages:[{stage:"narration",status:"running",reason:'{"source":"local-worker"}',at:baseTask.updated_at}]});
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    const panel=await productionPanel();
    expect(within(panel).getAllByText("后台制作中")).toHaveLength(1);
    const raw=within(panel).getByText(/local-worker/);
    expect(raw).not.toBeVisible();
    expect(within(panel).getByText("步骤与后台记录").closest("details")).not.toHaveAttribute("open");
    expect(within(panel).getByRole("button",{name:"停止制作"})).toBeEnabled();
    expect(raw.closest("details")).toHaveTextContent("最近更新");
  });

  it("edits the topic title, requires a non-empty title, and confirms the selected topic", async () => {
    const topicTask: CreationTask = {
      ...baseTask,
      stage: "topic",
      stage_status: "waiting_for_user",
      status: "waiting_for_user",
      progress: 5,
      recovery_point: "",
      stage_reason: null,
      stage_impact: null,
      stage_repair_action: null,
    };
    const topics: CreationTaskTopicsResponse = {
      task: topicTask,
      candidates: [
        { id: "topic-1", run_id: "run-1", title: "用三件小事讲清断舍离", angle: "从生活场景切入", audience: "想整理生活的人", outline: ["开场", "三件事", "收尾"], score: 92, selection_status: "unselected", created_at: topicTask.created_at },
        { id: "topic-2", run_id: "run-1", title: "一分钟复盘我的周计划", angle: "效率视角", audience: "职场人", outline: ["开场", "复盘", "收尾"], score: 88, selection_status: "unselected", created_at: topicTask.created_at },
      ],
      evidence_references: [],
      selected_topic_id: "topic-1",
      latest_run: null,
      route_preflight: { provider_kind: "minimax", execution_location: "cloud", model_id: "MiniMax-M3", endpoint_identity: "ep", connection_version: "1", availability: "succeeded", checked_at: topicTask.created_at },
      egress_preflight: { cloud_egress: true, source_count: 1, text_excerpt_count: 1, reason: null, impact: null, repair_action: null },
    };
    const initial = workspace(topicTask);
    mocks.getWorkspace.mockResolvedValue({ ...initial, task: { ...topicTask, stage: "script" as const } });
    vi.mocked(confirmCreationTaskTopic).mockResolvedValue({...topics, candidates:topics.candidates.map(item=>item.id==="topic-1" ? {...item,title:"服务端保存的新标题"} : item)});
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={topics} initialWorkspace={initial} />);
    const input = screen.getByRole("textbox", { name: "编辑标题" });
    expect(input).toHaveValue("用三件小事讲清断舍离");
    fireEvent.change(input, { target: { value: "我的新标题" } });
    expect(input).toHaveValue("我的新标题");
    fireEvent.change(input, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "确认选题" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "我的新标题" } });
    fireEvent.click(screen.getByRole("button", { name: "确认选题" }));
    await waitFor(() => expect(confirmCreationTaskTopic).toHaveBeenCalledWith("task-1", "topic-1", {title:"我的新标题"}));
    expect(await screen.findByText(/选题「服务端保存的新标题」已确认/)).toBeInTheDocument();
  });


  it("keeps material and task references together and preserves the dialogue draft", async () => {
    mocks.withReferenceProvider = true;
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{id:"clip-selected",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    mocks.seedReference = {kind:"material",label:"旧素材参考",selection:{scope:"asset",asset_id:"old-material",expected_updated_at:"asset-v1"}};
    mocks.askAssistant.mockResolvedValue({reply:"已读取当前镜头",provider:"test",model:"test",edit_preview:null,script_draft:null});
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    const input = await readyInput("局部修改要求");
    fireEvent.click(await screen.findByRole("button",{name:"查看本轮 1 项引用"}));
    await screen.findByRole("button",{name:"取消素材引用：旧素材参考"});
    fireEvent.click(screen.getByRole("button",{name:"关闭引用列表"}));
    fireEvent.change(input,{target:{value:"把当前镜头改为完整显示"}});
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    fireEvent.click(screen.getByRole("button",{name:"查看本轮 2 项引用"}));
    expect(await screen.findByRole("button",{name:"取消素材引用：旧素材参考"})).toBeVisible();
    expect(screen.getByRole("button",{name:/^取消镜头引用：镜头 1/})).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"关闭引用列表"}));
    expect(mocks.setMaterialReference).not.toHaveBeenCalled();
    expect(input).toHaveValue("把当前镜头改为完整显示");
    await waitFor(()=>expect(screen.getByRole("button",{name:"发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    await waitFor(()=>expect(mocks.askAssistant).toHaveBeenCalledWith(initial.task.id,expect.objectContaining({message:"把当前镜头改为完整显示",context_stage:"montage",selected_object_refs:[],expected_reference_version:1,expected_timeline_version_id:initial.task.current_timeline_version_id}),expect.any(Function)));
    expect(mocks.materialTurn).not.toHaveBeenCalled();
    const conversationId=mocks.askAssistant.mock.calls[0][1].conversation_id;
    fireEvent.click(screen.getByRole("button",{name:"查看本轮 2 项引用"}));
    fireEvent.click(await screen.findByRole("button",{name:/^取消镜头引用：镜头 1/}));
    fireEvent.click(screen.getByRole("button",{name:"关闭引用列表"}));
    const selection={scope:"segment" as const,asset_id:"new-material",segment_id:"new-segment",expected_updated_at:"asset-v2",expected_segment_updated_at:"segment-v2"};
    act(()=>referenceAssistantMaterial({label:"主动选择的素材",selection}));
    await screen.findByRole("button",{name:"取消素材引用：主动选择的素材"});
    expect(screen.queryByRole("button",{name:"取消镜头引用：镜头 1"})).not.toBeInTheDocument();
    fireEvent.change(input,{target:{value:"给参考片段添加空镜头标签"}});
    await waitFor(()=>expect(screen.getByRole("button",{name:"发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    await waitFor(()=>expect(mocks.materialTurn).toHaveBeenCalledWith("给参考片段添加空镜头标签",conversationId,selection,expect.any(String),expect.any(Number),expect.any(Function),3));
    expect(mocks.askAssistant).toHaveBeenCalledTimes(1);
  });

  it("does not send a material snapshot if the same task clip is reselected during its draft checkpoint", async () => {
    mocks.withReferenceProvider = true;
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{id:"clip-selected",cue_id:"cue-1",asset_id:"asset-1",segment_id:"segment-1",source_reference:"workspace://clip.mp4",source_start_seconds:0,source_end_seconds:3,timeline_start_seconds:0,timeline_end_seconds:3,match_score:.9,match_reasons:[],needs_confirmation:false,role:"b_roll",locked:false,manually_selected:false}];
    mocks.seedReference={kind:"material",label:"参考素材",selection:{scope:"asset",asset_id:"material-pending"}};
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    const input=await readyInput("局部修改要求");
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    await waitFor(()=>expect(mocks.saveReference).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button",{name:"查看本轮 1 项引用"}));
    fireEvent.click(await screen.findByRole("button",{name:/^取消镜头引用：镜头 1/}));
    fireEvent.change(input,{target:{value:"给这条素材添加空镜头"}});
    await waitFor(()=>expect(screen.getByRole("button",{name:"发送"})).toBeEnabled());
    const state=mocks.assistantActions!.assistantState;
    const beginTurn=state.beginTurn;
    let finishCheckpoint:(()=>void)|undefined;
    vi.spyOn(state,"beginTurn").mockImplementationOnce(message=>{
      const turn=beginTurn(message);
      return {...turn,draftCheckpoint:turn.draftCheckpoint.then(version=>new Promise<number>(resolve=>{finishCheckpoint=()=>resolve(version);}))};
    });
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    await waitFor(()=>expect(finishCheckpoint).toBeDefined());
    fireEvent.click(screen.getByRole("button",{name:"选择镜头 1"}));
    await act(async()=>finishCheckpoint!());
    expect(mocks.materialTurn).not.toHaveBeenCalled();expect(mocks.askAssistant).not.toHaveBeenCalled();
    expect(input).toHaveValue("给这条素材添加空镜头");
    fireEvent.click(screen.getByRole("button",{name:"查看本轮 1 项引用"}));
    expect(await screen.findByRole("button",{name:/^取消镜头引用：镜头 1/})).toBeVisible();
  });

  it("sends a frozen material reference from the task dialogue without an active editor target", async () => {
    const initial=subtitleCoverWorkspace();
    const selection={scope:"segment" as const,asset_id:"asset-context",segment_id:"segment-context",expected_updated_at:"asset-v1",expected_segment_updated_at:"segment-v1"};
    mocks.materialReference={selection,label:"参考片段"};
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    fireEvent.change(await readyInput("局部修改要求"), {target:{value:"解释这个参考的内容"}});
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    mocks.materialReference={selection:{...selection,expected_updated_at:"asset-v2"},label:"已换版本"};
    await waitFor(()=>expect(mocks.materialTurn).toHaveBeenCalledWith("解释这个参考的内容",expect.any(String),selection,expect.any(String),expect.any(Number),expect.any(Function),undefined));
    expect(mocks.askAssistant).not.toHaveBeenCalled();
    expect(mocks.exactEdit).not.toHaveBeenCalled();
  });

  it("shows subtitle visibility honestly and can redo the saved subtitle version",async()=>{
    const initial=workspaceWithCover();initial.task.can_redo=true;initial.subtitle_tracks[0].style.enabled=false;
    mocks.getWorkspace.mockResolvedValue(initial);mocks.redoTimeline.mockResolvedValue(initial);
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    expect(screen.getAllByText(/当前成片已关闭字幕/)).toHaveLength(2);
    expect(screen.queryByText("中文轨已就绪")).not.toBeInTheDocument();
    fireEvent.click(getWorkspaceViewControl(/^字幕与封面：/));
    fireEvent.click(screen.getByRole("button",{name:"重做"}));
    await waitFor(()=>expect(mocks.redoTimeline).toHaveBeenCalledWith("task-1", {
      expected_timeline_version_id: "timeline-version-6", expected_timeline_sequence: 6, request_id: expect.any(String),
    }));
  });

  it("counts a valid English-only subtitle track in the review checklist",()=>{
    const initial=workspaceWithCover();initial.subtitle_tracks[0].language="en-US";
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
    expect(screen.getByText("检查清单 · 2/3 项已就绪")).toBeInTheDocument();
    expect(screen.getAllByText(/英文轨已就绪/)).toHaveLength(2);
    expect(screen.queryByText("中文字幕轨已就绪")).not.toBeInTheDocument();
  });
  it("shows an honest read-only review checklist and drops the blanket pass claim", () => {
    const initial = workspaceWithCover();
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    expect(screen.getByText("检查清单 · 2/3 项已就绪")).toBeInTheDocument();
    expect(screen.getByText("中文轨已就绪")).toBeInTheDocument();
    expect(screen.getByText("讲解与音乐音量")).toBeInTheDocument();
    expect(screen.getByText("需人工试听确认，当前无自动音量校验")).toBeInTheDocument();
    expect(screen.getAllByText("已就绪")).toHaveLength(2);
    expect(screen.queryByText("画面、声音和尺寸检查已通过。")).not.toBeInTheDocument();
    expect(screen.getByLabelText("剪辑成片预览")).toBeVisible();
  });

  // WIL-56 遗留：箭头叠加层选中后，编辑上下文芯片不能叫「标题」。
  it("labels a selected arrow overlay as 箭头, not 标题", async () => {
    const initial = subtitleCoverWorkspace();
    initial.montage!.clips = [{
      id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
      source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
      timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9,
      match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false,
    }];
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    initial.montage!.graphic_overlays = [{
      kind: "arrow", id: "overlay-arrow", x: 0.3, y: 0.4, name: "指向数据",
      purpose: "指出关键数字", start_seconds: 1, end_seconds: 3,
      position: "right-top", color: "#FFDD00",
    }, ...["片头标题", "片尾标题"].map((text,index)=>({
      kind:"text" as const,id:`title-${index}`,text,start_seconds:index,end_seconds:index+2,
      x:.2,y:.3,width:.6,font_size:32,font_family:"PingFang SC",color:"#FFFFFF",
    }))];
    mocks.previewEdit.mockResolvedValue(exactPreview(initial.task, "EditGraphicOverlay"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button", { name: "箭头1 · 指向数据" }));
    // 右侧面板标题用的是同一个编辑上下文标签
    expect(screen.getByRole("complementary", { name: "箭头1" })).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "标题1" })).not.toBeInTheDocument();
    // 会话标题应显示选中的实际对象与播放区间。
    expect(screen.getByRole("region", { name: /^助手对话：/ })).toBeInTheDocument();
    const player = screen.getByLabelText("剪辑成片预览");
    const tracks = screen.getByLabelText("配音与字幕时间范围");
    for (const [name,selection] of [["标题2 · 片头标题","标题2"],["标题3 · 片尾标题","标题3"]]) {
      fireEvent.click(within(tracks).getByRole("button",{name}));
      expect(screen.getByRole("complementary",{name:selection})).toBeInTheDocument();
      expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    }
  });

  // WIL-160：连续制作已成功，但剪辑改动让成片不再对应当前时间线（video_ready=false），
  // 面板此前只剩一行状态文字、零按钮，且 onStart 会命中同一幂等键返回旧作业。
  // 当前纯素材编辑版本经原 resume 通道申请 render Grant，不从旧单步渲染绕过。
  it.each(["queued", "blocked"])("confirms current timeline re-output through resume and preserves %s status", async (outcome) => {
    const task = {
      ...baseTask, stage: "narration" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, stage_reason: null, stage_impact: null,
      stage_repair_action: null, stage_retryable: null, progress: 75,
      current_timeline_version_id: "timeline-version-2", timeline_sequence: 2,
      montage_plan_id: "montage-1", output_ready: false,
    };
    const montage = {
      id: "montage-1", task_id: task.id, script_id: "script-1", script_title: "故事板",
      status: "ready" as const,
      cues: [{ id: "cue-1", source_section_id: "section-1", narration_segment_id: "narration-segment-1", text: "第一段旁白", duration_seconds: 3 }],
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: ["已审核"], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false }],
      duration_seconds: 3, low_confidence_clip_count: 0,
      narration_reference: "narration://artifact/audio-1", output_url: null, subtitle_url: null,
      message: "镜头审核已完成，可以重新渲染。", created_at: task.created_at, updated_at: task.updated_at,
    };
    const initial = workspace(task, montage);
    const preview = queuePreview();
    preview.requirements.presenter_share_target = 0;
    mocks.previewExecution.mockResolvedValue(preview);
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue({
      task_id: "task-1",
      job: { id: "job-1", kind: "creation_task_execution", status: "succeeded", progress: 0, failure_reason: null, impact: null, repair_action: null },
      stages: [], current_stage: null, waiting_reason: null, status_updated_at: "2026-10-01T00:00:00+00:00",
      video_ready: false, cover_status: "pending", cover_message: null,
    });
    if (outcome === "queued") {
      mocks.resumeExecution.mockImplementation(async () => {
        const queued = queueStatus("queued");
        mocks.getExecution.mockResolvedValue(queued);
        return queued;
      });
    } else {
      mocks.resumeExecution.mockRejectedValue(new mocks.MockCreationPlanApiError(409, null,
        "当前有效音轨组合尚未接入 CPU 成片预算"));
    }

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    // 先打开当前要求；详情里的真实状态仍有重新输出出口。
    await productionPanel();
    await waitFor(() => expect(screen.getByRole("region", { name: "连续制作" })).toHaveTextContent("成片产物需要检查"));

    let panel = screen.getByRole("region", { name: "连续制作" });
    expect(panel).toHaveTextContent("重新输出当前时间线");
    expect(panel).toHaveTextContent("这段就是实际要读的内容");
    expect(panel).not.toHaveTextContent("当前暂不支持已编辑时间线重新输出");
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看剪辑与已有产物" }));
    expect(screen.getByLabelText("镜头故事板")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /查看制作详情|核对制作要求/ }));
    panel = screen.getByRole("region", { name: "连续制作" });
    expect(screen.getByRole("region", { name: "连续制作" })).toHaveTextContent("成片产物需要检查");
    expect(screen.queryByRole("button", { name: "生成成片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认以上文案和要求，开始制作" })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" }));
    await waitFor(() => expect(mocks.resumeExecution).toHaveBeenCalledExactlyOnceWith("task-1"));
    if (outcome === "queued") {
      expect(await within(panel).findByText("已排队")).toBeInTheDocument();
      expect(panel).not.toHaveTextContent("成片已生成");
    } else {
      expect(await screen.findByRole("alert")).toHaveTextContent("当前有效音轨组合尚未接入 CPU 成片预算");
      expect(panel).not.toHaveTextContent("成片已生成");
    }
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.generateNarration).not.toHaveBeenCalled();
    expect(mocks.createMontage).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("rechecks the changed timeline before confirming re-output and preserves the new preflight blocker", async () => {
    const initial = subtitleCoverWorkspace();
    const changed = structuredClone(initial);
    changed.task.current_timeline_version_id = "timeline-version-7";
    changed.task.output_ready = false;
    changed.montage!.output_url = null;
    const done = queueStatus("succeeded");
    done.video_ready = true;
    mocks.getWorkspace.mockResolvedValue(initial);
    mocks.getExecution.mockResolvedValue(done);
    let rejectPreview!: (cause: Error) => void;
    mocks.previewExecution.mockResolvedValueOnce(queuePreview())
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectPreview = reject; }));
    mocks.undoTimeline.mockImplementation(async () => {
      mocks.getWorkspace.mockResolvedValue(changed);
      mocks.getExecution.mockResolvedValue({ ...done, video_ready: false });
      return {};
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
    await waitFor(() => expect(mocks.previewExecution).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    await waitFor(() => expect(mocks.previewExecution).toHaveBeenCalledTimes(2));
    fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
    fireEvent.click(screen.getByRole("button", { name: /查看制作详情|核对制作要求/ }));
    expect(screen.getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" })).toBeDisabled();
    expect(screen.queryByText("这段就是实际要读的内容")).not.toBeInTheDocument();
    await act(async () => rejectPreview(new mocks.MockCreationPlanApiError(503,
      "device_budget_unavailable", "配音预算未标定", null, "先标定本机配音资源预算，再重试。")));
    const budgetAlert = await screen.findByRole("alert");
    expect(budgetAlert).toHaveTextContent("这台设备还没有完成制作资源标定，暂时不能开始制作。");
    expect(within(budgetAlert).getByText("配音预算未标定")).not.toBeVisible();
    expect(screen.getByRole("button", { name: "确认以上文案和要求，仅重新输出当前剪辑" })).toBeDisabled();
    expect(mocks.resumeExecution).not.toHaveBeenCalled();
    expect(mocks.startExecution).not.toHaveBeenCalled();
    expect(mocks.renderMontage).not.toHaveBeenCalled();
  });

  it("lists historical timeline versions and shows an old version's own snapshot read-only", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, progress: 70, timeline_sequence: 2 };
    const initial = workspace(task, {
      ...subtitleCoverWorkspace().montage!,
      clips: [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1",
        source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3,
        timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: 1, match_reasons: [],
        needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false }],
    });
    const versions = {
      task_id: "task-1",
      current_version_id: "tv-2",
      items: [
        { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
          is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
        { id: "tv-2", sequence: 2, parent_version_id: "tv-1", created_at: "2026-10-02T02:00:00+00:00",
          is_current: true, shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      ],
    };
    mocks.listTimelineVersions.mockResolvedValue(versions);
    mocks.getTimelineVersion.mockResolvedValue({
      task_id: "task-1",
      version: { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
        is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
      shots: [{ index: 0, id: "clip-1", narration_text: "旧版本第一句", start_seconds: 0, end_seconds: 1.5, description: "旧镜头描述" }],
      narration_segments: [],
      subtitle_tracks: [],
      cover_asset: null,
      artifacts: [
        { kind: "video", status: "available",
          url: "/api/creation-tasks/task-1/timeline/versions/tv-1/artifacts/video", reason: null },
        { kind: "cover", status: "not_generated", url: null, reason: "该版本没有生成封面。" },
      ],
    });

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "历史时间线版本" })).not.toBeInTheDocument());
    expect(mocks.listTimelineVersions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
    const player = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    const playerSource = player.getAttribute("src");
    player.currentTime = 1.25;
    fireEvent.seeked(player);
    fireEvent.change(await readyInput("局部修改要求"), { target: { value: "保留这份未应用草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    const panel = await screen.findByRole("dialog", { name: "历史时间线版本" });
    expect(panel).toHaveAttribute("data-overlay-id");
    expect(screen.getByRole("main")).not.toContainElement(panel);
    expect(within(panel).getByRole("heading", { name: "历史时间线版本" })).toBeInTheDocument();

    // 版本序号、时间与「当前版本」标识都来自接口当前返回的版本列表
    expect(mocks.listTimelineVersions).toHaveBeenCalledWith("task-1");
    await waitFor(() => expect(within(panel).getByText(/第 2 版/)).toBeInTheDocument());
    expect(within(panel).getByText("当前版本")).toBeInTheDocument();
    expect(within(panel).getAllByText(/2 个镜头/)).toHaveLength(2);
    expect(within(panel).getByText(/1 条字幕轨/)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: /第 1 版/ }));
    await waitFor(() => expect(mocks.getTimelineVersion).toHaveBeenCalledWith("task-1", "tv-1"));

    // 旧版本按自己的快照显示，缺失产物说明原因而不是留空
    expect(await within(panel).findByText("旧版本第一句")).toBeInTheDocument();
    expect(within(panel).getByText("旧镜头描述")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "查看这一版的成片" })).toHaveAttribute(
      "href", "/api/creation-tasks/task-1/timeline/versions/tv-1/artifacts/video");
    expect(within(panel).getByText("该版本没有生成封面。")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /回滚|恢复|应用/ })).not.toBeInTheDocument();

    // 收起/恢复只隐藏同一浮卡，保留历史选择和滚动，也不重建当前播放器、选择或草稿。
    const body = within(panel).getByRole("region", { name: "历史时间线版本" }).parentElement!;
    body.scrollTop = 120;
    fireEvent.click(within(panel).getByRole("button", { name: "收起历史版本" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "历史时间线版本" })).not.toBeInTheDocument());
    expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
    expect(player.currentTime).toBe(1.25);
    expect(player).toHaveAttribute("src", playerSource);
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("局部修改要求")).toHaveValue("保留这份未应用草稿");
    // 隐藏期间真实列表有新版本：重开必须重读，不能沿用挂载时的当前标记。
    mocks.listTimelineVersions.mockResolvedValue({
      ...versions, current_version_id: "tv-3",
      items: [...versions.items.map(version => ({ ...version, is_current: false })),
        { ...versions.items[1], id: "tv-3", sequence: 3, parent_version_id: "tv-2", is_current: true }],
    });
    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    expect(screen.getByRole("dialog", { name: "历史时间线版本" })).toBe(panel);
    expect(body.scrollTop).toBe(120);
    expect(within(panel).getByRole("button", { name: /第 1 版/ })).toHaveAttribute("aria-current", "true");
    expect(within(panel).getByText("旧版本第一句")).toBeInTheDocument();
    expect(await within(panel).findByRole("button", { name: /第 3 版/ })).toHaveTextContent("当前版本");
    expect(within(panel).getByRole("button", { name: /第 2 版/ })).not.toHaveTextContent("当前版本");
    expect(within(panel).getByRole("button", { name: /第 1 版/ })).toHaveAttribute("aria-current", "true");
    expect(mocks.listTimelineVersions).toHaveBeenCalledTimes(2);
    expect(mocks.getTimelineVersion).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "历史时间线版本" })).not.toBeInTheDocument());

    // 查看、收起与重开都不触发写时间线或替换当前工作区。
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.undoTimeline).not.toHaveBeenCalled();
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(1); // Only the controlled task-entry read; history browsing adds no read.
  });

  it("keeps the detail bound to the still-selected version when an older response arrives late", async () => {
    const task = { ...baseTask, stage: "montage" as const, stage_status: "waiting_for_user" as const,
      status: "waiting_for_user" as const, progress: 70, timeline_sequence: 2 };
    const initial = workspace(task);
    mocks.listTimelineVersions.mockResolvedValue({
      task_id: "task-1",
      current_version_id: "tv-2",
      items: [
        { id: "tv-1", sequence: 1, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
          is_current: false, shot_count: 2, subtitle_track_count: 1, narration_segment_count: 1 },
        { id: "tv-2", sequence: 2, parent_version_id: "tv-1", created_at: "2026-10-02T02:00:00+00:00",
          is_current: true, shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      ],
    });
    const detailFor = (versionId: "tv-1" | "tv-2", sequence: number, narrationText: string) => ({
      task_id: "task-1",
      version: { id: versionId, sequence, parent_version_id: null, created_at: "2026-10-01T02:00:00+00:00",
        is_current: versionId === "tv-2", shot_count: 2, subtitle_track_count: 0, narration_segment_count: 1 },
      shots: [{ index: 0, id: `clip-${sequence}`, narration_text: narrationText, start_seconds: 0, end_seconds: 1.5, description: null }],
      narration_segments: [],
      subtitle_tracks: [],
      cover_asset: null,
      artifacts: [],
    });
    const oldDetail = detailFor("tv-1", 1, "旧版本第一句");
    const currentDetail = detailFor("tv-2", 2, "当前版本第一句");
    // 每次详情请求都挂起，由测试自己决定哪一版先返回（A 慢 / B 快）
    const pending = new Map<string, Array<(value: unknown) => void>>();
    mocks.getTimelineVersion.mockImplementation((_taskId: string, versionId: string) =>
      new Promise((resolve) => {
        const queue = pending.get(versionId) ?? [];
        queue.push(resolve);
        pending.set(versionId, queue);
      }));
    const settle = async (versionId: string, value: unknown) => {
      const resolve = pending.get(versionId)?.shift();
      expect(resolve).toBeTruthy();
      await act(async () => { resolve?.(value); });
    };

    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);

    fireEvent.click(screen.getByRole("button", { name: "历史时间线版本" }));
    const panel = await screen.findByRole("dialog", { name: "历史时间线版本" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: /第 2 版/ })).toBeInTheDocument());

    const firstButton = within(panel).getByRole("button", { name: /第 1 版/ });
    const secondButton = within(panel).getByRole("button", { name: /第 2 版/ });

    fireEvent.click(secondButton);
    await settle("tv-2", currentDetail);
    expect(await within(panel).findByText("当前版本第一句")).toBeInTheDocument();

    // 切到第 1 版时，上一版详情必须立刻消失，不能继续显示着等新响应
    fireEvent.click(firstButton);
    expect(within(panel).queryByText("当前版本第一句")).not.toBeInTheDocument();
    expect(within(panel).getByText("正在读取这一版…")).toBeInTheDocument();
    expect(firstButton).toHaveAttribute("aria-current", "true");

    // 第 1 版这次请求被挂起：快速改选第 2 版，让后发先至
    fireEvent.click(secondButton);
    expect(within(panel).queryByText("当前版本第一句")).not.toBeInTheDocument();
    await settle("tv-2", currentDetail);
    expect(await within(panel).findByText("当前版本第一句")).toBeInTheDocument();
    expect(secondButton).toHaveAttribute("aria-current", "true");

    // 迟到的第 1 版响应不能覆盖当前选中的第 2 版
    await settle("tv-1", oldDetail);
    expect(within(panel).queryByText("旧版本第一句")).not.toBeInTheDocument();
    expect(within(panel).getByText("当前版本第一句")).toBeInTheDocument();
    expect(secondButton).toHaveAttribute("aria-current", "true");

    // 切换查看旧版本始终只是读，没有任何写任务的调用
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    expect(mocks.undoTimeline).not.toHaveBeenCalled();
    expect(mocks.getWorkspace).toHaveBeenCalledTimes(1); // Only the controlled task-entry read; history browsing adds no read.
  });

});

describe("作业进度读数", () => {
  it("shows known estimates as facts and keeps historical failures out of the current warning state", async () => {
    mocks.getJobProgress.mockResolvedValue({
      task_id: "task-1", total_count: 2,
      current: { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 40, attempt: 1,
        started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:05:00Z",
        elapsed_seconds: 300, failure_reason: null, impact: null, repair_action: null,
        wait_estimate_seconds: 120, processing_estimate_seconds: 540, estimate_basis: "最近同类作业记录" },
      items: [
        { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 40, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:05:00Z",
          elapsed_seconds: 300, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        { id: "job-0", kind: "creation_task_montage_render", status: "failed", progress: 10, attempt: 1,
          started_at: "2026-10-04T07:00:00Z", finished_at: "2026-10-04T07:01:00Z", updated_at: "2026-10-04T07:01:00Z",
          elapsed_seconds: 60, failure_reason: "输出磁盘写入失败", impact: "本次没有成片", repair_action: "检查磁盘空间后重新生成",
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
      ],
    });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const progress = await screen.findByRole("region", { name: "作业进度" });
    fireEvent.click(within(progress).getByRole("button", { name: "运行详情" }));
    const panel = await screen.findByRole("dialog", { name: "运行详情" });
    // 进度是作业自己写下的 40%，不是阶段序号；已耗时与更新时间来自记录。
    expect(within(panel).getAllByText(/成片输出 · 正在处理 · 进度 40% · 已耗时 5 分/).length).toBeGreaterThan(0);
    expect(within(panel).getByText(/预计等待：2 分 · 预计处理：9 分（依据：最近同类作业记录）/)).toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
    // 历史失败仍能按需查看，但不占任务首屏，也不把整块进度涂成告警。
    const history = within(panel).getByText("查看历史作业（2 条）");
    expect(history.closest("details")).not.toHaveAttribute("open");
    fireEvent.click(history);
    expect(within(panel).getAllByText(/失败原因：输出磁盘写入失败/).length).toBeGreaterThan(0);
  });

  it("marks only the current failed job as a warning", async () => {
    const failed = { id: "job-current", kind: "creation_task_montage_render" as const, status: "failed" as const,
      progress: 45, attempt: 1, started_at: "2026-10-04T08:00:00Z", finished_at: "2026-10-04T08:01:00Z",
      updated_at: "2026-10-04T08:01:00Z", elapsed_seconds: 60, failure_reason: "编码器退出",
      impact: "当前版本未更新", repair_action: "查看设备状态后重试", wait_estimate_seconds: null,
      processing_estimate_seconds: null, estimate_basis: null };
    mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", total_count: 1, current: failed, items: [failed] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const panel = await screen.findByRole("region", { name: "作业进度" });
    fireEvent.click(within(panel).getByText("运行详情"));
    const failure = within(await screen.findByRole("dialog", {name:"运行详情"})).getByRole("alert", { name: "当前作业失败信息" });
    expect(failure).toHaveTextContent("编码器退出");
    expect(failure).toHaveTextContent("当前版本未更新");
    expect(failure).toHaveTextContent("查看设备状态后重试");
  });

  it("labels estimates unknown only when the active job has no estimate or basis", async () => {
    const running = { id: "job-running", kind: "creation_task_montage_render" as const, status: "running" as const,
      progress: 12, attempt: 1, started_at: "2026-10-04T08:00:00Z", finished_at: null,
      updated_at: "2026-10-04T08:01:00Z", elapsed_seconds: 60, failure_reason: null,
      impact: null, repair_action: null, wait_estimate_seconds: null, processing_estimate_seconds: null,
      estimate_basis: null };
    mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", total_count: 1, current: running, items: [running] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const progress = await screen.findByRole("region", { name: "作业进度" });
    fireEvent.click(within(progress).getByRole("button", { name: "运行详情" }));
    const panel = await screen.findByRole("dialog", { name: "运行详情" });
    expect(within(panel).getByText(/预计等待：未知 · 预计处理：未知（尚无可信样本，无法预计）/)).toBeInTheDocument();
  });

  it("keeps completed jobs factual and folds long job history out of the primary view", async () => {
    const items = Array.from({ length: 32 }, (_, index) => ({
      id: `job-${index}`, kind: "creation_task_montage_render" as const, status: "succeeded" as const,
      progress: 100, attempt: 1, started_at: "2026-10-04T08:00:00Z", finished_at: "2026-10-04T08:01:00Z",
      updated_at: "2026-10-04T08:01:00Z", elapsed_seconds: 60, failure_reason: null, impact: null,
      repair_action: null, wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null,
    }));
    mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", total_count: items.length, current: items[0], items });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    const progress = await screen.findByRole("region", { name: "作业进度" });
    fireEvent.click(within(progress).getByRole("button", { name: "运行详情" }));
    const panel = await screen.findByRole("dialog", { name: "运行详情" });
    expect(within(panel).getAllByText(/成片输出 · 已完成 · 进度 100%/)[0]).toBeVisible();
    expect(within(panel).queryByText(/预计等待：|预计处理：/)).not.toBeInTheDocument();
    const history = within(panel).getByText("查看历史作业（32 条）");
    expect(history.closest("details")).not.toHaveAttribute("open");
    expect(history.closest("details")?.querySelectorAll("li")).toHaveLength(32);
    fireEvent.click(history);
    expect(history.closest("details")).toHaveAttribute("open");
    expect(within(panel).getAllByRole("listitem")).toHaveLength(32);
  });


  it("shows a concrete read failure and retries instead of silently showing nothing", async () => {
    mocks.getJobProgress.mockRejectedValue(new Error("本地服务未接通"));
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
    await screen.findByText("后台记录更新未完成");fireEvent.click(screen.getByRole("button",{name:"运行详情"}));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("最新一次刷新失败：本地服务未接通");
    // 重试会真的再读一次。
    mocks.getJobProgress.mockResolvedValue({
      task_id: "task-1", total_count: 1,
      current: { id: "job-2", kind: "creation_task_execution", status: "queued", progress: 0, attempt: 1,
        started_at: null, finished_at: null, updated_at: "2026-10-04T08:00:00Z",
        elapsed_seconds: null, failure_reason: null, impact: null, repair_action: null,
        wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
      items: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(()=>expect(screen.getByRole("region", { name: "作业进度" })).toHaveTextContent("整条制作 · 已安排，等待设备"));
  });

  it("re-reads after the execution poll reports a new job, even though the first read had no jobs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", current: null, items: [], total_count: 0 });
      mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
      renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
      const panel = await screen.findByRole("region", { name: "作业进度" });
      fireEvent.click(within(panel).getByRole("button", {name:"运行详情"}));
      expect(await screen.findByText("这个任务还没有后台作业记录。")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", {name:"关闭卡片"}));

      // 开始制作后，执行轮询读到新作业：面板随之重新读取，不停在空态。
      mocks.getJobProgress.mockResolvedValue({
        task_id: "task-1", total_count: 1,
        current: { id: "job-new", kind: "creation_task_execution", status: "running", progress: 5, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:00:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      mocks.getWorkspace.mockResolvedValue(subtitleCoverWorkspace());
      mocks.getExecution.mockResolvedValue({ task_id: "task-1",
        job: { id: "job-new", kind: "creation_task_execution", status: "running", progress: 5, failure_reason: null, impact: null, repair_action: null },
        stages: [{ stage: "render", status: "running", at: "2026-10-04T08:00:00Z" }], current_stage: "render",
        waiting_reason: null, status_updated_at: "2026-10-04T08:00:05Z", video_ready: false, cover_status: "pending", cover_message: null });
      await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
      await act(async () => { await Promise.resolve(); });
      expect(mocks.getJobProgress.mock.calls.length).toBeGreaterThan(1);
      await screen.findByText(/连续制作：后台制作中/);
      fireEvent.click(within(panel).getByRole("button",{name:"运行详情"}));expect(await screen.findByRole("dialog",{name:"运行详情"})).toHaveTextContent("进度 5%");
    } finally { vi.useRealTimers(); }
  });

  it("keeps the last reading and offers the same retry when a later refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.getJobProgress.mockResolvedValueOnce({
        task_id: "task-1", total_count: 1,
        current: { id: "job-1", kind: "creation_task_montage_render", status: "running", progress: 10, attempt: 1,
          started_at: "2026-10-04T08:00:00Z", finished_at: null, updated_at: "2026-10-04T08:00:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      mocks.getExecution.mockResolvedValue({ task_id: "task-1", job: null, stages: [], video_ready: false, cover_status: "pending", cover_message: null });
      renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={subtitleCoverWorkspace()} />);
      const panel = await screen.findByRole("region", { name: "作业进度" });
      expect(within(panel).getByText(/成片输出 · 正在处理/)).toBeInTheDocument();

      // 作业仍在执行，这次面板的重新读取失败：保留上次读数并给出同一个重试动作。
      mocks.getJobProgress.mockRejectedValueOnce(new Error("本地服务未接通"));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      await screen.findByText("后台记录更新未完成");fireEvent.click(screen.getByRole("button",{name:"运行详情"}));
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("最新一次刷新失败：本地服务未接通");
      expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();

      mocks.getJobProgress.mockResolvedValue({
        task_id: "task-1", total_count: 1,
        current: { id: "job-2", kind: "creation_task_montage_render", status: "running", progress: 20, attempt: 1,
          started_at: "2026-10-04T08:10:00Z", finished_at: null, updated_at: "2026-10-04T08:10:05Z",
          elapsed_seconds: 5, failure_reason: null, impact: null, repair_action: null,
          wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null },
        items: [],
      });
      fireEvent.click(screen.getByRole("button", { name: "重试" }));
      await within(panel).findByText(/成片输出 · 正在处理 · 进度 20%/);
    } finally { vi.useRealTimers(); }
  });
  it("shares the task and shows the visibility the API returned", async () => {
    const shared = { ...baseTask, visibility: "shared" as const };
    mocks.setCreationTaskVisibility.mockResolvedValue({ ...workspace(baseTask), task: shared });
    renderTaskSubject(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
    expect(mocks.setCreationTaskVisibility).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "共享给团队" }));

    await within(dialog).findByText("任务：团队共享");
    expect(mocks.setCreationTaskVisibility).toHaveBeenCalledWith("task-1", "shared");
  });

  it("keeps plan sharing independent inside the compact toolbar menu", async () => {
    mocks.getCreationPlan.mockResolvedValue({ visibility: "private" });
    mocks.setCreationPlanVisibility.mockResolvedValue({ visibility: "shared" });
    renderTaskSubject(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    await within(dialog).findByText("方案：仅自己可见");
    expect(mocks.setCreationPlanVisibility).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "方案共享给团队" }));

    await within(dialog).findByText("方案：团队共享");
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
    expect(mocks.setCreationPlanVisibility).toHaveBeenCalledWith(baseTask.plan_id, "shared");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps the current value and reports the reason when sharing fails", async () => {
    mocks.setCreationTaskVisibility.mockRejectedValue(new Error("本地服务拒绝了这次保存"));
    renderTaskSubject(<CreationTaskWorkspace initialTopics={null} initialTasks={[baseTask]} initialWorkspace={workspace(baseTask)} />);

    const trigger = screen.getByRole("button", { name: "共享设置" });
    trigger.focus(); fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "任务与方案共享" });
    fireEvent.click(within(dialog).getByRole("button", { name: "共享给团队" }));

    await within(dialog).findByRole("alert");
    expect(within(dialog).getByText("本地服务拒绝了这次保存")).toBeInTheDocument();
    // 没有保存成功就不能显示成已共享。
    expect(within(dialog).getByText("任务：仅自己可见")).toBeInTheDocument();
  });



it("switches review and production preparation without replacing the saved player or submitting either action", async () => {
  mocks.previewExecution.mockResolvedValue(queuePreview());
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspaceWithCover()} />);
  const result = screen.getByRole("tab", { name: "成片检查" });
  const prepare = screen.getByRole("tab", { name: "制作准备" });
  expect(result).toHaveAttribute("aria-selected", "true");
  const player = screen.getByLabelText("剪辑成片预览");
  expect(screen.queryByRole("button", { name: "确认以上文案和要求，开始制作" })).not.toBeInTheDocument();
  fireEvent.click(prepare);
  expect(prepare).toHaveAttribute("aria-selected", "true");
  expect(player).toBeVisible();
  expect(await productionAction("确认以上文案和要求，开始制作")).toBeVisible();
  expect(screen.queryByRole("button", { name: "保存到本地" })).not.toBeInTheDocument();
  fireEvent.keyDown(prepare, { key: "ArrowRight" });
  expect(result).toHaveFocus();
  expect(screen.getByLabelText("剪辑成片预览")).toBe(player);
  expect(player).toBeVisible();
  expect(mocks.startExecution).not.toHaveBeenCalled();
  expect(mocks.renderMontage).not.toHaveBeenCalled();
});

it("opens production preparation when the reviewed output no longer matches the current version", async () => {
  const initial = workspaceWithCover();
  initial.montage = { ...initial.montage!, status: "ready", output_url: null };
  initial.task.output_ready = false;
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
  expect(screen.getByRole("tab", { name: "制作准备" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("button", { name: "保存到本地" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("剪辑成片预览")).not.toBeInTheDocument();
  expect(mocks.renderMontage).not.toHaveBeenCalled();
});

  it("keeps raw technical failures in runtime details and offers ordinary language on the task", async () => {
    const current = { id: "job-current", kind: "creation_task_execution" as const, status: "failed" as const,
      progress: 0, attempt: 1, started_at: null, finished_at: null, updated_at: baseTask.updated_at,
      elapsed_seconds: null, failure_reason: "model_invalid_JSON", impact: "当前没有新成片",
      repair_action: "检查模型的 JSON 输出能力", wait_estimate_seconds: null, processing_estimate_seconds: null, estimate_basis: null };
    mocks.getJobProgress.mockResolvedValue({ task_id: "task-1", total_count: 1, current, items: [current] });
    renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={workspaceWithCover()} />);
    const progress = await screen.findByRole("region", { name: "作业进度" });
    await within(progress).findByText("后台有步骤需要处理");
    expect(progress).not.toHaveTextContent("JSON");
    fireEvent.click(within(progress).getByRole("button", { name: "运行详情" }));
    expect(await screen.findByRole("dialog", { name: "运行详情" })).toHaveTextContent("model_invalid_JSON");
  });

});

async function productionPanel() {
  const target = await waitFor(() => { const panel=screen.queryByRole("region",{name:"连续制作"}); if(panel) return panel; const entry=screen.queryByRole("button",{name:/查看制作详情|核对制作要求/}); expect(entry).not.toBeNull(); return entry!; });
  if (target.getAttribute("aria-label") === "连续制作") return target;
  fireEvent.click(target);return screen.findByRole("region",{name:"连续制作"});
}
async function productionAction(name: string) { const panel = await productionPanel(); return within(panel).findByRole("button", {name}); }
async function expectTaskVersion(value: RegExp) { const version=value.source.match(/版本 (\d+)/)![1]; await waitFor(()=>expect(document.querySelector("[data-timeline-version]")).toHaveAttribute("data-timeline-version",version)); }


describe("作品视图与真实版本",()=>{
 it("uses three editing views and keeps internal recovery in the progress menu",async()=>{
  const initial=subtitleCoverWorkspace();mocks.getWorkspace.mockResolvedValue(initial);
  render(<ActualCreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
  const nav=screen.getByRole("tablist",{name:"作品视图"});expect(within(nav).getAllByRole("tab")).toHaveLength(3);expect(screen.queryByRole("navigation",{name:"制作阶段"})).not.toBeInTheDocument();expect(screen.getByRole("button",{name:"制作进度"})).toBeInTheDocument();
  const first=within(nav).getByRole("tab",{name:/^视频剪辑：/});first.focus();const user=userEvent.setup();await user.keyboard("{End}");
  const last=within(nav).getByRole("tab",{name:/^文案：/});
  if (last.getAttribute("aria-disabled") !== "true") expect(last).toHaveFocus();
  const publish=getWorkspaceViewControl(/^发布与数据：/);fireEvent.click(publish);
  expect(screen.getByLabelText("剪辑成片预览")).toBeVisible();expect(screen.getByRole("link",{name:"打开发布工作区"})).toHaveAttribute("href","/history");
 });
 it("distinguishes the actual confirmed script and the unconfirmed latest draft",async()=>{
  const initial=subtitleCoverWorkspace();const original={id:"original-script",task_id:initial.task.id,run_id:"fixture-run",topic_id:"fixture-topic",title:"文案",language:"zh-CN" as const,selection_status:"confirmed" as const,created_at:initial.task.created_at,updated_at:initial.task.updated_at,confirmed_at:initial.task.updated_at,estimated_duration_seconds:3,draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections:[{id:"fixture-section",title:"正文",content:"固定正文",visual_prompt:"已有测试画面",evidence_reference_ids:[]}],warnings:[]};
  const confirmed={...original,id:"confirmed-script",title:"实际确认稿",selection_status:"confirmed" as const,sections:[{...original.sections[0],content:"已经确认的正文"}]};
  const draft={...confirmed,id:"new-draft",title:"实际新草稿",selection_status:"draft" as const,sections:[{...confirmed.sections[0],content:"尚未确认的新正文"}]};
  initial.scripts=[draft,confirmed];initial.task={...initial.task,confirmed_script_id:confirmed.id,can_revise_script:false};mocks.getWorkspace.mockResolvedValue(initial);
  stubLoadedScriptWindows(initial.scripts);
    initial.scripts=initial.scripts.map(script=>({...script,sections_loaded:false,sections:null}));
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);fireEvent.click(getWorkspaceViewControl("文案：有新草稿待确认"));
  expect(await screen.findByText("已经确认的正文")).toBeVisible();expect(screen.getByText("尚未确认的新正文")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab",{name:"新草稿"}));expect(screen.getByText("尚未确认的新正文")).toBeVisible();expect(screen.getByText("待确认草稿")).toBeVisible();
  expect(mocks.confirmScript).not.toHaveBeenCalled();
 });
 it("does not equate a zero plan target with the rendered person's actual share",async()=>{
  const initial=subtitleCoverWorkspace();initial.plan.presenter_share_target=0;initial.montage={...initial.montage!,presenter_share_actual:20.7};
  initial.narration={task:initial.task,script_id:initial.task.confirmed_script_id!,audio_reference:"fixture-audio",duration_seconds:3,sample_rate:24000,model_id:"fixture",voice_profile_id:null,language:"zh",pronunciation_version:"fixture",source_kind:"recorded",source_artifact_ref:"fixture-recording",source_sha256:null,source_script_digest:null,segments:[]};mocks.getWorkspace.mockResolvedValue(initial);
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);fireEvent.click(getWorkspaceViewControl("人物与口型：明确跳过 · 目标 0%"));
  expect(screen.getByText(/当前成片实际露脸 20.7%/)).toBeVisible();expect(screen.queryByText(/全素材画面|成片全部使用素材画面/)).not.toBeInTheDocument();expect(mocks.generatePersonRender).not.toHaveBeenCalled();
 });
 it("keeps a completed job and task versions in reachable runtime details",async()=>{
  const initial=subtitleCoverWorkspace();const job={id:"complete-job",kind:"creation_task_montage_render" as const,status:"succeeded" as const,progress:100,attempt:1,started_at:initial.task.created_at,finished_at:initial.task.updated_at,updated_at:initial.task.updated_at,elapsed_seconds:12,failure_reason:null,impact:null,repair_action:null,wait_estimate_seconds:null,processing_estimate_seconds:null,estimate_basis:null};
  mocks.getJobProgress.mockResolvedValue({task_id:initial.task.id,current:job,items:[job],total_count:1});mocks.getWorkspace.mockResolvedValue(initial);
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);await waitFor(()=>expect(mocks.getJobProgress).toHaveBeenCalled());
  expect(screen.queryByText(/进度 100%/)).not.toBeVisible();fireEvent.click(screen.getByRole("button",{name:"运行详情"}));const panel=await screen.findByRole("dialog",{name:"运行详情"});expect(panel).toHaveTextContent("进度 100%");
  fireEvent.click(within(panel).getByText("任务版本与时间"));expect(within(panel).getByText(`版本 ${initial.task.timeline_sequence}`)).toBeVisible();
 });
});

it.each(["control", "assistant"])("keeps a %s 202 preview pending and exposes apply only after its persistent result", async source => {
  const initial = subtitleCoverWorkspace();
  initial.montage!.clips = [{ id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: [], needs_confirmation: false, role: "b_roll", locked: false, manually_selected: false }];
  const job = { operation: "command_edit_preview", target_refs: ["clip:clip-1"], input_digest: "opaque", created_at: "2026-10-11T10:00:00Z", job_id: "preview-job", status: "queued", result_kind: "edit_preview", result_url: "/api/creation-tasks/task-1/media-jobs/preview-job", device_request_id: null, waiting_reason: "等待设备空闲", base_version_id: initial.task.current_timeline_version_id, result: null, failure_reason: null, repair_action: null };
  mocks.previewEdit.mockResolvedValue(job);
  mocks.askAssistant.mockResolvedValue({ reply: "已安排，预览未完成", provider: "test", model: "test", edit_preview: null, media_job: job, script_draft: null });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...job, status: "succeeded", result: exactPreview(initial.task, "ReplaceClip") }))));
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
  fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
  fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
  const input = await screen.findByLabelText("局部修改要求");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "换成另一个镜头" } });
  const send = screen.getByRole("button", { name: source === "assistant" ? "发送" : "精确预览修改" });
  await waitFor(() => expect(send).toBeEnabled());
  fireEvent.click(send);
  await screen.findByRole("button", { name: "取消预览" });
  expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "应用到草稿" })).not.toBeInTheDocument();
  expect(await screen.findByLabelText("变更预览", {}, { timeout: 3500 })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "应用到草稿" })).toBeEnabled();
  expect(mocks.confirmEdit).not.toHaveBeenCalled();
});

function recoveryWorkspace() {
  const initial = subtitleCoverWorkspace();
  const clip = { id: "clip-1", cue_id: "cue-1", asset_id: "asset-1", segment_id: "segment-1", source_reference: "workspace://clip.mp4", source_start_seconds: 0, source_end_seconds: 3, timeline_start_seconds: 0, timeline_end_seconds: 3, match_score: .9, match_reasons: [], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false };
  initial.montage!.clips = [clip, { ...clip, id: "clip-2", timeline_start_seconds: 3, timeline_end_seconds: 6 }];
  return initial;
}

function savedMediaJob(initial: ReturnType<typeof recoveryWorkspace>, jobId: string) {
  return { job_id: jobId, status: "succeeded" as const, operation: "audio_listen", result_kind: "audio_listen" as const,
    target_refs: [], input_digest: "opaque-server-digest", created_at: "2026-10-11T10:00:00Z",
    result_url: `/api/creation-tasks/task-1/media-jobs/${jobId}`, device_request_id: null, waiting_reason: null,
    base_version_id: initial.task.current_timeline_version_id, result: { audio_url: `/${jobId}.wav` }, failure_reason: null, repair_action: null };
}

it("recovers a registered image source with authenticated bytes and the existing layer editor without applying it", async () => {
  const initial=recoveryWorkspace();mocks.getWorkspace.mockResolvedValue(initial);
  mocks.realMediaJobReads=true;
  const source={source_ref:"artifact:png-source",artifact_id:"png-source",sha256:"a".repeat(64),size_bytes:128,
    name:"panel.png",width:64,height:64,applied:false};
  const job={...savedMediaJob(initial,"png-job"),operation:"media_script_preview",result_kind:"graphic_source",result:source};
  const createUrl=vi.fn().mockReturnValue("blob:registered-image"),revokeUrl=vi.fn();
  vi.stubGlobal("URL",class extends URL { static createObjectURL=createUrl; static revokeObjectURL=revokeUrl; });
  vi.mocked(getCreationTaskGraphicSources).mockResolvedValue([source]);
  const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==="POST")throw new Error("恢复不得提交");
    if(url.includes("/graphics/source/"))return new Response(new Uint8Array([137,80,78,71]),{headers:{"Content-Type":"image/png"}});
    return new Response(JSON.stringify(url.includes("?limit=")?{items:[job],next_cursor:null}:job));
  });vi.stubGlobal("fetch",fetch);
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
  fireEvent.click(screen.getByRole("button",{name:"运行详情"}));
  fireEvent.click(await screen.findByRole("button",{name:"恢复这次预览"}));
  expect(await screen.findByAltText("实际登记的图片来源")).toHaveAttribute("src","blob:registered-image");
  expect(within(screen.getByLabelText("恢复的图片来源")).getByText(/尚未应用到草稿或生成成片/)).toBeVisible();
  fireEvent.click(screen.getByRole("button",{name:"准备图片图层"}));
  fireEvent.click(screen.getByText("图文叠加"));
  expect(await screen.findByLabelText("已导入图片")).toBeVisible();
  expect(mocks.confirmEdit).not.toHaveBeenCalled();expect(mocks.exactPreview).not.toHaveBeenCalled();
  vi.mocked(getCreationTaskGraphicSources).mockResolvedValue([]);
});

it("offers bounded recovery after remount and reads only the explicitly chosen original job", async () => {
  const initial = recoveryWorkspace();
  mocks.getWorkspace.mockResolvedValue(initial);
  const newer = savedMediaJob(initial, "newer-job");
  const older = { ...savedMediaJob(initial, "older-job"), status: "queued", result: null, created_at: "2026-10-11T09:00:00Z" };
  const wrongBase = { ...newer, job_id: "old-base", base_version_id: "old-version" };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") throw new Error("恢复不得重新提交");
    return new Response(JSON.stringify(url.includes("?limit=")
      ? { items: [newer, older, wrongBase], next_cursor: null }
      : savedMediaJob(initial, "older-job")));
  });
  mocks.realMediaJobReads = true;
  vi.stubGlobal("fetch", fetch);
  const props = { initialTasks: [], initialTopics: null, initialWorkspace: initial };
  const first = renderTaskSubject(<CreationTaskWorkspace {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
  const candidates = await screen.findAllByRole("button", { name: "恢复这次预览" });
  expect(candidates).toHaveLength(2);
  expect(screen.queryByLabelText("恢复的音频试听")).not.toBeInTheDocument();
  fireEvent.click(candidates[1]);
  expect(await screen.findByLabelText("恢复的音频试听")).toHaveAttribute("src", "/older-job.wav");
  expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task-1/media-jobs/older-job", expect.anything());
  expect(fetch.mock.calls.every(call => call[1]?.method !== "POST")).toBe(true);
  expect(mocks.confirmEdit).not.toHaveBeenCalled();
  expect(mocks.previewEdit).not.toHaveBeenCalled();
  first.unmount();
  renderTaskSubject(<CreationTaskWorkspace {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
  await screen.findAllByRole("button", { name: "恢复这次预览" });
  expect(screen.queryByLabelText("恢复的音频试听")).not.toBeInTheDocument();
});

it("discards a recovering edit result after its trusted selection changes", async () => {
  const initial = recoveryWorkspace();
  mocks.getWorkspace.mockResolvedValue(initial);
  const preview = exactPreview(initial.task, "ReplaceClip");
  const candidate = { ...savedMediaJob(initial, "edit-job"), operation: "command_edit_preview", result_kind: "edit_preview", target_refs: ["clip:clip-1"], result: preview };
  let finish!: (response: Response) => void;
  const fetch = vi.fn((url: string) => url.includes("?limit=")
    ? Promise.resolve(new Response(JSON.stringify({ items: [candidate], next_cursor: null })))
    : new Promise<Response>(resolve => { finish = resolve; }));
  mocks.realMediaJobReads = true;
  vi.stubGlobal("fetch", fetch);
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
  fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
  fireEvent.click(screen.getByRole("button", { name: "选择镜头 1" }));
  fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "恢复这次预览" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(candidate.result_url, expect.anything()));
  fireEvent.click(screen.getByRole("button", { name: "选择镜头 2" }));
  await act(async () => finish(new Response(JSON.stringify(candidate))));
  expect(screen.queryByLabelText("变更预览")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "恢复这次预览" })).not.toBeInTheDocument();
  expect(mocks.confirmEdit).not.toHaveBeenCalled();
});

it.each(["denied", "stale"])("does not expose a recovered player after an API %s result", async reason => {
  const initial = recoveryWorkspace();
  mocks.getWorkspace.mockResolvedValue(initial);
  const candidate = savedMediaJob(initial, "member-job");
  mocks.realMediaJobReads = true;
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("?limit=")
    ? new Response(JSON.stringify({ items: [candidate], next_cursor: null }))
    : reason === "denied" ? new Response(JSON.stringify({ detail: "任务媒体作业不存在" }), { status: 404 })
    : new Response(JSON.stringify({ ...candidate, status: "stale", result: null }))));
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
  fireEvent.click(screen.getByRole("button", { name: "运行详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "恢复这次预览" }));
  if (reason === "denied") expect(await screen.findByRole("alert")).toHaveTextContent("任务媒体作业不存在");
  else expect(await screen.findByText("这次预览已不符合当前输入与选择，未恢复；请重新预览。")).toBeInTheDocument();
  expect(screen.queryByLabelText("恢复的音频试听")).not.toBeInTheDocument();
  expect(mocks.confirmEdit).not.toHaveBeenCalled();
});

it.each([false, true])("refreshes the saved task after actual adoption and wakes only its original chat (switched=%s)", async switched => {
  const initial = recoveryWorkspace();
  const preview = exactPreview(initial.task, "ChangeCrop");
  const reply = {reply: "修改待采用", provider: "test", model: "test", edit_preview: preview,
    script_draft: null, execution_action: null, execution_status: null, topic_candidates: null};
  mocks.getWorkspace.mockResolvedValue(initial);
  mocks.askAssistant.mockResolvedValue(reply);
  let finishAdoption!: (value: unknown) => void;
  mocks.confirmEdit.mockReturnValueOnce(new Promise(resolve => { finishAdoption = resolve; }));
  const view = renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial} />);
  fireEvent.click(getWorkspaceViewControl(/^视频剪辑：/));
  fireEvent.click(screen.getByRole("button", {name: "选择镜头 1"}));
  const input = await screen.findByLabelText("局部修改要求");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, {target: {value: "改好这个镜头后生成成片"}});
  await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: "发送"}));
  await screen.findByRole("button", {name: "应用到草稿"});
  const state = mocks.assistantActions!.assistantState;
  const saved = mocks.assistantConversations.get(state.conversation!.id)!;
  const root = saved.turns.at(-1)!;
  const goal = {original_turn_id: root.id, current_turn_id: root.id, status: "waiting_adoption",
    conversation_id: saved.id, workspace_id: saved.workspace_id, task_id: initial.task.id};
  const waiting = {...saved, turns: [...saved.turns.slice(0, -1), {...root,
    result: {...root.result, response: reply, goal_continuation: goal}}]};
  mocks.assistantConversations.set(saved.id, waiting);
  await act(async () => { await state.refresh(); });
  fireEvent.click(screen.getByRole("button", {name: "应用到草稿"}));
  await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith(initial.task.id, preview.proposal.id));
  if (switched) await act(async () => { await mocks.assistantActions!.assistantState.createNew(); });
  act(() => mocks.assistantActions!.assistantState.setDraftText(switched ? "新对话的草稿" : "下一条未发送草稿"));
  const currentChat = mocks.assistantActions!.assistantState.conversation!.id;
  const reads = vi.mocked(getAssistantConversation).mock.calls.length;
  const workspaceReads = mocks.getWorkspace.mock.calls.length;
  const latest = {...initial, task: {...initial.task, current_timeline_version_id: "adopted-version", timeline_sequence: 7}};
  mocks.getWorkspace.mockResolvedValue(latest);
  mocks.assistantConversations.set(saved.id, {...waiting, turns: [
    {...waiting.turns[0], result: {...waiting.turns[0].result,
      goal_continuation: {...goal, current_turn_id: "actual-auto-child", status: "running"}}},
    {id: "actual-auto-child", sequence: root.sequence + 1, user_message: "", assistant_text: "实际采用后的后台回合",
      status: "running", events: [], result: null, created_at: root.created_at, updated_at: root.updated_at},
  ]});
  await act(async () => { finishAdoption({operation: {id: "actual-applied-operation"}, montage: initial.montage}); });
  await waitFor(() => expect(mocks.getWorkspace.mock.calls.length).toBeGreaterThan(workspaceReads));
  await waitFor(() => expect(screen.getByLabelText("局部修改要求")).toBeEnabled());
  expect(mocks.assistantActions!.assistantState.draftText).toBe(switched ? "新对话的草稿" : "下一条未发送草稿");
  expect(mocks.assistantActions!.assistantState.conversation?.id).toBe(currentChat);
  if (switched) {
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    expect(vi.mocked(getAssistantConversation).mock.calls.length).toBe(reads);
    expect(screen.queryByText("实际采用后的后台回合")).not.toBeInTheDocument();
  } else {
    expect(vi.mocked(getAssistantConversation).mock.calls.length).toBe(reads + 1);
    expect(screen.getByText("实际采用后的后台回合")).toBeInTheDocument();
  }
  view.unmount();
});

const segmentApi=vi.hoisted(()=>({read:vi.fn(()=>new Promise(()=>{})),budget:vi.fn(()=>new Promise(()=>{})),native:vi.fn(()=>new Promise(()=>{})),content:vi.fn()}));
vi.mock('@/lib/api/task-media',async importOriginal=>({
  ...await importOriginal<typeof import('@/lib/api/task-media')>(),readCreationTaskSegments:segmentApi.read,readCreationTaskBudget:segmentApi.budget,readCreationTaskNativeOperations:segmentApi.native,readCreationTaskNativeArtifact:segmentApi.content,
}));

it('history pagination selects the original operation rather than the later edit',async()=>{
  const {TimelineVersionHistory}=await import('../TimelineVersionHistory');
  const onRestore=vi.fn();
  const original={id:'older',sequence:1,parent_version_id:null,created_at:'2026-10-01T00:00:00Z',is_current:false,
    shot_count:1,subtitle_track_count:0,narration_segment_count:1,operation_id:'original-operation',operation_type:'ReviseScriptSection'};
  mocks.listTimelineVersions.mockImplementation(async(taskId:string,before?:number)=>({task_id:taskId,current_version_id:'newer',
    items:before?[original]:[{...original,id:'newer',sequence:2,operation_id:'later-operation'}],
    total_count:2,truncated:before===undefined,next_before_sequence:before===undefined?2:null}));
  mocks.getTimelineVersion.mockResolvedValue({task_id:'task',version:original,shots:[],narration_segments:[],subtitle_tracks:[],cover_asset:null,artifacts:[]});
  render(<TimelineVersionHistory taskId="task" open currentVersionId="newer" onRestore={onRestore}/>);
  fireEvent.click(await screen.findByRole('button',{name:'更早版本'}));
  fireEvent.click(await screen.findByRole('button',{name:/第 1 版/}));
  fireEvent.click(await screen.findByRole('button',{name:'撤销这项修改'}));
  expect(onRestore).toHaveBeenCalledWith('original-operation',false);
  expect(mocks.listTimelineVersions).toHaveBeenLastCalledWith('task',2);
});

it('segment preview keeps the playing version and presents unknown cost as pending',async()=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  const segment={task_id:'task',section_id:'first',ordinal:1,script_id:'script',base_version_id:'base',segment_version:'v1',
    title:'卖点',text:'适用于干燥地面，最高可达30米',source_text_sha256:'digest',source_references:[],locked_body_digest:null,
    adjacent_sections:[],narration_segment_id:'audio',audio:{kind:'audio',artifact_ref:'audio',status:'ready',duration_seconds:5},
    video:{kind:'video',artifact_ref:'video',sha256:'digest',segment_version:'v1',duration_seconds:5,url:'/segments/v1/video'},adopted:true};
  const pending={...segment,section_id:'second',ordinal:2,title:'后续段',text:'后续段正在制作',
    narration_segment_id:null,audio:null,video:null,adopted:false};
  const initial={task_id:'task',total_count:2,available_video_count:1,segments:[segment,pending],offset:0,truncated:false,revision_requests:{items:[],total_count:0,truncated:false}};
  segmentApi.read.mockResolvedValue(initial as never);
  segmentApi.budget.mockResolvedValue({task_id:'task',status:'authorization_scope_unbound',waiting_reason:null,scopes:[],authorization_coverage:'scope_unbound',
    resources:{settled_execution_seconds:2,settled_generated_bytes:1024,total_execution_seconds:null,total_generated_bytes:null,coverage:'partial',receipt_count:1},
    actual_cost:{status:'pending_verification',amount:null,currency:null,supplier_usage_refs:[],pricing_or_invoice_refs:[]},estimates:{eta_seconds:null,cost:null}} as never);
  render(<SegmentProductionPanel taskId="task"/>);
  await screen.findByText('1 / 2 段有声视频可播放');
  fireEvent.click(screen.getByRole('button',{name:'播放本段'}));
  const video=screen.getByLabelText('第 1 段有声预览');
  expect(video.getAttribute('src')).toContain('/segments/v1/video');
  segmentApi.read.mockResolvedValue({...initial,segments:[{...segment,segment_version:'v2',video:{...segment.video,segment_version:'v2',url:'/segments/v2/video'}},pending]} as never);
  fireEvent.click(screen.getByRole('button',{name:'刷新状态'}));
  await screen.findByText('本段已有新版本，当前预览保持原版本。');
  expect(video.getAttribute('src')).toContain('/segments/v1/video');
  fireEvent.click(screen.getByRole('button',{name:'查看新版本'}));
  expect(screen.getByLabelText('第 1 段有声预览').getAttribute('src')).toContain('/segments/v2/video');
  expect(screen.getByText('实际费用：待核对供应商用量或账单')).toBeTruthy();
  expect(screen.getByText('用量记录尚待核对，当前数字为已记录部分')).toBeTruthy();
});


it('native registration waits for real artifacts and previews candidates before the existing image adoption path',async()=>{
  const {NativeOperationsPanel}=await import('../creation-task/TaskJobProgress');
  const create=vi.fn().mockReturnValue('blob:native-candidate'),revoke=vi.fn();
  vi.stubGlobal('URL',class extends URL {static createObjectURL=create;static revokeObjectURL=revoke;});
  const artifact={artifact_id:'image-id',source_ref:'artifact:real-image',logical_ref:'artifact:real-image',sha256:'image-sha',
    size_bytes:128,media_type:'image/png',role:'image',preview_url:'/native-artifacts/image-id/content',adopted:false};
  const job={job_id:'register-job',request_id:'register-request',operation:'register_task_artifacts',status:'queued',
    process_state:'not_started',release_state:'not_acquired',artifacts:[],failure_reason:null,
    waiting_reason:'当前设备正在完成其他制作任务',repair_action:'等待当前执行释放资源后继续'};
  const page={task_id:'task',items:[job],total_count:1,offset:0,truncated:false};
  segmentApi.native.mockResolvedValue(page as never);segmentApi.content.mockResolvedValue(new Blob(['fixture']));
  const preview=vi.fn();
  const view=render(<NativeOperationsPanel taskId="task" adoptedRefs={[]} onPreviewImage={preview} canPrepare/>);
  await screen.findByRole("button",{name:/^工作产物与执行记录/});
  expect(screen.getByRole("button",{name:/^工作产物与执行记录/})).toHaveAttribute("aria-expanded","false");
  fireEvent.click(screen.getByRole("button",{name:/^工作产物与执行记录/}));
  await screen.findByText('产物登记 · 等待执行');
  expect(screen.getByText('等待原因：当前设备正在完成其他制作任务')).toBeTruthy();
  expect(screen.getByText('恢复方式：等待当前执行释放资源后继续')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'预览候选'})).toBeNull();
  segmentApi.native.mockResolvedValue({...page,items:[{...job,status:'failed',waiting_reason:null,
    failure_reason:'输出文件内容与登记摘要不一致',repair_action:'核对原输出文件后重新提交登记'}]} as never);
  fireEvent.click(screen.getByRole('button',{name:'刷新产物'}));
  await screen.findByText('失败原因：输出文件内容与登记摘要不一致');
  expect(screen.getByText('恢复方式：核对原输出文件后重新提交登记')).toBeTruthy();
  segmentApi.native.mockResolvedValue({...page,items:[{...job,status:'succeeded',waiting_reason:null,repair_action:null,
    process_state:'exited',release_state:'released',artifacts:[artifact]}]} as never);
  fireEvent.click(screen.getByRole('button',{name:'刷新产物'}));
  await screen.findByRole('button',{name:'预览候选'});
  expect(preview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'预览候选'}));
  await screen.findByAltText('实际登记的工作产物候选');
  expect(segmentApi.content).toHaveBeenCalledWith('task','image-id',expect.any(AbortSignal));
  fireEvent.click(screen.getByRole('button',{name:'准备图片图层'}));expect(preview).toHaveBeenCalledWith(artifact);
  view.rerender(<NativeOperationsPanel taskId="task" adoptedRefs={[artifact.source_ref]} onPreviewImage={preview} canPrepare/>);
  expect(screen.getByText('已在当前时间线使用；最终合成状态另行核对')).toBeTruthy();
  for (const [type,label] of [['video/mp4','实际登记的视频候选'],['audio/wav','实际登记的音频候选']]) {
    segmentApi.native.mockResolvedValue({...page,items:[{...job,status:'succeeded',artifacts:[{...artifact,artifact_id:type,media_type:type}]}]} as never);
    fireEvent.click(screen.getByRole('button',{name:'刷新产物'}));
    await waitFor(()=>expect(screen.queryByRole('button',{name:'准备图片图层'})).toBeNull());
    fireEvent.click(screen.getByRole('button',{name:'预览候选'}));
    await screen.findByLabelText(label);
  }
  const audio = screen.getByLabelText('实际登记的音频候选');
  const reads = segmentApi.content.mock.calls.length;
  fireEvent.click(within(screen.getByRole('dialog',{name:'工作产物与执行记录'})).getByRole('button',{name:'关闭卡片'}));
  await waitFor(()=>expect(screen.queryByRole('dialog',{name:'工作产物与执行记录'})).not.toBeInTheDocument());
  expect(audio).not.toHaveAttribute('src');
  fireEvent.click(screen.getByRole('button',{name:/^工作产物与执行记录/}));
  expect(await screen.findByLabelText('实际登记的音频候选')).toBe(audio);
  expect(segmentApi.content).toHaveBeenCalledTimes(reads);
  view.unmount();vi.unstubAllGlobals();
});

it('shows the actual review protocol failure and recovery without hiding them as generation failure', async()=>{
  const {TaskIssue}=await import('../creation-task/TaskIssue');
  const reason='核查报告工具参数不是有效JSON；尚不能判定正文是否通过。';
  const impact='本次送审稿未保存，原采用内容保留。';
  const repair='核对原报告格式后重新核查，不能改写正文试探。';
  render(<TaskIssue stage="script" code="script_review_invalid" reason={reason} impact={impact} repair={repair}/>);
  const alert=screen.getByRole('alert');
  for (const text of [reason,impact,repair]) {
    const visible=within(alert).getByText(text);
    expect(visible.closest('details')).toBeNull();
  }
});

function sectionReaderScript(id = 'reader-script'): import('@/lib/types').CreationTaskScript {
  return {id, task_id:'reader-task', run_id:'run', topic_id:'topic', title:'窗口文案', language:'zh-CN',
    draft_complete: true, sections_loaded: true as const, sections_total_count: 1, sections:[{id:'cached',title:'旧首屏',content:'不可用旧首屏替代窗口读取',visual_prompt:'旧画面',evidence_reference_ids:[]}],
    selection_status:'confirmed',created_at:'2026-10-24T00:00:00Z',updated_at:'2026-10-24T00:00:00Z',confirmed_at:'2026-10-24T00:00:00Z'};
}
function sectionReaderPage(script: import('@/lib/types').CreationTaskScript, after = 0, version = script.updated_at): import('@/lib/types').CreationTaskScriptSectionWindow {
  const items = Array.from({length:Math.min(20,45-after)},(_,index)=>{
    const ordinal=after+index+1;
    return {ordinal,digest:`digest-${ordinal}`,section:{id:`${script.id}-${ordinal}`,title:`窗口段落 ${ordinal}`,
      content:`窗口正文 ${script.id} ${ordinal}`,visual_prompt:`画面 ${ordinal}`,evidence_reference_ids:[]}};
  });
  return {task_id:script.task_id,script_id:script.id,updated_at:version,title:script.title,language:script.language,
    selection_status:script.selection_status,total_count:45,items,next_after_ordinal:after+items.length<45?after+items.length:null,truncated:after+items.length<45};
}

it('reads 45 script sections through bounded versioned HTTP pages and returns to previous pages',async()=>{
  const script=sectionReaderScript();
  const fetch=vi.fn(async(url:string)=>{
    const params=new URL(url,'http://local').searchParams;
    const after=Number(params.get('after_ordinal'));
    expect(new URL(url,'http://local').pathname).toBe(`/api/creation-tasks/${script.task_id}/scripts/${script.id}/sections`);
    expect(params.get('expected_updated_at')).toBe(after===0 && fetch.mock.calls.length===1?null:script.updated_at);
    return new Response(JSON.stringify(sectionReaderPage(script,after)));
  });
  vi.stubGlobal('fetch',fetch);
  render(<ConfirmedScriptView script={script}/>);
  await screen.findByText('窗口正文 reader-script 1');
  expect(screen.queryByText('不可用旧首屏替代窗口读取')).not.toBeInTheDocument();
  expect(screen.getAllByRole('article')).toHaveLength(20);
  expect(screen.getByRole('button',{name:'上一页文案'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  await screen.findByText('窗口正文 reader-script 21');
  expect(screen.queryByText('窗口正文 reader-script 1')).not.toBeInTheDocument();
  expect(screen.getAllByRole('article')).toHaveLength(20);
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  await screen.findByText('窗口正文 reader-script 45');
  expect(screen.getAllByRole('article')).toHaveLength(5);
  expect(screen.getByRole('button',{name:'下一页文案'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'上一页文案'}));
  await screen.findByText('窗口正文 reader-script 21');
  expect(screen.queryByText('窗口正文 reader-script 45')).not.toBeInTheDocument();
});

it('clears a stale script page after HTTP 409 and explicitly restarts without mixing revisions',async()=>{
  const script=sectionReaderScript();
  const nextVersion='2026-10-24T00:01:00Z';
  const fetch=vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(sectionReaderPage(script))))
    .mockResolvedValueOnce(new Response(JSON.stringify({detail:'文案已变化，请重新读取窗口'}),{status:409}))
    .mockResolvedValueOnce(new Response(JSON.stringify(sectionReaderPage(script,0,nextVersion))))
    .mockResolvedValueOnce(new Response(JSON.stringify(sectionReaderPage(script,20,nextVersion))));
  vi.stubGlobal('fetch',fetch);
  render(<ConfirmedScriptView script={script}/>);
  await screen.findByText('窗口正文 reader-script 1');
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('文案已变化');
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'下一页文案'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'重新读取文案'}));
  await screen.findByText('窗口正文 reader-script 1');
  const restart=new URL(fetch.mock.calls[2][0],'http://local').searchParams;
  expect(restart.get('after_ordinal')).toBe('0');
  expect(restart.has('expected_updated_at')).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  await screen.findByText('窗口正文 reader-script 21');
  expect(new URL(fetch.mock.calls[3][0],'http://local').searchParams.get('expected_updated_at')).toBe(nextVersion);
});

it('ignores a late script-window response after switching script identity',async()=>{
  const scriptA=sectionReaderScript('script-a'),scriptB=sectionReaderScript('script-b');
  let finishA!: (response:Response)=>void;
  vi.stubGlobal('fetch',vi.fn().mockReturnValueOnce(new Promise<Response>(resolve=>{finishA=resolve;}))
    .mockResolvedValueOnce(new Response(JSON.stringify(sectionReaderPage(scriptB)))));
  const view=render(<ConfirmedScriptView script={scriptA}/>);
  view.rerender(<ConfirmedScriptView script={scriptB}/>);
  await screen.findByText('窗口正文 script-b 1');
  await act(async()=>{finishA(new Response(JSON.stringify(sectionReaderPage(scriptA))));});
  expect(screen.queryByText('窗口正文 script-a 1')).not.toBeInTheDocument();
  expect(screen.getByText('窗口正文 script-b 1')).toBeInTheDocument();
});

function scopeBudget(scopes:import('@/lib/types/creation-tasks').CreationTaskBudget['scopes']=[],taskId='task'):import('@/lib/types/creation-tasks').CreationTaskBudget {
  return {task_id:taskId,authorization_coverage:scopes.length?'tts_pcm_only':'scope_unbound',scopes,
    status:scopes.length?'local_work_authorized':'authorization_scope_unbound',waiting_reason:null,
    resources:{settled_execution_seconds:0,settled_generated_bytes:0,total_execution_seconds:null,total_generated_bytes:null,coverage:'complete',receipt_count:0},
    actual_cost:{status:'pending_verification',amount:null,currency:null,supplier_usage_refs:[],pricing_or_invoice_refs:[]},estimates:{eta_seconds:null,cost:null}};
}
function budgetScope(id='scope-local'):import('@/lib/types/creation-tasks').CreationTaskBudget['scopes'][number] {
  return {authorization:{scope_id:id,revision:2,limits:{execution_seconds:null,generated_bytes:null},policy_kind:'local_goal',revoked:false,target_task_ids:['task'],source_ref:'approval:local'},
    usage:{execution_seconds:2,generated_bytes:128,reserved_seconds:3,reserved_bytes:256,unknown_unit_count:0,unit_count:1},
    remaining:{execution_seconds:null,generated_bytes:null},status:'available',coverage:'tts_pcm_only'};
}
function emptySegments(taskId='task') {
  return {task_id:taskId,total_count:0,available_video_count:0,segments:[],offset:0,truncated:false,revision_requests:{items:[],total_count:0,truncated:false}};
}

it('shows an unbound budget scope without inventing task coverage or budget actions',async()=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  segmentApi.read.mockResolvedValue(emptySegments() as never);
  segmentApi.budget.mockResolvedValue(scopeBudget() as never);
  render(<SegmentProductionPanel taskId="task"/>);
  await screen.findByText('当前任务未绑定累计授权范围。');
  const details=screen.getByText('费用与资源记录').closest('details')!;
  fireEvent.click(screen.getByText('费用与资源记录'));
  expect(within(details).getByText(/不代表整个任务已计量/)).toBeInTheDocument();
  expect(within(details).getByText('实际费用：待核对供应商用量或账单')).toBeInTheDocument();
  expect(within(details).queryByText(/剩余额度：/)).not.toBeInTheDocument();
  expect(within(details).queryByRole('button')).not.toBeInTheDocument();
});

it('distinguishes uncapped local scope values from finite scope limits and remaining quantities',async()=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  const local=budgetScope(),finite=budgetScope('scope-finite');
  finite.authorization={...finite.authorization,policy_kind:'explicit_total',limits:{execution_seconds:10,generated_bytes:1024}};
  finite.remaining={execution_seconds:5,generated_bytes:640};
  segmentApi.read.mockResolvedValue(emptySegments() as never);
  segmentApi.budget.mockResolvedValue(scopeBudget([local,finite]) as never);
  render(<SegmentProductionPanel taskId="task"/>);
  await screen.findByText(/累计授权仅覆盖 TTS\/PCM/);
  fireEvent.click(screen.getByText('费用与资源记录'));
  const localView=within(screen.getByRole('region',{name:'累计授权 scope-local'}));
  expect(localView.getByText('累计上限：执行 未设置 · 生成 未设置')).toBeInTheDocument();
  expect(localView.getByText('剩余额度：执行 未提供数值（未设置累计上限） · 生成 未提供数值（未设置累计上限）')).toBeInTheDocument();
  const finiteView=within(screen.getByRole('region',{name:'累计授权 scope-finite'}));
  expect(finiteView.getByText('累计上限：执行 10 秒 · 生成 1,024 字节')).toBeInTheDocument();
  expect(finiteView.getByText('本范围已结算：执行 2 秒 · 生成 128 字节')).toBeInTheDocument();
  expect(finiteView.getByText('本范围预留：执行 3 秒 · 生成 256 字节')).toBeInTheDocument();
  expect(finiteView.getByText('剩余额度：执行 5 秒 · 生成 640 字节')).toBeInTheDocument();
  fireEvent.click(finiteView.getByText('授权来源与目标'));
  expect(finiteView.getByText('来源：approval:local')).toBeInTheDocument();
  expect(finiteView.getByText('目标任务：task')).toBeInTheDocument();
});

it.each([
  ['usage_unknown','状态：用量未知，须核对原回执；预留未释放'],
  ['exhausted','状态：累计额度已用尽'],
  ['revoked','状态：授权已撤销'],
] as const)('shows scope status %s without offering budget recovery or releasing unknown reservations',async(status,label)=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  const scope=budgetScope();
  scope.status=status;
  scope.authorization={...scope.authorization,policy_kind:'explicit_total',limits:{execution_seconds:5,generated_bytes:null},revoked:status==='revoked'};
  scope.usage.unknown_unit_count=status==='usage_unknown'?1:0;
  scope.remaining={execution_seconds:0,generated_bytes:null};
  segmentApi.read.mockResolvedValue(emptySegments() as never);
  segmentApi.budget.mockResolvedValue(scopeBudget([scope]) as never);
  render(<SegmentProductionPanel taskId="task"/>);
  await screen.findByText(label);
  fireEvent.click(screen.getByText('费用与资源记录'));
  const details=screen.getByText('费用与资源记录').closest('details')!;
  expect(within(details).getByText('本范围预留：执行 3 秒 · 生成 256 字节')).toBeInTheDocument();
  if(status==='usage_unknown') {
    expect(within(details).getByText('剩余额度：待核对原回执，当前不能确定')).toBeInTheDocument();
    expect(within(details).getByText('有 1 个执行单元用量待核对。')).toBeInTheDocument();
    expect(within(details).queryByText(/剩余额度：执行 0/)).not.toBeInTheDocument();
  } else expect(within(details).getByText(/剩余额度：执行 0 秒/)).toBeInTheDocument();
  expect(within(details).queryByRole('button')).not.toBeInTheDocument();
});

it('ignores a late budget-scope response after the task changes',async()=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  let finishA!:(value:import('@/lib/types/creation-tasks').CreationTaskBudget)=>void;
  segmentApi.read.mockResolvedValueOnce(emptySegments('task-a')).mockResolvedValueOnce(emptySegments('task-b'));
  segmentApi.budget.mockReturnValueOnce(new Promise(resolve=>{finishA=resolve;}) as never)
    .mockResolvedValueOnce(scopeBudget([budgetScope('scope-b')],'task-b') as never);
  const view=render(<SegmentProductionPanel taskId="task-a"/>);
  view.rerender(<SegmentProductionPanel taskId="task-b"/>);
  await screen.findByText('累计授权范围 scope-b');
  await act(async()=>{finishA(scopeBudget([budgetScope('scope-a')],'task-a'));});
  expect(screen.queryByText('累计授权范围 scope-a')).not.toBeInTheDocument();
  expect(screen.getByText('累计授权范围 scope-b')).toBeInTheDocument();
});

function stubLoadedScriptWindows(scripts:import('@/lib/types').CreationTaskScript[]) {
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    const path=new URL(url,'http://local').pathname;
    const script=scripts.find(script=>path.endsWith(`/scripts/${script.id}/sections`));
    if(!script?.sections_loaded) throw new Error(`Unconfigured script window: ${path}`);
    return new Response(JSON.stringify({...sectionReaderPage(script),total_count:script.sections_total_count,
      items:script.sections.map((section,index)=>({ordinal:index+1,digest:`digest-${index+1}`,section})),
      next_after_ordinal:null,truncated:false}));
  }));
}

it('keeps a 45-section unloaded header readable without enabling partial replacement or confirmation',async()=>{
  const script:import('@/lib/types').CreationTaskScript={...sectionReaderScript(),sections_loaded:false,sections:null,sections_total_count:45};
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(sectionReaderPage(script,Number(new URL(url,'http://local').searchParams.get('after_ordinal')))))));
  const confirm=vi.fn();
  render(<ScriptEditor taskId={script.task_id} script={script} busy={false} repairing={false} onConfirm={confirm}/>);
  await screen.findByText('窗口正文 reader-script 1');
  expect(screen.getByText('文案共 45 段，正文尚未完整载入；当前不可保存或确认。')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'保存草稿'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'确认文案'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'下一页文案'}));
  await screen.findByText('窗口正文 reader-script 21');
  expect(confirm).not.toHaveBeenCalled();
});

it('automatically reads a complete small header on edit intent before allowing versioned confirmation',async()=>{
  const script:import('@/lib/types').CreationTaskScript={...sectionReaderScript(),selection_status:'draft',sections_loaded:false,sections:null,sections_total_count:1};
  const section={id:'only-section',title:'正文',content:'完整正文',visual_prompt:'完整画面',evidence_reference_ids:[]};
  let finishRead!:(response:Response)=>void;
  const fetch=vi.fn((_url:string)=>new Promise<Response>(resolve=>{finishRead=resolve;}));
  vi.stubGlobal('fetch',fetch);
  const confirm=vi.fn();
  render(<ScriptEditor taskId={script.task_id} script={script} busy={false} repairing={false} onConfirm={confirm}/>);
  expect(screen.getByRole('status')).toHaveTextContent('正在读取完整文案');
  expect(screen.queryByRole('button',{name:'读取完整文案以编辑'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'确认文案'})).not.toBeInTheDocument();
  await waitFor(()=>expect(fetch).toHaveBeenCalled());
  const params=new URL(fetch.mock.calls[0][0],'http://local').searchParams;
  expect(params.get('expected_updated_at')).toBe(script.updated_at);
  await act(async()=>{finishRead(new Response(JSON.stringify({...sectionReaderPage(script),total_count:1,
    items:[{ordinal:1,digest:'digest-only',section}],next_after_ordinal:null,truncated:false})));});
  await screen.findByLabelText('第 1 段正文');
  fireEvent.click(screen.getByRole('button',{name:'确认文案'}));
  expect(confirm).toHaveBeenCalledWith(expect.objectContaining({sections_loaded:true,sections_total_count:1,sections:[section],updated_at:script.updated_at}));
});

it('rejects a partial window as a complete edit even when the header count was small',async()=>{
  const script:import('@/lib/types').CreationTaskScript={...sectionReaderScript(),sections_loaded:false,sections:null,sections_total_count:1};
  const complete=sectionReaderPage(script);
  complete.items=complete.items.slice(0,1);
  complete.total_count=1;
  complete.next_after_ordinal=null;
  complete.truncated=false;
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(sectionReaderPage(script))))
    .mockResolvedValueOnce(new Response(JSON.stringify(complete))));
  const confirm=vi.fn();
  render(<ScriptEditor taskId={script.task_id} script={script} busy={false} repairing={false} onConfirm={confirm}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('不能将当前窗口保存或确认为全文');
  expect(screen.queryByRole('button',{name:'确认文案'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'保存草稿'})).not.toBeInTheDocument();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'重试读取文案'}));
  await screen.findByLabelText('第 1 段正文');
  expect(screen.getByRole('button',{name:'确认文案'})).toBeEnabled();
});

it.each(['authorization_scope_mixed','unit_budget_blocked'])('keeps mixed scope details separate from the authoritative budget summary %s',async(status)=>{
  const {SegmentProductionPanel}=await import('../creation-task/NarrationStage');
  const exhausted=budgetScope('old-scope'),available=budgetScope('new-scope');
  exhausted.status='exhausted';
  exhausted.authorization={...exhausted.authorization,policy_kind:'explicit_total',limits:{execution_seconds:2,generated_bytes:128}};
  exhausted.remaining={execution_seconds:0,generated_bytes:0};
  const budget={...scopeBudget([exhausted,available]),status};
  segmentApi.read.mockResolvedValue(emptySegments() as never);
  segmentApi.budget.mockResolvedValue(budget as never);
  render(<SegmentProductionPanel taskId="task"/>);
  await screen.findByText('累计授权范围 new-scope');
  fireEvent.click(screen.getByText('费用与资源记录'));
  const details=screen.getByText('费用与资源记录').closest('details')!;
  expect(within(screen.getByRole('region',{name:'累计授权 old-scope'})).getByText('状态：累计额度已用尽')).toBeInTheDocument();
  expect(within(screen.getByRole('region',{name:'累计授权 new-scope'})).getByText('状态：当前范围未显示预算阻断')).toBeInTheDocument();
  const summary=within(details).getByRole('status');
  if(status==='authorization_scope_mixed') {
    expect(summary).toHaveTextContent('各累计授权范围的状态不一致，请逐项查看');
    expect(summary).toHaveTextContent('不代表当前作业可以切换授权范围继续');
  } else {
    expect(summary).toHaveTextContent('单次请求的资源声明超出预算');
    expect(within(details).queryByText(/各累计授权范围的状态不一致/)).not.toBeInTheDocument();
  }
  expect(within(details).queryByRole('button')).not.toBeInTheDocument();
});

it('ignores an old automatic edit read after the script version changes',async()=>{
  const oldScript:import('@/lib/types').CreationTaskScript={...sectionReaderScript(),sections_loaded:false,sections:null,sections_total_count:1};
  const newScript={...oldScript,updated_at:'2026-10-24T00:02:00Z'};
  function completePage(script:import('@/lib/types').CreationTaskScript,content:string) {
    const page=sectionReaderPage(script);
    return {...page,items:[{...page.items[0],section:{...page.items[0].section,content}}],total_count:1,next_after_ordinal:null,truncated:false};
  }
  let finishOld!:(response:Response)=>void;
  vi.stubGlobal('fetch',vi.fn().mockReturnValueOnce(new Promise<Response>(resolve=>{finishOld=resolve;}))
    .mockResolvedValueOnce(new Response(JSON.stringify(completePage(newScript,'新版本正文')))));
  const confirm=vi.fn();
  const view=render(<ScriptEditor taskId={oldScript.task_id} script={oldScript} busy={false} repairing={false} onConfirm={confirm}/>);
  view.rerender(<ScriptEditor taskId={newScript.task_id} script={newScript} busy={false} repairing={false} onConfirm={confirm}/>);
  expect(await screen.findByLabelText('第 1 段正文')).toHaveValue('新版本正文');
  await act(async()=>{finishOld(new Response(JSON.stringify(completePage(oldScript,'过期旧正文'))));});
  expect(screen.getByLabelText('第 1 段正文')).toHaveValue('新版本正文');
  fireEvent.click(screen.getByRole('button',{name:'确认文案'}));
  expect(confirm).toHaveBeenCalledWith(expect.objectContaining({updated_at:newScript.updated_at,sections:[expect.objectContaining({content:'新版本正文'})]}));
});

it('sends explicit replace_all from the complete editor for save and confirmation at the saved version',async()=>{
  const task={...baseTask,stage:'script' as const,stage_status:'waiting_for_user' as const,status:'waiting_for_user' as const,confirmed_script_id:null};
  const draft:Extract<import('@/lib/types').CreationTaskScript,{sections_loaded:true}>={...sectionReaderScript(),task_id:task.id,
    selection_status:'draft',sections_loaded:true,sections_total_count:1,
    sections:[{id:'complete-section',title:'正文',content:'完整正文',visual_prompt:'完整画面',evidence_reference_ids:[]}]};
  const saved={...draft,updated_at:'2026-10-24T00:05:00Z'};
  const initial={...workspace(task),scripts:[draft]};
  mocks.getWorkspace.mockResolvedValue(initial);
  mocks.getAssistantScriptContext.mockResolvedValue({reply_origin:null,sources:[]});
  mocks.saveScriptDraft.mockResolvedValue(saved);
  mocks.confirmScript.mockResolvedValue({});
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
  await screen.findByLabelText('第 1 段正文');
  fireEvent.click(screen.getByRole('button',{name:'保存草稿'}));
  await waitFor(()=>expect(mocks.saveScriptDraft).toHaveBeenCalledWith(task.id,{
    title:draft.title,sections:draft.sections,expected_updated_at:draft.updated_at,edit_mode:'replace_all',
  }));
  await waitFor(()=>expect(screen.getByRole('button',{name:'确认文案'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'确认文案'}));
  await waitFor(()=>expect(mocks.confirmScript).toHaveBeenCalledWith(task.id,draft.id,{
    title:saved.title,sections:saved.sections,expected_updated_at:saved.updated_at,edit_mode:'replace_all',
  }));
});

it('shows complete black-frame totals separately from samples and keeps delivery-duration findings locatable',async()=>{
  const durationFinding:import('@/lib/types').CreationTaskQualityCheck['findings'][number]={
    kind:'delivery_duration',start_seconds:0,end_seconds:30,duration_seconds:30,detail:'交付目标为 45 秒，当前为 30 秒。',clip_ids:[],
  };
  const check:import('@/lib/types').CreationTaskQualityCheck={check_state:'done',result_status:'issues_found',stale:false,
    check_id:'quality-check',timeline_version_id:'version',render_ref:'render',checked_at:'2026-10-24T00:00:00Z',error:null,
    findings:[{kind:'black_frame',start_seconds:0,end_seconds:.1,duration_seconds:.1,detail:'已列举的黑帧片段',clip_ids:['clip-1']},durationFinding],unavailable:[],
    continuation:null,clipping_summary:null,black_frame_summary:{event_count:3,total_black_seconds:.3,first_start_seconds:0,last_end_seconds:2.1,
      event_list_limit:2,events:[{start_seconds:0,end_seconds:.1,duration_seconds:.1},{start_seconds:1,end_seconds:1.1,duration_seconds:.1}],
      events_truncated:true,truncated_event_count:1,findings_event_limit:1,findings_truncated:true,findings_truncated_count:2},
    validation_scope:'deterministic_media_errors_only'};
  mocks.getQualityCheck.mockResolvedValue(check);
  const onLocate=vi.fn();
  const view=render(<FinalVideoQualityCheck taskId="task" versionId="version" outputUrl="/video" onLocate={onLocate}/>);
  const summary=await screen.findByLabelText('黑帧摘要');
  expect(summary).toHaveTextContent('黑帧共 3 段，累计 0.3 秒。');
  expect(summary).toHaveTextContent('黑帧问题明细已限流至 1 条，另有 2 段未逐条列出');
  expect(summary).toHaveTextContent('黑帧区间摘要仅保留 2 条，另有 1 段未在区间摘要中列举');
  expect(screen.getByText('交付时长偏差')).toBeInTheDocument();
  expect(screen.getByText(durationFinding.detail)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'定位 0.00–30.00 秒'}));
  expect(onLocate).toHaveBeenCalledWith(durationFinding);
  mocks.getQualityCheck.mockResolvedValue({...check,black_frame_summary:null});
  view.rerender(<FinalVideoQualityCheck taskId="task" versionId="version" outputUrl="/new-video" onLocate={onLocate}/>);
  await waitFor(()=>expect(screen.queryByLabelText('黑帧摘要')).not.toBeInTheDocument());
  expect(await screen.findByText(durationFinding.detail)).toBeInTheDocument();
});

it.each([false,true])('does not equate all stored sections with a complete draft when sections_loaded=%s',async(loaded)=>{
  const stored=sectionReaderScript();
  const partial:import('@/lib/types').CreationTaskScript=loaded
    ? {...stored,selection_status:'draft',draft_complete:false}
    : {...stored,selection_status:'draft',draft_complete:false,sections_loaded:false,sections:null};
  const complete={...partial,draft_complete:true,updated_at:'2026-10-24T00:08:00Z'};
  vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
    const version=new URL(url,'http://local').searchParams.get('expected_updated_at')??partial.updated_at;
    const page=sectionReaderPage(partial,0,version);
    return new Response(JSON.stringify({...page,items:page.items.slice(0,1),total_count:1,next_after_ordinal:null,truncated:false}));
  }));
  const confirm=vi.fn();
  const view=render(<ScriptEditor taskId={partial.task_id} script={partial} busy={false} repairing={false} onConfirm={confirm}/>);
  await screen.findByText('窗口正文 reader-script 1');
  expect(screen.getByText(/当前为部分稿，已存 1 段不代表目标已完整/)).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'保存草稿'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'确认文案'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('第 1 段正文')).toBeEnabled();
  expect(confirm).not.toHaveBeenCalled();
  view.rerender(<ScriptEditor taskId={complete.task_id} script={complete} busy={false} repairing={false} onConfirm={confirm}/>);
  await screen.findByLabelText('第 1 段正文');
  expect(screen.queryByText(/当前为部分稿/)).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认文案'})).toBeEnabled();
});

it('opens manual first-window writing from the existing script workspace and continues the same partial draft',async()=>{
  const task={...baseTask,stage:'script' as const,stage_status:'waiting_for_user' as const,status:'waiting_for_user' as const,confirmed_script_id:null};
  const initial=workspace(task);
  mocks.getWorkspace.mockResolvedValue(initial);
  const script:import('@/lib/types').CreationTaskScript={...sectionReaderScript(),id:'manual-script',task_id:task.id,
    selection_status:'draft',draft_complete:false,sections_loaded:false,sections:null,sections_total_count:1,updated_at:'2026-10-24T00:09:00Z'};
  const section={id:'manual-section',title:'手动首段',content:'手动首段正文',visual_prompt:'手动画面',evidence_reference_ids:[]};
  const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
    if(init?.method==='POST' && url.endsWith('/scripts/windows')) {
      const body=JSON.parse(init.body as string);
      Object.assign(section,body.sections[0]);
      expect(body.title).toBe('手动文案');
      expect(body).not.toHaveProperty('edit_mode');
      return new Response(JSON.stringify({task_id:task.id,script_id:script.id,request_id:body.request_id,status:'saved',
        updated_at:script.updated_at,total_count:1,affected_section_ids:[section.id]}));
    }
    if(url.includes(`/scripts/${script.id}/sections`)) return new Response(JSON.stringify({task_id:task.id,script_id:script.id,
      updated_at:script.updated_at,title:'手动文案',language:'zh-CN',selection_status:'draft',total_count:1,
      items:[{ordinal:1,section,digest:'digest'}],next_after_ordinal:null,truncated:false}));
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch',fetch);
  renderTaskSubject(<CreationTaskWorkspace initialTasks={[]} initialTopics={null} initialWorkspace={initial}/>);
  fireEvent.click(await screen.findByRole('button',{name:'手动编写文案'}));
  fireEvent.change(await screen.findByLabelText('新文案标题'),{target:{value:'手动文案'}});
  fireEvent.change(screen.getByLabelText('第 1 段标题'),{target:{value:'手动首段'}});
  fireEvent.change(screen.getByLabelText('第 1 段正文'),{target:{value:'手动首段正文'}});
  fireEvent.change(screen.getByLabelText('第 1 段画面建议'),{target:{value:'手动画面'}});
  mocks.getWorkspace.mockResolvedValue({...initial,scripts:[script]});
  fireEvent.click(screen.getByRole('button',{name:'保存首窗'}));
  await screen.findByRole('region',{name:'分窗编辑文案'});
  expect(await screen.findByLabelText('第 1 段正文')).toHaveValue('手动首段正文');
  expect(screen.queryByRole('button',{name:'确认文案'})).not.toBeInTheDocument();
  expect(mocks.confirmScript).not.toHaveBeenCalled();
});
