"use client";
import { useWorkspacePage } from "../workbench/context";
import { useEffect, useRef, useState } from "react";
import { getCreationExecution, getCreationTaskWorkspace, previewCreationExecution } from "@/lib/api";
import type { CreationExecutionPreview, CreationExecutionStatus, CreationTaskWorkspace } from "@/lib/types";
import { errorDetails } from './request-errors';
import { getSegmentVisualDecision, adoptSegmentVisualDecision, regenerateCreationTaskStoryboard, readCreationTaskStoryboardRegeneration } from '@/lib/api/creation-tasks';
import type { SegmentVisualDecision, SegmentVisualDecisionLocator, CreationTaskStoryboardRegenerationInput, CreationTaskStoryboardRegenerationReceipt } from '@/lib/types/creation-tasks';
type StoryboardAttempt = {
    input: CreationTaskStoryboardRegenerationInput;
    receipt: CreationTaskStoryboardRegenerationReceipt | null;
    inFlight: boolean;
    refreshed: boolean;
};
const storyboardFinished = (receipt: CreationTaskStoryboardRegenerationReceipt | null) =>
    receipt !== null && ["succeeded", "failed", "cancelled"].includes(receipt.status);

type ExecutionInput = {
    taskId: string;
    confirmedScriptId: string | null;
    timelineVersionId: string | null;
    voiceId: string | undefined;
    voiceVersion: string | undefined;
    workspaceReadRevision: number;
    onWorkspaceRead: (workspace: CreationTaskWorkspace, readRevision: number) => boolean;
};
function executionSnapshot(execution: CreationExecutionStatus) {
    return `${execution.job?.id ?? "none"}:${execution.job?.status ?? "none"}:${execution.stages.length}`;
}
// Production facts own a separate lifecycle from selection, chat, and unsaved inputs.
export function useCreationExecution({ taskId, confirmedScriptId, timelineVersionId, voiceId, voiceVersion, workspaceReadRevision, onWorkspaceRead }: ExecutionInput) {
    const viewActive = useWorkspacePage()?.active ?? true;
    const [visualDecision, setVisualDecision] = useState<SegmentVisualDecision | null>(null);
    const [visualError, setVisualError] = useState<string | null>(null);
    const [visualBusy, setVisualBusy] = useState(false);
    const [visualReadRequired, setVisualReadRequired] = useState(false);
    const visualReadRequiredRef = useRef(false);
    const visualEpoch = useRef(0);
    const visualPending = useRef<number | null>(null);
    useEffect(() => {
        visualEpoch.current++; visualPending.current = null;
        setVisualDecision(null); setVisualError(null); setVisualBusy(false);
        visualReadRequiredRef.current = false; setVisualReadRequired(false);
    }, [taskId]);
    const [executionPreview, setExecutionPreview] = useState<CreationExecutionPreview | null>(null);
    const [execution, setExecution] = useState<CreationExecutionStatus | null>(null);
    const [executionError, setExecutionError] = useState<ReturnType<typeof errorDetails> | null>(null);
    const applyWorkspace = useRef(onWorkspaceRead);
    applyWorkspace.current = onWorkspaceRead;
    const receiptRevision = useRef(workspaceReadRevision);
    receiptRevision.current = workspaceReadRevision;
    // Request locators survive task switches; an unknown POST is never replaced by a new UUID.
    const storyboardAttempts = useRef(new Map<string, StoryboardAttempt>());
    const [storyboardAttempt, setStoryboardAttempt] = useState<StoryboardAttempt | null>(null);
    const [storyboardBusy, setStoryboardBusy] = useState(false);
    const [storyboardError, setStoryboardError] = useState<string | null>(null);
    const currentTask = useRef(taskId);
    currentTask.current = taskId;
    const pollExecution = useRef<() => void>(() => undefined);
    const workspaceRead = useRef<(execution: CreationExecutionStatus) => Promise<boolean>>(async () => false);
    const commandRequest = useRef(0);
    useEffect(() => {
        const attempt = storyboardAttempts.current.get(taskId) ?? null;
        setStoryboardAttempt(attempt);
        setStoryboardBusy(attempt?.inFlight ?? false);
        setStoryboardError(null);
    }, [taskId, viewActive]);
    useEffect(() => {
        if (!viewActive) return;
        let active = true;
        let timer: number | undefined;
        let snapshot = "";
        let pollRequest = 0;
        let workspaceRequest = 0;
        let readError: { kind: "execution" | "workspace"; detail: ReturnType<typeof errorDetails> } | null = null;
        function clearReadError(kind?: "execution") {
            const previous = readError;
            if (!previous || (kind && previous.kind !== kind)) return;
            readError = null;
            setExecutionError(current => current === previous.detail ? null : current);
        }
        async function readWorkspace(execution: CreationExecutionStatus) {
            const request = ++workspaceRequest;
            const command = commandRequest.current;
            const readRevision = receiptRevision.current;
            try {
                const next = await getCreationTaskWorkspace(taskId);
                if (!active || request !== workspaceRequest || command !== commandRequest.current) return false;
                if (!applyWorkspace.current(next, readRevision)) return false;
                snapshot = executionSnapshot(execution);
                const attempt = storyboardAttempts.current.get(taskId);
                if (attempt && storyboardFinished(attempt.receipt)) {
                    attempt.refreshed = true;
                    setStoryboardAttempt({ ...attempt });
                }
                clearReadError();
                return true;
            } catch (cause) {
                if (active && request === workspaceRequest && command === commandRequest.current && readRevision === receiptRevision.current) {
                    readError = { kind: "workspace", detail: errorDetails(cause) };
                    setExecutionError(readError.detail);
                }
                return false;
            }
        }
        workspaceRead.current = readWorkspace;
        setExecution(null);
        setExecutionPreview(null);
        setExecutionError(null);
        if (confirmedScriptId) {
            void previewCreationExecution(taskId).then((preview) => {
                if (active)
                    setExecutionPreview(preview);
            }).catch((cause) => { if (active)
                setExecutionError(errorDetails(cause)); });
        }
        async function poll() {
            const request = ++pollRequest;
            const command = commandRequest.current;
            window.clearTimeout(timer);
            let nextDelay = 15000;
            try {
                const current = await getCreationExecution(taskId);
                if (!active || request !== pollRequest)
                    return;
                if (current.job?.status === "queued" || current.job?.status === "running")
                    nextDelay = 2500;
                if (command === commandRequest.current) {
                    setExecution(current);
                    const attempt = storyboardAttempts.current.get(taskId);
                    if (attempt?.receipt && !attempt.inFlight && attempt.receipt.job_id === current.job?.id
                        && attempt.receipt.status !== current.job.status) {
                        await settleStoryboard(attempt, () => readCreationTaskStoryboardRegeneration(taskId, attempt.input.request_id), false);
                    }
                    if (active && request === pollRequest && command === commandRequest.current) {
                        const nextSnapshot = executionSnapshot(current);
                        if ((current.job && nextSnapshot !== snapshot) || (attempt && storyboardFinished(attempt.receipt) && !attempt.refreshed)) {
                            await readWorkspace(current);
                        } else {
                            clearReadError("execution");
                        }
                    }
                }
            }
            catch (cause) {
                if (active && request === pollRequest && command === commandRequest.current) {
                    readError = { kind: "execution", detail: errorDetails(cause) };
                    setExecutionError(readError.detail);
                }
            }
            if (active && request === pollRequest)
                timer = window.setTimeout(() => void poll(), nextDelay);
        }
        pollExecution.current = () => void poll();
        void poll();
        return () => { active = false; pollExecution.current = () => undefined; workspaceRead.current = async () => false; window.clearTimeout(timer); };
    }, [viewActive, taskId, confirmedScriptId, timelineVersionId, voiceId, voiceVersion]);
    function acceptExecution(next: CreationExecutionStatus) {
        if (next.task_id === currentTask.current)
            setExecution(next);
    }
    async function recheckRequirements() {
        const preview = await previewCreationExecution(taskId);
        if (currentTask.current === taskId)
            setExecutionPreview(preview);
        return getCreationExecution(taskId);
    }
    async function settleStoryboard(attempt: StoryboardAttempt,
        operation: () => Promise<CreationTaskStoryboardRegenerationReceipt>, refresh: boolean) {
        if (attempt.inFlight) return;
        const readWorkspace = workspaceRead.current;
        const owns = () => currentTask.current === taskId && workspaceRead.current === readWorkspace;
        attempt.inFlight = true;
        attempt.refreshed = false;
        setStoryboardBusy(true); setStoryboardError(null);
        try {
            const receipt = await operation();
            if (receipt.task_id !== taskId || receipt.request_id !== attempt.input.request_id)
                throw new Error("画面重排结果与上次操作不一致，请检查上次操作结果。");
            attempt.receipt = receipt;
            if (!owns()) return;
            setStoryboardAttempt({ ...attempt });
            if (refresh) await changeExecution(() => getCreationExecution(taskId));
        } catch (cause) {
            if (owns()) setStoryboardError(errorDetails(cause).message);
        } finally {
            attempt.inFlight = false;
            if (currentTask.current === taskId && storyboardAttempts.current.get(taskId) === attempt) {
                setStoryboardAttempt({ ...attempt }); setStoryboardBusy(false);
            }
        }
    }

    async function regenerateStoryboard(input: Omit<CreationTaskStoryboardRegenerationInput, "request_id">) {
        if (currentTask.current !== taskId) return;
        const previous = storyboardAttempts.current.get(taskId);
        if (previous && (previous.inFlight || !storyboardFinished(previous.receipt) || !previous.refreshed)) return;
        const attempt: StoryboardAttempt = { input: { ...input, request_id: crypto.randomUUID() }, receipt: null, inFlight: false, refreshed: false };
        storyboardAttempts.current.set(taskId, attempt);
        setStoryboardAttempt(attempt);
        await settleStoryboard(attempt, () => regenerateCreationTaskStoryboard(taskId, attempt.input), true);
    }

    async function readStoryboard() {
        if (currentTask.current !== taskId) return;
        const attempt = storyboardAttempts.current.get(taskId);
        if (attempt) await settleStoryboard(attempt, () => readCreationTaskStoryboardRegeneration(taskId, attempt.input.request_id), true);
    }

    async function changeExecution(operation: () => Promise<CreationExecutionStatus>) {
        const readWorkspace = workspaceRead.current;
        const command = ++commandRequest.current;
        setExecutionError(null);
        try {
            const next = await operation();
            if (currentTask.current !== taskId || workspaceRead.current !== readWorkspace || command !== commandRequest.current)
                return;
            acceptExecution(next);
            await readWorkspace(next);
            if (currentTask.current !== taskId || workspaceRead.current !== readWorkspace || command !== commandRequest.current) return;
            pollExecution.current();
        }
        catch (cause) {
            if (currentTask.current !== taskId || workspaceRead.current !== readWorkspace || command !== commandRequest.current)
                return;
            setExecutionError(errorDetails(cause));
            if (confirmedScriptId) {
                void previewCreationExecution(taskId).then(preview => {
                    if (currentTask.current === taskId && command === commandRequest.current)
                        setExecutionPreview(preview);
                }).catch(() => undefined);
            }
        }
    }
    function closeVisualDecision() {
        visualEpoch.current++; visualPending.current = null;
        setVisualDecision(null); setVisualError(null); setVisualBusy(false);
    }
    async function readVisualDecision(locator: Pick<SegmentVisualDecisionLocator, 'job_id' | 'section_id'>, more = false) {
        if (visualPending.current !== null) return;
        const epoch = ++visualEpoch.current;
        visualPending.current = epoch; setVisualBusy(true); setVisualError(null);
        try {
            const value = await getSegmentVisualDecision(taskId, locator.job_id, locator.section_id,
                more ? visualDecision?.next_offset ?? 0 : 0);
            if (epoch !== visualEpoch.current || currentTask.current !== taskId) return;
            if (more) {
                if (!visualDecision || value.decision_digest === null || visualDecision.decision_digest === null || value.decision_digest !== visualDecision.decision_digest) throw new Error('原画面决定已变化，请关闭后重新查看；未重新提交采用。');
                setVisualDecision({ ...value, candidate_clips: [...visualDecision.candidate_clips, ...value.candidate_clips] });
            } else {
                setVisualDecision(value);
                visualReadRequiredRef.current = false; setVisualReadRequired(false);
            }
        } catch (cause) {
            if (epoch === visualEpoch.current && currentTask.current === taskId) setVisualError(cause instanceof Error ? cause.message : '原画面决定回读失败');
        } finally {
            if (visualPending.current === epoch) { visualPending.current = null; setVisualBusy(false); }
        }
    }
    async function adoptVisualDecision(confirmedClipIds: string[]) {
        if (visualPending.current !== null || visualReadRequiredRef.current || !visualDecision || !visualDecision.decision_digest || visualDecision.task_id !== taskId) return;
        const saved = visualDecision;
        const epoch = ++visualEpoch.current;
        visualPending.current = epoch; setVisualBusy(true); setVisualError(null);
        try {
            await changeExecution(async () => {
                try {
                    const value = await adoptSegmentVisualDecision(taskId, saved.job_id, saved.section_id,
                        { decision_digest: saved.decision_digest, confirmed_clip_ids: confirmedClipIds });
                    if (epoch === visualEpoch.current && currentTask.current === taskId) setVisualDecision(value.decision);
                    return value.execution;
                } catch (cause) {
                    if (epoch === visualEpoch.current && currentTask.current === taskId) {
                        visualReadRequiredRef.current = true; setVisualReadRequired(true);
                        setVisualError(`${cause instanceof Error ? cause.message : '原作业排队状态未知'}。请回查原决定，不要更换作业或重生成配音。`);
                    }
                    throw cause;
                }
            });
        } finally {
            if (visualPending.current === epoch) { visualPending.current = null; setVisualBusy(false); }
        }
    }
    const storyboard = { busy: storyboardBusy, requestId: storyboardAttempt?.input.request_id ?? null,
        receipt: storyboardAttempt?.receipt ?? null, error: storyboardError,
        refreshPending: !!storyboardAttempt && storyboardFinished(storyboardAttempt.receipt) && !storyboardAttempt.refreshed,
        canSubmit: !storyboardBusy && (!storyboardAttempt || (storyboardFinished(storyboardAttempt.receipt) && storyboardAttempt.refreshed)) };
    return { storyboard, regenerateStoryboard, readStoryboard, visualDecision, visualBusy, visualError, visualReadRequired, readVisualDecision, adoptVisualDecision, closeVisualDecision, executionPreview, execution, executionError, acceptExecution, changeExecution, recheckRequirements, pollExecution: () => pollExecution.current() };
}
