import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import type { CreationTask } from "@/lib/types";

const mocks = vi.hoisted(() => ({ saved: new Map<string, import("@/lib/types").AssistantConversation>(), sequence: 0, ask: vi.fn(), materialAsk: vi.fn(), preflight: vi.fn(), confirm: vi.fn(), settings: vi.fn(), editAsk: vi.fn(), confirmEdit: vi.fn(), undoEdit: vi.fn() }));
vi.mock("@/lib/api", () => {
  const saved = mocks.saved;
  function makeConversation(scope: { scope_kind: "home" | "task" | "material"; scope_id: string }, id = `conversation-${scope.scope_kind}-${scope.scope_id}`) {
    const value: import("@/lib/types").AssistantConversation = {
      id, workspace_id: "local", ...scope, title: "新对话", draft_text: "",
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
    };
    saved.set(id, value);
    return value;
  }
  async function runTurn(conversationId: string, userMessage: string, onEvent: (event: never) => void, work: (emit: (event: never) => void) => Promise<Record<string, unknown>>) {
    const turnId = `turn-${++mocks.sequence}`;
    const events: never[] = [];
    const emit = (event: never) => {
      const type = (event as { type?: string }).type;
      if (type !== "turn_started" && type !== "turn_status") events.push(event);
      onEvent(event);
    };
    emit({ type: "turn_started", turn_id: turnId } as never);
    const result = await work(emit);
    if (!events.some((event) => (event as { type?: string }).type === "text_delta") && typeof result.reply === "string") {
      emit({ type: "text_delta", delta: result.reply } as never);
    }
    const existing = saved.get(conversationId)!;
    const assistantText = events.filter((event) => (event as { type?: string }).type === "text_delta")
      .map((event) => (event as { delta: string }).delta).join("");
    const materialResult = result.material_result as { scope: string; changed: boolean } | null | undefined;
    const projected = result.plan_preview ? { kind: "plan_preview", plan_preview: result.plan_preview }
      : materialResult ? { kind: "material_result", scope: materialResult.scope, changed: materialResult.changed }
        : result.execution_action || result.script_draft || result.topic_candidates
          ? { kind: "task_turn", execution_action: result.execution_action, execution_status: result.execution_status,
              script_draft: result.script_draft ? "saved" : null, topic_candidates: Boolean(result.topic_candidates),
              edit_preview: Boolean(result.edit_preview) }
          : result.edit_preview ? { kind: "task_turn", edit_preview: true }
            : { kind: "conversation" };
    const turn = { id: turnId, sequence: mocks.sequence, user_message: userMessage, assistant_text: assistantText,
      status: "completed" as const, events: events as import("@/lib/types").AssistantConversationTurn["events"],
      result: projected, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:01Z" };
    saved.set(conversationId, { ...existing, turns: [...existing.turns.filter((item) => item.id !== turnId), turn], updated_at: turn.updated_at });
    emit({ type: "turn_status", turn_state: "completed" } as never);
    return { ...result, turn_id: turnId };
  }
  return {
  getCurrentAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => makeConversation(scope, `conversation-${scope.scope_kind}-${scope.scope_id}-${++mocks.sequence}`)),
  createAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => makeConversation(scope, `new-conversation-${scope.scope_kind}-${scope.scope_id}-${++mocks.sequence}`)),
  listAssistantConversations: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => ({ items: [...saved.values()].filter((item) => item.scope_kind === scope.scope_kind && item.scope_id === scope.scope_id) })),
  getAssistantConversation: vi.fn(async (id: string) => saved.get(id)!),
  resumeAssistantConversation: vi.fn(async (id: string) => saved.get(id)!),
  updateAssistantConversation: vi.fn(async (id: string, update: { draft_text?: string; title?: string }) => {
    const item = saved.get(id)!;
    const updated = { ...item, ...update, updated_at: "2026-10-09T00:00:01Z" };
    saved.set(id, updated);
    return updated;
  }),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantCapabilities: vi.fn().mockResolvedValue({ application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"], pi_tui_commands: { available: false, reason: "未接通", version: "1.1.0", commands: [] }, extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {} }),
  apiUrl: (path: string) => path,
  getRuntimeSettings: mocks.settings,
  sendWorkspaceAssistantPlanTurn: vi.fn((message: string, conversationId: string, onEvent: (event: never) => void) => runTurn(conversationId, message, onEvent, (emit) => mocks.ask(message, conversationId, emit))),
  updateAssistantPlanPreview: vi.fn((_conversationId: string, _turnId: string, input: unknown) => mocks.preflight(input)),
  sendMaterialAssistantTurn: vi.fn((message: string, conversationId: string, selection: unknown, onEvent: (event: never) => void) => runTurn(conversationId, message, onEvent, (emit) => mocks.materialAsk(message, conversationId, selection, emit))),
  previewCreationPlan: mocks.preflight,
  confirmCreationPlan: mocks.confirm,
  sendCreationTaskAssistantTurn: vi.fn((taskId: string, payload: { conversation_id: string; message: string }, onEvent: (event: never) => void) => runTurn(payload.conversation_id, payload.message, onEvent, (emit) => mocks.editAsk(taskId, payload, emit))),
  confirmCreationTaskEdit: mocks.confirmEdit,
  undoCreationTaskTimeline: mocks.undoEdit,
  };
});

