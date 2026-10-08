"use client";

import { AlertCircle, ArrowRight, BookOpen, CircleCheck, CircleMinus, FileText, Grid2X2, Link2, List, LoaderCircle, RefreshCw, RotateCcw, Search, Video } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiRequestError, getKnowledgeReferenceVersion, getScriptLibrarySources, listScriptLibraryEntries, reuseScriptLibraryEntry, getKnowledgeGrants, grantKnowledgeToWorkspace, revokeKnowledgeGrant } from "@/lib/api";
import type { ScriptLibraryEntry, ScriptLibrarySource, KnowledgeGrantState } from "@/lib/types";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { CardSelect, HomeCardDialog } from "@/components/HomeCardDialog";
import type { KnowledgeItem, KnowledgeItemDetail, KnowledgeItemVersion } from "@/lib/source-api";
import { getKnowledgeItem, parseKnowledgeReferenceId, setKnowledgeItemVisibility, sourceErrorMessage } from "@/lib/source-api";
import styles from "./KnowledgeWorkspace.module.css";

type KnowledgeDisplayItem = KnowledgeItem & {
  exampleCategory?: string;
  exampleSummary?: string;
  exampleParameters?: string;
  exampleIssue?: string;
};
type DisplayMode = "grid" | "list";

function DisplayModeToggle({ label, value, onChange }: { label: string; value: DisplayMode; onChange: (mode: DisplayMode) => void }) {
  return <div className={styles.viewToggle} role="group" aria-label={`${label}显示方式`}>
    <button aria-label="封面网格" aria-pressed={value === "grid"} title="封面网格" onClick={() => onChange("grid")} type="button"><Grid2X2 size={16} /></button>
    <button aria-label="对象列表" aria-pressed={value === "list"} title="对象列表" onClick={() => onChange("list")} type="button"><List size={16} /></button>
  </div>;
}
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
  const [collection, setCollection] = useState<"sources" | "scripts">("sources");
  const [sourcesView, setSourcesView] = useState<DisplayMode>("grid");
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const visibleItems = useMemo(() => items.filter((item) => {
    const matchesCategory = category === "全部" || (exampleMode && item.exampleCategory === category);
    const searchable = [item.title, item.body, item.file_reference].filter(Boolean).join(" ").toLocaleLowerCase();
    return matchesCategory && searchable.includes(query.trim().toLocaleLowerCase());
  }), [items, category, query, exampleMode]);
  // 可见范围变更用 PUT 返回的服务端值回写，不另发一轮 GET：
