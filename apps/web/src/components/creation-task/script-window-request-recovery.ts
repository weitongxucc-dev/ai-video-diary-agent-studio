"use client";
import { useEffect, useRef, useState } from "react";
import { CreationPlanApiError } from "@/lib/api";
import { readPreflightDraftScope, sameDraftScope, validDraftId, type DraftScope } from "@/lib/preflight-draft";
import type { CreationTaskScriptRequestLocator, CreationTaskScriptWindowResult } from "@/lib/types/creation-tasks";

type Pending = DraftScope & { taskId: string; scriptId: string | null; operation: CreationTaskScriptRequestLocator["operation"]; requestId: string };
const fields = ["memberId", "workspaceId", "taskId", "scriptId", "operation", "requestId"];
function storageKey(scope: DraftScope, taskId: string, scriptId: string | null) {
    return `script-window-request:${JSON.stringify([scope.memberId, scope.workspaceId, taskId, scriptId])}`;
}
function parse(raw: string | null, scope: DraftScope, taskId: string, scriptId: string | null): Pending | null {
    if (raw === null) return null;
    const value = raw.length <= 2048 ? JSON.parse(raw) : null;
    if (!value || Object.keys(value).length !== fields.length || fields.some(field => !Object.hasOwn(value, field))
        || !sameDraftScope(value, scope) || value.taskId !== taskId || value.scriptId !== scriptId
        || (scriptId === null ? value.operation !== "start" : !["patch", "confirm"].includes(value.operation))
        || typeof value.requestId !== "string" || !validDraftId(value.requestId)) {
        throw new Error("原请求定位与当前身份或稿件不符，未提交新请求；请保留定位并核对。");
    }
    return value;
}
function matches(value: CreationTaskScriptWindowResult, pending: Pending) {
    return value.task_id === pending.taskId && value.request_id === pending.requestId
        && (pending.scriptId === null || value.script_id === pending.scriptId)
        && (value.status === "saved" ? pending.operation !== "confirm" && !!value.script_id
            : value.status === "confirmed" ? pending.operation === "confirm"
                : value.operation === pending.operation);
}

// This is only a request locator. Current identity and the server receipt remain authoritative.
export function useScriptWindowRequestRecovery(taskId: string, scriptId: string | null) {
    const [pending, setPending] = useState<Pending | null>(null);
    const [ready, setReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const context = useRef<{ scope: DraftScope; key: string; pending: Pending | null } | null>(null);
    const active = useRef(false);
    const busy = useRef(false);
    function failure(cause: unknown): Error {
        const failure = new Error(`原请求恢复不可用：${cause instanceof Error ? cause.message : "无法访问会话存储"}。未重新提交，请核对身份和会话存储后刷新。`);
        if (active.current) { setError(failure.message); setReady(false); setPending(null); }
        return failure;
    }
    useEffect(() => {
        active.current = true;
        let cancelled = false;
        void readPreflightDraftScope().then(scope => {
            if (cancelled) return;
            const key = storageKey(scope, taskId, scriptId);
            const saved = parse(sessionStorage.getItem(key), scope, taskId, scriptId);
            context.current = { scope, key, pending: saved };
            setPending(saved);
            setReady(true);
        }).catch(cause => { if (!cancelled) failure(cause); });
        return () => { cancelled = true; active.current = false; };
    }, [taskId, scriptId]);
    async function current() {
        const original = context.current;
        try {
            const scope = await readPreflightDraftScope();
            if (!active.current) throw new Error("原页面已关闭");
            if (!original || !sameDraftScope(scope, original.scope)) throw new Error("当前身份或工作区已变化");
            const saved = parse(sessionStorage.getItem(original.key), scope, taskId, scriptId);
            if (saved?.requestId !== original.pending?.requestId || saved?.operation !== original.pending?.operation) throw new Error("原请求定位已变化");
            return original;
        } catch (cause) { throw failure(cause); }
    }
    async function verify(value?: CreationTaskScriptWindowResult) {
        const state = await current();
        if (!state.pending || (value && !matches(value, state.pending))) throw new Error("回执与原任务、稿件或请求操作不符，原请求继续保留。");
    }
    async function clear(value?: CreationTaskScriptWindowResult) {
        await verify(value);
        const state = context.current!;
        try { sessionStorage.removeItem(state.key); } catch (cause) { throw failure(cause); }
        state.pending = null;
        setPending(null);
    }
    async function execute<T extends CreationTaskScriptWindowResult>(requestId: string, operation: Pending["operation"], send: () => Promise<T>): Promise<T> {
        if (!ready || busy.current || context.current?.pending) throw new Error("原请求尚待核对，不能重复提交。");
        busy.current = true;
        try {
            const state = await current();
            const record: Pending = { ...state.scope, taskId, scriptId, operation, requestId };
            const raw = JSON.stringify(record);
            parse(raw, state.scope, taskId, scriptId);
            try { sessionStorage.setItem(state.key, raw); } catch (cause) { throw failure(cause); }
            state.pending = record;
            setPending(record);
            let value: T;
            try { value = await send(); }
            catch (cause) {
                // Only the existing definite write-rejection contract releases the locator.
                // GET failures, unknown receipts and storage/identity errors never enter this branch.
                if (cause instanceof CreationPlanApiError && [401, 403, 404, 409, 422].includes(cause.status)) await clear();
                throw cause;
            }
            await verify(value);
            return value;
        } finally { busy.current = false; }
    }
    return { pending, ready, error, verify, clear, execute };
}
