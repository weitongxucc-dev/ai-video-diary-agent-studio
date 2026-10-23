import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { AssistantConversationContext } from "../AssistantConversationContext";
import { useScopedAssistantConversation } from "../useAssistantConversation";
import type { AssistantConversation, AssistantConversationTurn, CreationTask } from "@/lib/types";
import type { AssistantBusinessContinuation, AssistantStoryboardRegeneration } from "@/lib/types/assistant";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
const restoreApi=vi.hoisted(()=>({read:vi.fn(async(taskId:string,requestId:string)=>({task_id:taskId,request_id:requestId,
  action:'undo',status:'applied',operation_id:'restored-operation',result_version_id:'restored-version',failure:null}))}));

const queuedStoryboard: AssistantStoryboardRegeneration = {
  kind: "storyboard_regeneration_receipt", action: "submit",
  receipt: {request_id: "request-original", task_id: "task-1", job_id: "job-storyboard", status: "queued",
    stage: "montage_plan", script_id: "script-1", narration_cache_key: "narration-original",
    base_timeline_version_id: "version-original", base_timeline_sequence: 1,
    result: null, failure_reason: null,
    result_url: "/api/creation-tasks/task-1/montage/plan/requests/request-original"},
};

const mocks = vi.hoisted(() => ({ selection: null as ReturnType<typeof import("../AssistantConversationProvider").useAssistantSelection>, saved: new Map<string, import("@/lib/types").AssistantConversation>(), receipts: new Map<string, unknown>(), sequence: 0, ask: vi.fn(), materialAsk: vi.fn(), preflight: vi.fn(), confirm: vi.fn(), settings: vi.fn(), editAsk: vi.fn(), confirmEdit: vi.fn(), undoEdit: vi.fn(), updateConversation: vi.fn(), sendTaskTurn: vi.fn(), restoreDraft: vi.fn() }));
vi.mock("@/components/AssistantConversationProvider", async importOriginal => ({
  ...(await importOriginal<typeof import("../AssistantConversationProvider")>()),
  useAssistantSelection: () => mocks.selection,
}));
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
        : result.execution_action || result.script_draft || result.topic_candidates || result.storyboard_regeneration || result.timeline_action || result.continuation
          ? { kind: "task_turn", execution_action: result.execution_action, execution_status: result.execution_status,
              script_draft: result.script_draft ? "saved" : null, topic_candidates: Boolean(result.topic_candidates),
              timeline_action: result.timeline_action, continuation: result.continuation,
              edit_preview: Boolean(result.edit_preview), ...(result.storyboard_regeneration ? {response: result} : {}) }
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
  readCreationTaskTimelineRestore: restoreApi.read,
  };
});

afterEach(() => { mocks.selection = null; vi.clearAllMocks(); vi.useRealTimers(); });

