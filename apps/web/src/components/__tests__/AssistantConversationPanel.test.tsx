import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantConversationPanel } from "../AssistantConversationPanel";
import { referenceAssistantKnowledge, referenceAssistantMaterial } from "@/lib/assistant-reference";
import { AssistantConversationProvider } from "../AssistantConversationProvider";
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
  restoreUnpersistedAssistantTurnDraft: vi.fn(),
  stopAssistantConversationTurn: vi.fn(),
  getAssistantConversationReference: vi.fn(),
  saveAssistantConversationReference: vi.fn(),
  referenceSaved:new Map<string, import("@/lib/types").AssistantConversationReferenceState>(),
}));
vi.mock("@/lib/api", () => ({
  ...api,
  getAssistantCapabilities: api.getAssistantCapabilities.mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0",
      runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查",
      product_mode: "headless", reason: "不接通", commands: ["/session"] },
    extension_commands: { available: false, reason: "当前不可用" },
    skills: { available: false, reason: "当前不可用" },
    prompt_templates: { available: false, reason: "当前不可用" },
    business_tools: { home: ["read_workspace_context"] },
  }),
}));

vi.mock("@/hooks/useLocalMemberIdentity", () => ({ useLocalMemberIdentity: () => ({ token: "trusted-member" }) }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  api.referenceSaved.clear();
  api.getAssistantConversationReference.mockImplementation(async (id:string) => api.referenceSaved.get(id) ?? {selected_reference:null,reference_version:0,status:"ready",reason:null});
  api.saveAssistantConversationReference.mockImplementation(async (id:string,reference:import("@/lib/types").AssistantSavedReference|null,version:number) => {
    const value={selected_reference:reference,reference_version:version+1,status:"ready" as const,reason:null};
    api.referenceSaved.set(id,value);return value;
  });
  api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number; title?: string }) => ({
    ...conversation(), id, title: update.title ?? "新对话", draft_text: update.draft_text ?? "",
    draft_version: (update.expected_draft_version ?? 0) + 1,
  }));
  api.restoreUnpersistedAssistantTurnDraft.mockImplementation(async (id: string, request: {
    user_message: string; expected_draft_version: number;
  }) => ({
    status: "restored",
    conversation: {
      ...conversation(), id, draft_text: request.user_message,
      draft_version: request.expected_draft_version + 1,
    },
  }));
});

