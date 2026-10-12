import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import type { CreationTask } from "@/lib/types";

const mocks = vi.hoisted(() => ({ saved: new Map<string, import("@/lib/types").AssistantConversation>(), receipts: new Map<string, unknown>(), sequence: 0, ask: vi.fn(), materialAsk: vi.fn(), preflight: vi.fn(), confirm: vi.fn(), settings: vi.fn(), editAsk: vi.fn(), confirmEdit: vi.fn(), undoEdit: vi.fn(), updateConversation: vi.fn(), sendTaskTurn: vi.fn(), restoreDraft: vi.fn() }));
vi.mock("@/lib/api", () => {
  const saved = mocks.saved;
  function makeConversation(scope: { scope_kind: "home" | "task" | "material"; scope_id: string }, id = `conversation-${scope.scope_kind}-${scope.scope_id}`) {
    const value: import("@/lib/types").AssistantConversation = {
      id, workspace_id: "local", ...scope, title: "新对话", draft_text: "", draft_version: 0,
      created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
    };
    saved.set(id, value);
    return value;
  }
  function mockUuid() {
    return `00000000-0000-4000-8000-${String(++mocks.sequence).padStart(12, "0")}`;
  }
  async function runTurn(conversationId: string, userMessage: string, clientRequestId: string, expectedDraftVersion: number,
    onEvent: (event: never) => void, work: (emit: (event: never) => void) => Promise<Record<string, unknown>>) {
    const priorReceipt = mocks.receipts.get(clientRequestId) as { request: string; response: Record<string, unknown> } | undefined;
    if (priorReceipt) {
      if (priorReceipt.request !== `${conversationId}:${userMessage}:${expectedDraftVersion}`) throw new Error("request id reused with different turn");
      return priorReceipt.response;
    }
    const before = saved.get(conversationId)!;
    if (before.draft_text !== userMessage || before.draft_version !== expectedDraftVersion) throw new Error("draft CAS conflict");
    const turnId = mockUuid();
    const events: never[] = [];
    const emit = (event: never) => {
      const type = (event as { type?: string }).type;
      if (type !== "turn_started" && type !== "turn_status") events.push(event);
      onEvent(event);
    };
    saved.set(conversationId, { ...before, draft_text: "", draft_version: expectedDraftVersion + 1 });
    emit({ type: "turn_started", turn_id: turnId, turn_state: "running", draft_version: expectedDraftVersion + 1 } as never);
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
    const nextVersion = expectedDraftVersion + 1;
    saved.set(conversationId, { ...existing, draft_text: "", draft_version: nextVersion,
      turns: [...existing.turns.filter((item) => item.id !== turnId), turn], updated_at: turn.updated_at });
    emit({ type: "turn_status", turn_id: turnId, turn_state: "completed" } as never);
    const response = { ...result, turn_id: turnId, draft_version: nextVersion };
    mocks.receipts.set(clientRequestId, { request: `${conversationId}:${userMessage}:${expectedDraftVersion}`, response });
    return response;
  }
  return {
  getCurrentAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => makeConversation(scope, mockUuid())),
  createAssistantConversation: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => makeConversation(scope, mockUuid())),
  listAssistantConversations: vi.fn(async (scope: { scope_kind: "home" | "task" | "material"; scope_id: string }) => ({ items: [...saved.values()].filter((item) => item.scope_kind === scope.scope_kind && item.scope_id === scope.scope_id) })),
  getAssistantConversation: vi.fn(async (id: string) => saved.get(id)!),
  resumeAssistantConversation: vi.fn(async (id: string) => saved.get(id)!),
  updateAssistantConversation: mocks.updateConversation.mockImplementation(async (id: string, update: { draft_text?: string; title?: string; expected_draft_version?: number }) => {
    const item = saved.get(id)!;
    if (update.draft_text !== undefined && update.expected_draft_version !== item.draft_version) throw new Error("draft CAS conflict");
    const { expected_draft_version: _expected, ...changes } = update;
    const updated = { ...item, ...changes,
      draft_version: item.draft_version + (update.draft_text !== undefined ? 1 : 0), updated_at: "2026-10-09T00:00:01Z" };
    saved.set(id, updated);
    return updated;
  }),
  restoreUnpersistedAssistantTurnDraft: mocks.restoreDraft.mockImplementation(async (id: string, input: { client_request_id: string; user_message: string; expected_draft_version: number }) => {
    if (mocks.receipts.has(input.client_request_id)) return { status: "already_persisted", conversation: saved.get(id)! };
    const item = saved.get(id)!;
    if (item.draft_version !== input.expected_draft_version) throw new Error("draft CAS conflict");
    const conversation = { ...item, draft_text: input.user_message, draft_version: item.draft_version + 1 };
    saved.set(id, conversation);
    return { status: "restored", conversation };
  }),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantCapabilities: vi.fn().mockResolvedValue({ application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"], pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0", runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查", product_mode: "headless", reason: "未接通", commands: [] }, extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {} }),
  apiUrl: (path: string) => path,
  getRuntimeSettings: mocks.settings,
  sendWorkspaceAssistantPlanTurn: vi.fn((message: string, conversationId: string, clientRequestId: string, expectedDraftVersion: number, onEvent: (event: never) => void) => runTurn(conversationId, message, clientRequestId, expectedDraftVersion, onEvent, (emit) => mocks.ask(message, conversationId, emit))),
  updateAssistantPlanPreview: vi.fn((_conversationId: string, _turnId: string, input: unknown) => mocks.preflight(input)),
  sendMaterialAssistantTurn: vi.fn((message: string, conversationId: string, selection: unknown, clientRequestId: string, expectedDraftVersion: number, onEvent: (event: never) => void) => runTurn(conversationId, message, clientRequestId, expectedDraftVersion, onEvent, (emit) => mocks.materialAsk(message, conversationId, selection, emit))),
  previewCreationPlan: mocks.preflight,
  confirmCreationPlan: mocks.confirm,
  sendCreationTaskAssistantTurn: mocks.sendTaskTurn.mockImplementation((taskId: string, payload: { conversation_id: string; message: string; client_request_id: string; expected_draft_version: number }, onEvent: (event: never) => void) => runTurn(payload.conversation_id, payload.message, payload.client_request_id, payload.expected_draft_version, onEvent, (emit) => mocks.editAsk(taskId, payload, emit))),
  confirmCreationTaskEdit: mocks.confirmEdit,
  undoCreationTaskTimeline: mocks.undoEdit,
  };
});

