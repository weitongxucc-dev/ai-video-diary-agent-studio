"use client";
import type { OpenAssistantResult } from "@/lib/types/assistant";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AssistantConversationPanel } from "@/components/AssistantConversationPanel";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import { getCreationTaskAssistantHistory } from "@/lib/api";
import type { AssistantConversationTurn, CreationTaskEditPreviewResponse } from "@/lib/types";
import editorUi from "../VideoEditor.module.css";
import { Button } from "../ui/button";
import { assistantHistoryTurns, type AssistantTurnRecord } from "./assistant-history";
import type { BusyAction } from './edit-context';
import { EditPreviewCard } from './EditPreviewCard';
import { errorDetails } from "./request-errors";

export function TaskConversationHistory({ taskId }: { taskId: string }) {
    const [attempt, setAttempt] = useState(0);
    const [history, setHistory] = useState<{ status: "loading" } | { status: "ready"; turns: AssistantTurnRecord[] } | { status: "failed"; message: string }>({ status: "loading" });
    useEffect(() => {
        let active = true;
        void getCreationTaskAssistantHistory(taskId).then(result => {
            if (active) setHistory({ status: "ready", turns: assistantHistoryTurns(result.items) });
        }).catch(cause => {
            if (active) setHistory({ status: "failed", message: errorDetails(cause).message });
        });
        return () => { active = false; };
    }, [taskId, attempt]);

    if (history.status === "loading") return <p role="status">正在读取旧版对话…</p>;
    if (history.status === "failed") return <>
        <p role="alert">旧版对话读取失败。{history.message}</p>
        <Button variant="outline" onClick={() => { setHistory({ status: "loading" }); setAttempt(value => value + 1); }}>重试读取旧版对话</Button>
    </>;
    return history.turns.length ? <details className="assistant-history">
        <summary>旧版只读历史</summary>
        <ol>{history.turns.map((turn, index) => <li key={index}><small>{turn.context}</small><p>{turn.command}</p><p>{turn.reply}</p></li>)}</ol>
    </details> : null;
}

export function ContextCommandBar({ localConfirmation = false, compact = false, scriptMode = false, busy, command, onCancel, onCommandChange, onConfirm, onExactPreview, onSelectionPreview, preview, assistantState, onTransferReply, onOpenResultStage, onOpenEditProposal, transferDraftState, onAssistantSend, onConversationChange, previewCommand, legacyHistory, assistantConfigured, selectedContextLabel, materialContextLabel, onClearReference, selectedContextPlaceholder, }: {
    localConfirmation?: boolean;
    compact?: boolean;
    scriptMode?: boolean;
    busy: BusyAction | null;
    command: string;
    onCancel: () => void;
    onCommandChange: (value: string) => void;
    onConfirm: () => void;
    onExactPreview: () => void;
    onSelectionPreview: (indices: number[]) => void;
    preview: CreationTaskEditPreviewResponse | null;
    assistantState: ReturnType<typeof useAssistantConversation>;
    onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
    onOpenResultStage?: OpenAssistantResult;
    onOpenEditProposal?: (turn: AssistantConversationTurn) => void;
    transferDraftState: "none" | "draft" | "confirmed";
    onAssistantSend: () => Promise<void>;
    onConversationChange: () => void;
    previewCommand: string;
    legacyHistory?: ReactNode;
    assistantConfigured: boolean;
    selectedContextLabel: string | null;
    materialContextLabel: string | null;
    onClearReference: () => void;
    selectedContextPlaceholder: string;
}) {
    const barRef = useRef<HTMLElement>(null);
    const [chosen, setChosen] = useState<number[] | null>(null);
    useEffect(() => {
        if (preview && !compact)
            barRef.current?.scrollIntoView?.({ block: "end" });
    }, [preview, compact]);
    useEffect(() => { setChosen(null); }, [preview?.proposal.id]);
    return (<section aria-label={scriptMode ? "文案对话" : "修改视频"} className={editorUi.chatCommand} ref={barRef}>
      <AssistantConversationPanel busy={busy !== null} compact={compact} configured={assistantConfigured} onTransferReply={onTransferReply} onOpenResultStage={onOpenResultStage} onOpenEditProposal={onOpenEditProposal} transferDraftState={transferDraftState} input={assistantState.draftText} inputLabel={scriptMode ? "文案要求" : "局部修改要求"} onInput={onCommandChange} onSend={async () => onAssistantSend()} onConversationChange={onConversationChange} placeholder={selectedContextPlaceholder} scopeLabel={materialContextLabel ? `当前素材 · ${materialContextLabel}` : scriptMode ? "当前任务 · 文案" : selectedContextLabel ? `当前任务 · ${selectedContextLabel}` : "当前任务"} state={assistantState} legacyHistory={legacyHistory}/>
      {selectedContextLabel && !scriptMode ? <button className={editorUi.composerPreview} disabled={busy !== null || !command.trim()} onClick={onExactPreview} type="button">精确预览修改</button> : null}
      {preview && !localConfirmation ? <EditPreviewCard key={preview.proposal.id} preview={preview} busy={busy} compact={compact} command={previewCommand} onCancel={onCancel} onConfirm={onConfirm} kind="modal" chosen={chosen} onChosen={setChosen} onSelectionPreview={onSelectionPreview}/> : null}
    </section>);
}