// 只更新同 id 的当前详情，保留已加载的版本历史；列表由页面刷新时取真实值。
  function applyVisibilityUpdate(updated: KnowledgeItem) {
    if (updated.id !== selectedId) return;
    setDetail((current) => current && current.id === updated.id ? { ...current, visibility: updated.visibility } : current);
  }
  const inspector = selected ? <KnowledgeInspector key={selected.id} detail={shownDetail} detailError={detailError} exampleMode={exampleMode} item={selected} viewVersion={viewVersion} onViewVersion={setViewVersion} onVisibilityChanged={applyVisibilityUpdate} /> : undefined;

  return <AppShell inspector={inspector} inspectorTitle="资料详情">
    <div className={styles.page}>
      {exampleMode ? <div className={styles.exampleBanner}>{exampleLabel ?? "隔离视觉示例"} · 所有名称、分类、摘要和状态均非真实服务数据</div> : null}
      {!exampleMode ? <nav aria-label="知识库集合" className={styles.collectionTabs}><button aria-pressed={collection === "sources"} onClick={() => setCollection("sources")} type="button">资料</button><button aria-pressed={collection === "scripts"} onClick={() => setCollection("scripts")} type="button">文案库</button></nav> : null}
      <div hidden={collection !== "sources"}>
      <div className={styles.top}>
        <nav aria-label="知识用途分类" className={styles.categories}>
          {exampleMode ? categories.map((name, index) => <button aria-pressed={category === name} className={styles.category} key={name} onClick={() => setCategory(category === name ? "全部" : name)} type="button">
            <span className={styles.categoryIcon}>{[<BookOpen key="a" size={20} />, <FileText key="b" size={20} />, <FileText key="c" size={20} />, <BookOpen key="d" size={20} />][index]}</span>
            <strong>{name}</strong>
            <small>{exampleMode ? items.filter((item) => item.exampleCategory === name).length + " 条示例来源" : "未分类"}</small>
          </button>) : <p className={styles.categoryUnavailable}>用途分类尚未接入 · 当前显示全部资料，不按文件格式推断分类。</p>}
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
          <div className={styles.cardHeading}><div><h2>资料集合</h2><small>{loading ? "正在读取" : error ? "读取失败" : visibleItems.length + " 条" + (exampleMode ? "示例" : "当前工作区") + "结果"} · 选择资料查看正文</small></div><DisplayModeToggle label="资料列表" value={sourcesView} onChange={setSourcesView} /></div>
          {loading ? <div className={styles.empty}><LoaderCircle className={styles.spinning} size={18} />正在读取知识库…</div>
            : error ? <div className={styles.empty}>无法读取知识库<button onClick={onRefresh} type="button">重新读取</button></div>
            : visibleItems.length ? <div className={`${styles.grid} ${styles.sourceList}${sourcesView === "list" ? ` ${styles.list}` : ""}`}>{visibleItems.map((item) => <button className={styles.cover} data-open-details key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                <span className={styles.coverArt}>{itemIcon(item.content_type)}<small>{itemType(item.content_type)}</small></span>
                <strong>{item.title}</strong><small>{exampleMode ? item.exampleCategory : itemType(item.content_type)} · 版本 {item.version}</small>
                <span className={styles.coverStatus}>{exampleMode ? item.exampleIssue ?? "示例状态" : visibilityLabel(item.visibility)}</span>
              </button>)}</div>
            : <div className={styles.empty}>{error ? "无法读取知识库" : items.length ? "没有匹配的来源" : "知识库还没有内容"}{!items.length && !error ? <Link href="/sources">去信息源添加内容</Link> : null}</div>}
      </section>
      <p className={styles.notWired}>主题关系、参数冲突核对、任务引用、问答和来源召回尚未接入；当前可查看已保存的来源正文。</p>
      </div>
      {!exampleMode ? <div hidden={collection !== "scripts"}><ScriptLibraryPanel /></div> : null}
    </div>
  </AppShell>;
}

function visibilityLabel(visibility: KnowledgeItem["visibility"]) { return visibility === "shared" ? "团队共享" : "仅自己"; }

function grantErrorMessage(cause: unknown): string {
  if (cause instanceof ApiRequestError) {
    if (cause.status === 503) return "同设备成员与空间关系尚未解析；跨空间授权未验证。";
    if (cause.status === 409) return "仅同空间，无需跨空间授权。";
    if (cause.status === 401) return "知识授权需要已验证成员身份。";
    if (cause.status === 404) return "知识资料或授权目标不可访问。";
  }
  return cause instanceof Error ? cause.message : "授权请求失败。";
}

