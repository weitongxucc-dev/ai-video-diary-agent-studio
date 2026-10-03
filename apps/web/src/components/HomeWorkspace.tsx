"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Clock3, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationTask, MaterialLibraryInsights, ProductionFoundation } from "@/lib/types";
import styles from "./HomeWorkspace.module.css";

type Count = { label: string; value: number };
type Entry = { label: string; count: number };
type TrendPoint = { date: string; count: number };
export type HomeMaterialFilterIntent = { kind: "industry" | "tag"; key: string; label: string };
type HomeExampleAsset = { id: string; title: string; receivedDate: string; analysisStatus: "ready" | "pending" | "failed"; industry: string; tags: string[] };
export type HomeDashboardExample = {
  scopeLabel: string;
  example: true;
  asOfDate: string;
  assets: HomeExampleAsset[];
  knowledgeSources: { id: string }[];
  queue: { id: string; title: string; stage: string; detail: string }[];
  works: { id: string; title: string; status: string }[];
};

const countLabels = ["可见素材", "分析可用", "待分析", "知识来源", "作品记录"];
function summarizeExample(dashboard: HomeDashboardExample) {
  const assets = dashboard.assets;
  const countBy = (key: "industry" | "tags") => {
    const counts = new Map<string, number>();
    assets.forEach((asset) => (key === "tags" ? [...new Set(asset.tags)] : [asset.industry]).forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1)));
    return [...counts].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count);
  };
  const trend = (days: number): TrendPoint[] => {
    const end = new Date(dashboard.asOfDate + "T00:00:00Z");
    return Array.from({ length: days }, (_, index) => {
      const date = new Date(end);
      date.setUTCDate(end.getUTCDate() - (days - index - 1));
      const label = date.toISOString().slice(0, 10);
      return { date: label, count: assets.filter((asset) => asset.receivedDate === label).length };
    });
  };
  const counts: Count[] = [
    { label: "可见素材", value: assets.length },
    { label: "分析可用", value: assets.filter((asset) => asset.analysisStatus === "ready").length },
    { label: "待分析", value: assets.filter((asset) => asset.analysisStatus === "pending").length },
    { label: "知识来源", value: dashboard.knowledgeSources.length },
    { label: "作品记录", value: dashboard.works.length },
  ];
  return { counts, sevenDays: trend(7), thirtyDays: trend(30), industries: countBy("industry"), tags: countBy("tags") };
}
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
export function TrendChart({ values }: { values: TrendPoint[] }) {
  if (!values.length) return <div className={styles.cardEmpty}>这段示例期间没有素材接收记录</div>;
  const maximum = Math.max(1, ...values.map((point) => point.count));
  const x = (index: number) => values.length === 1 ? 143 : 12 + index * 262 / (values.length - 1);
  const y = (count: number) => 66 - count / maximum * 48;
  const points = values.map((point, index) => [x(index), y(point.count)].join(",")).join(" ");
  return <div className={styles.chart}>
    <svg aria-label="示例素材接收趋势" preserveAspectRatio="none" role="img" viewBox="0 0 286 78">
      <path d="M12 68H274" fill="none" stroke="var(--border-1)" />
      {values.length > 1 ? <polyline fill="none" points={points} stroke="var(--accent)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" /> : null}
      {values.map((point, index) => <g key={point.date}><circle aria-label={point.date + " 接收 " + point.count + " 条"} cx={x(index)} cy={y(point.count)} fill="var(--accent)" r={values.length > 7 ? 2.5 : 3.5}><title>{point.date + "：" + point.count + " 条素材"}</title></circle>{values.length <= 7 ? <text fill="var(--text-2)" fontSize="9" textAnchor="middle" x={x(index)} y={Math.max(10, y(point.count) - 9)}>{point.count}</text> : null}</g>)}
    </svg>
    <div className={styles.chartLabels}><time>{values[0].date}</time><time>{values[values.length - 1].date}</time></div>
  </div>;
}
function IndustryDonut({ items, selected, onSelect }: { items: Entry[]; selected: HomeMaterialFilterIntent | null; onSelect: (intent: HomeMaterialFilterIntent) => void }) {
  const total = Math.max(1, items.reduce((sum, item) => sum + item.count, 0));
  const colors = ["var(--accent)", "var(--warning)", "var(--success)"];
  let offset = 0;
  const arcs = items.map((item, index) => {
    const start = offset;
    const length = item.count / total * 251.3;
    offset += length;
    const angle = -Math.PI / 2 + (start + length / 2) / 251.3 * Math.PI * 2;
    return { item, index, start, length, x: 50 + 40 * Math.cos(angle), y: 50 + 40 * Math.sin(angle) };
  });
  return <div className={styles.donutInteractive}>
    <svg aria-label="示例行业占比；可点环段筛选" className={styles.donut} role="img" viewBox="0 0 100 100">
      {arcs.map(({ item, index, start, length }) => <circle aria-hidden="true" cx="50" cy="50" fill="none" key={item.label} onClick={() => onSelect({ kind: "industry", key: item.label, label: item.label })} r="40" stroke={colors[index % colors.length]} strokeDasharray={length + " 251.3"} strokeDashoffset={-start} strokeWidth="18" transform="rotate(-90 50 50)" />)}
      <circle cx="50" cy="50" fill="var(--surface-1)" pointerEvents="none" r="30" />
      <text fill="var(--text-1)" fontSize="18" pointerEvents="none" textAnchor="middle" x="50" y="48">{items.reduce((sum, item) => sum + item.count, 0)}</text>
      <text fill="var(--text-2)" fontSize="8" pointerEvents="none" textAnchor="middle" x="50" y="61">可见素材</text>
    </svg>
    {arcs.map(({ item, x, y }) => <button aria-label={"查看" + item.label + "示例素材"} aria-pressed={selected?.kind === "industry" && selected.key === item.label} className={styles.donutHit} key={item.label} onClick={() => onSelect({ kind: "industry", key: item.label, label: item.label })} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect({ kind: "industry", key: item.label, label: item.label }); } }} style={{ left: x + "%", top: y + "%" }} type="button" />)}
  </div>;
}

