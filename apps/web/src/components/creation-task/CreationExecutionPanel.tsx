"use client";
import { ExecutionQueueStatus } from "../ExecutionQueueStatus";
import { creationTaskFailureReason } from "@/lib/creation-task-status";
import { useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { Button } from "@/components/ui/button";
import type { CreationTaskWorkspace as WorkspaceState, CreationExecutionPreview, CreationExecutionStatus } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import editorUi from "../VideoEditor.module.css";
import { executionStageLabels } from './execution-labels';
export function CreationExecutionPanel({ taskStage, taskReason, voice, availableVoices, preview, execution, busy, compact = false, onStart, onResume, onCancel, onOpenMontage }: {
    taskReason: string | null;
    taskStage: WorkspaceState["task"]["stage"];
    voice: WorkspaceState["plan"]["selected_voice"];
    availableVoices: WorkspaceState["available_voices"];
    preview: CreationExecutionPreview | null;
    execution: CreationExecutionStatus | null;
    busy: boolean;
    compact?: boolean;
    onStart: () => void;
    onResume: () => void;
    onCancel: () => void;
    onOpenMontage: () => void;
}) {
    const [detailsOpen, setDetailsOpen] = useState(false);
    const selectedVoice = voice && preview?.requirements.selected_voice === voice.profile_id
        ? availableVoices.find(item => item.profile_id === voice.profile_id && item.version === voice.version) : null;
    const voiceLabel = selectedVoice?.person_display_name || (preview?.requirements.selected_voice ? "已选择声音参考" : "尚未选择");
    const job = execution?.job;
    const failureReason = job && (job.status === "failed" || job.status === "interrupted" || job.status === "cancelled")
        ? job.failure_reason : null;
    const waitingForUser = job?.status === "queued" && job.failure_reason === "waiting_for_user";
    const awaitingMontageConfirmation = waitingForUser && execution?.current_stage === "montage_plan" && Boolean(execution.waiting_reason);
    const resumeWaiting = waitingForUser && !execution?.waiting_reason && preview?.requirements.can_continue === true;
    const needsOutput = job?.status === "succeeded" && !execution?.video_ready;
    const active = job?.status === "queued" || job?.status === "running" || job?.status === "interrupted";
    const resumable = resumeWaiting || needsOutput || job?.status === "failed" || job?.status === "cancelled" || job?.status === "interrupted";
    const statusText = job?.status === "queued" ? "已排队" : job?.status === "running" ? "后台制作中"
        : job?.status === "failed" ? "制作停在失败步骤" : job?.status === "cancelled" ? "已在安全边界停止"
            : job?.status === "interrupted" ? "应用中断，等待继续" : job?.status === "succeeded" ? execution?.video_ready ? "成片已生成，等待检查" : "成片产物需要检查" : "等待开始";
    const content = <section aria-label="连续制作" className={styles.workSection}>
    <div className={styles.operationHeading}><h2>{job ? "连续制作" : "制作要求"}</h2><span className={styles.stateBadge}>{statusText}</span></div>
    {needsOutput ? <p>沿用有效配音和故事板，重新输出当前时间线。</p> : null}
    {(!job || resumable || waitingForUser) && preview ? <>
      <details className={styles.executionScript}><summary>这次使用的文案：{preview.script_title}<span>{preview.script_sections.length} 段</span></summary>{preview.script_sections.map((section, index) => <p key={index}>第 {index + 1} 段 · {section.title}：{section.content}</p>)}</details>
      <section className={styles.executionRequirements} aria-label="这次制作要求">
        <p>画幅 {preview.requirements.aspect_ratio} · 目标时长 {preview.requirements.duration_target_seconds} 秒 · 露脸目标 {preview.requirements.presenter_share_target}%</p>
        <p>口型：{preview.requirements.lipsync_quality === "high" ? "高精度" : "较快档"} · {preview.narration_source?.source_kind === "recorded"
                ? `配音来源：本人录音 · 已采用 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
                : preview.narration_source?.source_kind === "generated"
                    ? `配音来源：已生成 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
                    : `声音：${voiceLabel}`}</p>
        {preview.requirements.selected_voice ? <details><summary>声音引用诊断</summary><p>声音身份：{preview.requirements.selected_voice}{voice?.profile_id === preview.requirements.selected_voice ? ` · 固定版本：${voice.version}` : ""}</p></details> : null}
        {Object.entries(preview.requirements.template_settings).length ? <details><summary>查看模板中的具体设置</summary><dl>{Object.entries(preview.requirements.template_settings).map(([name, value]) => <div key={name}><dt>{({ fit_mode: "画面适配", color_style: "调色", subtitle_style: "字幕样式" } as Record<string, string>)[name] ?? name}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details> : null}
      </section>
      {!preview.requirements.can_continue && preview.requirements.continuation_blocker ? <p role="status">{preview.requirements.continuation_blocker}</p> : null}
      {!job ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !preview.requirements.can_continue} onClick={onStart} type="button">{preview.requirements.can_continue ? "确认以上文案和要求，开始制作" : "暂不能开始制作"}</button></div> : null}
    </> : null}
    {job ? <div>
      {execution?.waiting_reason ? <p role="status">{creationTaskFailureReason(execution.waiting_reason, taskStage)}</p> : null}
      {!taskReason && failureReason ? <p>{creationTaskFailureReason(failureReason, taskStage)}</p> : null}
      <details className={styles.traceDetails}><summary>步骤与后台记录</summary>
        <ExecutionQueueStatus status={job.status} currentStage={execution?.current_stage ?? null} waitingReason={execution?.waiting_reason ?? null} statusUpdatedAt={execution?.status_updated_at ?? null}/>
        {failureReason ? <p>原始原因：{failureReason}</p> : null}{job.repair_action ? <p>后台处理原文：{job.repair_action}</p> : null}
        <ol>{execution?.stages.map((event, index) => <li key={`${event.at}-${index}`}>{executionStageLabels[event.stage] ?? event.stage}：{event.status === "running" ? "正在处理" : event.status === "completed" ? "已完成" : event.status === "reused" ? "沿用有效结果" : event.status === "failed" ? "失败" : "待完成"}{event.reason ? ` · ${event.reason}` : ""}</li>)}</ol>
      </details>
      {execution?.video_ready ? <p>成片可检查；{execution.cover_status === "ready" ? "封面已准备好。" : execution.cover_message}</p> : null}
      {needsOutput ? <><p>原成片不再对应当前时间线，请先核对以上文案和制作要求，再确认重新输出。</p><div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy} onClick={onOpenMontage} type="button">查看剪辑与已有产物</button></div></> : null}
      {active && job.status !== "interrupted" ? <button className={styles.secondaryButton} disabled={busy} onClick={onCancel} type="button">停止制作</button> : null}
      {awaitingMontageConfirmation ? <Button variant="outline" disabled={busy} onClick={onOpenMontage} type="button">审核镜头</Button> : null}
      {resumable ? <Button className="modern-primary-button" disabled={busy || !preview?.requirements.can_continue} onClick={onResume} type="button">{needsOutput ? "确认以上文案和要求，仅重新输出当前剪辑" : "确认以上文案和要求，继续制作"}</Button> : null}
    </div> : null}
  </section>;
    if (!compact)
        return content;
    return <>
    <section aria-label="制作状态" className={editorUi.productionSummary}>
      <div>{job && job.status !== "succeeded" ? <strong>连续制作 · {statusText}{execution?.current_stage ? ` · ${executionStageLabels[execution.current_stage]}` : ""}</strong> : !job ? <strong>制作要求</strong> : null}
        {execution?.waiting_reason ? <p>{creationTaskFailureReason(execution.waiting_reason, taskStage)}</p> : null}
        {failureReason ? <p>{creationTaskFailureReason(failureReason, taskStage)}</p> : null}
        {needsOutput ? <p>原成片不再对应当前时间线，请核对文案和要求后重新输出。</p> : null}
      </div>
      <button className={editorUi.button} type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>{job ? "查看制作详情" : "核对制作要求"}</button>
      {awaitingMontageConfirmation && !detailsOpen ? <Button variant="outline" className={editorUi.button} disabled={busy} onClick={onOpenMontage} type="button">审核镜头</Button> : null}
      {resumeWaiting && !detailsOpen ? <Button className={editorUi.button} disabled={busy} onClick={onResume} type="button">继续制作</Button> : null}
      {active && job?.status !== "interrupted" && !detailsOpen ? <button className={editorUi.button} type="button" disabled={busy} onClick={onCancel}>停止制作</button> : null}
    </section>
    <HomeCardDialog kind="detail" title="制作文案与要求" open={detailsOpen} onClose={() => setDetailsOpen(false)} footer={<button type="button" onClick={() => setDetailsOpen(false)}>收起制作详情</button>}>
      <div className={styles.productionDetails}>{content}</div>
    </HomeCardDialog>
  </>;
}
