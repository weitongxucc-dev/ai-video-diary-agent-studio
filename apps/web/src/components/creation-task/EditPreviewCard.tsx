"use client";
import { WorkspaceVideo, WorkspaceAudio } from "../workbench/WorkspaceMedia";
import NextImage from "next/image";
import { Check, LoaderCircle } from "lucide-react";
import { useEffect, useRef } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { apiUrl } from "@/lib/api";
import type { CreationTaskEditPreviewResponse } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import type { AudioEditInput } from "@/lib/types";
import editorUi from "../VideoEditor.module.css";
import type { BusyAction } from './edit-context';
export function PreviewVideo({ url, label, muted, pauseOthers }: {
    url: string;
    label: string;
    muted: boolean;
    pauseOthers: (current: HTMLVideoElement) => void;
}) {
    const frameRef = useRef<number | null>(null);
    const videoRef = useRef<HTMLVideoElement>(null);
    const previousUrlRef = useRef(url);
    const match = /#t=(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)$/.exec(url);
    const range = match ? { start: Number(match[1]), end: Number(match[2]) } : null;
    useEffect(() => {
        if (previousUrlRef.current === url)
            return;
        previousUrlRef.current = url;
        if (frameRef.current !== null)
            cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
        if (videoRef.current) {
            videoRef.current.pause();
            if (range)
                videoRef.current.currentTime = range.start;
        }
    }, [url, range]);
    useEffect(() => () => {
        if (frameRef.current !== null)
            cancelAnimationFrame(frameRef.current);
    }, []);
    const stopAtEnd = (video: HTMLVideoElement) => {
        if (!range)
            return;
        if (video.currentTime < range.start)
            video.currentTime = range.start;
        if (video.currentTime >= range.end) {
            video.pause();
            video.currentTime = range.end;
        }
    };
    const watchPlayback = (video: HTMLVideoElement) => {
        if (frameRef.current !== null)
            cancelAnimationFrame(frameRef.current);
        const tick = () => {
            frameRef.current = null;
            stopAtEnd(video);
            if (!video.paused)
                frameRef.current = requestAnimationFrame(tick);
        };
        frameRef.current = requestAnimationFrame(tick);
    };
    return <WorkspaceVideo ref={videoRef} className={editorUi.effectPreview} aria-label={label} controls muted={muted} playsInline preload="metadata" src={apiUrl(url)} onLoadedMetadata={event => { if (range)
        event.currentTarget.currentTime = range.start; }} onPlay={event => {
            pauseOthers(event.currentTarget);
            if (range && (event.currentTarget.currentTime < range.start || event.currentTarget.currentTime >= range.end)) {
                event.currentTarget.currentTime = range.start;
            }
            if (range)
                watchPlayback(event.currentTarget);
        }} onTimeUpdate={event => stopAtEnd(event.currentTarget)} onPause={event => {
            if (frameRef.current !== null)
                cancelAnimationFrame(frameRef.current);
            frameRef.current = null;
            stopAtEnd(event.currentTarget);
        }}/>;
}
export function EditPreviewCard({ preview, busy, compact = false, command, onCancel, onConfirm, kind, chosen = null, onChosen, onSelectionPreview }: {
    preview: CreationTaskEditPreviewResponse;
    busy: BusyAction | null;
    compact?: boolean;
    command: string;
    onCancel: () => void;
    onConfirm: () => void;
    kind: "modal" | "config" | "confirmation";
    chosen?: number[] | null;
    onChosen?: (indices: number[]) => void;
    onSelectionPreview?: (indices: number[]) => void;
}) {
    const ui = editorUi;
    const previewRef = useRef<HTMLDivElement>(null);
    const steps = preview.proposal.operation.operation_type === "EditCompoundTimeline" && preview.proposal.operation.payload.selection_enabled
        ? preview.proposal.operation.payload.steps as {
            index: number;
            command: string;
            enabled: boolean;
        }[] : [];
    const originalSelection = steps.filter(step => step.enabled).map(step => step.index);
    const selectedSteps = chosen ?? originalSelection;
    const commandChanged = steps.length > 0 && command.trim() !== preview.intent.raw_command.trim();
    const selectionChanged = selectedSteps.length !== originalSelection.length || selectedSteps.some(index => !originalSelection.includes(index));
    return (<HomeCardDialog kind={kind} title="修改提案" onClose={onCancel} footer={<div className={ui.previewActions}>
            <button data-card-cancel className={compact ? editorUi.button : styles.secondaryButton} disabled={busy !== null} onClick={onCancel} type="button">{kind === "modal" ? "取消" : "返回详情"}</button>
            {selectionChanged ? <button className={compact ? editorUi.primary : "modern-primary-button"} type="button" disabled={busy !== null || commandChanged || !selectedSteps.length} onClick={() => onSelectionPreview?.(selectedSteps)}>重看所选</button> : null}
            <button className={(selectionChanged || commandChanged) ? (compact ? editorUi.button : styles.secondaryButton) : (compact ? editorUi.primary : "modern-primary-button")} disabled={busy !== null || selectionChanged || commandChanged || preview.proposal.status !== "proposed"} onClick={onConfirm} type="button">
              {busy === "edit-confirm" ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}{preview.proposal.operation.operation_type.startsWith("Delete") || preview.proposal.operation.payload.overlay_action === "delete" || (preview.proposal.operation.operation_type === "EditAudioTimeline" && (preview.proposal.operation.payload.edit as AudioEditInput)?.action === "delete") ? "确认删除" : "应用到草稿"}
            </button>
          </div>}><div ref={previewRef} aria-label="变更预览" aria-live="polite" className={editorUi.dialogPreview}>
          <header><strong>{preview.proposal.summary}</strong><span>{(selectionChanged || commandChanged) ? "重看效果" : "待确认"}</span></header>
          {steps.length ? <fieldset className={editorUi.propertySection}><legend>选择修改</legend>{steps.map(step => <label className={editorUi.actionRow} key={step.index}><input type="checkbox" aria-label={`保留修改${step.index + 1}：${step.command}`} disabled={busy !== null || commandChanged} checked={selectedSteps.includes(step.index)} onChange={event => onChosen?.(event.target.checked ? [...selectedSteps, step.index] : selectedSteps.filter(index => index !== step.index))}/><span>{step.index + 1}. {step.command}</span></label>)}</fieldset> : null}
          {(selectionChanged || commandChanged) ? <p role="status">内容已变，请重看效果。</p> : <div className={ui.previewDiff}>
            <section><small>修改前</small><strong>{preview.proposal.preview.before.label}</strong><span>{preview.proposal.preview.before.detail}</span>{preview.proposal.preview.before.image_url ? <a href={apiUrl(preview.proposal.preview.before.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改前字幕样式"><NextImage alt="修改前字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.before.image_url)}/>查看原尺寸</a> : null}{preview.proposal.preview.before.video_url ? <PreviewVideo url={preview.proposal.preview.before.video_url} label="修改前画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { previewRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current)
            media.pause(); }); }}/> : null}{preview.proposal.preview.before.audio_url ? <WorkspaceAudio aria-label="修改前音频" controls onPlay={event => { previewRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget)
            audio.pause(); }); }} src={apiUrl(preview.proposal.preview.before.audio_url)}/> : null}</section>
            <section><small>修改后</small><strong>{preview.proposal.preview.after.label}</strong><span>{preview.proposal.preview.after.detail}</span>{preview.proposal.preview.after.image_url ? <a href={apiUrl(preview.proposal.preview.after.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改后字幕样式"><NextImage alt="修改后字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.after.image_url)}/>查看原尺寸</a> : null}{preview.proposal.preview.after.video_url ? <PreviewVideo url={preview.proposal.preview.after.video_url} label="修改后画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { previewRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current)
            media.pause(); }); }}/> : null}{preview.proposal.preview.after.audio_url ? <WorkspaceAudio aria-label="修改后音频" controls onPlay={event => { previewRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget)
            audio.pause(); }); }} src={apiUrl(preview.proposal.preview.after.audio_url)}/> : null}</section>
          </div>}
          {preview.proposal.preview.text_changes?.length ? <details className={editorUi.textChanges} open><summary>字幕变化 · {preview.proposal.preview.text_changes.length}</summary><ol>{preview.proposal.preview.text_changes.map(change => <li key={change.cue_id}><span>修改前：{change.before}</span><span>修改后：{change.after}</span></li>)}</ol></details> : null}
          <div className={ui.previewEffects}>
            <span><strong>需要重做</strong>{preview.proposal.invalidated_refs.map(ref => compact ? ref.endsWith(".mp4") ? "当前成片" : ref.endsWith(".srt") ? "字幕输出" : ref : ref).join("、") || "无"}</span>
            <span><strong>其他影响</strong>{preview.proposal.external_effects.join("；") || "无"}</span>
          </div>
        </div></HomeCardDialog>);
}
