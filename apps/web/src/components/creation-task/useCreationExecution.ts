"use client";
import { useEffect, useRef, useState } from "react";
import { getCreationExecution, getCreationTaskWorkspace, previewCreationExecution } from "@/lib/api";
import type { CreationExecutionPreview, CreationExecutionStatus, CreationTaskWorkspace } from "@/lib/types";
import { errorDetails } from './request-errors';
type ExecutionInput = {
    taskId: string;
    confirmedScriptId: string | null;
    timelineVersionId: string | null;
    voiceId: string | undefined;
    voiceVersion: string | undefined;
    onWorkspaceRead: (workspace: CreationTaskWorkspace) => void;
};
function executionSnapshot(execution: CreationExecutionStatus) {
    return `${execution.job?.id ?? "none"}:${execution.job?.status ?? "none"}:${execution.stages.length}`;
}
// Production facts own a separate lifecycle from selection, chat, and unsaved inputs.
export function useCreationExecution({ taskId, confirmedScriptId, timelineVersionId, voiceId, voiceVersion, onWorkspaceRead }: ExecutionInput) {
    const [executionPreview, setExecutionPreview] = useState<CreationExecutionPreview | null>(null);
    const [execution, setExecution] = useState<CreationExecutionStatus | null>(null);
    const [executionError, setExecutionError] = useState<ReturnType<typeof errorDetails> | null>(null);
    const applyWorkspace = useRef(onWorkspaceRead);
    applyWorkspace.current = onWorkspaceRead;
    const currentTask = useRef(taskId);
    currentTask.current = taskId;
    const pollExecution = useRef<() => void>(() => undefined);
    const workspaceRead = useRef<(execution: CreationExecutionStatus) => Promise<boolean>>(async () => false);
    const commandRequest = useRef(0);
    useEffect(() => {
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
            try {
                const next = await getCreationTaskWorkspace(taskId);
                if (!active || request !== workspaceRequest || command !== commandRequest.current) return false;
                applyWorkspace.current(next);
                snapshot = executionSnapshot(execution);
                clearReadError();
                return true;
            } catch (cause) {
                if (active && request === workspaceRequest && command === commandRequest.current) {
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
                    const nextSnapshot = executionSnapshot(current);
                    if (current.job && nextSnapshot !== snapshot) {
                        await readWorkspace(current);
                    } else {
                        clearReadError("execution");
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
    }, [taskId, confirmedScriptId, timelineVersionId, voiceId, voiceVersion]);
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
    return { executionPreview, execution, executionError, acceptExecution, changeExecution, recheckRequirements, pollExecution: () => pollExecution.current() };
}
