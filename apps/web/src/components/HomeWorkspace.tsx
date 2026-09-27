"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Clock3, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationTask, ProductionFoundation } from "@/lib/types";
import styles from "./HomeWorkspace.module.css";

type Count = { label: string; value: number };
type Entry = { label: string; count: number };
export type HomeDashboardExample = {
  scopeLabel: string;
  example: true;
  counts: [Count, Count, Count, Count, Count];
  trend: { sevenDays: number[]; thirtyDays: number[] };
  industries: Entry[];
  tags: Entry[];
  queue: { id: string; title: string; stage: string; detail: string }[];
  works: { id: string; title: string; status: string }[];
};

const countLabels = ["可见素材", "分析可用", "待分析", "知识来源", "作品记录"];
function taskName(name: string) {
  return name.replace(/\bb-?roll\b/gi, "补充画面").replace(/\bbatch\b/gi, "创作任务")
    .replace(/\bepisode\b/gi, "内容").replace(/\brecipe\b/gi, "制作方案").replace(/\bprovider\b/gi, "服务");
}
function readinessLabel(code?: string) {
  switch (code) {
    case "brand_profile_missing": return "品牌未设置";
    case "versioned_voice_authorization_missing": return "声音未授权";
    case "voice_reference_missing": return "声音缺资料";
    case "voice_model_not_ready": return "配音未备好";
    case "source_pool_empty": return "选题缺来源";
    case "approved_materials_empty": return "素材未选好";
    default: return "制作未备好";
  }
}
function TrendChart({ values }: { values: number[] }) {
  const maximum = Math.max(1, ...values);
  const points = values.map((value, index) => [12 + index * 43, 66 - value / maximum * 48].join(",")).join(" ");
  return <svg aria-label="示例素材接收趋势" preserveAspectRatio="none" role="img" viewBox="0 0 286 78">
    <path d="M12 68H274" fill="none" stroke="var(--border-1)" />
    <polyline fill="none" points={points} stroke="var(--accent)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />
    {values.map((value, index) => <g key={index}><circle cx={12 + index * 43} cy={66 - value / maximum * 48} fill="var(--accent)" r="3.5" /><text fill="var(--text-2)" fontSize="9" textAnchor="middle" x={12 + index * 43} y={Math.max(10, 55 - value / maximum * 48)}>{value}</text></g>)}
  </svg>;
}
function industryFill(items: Entry[]) {
  const total = Math.max(1, items.reduce((sum, item) => sum + item.count, 0));
  const colors = ["var(--accent)", "var(--warning)", "var(--success)"];
  let offset = 0;
  return "conic-gradient(" + items.map((item, index) => {
    const start = offset;
    offset += item.count / total * 100;
    return colors[index % colors.length] + " " + start + "% " + offset + "%";
  }).join(", ") + ")";
}