function KnowledgeInspector({ item, detail, detailError, exampleMode, viewVersion, onViewVersion, onVisibilityChanged }: { item: KnowledgeDisplayItem; detail: KnowledgeItemDetail | null; detailError: string | null; exampleMode: boolean; viewVersion: string | null; onViewVersion: (version: string | null) => void; onVisibilityChanged: (updated: KnowledgeItem) => void }) {
  const [versionCardOpen, setVersionCardOpen] = useState(false);
  const [grantState, setGrantState] = useState<KnowledgeGrantState | null>(null);
  const [targetWorkspace, setTargetWorkspace] = useState("");
  const [grantBusy, setGrantBusy] = useState(false);
  const [grantError, setGrantError] = useState<string | null>(null);
  const [grantConfirmation, setGrantConfirmation] = useState<{ workspaceId: string } | { grantId: string } | null>(null);
  useEffect(() => {
    if (exampleMode) return;
    let active = true;
    getKnowledgeGrants(item.id)
      .then(value => { if (active) setGrantState(value); })
      .catch(cause => { if (active) setGrantError(grantErrorMessage(cause)); });
    return () => { active = false; };
  }, [item.id, exampleMode]);
  async function grantToWorkspace(workspaceId: string) {
    setGrantBusy(true); setGrantError(null);
    try {
      const grant = await grantKnowledgeToWorkspace(item.id, workspaceId);
      setGrantState(current => current ? { ...current, grants: [...current.grants.filter(value => value.id !== grant.id), grant] } : current);
      setTargetWorkspace("");
    } catch (cause) { setGrantError(grantErrorMessage(cause)); }
    finally { setGrantBusy(false); }
  }
  async function revokeGrant(grantId: string) {
    setGrantBusy(true); setGrantError(null);
    try {
      const revoked = await revokeKnowledgeGrant(item.id, grantId);
      setGrantState(current => current ? { ...current, grants: current.grants.map(value => value.id === revoked.id ? revoked : value) } : current);
    } catch (cause) { setGrantError(grantErrorMessage(cause)); }
    finally { setGrantBusy(false); }
  }
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<string | null>(null);
  const visibility = detail?.visibility ?? item.visibility ?? "private";
  async function changeVisibility(next: "shared" | "private") {
    setVisibilityBusy(true); setVisibilityError(null);
    try {
      // 保存返回的条目就是服务端写入后的真实状态。
      onVisibilityChanged(await setKnowledgeItemVisibility(item.id, next));
    } catch (cause) {
      setVisibilityError(sourceErrorMessage(cause));
    } finally { setVisibilityBusy(false); }
  }
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
    <header><strong>{shown.title}</strong><small>{itemType(item.content_type)} · 版本 {shown.version ?? item.version} · {formatTime(item.created_at)}</small></header>
    {versions.length ? <div className={styles.detailSection}><h3>版本与出处</h3><button onClick={() => setVersionCardOpen(true)} type="button">选择查看版本</button>{isHistorical ? <p>正在查看历史版本 {selectedVersion?.version}：正文、出处和参数都来自这一版，不是当前版本。<button onClick={() => onViewVersion(null)} type="button">回到当前版本</button></p> : null}</div> : null}
    {versionCardOpen ? <HomeCardDialog kind="config" title="版本与出处" onClose={() => setVersionCardOpen(false)}><div className={styles.inspector}><CardSelect label="查看版本" value={viewVersion ?? ""} options={[{ value: "", label: `当前版本 ${detail?.version}` }, ...versions.map(version => ({ value: version.version, label: `版本 ${version.version} · ${formatTime(version.saved_at)}` }))]} onChange={version => onViewVersion(version || null)} /><p>只切换查看版本，不修改资料或引用已绑定的版本。</p><p>原网址：{shownWebUrl ?? "无网页来源"}</p><p>抓取时间：{formatFetchTime(shownFetchedAt)}</p></div></HomeCardDialog> : null}
    {!exampleMode ? <section className={styles.detailSection}><h3>授权给其他工作空间</h3>
      <p>保留原工作空间、所有者和版本。授权允许目标空间读取和引用；撤销后停止新访问与后续引用。</p>
      {grantState && grantState.target_resolution !== "resolved" ? <p role="status">{grantState.target_resolution_message} · {grantState.cross_workspace_status}</p> : null}
      {grantState?.can_manage ? <>
        {grantState.target_resolution === "resolved" ? <><label className={styles.grantTarget}><span>目标工作空间</span><select aria-label="目标工作空间" disabled={grantBusy} value={targetWorkspace} onChange={event => setTargetWorkspace(event.target.value)}>
          <option value="">请选择同设备工作空间</option>
          {grantState.targets.map(target => <option key={target.workspace_id} value={target.workspace_id}>{target.name}</option>)}
        </select></label>
        {!grantState.targets.length ? <p>当前没有其他可授权的同设备工作空间。</p> : null}
        <button disabled={grantBusy || !targetWorkspace} onClick={() => setGrantConfirmation({ workspaceId: targetWorkspace })} type="button">授予读取与引用</button>
        </> : null}
        <ul className={styles.grantList}>{grantState.grants.map(grant => <li key={grant.id}>
          <span>{grantState.targets.find(target => target.workspace_id === grant.target_workspace_id)?.name ?? grant.target_workspace_id} · {grant.revoked_at ? "已撤销" : "可读与引用"}</span>
          {!grant.revoked_at ? <button disabled={grantBusy} onClick={() => setGrantConfirmation({ grantId: grant.id })} type="button">撤销授权</button> : null}
        </li>)}</ul>
      </> : grantState ? <p>原工作空间：{grantState.source_workspace_id}。当前可读取资料，无权向其他空间授予或转移归属。</p> : !grantError ? <p>正在读取授权状态…</p> : null}
      {grantError ? <p role="alert">{grantError}</p> : null}
    </section> : null}
    {grantConfirmation ? <HomeCardDialog kind="modal" title={"workspaceId" in grantConfirmation ? "跨空间授权确认" : "撤销跨空间授权"} onClose={() => setGrantConfirmation(null)} footer={<><button data-card-cancel type="button" disabled={grantBusy} onClick={() => setGrantConfirmation(null)}>取消</button><button type="button" disabled={grantBusy} onClick={async () => {
      if ("workspaceId" in grantConfirmation) await grantToWorkspace(grantConfirmation.workspaceId);
      else await revokeGrant(grantConfirmation.grantId);
      setGrantConfirmation(null);
    }}>{"workspaceId" in grantConfirmation ? "确认授予读取与引用" : "确认撤销授权"}</button></>}><p>资料：{item.title}</p><p>{"workspaceId" in grantConfirmation ? `目标空间：${grantState?.targets.find(target => target.workspace_id === grantConfirmation.workspaceId)?.name ?? grantConfirmation.workspaceId}。该空间将可读取与引用这条资料；原空间、所有者和版本保持不变。` : "撤销后，目标空间停止新访问与后续引用；已引用的固定版本依现有授权规则核验，不删除原资料。"}</p></HomeCardDialog> : null}
    <div className={styles.detailSection}><h3>可见范围</h3>{exampleMode ? <p>示例：可见范围设置不会保存。</p> : <><p>{grantState?.access_via_grant ? "原空间的可见范围保持不变；你通过显式授权读取和引用这条资料。" : visibility === "shared" ? "当前与团队成员共享，成员可读取这条资料。" : "当前仅自己可见，成员读不到这条资料。"}</p><div className={styles.detailActions}>{grantState?.can_manage ? visibility === "private" ? <button disabled={visibilityBusy} onClick={() => void changeVisibility("shared")} type="button">共享给团队</button> : <button disabled={visibilityBusy} onClick={() => void changeVisibility("private")} type="button">收回为仅自己可见</button> : null}</div>{visibilityError ? <p role="alert">{visibilityError}</p> : null}</>}</div>
    <div className={styles.compare}>
      <section><h3>{exampleMode ? "原始来源片段 · 示例" : "已保存正文"}</h3><p>{shown.body || "这个条目没有可显示的正文。"}</p>{sections.length ? <ol>{sections.map(section => <li key={section.locator}><strong>{section.locator}{section.title ? ` ${section.title}` : ""}</strong><pre>{section.text}</pre></li>)}</ol> : null}</section>
      <section><h3>{exampleMode ? "AI 摘要 · 示例" : "摘要"}</h3><p>{exampleMode ? item.exampleSummary ?? "示例暂无摘要" : "尚无可核对的摘要来源；不能把正文当作 AI 摘要。"}</p></section>
    </div>
    <section className={styles.detailSection}><h3>参数与出处</h3>{detailError ? <p role="alert">{detailError}</p> : parameters.length ? <table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>出处</th></tr></thead><tbody>{parameters.map(parameter => <tr key={`${parameter.locator}-${parameter.key}`}><td>{parameter.key}</td><td>{parameter.value}</td><td>{parameter.unit ?? "—"}</td><td>{parameter.locator}</td></tr>)}</tbody></table> : <p>{exampleMode ? item.exampleParameters ?? "示例暂无参数" : "这个条目没有解析出的结构化参数；没有显式参数格式就不生成参数。"}</p>}<dl><div><dt>来源</dt><dd>{item.source_item_id ? "已关联信息源" : "当前条目无信息源关联"}</dd></div><div><dt>文件引用</dt><dd>{shown.file_reference || "无"}</dd></div><div><dt>{isHistorical ? "该版来源条目" : "内容指纹"}</dt><dd>{isHistorical ? (selectedVersion?.source_item_id ?? "无") : ((detail?.content_hash ?? item.content_hash) ?? "无")}</dd></div><div><dt>原网址</dt><dd>{shownWebUrl ? <a href={shownWebUrl} rel="noreferrer" target="_blank">{shownWebUrl}</a> : "无网页来源"}</dd></div><div><dt>{isHistorical ? "该版抓取时间" : "抓取时间"}</dt><dd>{formatFetchTime(shownFetchedAt)}</dd></div><div><dt>可见范围</dt><dd>{visibility === "shared" ? "团队共享" : "仅自己"}</dd></div><div><dt>归属</dt><dd>{item.ownership}</dd></div><div><dt>授权范围</dt><dd>{item.authorization}</dd></div><div><dt>版本</dt><dd>{shown.version ?? item.version}</dd></div></dl>{versions.length > 1 ? <ol>{versions.map(version => <li key={version.version}>版本 {version.version} · {formatTime(version.saved_at)} · {version.content_hash ?? "无指纹"}</li>)}</ol> : null}</section>
    <section className={styles.detailSection}><h3>核对与人工纠正</h3><p>{exampleMode ? `示例核对状态：${item.exampleIssue ?? "待核对"}` : "核对状态尚未接入；请核对原始来源后再用于创作。"}</p><div className={styles.correctionGrid}><div className={styles.correctionField}><span>摘要纠正</span><textarea aria-label="摘要纠正" defaultValue={exampleMode ? item.exampleSummary ?? "" : ""} disabled={!exampleMode} placeholder={exampleMode ? "示例：修订摘要" : "纠正保存接口尚未接入"} /></div><div className={styles.correctionField}><span>参数纠正</span><textarea aria-label="参数纠正" defaultValue={exampleMode ? item.exampleParameters ?? "" : ""} disabled={!exampleMode} placeholder={exampleMode ? "示例：修订参数" : "纠正保存接口尚未接入"} /></div></div><div className={styles.detailActions}><button disabled={!exampleMode} type="button">{exampleMode ? "保存纠正 · 示例" : "保存纠正"}</button><button disabled={!exampleMode} type="button">{exampleMode ? "用于当前任务 · 示例" : "用于当前任务"}</button><Link href="/sources">查看信息源 <ArrowRight size={14} /></Link></div>{!exampleMode ? <p className={styles.notWired}>纠正保存与任务引用接口尚未接入，以上操作暂不可用。</p> : null}</section>
  </div>;
}


