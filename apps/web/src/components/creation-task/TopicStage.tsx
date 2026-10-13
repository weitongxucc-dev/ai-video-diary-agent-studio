"use client";
import { CheckCircle2 } from "lucide-react";
import type { CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
export function ConfirmedTopicView({ topic, evidence }: {
    topic: WorkspaceState["selected_topic"];
    evidence: WorkspaceState["topic_evidence"];
}) {
    if (!topic)
        return null;
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>{topic.title}</h2><p>{topic.angle}</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15}/>已确认</span></div>
      <div className={styles.artifactGrid}>
        <section><strong>目标观众</strong><p>{topic.audience}</p></section>
        <section><strong>内容结构</strong><ol>{topic.outline.map((item) => <li key={item}>{item}</li>)}</ol></section>
      </div>
      <details className={styles.requirementDetails}><summary>来源证据</summary><div className={styles.evidenceList}>{evidence.length ? evidence.map((item) => <article key={item.id}><span>{item.kind === "knowledge" ? "知识库" : "信息源"}</span><div><strong>{item.title}</strong><small>{item.authorization_or_license}</small></div></article>) : <p>当前选题没有返回可展示的来源条目。</p>}</div></details>
    </section>);
}
