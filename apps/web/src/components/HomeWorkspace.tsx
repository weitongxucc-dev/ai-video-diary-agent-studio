"use client";
import { WorkspaceVideo, WorkspaceAudio } from "./workbench/WorkspaceMedia";

import Link from "next/link";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { ArrowRight, ChevronRight, Columns3, Info, List, LoaderCircle, Plus, Search, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { creationTaskFailureReason, creationTaskRepairAction, creationTaskShortStatus, creationTaskStatusPresentation } from "@/lib/creation-task-status";
import { CreationTaskStageIcon } from "@/components/CreationTaskStageIcon";
import { getHomeDashboard, getMaterialLibraryInsights, getProductionFoundation, getProductionMaterials, listCreationTasks } from "@/lib/api";
import { referenceAssistantMaterial } from "@/lib/assistant-reference";
import { readLocalMemberToken } from "@/lib/local-member-auth";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import type { HomeAggregation } from "@/lib/types";
import type { CreationTask, MaterialAsset, MaterialLibraryInsights, ProductionFoundation } from "@/lib/types";
import styles from "./HomeWorkspace.module.css";

type TrendPoint = { date: string; count: number };

function taskName(name: string) {
  return name.replace(/\bb-?roll\b/gi, "补充画面").replace(/\bbatch\b/gi, "创作任务")
    .replace(/\bepisode\b/gi, "内容").replace(/\brecipe\b/gi, "制作方案").replace(/\bprovider\b/gi, "服务");
}
function tagLabel(tag: string) {
  return tag.replaceAll("a_roll", "人物主镜头").replaceAll("b_roll", "补充画面");
}
function readinessRepair(code: string) {
  switch (code) {
    case "versioned_voice_authorization_missing":
    case "voice_reference_missing":
    case "voice_reference_unavailable":
      return { impact: "影响配音生成；可以先讨论目标和编写文案。", href: "/settings?section=voices", label: "添加或检查声音" };
    case "voice_model_not_ready":
      return { impact: "影响配音生成；可以先讨论目标和编写文案。", href: "/settings?section=paths", label: "检查本地模型" };
    case "source_pool_empty":
      return { impact: "影响自动选题的来源范围。", href: "/sources", label: "管理选题来源" };
    case "approved_materials_empty":
      return { impact: "影响后续画面选择与剪辑。", href: "/library", label: "补充或检查素材" };
    default: return null;
  }
}
export function TrendChart({ values }: { values: TrendPoint[] }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useEffect(() => {
    if (!chartRef.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, [values.length]);
  if (!values.length) return <p className={styles.trendEmptyText}>接收趋势暂无数据</p>;
  const peak = Math.max(...values.map(point => point.count));
  const maximum = Math.max(1, ...values.map((point) => point.count));
  const x = (index: number) => values.length === 1 ? width / 2 : 24 + index * (width - 40) / (values.length - 1);
  const y = (count: number) => 138 - count / maximum * 116;
  const points = values.map((point, index) => [x(index), y(point.count)].join(",")).join(" ");
  return <div className={styles.chart} ref={chartRef}>
    {peak > 0 ? <><div className={styles.chartAxis} aria-hidden><span>{peak}</span><span>0</span></div>
    <svg aria-label="素材接收趋势" preserveAspectRatio="none" role="img" viewBox={`0 0 ${width} 160`}>
      {[22,80,138].map(y => <path key={y} d={`M24 ${y}H${width - 16}`} fill="none" stroke="var(--border-1)" vectorEffect="non-scaling-stroke" />)}
      {values.length > 1 ? <polyline fill="none" points={points} stroke="var(--accent)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null}
      {values.map((point, index) => <g key={point.date}><circle aria-label={point.date + " 接收 " + point.count + " 条"} cx={x(index)} cy={y(point.count)} fill="var(--accent)" r={values.length > 7 ? 1.5 : 2}><title>{point.date + "：" + point.count + " 条素材"}</title></circle></g>)}
    </svg></> : <p className={styles.trendEmptyText}>这段时间没有素材接收记录</p>}
    <div className={styles.chartLabels}><time>{values[0].date}</time><time>{values[values.length - 1].date}</time></div>
  </div>;
}
type Overview = "trend" | "tags" | "device" | null;
type TaskFilter = "all" | CreationTask["stage_status"];
type HomeTaskStatus = CreationTask["stage_status"] | "needs_action";
type HomeTaskItem = {
  id: string; title: string; stage: string; status: HomeTaskStatus;
  reason: string | null; rawReason: string | null; repair: string | null; updatedAt: string; outputReady: boolean;
  task?: CreationTask;
};

function mergeHomeTasks(tasks: CreationTask[], home: HomeAggregation | null): HomeTaskItem[] {
  const items = new Map<string, HomeTaskItem>();
  home?.entries.works.forEach(work => items.set(work.task_id, {
    id: work.task_id, title: work.title, stage: "review", status: "completed", reason: null, rawReason: null,
    repair: null, updatedAt: work.updated_at, outputReady: true,
  }));
  home?.entries.queue.forEach(entry => items.set(entry.task_id, {
    id: entry.task_id, title: entry.title, stage: entry.stage,
    status: entry.status === "created" ? "not_started" : entry.status === "处理中" ? "running" : entry.status as CreationTask["stage_status"],
    reason: null, rawReason: null, repair: null, updatedAt: entry.updated_at,
    outputReady: items.get(entry.task_id)?.outputReady ?? false,
  }));
  home?.entries.todos.forEach(todo => items.set(todo.task_id, {
    id: todo.task_id, title: todo.title, stage: todo.stage, status: "needs_action",
    reason: creationTaskFailureReason(todo.reason, todo.stage), rawReason: todo.reason, repair: todo.repair_action,
    updatedAt: items.get(todo.task_id)?.updatedAt ?? "", outputReady: items.get(todo.task_id)?.outputReady ?? false,
  }));
  // The full task is the current stage fact. A previous output never overrides a new failure or running stage.
  tasks.forEach(task => {
    items.set(task.id, {
      id: task.id, title: task.name, stage: task.stage,
      status: task.status === "created" ? "not_started" : task.stage_status,
      reason: creationTaskFailureReason(task.stage_reason, task.stage),
      rawReason: task.stage_reason,
      repair: task.stage_repair_action,
      updatedAt: task.updated_at, outputReady: task.output_ready, task,
    });
  });
  return [...items.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
function itemPresentation(item: HomeTaskItem) {
  if (item.status === "needs_action") return { stageLabel: `${homeStageLabel(item.stage)}：待处理`, label: "待处理" };
  return homeStageKeys.has(item.stage)
    ? creationTaskStatusPresentation(item.status, item.stage as CreationTask["stage"])
    : { stageLabel: homeStageLabel(item.stage), label: homeStatusLabel(item.status) };
}
function taskNextAction(item: HomeTaskItem) {
  if (item.status === "running") return "查看制作进度";
  if (item.task?.can_revise_script) return "修改文案";
  if (item.status === "completed" && !item.outputReady) return "查看已完成步骤（尚无成片）";
  if (item.outputReady && item.status !== "failed") return "检查成片";
  return ({ topic: "查看选题", script: "检查文案", narration: "检查配音条件", montage: "查看视频剪辑", review: "检查成片" } as Record<string, string>)[item.stage] ?? "打开任务";
}
function MaterialPreview({ asset, controls = false }: { asset: MaterialAsset; controls?: boolean }) {
  const [failed, setFailed] = useState(false);
  const url = `/api/production/materials/${encodeURIComponent(asset.id)}/media`;
  if (failed) return <span className={styles.mediaUnavailable}>预览暂不可用</span>;
  if (asset.media_type === "audio") return controls
    ? <WorkspaceAudio aria-label="原素材预览" controls preload="metadata" src={url} onError={() => setFailed(true)} />
    : <Volume2 aria-hidden size={20} />;
  if (asset.media_type === "image") return <img alt={controls ? `原素材预览：${asset.file_name}` : ""} src={url} loading="lazy" onError={() => setFailed(true)} />;
  return <WorkspaceVideo aria-label={controls ? "原素材预览" : undefined} aria-hidden={!controls} controls={controls} muted={!controls} playsInline preload="metadata" src={url} onError={() => setFailed(true)} />;
}

export function HomeWorkspace({ tasks, foundation, insights, live, recentAssets = null }: {
  tasks: CreationTask[]; foundation: ProductionFoundation | null;
  insights?: MaterialLibraryInsights | null; live?: HomeLiveState; recentAssets?: MaterialAsset[] | null;
}) {
  const searchRef = useRef<HTMLInputElement>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<MaterialAsset | null>(null);
  const [factsOpen, setFactsOpen] = useState(false);
  const [range, setRange] = useState<7 | 30>(live?.days ?? 7);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<TaskFilter>("all");
  const [view, setView] = useState<"board" | "list">("board");
  const [activeOverview, setActiveOverview] = useState<Exclude<Overview, null>>("trend");
  const [overviewInfo, setOverviewInfo] = useState<Overview>(null);
  const [preparationOpen, setPreparationOpen] = useState(false);
  const [taskPage, setTaskPage] = useState(0);
  const home = live?.data ?? null;
  const items = mergeHomeTasks(tasks, home);
  const filteredTasks = items.filter(item => taskName(item.title).toLocaleLowerCase().includes(query.toLocaleLowerCase()) && (filter === "all" || item.status === filter));
  const pageCount = Math.max(1, Math.ceil(filteredTasks.length / 25));
  const currentPage = Math.min(taskPage, pageCount - 1);
  const pageTasks = filteredTasks.slice(currentPage * 25, (currentPage + 1) * 25);
  const groups = [
    { title: "待处理", items: filteredTasks.filter(item => !["running", "completed"].includes(item.status)), empty: "没有待处理事项" },
    { title: "进行中", items: filteredTasks.filter(item => item.status === "running"), empty: "没有正在制作的任务" },
    { title: "最近完成", items: filteredTasks.filter(item => item.status === "completed"), empty: "完成的任务会显示在这里" },
  ];
  const selectedTask = items.find(item => item.id === selectedTaskId);
  const displayedCounts = home?.counts ?? [];
  const hasCounts = displayedCounts.some(item => item.value > 0);
  const trendValues = home?.trend.points ?? [];
  const emptyWorkspace = Boolean(home && !live?.loading && !live?.error
    && ["visible_assets", "analysis_ready", "analysis_pending", "knowledge_sources", "voices", "tasks", "works"]
      .every(key => home.counts.some(fact => fact.key === key && fact.value === 0))
    && items.length === 0 && !trendValues.some(point => point.count > 0));
  const dependencyBlockers = foundation?.readiness.blockers.filter(blocker => blocker.code !== "brand_profile_missing") ?? [];
  const realTags = insights ? insights.tags.filter(item => item.asset_count > 0).sort((a, b) => b.asset_count - a.asset_count || a.tag.localeCompare(b.tag)) : [];
  const featuredTags = realTags.filter(item => item.tag.length <= 18).slice(0, 12);
  const featuredTagSet = new Set(featuredTags.map(item => item.tag));
  const moreTags = realTags.filter(item => !featuredTagSet.has(item.tag));
  const liveDays = live?.days;
  useEffect(() => { if (liveDays !== undefined) setRange(liveDays); }, [liveDays]);
  function setOverview(next: Overview) {
    setActiveOverview(next ?? "trend");
  }
  const maxTagCount = Math.max(1, ...featuredTags.map(item => item.asset_count));
  function cloudWord(item: MaterialLibraryInsights["tags"][number]) {
    const weight = Math.log1p(item.asset_count) / Math.log1p(Math.max(12, maxTagCount));
    return <Link href={"/library?mode=manage&tag=" + encodeURIComponent(item.tag)} key={item.tag} data-frequency={item.asset_count} title={`${tagLabel(item.tag)} · ${item.asset_count} 条素材`} style={{fontSize:12 + 18 * weight,fontWeight:weight > .65 ? 500 : 400,marginInline:2 + 5 * weight}}>{tagLabel(item.tag)}<sup>{item.asset_count}</sup></Link>;
  }
  function taskRow(item: HomeTaskItem) {
    return <button className={view === "board" ? styles.taskCard : styles.taskRow} key={item.id} onClick={() => setSelectedTaskId(item.id)} type="button" title={taskName(item.title)}>
      <span className={styles.taskName}><CreationTaskStageIcon stage={item.stage} className={styles.taskStatusIcon} /><strong>{taskName(item.title)}</strong><ChevronRight aria-hidden size={14} /></span>
      <span className={styles.taskMeta}><span className={styles.taskState} data-status={item.status}>{item.status === "running" ? <LoaderCircle aria-hidden="true" className="is-spinning" size={12} strokeWidth={1.7} /> : null}{creationTaskShortStatus(item.status, item.stage)}</span>
      <span className={styles.taskNext}>{taskNextAction(item)}</span></span>
    </button>;
  }
  const trendRangePicker = <div aria-label="趋势范围" className={styles.range}>{([7, 30] as const).map(days => <button aria-pressed={range === days} key={days} onClick={() => { setRange(days); live?.onDays(days); }} type="button">{days} 天</button>)}</div>;
  if (live?.loading && !home) return <div className={styles.home}><p className={styles.cardEmpty} role="status">正在读取整机概览…</p></div>;
  if (live?.error && !home) return <div className={styles.home}><div className={styles.readiness} role="alert"><strong>首页数据读取失败</strong><button onClick={live.onRetry} type="button">重新读取</button><details><summary>查看详情</summary><p>{live.error}</p></details></div></div>;

  return <div className={styles.home}>
    <div className={styles.toolbar}>
      <label className={styles.search} data-input-group><Search aria-hidden size={16} /><input aria-label="搜索任务" ref={searchRef} value={query} placeholder="搜索任务" onChange={event => { setQuery(event.target.value); setTaskPage(0); }} />{query ? <button aria-label="清空任务搜索" title="清空搜索" onClick={() => { setQuery(""); setTaskPage(0); searchRef.current?.focus(); }} type="button"><X aria-hidden size={14} /></button> : null}</label>
      <AppSelect aria-label="任务状态" value={filter} onValueChange={value => { setFilter(value as TaskFilter); setTaskPage(0); }}><AppSelectOption value="all">全部状态</AppSelectOption><AppSelectOption value="not_started">等待开始</AppSelectOption><AppSelectOption value="waiting_for_user">等你确认</AppSelectOption><AppSelectOption value="running">正在制作</AppSelectOption><AppSelectOption value="completed">已完成步骤</AppSelectOption><AppSelectOption value="failed">需要处理</AppSelectOption></AppSelect>
      <button aria-label={view === "board" ? "切换为列表" : "切换为看板"} title={view === "board" ? "切换为列表" : "切换为看板"} className={styles.viewButton} onClick={() => setView(view === "board" ? "list" : "board")} type="button">{view === "board" ? <List aria-hidden size={16} /> : <Columns3 aria-hidden size={16} />}</button>
      <Link className={styles.newTask} href="/?new=1"><Plus aria-hidden size={16} />新建视频</Link>
    </div>
    <div className={styles.content}>
      <section aria-label="创作任务看板"><div className={styles.sectionHeading}><h2>创作任务</h2><small>{items.length} 个视频任务</small></div>
        <div id="home-task-body">
          {emptyWorkspace ? <section aria-label="开始创作" className={styles.startHere}><h3>从一个视频目标开始</h3><p>当前可见范围还没有素材、资料、声音或创作任务。说说你想做的视频，也可以先导入画面。</p><div><Link href="/?new=1">说说视频目标 <ArrowRight aria-hidden size={14} /></Link><Link href="/library?mode=manage">去素材库导入</Link></div></section>
            : !filteredTasks.length ? <div className={styles.empty}><p>{query || filter !== "all" ? "没有匹配的任务" : "还没有创作任务"}</p>{query || filter !== "all" ? <button onClick={() => { setQuery(""); setFilter("all"); searchRef.current?.focus(); }} type="button">清除筛选</button> : <Link href="/production">新建视频</Link>}</div>
              : view === "board" ? <div className={styles.taskGroups}>{groups.map(group => <section className={styles.taskGroup} aria-label={group.title} key={group.title}><h3>{group.title}<span>{group.items.length}</span></h3><div className={styles.taskColumn} tabIndex={group.items.length ? 0 : undefined} aria-label={`${group.title}任务滚动列表`}>{group.items.length ? group.items.map(taskRow) : <p className={styles.taskGroupEmpty}>{group.empty}</p>}</div></section>)}</div>
                : <><div className={styles.taskList} aria-label="创作任务列表">{pageTasks.map(taskRow)}</div>{pageCount > 1 ? <nav className={styles.pagination} aria-label="已读取任务分页"><button disabled={currentPage === 0} onClick={() => setTaskPage(currentPage - 1)} type="button">上一页</button><span>{currentPage + 1} / {pageCount} · 已读 {filteredTasks.length} 项</span><button disabled={currentPage === pageCount - 1} onClick={() => setTaskPage(currentPage + 1)} type="button">下一页</button></nav> : null}</>}
        </div>
      </section>
      <section aria-label="最近素材"><div className={styles.sectionHeading}><h2>最近素材</h2><Link href="/library?mode=manage">素材库 <ArrowRight aria-hidden size={14} /></Link></div>
        <div id="home-recent-body">
          {recentAssets?.length ? <div className={styles.recentGrid}>{recentAssets.slice(0, 3).map(asset => <button className={styles.materialRow} key={asset.id} type="button" onClick={() => setSelectedAsset(asset)} title={asset.file_name}><span className={styles.materialPreview}><MaterialPreview asset={asset} /></span><span><strong>{asset.file_name}</strong><small>{asset.user_tags.slice(0, 2).join(" · ") || (asset.media_type === "audio" ? "音频" : asset.media_type === "image" ? "图片" : "视频")}</small><small>最近更新 · {new Date(asset.updated_at).toLocaleDateString("zh-CN")}</small></span></button>)}</div>
            : <p className={styles.compactEmpty}>{recentAssets === null ? "暂时无法读取最近素材" : "还没有素材，可以去素材库导入"}{recentAssets === null && live ? <button onClick={live.onRetry} type="button">重新读取</button> : null}</p>}
          {live?.materialsError ? <div className={styles.tagReadError} role="alert"><span>最近素材暂未更新</span><details className={styles.metaDetails}><summary>查看素材读取详情</summary><p>{live.materialsError}</p></details></div> : null}
        </div>
      </section>
      {!emptyWorkspace && home ? <div className={styles.resourceLine}><span>可用内容</span>{[["素材", "visible_assets"], ["资料", "knowledge_sources"], ["声音", "voices"]].map(([label, key]) => <button key={key} onClick={() => setFactsOpen(true)} type="button"><strong>{home.counts.find(fact => fact.key === key)?.value ?? "未知"}</strong>{label}</button>)}<button aria-label="查看内容统计范围" title="查看内容统计范围" onClick={() => setFactsOpen(true)} type="button"><Info aria-hidden size={16} /></button></div> : null}
      <section aria-label="内容与设备概览" className={styles.overviews}>
        <div className={styles.overviewTabs} role="tablist" aria-label="首页概览" aria-orientation="horizontal">
          {([['trend','素材接收趋势'],['tags','内容标签与行业'],['device','设备与存储']] as const).map(([key,title]) => <button key={key} id={`home-${key}-tab`} role="tab" aria-selected={activeOverview === key} aria-controls={`home-${key}-body`} tabIndex={activeOverview === key ? 0 : -1} onClick={() => setOverview(key)} onKeyDown={event => {
            if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
            event.preventDefault();
            const tabs = Array.from(event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
            const current = tabs.indexOf(event.currentTarget);
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            tabs[next].focus(); setOverview((['trend','tags','device'] as const)[next]);
          }}>{title}</button>)}
        </div>
        {activeOverview ? <div className={styles.cardHeading}>
          <div className={styles.cardMeta}>
            {activeOverview === 'trend' ? <><time dateTime={home?.trend.meta.as_of}>{home ? `更新 ${new Date(home.trend.meta.as_of).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false})}` : '更新时间未知'}</time>{trendRangePicker}</> : null}
            <button className={styles.infoButton} aria-label={activeOverview === 'trend' ? '趋势统计口径' : activeOverview === 'tags' ? '标签统计口径' : '设备范围与诊断'} title="范围与数据来源" onClick={() => setOverviewInfo(activeOverview)} type="button"><Info aria-hidden size={15} /></button>
          </div>
        </div> : null}
        <div id="home-trend-body" role="tabpanel" aria-labelledby="home-trend-tab" className={styles.overviewBody} hidden={activeOverview !== 'trend'}>
          {home ? <TrendChart values={trendValues} /> : <p className={styles.compactEmpty}>暂未读取接收趋势</p>}
        </div>
        <div id="home-tags-body" role="tabpanel" aria-labelledby="home-tags-tab" className={styles.overviewBody} hidden={activeOverview !== 'tags'}>
          <div className={styles.cloudField} aria-label="按真实素材频数排列的词云"><div className={styles.cloudCore}>{featuredTags.slice(0,1).map(cloudWord)}</div><div className={`${styles.cloudRow} ${styles.cloudTop}`}>{featuredTags.filter((_,index) => index > 0 && index % 2 === 1).map(cloudWord)}</div><div className={`${styles.cloudRow} ${styles.cloudBottom}`}>{featuredTags.filter((_,index) => index > 0 && index % 2 === 0).map(cloudWord)}</div></div>
          {!insights ? <div className={styles.tagReadError} role="alert"><span>暂时无法读取内容标签</span>{live ? <button onClick={live.onRetry} type="button">重新读取</button> : null}</div> : realTags.length === 0 ? <p className={styles.compactEmpty}>素材还没有可用标签</p> : null}
          {moreTags.length ? <details className={styles.moreTags}><summary>更多标签（{moreTags.length}）</summary><ul>{moreTags.map(item => <li key={item.tag}><Link href={'/library?mode=manage&tag=' + encodeURIComponent(item.tag)}>{tagLabel(item.tag)}</Link><small>{item.asset_count} 条素材</small></li>)}</ul></details> : null}
        </div>
        <div id="home-device-body" role="tabpanel" aria-labelledby="home-device-tab" className={styles.overviewBody} hidden={activeOverview !== 'device'}>
          {home ? <><dl className={styles.deviceMetrics}>
            <div><dt>处理中</dt><dd>{home.device_queue.running ?? '未知'}</dd></div><div><dt>排队</dt><dd>{home.device_queue.queued ?? '未知'}</dd></div>
            <div><dt>数据盘</dt><dd>{directoryStateLabel(home.storage.directory_state)}</dd></div><div><dt>可用空间</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.free_bytes) : '未知'}</dd></div>
          </dl>{home.storage.capacity && home.storage.capacity.total_bytes > 0 ? <progress aria-label="数据盘已用容量" value={home.storage.capacity.used_bytes} max={home.storage.capacity.total_bytes} /> : null}<Link href="/settings?section=paths">检查保存位置 <ArrowRight aria-hidden size={14} /></Link></> : <p className={styles.compactEmpty}>暂未读取设备与存储信息</p>}
        </div>
      </section>
      {!foundation ? <div className={styles.readiness}><strong>暂时连不上制作服务</strong><Link href="/health">查看原因</Link></div> : dependencyBlockers.length && !foundation.readiness.ready ? <div className={styles.preparation} role="status"><span>制作前还有 {dependencyBlockers.length} 项需要准备。</span><button onClick={() => setPreparationOpen(true)} type="button">查看准备事项<ArrowRight aria-hidden size={14} /></button></div> : null}
      {live?.loading ? <p className={styles.compactEmpty} role="status">正在更新首页数据…</p> : null}
      {live?.error ? <div className={styles.readiness} role="alert"><strong>部分首页数据读取失败</strong><button onClick={live.onRetry} type="button">重新读取</button><details><summary>查看详情</summary><p>{live.error}</p></details></div> : null}
    </div>
    {overviewInfo ? <HomeCardDialog kind="detail" title={overviewInfo === 'trend' ? '趋势统计口径' : overviewInfo === 'tags' ? '标签统计口径' : '设备范围与诊断'} onClose={() => setOverviewInfo(null)}><div className={styles.infoDetails}>
      {overviewInfo === 'trend' && home ? <><p>按接收日统计 · 近 {home.trend.days} 天 · 单位：条。</p><p>{scopeLabel(home.trend.meta.scope)} · {sourceLabel(home.trend.meta.source)}</p><p>更新于 <time dateTime={home.trend.meta.as_of}>{new Date(home.trend.meta.as_of).toLocaleString('zh-CN')}</time></p></> : null}
      {overviewInfo === 'tags' ? <><p>字号依据当前可见素材的真实出现频数；点击词语可查看对应素材。</p><p>标签接口未提供统计更新时间。暂无可信行业分类。</p>{home?.unknown.filter(item => item.key.startsWith('materials.')).map(item => <p key={item.key}>{unknownKeyLabel(item.key)}：{item.reason}</p>)}</> : null}
      {overviewInfo === 'device' && home ? <><p>{Object.keys(home.device_queue.counts).length ? Object.entries(home.device_queue.counts).map(([key,value]) => `${runtimeStateLabel(key)} ${value}`).join(' · ') : '状态分布未读到'}</p><p>作业记录 {home.device_queue.records_total ?? '未知'} · {scopeLabel(home.device_queue.meta.scope)} · {sourceLabel(home.device_queue.meta.source)} · 更新于 {new Date(home.device_queue.meta.as_of).toLocaleString('zh-CN')}</p><dl className={styles.homeFacts}><div><dt>已用</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.used_bytes) : '未知'}</dd></div><div><dt>容量</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.total_bytes) : '未知'}</dd></div><div><dt>可用</dt><dd>{home.storage.capacity ? formatBytes(home.storage.capacity.free_bytes) : '未知'}</dd></div><div><dt>数据盘</dt><dd>{directoryStateLabel(home.storage.directory_state)}{home.storage.bound_to_another_workspace ? '（绑定在其他空间）' : ''}{home.storage.configured_path_changed ? '（保存位置已变更）' : ''} · {home.storage.bound_to_current_workspace ? '已绑定当前空间' : '未绑定当前空间'}</dd></div><div><dt>统计口径</dt><dd>{scopeLabel(home.storage.meta.scope)} · {sourceLabel(home.storage.meta.source)} · 更新于 {new Date(home.storage.meta.as_of).toLocaleString('zh-CN')}</dd></div></dl>{home.unknown.filter(item => !item.key.startsWith('materials.')).map(item => <p key={item.key}>{unknownKeyLabel(item.key)}：{item.reason}</p>)}</> : null}
    </div></HomeCardDialog> : null}
    {preparationOpen ? <HomeCardDialog kind="detail" title="制作准备事项" onClose={() => setPreparationOpen(false)}><section aria-label="制作准备事项">{dependencyBlockers.map((blocker,index) => { const repair = readinessRepair(blocker.code); return <article className={styles.preparationRow} key={`${blocker.code}-${index}`}><div><p>{blocker.message}</p>{repair ? <small>{repair.impact}</small> : null}</div>{repair ? <Link href={repair.href}>{repair.label}<ArrowRight aria-hidden size={14} /></Link> : <Link href="/health">检查运行情况<ArrowRight aria-hidden size={14} /></Link>}</article>; })}<details className={styles.metaDetails}><summary>后台准备记录</summary>{dependencyBlockers.map((blocker,index) => <p key={`${blocker.code}-${index}`}>{blocker.code}：{blocker.message}</p>)}</details></section></HomeCardDialog> : null}
    {factsOpen ? <HomeCardDialog kind="detail" title="内容总览" onClose={() => setFactsOpen(false)}><section aria-label="工作空间七项概览" className={styles.metrics}>{displayedCounts.map(item => <div className={styles.metric} key={item.label}><strong>{item.value}</strong><small>{item.label}</small></div>)}</section>{!hasCounts ? <p className={styles.compactEmpty}>当前已读取的统计暂无记录。</p> : null}{home ? <><details className={styles.metaDetails}><summary>每个数字的口径与更新时间</summary><dl className={styles.homeFacts}>{home.counts.map(entry => <div key={entry.key}><dt>{entry.label}</dt><dd>{entry.value} · {scopeLabel(entry.meta.scope)} · {sourceLabel(entry.meta.source)} · {new Date(entry.meta.as_of).toLocaleString("zh-CN")}</dd></div>)}</dl></details><p className={styles.compactEmpty}>{scopeLabel(home.entries.meta.scope)} · {sourceLabel(home.entries.meta.source)} · 更新于 {new Date(home.entries.meta.as_of).toLocaleString("zh-CN")}</p>{home.unknown.length ? <details className={styles.metaDetails}><summary>暂时没有数据的统计（{home.unknown.length}）</summary><dl className={styles.homeFacts}>{home.unknown.map(item => <div key={item.key}><dt>{unknownKeyLabel(item.key)}</dt><dd>{item.reason}</dd></div>)}</dl></details> : null}</> : null}</HomeCardDialog> : null}
    {selectedTask ? <HomeCardDialog kind="detail" onClose={() => setSelectedTaskId(null)} title={taskName(selectedTask.title)} footer={<Link className={styles.taskOpen} href={"/tasks/" + encodeURIComponent(selectedTask.id)}>{taskNextAction(selectedTask)}<ArrowRight aria-hidden size={15} /></Link>}><div className={styles.taskDetail}>
      <span className={styles.taskState}>{itemPresentation(selectedTask).stageLabel}</span>
      {selectedTask.reason ? <p>{selectedTask.reason}</p> : null}
      {selectedTask.outputReady ? <p>已有本地成片可查看</p> : null}
      <details className={styles.metaDetails}><summary>原因与任务记录</summary><dl className={styles.homeFacts}>
        {selectedTask.rawReason ? <div><dt>后台原始原因</dt><dd>{selectedTask.rawReason}</dd></div> : null}
        {selectedTask.task?.stage_impact ? <div><dt>本次影响</dt><dd>{selectedTask.task.stage_impact}</dd></div> : null}
        {selectedTask.repair ? <div><dt>处理建议</dt><dd>{creationTaskRepairAction(selectedTask.repair)}</dd></div> : null}
        {selectedTask.repair ? <div><dt>后台处理原文</dt><dd>{selectedTask.repair}</dd></div> : null}
        {selectedTask.updatedAt ? <div><dt>最近更新</dt><dd><time dateTime={selectedTask.updatedAt}>{new Date(selectedTask.updatedAt).toLocaleString("zh-CN")}</time></dd></div> : null}
      </dl></details>
    </div></HomeCardDialog> : null}
    {selectedAsset ? <HomeCardDialog kind="detail" title={selectedAsset.file_name} onClose={() => setSelectedAsset(null)} footer={<button type="button" onClick={() => { referenceAssistantMaterial({ selection: { scope: "asset", asset_id: selectedAsset.id, expected_updated_at: selectedAsset.updated_at }, label: selectedAsset.file_name }); setSelectedAsset(null); }}>引用到对话</button>}><div className={styles.assetDetail}><MaterialPreview asset={selectedAsset} controls /><p>{selectedAsset.user_description || "尚未填写素材说明"}</p><p>{selectedAsset.user_tags.join(" · ")}</p><Link href="/library?mode=manage">打开素材库 <ArrowRight aria-hidden size={14} /></Link></div></HomeCardDialog> : null}

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
  materialsError?: string | null;
};

// 口径与来源的中文说明：不向用户铺 state 键或源码路径。
const scopeLabels: Record<string, string> = {
  caller_visible: "当前身份可见",
  local_diagnostic: "本机受控诊断",
  workspace: "当前工作区",
  device_anonymous: "整机匿名汇总",
  host: "整机",
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
// 服务端 source 是具体取数函数串（内部实现文本）。按其中出现的真实取数对象
// 给中文来源；匹配不上就说「本机记录」，不猜对应关系。
function sourceLabel(source: string) {
  if (source.includes("list_assets")) return "素材库（按既有可见性判据统计）";
  if (source.includes("list_knowledge_items")) return "知识库（当前身份可读的条目）";
  if (source.includes("list_versioned_voice_profiles")) return "声音库（工作区级计数，不含声纹名称）";
  if (source.includes("output_ready")) return "创作任务中已产出本地成片的数量";
  if (source.includes("list_creation_tasks")) return "创作任务（当前身份可见）";
  if (source.includes("disk_usage") || source.includes("directory_state")) return "数据盘目录状态与容量";
  if (source.includes("job_runs") || source.includes("job.")) return "本机作业记录";
  if (source.includes("created_at")) return "当前身份可读素材的接收日期";
  return "本机记录";
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
const homeStatusKeys = new Set(["created", "running", "waiting_for_user", "completed", "failed"]);
// 首页条目里的 stage/status 复用既有中文映射；服务端已经给中文时直接用它的说法。
function homeStageLabel(stage: string) {
  return homeStageKeys.has(stage) ? creationTaskStatusPresentation("running", stage as CreationTask["stage"]).stageLabel.split("：")[0] : stage;
}
function homeStatusLabel(status: string) {
  // 创建后的任务还没开始执行：按任务自身状态说「等待开始」，
  // 不能拿时间线的 not_started 冒充。
  if (status === "created" || status === "not_started") return "等待开始";
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
  const [recentAssets, setRecentAssets] = useState<MaterialAsset[] | null>(null);
  const [materialsError, setMaterialsError] = useState<string | null>(null);
  const [days, setDays] = useState<7 | 30>(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 身份一律走既有钩子：同标签加入空间、跨标签页切换、切回窗口都通过它收敛，
  // 页面不自己读 localStorage，避免两处令牌来源互相遮蔽。
  const identity = useLocalMemberIdentity();
  const identityKey = identity.token ?? "";
  const identityToken = identity.token;
  const refreshIdentity = identity.refresh;
  // 只有最后一次请求的结果可以提交：切身份或切天数之后，之前的响应一律丢弃，
  // 不让旧身份的私有标题或旧天数的数字落到当前页面上。
  const requestRef = useRef(0);
  const daysRef = useRef(days);
  daysRef.current = days;

  async function load(targetDays: 7 | 30, onlyTrend = false) {
    const request = ++requestRef.current;
    setLoading(true); setError(null);
    if (onlyTrend) {
      try {
        const dashboard = await getHomeDashboard(targetDays);
        if (request !== requestRef.current) return;
        setData(dashboard);
      } catch (cause) {
        if (request !== requestRef.current) return;
        setError(cause instanceof Error ? cause.message : "接收趋势读取失败。");
      }
      if (request === requestRef.current) setLoading(false);
      return;
    }
    const [dashboard, taskResponse, foundationResult, insightsResult, materialsResult] = await Promise.allSettled([
      getHomeDashboard(targetDays), listCreationTasks(), getProductionFoundation(),
      getMaterialLibraryInsights(), getProductionMaterials({ limit: 3, offset: 0 }),
    ]);
    if (request !== requestRef.current) return;
    if (dashboard.status === "fulfilled") setData(dashboard.value);
    else setError(dashboard.reason instanceof Error ? dashboard.reason.message : "首页数据读取失败。");
    if (taskResponse.status === "fulfilled") setTasks(taskResponse.value.items);
    else if (dashboard.status === "fulfilled") setError(taskResponse.reason instanceof Error ? taskResponse.reason.message : "创作任务读取失败。");
    setFoundation(foundationResult.status === "fulfilled" ? foundationResult.value : null);
    setInsights(insightsResult.status === "fulfilled" ? insightsResult.value : null);
    if (materialsResult.status === "fulfilled") {
      setRecentAssets(materialsResult.value.assets); setMaterialsError(null);
    } else {
      setMaterialsError(materialsResult.reason instanceof Error ? materialsResult.reason.message : "最近素材读取失败。");
    }
    setLoading(false);
  }

  // 跨标签页写入或切回窗口：只有本地成员令牌真的变了才算换身份。
  // 普通切回窗口令牌不变，不能把首页清空后再也不恢复。
  useEffect(() => {
    const apply = () => {
      const stored = readLocalMemberToken();
      if (stored === (identityToken ?? null)) return;
      // 令牌已变：先作废在途请求并清空当前身份的私有内容，再让钩子收敛。
      requestRef.current += 1;
      setData(null); setTasks([]); setFoundation(null); setInsights(null); setRecentAssets(null); setMaterialsError(null);
      setLoading(true); setError(null);
      refreshIdentity();
    };
    window.addEventListener("storage", apply);
    window.addEventListener("focus", apply);
    return () => {
      window.removeEventListener("storage", apply);
      window.removeEventListener("focus", apply);
    };
  }, [refreshIdentity, identityToken]);

  const previousIdentity = useRef<string | null>(null);
  useEffect(() => {
    if (previousIdentity.current !== null && previousIdentity.current !== identityKey) {
      // 换身份：让在途请求全部作废，旧身份的私有标题不得留在页面上。
      requestRef.current += 1;
      setData(null); setTasks([]); setFoundation(null); setInsights(null); setRecentAssets(null); setMaterialsError(null);
      setLoading(true); setError(null);
    }
    previousIdentity.current = identityKey;
  }, [identityKey]);

  // 身份收敛前不发请求：首帧匿名请求会把不该给这一身份的私有内容取回来。
  useEffect(() => {
    if (!identity.ready) return;
    void load(daysRef.current);
    return () => { requestRef.current += 1; };
  }, [identityKey, identity.ready]);

  useEffect(() => {
    if (!identity.ready) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(daysRef.current);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [identityKey, identity.ready]);

  const live: HomeLiveState = {
    data, loading, error, days,
    materialsError,
    onDays: nextDays => {
      setDays(nextDays);
      void load(nextDays, true);
    },
    // 重试用当前选择的天数，不回到首次的 7 天。
    onRetry: () => void load(daysRef.current),
  };

  if (loading && !data) {
    return <div className={styles.home}><p className={styles.cardEmpty} role="status">正在读取整机概览…</p></div>;
  }
  if (error && !data) {
    return <div className={styles.home}>
      <div className={styles.readiness} role="alert"><strong>首页数据读取失败</strong><details><summary>查看详情</summary><p>{error}</p></details><button onClick={live.onRetry} type="button">重新读取</button></div>
    </div>;
  }
  return <HomeWorkspace foundation={foundation} insights={insights} live={live} tasks={tasks} recentAssets={recentAssets} />;
}
