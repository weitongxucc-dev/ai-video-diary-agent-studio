"use client";

import { Check, CircleDashed, Download, History, LoaderCircle, MessageSquarePlus, Mic, Redo2, Share2, Undo2 } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../ui/button";
import { IconButton } from "../ui/icon-button";
import { Tooltip } from "../ui/tooltip";
import styles from "../VideoEditor.module.css";

export function TaskSaveState({ busy, pending, dirty, saved }: { busy: boolean; pending: boolean; dirty: boolean; saved: boolean }) {
  const label = busy ? "处理中" : pending ? "修改方案待确认" : dirty ? "修改尚未应用" : saved ? "已保存" : null;
  if (!label) return null;
  return <Tooltip label={label}><span className={styles.saveState} aria-label={label} role="status" data-dirty={pending || dirty || undefined}>
    {busy ? <LoaderCircle className={styles.spinning} size={14} /> : dirty || pending ? <CircleDashed size={14} /> : <Check size={14} />}
    {pending || dirty ? <span>{pending ? "待确认" : "未应用"}</span> : null}
  </span></Tooltip>;
}

export function TaskOutputActions({ onShare, onSave, shareOpen, canSave, busy }: {
  onShare: () => void; onSave?: () => void; shareOpen: boolean; canSave: boolean; busy: boolean;
}) {
  return <>
    <IconButton label="共享设置" description="设置任务与方案的共享范围" aria-haspopup="dialog" aria-expanded={shareOpen} onClick={onShare}><Share2 /></IconButton>
    {onSave ? <Tooltip label={busy ? "请等待当前操作完成" : canSave ? "将当前成片保存到本机" : "当前版本还没有可保存的成片"}>
      <Button variant="outline" aria-label="保存视频" disabled={busy || !canSave} onClick={onSave}><Download /><span>导出</span></Button>
    </Tooltip> : null}
  </>;
}

export function SelectedObjectReferenceAction({ onReference, referenceLabel }: { onReference: () => void; referenceLabel?: string }) {
  return <IconButton label="引用所选对象到对话" description={referenceLabel ?? "先选择要引用的对象"} onClick={onReference} disabled={!referenceLabel}><MessageSquarePlus /></IconButton>;
}

export function TimelineEditingActions({ busy, canUndo, canRedo, onUndo, onRedo, onRecord, onHistory, referenceAction }: {
  busy: boolean; canUndo: boolean; canRedo: boolean; onUndo: () => void; onRedo: () => void;
  onRecord: () => void; onHistory: () => void; referenceAction?: ReactNode;
}) {
  return <div className={styles.timelineEditingActions} role="group" aria-label="时间线编辑工具">
    <IconButton label="撤销" description={busy ? "请等待当前操作完成" : canUndo ? "撤销上一次修改" : "没有可撤销的修改"} disabled={busy || !canUndo} onClick={onUndo}><Undo2 /></IconButton>
    <IconButton label="重做" description={busy ? "请等待当前操作完成" : canRedo ? "重做刚才撤销的修改" : "没有可重做的修改"} disabled={busy || !canRedo} onClick={onRedo}><Redo2 /></IconButton>
    <span className={styles.toolDivider} aria-hidden />
    {referenceAction}
    <IconButton label="电脑录音替换旁白" description={busy ? "请等待当前操作完成" : "录制声音，替换整条旁白"} disabled={busy} onClick={onRecord}><Mic /></IconButton>
    <IconButton label="历史时间线版本" description="查看和恢复历史时间线版本" onClick={onHistory}><History /></IconButton>
  </div>;
}
