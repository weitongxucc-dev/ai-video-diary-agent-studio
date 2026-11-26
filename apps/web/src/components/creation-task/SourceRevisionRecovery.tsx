"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
import { getProductionMaterial } from "@/lib/api/materials";
import {
    matchesSourceRevisionRecovery, readSourceRevisionRecovery, recoverSourceRevision, sameSourceRevision,
    type SourceRevisionRecoveryInput, type SourceRevisionRecoveryReceipt,
} from "@/lib/api/source-revision-recovery";
import type { SourceMediaSelection } from "@/lib/types/creation-plans";
import { Button } from "../ui/button";
import { errorDetails } from "./request-errors";
import styles from "./SourceRevisionRecovery.module.css";

type Attempt = { planId: string; input: SourceRevisionRecoveryInput };
type Props = {
    taskId: string;
    planId: string;
    versionId: string;
    sequence: number;
    source: SourceMediaSelection;
    memberScope: string | null;
    active: boolean;
    disabled: boolean;
    onRecovered: (receipt: SourceRevisionRecoveryReceipt, isCurrent: () => boolean) => Promise<"updated" | "past" | "unverified">;
};

function readAttempt(storageKey: string): Attempt | null {
    const value = sessionStorage.getItem(storageKey);
    if (!value) return null;
    const saved = JSON.parse(value) as Attempt;
    const input = saved?.input;
    if (!input || typeof saved.planId !== "string" || !saved.planId
        || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(input.request_id)
        || typeof input.asset_id !== "string" || !input.asset_id
        || typeof input.expected_timeline_version_id !== "string" || !input.expected_timeline_version_id
        || !Number.isSafeInteger(input.expected_timeline_sequence)
        || !Number.isFinite(Date.parse(input.expected_bound_asset_updated_at))
        || !Number.isFinite(Date.parse(input.expected_asset_updated_at))) {
        throw new Error("无法核对保存的原复核请求。");
    }
    return saved;
}

function rejected(cause: unknown) {
    return cause instanceof CreationPlanApiError && (
        [401, 403, 404, 409, 422].includes(cause.status)
        || (cause.status === 503 && cause.message === "成员或设备身份暂不可核实，未复核原片")
    );
}

