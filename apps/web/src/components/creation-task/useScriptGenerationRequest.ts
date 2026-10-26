"use client";
import { useEffect, useRef, useState } from "react";
import { CreationPlanApiError, generateCreationTaskScript, getCreationTaskScriptGenerationRequest, stopAssistantConversationTurn } from "@/lib/api";
import type { CreationTaskScriptGenerationReceipt as Receipt } from "@/lib/types/creation-tasks";
import { useWorkspaceViewState } from "../workbench/context";

type Input = { taskId: string; requestId: string };
type Working = "submit" | "read" | "stop" | null;
function unsettled(receipt: Receipt | null) {
    return !receipt || receipt.turn_status === "running" || receipt.run_status === "running"
        || ["running", "stopping", "unknown"].includes(receipt.status);
}
export function useScriptGenerationRequest({ taskId, visible, getContextKey, onDraftReady, onStart }: {
    taskId: string; visible: boolean; getContextKey: () => object | null;
    onDraftReady: (receipt: Receipt, stillCurrent: () => boolean) => Promise<void>;
    onStart: () => void;
}) {
    const [input, setInput] = useWorkspaceViewState<Input | null>(`script-generation:${taskId}`, null);
    const inputRef = useRef(input); inputRef.current = input;
    const [receipt, setReceipt] = useState<Receipt | null>(null);
    const receiptRef = useRef<Receipt | null>(null);
    const [working, setWorking] = useState<Working>(null);
    const workingRef = useRef<Working>(null);
    const [error, setError] = useState<string | null>(null);
    const [stopping, setStopping] = useState(false);
    const mounted = useRef(true);
    const current = useRef({ taskId, getContextKey, onDraftReady, onStart });
    current.current = { taskId, getContextKey, onDraftReady, onStart };
    const applied = useRef<string | null>(null);
    const contextOwner = useRef<{ requestId: string; key: object | null } | null>(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    useEffect(() => {
        receiptRef.current = null; workingRef.current = null; applied.current = null; contextOwner.current = null;
        setReceipt(null); setWorking(null); setStopping(false); setError(null);
    }, [taskId]);
    const owns = (original: Input) => mounted.current && current.current.taskId === original.taskId && inputRef.current?.requestId === original.requestId;
    function busy(value: Working) { workingRef.current = value; setWorking(value); }
    function message(cause: unknown) { return cause instanceof CreationPlanApiError ? [cause.message, cause.impact, cause.repairAction].filter(Boolean).join(" ") : cause instanceof Error ? cause.message : "原生成结果未能读回，原 UUID 和当前编辑保留。"; }
    async function accept(original: Input, next: Receipt) {
        const old = receiptRef.current;
        if (next.request_id !== original.requestId || next.task_id !== original.taskId || next.requested_effect !== "draft"
            || (next.status === "draft_ready" && (!next.script_id || !next.script_updated_at))
            || (old && old.request_id === original.requestId && (old.conversation_id !== next.conversation_id || old.turn_id !== next.turn_id || old.run_id !== next.run_id || (old.script_id && old.script_id !== next.script_id)))) {
            throw new Error("原生成回执与请求或目标身份不符，当前草稿和原 UUID 保留。");
        }
        receiptRef.current = next; setReceipt(next); setStopping(next.stop_requested);
        const stillCurrent = () => owns(original) && contextOwner.current?.requestId === original.requestId
            && contextOwner.current.key !== null && current.current.getContextKey() === contextOwner.current.key;
        const key = `${next.script_id}:${next.script_updated_at}`;
        if (next.status === "draft_ready" && !next.result_stale && stillCurrent() && applied.current !== key) {
            await current.current.onDraftReady(next, stillCurrent);
            if (owns(original)) applied.current = key;
        }
    }
    async function generate() {
        if (workingRef.current || (inputRef.current && unsettled(receiptRef.current))) return;
        current.current.onStart();
        const original = { taskId: current.current.taskId, requestId: crypto.randomUUID() };
        contextOwner.current = { requestId: original.requestId, key: current.current.getContextKey() };
        inputRef.current = original; setInput(original); receiptRef.current = null; setReceipt(null); applied.current = null;
        setStopping(false); setError(null); busy("submit");
        try {
            const next = await generateCreationTaskScript(original.taskId, { request_id: original.requestId });
            if (owns(original)) await accept(original, next);
        } catch (cause) {
            if (!owns(original)) return;
            if (!receiptRef.current && cause instanceof CreationPlanApiError && [401,403,404,409,422].includes(cause.status)) { inputRef.current = null; setInput(null); }
            setError(message(cause));
        } finally { if (mounted.current && current.current.taskId === original.taskId && (!inputRef.current || owns(original))) busy(null); }
    }
    async function query() {
        const original = inputRef.current;
        if (!original || original.taskId !== current.current.taskId || workingRef.current) return;
        busy("read"); setError(null);
        try {
            const next = await getCreationTaskScriptGenerationRequest(original.taskId, original.requestId);
            if (owns(original)) await accept(original, next);
        } catch (cause) { if (owns(original)) setError(cause instanceof CreationPlanApiError && cause.status === 404 ? "尚未查到原生成回执，不代表未提交；原 UUID 和当前编辑继续保留。" : message(cause)); }
        finally { if (owns(original)) busy(null); }
    }
    async function stop() {
        const original = inputRef.current, target = receiptRef.current;
        if (!original || !target || workingRef.current || target.turn_status !== "running") return;
        busy("stop"); setStopping(true); setError(null);
        try {
            const result = await stopAssistantConversationTurn(target.conversation_id, target.turn_id);
            if (result.turn_id !== target.turn_id) throw new Error("停止回执与原生成回合不符，请查询原生成结果。");
        } catch (cause) { if (owns(original)) setError(`停止结果待核对。${message(cause)}`); }
        finally { if (owns(original)) busy(null); }
    }
    useEffect(() => {
        if (!visible || !input || !receipt || working || error || receipt.status === "unknown" || !unsettled(receipt)) return;
        const timer = window.setTimeout(() => { void query(); }, 1500);
        return () => window.clearTimeout(timer);
    }, [visible, input, receipt, working, error, stopping]);
    const currentReceipt = receipt?.task_id === taskId && receipt.request_id === input?.requestId ? receipt : null;
    return { input: input?.taskId === taskId ? input : null, receipt: currentReceipt, working, error, stopping, generate, query, stop,
        blocked: Boolean(working || (input && unsettled(currentReceipt))) };
}
