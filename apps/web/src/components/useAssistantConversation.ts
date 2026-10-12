"use client";

import { useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  createAssistantConversation,
  getAssistantConversation,
  getCurrentAssistantConversation,
  listAssistantConversations,
  restoreUnpersistedAssistantTurnDraft,
  resumeAssistantConversation,
  stopAssistantConversationTurn,
  updateAssistantConversation,
} from "@/lib/api";
import type {
  AssistantConversation,
  AssistantConversationScope,
  AssistantConversationTurn,
  AssistantStreamEvent,
  AssistantTaskTurnResult,
} from "@/lib/types";

import { AssistantConversationContext } from "./AssistantConversationContext";

export function assistantTaskTurnResult(turn: AssistantConversationTurn) {
  return turn.result?.kind === "task_turn" ? turn.result as AssistantTaskTurnResult : null;
}

const settledBusinessStatuses = new Set(["confirmed", "rejected", "cancelled", "failed", "stale", "stopped", "expired"]);

export function assistantTurnHasUnknownRequest(turn: AssistantConversationTurn) {
  const result = assistantTaskTurnResult(turn);
  if (result?.continuation && settledBusinessStatuses.has(result.continuation.status)) return false;
  return result?.continuation?.status === "unknown" || result?.response?.edit_request?.status === "unknown"
    || result?.response?.script_tool?.status === "unknown";
}

export function assistantTurnNeedsResultRefresh(turn: AssistantConversationTurn) {
  if (turn.status === "stopped") return false;
  const result = assistantTaskTurnResult(turn);
  if (!result) return false;
  const continuation = result.continuation;
  if (continuation && settledBusinessStatuses.has(continuation.status)) return false;
  const response = result.response;
  return assistantTurnHasUnknownRequest(turn) || continuation?.actionable === true
    || Boolean(response?.media_job && ["queued", "running"].includes(response.media_job.status))
    || (!continuation && response?.edit_preview?.proposal.status === "proposed");
}

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

