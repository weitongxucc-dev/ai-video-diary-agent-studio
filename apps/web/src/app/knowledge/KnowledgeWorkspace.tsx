"use client";

import { AlertCircle, ArrowRight, BookOpen, FileText, Link2, LoaderCircle, RefreshCw, Search, Video } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getScriptLibrarySources, listScriptLibraryEntries, reuseScriptLibraryEntry } from "@/lib/api";
import type { ScriptLibraryEntry, ScriptLibrarySource } from "@/lib/types";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import type { KnowledgeItem, KnowledgeItemDetail, ParameterComparison } from "@/lib/source-api";
import { getKnowledgeItem, listParameterComparisons, adjudicateParameterComparison, createParameterComparison, sourceErrorMessage } from "@/lib/source-api";
import styles from "./KnowledgeWorkspace.module.css";

type KnowledgeDisplayItem = KnowledgeItem & {
  exampleCategory?: string;
  exampleSummary?: string;
  exampleParameters?: string;
  exampleIssue?: string;
};
const categories = ["产品知识", "公司文档", "菜单条目", "销售话术"] as const;

function itemIcon(type: string) {
  if (type === "video") return <Video size={21} />;
  if (type === "link") return <Link2 size={21} />;
  if (type === "text") return <BookOpen size={21} />;
  return <FileText size={21} />;
}
function itemType(type: string) {
  if (type === "text") return "文字";
  if (type === "video") return "视频";
  if (type === "file") return "文件";
  if (type === "link") return "链接";
  return type || "未分类格式";
}
function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(value));
}