export function HomeWorkspace({ tasks, foundation, dashboard }: { tasks: CreationTask[]; foundation: ProductionFoundation | null; dashboard?: HomeDashboardExample }) {
  const router = useRouter();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [range, setRange] = useState<7 | 30>(7);
  const [selectedFilter, setSelectedFilter] = useState<string | null>(null);
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const readyTasks = tasks.filter((task) => task.output_ready);
  useEffect(() => {
    const refreshVisible = () => { if (document.visibilityState === "visible") router.refresh(); };
    const timer = window.setInterval(refreshVisible, 10000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); };
  }, [router]);

  return <div className={styles.home}>
    <header className={styles.heading}><div><h1>工作空间总览</h1><p>{dashboard?.scopeLabel ?? "当前工作区 · 数据按已接入来源展示"}</p></div>{dashboard ? <span className={styles.exampleBadge}>示例数据 · 不连接真实服务</span> : null}</header>
    {!foundation ? <div className={styles.readiness}><strong>暂时连不上制作服务</strong><Link href="/health">查看原因</Link></div>
      : !foundation.readiness.ready ? <div className={styles.readiness}><strong>{readinessLabel(foundation.readiness.blockers[0]?.code)}</strong><Link href="/health">查看原因</Link></div> : null}
    <div className={styles.dashboard}>
      <section aria-label="工作空间五项概览" className={styles.metrics}>{countLabels.map((label, index) => <div className={styles.metric} key={label}><strong>{dashboard ? dashboard.counts[index].value : "—"}</strong><small>{label}</small></div>)}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>素材接收趋势</h2><small>{dashboard ? "示例素材接收日" : "尚无接收日聚合数据"}</small></div><div aria-label="趋势范围" className={styles.range}><button aria-pressed={range === 7} onClick={() => setRange(7)} type="button">7 天</button><button aria-pressed={range === 30} onClick={() => setRange(30)} type="button">30 天</button></div></div>{dashboard ? <TrendChart values={range === 7 ? dashboard.trend.sevenDays : dashboard.trend.thirtyDays} /> : <div className={styles.cardEmpty}>接入按日素材统计后显示趋势</div>}</section>
      <section className={styles.card}><h2>素材行业分布</h2>{dashboard ? <div className={styles.donutLayout}><div aria-label="示例行业占比" className={styles.donut} role="img" style={{ background: industryFill(dashboard.industries) }}><strong>{dashboard.industries.reduce((sum, item) => sum + item.count, 0)}</strong><small>可见素材</small></div><div className={styles.legend}>{dashboard.industries.map((item) => <button aria-pressed={selectedFilter === item.label} key={item.label} onClick={() => setSelectedFilter(item.label)} type="button"><i />{item.label}<strong>{item.count}</strong></button>)}</div></div> : <div className={styles.cardEmpty}>素材尚无可信行业分类</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>任务与处理阶段</h2><small>{dashboard ? "整机队列 · 示例" : "当前工作区任务；整机队列待接入"}</small></div><Link href="/production">全部任务 <ArrowRight size={14} /></Link></div>{dashboard ? dashboard.queue.map((item) => <div className={styles.queueRow} key={item.id}><span className={styles.queueDot} /><div><strong>{item.title}</strong><small>{item.stage} · {item.detail}</small></div></div>) : tasks.length ? tasks.slice(0, 5).map((task) => { const state = creationTaskStatusPresentation(task.stage_status, task.stage); return <button className={styles.queueRow} key={task.id} onClick={() => setSelectedTaskId(task.id)} type="button"><span className={styles.queueDot} /><div><strong>{taskName(task.name)}</strong><small>{state.stageLabel} · {state.label}</small></div><ArrowRight size={14} /></button>; }) : <div className={styles.cardEmpty}>还没有创作任务。可以在左侧告诉助手要做什么。</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>内容标签词云</h2><small>{dashboard ? "按示例素材标签次数显示" : "尚无按素材去重的标签统计"}</small></div></div>{dashboard ? <div className={styles.cloudField}>{dashboard.tags.map((item) => <button aria-pressed={selectedFilter === item.label} key={item.label} onClick={() => setSelectedFilter(item.label)} style={{ fontSize: 12 + Math.min(12, item.count * 2) }} type="button">{item.label}<sup>{item.count}</sup></button>)}</div> : <div className={styles.cardEmpty}>接入可见素材标签统计后显示词云</div>}{dashboard && selectedFilter ? <p className={styles.filterNote}>示例已选择：{selectedFilter}</p> : null}</section>
      <section className={styles.card}><h2>近期作品</h2>{dashboard ? dashboard.works.map((item) => <div className={styles.workRow} key={item.id}><span><PlayCircle size={16} /></span><div><strong>{item.title}</strong><small>{item.status}</small></div></div>) : readyTasks.length ? <><small>当前工作区的本地成片，未代表平台发布</small>{readyTasks.slice(0, 3).map((task) => <Link className={styles.workRow} href={"/tasks/" + encodeURIComponent(task.id)} key={task.id}><span><PlayCircle size={16} /></span><div><strong>{taskName(task.name)}</strong><small>本地成片可查看</small></div></Link>)}</> : <div className={styles.cardEmpty}>还没有可查看的本地成片</div>}</section>
    </div>
    <p className={styles.note}>{dashboard ? "本页数字与筛选均为隔离展示页示例；真实统计与跨空间权限尚未接入。" : "概览数字与图表等待按权限汇总的接口；现有任务只代表当前工作区。"}</p>
    {selectedTask ? <HomeCardDialog onClose={() => setSelectedTaskId(null)} title="任务卡片"><div className="home-task-card"><h3>{taskName(selectedTask.name)}</h3><p>{creationTaskStatusPresentation(selectedTask.stage_status, selectedTask.stage).stageLabel}</p><div className="home-task-card__progress"><span>进度</span><strong>{selectedTask.progress}%</strong><i><b style={{ width: Math.max(0, Math.min(100, selectedTask.progress)) + "%" }} /></i></div>{selectedTask.stage_reason ? <div className="home-task-card__detail"><strong>需要处理</strong><p>{selectedTask.stage_reason}</p>{selectedTask.stage_impact ? <p>{selectedTask.stage_impact}</p> : null}{selectedTask.stage_repair_action ? <p>{selectedTask.stage_repair_action}</p> : null}</div> : null}<div className="home-task-card__detail"><Clock3 size={16} /><span>{new Date(selectedTask.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span></div><div className="home-task-card__actions"><button className="home-task-card__open" onClick={() => router.push("/tasks/" + encodeURIComponent(selectedTask.id))} type="button">对话修改</button><Link href={"/tasks/" + encodeURIComponent(selectedTask.id)}>打开任务 <ArrowRight size={15} /></Link></div></div></HomeCardDialog> : null}
  </div>;
}
