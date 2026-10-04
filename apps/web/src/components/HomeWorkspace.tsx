"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Clock3, PlayCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import { getHomeDashboard, getMaterialLibraryInsights, getProductionFoundation, listCreationTasks } from "@/lib/api";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import type { HomeAggregation } from "@/lib/types";
import type { CreationTask, HomeCountFact, MaterialLibraryInsights, ProductionFoundation } from "@/lib/types";
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

const countLabels = ["可见素材", "分析可用", "待分析", "知识来源", "声音", "任务", "作品"];
// 与服务端 counts.key 一一对应（98 与 125 同一组键）；改口径必须同时改这里和 98 的实现。
const countKeys = [
  "visible_assets", "analysis_ready", "analysis_pending",
  "knowledge_sources", "voices", "tasks", "works",
] as const;
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
    // 示例页里的声音/任务同样由这份可见素材推不出来，如实显示未知而不是编数。
    { label: "声音", value: -1 },
    { label: "任务", value: dashboard.queue.length },
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
  if (!values.length) return <div className={styles.cardEmpty}>这段时间没有素材接收记录</div>;
  const maximum = Math.max(1, ...values.map((point) => point.count));
  const x = (index: number) => values.length === 1 ? 143 : 12 + index * 262 / (values.length - 1);
  const y = (count: number) => 66 - count / maximum * 48;
  const points = values.map((point, index) => [x(index), y(point.count)].join(",")).join(" ");
  return <div className={styles.chart}>
    <svg aria-label="素材接收趋势" preserveAspectRatio="none" role="img" viewBox="0 0 286 78">
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