function conversation(scopeKind: "home" | "task" = "home", scopeId = scopeKind === "home" ? "home" : "task-one"): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: scopeKind, scope_id: scopeId,
    title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z",
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
  it("returns to the latest reply explicitly without enabling follow while reading earlier messages", () => {
    const saved = conversation();
    const positions = { current: new Map<string, number>() }, following = { current: new Map<string, boolean>() };
    render(<AssistantConversationPanel state={state({ conversation: saved, historyScrollPositions: positions, historyFollowBottom: following })}
      input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="当前任务"/>);
    const viewport = screen.getByRole("region", { name: "持久对话记录" });
    Object.defineProperties(viewport, { scrollHeight: { configurable: true, value: 1400 }, clientHeight: { configurable: true, value: 400 } });
    viewport.scrollTop = 200;
    fireEvent.scroll(viewport);
    expect(following.current.get(saved.id)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "回到最新回复" }));
    expect(viewport.scrollTop).toBe(1000);
    expect(positions.current.get(saved.id)).toBe(1000);
    expect(following.current.get(saved.id)).toBe(true);
    expect(screen.queryByRole("button", { name: "回到最新回复" })).not.toBeInTheDocument();
  });
  it.each(["completed", "stopped", "failed"] as const)("withholds this turn's reply actions while streaming and exposes only truthful %s actions at its terminal state", terminal => {
    const saved = conversation();
    const partial = { ...saved.turns[0], id: "streamed-turn", sequence: 2, user_message: "当前要求",
      assistant_text: "**已经生成的部分正文**", status: "running" as const, events: [], result: null };
    saved.turns = [saved.turns[0], partial];
    const handoff = vi.fn();
    const panel = () => <AssistantConversationPanel state={state({ conversation: saved })} input="下一条草稿"
      onInput={vi.fn()} onSend={vi.fn()} onCreatePlanFromReply={handoff} busy={false} configured scopeLabel="当前任务"/>;
    const view = render(panel());
    const article = () => view.container.querySelectorAll(".assistant-persisted-turn")[1] as HTMLElement;
    expect(within(article()).getByText("已经生成的部分正文")).toBeVisible();
    expect(within(article()).queryByRole("button", { name: "复制回复" })).not.toBeInTheDocument();
    expect(within(article()).queryByRole("button", { name: "回复操作" })).not.toBeInTheDocument();
    expect(within(view.container.querySelectorAll(".assistant-persisted-turn")[0] as HTMLElement).getByRole("button", { name: "复制回复" })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
    expect(screen.getByText("正在回复…")).toBeInTheDocument();
    saved.turns = [saved.turns[0], { ...partial, status: terminal,
      assistant_text: `${partial.assistant_text}\n\n收到的后续正文。` }];
    view.rerender(panel());
    expect(within(article()).getByRole("button", { name: "复制回复" })).toBeInTheDocument();
    expect(within(article()).getByText("收到的后续正文。")).toBeVisible();
    expect(screen.queryByText("正在回复…")).not.toBeInTheDocument();
    if (terminal === "completed") {
      fireEvent.click(within(article()).getByRole("button", { name: "回复操作" }));
      expect(screen.getByRole("menuitem", { name: "带入新任务文案" })).toBeInTheDocument();
    } else {
      expect(within(article()).queryByRole("button", { name: "回复操作" })).not.toBeInTheDocument();
      expect(within(article()).getByRole("button", { name: "继续编辑本条要求" })).toBeDisabled();
    }
    expect(screen.getByRole("textbox")).toHaveValue("下一条草稿");
    expect(handoff).not.toHaveBeenCalled();
  });

  it("keeps a greeting to readable text and small actions, with keyboard access to reply use", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], user_message: "你好", assistant_text: "你好，有什么想做的？", events: [], result: { kind: "conversation" } }];
    const handoff = vi.fn();
    render(<AssistantConversationPanel state={state({ conversation: saved })} input="" onInput={vi.fn()} onSend={vi.fn()}
      onCreatePlanFromReply={handoff} busy={false} configured scopeLabel="首页创作"/>);
    expect(screen.getByText("你好，有什么想做的？")).toBeVisible();
    expect(screen.queryByText("本轮结果")).not.toBeInTheDocument();
    expect(screen.queryByText(/未记录领域写入/)).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制回复" }).textContent).toBe("");
    const options = screen.getByRole("button", { name: "回复操作" });
    options.focus();
    fireEvent.keyDown(options, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: "带入新任务文案" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(options).toHaveFocus();
    fireEvent.keyDown(options, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("menuitem", { name: "带入新任务文案" }));
    expect(handoff).toHaveBeenCalledWith(saved.turns[0]);
  });

  it("mounts a single header action set into its own shell slot and preserves actual change callbacks", async () => {
    const saved = conversation();
    const historic = { ...saved, id: "historic", title: "旧对话", draft_text: "旧草稿" };
    const createNew = vi.fn().mockResolvedValue({ ...saved, id: "new", draft_text: "" });
    const resume = vi.fn().mockResolvedValue(historic);
    const onInput = vi.fn(), changed = vi.fn(), send = vi.fn();
    const controller = state({ createNew, resume, loadList: vi.fn().mockResolvedValue([saved, historic]),
      conversations: [saved, historic], setError: vi.fn() });
    const view = render(<section aria-label="创作助手"><header><div data-assistant-header-actions/></header>
      <AssistantConversationPanel state={controller} input="当前草稿" onInput={onInput} onSend={send}
        onConversationChange={changed} busy={false} configured scopeLabel="首页" legacyHistory={<details><summary>旧版只读历史</summary><p>任务原始旧回复</p></details>}/></section>);
    const slot = view.container.querySelector<HTMLElement>("[data-assistant-header-actions]")!;
    expect(within(slot).getByRole("button", { name: "新对话" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "新对话" })).toHaveLength(1);
    expect(view.container.querySelector(".assistant-conversation-caption .assistant-header-actions")).toBeNull();
    expect(screen.queryByText("旧版只读历史")).not.toBeInTheDocument();
    fireEvent.click(within(slot).getByRole("button", { name: "新对话" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(onInput).toHaveBeenLastCalledWith("");
    fireEvent.click(within(slot).getByRole("button", { name: "历史对话" }));
    const dialog = await screen.findByRole("dialog", { name: "历史对话" });
    expect(within(dialog).getByText("旧版只读历史")).toBeInTheDocument();
    expect(within(dialog).getByText("任务原始旧回复")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /旧对话/ }));
    await waitFor(() => expect(resume).toHaveBeenCalledWith("historic"));
    expect(onInput).toHaveBeenLastCalledWith("旧草稿");
    expect(changed).toHaveBeenCalledTimes(2);
    expect(send).not.toHaveBeenCalled();
  });

  it("shows a readable failure first and keeps raw JSON and codes in collapsed diagnostics", () => {
    const raw = '{"detail":{"reason":"provider_output_invalid","request_id":"qa-id"}}';
    render(<AssistantConversationPanel state={state({ error: raw })} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页"/>);
    const alert = screen.getByRole("alert");
    expect(alert.firstElementChild).toHaveTextContent("这次操作没有完成，请打开诊断查看原因。");
    expect(alert.firstElementChild).not.toHaveTextContent("provider_output_invalid");
    const diagnostic = within(alert).getByText("诊断详情").closest("details")!;
    expect(diagnostic).not.toHaveAttribute("open");
    expect(diagnostic).toHaveTextContent(raw);
  });

  it("folds the actual Chinese invalid-JSON failure and single-underscore code without filtering successful JSON discussion", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], assistant_text: "成功回复可以讨论 JSON，也可以引用‘文字模型返回的内容不是有效 JSON’作为例子。", events: [], result: { kind: "conversation" } }];
    const panel = (error: string | null) => <AssistantConversationPanel state={state({ conversation: saved, error })}
      input="" onInput={vi.fn()} onSend={vi.fn()} busy={false} configured scopeLabel="首页"/>;
    const view = render(panel(null));
    for (const message of ["文字模型返回的内容不是有效 JSON", "provider_error"]) {
      view.rerender(panel(message));
      const alert = screen.getByRole("alert");
      expect(alert.firstElementChild).toHaveTextContent("这次操作没有完成，请打开诊断查看原因。");
      expect(alert.firstElementChild).not.toHaveTextContent(message);
      const diagnostic = within(alert).getByText("诊断详情").closest("details")!;
      expect(diagnostic).not.toHaveAttribute("open");
      expect(diagnostic.querySelector("pre")).toHaveTextContent(message);
      expect(screen.getByText(saved.turns[0].assistant_text)).toBeVisible();
    }
    view.rerender(panel(null));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("诊断详情")).not.toBeInTheDocument();
    expect(screen.getByText(saved.turns[0].assistant_text)).toBeVisible();
  });

  it("follows streamed text and tools near the bottom, preserves middle reading across navigation, and resumes only after scrolling down", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({ items: [conversation()] });
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.set(++frameId, callback); return frameId;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
    const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
    let controller!: ReturnType<typeof useAssistantConversation>;
    let height = 600;
    function Harness({ page }: { page: string }) {
      controller = useAssistantConversation({ scope_kind: "home", scope_id: "home" });
      return <AssistantConversationPanel key={page} state={controller} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel={page} />;
    }
    const measure = () => {
      const node = screen.getByLabelText("持久对话记录");
      Object.defineProperties(node, {
        clientHeight: { configurable: true, value: 200 },
        scrollHeight: { configurable: true, get: () => height },
      });
      return node;
    };
    try {
      const view = render(<Harness page="首页" />);
      await waitFor(() => expect(controller.loading).toBe(false));
      let history = measure();
      act(paint);
      history.scrollTop = 380; // 20 px from the bottom, inside the 48 px tolerance.
      fireEvent.scroll(history);
      let turn!: ReturnType<typeof controller.beginTurn>;
      await act(async () => { turn = controller.beginTurn("读取当前事实"); await turn.draftCheckpoint; });
      const stream = controller.bindTurnEvents(turn.conversationId, turn.localTurnId);
      act(() => {
        stream({ type: "turn_started", turn_id: "streamed-turn", turn_state: "running", draft_version: 2 });
        stream({ type: "text_delta", delta: "第一段真实流式回复。" });
      });
      height = 900;
      act(paint);
      expect(history.scrollTop).toBe(700);
      expect(controller.historyScrollPositions.current.get(turn.conversationId)).toBe(700);
      history.scrollTop = 120;
      fireEvent.scroll(history);
      act(() => stream({ type: "text_delta", delta: "后续回复继续增长。" }));
      height = 1400;
      act(paint);
      expect(history.scrollTop).toBe(120);
      view.rerender(<Harness page="素材库" />);
      history = measure();
      act(paint);
      expect(history.scrollTop).toBe(120);
      act(() => stream({ type: "text_delta", delta: "跨导航后仍在运行。" }));
      height = 1600;
      act(paint);
      expect(history.scrollTop).toBe(120);
      history.scrollTop = 1400;
      fireEvent.scroll(history);
      act(() => stream({ type: "tool_start", tool_id: 1, label: "读取当前任务" }));
      height = 1700;
      act(paint);
      expect(history.scrollTop).toBe(1500);
      act(() => stream({ type: "tool_end", tool_id: 1, label: "读取当前任务", ok: true, summary: "已读取实际资料。" }));
      height = 1800;
      // Scrolling upward before the scheduled frame must cancel its pending follow.
      history.scrollTop = 100;
      fireEvent.scroll(history);
      act(paint);
      expect(history.scrollTop).toBe(100);
      view.unmount();
    } finally { request.mockRestore(); cancel.mockRestore(); }
  });

  it("restores two failed-turn reading expansions before restoring scroll after navigation remount", async () => {
    const saved = conversation();
    saved.turns = [1, 2].map(sequence => ({
      ...saved.turns[0], id: `failed-${sequence}`, sequence, status: "failed" as const,
      assistant_text: "", result: { kind: "failure", message: `API unreachable: result ${sequence}` },
      events: [{ type: "tool_end" as const, tool_id: 1, label: "读取当前任务", ok: false,
                 summary: `API unreachable: tool ${sequence}` }],
    }));
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    const frames = new Map<number, FrameRequestCallback>();
    let frameId = 0;
    const request = vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
      frames.set(++frameId, callback); return frameId;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(id => { frames.delete(id); });
    const paint = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
    try {
      const hook = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      const panel = () => <AssistantConversationPanel state={hook.result.current} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel="当前对话" />;
      const first = render(panel());
      act(paint);
      first.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]").forEach(detail => {
        detail.open = true;
        fireEvent(detail, new Event("toggle"));
      });
      const history = screen.getByLabelText("持久对话记录");
      history.scrollTop = 420;
      fireEvent.scroll(history);
      first.unmount();
      // Navigation remount initially has no hydrated turns; readiness must trigger restoration.
      const next = render(<AssistantConversationPanel state={{ ...hook.result.current, loading: true,
        conversation: { ...saved, turns: [] } }} input="" onInput={vi.fn()} onSend={vi.fn()}
        busy={false} configured scopeLabel="素材库" />);
      act(paint);
      next.rerender(panel());
      const restoredDetails = next.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]");
      expect(restoredDetails).toHaveLength(8); // Both results, tool groups, and their diagnostic children.
      expect([...restoredDetails].every(detail => detail.open)).toBe(true);
      const restoredHistory = screen.getByLabelText("持久对话记录");
      let scroll = 0;
      Object.defineProperty(restoredHistory, "scrollTop", { configurable: true,
        get: () => scroll, set: value => { scroll = [...restoredDetails].every(detail => detail.open) ? Math.min(value, 500) : 0; },
      });
      expect(scroll).toBe(0);
      act(paint);
      expect(scroll).toBe(420);
      next.rerender(<AssistantConversationPanel state={{ ...hook.result.current,
        conversation: { ...saved, id: "another-conversation" } }} input="" onInput={vi.fn()}
        onSend={vi.fn()} busy={false} configured scopeLabel="另一段对话" />);
      expect([...next.container.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]")].every(detail => !detail.open)).toBe(true);
      act(paint);
      expect(scroll).toBe(0);
      next.unmount();
    } finally { request.mockRestore(); cancel.mockRestore(); }
  });

  it("offers an explicit reply transfer and a second confirmation before replacing a draft", () => {
    const onTransferReply = vi.fn();
    render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      onTransferReply={onTransferReply} transferDraftState="draft" busy={false} configured scopeLabel="当前任务" />);
    fireEvent.click(screen.getByRole("button", { name: "回复操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "替换当前草稿并带入回复" }));
    expect(onTransferReply).not.toHaveBeenCalled();
    expect(screen.getByText("这会覆盖当前待确认文案；已确认的文案不会被替换。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认替换并带入回复" }));
    expect(onTransferReply).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-one" }), true);
  });

  it("offers an explicit new-task handoff for a completed home reply", () => {
    const onCreatePlanFromReply = vi.fn();
    render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      onCreatePlanFromReply={onCreatePlanFromReply} busy={false} configured scopeLabel="首页创作" />);
    fireEvent.click(screen.getByRole("button", { name: "回复操作" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "带入新任务文案" }));
    expect(onCreatePlanFromReply).toHaveBeenCalledWith(expect.objectContaining({ id: "turn-one", status: "completed" }));
    expect(screen.queryByText("先确认制作方案和选题；这条回复只作为文案来源，不代替资料事实。")).not.toBeInTheDocument();
  });

  it("renders one final Markdown body and one collapsed, ordered tool summary", async () => {
    const { container } = render(<AssistantConversationPanel state={state()} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);

    expect(screen.getByText("真实方案").tagName).toBe("STRONG");
    expect(screen.getAllByRole("button", { name: "复制回复" })).toHaveLength(1);
    const tools = container.querySelector(".assistant-turn-tools summary")!;
    expect(tools.textContent).toContain("工具记录 · 2 项 · 含未完成操作");
    const assistantText = screen.getByText("真实方案");
    expect(tools.compareDocumentPosition(assistantText) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const toolDetails = tools.closest("details")!;
    expect(toolDetails).not.toHaveAttribute("open");
    expect(within(toolDetails).getAllByRole("listitem")).toHaveLength(2);
    expect(within(toolDetails).getByText("读取当前方案 · 已完成")).toBeInTheDocument();
    expect(within(toolDetails).getByText("检查新建方案 · 未完成")).toBeInTheDocument();
    fireEvent.click(tools);
    expect(within(toolDetails).getByText("已读取 1 项方案。")).toBeInTheDocument();
    expect(within(toolDetails).getByText("这一步没有完成。")).toBeInTheDocument();
    expect(screen.getByText("本轮结果").closest("details")).not.toHaveAttribute("open");

    await screen.findByRole("button", {name:"技术能力状态（诊断）"});
    expect(screen.queryByText("对话操作")).not.toBeInTheDocument();
    const diagnostics = screen.getByRole("button", { name: "技术能力状态（诊断）" });
    expect(diagnostics.closest(".assistant-conversation-head")).not.toBeInTheDocument();
    expect(diagnostics.closest(".assistant-header-actions")).toBeInTheDocument();
    expect(diagnostics.closest("form")).not.toBeInTheDocument();
    expect(diagnostics).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/本机 Pi 隔离版本检查/)).not.toBeInTheDocument();
    fireEvent.click(diagnostics);
    expect(diagnostics).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(diagnostics.getAttribute("aria-controls")!)).toBeInTheDocument();
    expect(screen.getByText(/本机版本未确认或与清单基线不同/)).toBeInTheDocument();
  });

  it("uses the complete persisted assistant text when it extends past streamed deltas", () => {
    const saved = conversation();
    saved.turns = [{ ...saved.turns[0], assistant_text: "完整的 Markdown 正文\n\n**终态尾段**。",
      events: [{ type: "text_delta", delta: "完整的 Markdown 正文" }] }];
    render(<AssistantConversationPanel state={state({ conversation: saved })} input="" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);
    expect(screen.getByText("终态尾段").tagName).toBe("STRONG");
    expect(screen.queryByText("完整的 Markdown 正文完整的 Markdown 正文")).not.toBeInTheDocument();
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

    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(onInput).toHaveBeenCalledWith(next.draft_text));
  });

  it.each(["/new", "/name 会话甲", "/resume conversation-two", "/help"].flatMap((command) =>
    (["button", "enter"] as const).map((activation) => [command, activation] as const))) (
    "keeps %s available without a configured model via %s", async (command, activation) => {
    const next = { ...conversation(), id: "conversation-next", draft_text: "保留的当前草稿" };
    const renamed = { ...conversation(), title: "会话甲", draft_text: "保留的当前草稿" };
    const createNew = vi.fn().mockResolvedValue(next);
    const rename = vi.fn().mockResolvedValue(renamed);
    const resume = vi.fn().mockResolvedValue(next);
    const current = state({
      createNew, rename, resume,
    });
    const onSend = vi.fn();
    const onInput = vi.fn();
    const onConversationChange = vi.fn();
    render(<AssistantConversationPanel state={current} input={command} onInput={onInput} onSend={onSend}
      onConversationChange={onConversationChange} busy={false} configured={false} scopeLabel="首页创作" />);
    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeEnabled();
    expect(send).toHaveAttribute("title", "Enter 执行本地会话操作；无效命令不会发送给模型");

    if (activation === "button") fireEvent.click(send);
    else fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });

    if (command === "/new") {
      await waitFor(() => expect(createNew).toHaveBeenCalledOnce());
      expect(onConversationChange).toHaveBeenCalledOnce();
      expect(onInput).toHaveBeenCalledWith(next.draft_text);
    } else if (command.startsWith("/name")) {
      await waitFor(() => expect(rename).toHaveBeenCalledWith("会话甲"));
      expect(onInput).toHaveBeenCalledWith(renamed.draft_text);
      expect(onConversationChange).not.toHaveBeenCalled();
    } else if (command.startsWith("/resume")) {
      await waitFor(() => expect(resume).toHaveBeenCalledWith("conversation-two"));
      expect(onConversationChange).toHaveBeenCalledOnce();
      expect(onInput).toHaveBeenCalledWith(next.draft_text);
    } else {
      await waitFor(() => expect(onInput).toHaveBeenCalledWith("/"));
      expect(createNew).not.toHaveBeenCalled();
      expect(rename).not.toHaveBeenCalled();
      expect(resume).not.toHaveBeenCalled();
    }
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each(["button", "enter"] as const)("keeps invalid commands local when no model is configured via %s", async (activation) => {
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={state()} input="/unknown value" onInput={vi.fn()} onSend={onSend}
      busy={false} configured={false} scopeLabel="首页创作" />);
    await screen.findByRole("button", { name: "技术能力状态（诊断）" });
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeEnabled();

    if (activation === "button") fireEvent.click(send);
    else fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });

    expect(await screen.findByRole("alert")).toHaveTextContent("此命令格式不正确或当前不可用，没有发送给模型");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("blocks ordinary text consistently when the model is unconfigured", async () => {
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={state()} input="继续创作" onInput={vi.fn()} onSend={onSend}
      busy={false} configured={false} scopeLabel="首页创作" />);
    const send = screen.getByRole("button", { name: "发送" });
    expect(send).toBeDisabled();
    expect(send).toHaveAttribute("title", "请先配置对话服务");
    fireEvent.click(send);
    fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("请先配置对话服务");
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each([
    ["busy", false, true, "本轮正在处理，请稍后再发送"],
    ["running", true, false, "此对话已有正在运行的轮次；可先停止本轮或切换到另一段对话"],
  ] as const)("does not repeat ordinary sends while %s", async (_stateName, running, busy, reason) => {
    const current = state({ conversation: running ? {
      ...conversation(), turns: [{ ...conversation().turns[0], status: "running" as const }],
    } : conversation() });
    const onSend = vi.fn();
    render(<AssistantConversationPanel state={current} input="再发一条" onInput={vi.fn()} onSend={onSend}
      busy={busy} configured scopeLabel="首页创作" />);
    if (running) {
      expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "停止本轮" })).toBeEnabled();
    } else {
      const send = screen.getByRole("button", { name: "正在处理" });
      expect(send).toBeDisabled();
      expect(send).toHaveAttribute("title", reason);
    }
    fireEvent.keyDown(screen.getByRole("textbox", { name: "对话要求" }), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent(reason);
    expect(onSend).not.toHaveBeenCalled();
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

  it("continues editing a historical failure explicitly and protects a nonempty current draft",()=>{
    const saved=conversation();saved.turns[0].status="failed";
    const onInput=vi.fn(),onSend=vi.fn();
    const view=render(<AssistantConversationPanel state={state({conversation:saved})} input="" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    fireEvent.click(screen.getByRole("button",{name:"继续编辑本条要求"}));
    expect(onInput).toHaveBeenCalledWith(saved.turns[0].user_message);
    expect(onSend).not.toHaveBeenCalled();
    view.rerender(<AssistantConversationPanel state={state({conversation:saved})} input="当前新草稿" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    const button=screen.getByRole("button",{name:"继续编辑本条要求"});
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title","当前已有草稿，请先处理当前草稿。");
  });

  it("keeps a restored running turn as the send gate and exposes its stop action", () => {
    const running = conversation();
    running.turns = [{ ...running.turns[0], id: "restored-running-turn", status: "running" }];
    const stop = vi.fn();
    render(<AssistantConversationPanel state={state({ conversation: running, stop })} input="继续写" onInput={vi.fn()} onSend={vi.fn()}
      busy={false} configured scopeLabel="首页创作" />);
    expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
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

  it("records turn_started before the active scope gate so a late failure cannot restore a submitted message", async () => {
    const first = { ...conversation(), turns: [], draft_text: "" };
    const second = { ...conversation("task", "task-one"), id: "task-conversation", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockImplementation(async (scope: { scope_kind: string; scope_id: string }) =>
      scope.scope_kind === "home" ? first : second);
    api.listAssistantConversations.mockImplementation(async (scope: { scope_kind: string; scope_id: string }) => ({
      items: scope.scope_kind === "home" ? [first] : [second],
    }));
    api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...(id === first.id ? first : second), draft_text: update.draft_text ?? "",
      draft_version: (update.expected_draft_version ?? 0) + 1,
    }));

    const { result, rerender } = renderHook(({ scope }) => useAssistantConversation(scope), {
      initialProps: { scope: { scope_kind: "home" as "home" | "task", scope_id: "home" } },
    });
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    let stream!: (event: AssistantStreamEvent) => void;
    await act(async () => {
      pending = result.current.beginTurn("上一范围已提交的请求");
      stream = result.current.bindTurnEvents(pending.conversationId, pending.localTurnId);
      await pending.draftCheckpoint;
    });

    rerender({ scope: { scope_kind: "task", scope_id: "task-one" } });
    await waitFor(() => expect(result.current.conversation?.id).toBe(second.id));
    act(() => stream({ type: "turn_started", turn_id: "persisted-old-turn", turn_state: "running", draft_version: 2 }));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "already_persisted",
      conversation: {
        ...first, draft_version: 2,
        turns: [{ ...conversation().turns[0], id: "persisted-old-turn", status: "running" }],
      },
    });
    const updateCount = api.updateAssistantConversation.mock.calls.length;

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "连接中断")).toBe(true);
    });

    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(updateCount);
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.draftText).toBe("");
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

  it("marks a conversation inactive when its hook unmounts during a pending draft checkpoint", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result, unmount } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => {
      turn = result.current.beginTurn("卸载前等待保存的请求");
      await turn.draftCheckpoint;
    });

    unmount();

    expect(result.current.isCurrentConversation(initial.id)).toBe(false);
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
      await turn.draftCheckpoint;
      await result.current.refresh();
      api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
        status: "restored", conversation: { ...saved, draft_text: "请再试一次读取当前方案", draft_version: 2 },
      });
      expect(await result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "网络请求未能建立轮次。")).toBe(true);
    });
    expect(result.current.conversation?.turns.some((turn) => turn.id === localTurnId)).toBe(false);
    expect(result.current.draftText).toBe("请再试一次读取当前方案");
    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(saved.id, {
      client_request_id: expect.any(String), user_message: "请再试一次读取当前方案", expected_draft_version: 1,
    });
  });

  it("restores the original request when the draft checkpoint failed before persisting", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    api.updateAssistantConversation.mockRejectedValueOnce(new Error("checkpoint unavailable"));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    act(() => { turn = result.current.beginTurn("检查点失败仍需保留的原句"); });
    await expect(turn.draftCheckpoint).rejects.toThrow("checkpoint unavailable");

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断")).toBe(true);
    });

    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(initial.id, {
      client_request_id: turn.clientRequestId, user_message: "检查点失败仍需保留的原句", expected_draft_version: 0,
    });
    expect(result.current.draftText).toBe("检查点失败仍需保留的原句");
    expect(result.current.conversation?.draft_version).toBe(1);
  });

  it("keeps a late unpersisted-turn failure in its original conversation without replacing a newer conversation draft", async () => {
    const first = { ...conversation(), turns: [], draft_text: "" };
    const second = { ...conversation(), id: "conversation-two", turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(first);
    api.listAssistantConversations.mockResolvedValue({ items: [first, second] });
    api.createAssistantConversation.mockResolvedValue(second);
    api.getAssistantConversation.mockResolvedValue(first);
    api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...(id === first.id ? first : second), draft_text: update.draft_text ?? "",
      draft_version: (update.expected_draft_version ?? 0) + 1,
      updated_at: "2026-10-09T00:01:00Z",
    }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(first.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { pending = result.current.beginTurn("旧会话原始要求"); await pending.draftCheckpoint; });
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "restored", conversation: { ...first, draft_text: "旧会话原始要求", draft_version: 2 },
    });
    await act(async () => { await result.current.createNew(); });
    act(() => result.current.setDraftText("新会话较新的草稿"));

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "旧请求连接失败说明")).toBe(true);
    });

    expect(api.updateAssistantConversation).not.toHaveBeenCalledWith(first.id, expect.objectContaining({ draft_text: "" }));
    expect(api.restoreUnpersistedAssistantTurnDraft).toHaveBeenCalledWith(first.id, {
      client_request_id: expect.any(String), user_message: "旧会话原始要求", expected_draft_version: 1,
    });
    expect(result.current.conversation?.id).toBe(second.id);
    expect(result.current.draftText).toBe("新会话较新的草稿");
  });

  it("does not restore a submitted turn over a newer draft in the same conversation", async () => {
    const initial = { ...conversation(), turns: [], draft_text: "" };
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({ items: [initial] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let pending!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { pending = result.current.beginTurn("已提交但失败的要求"); await pending.draftCheckpoint; });
    act(() => result.current.setDraftText("发送后新写的草稿"));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "restored", conversation: { ...initial, draft_text: "已提交但失败的要求", draft_version: 2 },
    });

    await act(async () => {
      expect(await result.current.failUnpersistedTurn(pending.conversationId, pending.localTurnId, "网络失败说明")).toBe(true);
    });

    expect(result.current.draftText).toBe("发送后新写的草稿");
    expect(result.current.conversation?.turns.some((turn) => turn.id === pending.localTurnId)).toBe(false);
  });

  it.each(["failed","stopped"] as const)("restores a persisted %s turn original text with draft CAS and keeps its durable record", async terminal => {
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("QA 本机文字连接错误验证，不执行任何写入。");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"real-failed-turn",turn_state:"running",draft_version:2}));
    act(()=>stream({type:"turn_status",turn_id:"real-failed-turn",turn_state:terminal}));
    expect(result.current.draftText).toBe("QA 本机文字连接错误验证，不执行任何写入。");
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenLastCalledWith(initial.id,{draft_text:result.current.draftText,expected_draft_version:2}));
    expect(result.current.conversation?.turns).toHaveLength(1);
    expect(result.current.conversation?.turns[0]).toMatchObject({id:"real-failed-turn",status:terminal});
    expect(api.restoreUnpersistedAssistantTurnDraft).not.toHaveBeenCalled();
  });

  it.each(["新写的草稿",""])("does not restore a terminal turn after the user edits the next draft to %j", async nextText=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("原始失败要求");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    act(()=>result.current.setDraftText(nextText));
    const count=api.updateAssistantConversation.mock.calls.length;
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    expect(result.current.draftText).toBe(nextText);
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(count);
  });

  it("does not restore an old stream over the same conversation after an explicit resume epoch",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("旧epoch原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.resumeAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"",draft_version:3});
    await act(async()=>{await result.current.resume(initial.id);});
    const count=api.updateAssistantConversation.mock.calls.length;
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    expect(result.current.draftText).toBe("");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(count);
  });

  it("restores the original request from a stopped server refresh when the stream has not returned its terminal event",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("停止后继续编辑的原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"",draft_version:2,turns:[{...result.current.conversation!.turns[0],status:"stopped"}]});
    await act(async()=>{await result.current.stop();});
    expect(api.stopAssistantConversationTurn).toHaveBeenCalledWith(initial.id,"persisted-turn");
    expect(result.current.draftText).toBe("停止后继续编辑的原文");
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenLastCalledWith(initial.id,{draft_text:"停止后继续编辑的原文",expected_draft_version:2}));
    expect(result.current.conversation?.turns[0].status).toBe("stopped");
  });

  it("prefers a newer server draft during terminal refresh instead of restoring the old request",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("较旧发送要求");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"服务器新版草稿",draft_version:3,turns:[{...result.current.conversation!.turns[0],status:"failed"}]});
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("服务器新版草稿");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(1);
  });

  it("preserves restored local text when refresh returns before its draft CAS has finished",async()=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("等待保存的恢复原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    let resolveSave!:(value:AssistantConversation)=>void;
    api.updateAssistantConversation.mockImplementationOnce(()=>new Promise(resolve=>{resolveSave=resolve;}));
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    await waitFor(()=>expect(api.updateAssistantConversation).toHaveBeenCalledTimes(2));
    const failed={...result.current.conversation!,draft_text:"",draft_version:2};
    api.getAssistantConversation.mockResolvedValue(failed);
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({status:"already_persisted",conversation:failed});
    await act(async()=>{await result.current.failUnpersistedTurn(initial.id,pending.localTurnId,"已持久失败");});
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    await act(async()=>{await result.current.refresh();});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    await act(async()=>{resolveSave({...failed,draft_text:"等待保存的恢复原文",draft_version:3});});
    expect(result.current.draftText).toBe("等待保存的恢复原文");
    expect(result.current.conversation?.turns[0].status).toBe("failed");
  });

  it.each([false,true])("preserves draft and diagnostics after restoration CAS conflict (server read fails: %s)",async readFails=>{
    const initial={...conversation(),draft_text:"",turns:[]};
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    api.listAssistantConversations.mockResolvedValue({items:[initial]});
    const {result}=renderHook(()=>useAssistantConversation({scope_kind:"home",scope_id:"home"}));
    await waitFor(()=>expect(result.current.loading).toBe(false));
    let pending!:ReturnType<typeof result.current.beginTurn>;
    await act(async()=>{pending=result.current.beginTurn("不能覆盖新版的原文");await pending.draftCheckpoint;});
    const stream=result.current.bindTurnEvents(initial.id,pending.localTurnId);
    act(()=>stream({type:"turn_started",turn_id:"persisted-turn",turn_state:"running",draft_version:2}));
    api.updateAssistantConversation.mockRejectedValueOnce(new Error("草稿CAS冲突409"));
    if(readFails) api.getAssistantConversation.mockRejectedValueOnce(new Error("服务器回读不可达"));
    else api.getAssistantConversation.mockResolvedValue({...result.current.conversation,draft_text:"其他窗口较新的草稿",draft_version:3});
    act(()=>stream({type:"turn_status",turn_id:"persisted-turn",turn_state:"failed"}));
    if(readFails) {
      await waitFor(()=>expect(result.current.error).toContain("服务器回读不可达"));
      expect(result.current.draftText).toBe("不能覆盖新版的原文");
    } else await waitFor(()=>expect(result.current.draftText).toBe("其他窗口较新的草稿"));
    expect(result.current.error).toContain("409");
    expect(api.updateAssistantConversation).toHaveBeenCalledTimes(2);
    expect(result.current.conversation?.turns[0]).toMatchObject({id:"persisted-turn",status:"failed"});
  });

  it("reconciles a recovered server turn instead of leaving an optimistic duplicate running", async () => {
    const saved = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(saved);
    api.listAssistantConversations.mockResolvedValue({ items: [saved] });
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(saved.id));
    let localTurnId = "";
    let stream!: (event: AssistantStreamEvent) => void;
    await act(async () => {
      const pending = result.current.beginTurn("检查一条正在处理的请求");
      localTurnId = pending.localTurnId;
      stream = result.current.bindTurnEvents(saved.id, localTurnId);
    });
    const persisted = { ...saved.turns[0], id: "persisted-running", sequence: 2,
      user_message: "检查一条正在处理的请求", status: "running" as const,
      assistant_text: "", events: [], result: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    api.getAssistantConversation.mockResolvedValue({ ...saved, turns: [...saved.turns, persisted] });
    act(() => stream({ type: "turn_started", turn_id: "persisted-running", turn_state: "running" }));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.conversation?.turns.at(-1)?.id).toBe("persisted-running");
    expect(result.current.conversation?.turns.filter((turn) => turn.id === localTurnId)).toHaveLength(0);
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
    api.updateAssistantConversation.mockImplementation(async (_id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
      ...initial, draft_text: update.draft_text ?? "",
      draft_version: initial.draft_version + (update.draft_text === undefined ? 0 : 1),
    }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));

    act(() => result.current.setDraftText("提取当前镜头原声"));
    await act(async () => { resolveCurrent(initial); });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.draftText).toBe("提取当前镜头原声");
    expect(api.updateAssistantConversation).toHaveBeenCalledWith(initial.id, {
      draft_text: "提取当前镜头原声", expected_draft_version: initial.draft_version,
    });
  });
});