/**
 * 按引用给出的定位在那一版里找命中段落/参数。
 *
 * 只按实际存储的 locator 精确匹配：找不到就说这一版没有该定位的证据，
 * 不回退到整版正文、也不猜页码。
 */
function citedEvidence(version: KnowledgeItemVersion, locator: string) {
  const section = version.sections.find((value) => value.locator === locator) ?? null;
  const parameters = version.parameters.filter((value) => value.locator === locator);
  if (!section && !parameters.length) {
    return <p role="alert">引用绑定的定位 {locator} 不在这一版里；不会用其他段落或页码替代。</p>;
  }
  return <div className={styles.citationHit}>
    <p>命中定位 {locator}</p>
    {section ? <pre>{section.text}</pre> : null}
    {parameters.length ? <table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>出处</th></tr></thead><tbody>{parameters.map(parameter => <tr key={`${parameter.locator}-${parameter.key}`}><td>{parameter.key}</td><td>{parameter.value}</td><td>{parameter.unit ?? "—"}</td><td>{parameter.locator}</td></tr>)}</tbody></table> : null}
  </div>;
}

type CitationTrace = { state: "idle" | "loading" | "ready" | "unavailable" | "failed"; version?: KnowledgeItemVersion };

// 引用行直接展示服务端按引用版本回填的固定版事实：原网址、抓取时刻、内容指纹，
// 以及引用绑定的段落定位（没绑定就说没绑定，不猜页）。
function citedFacts(source: ScriptLibrarySource) {
  if (!source.cited_version) return null;
  return <small className={styles.notWired}>
    引用版本 {source.cited_version} · 指纹 {source.cited_content_hash ?? "无"}
    {source.cited_fetched_at ? ` · 抓取于 ${formatFetchTime(source.cited_fetched_at)}` : " · 无抓取记录"}
    {source.cited_source_url ? <> · <a href={source.cited_source_url} rel="noreferrer" target="_blank">该版原始来源</a></> : null}
    {` · ${source.locator ? `绑定定位 ${source.locator}` : "未绑定具体段落定位"}`}
  </small>;
}