afterEach(() => { vi.clearAllMocks(); vi.useRealTimers(); });

describe("HomeCommandComposer", () => {
  function configuredModel() {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } });
  }

  async function readyInput(label: string) {
    const input = screen.getByRole("textbox", { name: label });
    await waitFor(() => expect(input).toBeEnabled());
    return input;
  }

  it("restores the original input after a real-shaped persisted failure stream without deleting the failed turn",async()=>{
    configuredModel();
    const message="QA 本机文字连接错误验证，不执行任何写入。";
    let conversationId="";
    let consumedVersion=0;
    mocks.ask.mockImplementationOnce(async(text:string,id:string,emit:(event:never)=>void)=>{
      conversationId=id;
      const current=mocks.saved.get(id)!;
      consumedVersion=current.draft_version;
      const turnId=`00000000-0000-4000-8000-${String(mocks.sequence).padStart(12,"0")}`;
      const turn:import("@/lib/types").AssistantConversationTurn={id:turnId,sequence:1,user_message:text,assistant_text:"",status:"failed",events:[],result:{kind:"failure",message:"localhost不属于允许连接的模型地址"},created_at:"2026-10-10T00:00:00Z",updated_at:"2026-10-10T00:00:01Z"};
      mocks.saved.set(id,{...current,turns:[turn]});
      mocks.restoreDraft.mockImplementationOnce(async()=>({status:"already_persisted",conversation:mocks.saved.get(id)!}));
      emit({type:"turn_status",turn_id:turnId,turn_state:"failed"} as never);
      throw new Error("localhost不属于允许连接的模型地址");
    });
    render(<HomeCommandComposer/>);
    const input=await readyInput("制作要求");
    fireEvent.change(input,{target:{value:message}});
    await waitFor(()=>expect(screen.getByRole("button",{name:"发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    await waitFor(()=>expect(input).toHaveValue(message));
    await waitFor(()=>expect(mocks.updateConversation).toHaveBeenLastCalledWith(conversationId,{draft_text:message,expected_draft_version:consumedVersion}));
    expect(mocks.saved.get(conversationId)?.turns).toHaveLength(1);
    expect(mocks.saved.get(conversationId)?.turns[0].status).toBe("failed");
    expect(mocks.saved.get(conversationId)?.draft_text).toBe(message);
    expect(mocks.ask).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button",{name:"继续编辑本条要求"})).toBeDisabled();
  });

  it("routes a completed home reply as a stable source reference without copying its body", async () => {
    configuredModel();
    mocks.ask.mockResolvedValue({ reply: "已完成的回复正文，不放进链接。", provider: "minimax", model: "MiniMax-M3" });
    const assignLocation = vi.fn();
    render(<HomeCommandComposer assignLocation={assignLocation} />);
    fireEvent.change(await readyInput("制作要求"), { target: { value: "帮我整理成方案" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.click(await screen.findByRole("button", { name: "回复操作" }));
    const action = screen.getByRole("menuitem", { name: "带入新任务文案" });
    fireEvent.click(action);
    fireEvent.click(screen.getByRole("button", { name: "回复操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "带入新任务文案" }));

    expect(assignLocation).toHaveBeenCalledTimes(2);
    const first = new URL(assignLocation.mock.calls[0][0], window.location.origin);
    const second = new URL(assignLocation.mock.calls[1][0], window.location.origin);
    expect(first.pathname).toBe("/production");
    expect(first.searchParams.get("reply_conversation_id")).toMatch(/^[0-9a-f-]{36}$/i);
    expect(first.searchParams.get("reply_turn_id")).toMatch(/^[0-9a-f-]{36}$/i);
    expect(first.searchParams.get("reply_request_id")).toBe(second.searchParams.get("reply_request_id"));
    expect(sessionStorage.getItem(`ai-video-diary.reply-plan-request.v1:${first.searchParams.get("reply_conversation_id")}:${first.searchParams.get("reply_turn_id")}`))
      .toBe(first.searchParams.get("reply_request_id"));
    expect(first.search).not.toContain("已完成的回复正文");
  });

  it("sends the original home target when navigation changes during the draft checkpoint", async () => {
    configuredModel();
    let releaseInitialSave!: () => void;
    let oldConversationId = "";
    mocks.updateConversation.mockImplementationOnce((id: string, update: { draft_text?: string; expected_draft_version?: number }) => new Promise((resolve) => {
      oldConversationId = id;
      releaseInitialSave = () => {
        const existing = mocks.saved.get(id)!;
        const { expected_draft_version: _expected, ...changes } = update;
        const updated = { ...existing, ...changes, draft_version: existing.draft_version + 1, updated_at: "2026-10-09T00:00:01Z" };
        mocks.saved.set(id, updated);
        resolve(updated);
      };
    }));
    const view = render(<HomeCommandComposer />);
    const input = await readyInput("制作要求");

    vi.useFakeTimers();
    fireEvent.change(input, { target: { value: "本轮原始制作要求" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(451); });
    expect(releaseInitialSave).toBeTypeOf("function");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    view.rerender(<HomeCommandComposer materialContext={{
      selection: { scope: "asset", asset_id: "asset-1" }, label: "素材 A", onResult: vi.fn(),
    }} />);
    await act(async () => {
      releaseInitialSave();
      await Promise.resolve();
      await Promise.resolve();
    });
    vi.useRealTimers();
    await waitFor(() => expect(mocks.ask).toHaveBeenCalledTimes(1));
    expect(mocks.ask.mock.calls[0][0]).toBe("本轮原始制作要求");
    expect(mocks.ask.mock.calls[0][1]).toBe(oldConversationId);
    expect(mocks.materialAsk).not.toHaveBeenCalled();
  });

  it("allows library status questions without a selected object and does not write back", async () => {
    configuredModel();
    const onResult = vi.fn();
    mocks.materialAsk.mockResolvedValue({ reply: "当前可见素材共 12 条。", provider: "minimax", model: "MiniMax-M3" });
    render(<HomeCommandComposer materialContext={{ selection: null, label: null, onResult }} />);
    expect(screen.getByRole("region", { name: "助手对话：新对话" })).toBeInTheDocument();
    expect(screen.queryByText(/可询问全库数量、分析状态或按词查找，无需先选素材/)).not.toBeInTheDocument();
    const input = await readyInput("素材对话");
    fireEvent.change(input, { target: { value: "素材库里有多少素材？" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(mocks.materialAsk).toHaveBeenCalledWith(
      "素材库里有多少素材？", expect.any(String), null, expect.any(Function),
    ));
    expect(await screen.findByText("当前可见素材共 12 条。")).toBeInTheDocument();
    expect(onResult).not.toHaveBeenCalled();
  });

  it("keeps the material conversation empty state quiet and preserves a typed draft without sending it", async () => {
    configuredModel();
    const onResult = vi.fn();
    render(<HomeCommandComposer materialContext={{ selection: null, label: null, onResult }} />);
    const input = await readyInput("素材对话");
    expect(screen.getByRole("heading", { name: "想聊点什么？" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "素材库分析情况怎样？" })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "素材库分析情况怎样？" } });
    expect(input).toHaveValue("素材库分析情况怎样？");
    expect(mocks.materialAsk).not.toHaveBeenCalled();
    expect(onResult).not.toHaveBeenCalled();
  });

  it("binds selected-content questions to the exact selection and skips tag readback", async () => {
    configuredModel();
    const selection = { scope: "segment" as const, asset_id: "asset-1", segment_id: "segment-2" };
    const onResult = vi.fn();
    mocks.materialAsk.mockResolvedValue({ reply: "这个片段展示了门店入口。", provider: "minimax", model: "MiniMax-M3" });
    render(<HomeCommandComposer materialContext={{ selection, label: "片段 0:02–0:04", onResult }} />);
    const input = await readyInput("素材对话");
    fireEvent.change(input, { target: { value: "这个片段画面里有什么？" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(mocks.materialAsk).toHaveBeenCalledWith(
      "这个片段画面里有什么？", expect.any(String), selection, expect.any(Function),
    ));
    expect(await screen.findByText("这个片段展示了门店入口。")).toBeInTheDocument();
    expect(onResult).not.toHaveBeenCalled();
  });

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
    fireEvent.change(await readyInput("素材对话"), { target: { value: "这个片段的标签帮我改一下" } });
    fireEvent.submit(screen.getByRole("textbox", { name: "素材对话" }).closest("form")!);
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
      client_request_id: expect.any(String), expected_draft_version: expect.any(Number),
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

  it("keeps the old turn in the same conversation after switching tasks without opening its preview", async () => {
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
    expect(await screen.findByText("第一条已处理")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "修改预览" })).not.toBeInTheDocument();
  });

  it("records turn_started after navigation and does not restore a request the server already consumed", async () => {
    configuredModel();
    let deliver!: (event: { type: "turn_started"; turn_id: string; turn_state: "running"; draft_version: number }) => void;
    let rejectTurn!: (cause: Error) => void;
    let submittedDraftVersion = 0;
    mocks.sendTaskTurn.mockImplementationOnce((_taskId: string, payload: { conversation_id: string; message: string; client_request_id: string; expected_draft_version: number }, onEvent: typeof deliver) => {
      submittedDraftVersion = payload.expected_draft_version;
      const saved = mocks.saved.get(payload.conversation_id)!;
      mocks.saved.set(saved.id, { ...saved, draft_text: "", draft_version: payload.expected_draft_version + 1 });
      mocks.receipts.set(payload.client_request_id, { request: `${payload.conversation_id}:${payload.message}:${payload.expected_draft_version}` });
      deliver = onEvent;
      return new Promise((_resolve, reject) => { rejectTurn = reject; });
    });
    const first = { id: "task-1", stage: "montage", name: "第一条" } as CreationTask;
    const second = { id: "task-2", stage: "montage", name: "第二条" } as CreationTask;
    const view = render(<HomeCommandComposer task={first} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.sendTaskTurn).toHaveBeenCalledOnce());
    const oldConversationId = mocks.sendTaskTurn.mock.calls[0][1].conversation_id;

    view.rerender(<HomeCommandComposer task={second} />);
    await act(async () => {
      deliver({ type: "turn_started", turn_id: "persisted-after-navigation", turn_state: "running", draft_version: submittedDraftVersion + 1 });
      rejectTurn(new Error("响应连接中断"));
    });

    await waitFor(() => expect(mocks.restoreDraft).toHaveBeenCalledWith(oldConversationId,
      expect.objectContaining({ user_message: "调亮", expected_draft_version: submittedDraftVersion })));
    expect(mocks.saved.get(oldConversationId)?.draft_text).toBe("");
    expect(mocks.saved.get(oldConversationId)?.draft_version).toBe(submittedDraftVersion + 1);
    expect(mocks.updateConversation.mock.calls.filter(([id]) => id === oldConversationId)).toHaveLength(1);
    expect(screen.queryByText("调亮")).not.toBeInTheDocument();
  });
});
