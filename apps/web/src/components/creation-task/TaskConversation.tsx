"use client";
import { useEffect, useRef, useState } from "react";
import { AssistantConversationPanel } from "@/components/AssistantConversationPanel";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import type { AssistantConversationTurn, CreationTaskEditPreviewResponse, AssistantTurnActivity } from "@/lib/types";
import editorUi from "../VideoEditor.module.css";
import type { BusyAction } from './edit-context';
import { EditPreviewCard } from './EditPreviewCard';
export function ContextCommandBar({ localConfirmation = false, compact = false, scriptMode = false, busy, command, onCancel, onCommandChange, onConfirm, onExactPreview, onSelectionPreview, preview, assistantState, onTransferReply, onOpenEditProposal, transferDraftState, onAssistantSend, onConversationChange, previewCommand, assistantTurns, assistantConfigured, selectedContextLabel, materialContextLabel, onClearReference, selectedContextPlaceholder, }: {
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
    onOpenEditProposal?: (turn: AssistantConversationTurn) => void;
    transferDraftState: "none" | "draft" | "confirmed";
    onAssistantSend: () => Promise<void>;
    onConversationChange: () => void;
    previewCommand: string;
    assistantTurns: {
        command: string;
        reply: string;
        context: string;
        activity?: AssistantTurnActivity;
    }[];
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
      <AssistantConversationPanel busy={busy !== null} compact={compact} configured={assistantConfigured} onTransferReply={onTransferReply} onOpenEditProposal={onOpenEditProposal} transferDraftState={transferDraftState} input={assistantState.draftText} inputLabel={scriptMode ? "文案要求" : "局部修改要求"} onInput={onCommandChange} onSend={async () => onAssistantSend()} onConversationChange={onConversationChange} placeholder={selectedContextPlaceholder} scopeLabel={materialContextLabel ? `当前素材 · ${materialContextLabel}` : scriptMode ? "当前任务 · 文案" : selectedContextLabel ? `当前任务 · ${selectedContextLabel}` : "当前任务"} state={assistantState} legacyHistory={assistantTurns.length ? <details className="assistant-history"><summary>旧版只读历史</summary><ol>{assistantTurns.map((turn, index) => <li key={index}><small>{turn.context}</small><p>{turn.command}</p><p>{turn.reply}</p></li>)}</ol></details> : undefined}/>
      {selectedContextLabel && !scriptMode ? <button className={editorUi.composerPreview} disabled={busy !== null || !command.trim()} onClick={onExactPreview} type="button">精确预览修改</button> : null}
      {preview && !localConfirmation ? <EditPreviewCard key={preview.proposal.id} preview={preview} busy={busy} compact={compact} command={previewCommand} onCancel={onCancel} onConfirm={onConfirm} kind="modal" chosen={chosen} onChosen={setChosen} onSelectionPreview={onSelectionPreview}/> : null}
    </section>);
}