// 抓取时刻精确到秒：同一天的两版快照（V1/V2）只有这样才分得开。
function formatFetchTime(value: string | null) {
  if (!value) return "无抓取记录";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

export function KnowledgeWorkspace({ items, loading, error, onRefresh, exampleMode = false, exampleLabel }: {
  items: KnowledgeDisplayItem[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  exampleMode?: boolean;
  exampleLabel?: string;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeItemDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [viewVersion, setViewVersion] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId || exampleMode) { setDetail(null); setDetailError(null); setViewVersion(null); return; }
    let active = true;
    setDetail(null); setDetailError(null); setViewVersion(null);
    getKnowledgeItem(selectedId)
      .then(result => { if (active && result.id === selectedId) setDetail(result); })
      .catch(cause => { if (active) setDetailError(cause instanceof Error ? cause.message : "知识详情读取失败"); });
    return () => { active = false; };
  }, [selectedId, exampleMode]);
  // 详情可能晚于列表刷新到达；只展示与当前选中条目一致的详情。
  const shownDetail = detail && detail.id === selectedId ? detail : null;
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("全部");
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const visibleItems = useMemo(() => items.filter((item) => {
    const matchesCategory = category === "全部" || (exampleMode && item.exampleCategory === category);
    const searchable = [item.title, item.body, item.file_reference].filter(Boolean).join(" ").toLocaleLowerCase();
    return matchesCategory && searchable.includes(query.trim().toLocaleLowerCase());
  }), [items, category, query, exampleMode]);
  const inspector = selected ? <KnowledgeInspector detail={shownDetail} detailError={detailError} exampleMode={exampleMode} item={selected} items={items} viewVersion={viewVersion} onViewVersion={setViewVersion} /> : undefined;

  return <AppShell inspector={inspector} inspectorTitle="来源详情">
    <div className={styles.page}>
      {exampleMode ? <div className={styles.exampleBanner}>{exampleLabel ?? "隔离视觉示例"} · 所有名称、分类、摘要和状态均非真实服务数据</div> : null}
      <div className={styles.top}>
        <nav aria-label="知识用途分类" className={styles.categories}>
          {categories.map((name, index) => <button aria-pressed={category === name} className={styles.category} key={name} onClick={() => setCategory(category === name ? "全部" : name)} type="button">
            <span className={styles.categoryIcon}>{[<BookOpen key="a" size={20} />, <FileText key="b" size={20} />, <FileText key="c" size={20} />, <BookOpen key="d" size={20} />][index]}</span>
            <strong>{name}</strong>
            <small>{exampleMode ? items.filter((item) => item.exampleCategory === name).length + " 条示例来源" : "未分类"}</small>
          </button>)}
        </nav>
        <div className={styles.actions}><Link href="/sources">添加内容 <Link2 size={14} /></Link></div>
      </div>
      <div className={styles.searchBar}>
        <label><Search size={16} /><input aria-label="查找来源" onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、正文或文件引用" value={query} /></label>
        <button aria-pressed={category === "全部"} onClick={() => setCategory("全部")} type="button">全部来源</button>
        <button aria-label="刷新知识库" disabled={loading} onClick={onRefresh} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={16} /></button>
      </div>
      {error ? <section className={styles.error} role="alert"><AlertCircle size={17} /><div><strong>{error}</strong><p>本地服务恢复或工作区授权确认后再刷新。</p></div></section> : null}
      <section className={styles.card}>
          <div className={styles.cardHeading}><div><h2>来源封面</h2><small>{loading ? "正在读取" : error ? "读取失败" : category !== "全部" && !exampleMode ? "用途分类尚未接入" : visibleItems.length + " 条" + (exampleMode ? "示例" : "当前工作区") + "结果"} · 选来源查看内容</small></div></div>
          {loading ? <div className={styles.empty}><LoaderCircle className={styles.spinning} size={18} />正在读取知识库…</div>
            : category !== "全部" && !exampleMode ? <div className={styles.empty}>现有知识条目没有用途分类字段，不能按文件格式推断“{category}”。<button onClick={() => setCategory("全部")} type="button">查看全部来源</button></div>
            : visibleItems.length ? <div className={styles.grid}>{visibleItems.map((item) => <button className={styles.cover} data-open-details key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                <span className={styles.coverArt}>{itemIcon(item.content_type)}<small>{itemType(item.content_type)}</small></span>
                <strong>{item.title}</strong><small>{exampleMode ? item.exampleCategory : "未分类"} · 版本 {item.version}</small>
                <span className={styles.coverStatus}>{exampleMode ? item.exampleIssue ?? "示例状态" : "核对状态未接入"}</span>
              </button>)}</div>
            : <div className={styles.empty}>{error ? "无法读取知识库" : items.length ? "没有匹配的来源" : "知识库还没有内容"}{!items.length && !error ? <Link href="/sources">去信息源添加内容</Link> : null}</div>}
      </section>
      {!exampleMode ? <ScriptLibraryPanel /> : null}
      <p className={styles.notWired}>主题关系、参数冲突核对、任务引用、问答和来源召回尚未接入；当前可查看已保存的来源正文。</p>
    </div>
  </AppShell>;
}

