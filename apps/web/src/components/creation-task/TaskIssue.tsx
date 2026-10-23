"use client";
import { creationTaskFailureReason, creationTaskRepairAction } from "@/lib/creation-task-status";
import { AlertCircle } from "lucide-react";
import { type ReactNode } from "react";
import styles from "../CreationTaskWorkspace.module.css";
export function TaskIssue({ stage, reason, impact, repair, code, label, children }: {
    stage: string;
    reason: string | null;
    impact?: string | null;
    repair?: string | null;
    code?: string | null;
    label?: string;
    children?: ReactNode;
}) {
    const reviewFailure = ["script_review_invalid", "script_fact_unsupported", "script_goal_conflict"].includes(code ?? "");
    return <section className={styles.blocker} role="alert" aria-label={label}>
    <AlertCircle size={16}/><div className={styles.issueBody}>
      <strong>{(reviewFailure ? reason : creationTaskFailureReason(reason ?? impact ?? null, stage)) ?? "这次操作没有完成"}</strong>
      {reviewFailure && impact ? <p>{impact}</p> : null}
      {reviewFailure && repair ? <p>{repair}</p> : null}
      {children}
      <details className={styles.traceDetails}><summary>诊断详情</summary>
        {reason ? <p>原始原因：{reason}</p> : null}
        {impact ? <p>本次影响：{impact}</p> : null}
        {repair ? <p>处理建议：{creationTaskRepairAction(repair)}</p> : null}
        {repair ? <p>后台处理原文：{repair}</p> : null}
        {code ? <p>后台标识：{code}</p> : null}
      </details>
    </div>
  </section>;
}