describe("HomeCommandComposer", () => {
  function configuredModel() {
    mocks.settings.mockResolvedValue({ status: { llm: { configured: true, provider: "minimax", model: "MiniMax-M3" } } });
  }

  async function readyInput(label: string) {
    const input = screen.getByRole("textbox", { name: label });
    await waitFor(() => expect(input).toBeEnabled());
    return input;
  }

  function TaskConversation({task, onTaskUpdated}: {task: CreationTask; onTaskUpdated: () => void}) {
    const assistant = useScopedAssistantConversation({scope_kind: "home", scope_id: "home"});
    return <AssistantConversationContext.Provider value={assistant}>
      <button type="button" onClick={() => void assistant.refresh()}>回读持久会话</button>
      <button type="button" onClick={() => void assistant.createNew()}>切换持久会话</button>
      <output aria-label="持久会话ID">{assistant.conversation?.id}</output>
      <output aria-label="持久回合更新时间">{assistant.conversation?.turns.at(-1)?.updated_at}</output>
      <HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated}/>
    </AssistantConversationContext.Provider>;
  }

  function appliedReceipt(operationId: string): AssistantBusinessContinuation {
    return {type: "business_result", event_id: `event-${operationId}`, task_id: "task-1", phase: "apply",
      status: "confirmed", actionable: false, operation_id: operationId, result_version_id: `version-${operationId}`};
  }

  function persistedReceiptTurn(id: string, continuation: unknown): AssistantConversationTurn {
    return {id, sequence: 2, status: "completed", user_message: "", assistant_text: "已回读持久回合", events: [],
      result: {kind: "task_turn", continuation}, created_at: "2026-10-18T11:00:00Z", updated_at: "2026-10-18T11:00:00Z"};
  }

  it("refreshes persisted apply receipts from the initial turn and a second autonomous edit once each", async () => {
    configuredModel();
    const task = {id: "task-1", stage: "montage", name: "当前作品"} as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.selection = {version: 7, items: [{kind: "shot", task_id: task.id, object_id: "clip-1", version: "v1", label: "镜头 1"}],
      select: vi.fn(), remove: vi.fn()};
    const fixedReferences = structuredClone(mocks.selection.items);
    mocks.editAsk.mockResolvedValueOnce({reply: "标题已保存，继续调整声音。", provider: "fixture", model: "fixture",
      edit_preview: null, script_draft: null, execution_action: null, topic_candidates: null,
      timeline_action: "apply", continuation: appliedReceipt("operation-title")});
    const view = render(<TaskConversation task={task} onTaskUpdated={onTaskUpdated}/>);
    const input = await readyInput("任务对话要求");
    fireEvent.change(input, {target: {value: "修改标题，再降低声音"}});
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeDisabled());
    const conversationId = screen.getByLabelText("持久会话ID").textContent!;
    fireEvent.change(input, {target: {value: "下一条草稿保持"}});
    const saved = mocks.saved.get(conversationId)!;
    const child = persistedReceiptTurn("autonomous-audio-turn", appliedReceipt("operation-audio"));
    mocks.saved.set(conversationId, {...saved, turns: [...saved.turns, child]});
    fireEvent.click(screen.getByRole("button", {name: "回读持久会话"}));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledTimes(2));
    expect(input).toHaveValue("下一条草稿保持");
    expect(mocks.selection.items).toEqual(fixedReferences);
    expect(mocks.selection.select).not.toHaveBeenCalled();
    expect(mocks.selection.remove).not.toHaveBeenCalled();
    expect(mocks.sendTaskTurn).toHaveBeenCalledOnce();

    const repeated = mocks.saved.get(conversationId)!;
    mocks.saved.set(conversationId, {...repeated, turns: repeated.turns.map(turn => ({...turn, updated_at: "2026-10-18T11:00:01Z"}))});
    fireEvent.click(screen.getByRole("button", {name: "回读持久会话"}));
    await waitFor(() => expect(screen.getByLabelText("持久回合更新时间")).toHaveTextContent("2026-10-18T11:00:01Z"));
    view.rerender(<TaskConversation task={{...task, name: "已回读的同一作品"}} onTaskUpdated={() => onTaskUpdated()}/>);
    expect(onTaskUpdated).toHaveBeenCalledTimes(2);
    expect(input).toHaveValue("下一条草稿保持");
    expect(mocks.sendTaskTurn).toHaveBeenCalledOnce();
  });

  it.each([
    {phase: "preview"}, {status: "unknown"}, {status: "failed"}, {task_id: "task-other"},
    {operation_id: null}, {result_version_id: null}, {type: "model_text"}, {textOnly: true},
  ])("ignores unconfirmed, incomplete or foreign persisted apply receipts (%#)", async invalid => {
    configuredModel();
    const onTaskUpdated = vi.fn();
    render(<TaskConversation task={{id: "task-1", stage: "montage", name: "当前作品"} as CreationTask} onTaskUpdated={onTaskUpdated}/>);
    await readyInput("任务对话要求");
    const conversationId = screen.getByLabelText("持久会话ID").textContent!;
    const saved = mocks.saved.get(conversationId)!;
    const turn = persistedReceiptTurn("invalid-receipt", {...appliedReceipt("operation-unconfirmed"), ...invalid});
    if ("textOnly" in invalid) turn.result = {kind: "task_turn"};
    mocks.saved.set(conversationId, {...saved, turns: [turn]});
    fireEvent.click(screen.getByRole("button", {name: "回读持久会话"}));
    expect(await screen.findByText("已回读持久回合")).toBeVisible();
    expect(onTaskUpdated).not.toHaveBeenCalled();
    expect(mocks.sendTaskTurn).not.toHaveBeenCalled();
  });

  it.each(["task", "conversation", "unmount"])("isolates a late persisted apply read after changing %s", async change => {
    configuredModel();
    const task = {id: "task-1", stage: "montage", name: "原作品"} as CreationTask;
    const onTaskUpdated = vi.fn();
    const view = render(<TaskConversation task={task} onTaskUpdated={onTaskUpdated}/>);
    await readyInput("任务对话要求");
    const conversationId = screen.getByLabelText("持久会话ID").textContent!;
    const saved = mocks.saved.get(conversationId)!;
    const api = await import("@/lib/api");
    let finish!: (value: AssistantConversation) => void;
    vi.mocked(api.getAssistantConversation).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    fireEvent.click(screen.getByRole("button", {name: "回读持久会话"}));
    await waitFor(() => expect(api.getAssistantConversation).toHaveBeenCalledOnce());
    if (change === "task") view.rerender(<TaskConversation task={{...task, id: "task-other"}} onTaskUpdated={onTaskUpdated}/>);
    else if (change === "conversation") {
      fireEvent.click(screen.getByRole("button", {name: "切换持久会话"}));
      await waitFor(() => expect(screen.getByLabelText("持久会话ID").textContent).not.toBe(conversationId));
    } else view.unmount();
    await act(async () => finish({...saved, turns: [persistedReceiptTurn("late-applied-turn", appliedReceipt("operation-late"))]}));
    expect(onTaskUpdated).not.toHaveBeenCalled();
  });

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

  it("keeps a task draft unsent when its persisted reference changes during the draft checkpoint", async () => {
    configuredModel();
    const task = {id:"task-1",stage:"montage",name:"产品介绍",current_timeline_version_id:"v1",timeline_sequence:1} as CreationTask;
    mocks.selection={version:1,items:[{kind:"shot",task_id:task.id,object_id:"clip-1",version:"v1",label:"镜头 1"}],select:vi.fn(),remove:vi.fn()};
    let releaseInitialSave!:()=>void;
    mocks.updateConversation.mockImplementationOnce((id:string,update:{draft_text?:string;expected_draft_version?:number})=>new Promise(resolve=>{
      releaseInitialSave=()=>{const existing=mocks.saved.get(id)!;const {expected_draft_version:_expected,...changes}=update;const updated={...existing,...changes,draft_version:existing.draft_version+1};mocks.saved.set(id,updated);resolve(updated);};
    }));
    const view=render(<HomeCommandComposer task={task}/>);
    const input=await readyInput("任务对话要求");
    vi.useFakeTimers();
    fireEvent.change(input,{target:{value:"调整原镜头，保留这段草稿"}});
    await act(async()=>{await vi.advanceTimersByTimeAsync(451);});
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    mocks.selection={...mocks.selection,version:2,items:[{kind:"shot",task_id:task.id,object_id:"clip-2",version:"v1",label:"镜头 2"}]};
    view.rerender(<HomeCommandComposer task={task}/>);
    await act(async()=>{releaseInitialSave();await Promise.resolve();await Promise.resolve();});
    vi.useRealTimers();
    await waitFor(()=>expect(input).toHaveValue("调整原镜头，保留这段草稿"));
    expect(mocks.sendTaskTurn).not.toHaveBeenCalled();expect(mocks.materialAsk).not.toHaveBeenCalled();expect(mocks.ask).not.toHaveBeenCalled();
    expect(await screen.findByText("会话或引用已切换；本轮没有发送，原要求已保留为草稿。",{exact:false})).toBeVisible();
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
    const toolSummary = screen.getByText("工具记录 · 1 项 · 调用已结束");
    expect(toolSummary.compareDocumentPosition(finalBody) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(toolSummary);
    expect(screen.getByText("已读取 1 项任务摘要。")).toBeInTheDocument();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it("keeps an existing task in the chat, previews a change, confirms and undoes it", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "产品介绍", can_undo: false, current_timeline_version_id: "v1", timeline_sequence: 1 } as CreationTask;
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
    const view = render(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={task} />);

    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "把画面调亮" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.editAsk).toHaveBeenCalledWith("task-1", {
      conversation_id: expect.any(String), message: "把画面调亮", context_stage: "montage", selected_object_refs: [], playhead_ms: null,
      client_request_id: expect.any(String), expected_draft_version: expect.any(Number),
      expected_timeline_version_id: "v1", expected_timeline_sequence: 1,
    }, expect.any(Function)));
    expect(await screen.findByRole("dialog", { name: "修改预览" })).toHaveTextContent("调亮画面");
    expect(mocks.confirmEdit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalledWith("task-1", "proposal-1"));
    expect(onTaskUpdated).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    view.rerender(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={{ ...task, current_timeline_version_id: "v2", timeline_sequence: 2, can_undo: true }} />);
    fireEvent.click(screen.getByRole("button", { name: "撤销修改" }));
    await waitFor(() => expect(mocks.undoEdit).toHaveBeenCalledWith("task-1", {
      expected_timeline_version_id: "v2", expected_timeline_sequence: 2, request_id:expect.any(String),
    }));
    expect(onTaskUpdated).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    view.rerender(<HomeCommandComposer onTaskUpdated={onTaskUpdated} task={{ ...task, current_timeline_version_id: "v3", timeline_sequence: 3, can_undo: false }} />);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
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

  it.each(["draft", "confirmed"])("refreshes the targeted task after a home conversation returns a %s script receipt", async selectionStatus => {
    configuredModel();
    const task = { id: "task-1", stage: "script", name: "产品介绍" } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.editAsk.mockResolvedValueOnce({
      reply: selectionStatus === "confirmed" ? "实际文案版本已采用，尚未生成成片。" : "文案草稿已保存，尚未采用。",
      provider: "fixture", model: "fixture", edit_preview: null,
      script_draft: { id: "script-1", task_id: task.id, selection_status: selectionStatus },
      execution_action: null, topic_candidates: null, timeline_action: null,
    });
    render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "按现有资料整理这份文案" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledOnce());
    const payload = mocks.editAsk.mock.calls[0][1];
    expect(mocks.saved.get(payload.conversation_id)).toMatchObject({ scope_kind: "home", scope_id: "home" });
    expect(mocks.editAsk).toHaveBeenCalledWith(task.id, expect.objectContaining({ context_stage: "script" }), expect.any(Function));
    expect(mocks.ask).not.toHaveBeenCalled();
  });

  it.each(["submit", "query", "unknown", "rejected"] as const)("refreshes the targeted task for a storyboard %s result", async mode => {
    configuredModel();
    const onTaskUpdated = vi.fn();
    const storyboard: AssistantStoryboardRegeneration = mode === "unknown" || mode === "rejected"
      ? {kind: mode === "unknown" ? "storyboard_regeneration_unknown" : "storyboard_regeneration_rejected",
        action: "submit", request_id: "request-original", query_url: queuedStoryboard.receipt.result_url,
        failure: {reason: "原请求结果需核对。", repair_action: "查询原请求，不重复提交。"}}
      : {...queuedStoryboard, action: mode};
    mocks.editAsk.mockResolvedValueOnce({reply: "故事板原请求状态已登记。", provider: "fixture", model: "fixture",
      edit_preview: null, script_draft: null, execution_action: null, topic_candidates: null, timeline_action: null,
      storyboard_regeneration: storyboard});
    render(<HomeCommandComposer task={{id: "task-1", stage: "montage", name: "当前任务"} as CreationTask} onTaskUpdated={onTaskUpdated}/>);
    fireEvent.change(await readyInput("任务对话要求"), {target: {value: "重新生成故事板"}});
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledOnce());
    const originalConversationId = mocks.editAsk.mock.calls[0][1].conversation_id;
    expect(mocks.saved.get(originalConversationId)?.turns.at(-1)?.result).toMatchObject({kind: "task_turn",
      response: {storyboard_regeneration: storyboard}});
    expect(mocks.editAsk).toHaveBeenCalledOnce();
  });

  it("reads current task facts after a typed version conflict, preserving the unsent draft and fixed references", async () => {
    configuredModel();
    const task = {id: "task-1", stage: "montage", name: "当前作品",
      current_timeline_version_id: "v1", timeline_sequence: 1} as CreationTask;
    mocks.selection = {version: 7, items: [{kind: "shot", task_id: task.id, object_id: "clip-1", version: "v1", label: "镜头 1"}],
      select: vi.fn(), remove: vi.fn()};
    const fixedReferences = structuredClone(mocks.selection.items);
    const onTaskUpdated = vi.fn();
    mocks.sendTaskTurn.mockRejectedValueOnce(new CreationPlanApiError(409, "creation_task_timeline_version_conflict", "当前任务版本已变化，请重新查看选中对象后发送。"));
    const view = render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated}/>);
    const input = await readyInput("任务对话要求");
    fireEvent.change(input, {target: {value: "核对并补齐片尾图层"}});
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledOnce());
    expect(input).toHaveValue("核对并补齐片尾图层");
    expect(screen.getByText("作品版本已变化，正在重新读取；原要求与引用保持原值，暂不发送。")).toBeVisible();
    expect(screen.getByRole("button", {name: "正在处理"})).toBeDisabled();
    const originalConversationId = mocks.sendTaskTurn.mock.calls[0][1].conversation_id;
    expect(mocks.saved.get(originalConversationId)?.turns).toHaveLength(0);
    expect(mocks.selection.items).toEqual(fixedReferences);
    expect(mocks.selection.select).not.toHaveBeenCalled();
    expect(mocks.selection.remove).not.toHaveBeenCalled();

    view.rerender(<HomeCommandComposer task={{...task, current_timeline_version_id: "v2", timeline_sequence: 2}} onTaskUpdated={onTaskUpdated}/>);
    expect(screen.getByText("当前作品已重新读取，请核对内容与引用后再发送；原要求已保留。")).toBeVisible();
    expect(input).toHaveValue("核对并补齐片尾图层");
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    expect(mocks.selection.items).toEqual(fixedReferences);
    expect(mocks.sendTaskTurn).toHaveBeenCalledOnce();
    expect(onTaskUpdated).toHaveBeenCalledOnce();
    expect(mocks.saved.get(originalConversationId)?.turns).toHaveLength(0);
  });

  it.each([
    new CreationPlanApiError(409, "another_conflict", "当前任务版本已变化，请重新查看选中对象后发送。"),
    new CreationPlanApiError(403, "creation_task_timeline_version_conflict", "当前权限不可用。"),
    new CreationPlanApiError(409, null, "当前任务版本已变化，请重新查看选中对象后发送。"),
    new Error("当前任务版本已变化，请重新查看选中对象后发送。"),
  ])("keeps non-target errors unchanged without refreshing the task (%#)", async failure => {
    configuredModel();
    const onTaskUpdated = vi.fn();
    mocks.sendTaskTurn.mockRejectedValueOnce(failure);
    render(<HomeCommandComposer task={{id: "task-1", stage: "montage", name: "当前作品"} as CreationTask} onTaskUpdated={onTaskUpdated}/>);
    const input = await readyInput("任务对话要求");
    fireEvent.change(input, {target: {value: "保留未发送要求"}});
    await waitFor(() => expect(screen.getByRole("button", {name: "发送"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    expect(await screen.findAllByText(failure.message)).not.toHaveLength(0);
    expect(input).toHaveValue("保留未发送要求");
    expect(onTaskUpdated).not.toHaveBeenCalled();
    expect(mocks.sendTaskTurn).toHaveBeenCalledOnce();
  });

  it.each(["task", "conversation", "unmount"].flatMap(change => ["script", "storyboard", "version_conflict"].map(kind => [change, kind])))
  ("does not refresh after changing %s with a late %s receipt", async (change, kind) => {
    configuredModel();
    let finish!: (value: unknown) => void;
    const result = new Promise((resolve, reject) => { finish = kind === "version_conflict" ? reject : resolve; });
    if (kind === "version_conflict") mocks.sendTaskTurn.mockReturnValueOnce(result);
    else mocks.editAsk.mockReturnValueOnce(result);
    const task = { id: "task-1", stage: "script", name: "原任务" } as CreationTask;
    const onTaskUpdated = vi.fn();
    function SharedConversation({ selectedTask }: { selectedTask: CreationTask }) {
      const assistant = useScopedAssistantConversation({ scope_kind: "home", scope_id: "home" });
      return <AssistantConversationContext.Provider value={assistant}>
        <button type="button" onClick={() => void assistant.createNew()}>切换共享会话</button>
        <output aria-label="共享会话ID">{assistant.conversation?.id}</output>
        <HomeCommandComposer task={selectedTask} onTaskUpdated={onTaskUpdated} />
      </AssistantConversationContext.Provider>;
    }
    const view = render(<SharedConversation selectedTask={task} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "保存当前文案" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.sendTaskTurn).toHaveBeenCalledOnce());
    const originalConversationId = mocks.sendTaskTurn.mock.calls[0][1].conversation_id;
    if (change === "task") view.rerender(<SharedConversation selectedTask={{ ...task, id: "task-2" }} />);
    else if (change === "conversation") {
      fireEvent.click(screen.getByRole("button", { name: "切换共享会话" }));
      await waitFor(() => expect(screen.getByLabelText("共享会话ID").textContent).not.toBe(originalConversationId));
    } else view.unmount();
    await act(async () => finish(kind === "version_conflict"
      ? new CreationPlanApiError(409, "creation_task_timeline_version_conflict", "当前任务版本已变化。") : {
      reply: "原请求已回读。", provider: "fixture", model: "fixture", edit_preview: null,
      script_draft: kind === "script" ? { id: "script-1", task_id: task.id, selection_status: "confirmed" } : null,
      storyboard_regeneration: kind === "storyboard" ? queuedStoryboard : null,
      execution_action: null, topic_candidates: null, timeline_action: null,
    }));
    expect(onTaskUpdated).not.toHaveBeenCalled();
    if (kind !== "version_conflict") expect(mocks.saved.get(originalConversationId)?.turns.at(-1)?.result).toMatchObject(kind === "script"
      ? { kind: "task_turn", script_draft: "saved" }
      : { kind: "task_turn", response: {storyboard_regeneration: queuedStoryboard} });
    expect(mocks.editAsk).toHaveBeenCalledTimes(kind === "version_conflict" ? 0 : 1);
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

  it("uses same-task server undo facts while retaining the draft and conversation", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "测试任务", current_timeline_version_id: "v1", timeline_sequence: 1, can_undo: true } as CreationTask;
    const view = render(<HomeCommandComposer task={task} />);
    const input = await readyInput("任务对话要求");
    fireEvent.change(input, { target: { value: "下一轮草稿保持" } });
    const reads = mocks.saved.size;
    expect(screen.getByRole("button", { name: "撤销修改" })).toBeEnabled();
    view.rerender(<HomeCommandComposer task={{ ...task, current_timeline_version_id: "v2", timeline_sequence: 2, can_undo: false }} />);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    expect(input).toHaveValue("下一轮草稿保持");
    expect(mocks.saved.size).toBe(reads);
    view.rerender(<HomeCommandComposer task={{ ...task, current_timeline_version_id: "v3", timeline_sequence: 3, can_undo: undefined } as unknown as CreationTask} />);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    expect(input).toHaveValue("下一轮草稿保持");
  });

  it("waits for a post-command read even when the same task was read during confirmation", async () => {
    configuredModel();
    let finish!: (result: unknown) => void;
    const onTaskUpdated = vi.fn();
    const task = { id: "task-1", stage: "montage", name: "测试任务", current_timeline_version_id: "v1", can_undo: true } as CreationTask;
    mocks.editAsk.mockResolvedValue({ reply: "修改待确认", provider: "fixture", model: "fixture", edit_preview: { proposal: {
      id: "proposal-a", summary: "测试编辑", invalidated_refs: [], external_effects: [],
      preview: { before: { label: "原稿", detail: "当前版本" }, after: { label: "修改", detail: "待应用" } },
    } } });
    mocks.confirmEdit.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const view = render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "测试编辑" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改预览" });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalled());
    view.rerender(<HomeCommandComposer task={{ ...task, name: "读取中的同一任务" }} onTaskUpdated={onTaskUpdated} />);
    await act(async () => finish({ operation: { id: "op-a" } }));
    expect(onTaskUpdated).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    view.rerender(<HomeCommandComposer task={{ ...task, current_timeline_version_id: "v2", can_undo: true }} onTaskUpdated={onTaskUpdated} />);
    expect(screen.getByRole("button", { name: "撤销修改" })).toBeEnabled();
  });

  it("rechecks task facts after an undo error rather than re-enabling the old undo bit", async () => {
    configuredModel();
    const task = { id: "task-1", stage: "montage", name: "测试任务", current_timeline_version_id: "v1", timeline_sequence: 1, can_undo: true } as CreationTask;
    const onTaskUpdated = vi.fn();
    mocks.undoEdit.mockRejectedValueOnce(new Error("当前版本已变化"));
    const view = render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated} />);
    await readyInput("任务对话要求");
    fireEvent.click(screen.getByRole("button", { name: "撤销修改" }));
    await waitFor(() => expect(onTaskUpdated).toHaveBeenCalledOnce());
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
    expect(screen.getByText("正在回读当前草稿，撤销暂不可用。")).toBeInTheDocument();
    view.rerender(<HomeCommandComposer task={{ ...task, current_timeline_version_id: "v2", can_undo: false }} onTaskUpdated={onTaskUpdated} />);
    expect(screen.queryByRole("button", { name: "撤销修改" })).not.toBeInTheDocument();
  });

  it.each(['not_submitted','rejected'] as const)('home undo readback %s cannot enable a new write UUID',async effect=>{
    configuredModel();
    const task={id:'task-1',stage:'montage',name:'测试任务',current_timeline_version_id:'v1',timeline_sequence:1,can_undo:true} as CreationTask;
    mocks.undoEdit.mockReset().mockResolvedValue({});
    restoreApi.read.mockRejectedValueOnce(new CreationPlanApiError(404,null,'原读取拒绝',null,null,null,effect));
    const onTaskUpdated=vi.fn();
    const view=render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated}/>);
    await readyInput('任务对话要求');
    fireEvent.click(screen.getByRole('button',{name:'撤销修改'}));
    await screen.findByText('原读取拒绝');
    await waitFor(()=>expect(onTaskUpdated).toHaveBeenCalledOnce());
    const requestId=mocks.undoEdit.mock.calls[0][1].request_id;
    view.rerender(<HomeCommandComposer task={{...task}} onTaskUpdated={onTaskUpdated}/>);
    expect(screen.queryByRole('button',{name:'撤销修改'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'查询原撤销结果'}));
    await waitFor(()=>expect(restoreApi.read).toHaveBeenLastCalledWith(task.id,requestId));
    expect(mocks.undoEdit).toHaveBeenCalledTimes(1);
  });

  it.each(["switch", "unmount"])("does not invoke the old task read callback after confirmation and %s", async change => {
    configuredModel();
    let finish!: (result: unknown) => void;
    const onTaskUpdated = vi.fn();
    const task = { id: "task-1", stage: "montage", name: "测试任务", can_undo: false } as CreationTask;
    mocks.editAsk.mockResolvedValue({ reply: "修改待确认", provider: "fixture", model: "fixture", edit_preview: { proposal: {
      id: "proposal-a", summary: "测试编辑", invalidated_refs: [], external_effects: [],
      preview: { before: { label: "原稿", detail: "当前版本" }, after: { label: "修改", detail: "待应用" } },
    } } });
    mocks.confirmEdit.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const view = render(<HomeCommandComposer task={task} onTaskUpdated={onTaskUpdated} />);
    fireEvent.change(await readyInput("任务对话要求"), { target: { value: "测试编辑" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("dialog", { name: "修改预览" });
    fireEvent.click(screen.getByRole("button", { name: "确认修改" }));
    await waitFor(() => expect(mocks.confirmEdit).toHaveBeenCalled());
    if (change === "switch") view.rerender(<HomeCommandComposer task={{ ...task, id: "task-2" }} onTaskUpdated={onTaskUpdated} />);
    else view.unmount();
    await act(async () => finish({ operation: { id: "op-a" } }));
    expect(onTaskUpdated).not.toHaveBeenCalled();
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
