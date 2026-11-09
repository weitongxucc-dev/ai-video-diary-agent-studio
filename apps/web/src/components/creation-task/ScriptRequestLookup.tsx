"use client";
import { useEffect, useRef, useState } from "react";
import { CreationPlanApiError } from "@/lib/api";
import { getCreationTaskScriptWindowRequest } from "@/lib/api/creation-tasks";
import type { CreationTaskScriptRequestLocator, CreationTaskScriptWindowSaved, CreationTaskScriptConfirmed, CreationTaskScriptWindowUnsettled, CreationTaskScriptWindowResult } from "@/lib/types/creation-tasks";
import { Button } from "../ui/button";

export function ScriptRequestLookup({ taskId, requestId, locator, disabled, verify, onResult }: {
    taskId: string; requestId: string; locator: CreationTaskScriptRequestLocator; disabled: boolean;
    verify: (result?: CreationTaskScriptWindowResult) => Promise<void>;
    onResult: (result: CreationTaskScriptWindowSaved | CreationTaskScriptConfirmed) => Promise<void>;
}) {
    const [checking, setChecking] = useState(false);
    const [result, setResult] = useState<CreationTaskScriptWindowUnsettled | null>(null);
    const [error, setError] = useState<string | null>(null);
    const active = useRef(true);
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    async function inspect(includeInput = false) {
        setChecking(true);
        setError(null);
        setResult(null);
        try {
            await verify();
            if (!active.current) return;
            const value = await getCreationTaskScriptWindowRequest(taskId, requestId, locator, { include_input: includeInput });
            if (!active.current) return;
            await verify(value);
            if (!active.current) return;
            if (value.status === "saved" || value.status === "confirmed") await onResult(value);
            else setResult(value);
        } catch (cause) {
            if (!active.current) return;
            setError(cause instanceof CreationPlanApiError && cause.status === 404
                ? "尚未查到原请求回执，不代表请求未提交；原请求继续保留。"
                : cause instanceof Error ? cause.message : "原请求回执未能读回，未重新提交。");
        } finally { if (active.current) setChecking(false); }
    }
    return <section aria-label="原文案请求对账">
        <p>原请求：{requestId}。只读取回执，不重新提交正文或核查。</p>
        {result ? <><p role="status">{result.status === "failed" ? "原请求记录为失败，但提交结果仍未确认。" : "原请求结果仍未知。"}原请求继续保留。</p><p>{result.message}</p>
            {result.error_detail != null ? <details><summary>原请求失败详情</summary><pre>{typeof result.error_detail === "string" ? result.error_detail : JSON.stringify(result.error_detail, null, 2)}</pre></details> : null}</> : null}
        {result?.submitted_input ? <section aria-label="原请求输入"><p>原请求输入已读回，仅用于核对，不覆盖当前草稿，不重新提交。</p><pre>{JSON.stringify(result.submitted_input, null, 2)}</pre></section> : null}
        {result?.submitted_input_available ? <Button variant="outline" disabled={disabled || checking} onClick={() => void inspect(true)}>恢复原请求输入</Button> : null}
        {error ? <p role="alert">{error}</p> : null}
        <Button variant="outline" disabled={disabled || checking} onClick={() => void inspect()}>{checking ? "正在查询原请求…" : "查询原请求结果"}</Button>
    </section>;
}
