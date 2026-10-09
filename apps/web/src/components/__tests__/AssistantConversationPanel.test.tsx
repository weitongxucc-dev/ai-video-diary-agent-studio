import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssistantConversationPanel } from "../AssistantConversationPanel";
import { useAssistantConversation } from "../useAssistantConversation";
import type { AssistantConversation, AssistantStreamEvent } from "@/lib/types";

const api = vi.hoisted(() => ({
  getAssistantCapabilities: vi.fn(),
  getCurrentAssistantConversation: vi.fn(),
  listAssistantConversations: vi.fn(),
  updateAssistantConversation: vi.fn(),
  createAssistantConversation: vi.fn(),
  resumeAssistantConversation: vi.fn(),
  getAssistantConversation: vi.fn(),
  stopAssistantConversationTurn: vi.fn(),
}));
vi.mock("@/lib/api", () => ({
  ...api,
  getAssistantCapabilities: api.getAssistantCapabilities.mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    pi_tui_commands: { available: false, reason: "不接通", version: "1.1.0", commands: ["/session"] },
    extension_commands: { available: false, reason: "当前不可用" },
    skills: { available: false, reason: "当前不可用" },
    prompt_templates: { available: false, reason: "当前不可用" },
    business_tools: { home: ["read_workspace_context"] },
  }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function conversation(): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: "home", scope_id: "home",
    title: "新对话", draft_text: "", created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z",
    turns: [{
      id: "turn-one", sequence: 1, user_message: "请先读当前方案",
      assistant_text: "已读取 **真实方案**。\n\n下一步可查看「本轮结果」。",
      status: "completed", created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:01Z",
      events: [
        { type: "tool_start", tool_id: 1, label: "读取当前方案" },
        { type: "text_delta", delta: "已读取 **真实方案**。\n\n下一步可查看「本轮结果」。" },
        { type: "tool_end", tool_id: 1, label: "读取当前方案", ok: true, summary: "已读取 1 项方案。" },
        { type: "tool_start", tool_id: 2, label: "检查新建方案" },
        { type: "tool_end", tool_id: 2, label: "检查新建方案", ok: false, summary: "这一步没有完成。" },
      ],
      result: { kind: "plan_preview", plan_preview: { confirmable: false } },
    }],
  };
}

function state(overrides: Record<string, unknown> = {}) {
  return {
    conversation: conversation(), conversations: [], loading: false, error: null,
    createNew: vi.fn(), resume: vi.fn(), rename: vi.fn(), stop: vi.fn(),
    isCurrentConversation: vi.fn((id: string) => id === conversation().id),
    ...overrides,
  } as never;
}