function KnowledgeInspector({ item, detail, detailError, exampleMode, viewVersion, onViewVersion, items }: { item: KnowledgeDisplayItem; detail: KnowledgeItemDetail | null; detailError: string | null; exampleMode: boolean; viewVersion: string | null; onViewVersion: (version: string | null) => void; items: KnowledgeDisplayItem[] }) {
  const versions = detail?.versions ?? [];
  const selectedVersion = viewVersion ? versions.find((version) => version.version === viewVersion) ?? null : null;
  const shown = selectedVersion ?? detail ?? item;
  const parameters = selectedVersion?.parameters ?? detail?.parameters ?? [];
  const sections = selectedVersion?.sections ?? detail?.sections ?? [];
  const isHistorical = Boolean(selectedVersion);
  // 历史版本用那一版自己的网址和抓取时间，回查时不会拿当前版本的值冒充。
  const shownWebUrl = isHistorical ? selectedVersion?.source_url ?? null : detail?.source_url ?? item.source_url ?? null;
  const shownFetchedAt = isHistorical ? selectedVersion?.fetched_at ?? null : detail?.fetched_at ?? item.fetched_at ?? null;
  return <div className={styles.inspector}>
    {exampleMode ? <div className={styles.exampleBanner}>示例详情 · 不会保存或引用到任务</div> : null}
    <KnowledgeConflicts allItems={items} current={item} currentDetail={detail} exampleMode={exampleMode} key={item.id} />
    <header><strong>{shown.title}</strong><small>{itemType(item.content_type)} · 版本 {shown.version ?? item.version} · {formatTime(item.created_at)}</small></header>
    {versions.length ? <div className={styles.detailSection}><h3>版本</h3><label><span>查看版本</span><select aria-label="查看版本" onChange={(event) => onViewVersion(event.target.value || null)} value={viewVersion ?? ""}><option value="">当前版本 {detail?.version}</option>{versions.map((version) => <option key={version.version} value={version.version}>版本 {version.version} · {formatTime(version.saved_at)}</option>)}</select></label>{isHistorical ? <p>正在查看历史版本 {selectedVersion?.version}：正文、出处和参数都来自这一版，不是当前版本。<button onClick={() => onViewVersion(null)} type="button">回到当前版本</button></p> : null}</div> : null}
    <div className={styles.compare}>
      <section><h3>{exampleMode ? "原始来源片段 · 示例" : "已保存正文"}</h3><p>{shown.body || "这个条目没有可显示的正文。"}</p>{sections.length ? <ol>{sections.map(section => <li key={section.locator}><strong>{section.locator}{section.title ? ` ${section.title}` : ""}</strong><pre>{section.text}</pre></li>)}</ol> : null}</section>
      <section><h3>{exampleMode ? "AI 摘要 · 示例" : "摘要"}</h3><p>{exampleMode ? item.exampleSummary ?? "示例暂无摘要" : "尚无可核对的摘要来源；不能把正文当作 AI 摘要。"}</p></section>
    </div>
    <section className={styles.detailSection}><h3>参数与出处</h3>{detailError ? <p role="alert">{detailError}</p> : parameters.length ? <table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>出处</th></tr></thead><tbody>{parameters.map(parameter => <tr key={`${parameter.locator}-${parameter.key}`}><td>{parameter.key}</td><td>{parameter.value}</td><td>{parameter.unit ?? "—"}</td><td>{parameter.locator}</td></tr>)}</tbody></table> : <p>{exampleMode ? item.exampleParameters ?? "示例暂无参数" : "这个条目没有解析出的结构化参数；没有显式参数格式就不生成参数。"}</p>}<dl><div><dt>来源</dt><dd>{item.source_item_id ? "已关联信息源" : "当前条目无信息源关联"}</dd></div><div><dt>文件引用</dt><dd>{shown.file_reference || "无"}</dd></div><div><dt>{isHistorical ? "该版来源条目" : "内容指纹"}</dt><dd>{isHistorical ? (selectedVersion?.source_item_id ?? "无") : ((detail?.content_hash ?? item.content_hash) ?? "无")}</dd></div><div><dt>原网址</dt><dd>{shownWebUrl ? <a href={shownWebUrl} rel="noreferrer" target="_blank">{shownWebUrl}</a> : "无网页来源"}</dd></div><div><dt>{isHistorical ? "该版抓取时间" : "抓取时间"}</dt><dd>{formatFetchTime(shownFetchedAt)}</dd></div><div><dt>归属</dt><dd>{item.ownership}</dd></div><div><dt>授权范围</dt><dd>{item.authorization}</dd></div><div><dt>版本</dt><dd>{shown.version ?? item.version}</dd></div></dl>{versions.length > 1 ? <ol>{versions.map(version => <li key={version.version}>版本 {version.version} · {formatTime(version.saved_at)} · {version.content_hash ?? "无指纹"}</li>)}</ol> : null}</section>
    <section className={styles.detailSection}><h3>核对与人工纠正</h3><p>{exampleMode ? `示例核对状态：${item.exampleIssue ?? "待核对"}` : "核对状态尚未接入；请核对原始来源后再用于创作。"}</p><div className={styles.correctionGrid}><div className={styles.correctionField}><span>摘要纠正</span><textarea aria-label="摘要纠正" defaultValue={exampleMode ? item.exampleSummary ?? "" : ""} disabled={!exampleMode} placeholder={exampleMode ? "示例：修订摘要" : "纠正保存接口尚未接入"} /></div><div className={styles.correctionField}><span>参数纠正</span><textarea aria-label="参数纠正" defaultValue={exampleMode ? item.exampleParameters ?? "" : ""} disabled={!exampleMode} placeholder={exampleMode ? "示例：修订参数" : "纠正保存接口尚未接入"} /></div></div><div className={styles.detailActions}><button disabled={!exampleMode} type="button">{exampleMode ? "保存纠正 · 示例" : "保存纠正"}</button><button disabled={!exampleMode} type="button">{exampleMode ? "用于当前任务 · 示例" : "用于当前任务"}</button><Link href="/sources">查看信息源 <ArrowRight size={14} /></Link></div>{!exampleMode ? <p className={styles.notWired}>纠正保存与任务引用接口尚未接入，以上操作暂不可用。</p> : null}</section>
  </div>;
}


