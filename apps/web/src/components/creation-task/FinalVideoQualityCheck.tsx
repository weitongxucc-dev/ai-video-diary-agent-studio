"use client";
import { useEffect, useRef, useState } from "react";
import { checkCreationTaskQuality, getCreationTaskQualityCheck, CreationPlanApiError } from "@/lib/api";
import { continueCreationTaskQuality } from "@/lib/api/creation-tasks";
import type { CreationTaskQualityCheck } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { errorDetails } from './request-errors';
import { HomeCardDialog } from "../HomeCardDialog";
import { Button } from "../ui/button";
const findingLabels: Record<CreationTaskQualityCheck["findings"][number]["kind"], string> = {
    black_frame: "黑帧", truncation: "尾部截断", clipping: "音频削波",
    timestamp_offset: "起点时间戳错位", delivery_duration: "交付时长偏差",
};
type Continuation = NonNullable<CreationTaskQualityCheck["continuation"]>;
type PendingContinuation = { check_id: string; job_id: string; failed_request_id: string };
const phaseLabels: Record<string, string> = { metadata: "媒体信息", black_frames: "黑帧检查", truncation: "尾部截断检查", clipping: "音频削波检查", finalize: "结果汇总" };
const continuationReasons: Record<NonNullable<Continuation["reason_code"]>, string> = {
    actor_unavailable: "当前执行身份尚未核验", runtime_unavailable: "后台执行环境不可用",
    original_actor_required: "仅原执行身份可以续检", source_changed: "成片或时间线已变化，不能沿原阶段续检",
    policy_unavailable: "原资源策略不可用", algorithm_changed: "检查算法已变化，不能沿原阶段续检",
    request_in_flight: "原阶段仍在排队或执行", failed_request_unavailable: "原失败执行请求不可用，当前不能续检",
};
export function FinalVideoQualityCheck({ taskId, versionId, outputUrl, onLocate, compact = false }: {
    compact?: boolean;
    taskId: string;
    versionId: string | null;
    outputUrl: string | null;
    onLocate: (finding: CreationTaskQualityCheck["findings"][number]) => void;
}) {
    const [detailsOpen, setDetailsOpen] = useState(false);
    const [result, setResult] = useState<CreationTaskQualityCheck | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const [continuing, setContinuing] = useState(false);
    const [pendingContinuation, setPendingContinuation] = useState<PendingContinuation | null>(null);
    const [continuedFrom, setContinuedFrom] = useState<string | null>(null);
    const [continuationNotice, setContinuationNotice] = useState<string | null>(null);
    const [continuationError, setContinuationError] = useState<string | null>(null);
    const revision = useRef(0);
    useEffect(() => {
        const current = ++revision.current;
        setResult(null);
        setError(null);
        setSubmitting(false);
        setContinuing(false);
        setPendingContinuation(null);
        setContinuedFrom(null);
        setContinuationNotice(null);
        setContinuationError(null);
        void getCreationTaskQualityCheck(taskId).then(value => {
            if (current === revision.current)
                setResult(value);
        }).catch(cause => { if (current === revision.current)
            setError(errorDetails(cause).message); });
        return () => { revision.current += 1; };
    }, [taskId, versionId, outputUrl]);
    useEffect(() => {
        if (result?.check_state !== "checking" || result.stale)
            return;
        const current = revision.current;
        const timer = window.setTimeout(() => {
            void getCreationTaskQualityCheck(taskId).then(value => {
                if (current === revision.current)
                    setResult(value);
            }).catch(cause => { if (current === revision.current)
                setError(errorDetails(cause).message); });
        }, 1000);
        return () => window.clearTimeout(timer);
    }, [result, taskId]);
    async function check() {
        if (pendingContinuation || continuing) return;
        const current = revision.current;
        setSubmitting(true);
        setError(null);
        setContinuedFrom(null);
        setContinuationNotice(null);
        setContinuationError(null);
        try {
            const next = await checkCreationTaskQuality(taskId);
            if (current === revision.current)
                setResult(next);
        }
        catch (cause) {
            if (current === revision.current)
                setError(errorDetails(cause).message);
        }
        finally {
            if (current === revision.current)
                setSubmitting(false);
        }
    }
    async function readOriginalCheck() {
        const currentRevision = revision.current;
        setContinuing(true);
        setContinuationError(null);
        try {
            const next = await getCreationTaskQualityCheck(taskId);
            if (currentRevision !== revision.current) return;
            setResult(next);
            setError(null);
            if (pendingContinuation && next.check_id === pendingContinuation.check_id &&
                (next.check_state === "checking" || next.check_state === "done" || next.continuation?.reason_code === "request_in_flight" ||
                 (next.continuation?.failed_request_id && next.continuation.failed_request_id !== pendingContinuation.failed_request_id))) {
                setContinuedFrom(pendingContinuation.failed_request_id);
                setPendingContinuation(null);
                setContinuationNotice("原检查最新状态已读回，进度以该检查记录为准。");
            }
        } catch (cause) { if (currentRevision === revision.current) setContinuationError(errorDetails(cause).message); }
        finally { if (currentRevision === revision.current) setContinuing(false); }
    }
    async function continuePhase() {
        const source = current?.continuation;
        if (!source?.can_request || !source.failed_request_id || !current?.check_id) return;
        const currentRevision = revision.current;
        const original = { check_id: current.check_id, job_id: source.job_id, failed_request_id: source.failed_request_id };
        setContinuing(true);
        setContinuationError(null);
        setContinuationNotice(null);
        setPendingContinuation(original);
        try {
            const receipt = await continueCreationTaskQuality(taskId, { job_id: original.job_id, failed_request_id: original.failed_request_id });
            if (currentRevision !== revision.current) return;
            if (receipt.check_id !== original.check_id || receipt.job_id !== original.job_id) throw new Error("续检回执与原检查不符，结果尚待核对。");
            setPendingContinuation(null);
            setContinuedFrom(original.failed_request_id);
            setContinuationNotice(`续检回执已读回：${receipt.request_id}。具体进度以原检查当前状态为准。`);
            try {
                const next = await getCreationTaskQualityCheck(taskId);
                if (currentRevision === revision.current) setResult(next);
            } catch (cause) { if (currentRevision === revision.current) setContinuationError(`续检回执已读回，但检查状态未能刷新：${errorDetails(cause).message}`); }
        } catch (cause) {
            if (currentRevision !== revision.current) return;
            if (cause instanceof CreationPlanApiError && [403, 404, 409, 422].includes(cause.status)) setPendingContinuation(null);
            setContinuationError(errorDetails(cause).message);
        } finally { if (currentRevision === revision.current) setContinuing(false); }
    }
    const stale = result && (result.stale || result.timeline_version_id !== versionId
        || (result.check_state === "done" && !outputUrl));
    const current = !error && !stale ? result : null;
    const label = error ? "检查结果读取失败" : stale ? "旧检查已失效，请检查当前成片"
        : !current ? "正在读取检查结果" : current.check_state === "checking" ? "成片检查中"
            : current.check_state === "failed" ? "成片检查失败"
                : current.result_status === "issues_found" ? "成片发现问题"
                    : current.result_status === "unavailable" ? "部分检测不可用"
                        : current.result_status === "clean" ? "未发现确定性媒体问题" : "当前成片尚未检查";
    const content = <section className={styles.qualityCheck} aria-label="成片质量检查">
    <div className={styles.qualityHeading}><strong role="status" aria-label="成片质量检查状态">{label}</strong>
      <button className={styles.secondaryButton} type="button" disabled={!outputUrl || submitting || continuing || pendingContinuation !== null || current?.check_state === "checking"} onClick={() => void check()}>重新检查成片</button></div>
    <p>检查黑帧、尾部截断、音频削波、起点时间戳错位和交付时长偏差；内容质量仍需人工确认。</p>
    {error || current?.error ? <p role="alert">{error ?? current?.error}</p> : null}
    {current?.continuation ? <section aria-label="原失败阶段续检">
      <h3>原失败阶段续检</h3>
      <p>失败阶段：{phaseLabels[current.continuation.phase] ?? current.continuation.phase}。已完成阶段：{current.continuation.completed_phases.map(phase => phaseLabels[phase] ?? phase).join("、") || "无"}。</p>
      <p>续检保留原检查、原 Job 和已完成阶段；重新检查会新建检查。</p>
      <p>原 Job：{current.continuation.job_id}</p>
      <p>{current.continuation.can_request ? "可请求续检，不代表已获设备准入。提交时仍会核验资源释放、原策略与成片来源。" : current.continuation.reason_code ? continuationReasons[current.continuation.reason_code] : "当前不能请求原阶段续检。"}</p>
      <Button variant="outline" disabled={submitting || continuing || pendingContinuation !== null || !current.continuation.can_request || !current.continuation.failed_request_id || !current.check_id || continuedFrom === current.continuation.failed_request_id} onClick={() => void continuePhase()}>续检原失败阶段</Button>
    </section> : null}
    {pendingContinuation ? <p role="status">原阶段续检结果尚待核对，保留原 Job 和失败请求，不新建检查或自动重提。</p> : null}
    {continuationNotice ? <p role="status">{continuationNotice}</p> : null}
    {continuationError ? <p role="alert">{continuationError}</p> : null}
    {current?.continuation || pendingContinuation || continuedFrom ? <Button variant="outline" disabled={continuing || submitting} onClick={() => void readOriginalCheck()}>查询原检查状态</Button> : null}
    {current?.checked_at ? <small>检查时间：{new Date(current.checked_at).toLocaleString()}</small> : null}
    {current?.check_state === "done" && current.clipping_summary ? <div aria-label="音频削平摘要">
      <p>音频削平共 {current.clipping_summary.event_count} 段，累计 {current.clipping_summary.total_clipped_seconds} 秒。</p>
      {current.clipping_summary.findings_truncated ? <p>
        削平问题明细已限流至 {current.clipping_summary.findings_event_limit} 条，另有 {current.clipping_summary.findings_truncated_count} 段未逐条列出；总量包含这些事件。
      </p> : null}
      {current.clipping_summary.events_truncated ? <p>
        削平区间摘要仅保留 {current.clipping_summary.event_list_limit} 条，另有 {current.clipping_summary.truncated_event_count} 段未在区间摘要中列举；总量仍完整。
      </p> : null}
    </div> : null}
    {current?.check_state === "done" && current.black_frame_summary ? <div aria-label="黑帧摘要">
      <p>黑帧共 {current.black_frame_summary.event_count} 段，累计 {current.black_frame_summary.total_black_seconds} 秒。</p>
      {current.black_frame_summary.findings_truncated ? <p>
        黑帧问题明细已限流至 {current.black_frame_summary.findings_event_limit} 条，另有 {current.black_frame_summary.findings_truncated_count} 段未逐条列出；总量包含这些事件。
      </p> : null}
      {current.black_frame_summary.events_truncated ? <p>
        黑帧区间摘要仅保留 {current.black_frame_summary.event_list_limit} 条，另有 {current.black_frame_summary.truncated_event_count} 段未在区间摘要中列举；总量仍完整。
      </p> : null}
    </div> : null}
    {current?.findings.map((finding, index) => <article key={`${finding.kind}:${index}`}>
      <button type="button" onClick={() => { setDetailsOpen(false); onLocate(finding); }}>
        定位 {finding.start_seconds.toFixed(2)}–{finding.end_seconds.toFixed(2)} 秒
      </button><strong>{findingLabels[finding.kind]}</strong><span>{finding.detail}</span>
    </article>)}
    {current?.unavailable.map((item, index) => <p key={`${item.kind}:${index}`}>
      检测不可用：{item.reason}</p>)}
  </section>;
  return compact ? <section className={styles.compactQuality} aria-label="成片检查摘要">
    <Button variant="ghost" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={()=>setDetailsOpen(true)}>成片检查</Button>
    <span role={error || current?.check_state === "failed" ? "alert" : "status"}>{label}</span>
    <HomeCardDialog kind="detail" title="成片质量检查" open={detailsOpen} onClose={()=>setDetailsOpen(false)}>{content}</HomeCardDialog>
  </section> : content;
}
