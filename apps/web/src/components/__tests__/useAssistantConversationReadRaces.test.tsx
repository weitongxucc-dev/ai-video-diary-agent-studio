import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantConversation } from "../useAssistantConversation";
import type { AssistantConversation } from "@/lib/types";

const api = vi.hoisted(() => ({
  getCurrentAssistantConversation: vi.fn(),
  listAssistantConversations: vi.fn(),
  getAssistantConversation: vi.fn(),
  updateAssistantConversation: vi.fn(),
  restoreUnpersistedAssistantTurnDraft: vi.fn(),
  createAssistantConversation: vi.fn(),
  resumeAssistantConversation: vi.fn(),
  stopAssistantConversationTurn: vi.fn(),
}));

vi.mock("@/lib/api", () => api);

function conversation(overrides: Partial<AssistantConversation> = {}): AssistantConversation {
  return {
    id: "conversation-one", workspace_id: "workspace-one", scope_kind: "home", scope_id: "home",
    title: "对话", draft_text: "", draft_version: 1, created_at: "2026-10-09T00:00:00Z",
    updated_at: "2026-10-09T00:00:00Z", turns: [], ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  const initial = conversation();
  api.getCurrentAssistantConversation.mockResolvedValue(initial);
  api.listAssistantConversations.mockResolvedValue({ items: [initial] });
  api.getAssistantConversation.mockResolvedValue(initial);
  api.updateAssistantConversation.mockImplementation(async (id: string, update: { draft_text?: string; expected_draft_version?: number }) => ({
    ...initial, id, draft_text: update.draft_text ?? initial.draft_text,
    draft_version: (update.expected_draft_version ?? initial.draft_version) + (update.draft_text === undefined ? 0 : 1),
  }));
  api.restoreUnpersistedAssistantTurnDraft.mockImplementation(async (id: string, input: { user_message: string; expected_draft_version: number }) => ({
    status: "restored", conversation: conversation({ id, draft_text: input.user_message, draft_version: input.expected_draft_version + 1 }),
  }));
});

afterEach(() => { vi.useRealTimers(); });