export function ScriptLibraryPanel() {
  const router = useRouter();
  const [entries, setEntries] = useState<ScriptLibraryEntry[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<ScriptLibraryEntry | null>(null);
  const [sources, setSources] = useState<ScriptLibrarySource[]>([]);
  const [sourcesChecked, setSourcesChecked] = useState(false);
  const [name, setName] = useState("");
  const [ratio, setRatio] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [duration, setDuration] = useState(60);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(null); setSelected(null); setSources([]);
    listScriptLibraryEntries(query, category).then(result => {
      if (active) setEntries(result.items);
    }).catch(cause => {
      if (active) { setEntries([]); setError(cause instanceof Error ? cause.message : "文案库读取失败"); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [query, category]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    getScriptLibrarySources(selected.id).then(result => { if (active) { setSources(result); setSourcesChecked(true); } })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "来源核对失败"); });
    return () => { active = false; };
  }, [selected]);
  function select(entry: ScriptLibraryEntry) {
    setSelected(entry); setSources([]); setSourcesChecked(false); setConfirmed(false); setName(`${entry.name} · 新任务`); setError(null);
  }
  async function reuse() {
    if (!selected) return;
    setBusy(true); setError(null);
    try {
      const result = await reuseScriptLibraryEntry(selected.id, {
        name, aspect_ratio: ratio, duration_target_seconds: duration, requirements_confirmed: confirmed,
      });
      router.push(`/tasks/${encodeURIComponent(result.task_id)}`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "文案复用失败"); }
    finally { setBusy(false); }
  }
  const readable = sourcesChecked && sources.every(source => source.status === "readable");
  return <section className={styles.card} aria-label="文案库">
    <div className={styles.cardHeading}><div><h2>文案库</h2><small>已确认文案的命名快照与历史版本；复用需要新确认。</small></div></div>
    <div className={styles.searchBar}>
      <label><Search size={16} /><input aria-label="搜索文案" placeholder="搜索名称或正文" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>分类<input aria-label="筛选文案分类" placeholder="输入分类，留空查看全部" value={category} onChange={event => setCategory(event.target.value)} /></label>
    </div>
    {error ? <p role="alert">{error}</p> : null}
    {loading ? <p role="status">正在读取文案…</p> : entries.length ? <div className={styles.grid}>{entries.map(entry =>
      <button className={styles.cover} key={entry.id} onClick={() => void select(entry)} type="button"><strong>{entry.name}</strong><small>{entry.category || "未分类"} · 版本 {entry.version}</small><span>{entry.title}</span></button>
    )}</div> : <p>没有匹配的可用文案。可从任务的已确认文案页保存；来源不可读或版本已变化的条目不会用于复用。</p>}
    {selected ? <section className={styles.detailSection} aria-label="文案复用详情">
      <h3>{selected.name} · 版本 {selected.version}</h3>
      <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看原任务</Link>
      <p>原稿 {selected.source_script_id} · {selected.source_run_id ? `原记录 ${selected.source_run_id}` : "复用或人工文案，无模型生成记录"}</p>
      {selected.sections.map(section => <article key={section.id}><strong>{section.title}</strong><p>{section.content}</p><small>{section.reference_ids.join("、")}</small></article>)}
      <h4>事实出处（实时核对）</h4>
      {!sourcesChecked ? <p>正在核对来源…</p> : !sources.length ? <p>本人文案没有外部事实引用。</p> : sources.map(source => <p key={source.reference_id}>{source.reference_id} · {source.status === "readable" ? `${source.title} · ${source.version_or_identity}` : source.status === "version_changed" ? "版本或授权已变化" : "不可读"}{source.source_url ? <> · <a href={source.source_url}>原始来源</a></> : null}</p>)}
      <label className={styles.correctionField}><span>新任务名称</span><input value={name} onChange={event => { setName(event.target.value); setConfirmed(false); }} /></label>
      <label className={styles.correctionField}><span>新任务画幅</span><select value={ratio} onChange={event => { setRatio(event.target.value as typeof ratio); setConfirmed(false); }}><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></label>
      <label className={styles.correctionField}><span>目标时长（秒）</span><input type="number" min={15} max={600} value={duration} onChange={event => { setDuration(Number(event.target.value)); setConfirmed(false); }} /></label>
      <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />确认以上新任务要求，正文仍需重新确认</label>
      <p>仅复制文案到独立新draft，不复制旧确认、配音或成片，不调用模型。</p>
      <button disabled={busy || !confirmed || !readable || !name.trim() || duration < 15 || duration > 600} onClick={() => void reuse()} type="button">{busy ? "正在创建…" : "创建待确认新稿"}</button>
    </section> : null}
  </section>;
}