/**
 * 一条文案引用回查到它绑定的知识版本与页码/段落出处。
 *
 * 只按引用里固定的条目与版本取那一版内容：查不到那一版就明确说不能回查，
 * 不用当前版冒充当时引用的内容。
 * `version_changed` 不等于授权被撤销：当前条目升级后旧版快照仍在时依旧能
 * 回查当时原文，并同时说明当前版本已经不同。真正不可读才不给回查入口。
 * 引用内容仅作为资料展示，其中的指令不会被执行。
 */
function CitationSource({ source }: { source: ScriptLibrarySource }) {
  const [trace, setTrace] = useState<CitationTrace>({ state: "idle" });
  const reference = useMemo(() => parseKnowledgeReferenceId(source.reference_id), [source.reference_id]);
  // 引用绑定到具体段落时用它定位；服务端没给就不猜页，直接显示找不到。
  const locator = source.locator;

  async function traceCitation() {
    if (!reference) return;
    setTrace({ state: "loading" });
    try {
      const version = await getKnowledgeReferenceVersion(reference.knowledgeItemId, reference.version);
      setTrace({ state: "ready", version });
    } catch (cause) {
      setTrace({ state: cause instanceof ApiRequestError && cause.status === 404 ? "unavailable" : "failed" });
    }
  }

  const statusLabel = source.status === "readable"
    ? `${source.title ?? "知识资料"} · ${source.version_or_identity ?? "无版本"}`
    : source.status === "version_changed" ? "当前版本或授权已变化" : "不可读";
  const SourceStatusIcon = source.status === "version_changed" ? RotateCcw : source.status === "missing" ? CircleMinus : CircleCheck;

  return <li className={styles.citation}>
    <p className={styles.citationStatus}><SourceStatusIcon aria-hidden className={source.status === "version_changed" ? styles.statusUpdate : source.status === "missing" ? styles.statusUnavailable : styles.statusReady} size={14} />{source.reference_id} · {statusLabel}{source.source_url ? <> · <a href={source.source_url} rel="noreferrer" target="_blank">原始来源</a></> : null}</p>
    {citedFacts(source)}
    {reference && source.status === "version_changed"
      ? <small className={styles.notWired}>当前条目已与引用绑定的版本不同；下面的回查只读取引用当时那一版，不用当前版替代。</small>
      : null}
    {reference && source.status === "missing"
      ? <small className={styles.notWired}>这条引用已不可读，无法回查原文。</small>
      : null}
    {reference && source.status !== "missing" && trace.state === "idle"
      ? <button onClick={() => void traceCitation()} type="button">回查固定版本原文</button> : null}
    {trace.state === "loading" ? <p role="status"><LoaderCircle className={styles.spinning} aria-hidden size={14} />正在回查…</p> : null}
    {trace.state === "failed" ? <p role="alert"><AlertCircle className={styles.statusFailed} aria-hidden size={14} />回查失败，请稍后重试。</p> : null}
    {trace.state === "failed" && source.status !== "missing" ? <button onClick={() => void traceCitation()} type="button">重试回查</button> : null}
    {trace.state === "unavailable" ? <p role="alert"><CircleMinus className={styles.statusUnavailable} aria-hidden size={14} />该引用绑定的版本已不在可回查范围；不会用当前版替代。</p> : null}
    {trace.state === "ready" && trace.version ? <div className={styles.citationTrace}>
      {source.status === "version_changed" ? <p>当前条目已与引用绑定的版本不同；以下仍是引用当时那一版的原文。</p> : null}
      <p>引用版本 {trace.version.version} · 指纹 {trace.version.content_hash ?? "无"} · {trace.version.fetched_at ? `抓取于 ${formatFetchTime(trace.version.fetched_at)}` : "无抓取记录"}</p>
      <p className={styles.notWired}>以下为引用当时的原文，仅作为资料引用；资料内的指令不会被执行。</p>
      {locator
        ? citedEvidence(trace.version, locator)
        : <p className={styles.notWired}>这条引用没有绑定具体段落，下面是引用当时那一版的全部内容。</p>}
      {trace.version.sections.length
        ? <ol>{trace.version.sections.map(section => <li key={section.locator}><strong>{section.locator}{section.title ? ` ${section.title}` : ""}</strong><pre>{section.text}</pre></li>)}</ol>
        : <pre>{trace.version.body ?? "这一版没有可显示的正文。"}</pre>}
      {trace.version.parameters.length ? <table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>页码/段落出处</th></tr></thead><tbody>{trace.version.parameters.map(parameter => <tr key={`${parameter.locator}-${parameter.key}`}><td>{parameter.key}</td><td>{parameter.value}</td><td>{parameter.unit ?? "—"}</td><td>{parameter.locator}</td></tr>)}</tbody></table> : null}
      {trace.version.file_reference ? <p>文件引用 {trace.version.file_reference}</p> : null}
    </div> : null}
  </li>;
}