describe("useAssistantConversation read races", () => {
  it("does not let a refresh GET replace text typed after that read started", async () => {
    const initial = conversation({ draft_text: "旧服务端草稿" });
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    let resolveRead!: (value: AssistantConversation) => void;
    api.getAssistantConversation.mockReturnValue(new Promise((resolve) => { resolveRead = resolve; }));
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));

    let refresh!: Promise<AssistantConversation | null>;
    act(() => { refresh = result.current.refresh(); });
    act(() => result.current.setDraftText("刷新期间新写的草稿"));
    await act(async () => {
      resolveRead(conversation({ draft_text: "旧服务端草稿", draft_version: 2 }));
      await refresh;
    });

    expect(result.current.draftText).toBe("刷新期间新写的草稿");
    // conversation keeps the persisted snapshot; draftText owns newer local edits.
    expect(result.current.conversation?.draft_text).toBe("旧服务端草稿");
  });

  it("preserves an unsaved local draft even when refresh starts after the edit", async () => {
    const initial = conversation({ draft_text: "旧服务端草稿" });
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    act(() => result.current.setDraftText("尚未到自动保存时间的新草稿"));
    api.getAssistantConversation.mockResolvedValue(conversation({ draft_text: "旧服务端草稿", draft_version: 2 }));

    await act(async () => { await result.current.refresh(); });

    expect(result.current.draftText).toBe("尚未到自动保存时间的新草稿");
    expect(result.current.conversation?.draft_text).toBe("旧服务端草稿");
    expect(result.current.conversation?.draft_version).toBe(2);
  });

  it("does not let a failed restore read started after the failure wipe text typed while restore was pending", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const restore = deferred<Awaited<ReturnType<typeof api.restoreUnpersistedAssistantTurnDraft>>>();
    api.restoreUnpersistedAssistantTurnDraft.mockReturnValue(restore.promise);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { turn = result.current.beginTurn("提交中的原句"); await turn.draftCheckpoint; });

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "提交失败"); });
    act(() => result.current.setDraftText("恢复请求期间新写的草稿"));
    const staleRead = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValue(staleRead.promise);
    await act(async () => { restore.reject(new Error("恢复接口不可用")); await Promise.resolve(); });
    await waitFor(() => expect(api.getAssistantConversation).toHaveBeenCalledWith(initial.id));
    await act(async () => {
      staleRead.resolve(conversation({ draft_text: "提交中的原句", draft_version: 2 }));
      await fail;
    });

    expect(result.current.draftText).toBe("恢复请求期间新写的草稿");
    expect(result.current.error).toContain("恢复接口不可用");
  });

  it("drops only the failed optimistic turn when a successful restore races with a newer draft", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const restore = deferred<{ status: "restored"; conversation: AssistantConversation }>();
    api.restoreUnpersistedAssistantTurnDraft.mockReturnValue(restore.promise);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    await act(async () => { turn = result.current.beginTurn("提交失败后可恢复的原句"); await turn.draftCheckpoint; });

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断"); });
    act(() => result.current.setDraftText("恢复完成前写下的新内容"));
    await act(async () => {
      restore.resolve({ status: "restored", conversation: conversation({ draft_text: "提交失败后可恢复的原句", draft_version: 3 }) });
      await fail;
    });

    expect(result.current.draftText).toBe("恢复完成前写下的新内容");
    expect(result.current.conversation?.draft_text).toBe("提交失败后可恢复的原句");
    expect(result.current.conversation?.draft_version).toBe(3);
    expect(result.current.conversation?.turns).toHaveLength(0);
  });

  it("rechecks the draft epoch after an already-persisted receipt's follow-up GET", async () => {
    const initial = conversation();
    api.getCurrentAssistantConversation.mockResolvedValue(initial);
    const { result } = renderHook(() => useAssistantConversation({ scope_kind: "home", scope_id: "home" }));
    await waitFor(() => expect(result.current.conversation?.id).toBe(initial.id));
    let turn!: ReturnType<typeof result.current.beginTurn>;
    let stream!: ReturnType<typeof result.current.bindTurnEvents>;
    await act(async () => {
      turn = result.current.beginTurn("已持久化的原句");
      stream = result.current.bindTurnEvents(initial.id, turn.localTurnId);
      await turn.draftCheckpoint;
    });
    act(() => stream({ type: "turn_started", turn_id: "persisted-turn", turn_state: "running", draft_version: 2 }));
    api.restoreUnpersistedAssistantTurnDraft.mockResolvedValue({
      status: "already_persisted", conversation: conversation({ draft_version: 3,
        turns: [{ id: "persisted-turn", sequence: 1, user_message: "已持久化的原句", assistant_text: "", status: "running", events: [], result: null, created_at: initial.created_at, updated_at: initial.updated_at }] }),
    });
    const latest = deferred<AssistantConversation>();
    api.getAssistantConversation.mockReturnValue(latest.promise);

    let fail!: Promise<boolean>;
    act(() => { fail = result.current.failUnpersistedTurn(turn.conversationId, turn.localTurnId, "连接中断"); });
    await waitFor(() => expect(api.getAssistantConversation).toHaveBeenCalledWith(initial.id));
    act(() => result.current.setDraftText("二次读取期间新写的草稿"));
    act(() => stream({ type: "text_delta", delta: "保留的实时增量" }));
    await act(async () => {
      latest.resolve(conversation({ draft_text: "已持久化的原句", draft_version: 2,
        turns: [{ id: "persisted-turn", sequence: 1, user_message: "已持久化的原句", assistant_text: "", status: "running", events: [], result: null, created_at: initial.created_at, updated_at: initial.updated_at }] }));
      await fail;
    });

    expect(result.current.draftText).toBe("二次读取期间新写的草稿");
    expect(result.current.conversation?.draft_text).toBe("已持久化的原句");
    expect(result.current.conversation?.draft_version).toBe(3);
    expect(result.current.conversation?.turns.at(-1)?.assistant_text).toBe("保留的实时增量");
  });
});