function statusLabel(comparison: ParameterComparison) {
  if (comparison.status === "agreed") return "两侧一致";
  if (comparison.status === "adjudicated") return "已裁定";
  return "待裁定";
}

function adjudicatedValue(adjudication: { retained_value: string; corrected_value: string | null }) {
  // 纠正值优先：真正生效的裁定值以纠正为准，没有纠正才是保留的那一侧。
  return adjudication.corrected_value?.trim() ? adjudication.corrected_value : adjudication.retained_value;
}

function KnowledgeConflicts({ allItems, current, currentDetail, exampleMode }: {
  allItems: KnowledgeDisplayItem[];
  current: KnowledgeDisplayItem;
  currentDetail: KnowledgeItemDetail | null;
  exampleMode: boolean;
}) {
  const [comparisons, setComparisons] = useState<ParameterComparison[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [corrections, setCorrections] = useState<Record<string, string>>({});
  // 对比条目的真实详情：版本和参数都从这里来，不让用户记机器出处串。
  const [counterpartId, setCounterpartId] = useState<string>("");
  const [counterpartDetail, setCounterpartDetail] = useState<KnowledgeItemDetail | null>(null);
  const [counterpartVersion, setCounterpartVersion] = useState<string>("");
  // 打开某一侧的真实原文：固定到那一版，不拿当前版冒充。
  const [evidence, setEvidence] = useState<{ key: string; version: string; source_url: string | null; body: string; section: string } | null>(null);

  async function refresh() {
    setLoading(true); setError(null);
    try {
      const result = await listParameterComparisons(current.id);
      setComparisons(result.items);
    } catch (cause) { setComparisons([]); setError(sourceErrorMessage(cause)); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (!exampleMode) void refresh(); }, [current.id, exampleMode]);

  useEffect(() => {
    if (!counterpartId || exampleMode) { setCounterpartDetail(null); setCounterpartVersion(""); return; }
    let active = true;
    setCounterpartDetail(null); setCounterpartVersion("");
    getKnowledgeItem(counterpartId)
      .then((result) => {
        if (!active) return;
        setCounterpartDetail(result);
        setCounterpartVersion(result.version);
      })
      .catch((cause) => { if (active) setError(sourceErrorMessage(cause)); });
    return () => { active = false; };
  }, [counterpartId, exampleMode]);

  const counterpartParameters = (() => {
    if (!counterpartDetail) return [];
    const chosen = counterpartDetail.versions.find((version) => version.version === counterpartVersion);
    return chosen ? chosen.parameters : counterpartDetail.parameters;
  })();
  const currentParameters = currentDetail?.parameters ?? [];

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setLoading(true); setError(null); setNotice(null);
    try {
      await createParameterComparison({
        object_label: String(form.get("object_label") ?? ""),
        parameter_key: String(form.get("parameter_key") ?? ""),
        unit: String(form.get("unit") ?? ""),
        condition: String(form.get("condition") ?? ""),
        left_knowledge_item_id: current.id,
        left_version: current.version,
        left_locator: String(form.get("left_locator") ?? ""),
        right_knowledge_item_id: counterpartId,
        right_version: counterpartVersion,
        right_locator: String(form.get("right_locator") ?? ""),
      });
      setNotice("已建立比较。值不同即为待裁定，两侧证据都已保留。");
      await refresh();
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  async function decide(comparison: ParameterComparison, side: "left" | "right") {
    const reason = reasons[comparison.id]?.trim();
    if (!reason) { setError("裁定需要写明理由。"); return; }
    const correction = corrections[comparison.id]?.trim() || undefined;
    setLoading(true); setError(null); setNotice(null);
    try {
      await adjudicateParameterComparison(comparison.id, {
        reason, retained_side: side, ...(correction ? { corrected_value: correction } : {}),
      });
      setNotice("已记录裁定：身份、理由、时间和双方版本都已留存，未选的一侧仍保留。");
      await refresh();
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  async function openEvidence(itemId: string, version: string, locator: string, key: string) {
    setLoading(true); setError(null);
    try {
      const detail = await getKnowledgeItem(itemId);
      const chosen = detail.versions.find((entry) => entry.version === version);
      // 只在这一版里找正文：当前版换了内容也不能拿来冒充旧版。
      if (!chosen) { setError(`这个条目没有版本 ${version}`); return; }
      const section = chosen.sections.find((entry) => entry.locator === locator);
      setEvidence({
        key, version,
        source_url: chosen.source_url,
        body: section ? section.text : (chosen.body ?? "这一版没有正文。"),
        section: section ? `${section.locator}${section.title ? ` ${section.title}` : ""}` : "该出处没有单独分段，显示这一版全文",
      });
    } catch (cause) { setError(sourceErrorMessage(cause)); }
    finally { setLoading(false); }
  }

  if (exampleMode) return null;
  const counterparts = allItems.filter((entry) => entry.id !== current.id);

  return <section className={styles.detailSection} aria-label="参数比较与裁定">
    <h3>参数比较与裁定</h3>
    <p>比较范围只按你确认的对象、参数名、单位和适用条件判定；不猜对象、同义词或单位换算。值不同即为待裁定，未裁定时不作为唯一事实。</p>
    {comparisons.length ? <ul>{comparisons.map((comparison) => <li key={comparison.id}>
      <p>{comparison.object_label} · {comparison.parameter_key}（{comparison.unit}）· 条件：{comparison.condition} · {statusLabel(comparison)}</p>
      <p>左：{comparison.left.value} · 版本 {comparison.left.version} · 出处 {comparison.left.locator}</p>
      <p>右：{comparison.right.value} · 版本 {comparison.right.version} · 出处 {comparison.right.locator}</p>
      <p><button onClick={() => void openEvidence(comparison.left.knowledge_item_id, comparison.left.version, comparison.left.locator, `${comparison.id}:left`)} type="button">查看左侧来源</button>
        <button onClick={() => void openEvidence(comparison.right.knowledge_item_id, comparison.right.version, comparison.right.locator, `${comparison.id}:right`)} type="button">查看右侧来源</button></p>
      {evidence && (evidence.key === `${comparison.id}:left` || evidence.key === `${comparison.id}:right`) ? <div aria-label="来源原文" role="group">
        <p>版本 {evidence.version} · {evidence.section}</p>
        {evidence.source_url ? <p><a href={evidence.source_url} rel="noreferrer" target="_blank">原网址</a></p> : null}
        <pre>{evidence.body}</pre>
      </div> : null}
      {comparison.adjudication ? <p>裁定值：{adjudicatedValue(comparison.adjudication)}{comparison.adjudication.corrected_value ? "（人工纠正）" : ""} · 保留{comparison.adjudication.retained_side === "left" ? "左" : "右"}侧来源 · {comparison.adjudication.operator} · {formatFetchTime(comparison.adjudication.decided_at)} · 理由：{comparison.adjudication.reason} · 依据版本 {comparison.adjudication.left_version_ref} / {comparison.adjudication.right_version_ref}</p>
        : comparison.status === "pending" ? <div>
          <label><span>裁定理由</span><input aria-label={`裁定理由 ${comparison.id}`} onChange={(event) => setReasons((current_) => ({ ...current_, [comparison.id]: event.target.value }))} value={reasons[comparison.id] ?? ""} /></label>
          <label><span>纠正值（可选，留空表示直接采用保留侧的值）</span><input aria-label={`纠正值 ${comparison.id}`} onChange={(event) => setCorrections((current_) => ({ ...current_, [comparison.id]: event.target.value }))} value={corrections[comparison.id] ?? ""} /></label>
          <button disabled={loading} onClick={() => void decide(comparison, "left")} type="button">保留左侧 {comparison.left.value}</button>
          <button disabled={loading} onClick={() => void decide(comparison, "right")} type="button">保留右侧 {comparison.right.value}</button></div>
        : null}
    </li>)}</ul> : <p>{loading ? "正在读取参数比较…" : "还没有与这个条目相关的参数比较。"}</p>}
    {error ? <p role="alert">{error}</p> : null}
    {notice ? <p>{notice}</p> : null}
    <form onSubmit={create}>
      <h4>与另一条目比较同一参数</h4>
      <label><span>对象</span><input name="object_label" required /></label>
      <label><span>参数名</span><input name="parameter_key" required /></label>
      <label><span>单位</span><input name="unit" required /></label>
      <label><span>适用条件</span><input name="condition" required /></label>
      <label><span>对比条目</span><select aria-label="对比条目" onChange={(event) => setCounterpartId(event.target.value)} required value={counterpartId}><option value="">请选择对比资料</option>{counterparts.map((entry) => <option key={entry.id} value={entry.id}>{entry.title}</option>)}</select></label>
      <label><span>对比条目版本</span><select aria-label="对比条目版本" onChange={(event) => setCounterpartVersion(event.target.value)} required value={counterpartVersion}>{(counterpartDetail?.versions ?? []).map((version) => <option key={version.version} value={version.version}>版本 {version.version}</option>)}</select></label>
      <label><span>本条目参数（出处）</span><select aria-label="本条目参数" name="left_locator" required>{currentParameters.map((parameter) => <option key={`${parameter.locator}-${parameter.key}`} value={parameter.locator}>{parameter.key} · {parameter.value}{parameter.unit ? `（${parameter.unit}）` : ""} · {parameter.locator}</option>)}</select></label>
      <label><span>对比条目参数（出处）</span><select aria-label="对比条目参数" name="right_locator" required>{counterpartParameters.map((parameter) => <option key={`${parameter.locator}-${parameter.key}`} value={parameter.locator}>{parameter.key} · {parameter.value}{parameter.unit ? `（${parameter.unit}）` : ""} · {parameter.locator}</option>)}</select></label>
      <button disabled={loading || !currentParameters.length || !counterpartParameters.length} type="submit">建立比较</button>
    </form>
  </section>;
}