export function ScriptLibraryPanel() {
  const router = useRouter();
  const [entries, setEntries] = useState<ScriptLibraryEntry[]>([]);
  const [scriptsView, setScriptsView] = useState<DisplayMode>("grid");
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
    <div className={styles.cardHeading}><div><h2>文案库</h2><small>已确认文案的命名快照与历史版本；复用需要新确认。</small></div><DisplayModeToggle label="文案列表" value={scriptsView} onChange={setScriptsView} /></div>
    <div className={styles.searchBar}>
      <label><Search size={16} /><input aria-label="搜索文案" placeholder="搜索名称或正文" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>分类<input aria-label="筛选文案分类" placeholder="输入分类，留空查看全部" value={category} onChange={event => setCategory(event.target.value)} /></label>
    </div>
    {error ? <p role="alert">{error}</p> : null}
    {loading ? <p role="status">正在读取文案…</p> : entries.length ? <div className={`${styles.grid} ${styles.scriptList}${scriptsView === "list" ? ` ${styles.list}` : ""}`}>{entries.map(entry =>
      <button className={styles.cover} key={entry.id} onClick={() => void select(entry)} type="button"><strong>{entry.name}</strong><small>{entry.category || "未分类"} · 版本 {entry.version}</small><span>{entry.title}</span></button>
    )}</div> : <p>没有匹配的可用文案。可从任务的已确认文案页保存；来源不可读或版本已变化的条目不会用于复用。</p>}
    {selected ? <section className={styles.detailSection} aria-label="文案复用详情">
      <h3>{selected.name} · 版本 {selected.version}</h3>
      <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看原任务</Link>
      <p>原稿 {selected.source_script_id} · {selected.source_run_id ? `原记录 ${selected.source_run_id}` : "复用或人工文案，无模型生成记录"}</p>
      {selected.sections.map(section => <article key={section.id}><strong>{section.title}</strong><p>{section.content}</p><small>{section.reference_ids.join("、")}</small></article>)}
      <h4>事实出处（实时核对）</h4>
      {!sourcesChecked ? <p>正在核对来源…</p> : !sources.length ? <p>本人文案没有外部事实引用。</p> : <ul className={styles.citationList}>{sources.map(source => <CitationSource key={`${selected.id}:${source.reference_id}:${source.status}:${source.version_or_identity}`} source={source} />)}</ul>}
      <label className={styles.correctionField}><span>新任务名称</span><input value={name} onChange={event => { setName(event.target.value); setConfirmed(false); }} /></label>
      <label className={styles.correctionField}><span>新任务画幅</span><select value={ratio} onChange={event => { setRatio(event.target.value as typeof ratio); setConfirmed(false); }}><option value="9:16">9:16</option><option value="16:9">16:9</option><option value="1:1">1:1</option></select></label>
      <label className={styles.correctionField}><span>目标时长（秒）</span><input type="number" min={15} max={600} value={duration} onChange={event => { setDuration(Number(event.target.value)); setConfirmed(false); }} /></label>
      <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />确认以上新任务要求，正文仍需重新确认</label>
      <p>仅复制文案到独立新draft，不复制旧确认、配音或成片，不调用模型。</p>
      <button disabled={busy || !confirmed || !readable || !name.trim() || duration < 15 || duration > 600} onClick={() => void reuse()} type="button">{busy ? "正在创建…" : "创建待确认新稿"}</button>
    </section> : null}
  </section>;
}
