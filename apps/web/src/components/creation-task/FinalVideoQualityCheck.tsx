"use client";
import { useEffect, useRef, useState } from "react";
import { checkCreationTaskQuality, getCreationTaskQualityCheck } from "@/lib/api";
import type { CreationTaskQualityCheck } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { errorDetails } from './request-errors';
export function FinalVideoQualityCheck({ taskId, versionId, outputUrl, onLocate }: {
    taskId: string;
    versionId: string | null;
    outputUrl: string | null;
    onLocate: (finding: CreationTaskQualityCheck["findings"][number]) => void;
}) {
    const [result, setResult] = useState<CreationTaskQualityCheck | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const revision = useRef(0);
    useEffect(() => {
        const current = ++revision.current;
        setResult(null);
        setError(null);
        setSubmitting(false);
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
        const current = revision.current;
        setSubmitting(true);
        setError(null);
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
    const stale = result && (result.stale || result.timeline_version_id !== versionId
        || (result.check_state === "done" && !outputUrl));
    const current = !error && !stale ? result : null;
    const label = error ? "检查结果读取失败" : stale ? "旧检查已失效，请检查当前成片"
        : !current ? "正在读取检查结果" : current.check_state === "checking" ? "成片检查中"
            : current.check_state === "failed" ? "成片检查失败"
                : current.result_status === "issues_found" ? "成片发现问题"
                    : current.result_status === "unavailable" ? "部分检测不可用"
                        : current.result_status === "clean" ? "未发现确定性媒体问题" : "当前成片尚未检查";
    return <section className={styles.qualityCheck} aria-label="成片质量检查">
    <div className={styles.qualityHeading}><strong role="status" aria-label="成片质量检查状态">{label}</strong>
      <button className={styles.secondaryButton} type="button" disabled={!outputUrl || submitting || current?.check_state === "checking"} onClick={() => void check()}>重新检查成片</button></div>
    <p>检查黑帧、尾部截断、音频削波和起点时间戳错位；内容质量仍需人工确认。</p>
    {error || current?.error ? <p role="alert">{error ?? current?.error}</p> : null}
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
    {current?.findings.map((finding, index) => <article key={`${finding.kind}:${index}`}>
      <button type="button" onClick={() => onLocate(finding)}>
        定位 {finding.start_seconds.toFixed(2)}–{finding.end_seconds.toFixed(2)} 秒
      </button><span>{finding.detail}</span>
    </article>)}
    {current?.unavailable.map((item, index) => <p key={`${item.kind}:${index}`}>
      检测不可用：{item.reason}</p>)}
  </section>;
}