export function useScopedAssistantConversation(scope: AssistantConversationScope) {
  const { scope_kind: scopeKind, scope_id: scopeId } = scope;
  const scopeKey = `${scopeKind}:${scopeId}`;
  const activeScopeKey = useRef<string | null>(scopeKey);
  activeScopeKey.current = scopeKey;
  const matchesScope = useCallback((value: AssistantConversation | null | undefined) => Boolean(
    value && ((scopeKind === "home" && scopeId === "home") || value.scope_kind === scopeKind && (value.scope_id === scopeId
      || (scopeKind === "material" && scopeId === "library"))),
  ), [scopeKind, scopeId]);
  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  const conversationRef = useRef<AssistantConversation | null>(null);
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [draftText, setDraftTextState] = useState("");
  const draftValue = useRef("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const historyScrollPositions = useRef(new Map<string, number>());
  const historyExpandedDetails = useRef(new Map<string, Map<string, boolean>>());
  const historyFollowBottom = useRef(new Map<string, boolean>());
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftQueue = useRef<Promise<unknown>>(Promise.resolve());
  const revision = useRef(0);
  const loadedScope = useRef<string | null>(null);
  const streamRevision = useRef(0);
  const readRevision = useRef(0);
  const loadEpoch = useRef(0);
  const activeReads = useRef({ epoch: 0, count: 0 });
  const [businessRefreshPaused, setBusinessRefreshPaused] = useState(false);
  const navigationQueue = useRef<Promise<unknown>>(Promise.resolve());
  const navigationRevision = useRef(0);
  const renameRevision = useRef(0);
  const draftVersionByConversation = useRef(new Map<string, number>());
  const draftRevisionByConversation = useRef(new Map<string, number>());
  const localTurnRecovery = useRef(new Map<string, {
    conversationId: string;
    userMessage: string;
    draftRevision: number;
    persistedTurnId: string | null;
    clientRequestId: string;
    initialDraftVersion: number;
    expectedDraftVersion: number | null;
    navigationRevision: number;
  }>());

  const replace = useCallback((next: AssistantConversation | null) => {
    conversationRef.current = next;
    setConversation(next);
  }, []);

  const persistDraftValue = useCallback(async (conversationId: string, text: string) => {
    const current = conversationRef.current;
    const knownVersion = draftVersionByConversation.current.get(conversationId)
      ?? (current?.id === conversationId ? current.draft_version : 0);
    const updated = await updateAssistantConversation(conversationId, {
      draft_text: text, expected_draft_version: knownVersion,
    });
    const latestVersion = Math.max(updated.draft_version, draftVersionByConversation.current.get(conversationId) ?? 0);
    draftVersionByConversation.current.set(conversationId, latestVersion);
    if (conversationRef.current?.id === conversationId && updated.draft_version === latestVersion) {
      const latest = conversationRef.current;
      if (latest) replace({
        ...latest,
        draft_text: updated.draft_text,
        draft_version: updated.draft_version,
        updated_at: updated.updated_at,
      });
    }
    return updated;
  }, [replace]);

  const saveDraftValue = useCallback((conversationId: string, text: string) => {
    const save = draftQueue.current.catch(() => undefined)
      .then(() => persistDraftValue(conversationId, text));
    draftQueue.current = save;
    return save.catch((cause) => {
      const current = conversationRef.current;
      if (current?.id === conversationId
        && activeScopeKey.current === scopeKey) {
        setError(cause instanceof Error ? cause.message : "未发送草稿没有保存。");
      }
      throw cause;
    });
  }, [persistDraftValue, scopeKey]);

  const restoreSubmittedDraft = useCallback((conversationId: string, turnId: string) => {
    const recovery = [...localTurnRecovery.current.values()].find(item =>
      item.conversationId === conversationId && item.persistedTurnId === turnId);
    const current = conversationRef.current;
    if (!recovery || !current || current.id !== conversationId || !matchesScope(current)
      || activeScopeKey.current !== scopeKey || draftValue.current !== "" || current.draft_text !== ""
      || recovery.navigationRevision !== navigationRevision.current
      || draftRevisionFor(draftRevisionByConversation.current, conversationId) !== recovery.draftRevision) return;
    const restoredRevision = advanceDraftRevision(draftRevisionByConversation.current, conversationId);
    const stillOwnsDraft = () => activeScopeKey.current === scopeKey
      && conversationRef.current?.id === conversationId
      && navigationRevision.current === recovery.navigationRevision
      && draftRevisionFor(draftRevisionByConversation.current, conversationId) === restoredRevision
      && draftValue.current === recovery.userMessage;
    revision.current += 1;
    draftValue.current = recovery.userMessage;
    setDraftTextState(recovery.userMessage);
    const save = draftQueue.current.catch(() => undefined).then(async () => {
      if (!stillOwnsDraft()) return;
      const saved = await persistDraftValue(conversationId, recovery.userMessage);
      const latest = conversationRef.current;
      if (stillOwnsDraft() && latest && saved.draft_version < latest.draft_version && latest.draft_text !== "") {
        advanceDraftRevision(draftRevisionByConversation.current, conversationId);
        revision.current += 1;
        draftValue.current = latest.draft_text;
        setDraftTextState(latest.draft_text);
      }
    });
    draftQueue.current = save;
    void save.catch(async cause => {
      if (!stillOwnsDraft()) return;
      setError(cause instanceof Error ? cause.message : "本条要求已回到输入框，但草稿没有确认保存。");
      // A rejected CAS can mean another client saved a newer server draft.
      // Read it once, without retrying this rejected restoration over it.
      try {
        const fresh = await getAssistantConversation(conversationId);
        if (!stillOwnsDraft() || fresh.draft_text === ""
          || fresh.draft_version < (draftVersionByConversation.current.get(conversationId) ?? 0)) return;
        draftVersionByConversation.current.set(conversationId, fresh.draft_version);
        advanceDraftRevision(draftRevisionByConversation.current, conversationId);
        revision.current += 1;
        draftValue.current = fresh.draft_text;
        setDraftTextState(fresh.draft_text);
        const latest = conversationRef.current;
        if (latest) replace({ ...latest, draft_text: fresh.draft_text, draft_version: fresh.draft_version });
      } catch (readCause) {
        if (stillOwnsDraft()) setError(`${cause instanceof Error ? cause.message : "草稿没有确认保存。"}；服务器草稿也未能回读：${readCause instanceof Error ? readCause.message : "读取失败"}`);
      }
    });
  }, [matchesScope, persistDraftValue, replace, scopeKey]);

  const flushDraft = useCallback(async () => {
    if (draftTimer.current) {
      clearTimeout(draftTimer.current);
      draftTimer.current = null;
    }
    revision.current += 1;
    const current = conversationRef.current;
    if (!current) return;
    await draftQueue.current.catch(() => undefined);
    const recoveryOwnsEmptyDraft = draftValue.current === "" && [...localTurnRecovery.current.values()].some((turn) =>
      turn.conversationId === current.id
        && turn.persistedTurnId === null
        && draftRevisionFor(draftRevisionByConversation.current, current.id) === turn.draftRevision);
    if (!recoveryOwnsEmptyDraft) await saveDraftValue(current.id, draftValue.current);
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
    const read = ++readRevision.current;
    const epoch = loadEpoch.current;
    const observedStreamRevision = streamRevision.current;
    const observedRevision = revision.current;
    const observedDraftRevision = draftRevisionFor(draftRevisionByConversation.current, current.id);
    const hasUnpersistedDraft = draftValue.current !== current.draft_text;
    if (activeReads.current.epoch !== epoch) activeReads.current = {epoch, count: 0};
    const reads = activeReads.current;
    reads.count += 1;
    try {
      const fresh = await getAssistantConversation(current.id);
      if (activeScopeKey.current === scopeKey
        && readRevision.current === read && loadEpoch.current === epoch
        && conversationRef.current?.id === fresh.id && matchesScope(fresh)
        && streamRevision.current === observedStreamRevision) {
        const latestLocal = conversationRef.current;
        const freshTurnIds = new Set(fresh.turns.map((turn) => turn.id));
        const localPendingTurns = latestLocal?.turns.filter((turn) => {
          return turn.id.startsWith("pending-") && !freshTurnIds.has(turn.id);
        }) ?? [];
        const preserveDraft = hasUnpersistedDraft || revision.current !== observedRevision
          || draftRevisionFor(draftRevisionByConversation.current, fresh.id) !== observedDraftRevision
          || fresh.draft_version < (draftVersionByConversation.current.get(fresh.id) ?? 0);
        const knownDraftVersion = Math.max(
          fresh.draft_version,
          draftVersionByConversation.current.get(fresh.id) ?? 0,
          latestLocal?.draft_version ?? 0,
        );
        draftVersionByConversation.current.set(fresh.id, knownDraftVersion);
        const currentConversation = {
          ...fresh,
          draft_text: fresh.draft_version < knownDraftVersion
            ? latestLocal?.draft_text ?? fresh.draft_text : fresh.draft_text,
          draft_version: knownDraftVersion,
          turns: localPendingTurns.length ? [...fresh.turns, ...localPendingTurns] : fresh.turns,
        };
        replace(currentConversation);
        if (!preserveDraft && draftValue.current !== fresh.draft_text) {
          advanceDraftRevision(draftRevisionByConversation.current, fresh.id);
          revision.current += 1;
          draftValue.current = fresh.draft_text;
          setDraftTextState(fresh.draft_text);
        }
        for (const turn of fresh.turns) {
          if ((turn.status === "failed" || turn.status === "stopped")
            && assistantTaskTurnResult(turn)?.response?.edit_request?.status !== "unknown") restoreSubmittedDraft(fresh.id, turn.id);
        }
        setConversations((items) => [currentConversation, ...items.filter((item) => item.id !== currentConversation.id)]
          .sort((left, right) => right.updated_at.localeCompare(left.updated_at)));
        return fresh;
      }
      return null;
    } finally { reads.count -= 1; }
  }, [replace, matchesScope, restoreSubmittedDraft, scopeKey]);

  useEffect(() => {
    loadEpoch.current += 1;
    activeScopeKey.current = scopeKey;
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
        draftVersionByConversation.current.set(current.id, current.draft_version);
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
    const recoveries = localTurnRecovery.current;
    const draftRevisions = draftRevisionByConversation.current;
    return () => {
      active = false;
      loadEpoch.current += 1;
      if (draftTimer.current) clearTimeout(draftTimer.current);
      const previous = conversationRef.current;
      const recoveryOwnsEmptyDraft = previous && draftValue.current === "" && [...recoveries.values()].some((turn) =>
        turn.conversationId === previous.id
          && turn.persistedTurnId === null
          && draftRevisionFor(draftRevisions, previous.id) === turn.draftRevision);
      if (previous && previous.draft_text !== draftValue.current && !recoveryOwnsEmptyDraft) {
        void saveDraftValue(previous.id, draftValue.current).catch(() => undefined);
      }
      if (activeScopeKey.current === scopeKey) activeScopeKey.current = null;
    };
  }, [scopeKind, scopeId, replace, saveDraftValue]);

  const setDraftText = useCallback((text: string) => {
    draftValue.current = text;
    setDraftTextState(text);
    const currentRevision = ++revision.current;
    const current = conversationRef.current;
    const id = matchesScope(current) ? current?.id : undefined;
    if (!id) return;
    advanceDraftRevision(draftRevisionByConversation.current, id);
    if (draftTimer.current) clearTimeout(draftTimer.current);
    draftTimer.current = setTimeout(() => {
      if (currentRevision !== revision.current) return;
      void saveDraftValue(id, text).catch(() => undefined);
    }, 450);
  }, [matchesScope, saveDraftValue]);

  const beginTurn = useCallback((message: string) => {
    const current = conversationRef.current;
    if (!current) throw new Error("对话尚未恢复，请稍后重试。");
    if (!matchesScope(current)) {
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
    const clientRequestId = crypto.randomUUID();
    const recovery = {
      conversationId: current.id,
      userMessage: message,
      draftRevision,
      persistedTurnId: null as string | null,
      clientRequestId,
      initialDraftVersion: current.draft_version,
      expectedDraftVersion: null as number | null,
      navigationRevision: navigationRevision.current,
    };
    localTurnRecovery.current.set(turn.id, recovery);
    replace({ ...current, draft_text: "", turns: [...current.turns, turn] });
    draftValue.current = "";
    setDraftTextState("");
    const checkpoint = draftQueue.current.catch(() => undefined).then(async () => {
      const saved = await persistDraftValue(current.id, message);
      recovery.expectedDraftVersion = saved.draft_version;
      return saved.draft_version;
    });
    draftQueue.current = checkpoint.then(() => undefined, () => undefined);
    return {
      conversationId: current.id,
      localTurnId: turn.id,
      clientRequestId,
      draftCheckpoint: checkpoint,
    };
  }, [replace, persistDraftValue, scopeKind, scopeId, matchesScope]);

  const isCurrentConversation = useCallback((conversationId: string) =>
    activeScopeKey.current === scopeKey
      && conversationRef.current?.id === conversationId
      && matchesScope(conversationRef.current), [scopeKey, matchesScope]);

  const failUnpersistedTurn = useCallback(async (conversationId: string, localTurnId: string, _message: string) => {
    const recovery = localTurnRecovery.current.get(localTurnId);
    if (recovery?.conversationId !== conversationId) return false;
    const observedRevision = revision.current;
    const observedDraftRevision = draftRevisionFor(draftRevisionByConversation.current, conversationId);
    const observedStreamRevision = streamRevision.current;
    const isActiveHere = () => activeScopeKey.current === scopeKey
      && conversationRef.current?.id === conversationId && matchesScope(conversationRef.current);
    const canReplaceDraft = () => isActiveHere()
      && revision.current === observedRevision
      && draftRevisionFor(draftRevisionByConversation.current, conversationId) === observedDraftRevision
      && observedDraftRevision === recovery.draftRevision;
    const dropPending = (value: AssistantConversation) => ({
      ...value, turns: value.turns.filter((turn) => turn.id !== localTurnId),
    });
    const mergeRead = (fresh: AssistantConversation, replaceDraft: boolean) => {
      const latest = conversationRef.current;
      if (!latest || latest.id !== conversationId) return;
      const freshTurnIds = new Set(fresh.turns.map((turn) => turn.id));
      const streamAdvanced = streamRevision.current !== observedStreamRevision;
      const currentTurns = streamAdvanced
        ? latest.turns.filter((turn) => turn.id !== localTurnId)
        : latest.turns.filter((turn) => turn.id.startsWith("pending-")
          && !freshTurnIds.has(turn.id) && turn.id !== localTurnId);
      const mergedTurns = [...fresh.turns];
      for (const turn of currentTurns) {
        const index = mergedTurns.findIndex((candidate) => candidate.id === turn.id);
        if (index < 0) mergedTurns.push(turn);
        else if (streamAdvanced) mergedTurns[index] = turn;
      }
      const knownDraftVersion = Math.max(
        fresh.draft_version,
        draftVersionByConversation.current.get(conversationId) ?? 0,
        latest.draft_version,
      );
      draftVersionByConversation.current.set(conversationId, knownDraftVersion);
      const next = {
        ...fresh,
        // Keep server draft metadata distinct from an unsaved local restoration.
        draft_text: fresh.draft_text,
        draft_version: knownDraftVersion,
        turns: mergedTurns,
      };
      replace(next);
      if (replaceDraft) {
        revision.current += 1;
        advanceDraftRevision(draftRevisionByConversation.current, conversationId);
        draftValue.current = fresh.draft_text;
        setDraftTextState(fresh.draft_text);
      }
    };

    try {
      const restored = await restoreUnpersistedAssistantTurnDraft(conversationId, {
        client_request_id: recovery.clientRequestId,
        user_message: recovery.userMessage,
        expected_draft_version: recovery.expectedDraftVersion ?? recovery.initialDraftVersion,
      });
      const saved = restored.conversation;
      const knownDraftVersion = draftVersionByConversation.current.get(conversationId) ?? 0;
      draftVersionByConversation.current.set(conversationId, Math.max(knownDraftVersion, saved.draft_version));
      if (isActiveHere()) {
        if (restored.status === "already_persisted") {
          const refreshed = await getAssistantConversation(conversationId);
          if (isActiveHere()) {
            mergeRead(refreshed, canReplaceDraft() && refreshed.draft_text !== "");
            for (const turn of refreshed.turns) {
              if (turn.status === "failed" || turn.status === "stopped") restoreSubmittedDraft(conversationId, turn.id);
            }
          }
        } else {
          mergeRead(saved, canReplaceDraft());
        }
      }
      return restored.status !== "already_persisted" || Boolean(saved.turns.some((turn) => turn.id === recovery.persistedTurnId));
    } catch (cause) {
      if (isActiveHere()) {
        const latest = conversationRef.current;
        if (latest) replace(dropPending(latest));
        const refreshed = await getAssistantConversation(conversationId).catch(() => null);
        if (refreshed && isActiveHere()) mergeRead(refreshed, canReplaceDraft());
        if (isActiveHere()) {
          setError(cause instanceof Error ? cause.message : "未发送草稿没有恢复；较新的草稿未被覆盖。请重新读取后确认。");
        }
      }
      return false;
    }
  }, [replace, matchesScope, restoreSubmittedDraft, scopeKey]);

  const bindTurnEvents = useCallback((conversationId: string, localTurnId: string) => {
    let persistedTurnId = localTurnId;
    return (event: AssistantStreamEvent) => {
      // The stream receipt belongs to the submitted turn even after navigation
      // has moved the visible hook to another scope. Record its durable identity
      // before deciding whether this hook should render the event.
      if (event.type === "turn_started") {
        persistedTurnId = event.turn_id;
        if (event.draft_version !== undefined) {
          const knownVersion = draftVersionByConversation.current.get(conversationId) ?? 0;
          draftVersionByConversation.current.set(conversationId, Math.max(knownVersion, event.draft_version));
        }
        const recovery = localTurnRecovery.current.get(localTurnId);
        if (recovery?.conversationId === conversationId) recovery.persistedTurnId = event.turn_id;
      }
      if (activeScopeKey.current !== scopeKey) return;
      const current = conversationRef.current;
      if (!current || current.id !== conversationId) return;
      const turns = [...current.turns];
      const index = turns.findIndex((turn) =>
        turn.id === persistedTurnId || (event.type === "turn_started" && (turn.id === localTurnId || turn.id === event.turn_id)));
      if (index < 0) return;
      const turn = turns[index];
      if (event.type === "turn_started") {
        turns[index] = { ...turn, id: event.turn_id, status: event.turn_state, updated_at: new Date().toISOString() };
        const nextVersion = event.draft_version ?? current.draft_version;
        if (nextVersion > current.draft_version) draftVersionByConversation.current.set(current.id, nextVersion);
        replace({ ...current, draft_text: draftValue.current, draft_version: Math.max(current.draft_version, nextVersion), turns });
        streamRevision.current += 1;
        if (event.turn_state === "failed" || event.turn_state === "stopped") restoreSubmittedDraft(conversationId, event.turn_id);
        return;
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
      if (event.type === "turn_status" && (event.turn_state === "failed" || event.turn_state === "stopped")) {
        restoreSubmittedDraft(conversationId, event.turn_id);
      }
    };
  }, [replace, restoreSubmittedDraft, scopeKey]);

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
      draftVersionByConversation.current.set(created.id, created.draft_version);
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
      if (!matchesScope(resumed)) {
        throw new Error("这段对话属于其他页面或对象，请回到原范围恢复。");
      }
      advanceDraftRevision(draftRevisionByConversation.current, resumed.id);
      draftVersionByConversation.current.set(resumed.id, resumed.draft_version);
      replace(resumed);
      draftValue.current = resumed.draft_text;
      setDraftTextState(resumed.draft_text);
      return resumed;
    });
    navigationQueue.current = operation.then(() => undefined, () => undefined);
    return operation;
  }, [scopeKey, scopeKind, scopeId, replace, flushDraft, matchesScope]);

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

  const running = conversation?.turns.at(-1)?.status === "running";
  const pendingResults = (conversation?.turns ?? []).filter(assistantTurnNeedsResultRefresh)
    .map(turn => [turn.id, assistantTaskTurnResult(turn)?.continuation?.status,
      assistantTaskTurnResult(turn)?.response?.media_job?.status, assistantTaskTurnResult(turn)?.response?.edit_request?.status]);
  const pendingKey = JSON.stringify(pendingResults);
  const hasPendingResults = pendingResults.length > 0;
  useEffect(() => {
    setBusinessRefreshPaused(false);
    if ((!running && !hasPendingResults) || loading) return;
    const id = conversation?.id;
    let active = true, reading = false, attempts = 0;
    const tick = async () => {
      if (!active || reading || (activeReads.current.epoch === loadEpoch.current && activeReads.current.count > 0)) return;
      if (!running && attempts >= 24) {
        clearInterval(timer);
        setBusinessRefreshPaused(true);
        return;
      }
      reading = true;
      attempts += 1;
      try { await refresh(); }
      catch (cause) {
        if (active && conversationRef.current?.id === id && activeScopeKey.current === scopeKey) {
          setError(cause instanceof Error ? cause.message : "暂时无法查询本轮结果。");
          setBusinessRefreshPaused(true);
          clearInterval(timer);
        }
      } finally { reading = false; }
    };
    const timer = setInterval(() => { void tick(); }, running ? 1500 : 5000);
    return () => { active = false; clearInterval(timer); };
  }, [conversation?.id, running, pendingKey, hasPendingResults, loading, refresh, scopeKey]);

  return {
    conversation, conversations, draftText, setDraftText, loading, error, setError,
    beginTurn, bindTurnEvents, isCurrentConversation, failUnpersistedTurn,
    flushDraft, refresh, createNew, resume, rename, stop, loadList, historyScrollPositions, historyExpandedDetails, historyFollowBottom,
    businessRefreshPaused,
  };
}

export function useAssistantConversation(scope: AssistantConversationScope) {
  const shared = useContext(AssistantConversationContext);
  // Provider presence is fixed for the lifetime of every consumer. Standalone
  // tests and isolated embedded consumers retain the scoped entry point.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return shared ?? useScopedAssistantConversation(scope);
}