describe("persistent workbench conversation and command completion", () => {
  it.each(["pending", "unavailable"] as const)("keeps four local commands available across button, Enter and menu with %s references", async referenceState => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    api.getAssistantConversationReference.mockImplementation(() => referenceState === "pending" ? new Promise(() => {}) : Promise.resolve({
      selected_reference:{kind:"knowledge",knowledge_item_id:"missing",version:"1",label:"旧资料"},
      reference_version:1,status:"unavailable",reason:"固定知识版本当前不可读取",
    }));
    const next={...conversation(),draft_text:"恢复的草稿"};
    const createNew=vi.fn().mockResolvedValue(next),rename=vi.fn().mockResolvedValue(next),resume=vi.fn().mockResolvedValue(next);
    const onInput=vi.fn(),onSend=vi.fn();
    const current=state({createNew,rename,resume,conversations:[{...conversation(),title:"可恢复记录"}]});
    const panel=(input:string,configured=false)=><AssistantConversationProvider><AssistantConversationPanel state={current} input={input} onInput={onInput} onSend={onSend} busy={false} configured={configured} scopeLabel="工作区"/></AssistantConversationProvider>;
    const view=render(panel("/new"));
    await screen.findByRole("button",{name:"技术能力状态（诊断）"});
    await waitFor(()=>expect(api.getAssistantConversationReference).toHaveBeenCalled());
    if(referenceState === "unavailable") await screen.findByText("引用核验未通过。固定版本保持原值，请重新查看或取消引用后发送。");
    for(const activation of ["button","enter"] as const) {
      for(const command of ["/new","/name 改名","/resume conversation-one","/help"]) {
        onInput.mockClear();createNew.mockClear();rename.mockClear();resume.mockClear();
        view.rerender(panel(command));
        const send=screen.getByRole("button",{name:"发送"});
        expect(send).toBeEnabled();
        expect(send).toHaveAttribute("title","Enter 执行本地会话操作；无效命令不会发送给模型");
        if(activation === "button") fireEvent.click(send);
        else fireEvent.keyDown(screen.getByRole("textbox"),{key:"Enter"});
        await waitFor(()=>expect(onInput).toHaveBeenCalledWith(command === "/help" ? "/" : next.draft_text));
        if(command === "/new") expect(createNew).toHaveBeenCalledOnce();
        if(command.startsWith("/name")) expect(rename).toHaveBeenCalledWith("改名");
        if(command.startsWith("/resume")) expect(resume).toHaveBeenCalledWith("conversation-one");
      }
    }
    for(const [name,query] of [["新对话","/ne"],["命名对话","/na"],["恢复对话","/re"],["查看命令","/he"]]) {
      onInput.mockClear();createNew.mockClear();resume.mockClear();
      view.rerender(panel(query));
      const option=screen.getByRole("option",{name:new RegExp(name)});
      expect(option).toBeEnabled();fireEvent.click(option);
      if(name === "新对话") await waitFor(()=>expect(onInput).toHaveBeenCalledWith(next.draft_text));
      if(name === "命名对话") expect(onInput).toHaveBeenCalledWith("/name ");
      if(name === "查看命令") expect(onInput).toHaveBeenCalledWith("/");
      if(name === "恢复对话") {
        fireEvent.click(await screen.findByRole("button",{name:/可恢复记录/}));
        await waitFor(()=>expect(resume).toHaveBeenCalledWith("conversation-one"));
      }
    }
    expect(onSend).not.toHaveBeenCalled();
    view.rerender(panel("/compact"));
    expect(screen.getByRole("button",{name:"发送"})).toBeEnabled();
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    expect(screen.getByRole("textbox")).toHaveValue("/compact");
    expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("没有发送给模型");
    view.rerender(panel("普通消息",true));
    expect(screen.getByRole("button",{name:"发送"})).toBeDisabled();
    expect(screen.getByRole("button",{name:"发送"})).toHaveAttribute("title",referenceState === "pending" ? "正在保存或核验引用" : "引用当前不可用，请重新查看或取消引用");
    fireEvent.keyDown(screen.getByRole("textbox"),{key:"Enter"});
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each(["loading","missing","busy","running","ime"] as const)("preserves a local command draft and blocks all execution paths while %s", async condition => {
    const currentConversation=conversation();
    if(condition === "running") currentConversation.turns=[{...currentConversation.turns[0],id:"pending-turn",status:"running"}];
    const createNew=vi.fn(),onInput=vi.fn(),onSend=vi.fn();
    render(<AssistantConversationPanel state={state({conversation:condition === "missing" ? null : currentConversation,loading:condition === "loading",createNew})} input="/new" onInput={onInput} onSend={onSend} busy={condition === "busy"} configured={false} scopeLabel="工作区"/>);
    const input=screen.getByRole("textbox");
    await screen.findByRole("option",{name:/新对话/});
    if(condition === "ime") fireEvent.compositionStart(input);
    const option=screen.getByRole("option",{name:/新对话/});
    expect(option).toBeDisabled();expect(option).toHaveAttribute("aria-disabled","true");
    expect(screen.getByRole("button",{name:"发送"})).toBeDisabled();
    fireEvent.click(option);fireEvent.click(screen.getByRole("button",{name:"发送"}));
    fireEvent.keyDown(input,{key:"Enter"});
    fireEvent.submit(input.closest("form")!);
    expect(createNew).not.toHaveBeenCalled();expect(onInput).not.toHaveBeenCalled();expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("/new");
    if(condition === "ime") {
      fireEvent.compositionEnd(input);
      expect(screen.getByRole("button",{name:"发送"})).toBeEnabled();
      expect(screen.getByRole("option",{name:/新对话/})).toBeEnabled();
    }
  });

  it("preserves a failed local command draft without forwarding it to the model",async()=>{
    const onInput=vi.fn(),onSend=vi.fn();
    render(<AssistantConversationPanel state={state({rename:vi.fn().mockRejectedValue(new Error("名称没有保存，请重试。"))})} input="/name 原草稿名称" onInput={onInput} onSend={onSend} busy={false} configured={false} scopeLabel="工作区"/>);
    await screen.findByRole("button",{name:"技术能力状态（诊断）"});
    fireEvent.click(screen.getByRole("button",{name:"发送"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("名称没有保存，请重试。");
    expect(screen.getByRole("textbox")).toHaveValue("/name 原草稿名称");
    expect(onInput).not.toHaveBeenCalled();expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps one active conversation and next draft when page consumers unmount", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({ items: [conversation()] });
    function Consumer({ kind }: { kind: "home" | "task" }) {
      const value = useAssistantConversation({ scope_kind: kind, scope_id: kind === "home" ? "home" : "different-task" });
      return <><span>{value.conversation?.id}</span><input aria-label="next draft" value={value.draftText} onChange={event => value.setDraftText(event.target.value)}/></>;
    }
    const view = render(<AssistantConversationProvider><Consumer key="home" kind="home"/></AssistantConversationProvider>);
    await screen.findByText("conversation-one");
    fireEvent.change(screen.getByLabelText("next draft"), { target: { value: "跨页保留" } });
    view.rerender(<AssistantConversationProvider><Consumer key="task" kind="task"/></AssistantConversationProvider>);
    expect(screen.getByLabelText("next draft")).toHaveValue("跨页保留");
    expect(screen.getByText("conversation-one")).toBeInTheDocument();
    expect(api.getCurrentAssistantConversation).toHaveBeenCalledTimes(1);
    expect(api.getCurrentAssistantConversation).toHaveBeenCalledWith({scope_kind:"home",scope_id:"home"});
  });

  it("keeps a fixed knowledge reference across consumers and clears it explicitly", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    function Consumer() {
      const value = useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    const view = render(<AssistantConversationProvider><Consumer key="knowledge"/></AssistantConversationProvider>);
    await screen.findByRole("textbox");
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",locator:"第1段",label:"第一版资料"}));
    expect(screen.getByText(/第一版资料 · 版本 1 · 第1段/)).toBeVisible();
    view.rerender(<AssistantConversationProvider><Consumer key="profiles"/></AssistantConversationProvider>);
    expect(screen.getByText(/第一版资料 · 版本 1 · 第1段/)).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"取消知识引用"}));
    expect(screen.queryByText(/第一版资料 · 版本 1/)).not.toBeInTheDocument();
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"第一版资料"}));
    act(() => referenceAssistantMaterial({selection:{scope:"asset",asset_id:"asset-real",expected_updated_at:"2026-10-10T00:00:00Z"},label:"明确素材"}));
    expect(screen.queryByText(/第一版资料 · 版本 1/)).not.toBeInTheDocument();
    expect(screen.getByText(/明确素材/)).toBeVisible();
  });

  it("restores saved reference after full Provider remount and persists explicit cancel", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    function Consumer() {
      const value = useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    const first=render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"固定旧版"}));
    await waitFor(() => expect(api.referenceSaved.get("conversation-one")?.selected_reference).toEqual({kind:"knowledge",knowledge_item_id:"knowledge-real",version:"1",label:"固定旧版"}));
    first.unmount();
    const second=render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    expect(await screen.findByText(/固定旧版 · 版本 1/)).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"取消知识引用"}));
    await waitFor(() => expect(api.referenceSaved.get("conversation-one")?.selected_reference).toBeNull());
    second.unmount();
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalledTimes(3));
    expect(screen.queryByText(/固定旧版 · 版本 1/)).not.toBeInTheDocument();
  });

  it("restores each conversation's own fixed reference when resuming after an explicit selection", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    api.resumeAssistantConversation.mockImplementation(async (id:string) => ({...conversation(),id}));
    api.referenceSaved.set("conversation-two",{selected_reference:null,reference_version:0,status:"ready",reason:null});
    function Consumer() {
      const value=useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <><button onClick={()=>void value.resume("conversation-two")}>第二段</button><button onClick={()=>void value.resume("conversation-one")}>第一段</button>
        <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/></>;
    }
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    act(() => referenceAssistantKnowledge({knowledge_item_id:"knowledge-real",version:"1",label:"第一段固定资料"}));
    await waitFor(() => expect(api.referenceSaved.get("conversation-one")?.selected_reference?.label).toBe("第一段固定资料"));
    fireEvent.click(screen.getByRole("button",{name:"第二段"}));
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalledWith("conversation-two"));
    await waitFor(() => expect(screen.queryByText(/第一段固定资料 ·/)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button",{name:"第一段"}));
    expect(await screen.findByText(/第一段固定资料 · 版本 1/)).toBeVisible();
  });

  it("does not revive an older selected reference when its save returns after cancel", async () => {
    api.getCurrentAssistantConversation.mockResolvedValue(conversation());
    api.listAssistantConversations.mockResolvedValue({items:[conversation()]});
    let resolveSave!:(value:import("@/lib/types").AssistantConversationReferenceState)=>void;
    api.saveAssistantConversationReference.mockImplementationOnce(() => new Promise(resolve => {resolveSave=resolve;}));
    function Consumer() {
      const value=useAssistantConversation({scope_kind:"home",scope_id:"home"});
      return <AssistantConversationPanel state={value} input={value.draftText} onInput={value.setDraftText} onSend={vi.fn()} busy={false} configured scopeLabel="工作区"/>;
    }
    render(<AssistantConversationProvider><Consumer/></AssistantConversationProvider>);
    await waitFor(() => expect(api.getAssistantConversationReference).toHaveBeenCalled());
    const selected={kind:"knowledge" as const,knowledge_item_id:"knowledge-real",version:"1",label:"不能复活"};
    act(() => referenceAssistantKnowledge(selected));
    await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button",{name:"取消知识引用"}));
    expect(screen.queryByText(/不能复活/)).not.toBeInTheDocument();
    await act(async () => resolveSave({selected_reference:selected,reference_version:1,status:"ready",reason:null}));
    await waitFor(() => expect(api.saveAssistantConversationReference).toHaveBeenLastCalledWith("conversation-one",null,1));
    expect(screen.queryByText(/不能复活/)).not.toBeInTheDocument();
  });

  it("completes name parameters with Tab and protects IME, no results and Esc", async () => {
    const onInput = vi.fn(); const onSend = vi.fn();
    const view = render(<AssistantConversationPanel state={state()} input="/na" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    await screen.findByRole("option", { name: /命名对话/ });
    const input = screen.getByRole("textbox"); input.focus();
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {key:"Enter", keyCode:229});
    expect(onInput).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input); fireEvent.keyDown(input, {key:"Tab"});
    expect(onInput).toHaveBeenCalledWith("/name "); expect(document.activeElement).toBe(input);
    view.rerender(<AssistantConversationPanel state={state()} input="/not-supported" onInput={onInput} onSend={onSend} busy={false} configured scopeLabel="工作区"/>);
    expect(screen.getByText("没有匹配的已接通命令")).toBeInTheDocument();
    fireEvent.keyDown(input, {key:"Enter"}); expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("没有发送给模型");
    fireEvent.keyDown(input, {key:"Escape"}); expect(screen.queryByRole("listbox")).not.toBeInTheDocument(); expect(input).toHaveValue("/not-supported");
  });

  it("allows filtering while running but cannot execute a command", async () => {
    const createNew = vi.fn(); const running = conversation(); running.turns[0].status="running";
    render(<AssistantConversationPanel state={state({conversation:running,createNew})} input="/new" onInput={vi.fn()} onSend={vi.fn()} busy configured scopeLabel="工作区"/>);
    await screen.findByRole("option", {name:/新对话/}); fireEvent.keyDown(screen.getByRole("textbox"), {key:"Enter"});
    expect(createNew).not.toHaveBeenCalled(); expect(screen.getByRole("option", {name:/新对话/})).toHaveAttribute("aria-disabled","true");
  });
});