export function SourceRevisionRecovery({ taskId, planId, versionId, sequence, source,
    memberScope, active, disabled, onRecovered }: Props) {
    const detailId = useId();
    const storageKey = memberScope ? `source-revision-recovery:${memberScope}` : null;
    const [attempt, setAttempt] = useState<Attempt | null>(null);
    const [storageReady, setStorageReady] = useState(false);
    const [observation, setObservation] = useState<{ name: string; revision: string; context: string } | null>(null);
    const [feedback, setFeedback] = useState<{ message: string; error?: boolean } | null>(null);
    const [pending, setPending] = useState<"read" | "write" | "receipt" | null>(null);
    const [canReplay, setCanReplay] = useState(false);
    const operation = useRef<object | null>(null);
    const context = JSON.stringify([taskId, planId, versionId, sequence, source.asset_id, source.asset_updated_at]);
    const currentContext = useRef(context);
    const currentObservation = observation?.context === context ? observation : null;
    const matches = currentObservation && sameSourceRevision(source.asset_updated_at, currentObservation.revision);

    useLayoutEffect(() => { currentContext.current = context; }, [context]);

    useLayoutEffect(() => {
        setStorageReady(false);
        setAttempt(null);
        setObservation(null);
        setFeedback(null);
        setCanReplay(false);
        if (!storageKey) return;
        try {
            setAttempt(readAttempt(storageKey));
            setStorageReady(true);
        } catch (cause) {
            setFeedback({ message: errorDetails(cause).message, error: true });
        }
    }, [storageKey]);

    useLayoutEffect(() => {
        setPending(null);
        return () => { operation.current = null; };
    }, [active, taskId, storageKey]);

    async function inspect() {
        if (!active || operation.current || !storageReady || attempt) return;
        const ticket = {};
        operation.current = ticket;
        setPending("read");
        setObservation(null);
        setFeedback(null);
        try {
            const material = await getProductionMaterial(source.asset_id);
            if (operation.current !== ticket || currentContext.current !== context) return;
            if (material.id !== source.asset_id || !Number.isFinite(Date.parse(material.updated_at))) {
                throw new Error("无法核对当前素材登记。");
            }
            setObservation({ name: material.file_name, revision: material.updated_at, context });
        } catch (cause) {
            if (operation.current === ticket && currentContext.current === context) {
                setFeedback({ message: errorDetails(cause).message, error: true });
            }
        } finally {
            if (operation.current === ticket) {
                operation.current = null;
                setPending(null);
            }
        }
    }

    function matchesOriginalHead(value: Attempt) {
        return value.planId === planId && value.input.asset_id === source.asset_id
            && value.input.expected_timeline_version_id === versionId && value.input.expected_timeline_sequence === sequence
            && sameSourceRevision(value.input.expected_bound_asset_updated_at, source.asset_updated_at);
    }

    async function settle(value: Attempt, mode: "submit" | "replay" | "receipt") {
        if (!active || operation.current || !storageKey || !storageReady || (mode !== "receipt" && disabled)) return;
        const ticket = {};
        const isCurrent = () => operation.current === ticket;
        operation.current = ticket;
        setPending(mode === "receipt" ? "receipt" : "write");
        setFeedback(null);
        setCanReplay(false);
        try {
            if (mode === "submit") {
                try {
                    const saved = readAttempt(storageKey);
                    if (saved) {
                        setAttempt(saved);
                        setFeedback({ message: "已有待核对的原复核请求。请先查询原结果。", error: true });
                        return;
                    }
                    sessionStorage.setItem(storageKey, JSON.stringify(value));
                } catch (cause) {
                    setFeedback({ message: `无法保留本次复核编号。${errorDetails(cause).message}`, error: true });
                    return;
                }
                setAttempt(value);
            }
            const read = mode === "receipt";
            let receipt: SourceRevisionRecoveryReceipt;
            try {
                receipt = read ? await readSourceRevisionRecovery(taskId, value.input.request_id)
                    : await recoverSourceRevision(taskId, value.input);
            } catch (cause) {
                if (!isCurrent()) return;
                const replayConflict = mode === "replay" && cause instanceof CreationPlanApiError && cause.status === 409;
                if ((mode === "submit" && rejected(cause)) || replayConflict) {
                    sessionStorage.removeItem(storageKey);
                    setAttempt(null);
                    setObservation(null);
                    setFeedback({ message: `${errorDetails(cause).message} 请重新核对素材登记。如作品版本已变化，请先保留未保存编辑，再重新打开作品。`, error: true });
                } else if (read && cause instanceof CreationPlanApiError && cause.status === 404) {
                    setFeedback({ message: "暂未读到原复核回执。原请求可能仍在处理，可继续查询。" });
                    setCanReplay(matchesOriginalHead(value));
                } else {
                    setFeedback({ message: `原复核结果待核对。${errorDetails(cause).message} 请查询同一请求。`, error: true });
                }
                return;
            }
            if (!isCurrent()) return;
            if (!matchesSourceRevisionRecovery(receipt, taskId, value.planId, value.input)) {
                setFeedback({ message: "回执与原复核请求不一致。请查询同一请求。", error: true });
                return;
            }
            const result = await onRecovered(receipt, isCurrent);
            if (!isCurrent()) return;
            if (result === "unverified") {
                setFeedback({ message: "已找到原复核回执，当前作品绑定尚未核实。请查询同一请求。", error: true });
                return;
            }
            sessionStorage.removeItem(storageKey);
            setAttempt(null);
            setObservation(null);
            setFeedback({ message: result === "updated"
                ? "已复核同一原片。可继续编辑当前版本。"
                : "已找到原复核回执，作品版本或来源绑定已变化。请核对当前版本。" });
        } catch (cause) {
            if (isCurrent()) setFeedback({ message: `原复核结果待核对。${errorDetails(cause).message}`, error: true });
        } finally {
            if (isCurrent()) {
                operation.current = null;
                setPending(null);
            }
        }
    }

    function confirm() {
        if (!currentObservation || matches || attempt || disabled) return;
        void settle({ planId, input: {
            request_id: crypto.randomUUID(),
            expected_timeline_version_id: versionId,
            expected_timeline_sequence: sequence,
            asset_id: source.asset_id,
            expected_bound_asset_updated_at: source.asset_updated_at,
            expected_asset_updated_at: currentObservation.revision,
        } }, "submit");
    }

    return <section className={styles.section} aria-label="原片来源">
        <div className={styles.actions}>
            {attempt ? <>
                <Button type="button" variant="outline" disabled={pending !== null || !active}
                    onClick={() => void settle(attempt, "receipt")}>查询原复核结果</Button>
                {canReplay && matchesOriginalHead(attempt) ? <Button type="button" variant="ghost"
                    disabled={pending !== null || !active || disabled}
                    onClick={() => void settle(attempt, "replay")}>按原请求重试</Button> : null}
            </> : <Button type="button" variant="outline" disabled={!storageReady || pending !== null || !active}
                aria-controls={detailId} aria-expanded={Boolean(currentObservation)} onClick={() => void inspect()}>核对原片来源</Button>}
        </div>
        <div id={detailId}>
            {pending ? <p>{pending === "read" ? "正在读取素材登记…" : pending === "receipt" ? "正在查询原复核回执…" : "正在复核同一原片…"}</p> : null}
            {currentObservation ? <>
                <dl className={styles.facts}>
                    <div><dt>原片素材</dt><dd>{currentObservation.name}</dd></div>
                    <div><dt>原片区间</dt><dd>{source.source_start_seconds}–{source.source_end_seconds} 秒</dd></div>
                    <div><dt>当前作品版本</dt><dd>第 {sequence} 版</dd></div>
                    <div><dt>作品绑定修订</dt><dd><time dateTime={source.asset_updated_at}>{source.asset_updated_at}</time></dd></div>
                    <div><dt>当前登记修订</dt><dd><time dateTime={currentObservation.revision}>{currentObservation.revision}</time></dd></div>
                </dl>
                {matches ? <p role="status">当前素材登记与作品绑定的修订已匹配。</p>
                    : !attempt ? <div className={styles.actions}>
                        <p>素材登记已变化。复核会核对是否仍为同一原片及原区间。</p>
                        <Button type="button" variant="outline" disabled={disabled || pending !== null || !active}
                            onClick={confirm}>确认复核同一原片</Button>
                    </div> : null}
            </> : null}
            {feedback ? <p role={feedback.error ? "alert" : "status"}>{feedback.message}</p> : null}
            {attempt ? <details><summary>原复核请求</summary><p>{attempt.input.request_id}</p></details> : null}
        </div>
    </section>;
}
