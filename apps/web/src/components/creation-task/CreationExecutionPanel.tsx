"use client";
import { PreviewVideo } from "./EditPreviewCard";
import { ExecutionQueueStatus } from "../ExecutionQueueStatus";
import { creationTaskFailureReason } from "@/lib/creation-task-status";
import { useEffect, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { Button } from "@/components/ui/button";
import type { CreationTaskWorkspace as WorkspaceState, CreationExecutionPreview, CreationExecutionStatus } from "@/lib/types";
import type { SegmentVisualDecision, SegmentVisualDecisionLocator, CreationTaskStoryboardRegenerationReceipt } from "@/lib/types/creation-tasks";
import styles from "../CreationTaskWorkspace.module.css";
import editorUi from "../VideoEditor.module.css";
import { executionStageLabels } from './execution-labels';
import { CreationPlanApiError } from "@/lib/api";
import { getCreationTaskScriptSections } from "@/lib/api/creation-tasks";
import type { CreationTaskScriptSectionWindow } from "@/lib/types/creation-tasks";
import type { SourceMediaSelection } from "@/lib/types/creation-plans";

function ExecutionScriptWindow({ preview, onStale }: {
    preview: CreationExecutionPreview; onStale: (key: string) => void;
}) {
    const [open, setOpen] = useState(false);
    const [cursors, setCursors] = useState([0]);
    const [page, setPage] = useState<CreationTaskScriptSectionWindow | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [retry, setRetry] = useState(0);
    const after = cursors[cursors.length - 1];
    const { task_id: taskId, script_id: scriptId, script_updated_at: version, input_digest: digest } = preview;
    useEffect(() => {
        if (!open || scriptId === null || version === null) return;
        let active = true;
        setPage(null);
        setLoading(true);
        setError(null);
        void getCreationTaskScriptSections(taskId, scriptId, { after_ordinal: after, expected_updated_at: version }).then(value => {
            if (active) setPage(value);
        }).catch(cause => {
            if (!active) return;
            if (cause instanceof CreationPlanApiError && cause.status === 409) {
                onStale(`${taskId}:${digest}`);
                setError("文案版本已变化，旧页已清除；请重新核对文案和制作要求。");
            } else setError(cause instanceof Error ? cause.message : "文案窗口读取失败。");
        }).finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [open, taskId, scriptId, version, digest, after, retry, onStale]);
    return <details className={styles.executionScript} onToggle={event => setOpen(event.currentTarget.open)}>
        <summary>这次使用的文案：{preview.script_title}<span>共 {preview.script_sections_total_count} 段</span></summary>
        {loading ? <p role="status">正在读取已确认文案…</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {page ? <>
            <p>当前显示第 {page.items[0]?.ordinal ?? 0}–{page.items.at(-1)?.ordinal ?? 0} 段，共 {page.total_count} 段。</p>
            {page.items.map(item => <p key={item.section.id}>第 {item.ordinal} 段 · {item.section.title}：{item.section.content}</p>)}
            <Button variant="outline" disabled={loading || cursors.length === 1} onClick={() => setCursors(current => current.slice(0, -1))}>上一页已确认文案</Button>
            <Button variant="outline" disabled={loading || page.next_after_ordinal === null} onClick={() => setCursors(current => [...current, page.next_after_ordinal!])}>下一页已确认文案</Button>
        </> : null}
        {error ? <Button variant="outline" disabled={loading} onClick={() => setRetry(value => value + 1)}>重试读取原版本文案</Button> : null}
    </details>;
}

export function CreationExecutionPanel({ taskStage, taskReason, sourceMedia, voice, availableVoices, preview, execution, busy, visualDecision, visualBusy = false, visualError, visualReadRequired = false, onReadVisualDecision, onAdoptVisualDecision, onCloseVisualDecision, storyboard, onRegenerateStoryboard, onReadStoryboard, compact = false, onStart, onResume, onCancel, onOpenMontage, onRefreshPreview }: {
    sourceMedia?: SourceMediaSelection | null;
    taskReason: string | null;
    taskStage: WorkspaceState["task"]["stage"];
    voice: WorkspaceState["plan"]["selected_voice"];
    availableVoices: WorkspaceState["available_voices"];
    preview: CreationExecutionPreview | null;
    execution: CreationExecutionStatus | null;
    busy: boolean;
    visualDecision?: SegmentVisualDecision | null; visualBusy?: boolean; visualError?: string | null; visualReadRequired?: boolean;
    onReadVisualDecision?: (locator: Pick<SegmentVisualDecisionLocator, 'job_id' | 'section_id'>, more?: boolean) => void;
    onAdoptVisualDecision?: (ids: string[]) => void; onCloseVisualDecision?: () => void;
    storyboard?: { busy: boolean; canSubmit: boolean; refreshPending: boolean; requestId: string | null;
        receipt: CreationTaskStoryboardRegenerationReceipt | null; error: string | null };
    onRegenerateStoryboard?: () => void;
    onReadStoryboard?: () => void;
    compact?: boolean;
    onStart: () => void;
    onResume: () => void;
    onCancel: () => void;
    onOpenMontage: () => void;
    onRefreshPreview?: () => void;
}) {
    const [stalePreview, setStalePreview] = useState<string | null>(null);
    const previewKey = preview ? `${preview.task_id}:${preview.input_digest}` : null;
    const previewStale = stalePreview !== null && stalePreview === previewKey;
    const storyboardControls = !sourceMedia && storyboard && onRegenerateStoryboard ? <section aria-label="重新安排画面" className={styles.workSection}>
      <Button variant="outline" disabled={busy || visualBusy || !storyboard.canSubmit} onClick={onRegenerateStoryboard} type="button">重新安排画面，保留配音</Button>
      <p>重新编排当前文案的镜头，保留原配音、字幕与图层；不会自动生成成片。</p>
      {storyboard.requestId ? <>
        <p role="status">{storyboard.receipt?.status === "succeeded" ? "画面编排已更新，请检查候选后重新输出。"
          : storyboard.receipt?.failure_reason ?? (storyboard.receipt ? "画面重排已提交，请等待完成。"
            : storyboard.busy ? "正在提交或检查画面重排结果。" : "上次操作结果尚未核实，请先检查结果；不会重复提交。")}</p>
        <Button variant="outline" disabled={busy || storyboard.busy} onClick={onReadStoryboard} type="button">检查上次操作结果</Button>
      </> : null}
      {storyboard.refreshPending ? <p role="status">上次操作结果已取得，但当前任务尚未刷新；请先检查上次操作结果。</p> : null}
      {storyboard.error ? <p role="alert">{storyboard.error}</p> : null}
    </section> : null;
    const [detailsOpen, setDetailsOpen] = useState(false);
    const visualMedia = useRef<HTMLDivElement>(null);
    const [selected, setSelected] = useState<string[]>([]);
    useEffect(() => setSelected([]), [visualDecision?.job_id, visualDecision?.section_id, visualDecision?.decision_digest]);
    const locators = execution?.visual_decisions;
    const required = visualDecision?.required_confirmation_ids ?? [];
    const canAdopt = visualDecision?.status === 'waiting_for_user' && !visualDecision.adopted
        && required.length > 0 && required.every(id => selected.includes(id)) && !visualBusy && !busy && !visualReadRequired;
    const visualControls = <>
      {locators && locators.items.length > 0 && <section className={styles.visualDecisionNotice} aria-label="段落画面待确认">
        <strong>{locators.items.every(item => item.adopted) ? "画面采用已保存，原作业待继续" : "配音已保留，画面待确认"}</strong><p>明确确认原候选后继续原作业，不会重新生成这段配音。</p>
        {locators.items.map(item => <Button key={`${item.job_id}:${item.section_id}`} variant="outline"
          disabled={visualBusy || busy} onClick={() => onReadVisualDecision?.(item)}>{item.section_title} · {item.adopted ? "查看已保存采用" : "查看画面"}</Button>)}
        {locators.truncated && <p>当前显示 {locators.items.length} / {locators.total_count} 项等待决定；处理后继续回读。</p>}
      </section>}
      {visualError && !visualDecision && <p role="alert">{visualError}</p>}
      {visualDecision && <HomeCardDialog kind="config" title={visualDecision.status === "planning_conflict" ? "画面安排暂时冲突" : visualDecision.decision_digest === null ? "原画面准备记录" : "确认段落画面"} onClose={() => { if (!visualBusy) onCloseVisualDecision?.(); }}
        footer={<><Button variant="ghost" disabled={visualBusy} onClick={onCloseVisualDecision}>关闭</Button>
          {visualDecision.decision_digest !== null && <Button disabled={!canAdopt} onClick={() => onAdoptVisualDecision?.(selected)}>确认画面并继续原作业</Button>}</>}>
        {visualDecision.decision_digest === null ? <>
          <p role={visualDecision.adopted ? "status" : "alert"}>{visualDecision.reason}</p>
          <p>{visualDecision.audio.persisted ? "原配音已保留，不会重新生成已就绪配音。" : "原配音保存状态尚未核实，不会自动重新配音。"}</p>
          <p>{visualDecision.adopted ? "这是已恢复的原请求记录，无需再次采用画面。请沿原作业查看执行状态。" : "当前没有可采用的画面候选，请先处理以上冲突。"}</p>
          <p>{visualDecision.recovery.detail}</p>
        </> : <>
        <p>原配音已保存{visualDecision.usage.settled ? '，用量已对账' : '，用量仍待核对'}；本次只确认画面，不重新配音。</p>
        {visualDecision.adopted && <p role="status">画面采用已保存；沿原作业查看排队与完成状态。</p>}
        {visualDecision.adopted && visualDecision.status !== 'stale' && !visualDecision.device_request_id &&
          <Button disabled={visualBusy || busy || visualReadRequired} onClick={() => onAdoptVisualDecision?.(visualDecision.confirmed_clip_ids)}>继续已采用的原作业</Button>}
        {visualDecision.status === 'stale' && <p role="alert">原候选或权限已变化，不能采用；请核对原作业，不自动重试。</p>}
        <div className={styles.visualCandidates} ref={visualMedia}>{visualDecision.candidate_clips.map((clip, index) => <article key={clip.id}>
          <PreviewVideo url={clip.preview_url} label={`画面 ${index + 1} 预览`} muted
            pauseOthers={current => visualMedia.current?.querySelectorAll<HTMLMediaElement>('video,audio').forEach(media => { if (media !== current) media.pause(); })} />
          <p>{clip.label}</p><small>源片段 {clip.source_start_seconds.toFixed(1)}–{clip.source_end_seconds.toFixed(1)} 秒；
            段内 {clip.timeline_start_seconds.toFixed(1)}–{clip.timeline_end_seconds.toFixed(1)} 秒</small>
          {required.includes(clip.id) ? <label><input type="checkbox" checked={selected.includes(clip.id)}
            disabled={visualBusy || visualDecision.adopted || visualDecision.status !== 'waiting_for_user'}
            onChange={event => setSelected(ids => event.target.checked ? [...ids, clip.id] : ids.filter(id => id !== clip.id))} />明确采用画面 {index + 1}</label> : <p>原画面已确认</p>}
        </article>)}</div>
        <p>已展示 {visualDecision.candidate_clips.length} / {visualDecision.candidate_total_count} 个候选；已明确确认 {selected.length} / {required.length} 个待决定画面。</p>
        {visualDecision.next_offset !== null && <Button variant="outline" disabled={visualBusy} onClick={() => onReadVisualDecision?.(visualDecision, true)}>查看更多原候选</Button>}
        </>}
        {visualError && <p role="alert">{visualError}</p>}
        <Button variant="ghost" disabled={visualBusy} onClick={() => onReadVisualDecision?.(visualDecision)}>回查原决定</Button>
      </HomeCardDialog>}
    </>;
    const stageLabels: Record<string, string> = { ...executionStageLabels, segment_video: "段落画面", segment_revision: "段落修改" };
    const selectedVoice = voice && preview?.requirements.selected_voice === voice.profile_id
        ? availableVoices.find(item => item.profile_id === voice.profile_id && item.version === voice.version) : null;
    const voiceLabel = selectedVoice?.person_display_name || (preview?.requirements.selected_voice ? "已选择声音参考" : "尚未选择");
    const job = execution?.job;
    const hasInitialExecutionControls = !job || job.kind === "creation_task_execution";
    const failureReason = job && (job.status === "failed" || job.status === "interrupted" || job.status === "cancelled")
        ? job.failure_reason : null;
    const waitingForUser = job?.status === "queued" && job.failure_reason === "waiting_for_user";
    const awaitingMontageConfirmation = hasInitialExecutionControls && waitingForUser && execution?.current_stage === "montage_plan" && Boolean(execution.waiting_reason);
    const resumeWaiting = hasInitialExecutionControls && waitingForUser && !execution?.waiting_reason && preview?.requirements.can_continue === true;
    const needsOutput = hasInitialExecutionControls && job?.status === "succeeded" && !execution?.video_ready;
    const active = job?.status === "queued" || job?.status === "running" || job?.status === "interrupted";
    const resumable = hasInitialExecutionControls && (resumeWaiting || needsOutput || job?.status === "failed" || job?.status === "cancelled" || job?.status === "interrupted");
    const statusText = locators?.items.some(item => !item.adopted) ? "等待画面确认" : locators?.items.some(item => item.adopted) ? "采用已保存，待继续原作业" : job?.status === "queued" ? "已排队" : job?.status === "running" ? "后台制作中"
        : job?.status === "failed" ? "制作停在失败步骤" : job?.status === "cancelled" ? "已在安全边界停止"
            : job?.status === "interrupted" ? "应用中断，等待继续" : job?.status === "succeeded" ? execution?.video_ready ? "成片已生成，等待检查" : "成片产物需要检查" : "等待开始";
    const content = <section aria-label="连续制作" className={styles.workSection}>
    <div className={styles.operationHeading}><h2>{job ? "连续制作" : "制作要求"}</h2><span className={styles.stateBadge}>{statusText}</span></div>
    {needsOutput ? <p>{sourceMedia ? "沿用当前原片与剪辑，重新输出当前时间线。" : "沿用有效配音和故事板，重新输出当前时间线。"}</p> : null}
    {(!job || resumable || waitingForUser) && preview ? <>
      {sourceMedia ? <p>沿用原片 {sourceMedia.source_start_seconds}–{sourceMedia.source_end_seconds} 秒的当前剪辑。</p> : <ExecutionScriptWindow key={previewKey} preview={preview} onStale={setStalePreview}/>}
      {previewStale ? <p role="status">制作前览已过期，请重新核对文案和制作要求。{onRefreshPreview ? <Button variant="outline" disabled={busy} onClick={onRefreshPreview}>重新核对文案和制作要求</Button> : null}</p> : null}
      <section className={styles.executionRequirements} aria-label="这次制作要求">
        <p>画幅 {preview.requirements.aspect_ratio} · 目标时长 {preview.requirements.duration_target_seconds} 秒 · 露脸目标 {preview.requirements.presenter_share_target}%</p>
        {sourceMedia ? <p>{sourceMedia.preserve_audio ? "保留原声" : "不使用原声音轨"}</p> : <p>口型：{preview.requirements.lipsync_quality === "high" ? "高精度" : "较快档"} · {preview.narration_source?.source_kind === "recorded"
                ? `配音来源：本人录音 · 已采用 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
                : preview.narration_source?.source_kind === "generated"
                    ? `配音来源：已生成 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
                    : `声音：${voiceLabel}`}</p>}
        {preview.requirements.selected_voice ? <details><summary>声音引用诊断</summary><p>声音身份：{preview.requirements.selected_voice}{voice?.profile_id === preview.requirements.selected_voice ? ` · 固定版本：${voice.version}` : ""}</p></details> : null}
        {Object.entries(preview.requirements.template_settings).length ? <details><summary>查看模板中的具体设置</summary><dl>{Object.entries(preview.requirements.template_settings).map(([name, value]) => <div key={name}><dt>{({ fit_mode: "画面适配", color_style: "调色", subtitle_style: "字幕样式" } as Record<string, string>)[name] ?? name}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details> : null}
      </section>
      {!preview.requirements.can_continue && preview.requirements.continuation_blocker ? <p role="status">{preview.requirements.continuation_blocker}</p> : null}
      {!job ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || previewStale || !preview.requirements.can_continue} onClick={onStart} type="button">{preview.requirements.can_continue ? sourceMedia ? "生成当前剪辑成片" : "确认以上文案和要求，开始制作" : "暂不能开始制作"}</button></div> : null}
    </> : null}
    {job ? <div>
      {execution?.waiting_reason ? <p role="status">{creationTaskFailureReason(execution.waiting_reason, taskStage)}</p> : null}
      {!taskReason && failureReason ? <p>{creationTaskFailureReason(failureReason, taskStage)}</p> : null}
      <details className={styles.traceDetails}><summary>步骤与后台记录</summary>
        <ExecutionQueueStatus status={job.status} currentStage={execution?.current_stage ?? null} waitingReason={execution?.waiting_reason ?? null} statusUpdatedAt={execution?.status_updated_at ?? null}/>
        {failureReason ? <p>原始原因：{failureReason}</p> : null}{job.repair_action ? <p>后台处理原文：{job.repair_action}</p> : null}
        <ol>{execution?.stages.map((event, index) => <li key={`${event.at}-${index}`}>{stageLabels[event.stage] ?? event.stage}：{event.status === "running" ? "正在处理" : event.status === "completed" ? "已完成" : event.status === "reused" ? "沿用有效结果" : event.status === "failed" ? "失败" : "待完成"}{event.reason ? ` · ${event.reason}` : ""}</li>)}</ol>
      </details>
      {execution?.video_ready ? <p>成片可检查；{execution.cover_status === "ready" ? "封面已准备好。" : execution.cover_message}</p> : null}
      {needsOutput ? <><p>原成片不再对应当前时间线，请先核对以上文案和制作要求，再确认重新输出。</p><div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || visualBusy} onClick={onOpenMontage} type="button">查看剪辑与已有产物</button></div></> : null}
      {hasInitialExecutionControls && active && job.status !== "interrupted" ? <button className={styles.secondaryButton} disabled={busy || visualBusy} onClick={onCancel} type="button">停止制作</button> : null}
      {awaitingMontageConfirmation ? <Button variant="outline" disabled={busy || visualBusy} onClick={onOpenMontage} type="button">审核镜头</Button> : null}
      {resumable ? <Button className="modern-primary-button" disabled={busy || previewStale || !preview?.requirements.can_continue} onClick={onResume} type="button">{sourceMedia ? needsOutput ? "重新输出当前剪辑" : "继续原片制作" : needsOutput ? "确认以上文案和要求，仅重新输出当前剪辑" : "确认以上文案和要求，继续制作"}</Button> : null}
    </div> : null}
  </section>;
    if (!compact)
        return <>{content}{storyboardControls}{visualControls}</>;
    return <>
    <section aria-label="制作状态" className={editorUi.productionSummary}>
      <div>{job && job.status !== "succeeded" ? <strong>连续制作 · {statusText}{execution?.current_stage ? ` · ${stageLabels[execution.current_stage]}` : ""}</strong> : !job ? <strong>制作要求</strong> : null}
        {execution?.waiting_reason ? <p>{creationTaskFailureReason(execution.waiting_reason, taskStage)}</p> : null}
        {failureReason ? <p>{creationTaskFailureReason(failureReason, taskStage)}</p> : null}
        {needsOutput ? <p>原成片不再对应当前时间线，请核对文案和要求后重新输出。</p> : null}
      </div>
      <button className={editorUi.button} type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>{job ? "查看制作详情" : "核对制作要求"}</button>
      {awaitingMontageConfirmation && !detailsOpen ? <Button variant="outline" className={editorUi.button} disabled={busy || visualBusy} onClick={onOpenMontage} type="button">审核镜头</Button> : null}
      {resumeWaiting && !detailsOpen ? <Button className={editorUi.button} disabled={busy || visualBusy || previewStale} onClick={onResume} type="button">继续制作</Button> : null}
      {hasInitialExecutionControls && active && job?.status !== "interrupted" && !detailsOpen ? <button className={editorUi.button} type="button" disabled={busy || visualBusy} onClick={onCancel}>停止制作</button> : null}
    </section>
    {storyboard?.requestId && !storyboard.receipt ? <span role="status">原画面安排结果待核对</span> : null}
    {storyboard?.error ? <p role="alert">{storyboard.error}</p> : null}
    {visualControls}
    <HomeCardDialog kind="detail" title={sourceMedia ? "原片制作要求" : "制作文案与要求"} open={detailsOpen} onClose={() => setDetailsOpen(false)} footer={<button type="button" onClick={() => setDetailsOpen(false)}>收起制作详情</button>}>
      <div className={styles.productionDetails}>{content}{storyboardControls}</div>
    </HomeCardDialog>
  </>;
}