describe("AssistantConversationPanel", () => {
  it("renders one Markdown reply with tool results in their actual event order", async () => {
    render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);

    expect(screen.getByText("真实方案").tagName).toBe("STRONG");
    expect(screen.getAllByRole("button", { name: "复制回复" })).toHaveLength(1);
    const firstTool = screen.getByText("读取当前方案 · 已完成");
    const assistantText = screen.getByText("真实方案");
    const secondTool = screen.getByText("检查新建方案 · 未完成");
    expect(firstTool.compareDocumentPosition(assistantText) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(assistantText.compareDocumentPosition(secondTool) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const toolDetails = [firstTool.closest("details")!, secondTool.closest("details")!];
    expect(toolDetails.every((item) => !item.open)).toBe(true);
    fireEvent.click(firstTool);
    expect(within(toolDetails[0]).getByText("已读取 1 项方案。")).toBeInTheDocument();
    fireEvent.click(secondTool);
    expect(within(toolDetails[1]).getByText("这一步没有完成。")).toBeInTheDocument();
    expect(screen.getByText("本轮结果").closest("details")).not.toHaveAttribute("open");

    await screen.findByText("技术能力状态（诊断）");
    expect(screen.getByText("对话操作").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("技术能力状态（诊断）").closest("details")).not.toHaveAttribute("open");
  });

  it("sends on plain Enter only, preserves Shift+Enter and IME composition, and restores input focus", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<AssistantConversationPanel state={state()} input="下一条要求" onInput={vi.fn()} onSend={onSend}
      busy={false} configured scopeLabel="首页创作" />);
    const input = screen.getByRole("textbox", { name: "对话要求" });

    fireEvent.keyDown(input, { key: " " });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("下一条要求");
    await Promise.resolve();
    expect(document.activeElement).toBe(input);
  });

  it.each(["/new", "/resume conversation-two"]) ("loads the returned draft after %s instead of the previous selection", async (command) => {
    const next = { ...conversation(), id: command.startsWith("/new") ? "conversation-new" : "conversation-two",
      draft_text: "恢复到这个会话的草稿", turns: [] };
    const onInput = vi.fn();
    const current = state({
      createNew: vi.fn().mockResolvedValue(next),
      resume: vi.fn().mockResolvedValue(next),
    });
    render(<AssistantConversationPanel state={current} input={command} onInput={onInput} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);

    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(onInput).toHaveBeenCalledWith(next.draft_text));
  });

  it("keeps an invalid application command visible and does not send it to Pi", async () => {
    const onSend = vi.fn();
    const createNew = vi.fn();
    const current = state({ createNew });
    render(<AssistantConversationPanel state={current} input="/new unexpected" onInput={vi.fn()} onSend={onSend}
      busy={false} configured scopeLabel="首页创作" />);
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("此命令格式不正确");
    expect(createNew).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps a restored running turn as the send gate and exposes its stop action", () => {
    const running = conversation();
    running.turns = [{ ...running.turns[0], id: "restored-running-turn", status: "running" }];
    const stop = vi.fn();
    render(<AssistantConversationPanel state={state({ conversation: running, stop })} input="继续写" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);
    expect(screen.getByRole("button", { name: "本轮运行中" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "停止本轮" })).toBeInTheDocument();
  });

  it("keeps a late stream callback bound to the conversation and turn that sent it", async () => {
    const first = conversation();
    const second = { ...conversation(), id: "conversation-two", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(first);
    api.listAssistantConversations.mockResolvedValue({ items: [first] });
    api.updateAssistantConversation.mockImplementation(async (id: string) => ({ ...(id === first.id ? first : second), draft_text: "" }));
    api.createAssistantConversation.mockResolvedValue(second);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));

    let stream: (event: AssistantStreamEvent) => void = () => undefined;
    await act(async () => {
      const turn = await result.current.beginTurn("旧会话发送中的请求");
      stream = result.current.bindTurnEvents(turn.conversationId, turn.localTurnId);
    });
    await act(async () => { await result.current.createNew(); });
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.isCurrentConversation(first.id)).toBe(false);
    expect(result.current.isCurrentConversation(second.id)).toBe(true);
    act(() => stream({ type: "text_delta", delta: "迟到的旧流事件" }));
    expect(result.current.conversation?.turns).toHaveLength(0);
  });

  it("shows a sent user turn immediately without waiting for the server request", async () => {
    const initial = { ...conversation(), draft_text: "上次未提交草稿", turns: [] };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    const updatesBeforeBegin = api.updateAssistantConversation.mock.calls.length;

    let created!: ReturnType<typeof result.current.beginTurn>;
    act(() => { created = result.current.beginTurn("即时显示这条要求"); });
    expect(created.conversationId).toBe(initial.id);
    expect(result.current.conversation?.turns.at(-1)?.user_message).toBe("即时显示这条要求");
    expect(result.current.conversation?.turns.at(-1)?.status).toBe("running");
    expect(result.current.draftText).toBe("");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(updatesBeforeBegin);
  });

  it("turns an unpersisted failed optimistic turn into a retryable draft", async () => {
    const saved = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    api.getAssistantConversation.mockResolvedValue(saved);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(saved.id));
    let localTurnId = "";
    await act(async () => {
      const turn = result.current.beginTurn("请再试一次读取当前方案");
      localTurnId = turn.localTurnId;
      await result.current.refresh();
      expect(await result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "网络请求未能建立轮次。")).toBe(true);
    });
    expect(result.current.conversation?.turns.at(-1)?.id).toBe(localTurnId);
    expect(result.current.conversation?.turns.at(-1)?.status).toBe("failed");
    expect(result.current.draftText).toBe("请再试一次读取当前方案");
  });

  it("reconciles a recovered server turn instead of leaving an optimistic duplicate running", async () => {
    const saved = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(saved.id));
    let localTurnId = "";
    await act(async () => {
      const pending = result.current.beginTurn("检查一条正在处理的请求");
      localTurnId = pending.localTurnId;
    });
    const persisted = { ...saved.turns[0], id: "persisted-running", sequence: 2,
      user_message: "检查一条正在处理的请求", status: "running" as const,
      assistant_text: "", events: [], result: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    api.getAssistantConversation.mockResolvedValue({ ...saved, turns: [...saved.turns, persisted] });
    await act(async () => { await result.current.refresh(); });
    expect(result.current.conversation?.turns.at(-1)?.id).toBe("persisted-running");
    expect(result.current.conversation?.turns.filter((turn) => turn.id === localTurnId)).toHaveLength(0);
    const stream = result.current.bindTurnEvents(saved.id, localTurnId);
    act(() => stream({ type: "turn_started", turn_id: "persisted-running", turn_state: "running" }));
    act(() => stream({ type: "text_delta", delta: "服务端已开始" }));
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("服务端已开始");
  });

  it("does not let a stale conversation refresh replace newer streamed text", async () => {
    const initial = { ...conversation(), turns: [] };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    let resolveFetch!: (value: AssistantConversation) => void;
    api.getAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let stream!: (event: AssistantStreamEvent) => void;
    act(() => {
      const turn = result.current.beginTurn("新一轮");
      stream = result.current.bindTurnEvents(turn.conversationId, turn.localTurnId);
      stream({ type: "turn_started", turn_id: "turn-live", turn_state: "running" });
    });
    let refresh!: Promise<AssistantConversation | null>;
    act(() => { refresh = result.current.refresh(); });
    act(() => { stream({ type: "text_delta", delta: "新鲜回复" }); });
    resolveFetch({ ...initial, turns: [{ id: "turn-live", sequence: 1, user_message: "新一轮",
      assistant_text: "旧快照", status: "running", events: [], result: null,
      created_at: initial.created_at, updated_at: initial.updated_at }] });
    await act(async () => { await refresh; });
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("新鲜回复");
  });

  it("preserves and persists a local draft created while the initial conversation is loading", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    let resolveCurrent!: (value: AssistantConversation) => void;
    api.getCurrentAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveCurrent = resolve; }));
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    api.updateAssistantConversation.mockImplementation(async (_id: string, update: { draft_text?: string }) => ({ ...initial, draft_text: update.draft_text ?? "" }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));

    act(() => result.current.setDraftText("提取当前镜头原声"));
    await act(async () => { resolveCurrent(initial); });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.draftText).toBe("提取当前镜头原声");
    expect(api.updateAssistantConversation).toHaveBeenCalledWith(initial.id, { draft_text: "提取当前镜头原声" });
  });
});
