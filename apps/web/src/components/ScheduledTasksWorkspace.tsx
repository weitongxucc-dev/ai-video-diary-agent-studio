"use client";

import { useEffect, useState } from "react";
import { listScheduledTasks, type ScheduledTask } from "@/lib/api";
import styles from "./ScheduledTasksWorkspace.module.css";

const taskTypeLabels: Record<string, string> = { material_analysis: "素材解析", creation_render: "成片制作" };
const modeLabels: Record<string, string> = { scheduled: "指定时间", idle: "设备空闲" };

function formatUtc(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间格式无法识别";
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function ScheduledTasksWorkspace() {
  const [tasks, setTasks] = useState<ScheduledTask[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  async function refresh() {
    setTasks(null); setLoadError(null);
    try { setTasks((await listScheduledTasks()).tasks); }
    catch (cause) { setLoadError(cause instanceof Error ? cause.message : "定时任务定义读取失败"); }
  }
  useEffect(() => { void refresh(); }, []);

  return <section className={styles.page} aria-label="定时任务只读预览">
    <div className={styles.previewNote}>
      <strong>只读预览 · 尚未接通执行</strong>
      <p>这里只查看已保存的任务定义，不会在指定时间或设备空闲时自动运行。</p>
    </div>
    {loadError ? <div role="alert" className={styles.errorBox}>
      <p>定时任务定义读取失败：{loadError}</p>
      <button className={styles.button} type="button" onClick={() => void refresh()}>重试</button>
    </div> : tasks === null ? <p role="status">正在读取已保存定义…</p>
      : tasks.length === 0 ? <div className={styles.empty}>
        <strong>还没有已保存的定时任务定义</strong>
        <p>定时执行尚未接通，当前仅提供只读预览。</p>
      </div> : <>
        <p className={styles.count}>{tasks.length} 项已保存定义</p>
        <ul className={styles.list}>
          {tasks.map(task => <li key={task.id} className={styles.row}>
            <div className={styles.rowHeading}>
              <h2>{taskTypeLabels[task.task_type] ?? "任务定义"}</h2>
              <span className={styles.state}>尚未接通执行</span>
            </div>
            <dl className={styles.facts}>
              <div><dt>已保存触发条件</dt><dd>{modeLabels[task.trigger_mode] ?? "无法识别，详见诊断"}</dd></div>
              <div><dt>已保存最早时间</dt><dd>{task.not_before_utc ? formatUtc(task.not_before_utc) : "未设置"}</dd></div>
            </dl>
            <details className={styles.diagnostics}>
              <summary>诊断详情</summary>
              <p>以下是已保存的定义字段，不代表已调度或已执行。</p>
              <pre>{JSON.stringify(task, null, 2)}</pre>
            </details>
          </li>)}
        </ul>
      </>}
  </section>;
}