export function HomeWorkspace({ tasks, foundation, dashboard, insights }: { tasks: CreationTask[]; foundation: ProductionFoundation | null; dashboard?: HomeDashboardExample; insights?: MaterialLibraryInsights | null }) {
  const router = useRouter();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [range, setRange] = useState<7 | 30>(7);
  const [selectedFilter, setSelectedFilter] = useState<HomeMaterialFilterIntent | null>(null);
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const selectedTaskState = selectedTask ? creationTaskStatusPresentation(selectedTask.stage_status, selectedTask.stage) : null;
  const readyTasks = tasks.filter((task) => task.output_ready);
  const example = dashboard ? summarizeExample(dashboard) : null;
  // 真实词云只认素材数量 asset_count；片段数量 segment_count 语义不同，不做兜底。
  // 服务端为默认分类表补了零值行，asset_count 为 0 的项没有任何素材，不能进词云。
  const realTags = !example && insights?.tags.length
    ? insights.tags.filter((item) => item.asset_count > 0).sort((a, b) => b.asset_count - a.asset_count || a.tag.localeCompare(b.tag))
    : [];
  const filteredExampleAssets = dashboard?.assets.filter((asset) => selectedFilter?.kind === "industry" ? asset.industry === selectedFilter.key : asset.tags.includes(selectedFilter?.key ?? "")) ?? [];
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
      <section aria-label="工作空间五项概览" className={styles.metrics}>{countLabels.map((label, index) => <div className={styles.metric} key={label}><strong>{example ? example.counts[index].value : "—"}</strong><small>{label}</small></div>)}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>素材接收趋势</h2><small>{dashboard ? "示例素材接收日" : "尚无接收日聚合数据"}</small></div><div aria-label="趋势范围" className={styles.range}><button aria-pressed={range === 7} onClick={() => setRange(7)} type="button">7 天</button><button aria-pressed={range === 30} onClick={() => setRange(30)} type="button">30 天</button></div></div>{example ? <TrendChart values={range === 7 ? example.sevenDays : example.thirtyDays} /> : <div className={styles.cardEmpty}>接入按日素材统计后显示趋势</div>}</section>
      <section className={styles.card}><h2>素材行业分布</h2>{example?.industries.length ? <div className={styles.donutLayout}><IndustryDonut items={example.industries} onSelect={setSelectedFilter} selected={selectedFilter} /><div className={styles.legend}>{example.industries.map((item) => <button aria-pressed={selectedFilter?.kind === "industry" && selectedFilter.key === item.label} key={item.label} onClick={() => setSelectedFilter({ kind: "industry", key: item.label, label: item.label })} type="button"><i />{item.label}<strong>{item.count}</strong></button>)}</div></div> : <div className={styles.cardEmpty}>{dashboard ? "示例范围没有可见素材" : "素材尚无可信行业分类"}</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>任务与处理阶段</h2><small>{dashboard ? "整机队列 · 示例" : "当前工作区任务；整机队列待接入"}</small></div><Link href="/production">全部任务 <ArrowRight size={14} /></Link></div>{dashboard ? dashboard.queue.map((item) => <div className={styles.queueRow} key={item.id}><span className={styles.queueDot} /><div><strong>{item.title}</strong><small>{item.stage} · {item.detail}</small></div></div>) : tasks.length ? tasks.slice(0, 5).map((task) => { const state = creationTaskStatusPresentation(task.stage_status, task.stage); return <button className={styles.queueRow} key={task.id} onClick={() => setSelectedTaskId(task.id)} type="button"><span className={styles.queueDot} /><div><strong>{taskName(task.name)}</strong><small>{state.stageLabel} · {state.label}</small></div><ArrowRight size={14} /></button>; }) : <div className={styles.cardEmpty}>还没有创作任务。可以在左侧告诉助手要做什么。</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>内容标签词云</h2><small>{dashboard ? "按示例可见素材去重计数" : realTags.length ? "按素材去重计数 · 点击进入素材库" : insights === undefined ? "标签统计未载入" : insights === null ? "标签统计读取失败，请刷新重试" : "当前工作区还没有素材标签"}</small></div></div>{example?.tags.length ? <div className={styles.cloudField}>{example.tags.map((item) => <button aria-pressed={selectedFilter?.kind === "tag" && selectedFilter.key === item.label} key={item.label} onClick={() => setSelectedFilter({ kind: "tag", key: item.label, label: item.label })} style={{ fontSize: 12 + Math.min(12, item.count * 2) }} type="button">{item.label}<sup>{item.count}</sup></button>)}</div> : realTags.length ? <div className={styles.cloudField}>{realTags.map((item) => <Link className={styles.cloudLink} href={"/library?tag=" + encodeURIComponent(item.tag)} key={item.tag} style={{ fontSize: 12 + Math.min(12, item.asset_count * 2) }}>{item.tag}<sup>{item.asset_count}</sup></Link>)}</div> : <div className={styles.cardEmpty}>{dashboard ? "示例范围没有可见素材标签" : insights === null ? "标签统计读取失败，请刷新重试" : insights === undefined ? "标签统计未载入" : "当前工作区还没有素材标签"}</div>}</section>
      <section className={styles.card}><h2>近期作品</h2>{dashboard ? dashboard.works.map((item) => <div className={styles.workRow} key={item.id}><span><PlayCircle size={16} /></span><div><strong>{item.title}</strong><small>{item.status}</small></div></div>) : readyTasks.length ? <><small>当前工作区的本地成片，未代表平台发布</small>{readyTasks.slice(0, 3).map((task) => <Link className={styles.workRow} href={"/tasks/" + encodeURIComponent(task.id)} key={task.id}><span><PlayCircle size={16} /></span><div><strong>{taskName(task.name)}</strong><small>本地成片可查看</small></div></Link>)}</> : <div className={styles.cardEmpty}>还没有可查看的本地成片</div>}</section>
    </div>
    <p className={styles.note}>{dashboard ? "本页数字与筛选均为隔离展示页示例；真实统计与跨空间权限尚未接入。" : "概览数字与图表等待按权限汇总的接口；现有任务只代表当前工作区。"}</p>
    {dashboard && selectedFilter ? <HomeCardDialog onClose={() => setSelectedFilter(null)} title="示例素材筛选">
      <div className={styles.filterResults}><p>筛选意图：{selectedFilter.kind === "industry" ? "行业" : "标签"} · {selectedFilter.label}。真实素材库精确筛选尚未接入。</p><strong>{filteredExampleAssets.length} 条对应示例素材</strong>{filteredExampleAssets.map((asset) => <div className={styles.filterResult} key={asset.id}><span>{asset.title}</span><small>{asset.industry} · {asset.tags.join("、")}</small></div>)}</div>
    </HomeCardDialog> : null}
    {selectedTask && selectedTaskState ? <HomeCardDialog onClose={() => setSelectedTaskId(null)} title="任务卡片"><div className="home-task-card"><h3>{taskName(selectedTask.name)}</h3><p>{selectedTaskState.stageLabel} · {selectedTaskState.label}</p>{selectedTask.stage_reason ? <div className="home-task-card__detail"><strong>需要处理</strong><p>{selectedTask.stage_reason}</p>{selectedTask.stage_impact ? <p>{selectedTask.stage_impact}</p> : null}{selectedTask.stage_repair_action ? <p>{selectedTask.stage_repair_action}</p> : null}</div> : null}<div className="home-task-card__detail"><Clock3 size={16} /><span>{new Date(selectedTask.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span></div><div className="home-task-card__actions"><button className="home-task-card__open" onClick={() => router.push("/tasks/" + encodeURIComponent(selectedTask.id))} type="button">对话修改</button><Link href={"/tasks/" + encodeURIComponent(selectedTask.id)}>打开任务 <ArrowRight size={15} /></Link></div></div></HomeCardDialog> : null}
  </div>;
}
