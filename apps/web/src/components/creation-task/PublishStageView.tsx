"use client";
import Link from "next/link";
import styles from "../CreationTaskWorkspace.module.css";
export function PublishStageView({ ready, onOpenReview }: {
    ready: boolean;
    onOpenReview: () => void;
}) {
    return <section className={styles.workSection}>
    <div className={styles.sectionHeading}><div><h2>核对发布目标</h2><p>账号、平台能力、成片版本和发布确认在发布工作区核对。本任务页不显示平台结果或发布数据。</p></div></div>
    <p>{ready ? "已有成片；进入发布工作区后选择本任务，重新核对当前版本与账号。" : "本任务还没有可用成片；发布工作区会保留未就绪条件，不能据此称为已发布。"}</p>
    <div className={styles.primaryAction}><Link className="modern-primary-button" href="/history">打开发布工作区</Link>{ready ? <button className={styles.secondaryButton} onClick={onOpenReview} type="button">回到检查与输出</button> : null}</div>
  </section>;
}
