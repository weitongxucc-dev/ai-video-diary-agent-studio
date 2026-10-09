"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createAssistantConversation,
  getAssistantConversation,
  getCurrentAssistantConversation,
  listAssistantConversations,
  resumeAssistantConversation,
  stopAssistantConversationTurn,
  updateAssistantConversation,
} from "@/lib/api";
import type {
  AssistantConversation,
  AssistantConversationScope,
  AssistantConversationTurn,
  AssistantStreamEvent,
} from "@/lib/types";

function localTurn(message: string): AssistantConversationTurn {
  const now = new Date().toISOString();
  return {
    id: `pending-${crypto.randomUUID()}`, sequence: 0, user_message: message,
    assistant_text: "", status: "running", events: [], result: null, created_at: now, updated_at: now,
  };
}

function draftRevisionFor(revisions: Map<string, number>, conversationId: string): number {
  return revisions.get(conversationId) ?? 0;
}

function advanceDraftRevision(revisions: Map<string, number>, conversationId: string): number {
  const next = draftRevisionFor(revisions, conversationId) + 1;
  revisions.set(conversationId, next);
  return next;
}

export function useAssistantConversation(scope: AssistantConversationScope) {
  const { scope_kind: scopeKind, scope_id: scopeId } = scope;
  const scopeKey = `${scopeKind}:${scopeId}`;
  const activeScopeKey = useRef(scopeKey);
  activeScopeKey.current = scopeKey;
  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  const conversationRef = useRef<AssistantConversation | null>(null);
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [draftText, setDraftTextState] = useState("");
  const draftValue = useRef("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftQueue = useRef<Promise<unknown>>(Promise.resolve());
  const revision = useRef(0);
  const loadedScope = useRef<string | null>(null);
  const streamRevision = useRef(0);
  const navigationQueue = useRef<Promise<unknown>>(Promise.resolve());
  const navigationRevision = useRef(0);
  const renameRevision = useRef(0);
  const draftRevisionByConversation = useRef(new Map<string, number>());
  const localTurnRecovery = useRef(new Map<string, {
    conversationId: string;
    userMessage: string;
    draftRevision: number;
    persistedTurnId: string | null;
  }>());

  const replace = useCallback((next: AssistantConversation | null) => {
    conversationRef.current = next;
    setConversation(next);
  }, []);

  const saveDraftValue = useCallback(async (conversationId: string, text: string) => {
    const save = draftQueue.current.catch(() => undefined)
      .then(() => updateAssistantConversation(conversationId, { draft_text: text }));
    draftQueue.current = save;
    try {
      const updated = await save;
      if (conversationRef.current?.id === conversationId) {
        const current = conversationRef.current;
        if (current) replace({ ...current, draft_text: updated.draft_text, updated_at: updated.updated_at });
      }
      return updated;
    } catch (cause) {
      const current = conversationRef.current;
      if (current?.id === conversationId
        && activeScopeKey.current === `${current.scope_kind}:${current.scope_id}`) {
        setError(cause instanceof Error ? cause.message : "未发送草稿没有保存。");
      }
      throw cause;
    }
  }, [replace]);

  const flushDraft = useCallback(async () => {
    if (draftTimer.current) {
      clearTimeout(draftTimer.current);
      draftTimer.current = null;
    }
    revision.current += 1;
    const current = conversationRef.current;
    if (current) await saveDraftValue(current.id, draftValue.current);
  }, [saveDraftValue]);

  const loadList = useCallback(async (scopeArg?: AssistantConversationScope) => {
    const requestedScope = scopeArg ?? { scope_kind: scopeKind, scope_id: scopeId };
    const requestedKey = `${requestedScope.scope_kind}:${requestedScope.scope_id}`;
    const result = await listAssistantConversations(requestedScope);
    if (activeScopeKey.current === requestedKey) setConversations(result.items);
    return result.items;
  }, [scopeKind, scopeId]);

  const refresh = useCallback(async () => {
    const current = conversationRef.current;
    if (!current) return null;
    const observedStreamRevision = streamRevision.current;
    const fresh = await getAssistantConversation(current.id);
    const freshScopeKey = `${fresh.scope_kind}:${fresh.scope_id}`;
    if (conversationRef.current?.id === fresh.id && activeScopeKey.current === freshScopeKey
      && streamRevision.current === observedStreamRevision) {
      const latestLocal = conversationRef.current;
      const freshTurnIds = new Set(fresh.turns.map((turn) => turn.id));
      const localPendingTurns = latestLocal?.turns.filter((turn) => {
        return turn.id.startsWith("pending-") && !freshTurnIds.has(turn.id);
      }) ?? [];
      const currentConversation = localPendingTurns.length
        ? { ...fresh, turns: [...fresh.turns, ...localPendingTurns] }
        : fresh;
      replace(currentConversation);
      setConversations((items) => [currentConversation, ...items.filter((item) => item.id !== currentConversation.id)]
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at)));
    }
    return fresh;
  }, [replace]);

  useEffect(() => {
    let active = true;
    if (loadedScope.current !== scopeKey) {
      loadedScope.current = scopeKey;
      revision.current += 1;
      draftValue.current = "";
      setDraftTextState("");
    }
    const loadRevision = revision.current;
    setLoading(true);
    setError(null);
    replace(null);
    setDraftTextState("");
    void getCurrentAssistantConversation({ scope_kind: scopeKind, scope_id: scopeId })
      .then(async (current) => {
        if (!active) return;
        // Re-entering a conversation is a new editing epoch. A request left
        // behind by its previous visit must not replace this visit's draft.
        advanceDraftRevision(draftRevisionByConversation.current, current.id);
        replace(current);
        if (revision.current === loadRevision) {
          draftValue.current = current.draft_text;
          setDraftTextState(current.draft_text);
        } else {
          const localDraft = draftValue.current;
          setDraftTextState(localDraft);
          if (localDraft !== current.draft_text) {
            void saveDraftValue(current.id, localDraft).catch(() => undefined);
          }
        }
        const list = await listAssistantConversations({ scope_kind: scopeKind, scope_id: scopeId });
        if (active) setConversations(list.items);
      })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "无法恢复此范围的对话。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => {
      active = false;
      if (draftTimer.current) clearTimeout(draftTimer.current);
      const previous = conversationRef.current;
      if (previous && previous.draft_text !== draftValue.current) {
        void saveDraftValue(previous.id, draftValue.current).catch(() => undefined);
      }
    };
  }, [scopeKind, scopeId, replace, saveDraftValue]);

  const setDraftText = useCallback((text: string) => {
    draftValue.current = text;
    setDraftTextState(text);
    const currentRevision = ++revision.current;
    const current = conversationRef.current;
    const id = current?.scope_kind === scopeKind && current.scope_id === scopeId ? current.id : undefined;
    if (!id) return;
    advanceDraftRevision(draftRevisionByConversation.current, id);
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      if (currentRevision !== revision.current) return;
      void saveDraftValue(id, text).catch(() => undefined);
    }, 450);
  }, [scopeKind, scopeId, saveDraftValue]);

  const beginTurn = useCallback((message: string) => {
    const current = conversationRef.current;
    if (!current) throw new Error("对话尚未恢复，请稍后重试。");
    if (current.scope_kind !== scopeKind || current.scope_id !== scopeId) {
      throw new Error("对话范围正在切换，请等当前范围恢复后再发送。");
    }
    if (current.turns.at(-1)?.status === "running") {
      throw new Error("此对话已有正在运行的轮次；可停止它，或切换到另一段对话。草稿仍保留。");
    }
    if (draftTimer.current) {
      clearTimeout(draftTimer.current);
      draftTimer.current = null;
    }
    revision.current += 1;
    const turn = localTurn(message);
    const draftRevision = advanceDraftRevision(draftRevisionByConversation.current, current.id);
    localTurnRecovery.current.set(turn.id, {
      conversationId: current.id,
      userMessage: message,
      draftRevision,
      persistedTurnId: null,
    });
    replace({ ...current, draft_text: "", turns: [...current.turns, turn] });
    draftValue.current = "";
    setDraftTextState("");
    return {
      conversationId: current.id,
      localTurnId: turn.id,
      draftCheckpoint: draftQueue.current.catch(() => undefined),
    };
  }, [replace, scopeKind, scopeId]);

  const isCurrentConversation = useCallback((conversationId: string) =>
    conversationRef.current?.id === conversationId
      && conversationRef.current.scope_kind === scopeKind
      && conversationRef.current.scope_id === scopeId, [scopeKind, scopeId]);

  const failUnpersistedTurn = useCallback(async (conversationId: string, localTurnId: string, message: string) => {
    const recovery = localTurnRecovery.current.get(localTurnId);
    if (recovery?.persistedTurnId) return true;
    let current = conversationRef.current;
    let index = current?.turns.findIndex((turn) => turn.id === localTurnId) ?? -1;
    const userMessage = recovery?.userMessage
      ?? (current?.id === conversationId && index >= 0 ? current.turns[index].user_message : null);
    if (recovery?.conversationId !== conversationId || !userMessage) return false;

    const currentIsSameScope = current?.id === conversationId
      && activeScopeKey.current === `${current.scope_kind}:${current.scope_id}`;
    if (currentIsSameScope && index >= 0) {
      const turns = [...current.turns];
      const turn = turns[index];
      turns[index] = {
        ...turn, status: "failed", result: { kind: "failure", message }, updated_at: new Date().toISOString(),
      };
      replace({ ...current, turns });
      if (recovery && draftRevisionFor(draftRevisionByConversation.current, conversationId) === recovery.draftRevision) {
        setDraftText(userMessage);
      }
      return true;
    }

    // A turn_started event is the server's durable-turn receipt. If we never
    // received it, retain the actual submitted sentence as a retryable draft,
    // never the transport/error explanation. Per-conversation revision fences
    // stale failures from a later draft or a later visit to this conversation.
    if (recovery && draftRevisionFor(draftRevisionByConversation.current, conversationId) === recovery.draftRevision) {
      try {
        const save = draftQueue.current.catch(() => undefined).then(() => {
          if (draftRevisionFor(draftRevisionByConversation.current, conversationId) !== recovery.draftRevision) return null;
          return updateAssistantConversation(conversationId, { draft_text: userMessage });
        });
        draftQueue.current = save;
        const updated = await save;
        if (updated && draftRevisionFor(draftRevisionByConversation.current, conversationId) === recovery.draftRevision
          && conversationRef.current?.id === conversationId) {
          const latest = conversationRef.current;
          replace({ ...latest, draft_text: updated.draft_text, updated_at: updated.updated_at });
        }
        return true;
      } catch (cause) {
        if (conversationRef.current?.id === conversationId) {
          setError(cause instanceof Error ? cause.message : "未发送草稿没有保存。");
        }
        return false;
      }
    }
    return true;
  }, [replace, setDraftText]);

  const bindTurnEvents = useCallback((conversationId: string, localTurnId: string) => {
    let persistedTurnId = localTurnId;
    return (event: AssistantStreamEvent) => {
      const current = conversationRef.current;
      if (!current || current.id !== conversationId) return;
      const turns = [...current.turns];
      const index = turns.findIndex((turn) =>
        turn.id === persistedTurnId || (event.type === "turn_started" && (turn.id === localTurnId || turn.id === event.turn_id)));
      if (index < 0) return;
      const turn = turns[index];
      if (event.type === "turn_started") {
        persistedTurnId = event.turn_id;
        const recovery = localTurnRecovery.current.get(localTurnId);
        if (recovery?.conversationId === conversationId) recovery.persistedTurnId = event.turn_id;
        turns[index] = { ...turn, id: event.turn_id, status: "running", updated_at: new Date().toISOString() };
      } else if (event.type === "turn_status") {
        turns[index] = { ...turn, status: event.turn_state, updated_at: new Date().toISOString() };
      } else {
        const events = [...turn.events, event];
        turns[index] = {
          ...turn,
          events,
          assistant_text: event.type === "text_delta" ? turn.assistant_text + event.delta : turn.assistant_text,
          updated_at: new Date().toISOString(),
        };
      }
      streamRevision.current += 1;
      replace({ ...current, turns });
    };
  }, [replace]);

  const createNew = useCallback(() => {
    const navigation = ++navigationRevision.current;
    const operation = navigationQueue.current.catch(() => undefined).then(async () => {
      await flushDraft();
      if (navigation !== navigationRevision.current || activeScopeKey.current !== scopeKey) {
        throw new Error("页面或会话已切换；没有在新范围创建会话。");
      }
      const created = await createAssistantConversation({ scope_kind: scopeKind, scope_id: scopeId });
      if (navigation !== navigationRevision.current || activeScopeKey.current !== scopeKey) {
        throw new Error("页面或会话已切换；新会话保留在原范围，没有切换当前页面。");
      }
      replace(created);
      draftValue.current = "";
      setDraftTextState("");
      await loadList({ scope_kind: scopeKind, scope_id: scopeId });
      return created;
    });
    navigationQueue.current = operation.then(() => undefined, () => undefined);
    return operation;
  }, [scopeKey, scopeKind, scopeId, replace, loadList, flushDraft]);

  const resume = useCallback((id: string) => {
    const navigation = ++navigationRevision.current;
    const operation = navigationQueue.current.catch(() => undefined).then(async () => {
      await flushDraft();
      if (navigation !== navigationRevision.current || activeScopeKey.current !== scopeKey) {
        throw new Error("页面或会话已切换；没有恢复其他范围的会话。");
      }
      const resumed = await resumeAssistantConversation(id);
      if (navigation !== navigationRevision.current || activeScopeKey.current !== scopeKey) {
        throw new Error("页面或会话已切换；恢复结果仍留在原范围。");
      }
      if (resumed.scope_kind !== scopeKind || resumed.scope_id !== scopeId) {
        throw new Error("这段对话属于其他页面或对象，请回到原范围恢复。");
      }
      advanceDraftRevision(draftRevisionByConversation.current, resumed.id);
      replace(resumed);
      draftValue.current = resumed.draft_text;
      setDraftTextState(resumed.draft_text);
      return resumed;
    });
    navigationQueue.current = operation.then(() => undefined, () => undefined);
    return operation;
  }, [scopeKey, scopeKind, scopeId, replace, flushDraft]);

  const rename = useCallback(async (title: string) => {
    const current = conversationRef.current;
    if (!current) throw new Error("当前对话尚未恢复。");
    const navigation = navigationRevision.current;
    const renameRequest = ++renameRevision.current;
    const updated = await updateAssistantConversation(current.id, { title });
    if (navigation !== navigationRevision.current || renameRequest !== renameRevision.current
      || !isCurrentConversation(current.id)) return updated;
    replace(updated);
    await loadList({ scope_kind: scopeKind, scope_id: scopeId });
    return updated;
  }, [replace, loadList, isCurrentConversation, scopeKind, scopeId]);

  const stop = useCallback(async () => {
    const current = conversationRef.current;
    const running = current?.turns.at(-1);
    if (!current || !running || running.status !== "running" || running.id.startsWith("pending-")) {
      throw new Error("助手进程尚未建立可停止的轮次连接。");
    }
    await stopAssistantConversationTurn(current.id, running.id);
    if (isCurrentConversation(current.id)) await refresh();
  }, [refresh, isCurrentConversation]);

  useEffect(() => {
    const running = conversation?.turns.at(-1)?.status === "running";
    if (!running || loading) return;
    const timer = setInterval(() => { void refresh().catch(() => undefined); }, 1500);
    return () => clearInterval(timer);
  }, [conversation?.id, conversation?.turns.at(-1)?.status, loading, refresh]);

  return {
    conversation, conversations, draftText, setDraftText, loading, error, setError,
    beginTurn, bindTurnEvents, isCurrentConversation, failUnpersistedTurn,
    flushDraft, refresh, createNew, resume, rename, stop, loadList,
  };
}
