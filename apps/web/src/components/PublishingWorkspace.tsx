"use client";

import Link from "next/link";
import { AlertCircle, ArrowRight, CheckCircle2, CircleDashed, Clock3, Film, LoaderCircle, RefreshCw, Send, Signal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { listCreationTasks } from "@/lib/api";
import type { CreationTask } from "@/lib/types";
import styles from "./ProductWorkspace.module.css";

type Filter = "all" | "attention" | "ready" | "failed";
const filters: Array<{ id: Filter; label: string }> = [
  { id: "all", label: "全部记录" }, { id: "attention", label: "需要处理" },
  { id: "ready", label: "成片就绪" }, { id: "failed", label: "运行失败" },
];
const stageLabels: Record<CreationTask["stage"], string> = { topic: "选题", script: "文案", narration: "配音", montage: "视频剪辑", review: "成片检查" };

function taskState(task: CreationTask): { label: string; tone: string; icon: typeof Clock3 } {
  if (task.stage_status === "failed") return { label: "运行失败", tone: styles.stateDanger, icon: AlertCircle };
  if (task.output_ready) return { label: "成片就绪", tone: styles.stateSuccess, icon: CheckCircle2 };
  if (task.stage_status === "waiting_for_user") return { label: "需要处理", tone: styles.stateWarning, icon: Clock3 };
  if (task.stage_status === "running") return { label: "正在制作", tone: styles.stateBlue, icon: LoaderCircle };
  return { label: "尚未开始", tone: styles.stateNeutral, icon: CircleDashed };
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function taskNextStep(task: CreationTask): string {
  if (task.output_ready) return "检查并保存本地成片";
  if (task.stage_repair_action) return task.stage_repair_action;
  if (task.stage_status === "waiting_for_user") return `继续确认${stageLabels[task.stage]}`;
  if (task.stage_status === "failed") return "打开任务查看失败原因";
  return `继续${stageLabels[task.stage]}`;
}

export function PublishingWorkspace() {
  const [tasks, setTasks] = useState<CreationTask[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setLoading(true); setError(null);
    try { const response = await listCreationTasks(); setTasks(response.items); setSelectedId((current) => current ?? response.items[0]?.id ?? null); }
    catch (cause) { setError(cause instanceof Error && cause.message ? cause.message : "暂时无法连接本地服务。"); }
    finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  const filtered = useMemo(() => tasks.filter((task) => {
    if (filter === "attention") return task.stage_status === "waiting_for_user";
    if (filter === "ready") return task.output_ready;
    if (filter === "failed") return task.stage_status === "failed";
    return true;
  }), [filter, tasks]);
  const selected = filtered.find((task) => task.id === selectedId) ?? filtered[0] ?? null;
  const attentionCount = tasks.filter((task) => task.stage_status === "waiting_for_user").length;
  const readyCount = tasks.filter((task) => task.output_ready).length;

  const inspector = selected ? (
    <div className={styles.inspector}>
      <span className={styles.objectEyebrow}>当前创作任务</span><h3>{selected.name}</h3>
      <span className={`${styles.stateBadge} ${taskState(selected).tone}`}>{taskState(selected).label}</span>
      <dl><div><dt>当前阶段</dt><dd>{stageLabels[selected.stage]}</dd></div><div><dt>整体进度</dt><dd>{selected.progress}%</dd></div><div><dt>最后更新</dt><dd>{formatTime(selected.updated_at)}</dd></div><div><dt>本地成片</dt><dd>{selected.output_ready ? "已生成，可检查" : "尚未生成"}</dd></div></dl>
      {selected.stage_reason ? <section className={styles.inspectorNotice}><strong>{selected.stage_impact || "当前任务需要处理"}</strong>{selected.stage_repair_action ? <p>处理方式：{selected.stage_repair_action}</p> : null}</section> : null}
      <Link className={styles.primaryButton} href={`/tasks/${encodeURIComponent(selected.id)}`}>打开创作任务 <ArrowRight size={15} /></Link>
      <section className={styles.inspectorMuted}><strong>平台能力</strong><p>平台发布、定时发布和发布后监控尚未接入；本页不会把本地成片计为已发布。</p></section>
    </div>
  ) : undefined;

  return (
    <AppShell creationTasks={tasks} inspector={inspector} inspectorTitle="记录详情">
      <div className={styles.page}>
        <section className={styles.capabilityBanner}><span><Signal size={18} /></span><div><strong>平台发布与监控尚未接入</strong><p>当前只展示本地创作任务的真实状态，不显示虚构播放量、点赞量或发布成功数。</p></div><Link href="/health">查看运行情况 <ArrowRight size={14} /></Link></section>
        <div className={styles.summaryRow} aria-label="真实任务摘要"><span><strong>{tasks.length}</strong> 全部任务</span><span><strong>{attentionCount}</strong> 需要处理</span><span><strong>{readyCount}</strong> 成片就绪</span><button className={styles.secondaryButton} disabled={loading} onClick={() => void refresh()} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={15} />刷新</button></div>
        <nav aria-label="记录筛选" className={styles.tabs}>{filters.map((item) => <button aria-pressed={filter === item.id} className={filter === item.id ? styles.activeTab : undefined} key={item.id} onClick={() => setFilter(item.id)} type="button">{item.label}</button>)}</nav>
        <section className={styles.recordList}>
          <div className={styles.sectionHeading}><h2>{filter === "all" ? "最近更新" : filters.find((item) => item.id === filter)?.label}</h2><p>{loading ? "正在读取…" : `${filtered.length} 项`}</p></div>
          {error ? <div className={styles.errorState} role="alert"><AlertCircle size={20} /><div><strong>暂时无法读取创作记录</strong><p>{error} 页面没有改用旧发布数据。</p></div><button onClick={() => void refresh()} type="button">重新读取</button></div> : loading ? <div className={styles.loadingState}><LoaderCircle className={styles.spinning} size={19} /> 正在读取当前工作区…</div> : filtered.length ? (
            <ol className={styles.records}>{filtered.map((task) => { const state = taskState(task); const StateIcon = state.icon; return <li key={task.id}><button aria-pressed={selected?.id === task.id} className={selected?.id === task.id ? styles.selectedRecord : undefined} onClick={() => setSelectedId(task.id)} type="button"><span className={`${styles.recordState} ${state.tone}`}><StateIcon className={task.stage_status === "running" ? styles.spinning : undefined} size={16} /></span><span className={styles.recordBody}><span className={styles.recordTitle}><strong>{task.name}</strong><em>{state.label}</em></span><span>{stageLabels[task.stage]} · {taskNextStep(task)}</span><span className={styles.progressTrack} aria-label={`进度 ${task.progress}%`}><i style={{ width: `${task.progress}%` }} /></span></span><span className={styles.recordTime}>{formatTime(task.updated_at)}<ArrowRight size={15} /></span></button></li>; })}</ol>
          ) : <div className={styles.emptyStage}><span className={styles.emptyIcon}>{filter === "all" ? <Film size={22} /> : <Send size={22} />}</span><div><h2>{filter === "all" ? "还没有创作记录" : "当前筛选下没有记录"}</h2><p>{filter === "all" ? "从一个真实制作目标开始，任务进度会自动出现在这里。" : "切换筛选，或继续处理已有创作任务。"}</p></div><Link className={styles.primaryButton} href="/production?new=1">开始制作新视频</Link></div>}
        </section>
      </div>
    </AppShell>
  );
}