export function HomeWorkspace({ tasks, foundation, dashboard, insights, live }: { tasks: CreationTask[]; foundation: ProductionFoundation | null; dashboard?: HomeDashboardExample; insights?: MaterialLibraryInsights | null; live?: HomeLiveState }) {
  const router = useRouter();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [range, setRange] = useState<7 | 30>(7);
  const [selectedFilter, setSelectedFilter] = useState<HomeMaterialFilterIntent | null>(null);
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const selectedTaskState = selectedTask ? creationTaskStatusPresentation(selectedTask.stage_status, selectedTask.stage) : null;
  const readyTasks = tasks.filter((task) => task.output_ready);
  const example = dashboard ? summarizeExample(dashboard) : null;
  const home = live?.data ?? null;
  // 整机计数按服务端给的 key 口径显示；没有真实载体的项由 unknown 说明原因，
  // 页面不补 0、不换口径。
  const countByKey = (key: string): HomeCountFact | null =>
    home ? (home.counts.find((entry) => entry.key === key) ?? null) : null;
  const unknownReason = (key: string): string | null =>
    home ? (home.unknown.find((item) => item.key === key)?.reason ?? null) : null;
  const countValue = (index: number) => {
    if (example) { const value = example.counts[index].value; return value < 0 ? "未知" : value; }
    if (home) {
      const key = countKeys[index];
      const fact = countByKey(key);
      if (fact) return fact.value;
      return unknownReason(key) ?? "未知";
    }
    return "—";
  };
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

  // 真实数据还没到：显示加载状态而不是空白或编造数字。
  if (live && live.loading && !live.data) {
    return <div className={styles.home}><p className={styles.cardEmpty} role="status">正在读取整机概览…</p></div>;
  }

  return <div className={styles.home}>
    <header className={styles.heading}><div><h1>工作空间总览</h1><p>{dashboard?.scopeLabel ?? "当前工作区 · 数据按已接入来源展示"}</p></div>{dashboard ? <span className={styles.exampleBadge}>示例数据 · 不连接真实服务</span> : null}</header>
    {!foundation ? <div className={styles.readiness}><strong>暂时连不上制作服务</strong><Link href="/health">查看原因</Link></div>
      : !foundation.readiness.ready ? <div className={styles.readiness}><strong>{readinessLabel(foundation.readiness.blockers[0]?.code)}</strong><Link href="/health">查看原因</Link></div> : null}
    <div className={styles.dashboard}>
      <section aria-label="工作空间七项概览" className={styles.metrics}>{countLabels.map((label, index) => <div className={styles.metric} key={label}><strong>{countValue(index)}</strong><small>{label}</small></div>)}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>素材接收趋势</h2><small>{dashboard ? "示例素材接收日" : home ? `按接收日统计 · 近 ${home.trend.days} 天 · ${scopeLabel(home.trend.meta.scope)} · ${sourceLabel(home.trend.meta.source)} · 更新于 ${new Date(home.trend.meta.as_of).toLocaleString("zh-CN")}` : "尚无接收日聚合数据"}</small></div><div aria-label="趋势范围" className={styles.range}><button aria-pressed={range === 7} onClick={() => { setRange(7); live?.onDays(7); }} type="button">7 天</button><button aria-pressed={range === 30} onClick={() => { setRange(30); live?.onDays(30); }} type="button">30 天</button></div></div>{example ? <TrendChart values={range === 7 ? example.sevenDays : example.thirtyDays} /> : home ? (home.trend.points.length ? <TrendChart values={home.trend.points} /> : <div className={styles.cardEmpty}>这段时间没有素材接收记录</div>) : <div className={styles.cardEmpty}>接入按日素材统计后显示趋势</div>}</section>
      <section className={styles.card}><h2>素材行业分布</h2>{example?.industries.length ? <div className={styles.donutLayout}><IndustryDonut items={example.industries} onSelect={setSelectedFilter} selected={selectedFilter} /><div className={styles.legend}>{example.industries.map((item) => <button aria-pressed={selectedFilter?.kind === "industry" && selectedFilter.key === item.label} key={item.label} onClick={() => setSelectedFilter({ kind: "industry", key: item.label, label: item.label })} type="button"><i />{item.label}<strong>{item.count}</strong></button>)}</div></div> : <div className={styles.cardEmpty}>{dashboard ? "示例范围没有可见素材" : "素材尚无可信行业分类"}</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>任务与处理阶段</h2><small>{dashboard ? "整机队列 · 示例" : home
            ? `整机状态：处理中 ${home.device_queue.running ?? "未知"} · 排队 ${home.device_queue.queued ?? "未知"}`
              + (Object.keys(home.device_queue.counts).length
                ? ` · ${Object.entries(home.device_queue.counts).map(([key, value]) => `${runtimeStateLabel(key)} ${value}`).join(" · ")}`
                : " · 状态分布未读到")
              + ` · ${scopeLabel(home.device_queue.meta.scope)} · 更新于 ${new Date(home.device_queue.meta.as_of).toLocaleString("zh-CN")}`
            : "当前工作区任务；整机队列待接入"}</small></div><Link href="/production">全部任务 <ArrowRight size={14} /></Link></div>{dashboard ? dashboard.queue.map((item) => <div className={styles.queueRow} key={item.id}><span className={styles.queueDot} /><div><strong>{item.title}</strong><small>{item.stage} · {item.detail}</small></div></div>) : home ? <div className={styles.cardEmpty}>没有本人正在处理的任务</div> : tasks.length ? tasks.slice(0, 5).map((task) => { const state = creationTaskStatusPresentation(task.stage_status, task.stage); return <button className={styles.queueRow} key={task.id} onClick={() => setSelectedTaskId(task.id)} type="button"><span className={styles.queueDot} /><div><strong>{taskName(task.name)}</strong><small>{state.stageLabel} · {state.label}</small></div><ArrowRight size={14} /></button>; }) : <div className={styles.cardEmpty}>还没有创作任务。可以在左侧告诉助手要做什么。</div>}</section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>内容标签词云</h2><small>{dashboard ? "按示例可见素材去重计数" : realTags.length ? "按素材去重计数 · 点击进入素材库" : insights === undefined ? "标签统计未载入" : insights === null ? "标签统计读取失败，请刷新重试" : "当前工作区还没有素材标签"}</small></div></div>{example?.tags.length ? <div className={styles.cloudField}>{example.tags.map((item) => <button aria-pressed={selectedFilter?.kind === "tag" && selectedFilter.key === item.label} key={item.label} onClick={() => setSelectedFilter({ kind: "tag", key: item.label, label: item.label })} style={{ fontSize: 12 + Math.min(12, item.count * 2) }} type="button">{item.label}<sup>{item.count}</sup></button>)}</div> : realTags.length ? <div className={styles.cloudField}>{realTags.map((item) => <Link className={styles.cloudLink} href={"/library?tag=" + encodeURIComponent(item.tag)} key={item.tag} style={{ fontSize: 12 + Math.min(12, item.asset_count * 2) }}>{item.tag}<sup>{item.asset_count}</sup></Link>)}</div> : <div className={styles.cardEmpty}>{dashboard ? "示例范围没有可见素材标签" : insights === null ? "标签统计读取失败，请刷新重试" : insights === undefined ? "标签统计未载入" : "当前工作区还没有素材标签"}</div>}</section>
      <section className={styles.card}>{home ? null : <><h2>近期作品</h2>{dashboard ? dashboard.works.map((item) => <div className={styles.workRow} key={item.id}><span><PlayCircle size={16} /></span><div><strong>{item.title}</strong><small>{item.status}</small></div></div>) : readyTasks.length ? <><small>当前工作区的本地成片，未代表平台发布</small>{readyTasks.slice(0, 3).map((task) => <Link className={styles.workRow} href={"/tasks/" + encodeURIComponent(task.id)} key={task.id}><span><PlayCircle size={16} /></span><div><strong>{taskName(task.name)}</strong><small>本地成片可查看</small></div></Link>)}</> : <div className={styles.cardEmpty}>还没有可查看的本地成片</div>}</>}</section>
    </div>
    {home ? <>
      <section className={styles.card}><h2>存储</h2><dl className={styles.homeFacts}>
        <div><dt>已用</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.used_bytes) : "未知"}</dd></div>
        <div><dt>容量</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.total_bytes) : "未知"}</dd></div>
        <div><dt>可用</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.free_bytes) : "未知"}</dd></div>
        <div><dt>数据盘</dt><dd>{directoryStateLabel(home.storage.directory_state)}{home.storage.bound_to_another_workspace ? "（绑定在其他空间）" : ""}{home.storage.configured_path_changed ? "（保存位置已变更）" : ""}</dd></div>
        <div><dt>统计口径</dt><dd>{scopeLabel(home.storage.meta.scope)} · {sourceLabel(home.storage.meta.source)} · 更新于 {new Date(home.storage.meta.as_of).toLocaleString("zh-CN")}</dd></div>
      </dl></section>
      <section className={styles.card}><div className={styles.cardHeading}><div><h2>我的任务</h2><small>当前身份可进入 · 每条都有真实任务</small></div></div>{home.entries.queue.length ? home.entries.queue.map((item) => <button className={styles.queueRow} key={item.task_id} onClick={() => router.push("/tasks/" + encodeURIComponent(item.task_id))} type="button"><span className={styles.queueDot} /><div><strong>{taskName(item.title)}</strong><small>{homeStageLabel(item.stage)} · {homeStatusLabel(item.status)}</small></div><ArrowRight size={14} /></button>) : <div className={styles.cardEmpty}>当前没有属于你的排队任务</div>}</section>
      <section className={styles.card}><h2>待处理</h2>{home.entries.todos.length ? home.entries.todos.map((item) => <Link className={styles.workRow} href={"/tasks/" + encodeURIComponent(item.task_id)} key={item.task_id}><div><strong>{taskName(item.title)}</strong><small>{homeStageLabel(item.stage)} · {item.reason}</small>{item.repair_action ? <small>可处理：{item.repair_action}</small> : null}</div><ArrowRight size={14} /></Link>) : <div className={styles.cardEmpty}>没有需要你处理的事项</div>}</section>
      <section className={styles.card}><h2>近期成果</h2>{home.entries.works.length ? home.entries.works.map((item) => <Link className={styles.workRow} href={"/tasks/" + encodeURIComponent(item.task_id)} key={item.task_id}><span><PlayCircle size={16} /></span><div><strong>{taskName(item.title)}</strong><small>{new Date(item.updated_at).toLocaleString("zh-CN")} · 本地成片</small></div></Link>) : <div className={styles.cardEmpty}>还没有可查看的本地成片</div>}</section>
      {home.unknown.length ? <section className={styles.card}><h2>没有真实数据的项</h2><dl className={styles.homeFacts}>{home.unknown.map((item) => <div key={item.key}><dt>{unknownKeyLabel(item.key)}</dt><dd>{item.reason}</dd></div>)}</dl></section> : null}
      {live?.error ? <div className={styles.readiness} role="alert"><strong>{live.error}</strong><button onClick={live.onRetry} type="button">重新读取</button></div> : null}
    </> : null}
    <p className={styles.note}>{dashboard ? "本页数字与筛选均为隔离展示页示例；真实统计与跨空间权限尚未接入。" : home ? "口径与更新时间见各卡片；明细只含当前身份可进入的任务。行业分类仍待定义。" : "概览数字与图表等待按权限汇总的接口；现有任务只代表当前工作区。"}</p>
    {dashboard && selectedFilter ? <HomeCardDialog onClose={() => setSelectedFilter(null)} title="示例素材筛选">
      <div className={styles.filterResults}><p>筛选意图：{selectedFilter.kind === "industry" ? "行业" : "标签"} · {selectedFilter.label}。真实素材库精确筛选尚未接入。</p><strong>{filteredExampleAssets.length} 条对应示例素材</strong>{filteredExampleAssets.map((asset) => <div className={styles.filterResult} key={asset.id}><span>{asset.title}</span><small>{asset.industry} · {asset.tags.join("、")}</small></div>)}</div>
    </HomeCardDialog> : null}
    {selectedTask && selectedTaskState ? <HomeCardDialog onClose={() => setSelectedTaskId(null)} title="任务卡片"><div className="home-task-card"><h3>{taskName(selectedTask.name)}</h3><p>{selectedTaskState.stageLabel} · {selectedTaskState.label}</p>{selectedTask.stage_reason ? <div className="home-task-card__detail"><strong>需要处理</strong><p>{selectedTask.stage_reason}</p>{selectedTask.stage_impact ? <p>{selectedTask.stage_impact}</p> : null}{selectedTask.stage_repair_action ? <p>{selectedTask.stage_repair_action}</p> : null}</div> : null}<div className="home-task-card__detail"><Clock3 size={16} /><span>{new Date(selectedTask.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span></div><div className="home-task-card__actions"><button className="home-task-card__open" onClick={() => router.push("/tasks/" + encodeURIComponent(selectedTask.id))} type="button">对话修改</button><Link href={"/tasks/" + encodeURIComponent(selectedTask.id)}>打开任务 <ArrowRight size={15} /></Link></div></div></HomeCardDialog> : null}
  </div>;
}


/** 真实概览的加载/失败/真空状态；不显示任何编造数字。 */
export type HomeLiveState = {
  data: HomeAggregation | null;
  loading: boolean;
  error: string | null;
  days: 7 | 30;
  onDays: (days: 7 | 30) => void;
  onRetry: () => void;
};

// 口径与来源的中文说明：不向用户铺 state 键或源码路径。
const scopeLabels: Record<string, string> = {
  caller_visible: "当前身份可见",
  local_diagnostic: "本机受控诊断",
  workspace: "当前工作区",
  device_anonymous: "整机匿名汇总",
  host: "整机",
};
const sourceLabels: Record<string, string> = {
  material_asset: "素材库",
  knowledge_item: "知识库",
  voice_profile: "声音库",
  creation_task: "创作任务",
  storage_capacity: "存储目录",
  device_runtime: "本机运行记录",
};
// 服务端的 unknown.key 与 directory_state 是技术串，界面上给中文说法。
const unknownKeyLabels: Record<string, string> = {
  "materials.by_industry": "素材行业分布",
  "device_queue.analysis": "分析任务的运行与排队数",
  device_queue: "整机运行与排队统计",
  "storage.capacity": "存储容量",
  voices: "声音库统计", tasks: "任务统计", works: "作品统计",
  knowledge_sources: "知识来源统计", analysis_ready: "分析可用数",
  analysis_pending: "待分析数", visible_assets: "可见素材数",
};
function unknownKeyLabel(key: string) {
  return unknownKeyLabels[key] ?? "其他统计";
}
const directoryStateLabels: Record<string, string> = {
  missing: "数据盘目录不存在", unmounted: "数据盘未挂载",
  invalid: "数据盘路径无效", read_only: "数据盘只读", ready: "数据盘可用",
};
function directoryStateLabel(state: string) {
  return directoryStateLabels[state] ?? "数据盘状态未知";
}
function scopeLabel(scope: string) {
  return scopeLabels[scope] ?? "未声明口径";
}
function sourceLabel(source: string) {
  return sourceLabels[source] ?? "本机记录";
}
// 整机状态计数里的状态名用中文说明；ready 是「已就绪」，不是正在运行。
// 状态计数的技术键（job.* / intake.*）只在这里翻成中文，不直接铺到界面上。
// 状态全集与后端一致（job_runs 的状态 / 素材接收状态），不另造状态词。
const runtimeStateLabels: Record<string, string> = {
  "job.queued": "任务排队中", "job.running": "任务处理中", "job.succeeded": "任务已完成",
  "job.failed": "任务失败", "job.cancelled": "任务已取消", "job.interrupted": "任务中断待恢复",
  "intake.observing": "素材待确认", "intake.running": "素材接收中", "intake.ready": "素材已就绪",
  "intake.failed": "素材接收失败", "intake.blocked_disk": "素材被数据盘阻塞",
};
function runtimeStateLabel(key: string) {
  return runtimeStateLabels[key] ?? "其他状态";
}

const homeStageKeys = new Set(["topic", "script", "narration", "montage", "review"]);
const homeStatusKeys = new Set(["not_started", "running", "waiting_for_user", "completed", "failed"]);
// 首页条目里的 stage/status 复用既有中文映射；服务端已经给中文时直接用它的说法。
function homeStageLabel(stage: string) {
  return homeStageKeys.has(stage) ? creationTaskStatusPresentation("running", stage as CreationTask["stage"]).stageLabel.split("：")[0] : stage;
}
function homeStatusLabel(status: string) {
  if (!homeStatusKeys.has(status)) return status;
  const stage: CreationTask["stage"] = "montage";
  const label = creationTaskStatusPresentation(status as CreationTask["stage_status"], stage).label;
  return label.replace("视频剪辑", "");
}

function formatBytes(value: number | null) {
  if (value === null) return "未知";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/**
 * 首页真实数据容器。
 *
 * 所有请求都在浏览器端带上当前成员令牌：服务端渲染拿不到 localStorage 里的
 * 身份，先按 owner 预取私有任务再在客户端隐藏会把别人的名字发到浏览器首帧，
 * 所以这里不在 SSR 预取任何私有任务。
 * 切换成员（localStorage 令牌变化）后自动重新读取。
 */
export function HomeDashboardLive() {
  const [data, setData] = useState<HomeAggregation | null>(null);
  const [tasks, setTasks] = useState<CreationTask[]>([]);
  const [foundation, setFoundation] = useState<ProductionFoundation | null>(null);
  const [insights, setInsights] = useState<MaterialLibraryInsights | null>(null);
  const [days, setDays] = useState<7 | 30>(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 身份一律走既有钩子：同标签加入空间、跨标签页切换、切回窗口都通过它收敛，
  // 页面不自己读 localStorage，避免两处令牌来源互相遮蔽。
  const identity = useLocalMemberIdentity();
  const identityKey = identity.token ?? "";
  // 只有最后一次请求的结果可以提交：切身份或切天数之后，之前的响应一律丢弃，
  // 不让旧身份的私有标题或旧天数的数字落到当前页面上。
  const requestRef = useRef(0);
  const daysRef = useRef(days);
  daysRef.current = days;

  async function load(targetDays: 7 | 30) {
    const request = ++requestRef.current;
    setLoading(true); setError(null);
    try {
      const [dashboard, taskResponse, foundationResult, insightsResult] = await Promise.all([
        getHomeDashboard(targetDays),
        listCreationTasks(),
        getProductionFoundation().catch(() => null),
        getMaterialLibraryInsights().catch(() => null),
      ]);
      if (request !== requestRef.current) return;
      setData(dashboard);
      setTasks(taskResponse.items ?? []);
      setFoundation(foundationResult);
      setInsights(insightsResult);
      setLoading(false);
    } catch (cause) {
      if (request !== requestRef.current) return;
      setError(cause instanceof Error ? cause.message : "首页数据读取失败。");
      setLoading(false);
    }
  }

  // 跨标签页写入或切回窗口：令牌可能已经变了，而 whoami 收敛是异步的，
  // 所以先作废在途请求并清空当前身份的私有内容，再让既有钩子重新收敛。
  useEffect(() => {
    const apply = () => {
      requestRef.current += 1;
      setData(null); setTasks([]); setFoundation(null); setInsights(null);
      identity.refresh();
    };
    window.addEventListener("storage", apply);
    window.addEventListener("focus", apply);
    return () => {
      window.removeEventListener("storage", apply);
      window.removeEventListener("focus", apply);
    };
  }, [identity.refresh]);

  const previousIdentity = useRef<string | null>(null);
  useEffect(() => {
    if (previousIdentity.current !== null && previousIdentity.current !== identityKey) {
      // 换身份：让在途请求全部作废，旧身份的私有标题不得留在页面上。
      requestRef.current += 1;
      setData(null); setTasks([]); setFoundation(null); setInsights(null);
    }
    previousIdentity.current = identityKey;
  }, [identityKey]);

  // 身份收敛前不发请求：首帧匿名请求会把不该给这一身份的私有内容取回来。
  useEffect(() => {
    if (!identity.ready) return;
    void load(days);
  }, [days, identityKey, identity.ready]);

  const live: HomeLiveState = {
    data, loading, error, days,
    onDays: setDays,
    // 重试用当前选择的天数，不回到首次的 7 天。
    onRetry: () => void load(daysRef.current),
  };

  if (loading && !data) {
    return <div className={styles.home}><p className={styles.cardEmpty} role="status">正在读取整机概览…</p></div>;
  }
  if (error && !data) {
    return <div className={styles.home}>
      <div className={styles.readiness} role="alert"><strong>{error}</strong><button onClick={live.onRetry} type="button">重新读取</button></div>
    </div>;
  }
  return <HomeWorkspace foundation={foundation} insights={insights} live={live} tasks={tasks} />;
}