afterEach(() => vi.clearAllMocks());

describe("HomeCommandComposer", () => {
  function configuredModel() {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } });
  }

  async function readyInput(label: string) {
    const input = screen.getByRole("textbox", { name: label });
    await waitFor(() => expect(input).toBeEnabled());
    return input;
  }

  it("asks the assistant before creating anything, then confirms the real preflight", async () => {
    configuredModel();
    const assignLocation = vi.fn();
    mocks.ask.mockResolvedValue({
      reply: "可制作一条 60 秒产品视频。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 4,
        approved_asset_count: 2, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "已审核素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: { profile_id: "voice-1" }, blockers: [], confirmable: true,
        preflight_fingerprint: "a".repeat(64),
      },
    });
    mocks.confirm.mockResolvedValue({ creation_task: { id: "task-new" } });
    render(<HomeCommandComposer assignLocation={assignLocation} />);

    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.change(await readyInput("制作要求"), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.ask).toHaveBeenCalledWith("介绍产品", expect.any(String), expect.any(Function)));
    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(await screen.findByRole("dialog", { name: "制作方案" })).toBeVisible();
    expect(await screen.findByRole("region", { name: "制作方案" })).toHaveTextContent("镜头 4");

    fireEvent.click(screen.getByRole("button", { name: "关闭卡片" }));
    expect(screen.queryByRole("dialog", { name: "制作方案" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看方案" }));

    fireEvent.click(await screen.findByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith({
      goal: "介绍产品", name: "产品视频", target_count: 1, language: "zh-CN",
      aspect_ratio: "9:16", duration_target_seconds: 60, presenter_share_target: 20, lipsync_quality: "high", preflight_fingerprint: "a".repeat(64),
    }));
    expect(assignLocation).toHaveBeenCalledWith("/tasks/task-new");
  });

  it("sends only the bound material selection and preserves its real assistant answer", async () => {
    configuredModel();
    const selection = { scope: "segment" as const, asset_id: "asset-1", segment_id: "segment-2" };
    const materialResult = { scope: "segment" as const, asset_ref: "asset:asset-1", segment_ref: "segment:segment-2", user_tags: ["旧标签"] };
    const onResult = vi.fn();
    mocks.materialAsk.mockResolvedValue({ reply: "已读取旧标签；你想替换还是添加？", provider: "minimax", model: "MiniMax-M3", material_result: materialResult });
    render(<HomeCommandComposer materialContext={{ selection, label: "片段 0:02–0:04", onResult }} />);
    fireEvent.change(await readyInput("素材标签要求"), { target: { value: "这个片段的标签帮我改一下" } });
    fireEvent.submit(screen.getByRole("textbox", { name: "素材标签要求" }).closest("form")!);
    await waitFor(() => expect(mocks.materialAsk).toHaveBeenCalledWith(
      "这个片段的标签帮我改一下", expect.any(String), selection, expect.any(Function),
    ));
    expect(await screen.findByText("已读取旧标签；你想替换还是添加？")).toBeVisible();
    expect(onResult).toHaveBeenCalledWith(selection, materialResult);
  });

  it("rechecks a selected face-share stop before allowing creation", async () => {
    configuredModel();
    const base = {
      name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN" as const,
      aspect_ratio: "9:16" as const, duration_target_seconds: 60,
      presenter_share_target: 20 as const, approved_clip_count: 2, approved_asset_count: 1,
      knowledge_references: [], source_references: [], material_matches: [], material_rule_summary: "已审核素材",
      model_snapshot: [], confirmation_points: [], pronunciation_references: [], selected_voice: null,
      blockers: [], confirmable: true, preflight_fingerprint: "a".repeat(64),
    };
    mocks.ask.mockResolvedValue({ reply: "方案已备好", provider: "minimax", model: "MiniMax-M3", plan_preview: base });
    mocks.preflight.mockResolvedValue({ ...base, presenter_share_target: 40, preflight_fingerprint: "b".repeat(64) });
    mocks.confirm.mockResolvedValue({ creation_task: { id: "task-40" } });
    render(<HomeCommandComposer assignLocation={vi.fn()} />);
    fireEvent.change(await readyInput("制作要求"), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    const slider = await screen.findByRole("slider", { name: "露脸占比" });
    fireEvent.change(slider, { target: { value: "40" } });
    await waitFor(() => expect(mocks.preflight).toHaveBeenCalledWith(expect.objectContaining({ presenter_share_target: 40 })));
    await waitFor(() => expect(screen.getByRole("button", { name: "创建任务" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ presenter_share_target: 40, preflight_fingerprint: "b".repeat(64) })));
  });

  it("keeps a blocked preflight visible without creating a task", async () => {
    configuredModel();
    mocks.ask.mockResolvedValue({
      reply: "还没有可用声音。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "演示", goal: "演示产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 0,
        approved_asset_count: 0, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "无可用素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: null, confirmable: false, preflight_fingerprint: "b".repeat(64),
        blockers: [{ reason: "voice_missing", impact: "缺少声音。", repair_action: "请先选择授权声音。", blocks_confirmation: true }],
      },
    });
    render(<HomeCommandComposer />);
    fireEvent.change(await readyInput("制作要求"), { target: { value: "演示产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/先处理/)).toBeInTheDocument();
    expect(screen.getByText(/请先选择授权声音/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建任务" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("shows a voice blocker before allowing a draft task", async () => {
    configuredModel();
    mocks.ask.mockResolvedValue({
      reply: "可以先建任务，配音还需处理。", provider: "minimax", model: "MiniMax-M3",
      plan_preview: {
        name: "产品视频", goal: "介绍产品", target_count: 1, language: "zh-CN",
        aspect_ratio: "9:16", duration_target_seconds: 60, approved_clip_count: 4,
        approved_asset_count: 2, knowledge_references: [], source_references: [],
        material_matches: [], material_rule_summary: "已审核素材", model_snapshot: [],
        confirmation_points: [], pronunciation_references: [],
        selected_voice: { profile_id: "voice-1" }, confirmable: true,
        preflight_fingerprint: "a".repeat(64),
        blockers: [{ reason: "voice_model_unavailable", stage: "narration", impact: "当前不能配音。", repair_action: "请先安装声音模型。", blocks_confirmation: false }],
      },
    });
    render(<HomeCommandComposer />);
    fireEvent.change(await readyInput("制作要求"), { target: { value: "介绍产品" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("声音待处理")).toBeInTheDocument();
    expect(screen.getByText("当前不能配音。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建任务" })).toBeEnabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("shows setup instead of an active assistant when no text model is connected", async () => {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: false, provider: "minimax", model: "MiniMax-M3" } } });
    render(<HomeCommandComposer />);
    fireEvent.change(await readyInput("制作要求"), { target: { value: "介绍产品" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    expect(await screen.findByRole("link", { name: "连接文字模型" })).toHaveAttribute("href", "/settings");
    expect(mocks.ask).not.toHaveBeenCalled();
  });

  it("focuses the conversation when opened from a new-video entry", async () => {
    configuredModel();
    render(<HomeCommandComposer autoFocus />);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "制作要求" })).toHaveFocus());
  });

  it("sends on Enter, keeps Space/Shift+Enter/IME Enter in the input, streams safe steps and restores focus", async () => {
    configuredModel();
    let resolveTurn!: (value: unknown) => void;
    let emitDelta!: (event: unknown) => void;
    const result = new Promise(resolve => { resolveTurn = resolve; });
    mocks.ask.mockImplementation((_command: string, _conversation: string, onEvent: (event: unknown) => void) => {
      emitDelta = onEvent;
      onEvent({ type: "text_delta", delta: "已读到当前" });
      onEvent({ type: "tool_start", tool_id: 1, label: "读取当前工作区" });
      onEvent({ type: "tool_end", tool_id: 1, label: "读取当前工作区", ok: true, summary: "已读取 1 项任务摘要。" });
      return result;
    });
    render(<HomeCommandComposer />);
    const input = await readyInput("制作要求");
    fireEvent.change(input, { target: { value: "介绍产品 " } });
    fireEvent.keyDown(input, { key: " ", code: "Space" });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    fireEvent.compositionEnd(input);
    expect(mocks.ask).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "介绍产品" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByText("已读到当前", { exact: false })).toBeInTheDocument();
    expect(mocks.ask).toHaveBeenCalledWith("介绍产品", expect.any(String), expect.any(Function));
    await act(async () => {
      emitDelta({ type: "text_delta", delta: "制作条件。" });
      resolveTurn({ reply: "已读到当前制作条件。", provider: "minimax", model: "test", plan_preview: null });
    });
    const finalBody = await screen.findByText("已读到当前制作条件。", { exact: true });
    const toolSummary = screen.getByText("工具记录 · 1 项 · 已完成");
    expect(toolSummary.compareDocumentPosition(finalBody) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(toolSummary);
    expect(screen.getByText("已读取 1 项任务摘要。")).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it("keeps an existing task in the chat, previews a change, confirms and undoes it", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValue({
      reply: "会调亮这一段。", provider: "minimax", model: "MiniMax-M3",
      edit_preview: { proposal: {
        id: "proposal-1", summary: "调亮画面", invalidated_refs: [], external_effects: [],
        preview: { before: { label: "原画面", detail: "偏暗" }, after: { label: "新画面", detail: "更亮" } },
      } },
    });
    mocks.confirmEdit.mockResolvedValue({});
    mocks.undoEdit.mockResolvedValue({});
    render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);

    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "把画面调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.editAsk).toHaveBeenCalledWith("task-1", {
      conversation_id: expect.any(String), message: "把画面调亮", context_stage: "montage", selected_object_refs: [], playhead_ms: null,
    }, expect.any(Function)));
    expect(await screen.findByRole("dialog", { name: "修改预览" })).toHaveTextContent("调亮画面");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-1"));
    expect(onTaskUpdated).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "撤销修改" }));
    await waitFor(() => expect(mocks.undoEdit).toHaveBeenCalledWith("task-1"));
    expect(onTaskUpdated).toHaveBeenCalledTimes(2);
    expect(mocks.ask).not.toHaveBeenCalled();
  });

  it("refreshes the task consumer after a natural-language action reports real queue status", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValue({
      reply: "连续制作已提交并排队，尚未完成；可在任务页查看实际进度。",
      provider: "minimax", model: "MiniMax-M3", edit_preview: null, script_draft: null,
      execution_action: "start", execution_status: { task_id: "task-1", job: { id: "job-1", status: "queued" } },
      topic_candidates: null,
    });
    render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "就按已确认的文案开始做成片，请回报真实作业进度" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/已提交并排队，尚未完成/)).toBeInTheDocument();
    expect(mocks.editAsk).toHaveBeenCalledWith("task-1", expect.objectContaining({
      message: "就按已确认的文案开始做成片，请回报真实作业进度", context_stage: "montage",
    }), expect.any(Function));
    expect(onTaskUpdated).toHaveBeenCalledOnce();
  });

  it("keeps a queued person proposal pending without enabling an uncommitted undo", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValue({
      reply: "人物修改待确认。", provider: "minimax", model: "MiniMax-M3",
      edit_preview: { proposal: {
        id: "proposal-person", summary: "重做所选人物口型", invalidated_refs: [], external_effects: [],
        preview: { before: { label: "原镜头", detail: "当前版本" }, after: { label: "人物口型", detail: "待生成" } },
      } },
    });
    mocks.confirmEdit.mockResolvedValue({ status: "queued", job_id: "person-job", request_id: "device-request" });
    render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "这里只重做口型，其他都不要动" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改预览" });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    expect(await screen.findByText("修改已排队，作业 ID：person-job。队列回执不代表制作完成，当前版本保持不变。")).toBeInTheDocument();
    expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-person");
    expect(onTaskUpdated).toHaveBeenCalledOnce();
    expect(screen.queryByText("修改已完成。")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    expect(mocks.undoEdit).not.toHaveBeenCalled();
  });

  it("does not show an old task reply after switching tasks", async () => {
    configuredModel();
    let finish: (value: unknown) => void = () => undefined;
    mocks.editAsk.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const first = { id: "task-1", stage: "montage", name: "第一条" } as CreationTask;
    const second = { id: "task-2", stage: "montage", name: "第二条" } as CreationTask;
    const view = render(<HomeCommandComposer task={first} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.editAsk).toHaveBeenCalledTimes(1));
    view.rerender(<HomeCommandComposer task={second} />);
    finish({ reply: "第一条已处理", provider: "minimax", model: "MiniMax-M3", edit_preview: null });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeDisabled());
    expect(screen.queryByText("第一条已处理")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "修改预览" })).not.toBeInTheDocument();
  });
});
