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
    useEffect(() => {
        let active = true;
        let timer: number | undefined;
        let snapshot = "";
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
            window.clearTimeout(timer);
            let nextDelay = 15000;
            try {
                const current = await getCreationExecution(taskId);
                if (!active)
                    return;
                setExecution(current);
                if (current.job?.status === "queued" || current.job?.status === "running")
                    nextDelay = 2500;
                const nextSnapshot = `${current.job?.id ?? "none"}:${current.job?.status ?? "none"}:${current.stages.length}`;
                if (current.job && nextSnapshot !== snapshot) {
                    snapshot = nextSnapshot;
                    const next = await getCreationTaskWorkspace(taskId);
                    if (!active)
                        return;
                    applyWorkspace.current(next);
                }
            }
            catch (cause) {
                if (active)
                    setExecutionError(errorDetails(cause));
            }
            if (active)
                timer = window.setTimeout(() => void poll(), nextDelay);
        }
        pollExecution.current = () => void poll();
        void poll();
        return () => { active = false; pollExecution.current = () => undefined; window.clearTimeout(timer); };
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
        setExecutionError(null);
        try {
            const next = await operation();
            if (currentTask.current !== taskId)
                return;
            acceptExecution(next);
            pollExecution.current();
            const updated = await getCreationTaskWorkspace(taskId);
            if (currentTask.current === taskId)
                applyWorkspace.current(updated);
        }
        catch (cause) {
            if (currentTask.current !== taskId)
                return;
            setExecutionError(errorDetails(cause));
            if (confirmedScriptId) {
                void previewCreationExecution(taskId).then(preview => {
                    if (currentTask.current === taskId)
                        setExecutionPreview(preview);
                }).catch(() => undefined);
            }
        }
    }
    return { executionPreview, execution, executionError, acceptExecution, changeExecution, recheckRequirements, pollExecution: () => pollExecution.current() };
}
