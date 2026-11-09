"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { AlertCircle, ArrowRight, BookOpen, CircleCheck, CircleMinus, FileText, Grid2X2, Link2, List, LoaderCircle, RefreshCw, RotateCcw, Search, Video } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useWorkspaceSearchParams as useSearchParams, useWorkspaceViewState } from "@/components/workbench/context";
import { ApiRequestError, getKnowledgeReferenceVersion, getScriptLibrarySources, listScriptLibraryEntries, reuseScriptLibraryEntry, getKnowledgeGrants, grantKnowledgeToWorkspace, revokeKnowledgeGrant } from "@/lib/api";
import type { ScriptLibrarySource, KnowledgeGrantState } from "@/lib/types";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { getScriptLibraryEntry, getScriptLibrarySections, getScriptLibraryRequest, isScriptLibraryWriteRejected } from "@/lib/api/script-library";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
import type { ScriptLibraryEntryHeader, ScriptLibraryCursor, ScriptLibrarySectionWindow, ScriptLibraryReuseInput, ScriptLibraryReuseReceipt } from "@/lib/types/script-library";
import { AppShell } from "@/components/AppShell";
import { CardSelect, HomeCardDialog } from "@/components/HomeCardDialog";
import type { KnowledgeItem, KnowledgeItemDetail, KnowledgeItemVersion } from "@/lib/source-api";
import { editKnowledgeItem, getKnowledgeItem, parseKnowledgeReferenceId, setKnowledgeItemVisibility, sourceErrorMessage } from "@/lib/source-api";
import { readAssistantLocateReference, referenceAssistantKnowledge } from "@/lib/assistant-reference";
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

export function KnowledgeWorkspace(props: Parameters<typeof KnowledgeWorkspaceContent>[0]) {
  return <Suspense fallback={<p role="status">正在读取资料…</p>}><KnowledgeWorkspaceContent {...props} /></Suspense>;
}

function KnowledgeWorkspaceContent({ items, loading, error, onRefresh, exampleMode = false, exampleLabel }: {
  items: KnowledgeDisplayItem[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  exampleMode?: boolean;
  exampleLabel?: string;
}) {
  const search = useSearchParams();
  const locateRaw = search.get("locate_ref");
  const location = useMemo(() => {
    try {
      const reference = readAssistantLocateReference(new URLSearchParams(locateRaw ? { locate_ref: locateRaw } : {}));
      return { reference: reference?.kind === "knowledge" ? reference : null, error: null };
    } catch { return { reference: null, error: "资料引用定位格式不可用。" }; }
  }, [locateRaw]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeItemDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [viewVersion, setViewVersion] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId || exampleMode) { setDetail(null); setDetailError(null); setViewVersion(null); return; }
    let active = true;
    setDetail(null); setDetailError(null); setViewVersion(null);
    getKnowledgeItem(selectedId)
      .then(result => {
        if (!active || result.id !== selectedId) return;
        const ref = location.reference?.knowledge_item_id === selectedId ? location.reference : null;
        if (ref) {
          const fixed = result.version === ref.version ? result : result.versions.find(version => version.version === ref.version);
          if (!fixed) throw new Error("引用的资料版本已不可用；没有替换成当前版本。");
          if (ref.locator && !fixed.sections.some(section => section.locator === ref.locator)
              && !fixed.parameters.some(parameter => parameter.locator === ref.locator)) {
            throw new Error("引用的资料范围已不可用；没有改绑其他片段。");
          }
          setViewVersion(result.version === ref.version ? null : ref.version);
        }
        setDetail(result);
      })
      .catch(cause => { if (active) setDetailError(cause instanceof Error ? cause.message : "知识详情读取失败"); });
    return () => { active = false; };
  }, [selectedId, exampleMode, location.reference]);
  // 详情可能晚于列表刷新到达；只展示与当前选中条目一致的详情。
  const shownDetail = detail && detail.id === selectedId ? detail : null;
  const [query, setQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState<string>("全部");
  const [collection, setCollection] = useState<"sources" | "scripts">("sources");
  useEffect(() => {
    if (location.reference && !exampleMode) {
      setSelectedId(location.reference.knowledge_item_id);
      setCollection("sources"); setQuery(""); setCategory("全部");
    }
  }, [location.reference, exampleMode]);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("entry_id")) setCollection("scripts");
  }, []);
  const [sourcesView, setSourcesView] = useState<DisplayMode>("list");
  const visibleItems = useMemo(() => items.filter((item) => {
    const matchesCategory = category === "全部" || (exampleMode ? item.exampleCategory === category : item.content_type === category);
    const searchable = [item.title, item.body, item.file_reference].filter(Boolean).join(" ").toLocaleLowerCase();
    return matchesCategory && searchable.includes(query.trim().toLocaleLowerCase());
  }), [items, category, query, exampleMode]);
  const selected = items.find((item) => item.id === selectedId) ?? shownDetail;
  // 可见范围变更用 PUT 返回的服务端值回写，不另发一轮 GET：
// 只更新同 id 的当前详情，保留已加载的版本历史；列表由页面刷新时取真实值。
  function applyVisibilityUpdate(updated: KnowledgeItem) {
    if (updated.id !== selectedId) return;
    setDetail((current) => current && current.id === updated.id ? { ...current, visibility: updated.visibility } : current);
  }
  const inspector = selected && (!location.reference || shownDetail) ? <KnowledgeInspector key={selected.id} detail={shownDetail} detailError={detailError} exampleMode={exampleMode} item={selected} viewVersion={viewVersion} onViewVersion={setViewVersion} onVisibilityChanged={applyVisibilityUpdate} onContentSaved={(updated) => { setDetail(updated); setViewVersion(null); onRefresh(); }} /> : undefined;

  return <AppShell inspector={inspector} inspectorTitle="资料详情" inspectorOpenKey={shownDetail && location.reference ? locateRaw ?? undefined : undefined}>
    <div className={styles.page}>
      {location.error || (location.reference && detailError) ? <p role="alert">{location.error ?? detailError}</p> : null}
      {exampleMode ? <div className={styles.exampleBanner}>{exampleLabel ?? "隔离视觉示例"} · 所有名称、分类、摘要和状态均非真实服务数据</div> : null}
      {!exampleMode ? <nav aria-label="知识库集合" className={styles.collectionTabs}><button aria-pressed={collection === "sources"} onClick={() => setCollection("sources")} type="button">资料</button><button aria-pressed={collection === "scripts"} onClick={() => setCollection("scripts")} type="button">文案库</button></nav> : null}
      <div hidden={collection !== "sources"}>
      {exampleMode ? <div className={styles.top}>
        <nav aria-label="知识用途分类" className={styles.categories}>
          {exampleMode ? categories.map((name, index) => <button aria-pressed={category === name} className={styles.category} key={name} onClick={() => setCategory(category === name ? "全部" : name)} type="button">
            <span className={styles.categoryIcon}>{[<BookOpen key="a" size={20} />, <FileText key="b" size={20} />, <FileText key="c" size={20} />, <BookOpen key="d" size={20} />][index]}</span>
            <strong>{name}</strong>
            <small>{items.filter((item) => item.exampleCategory === name).length + " 条示例来源"}</small>
          </button>) : null}
        </nav>
      </div> : null}
      <div className={styles.searchBar}>
        <label data-input-group><Search size={16} /><input ref={searchInput} aria-label="查找来源" onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、正文或文件引用" value={query} /></label>
        {query ? <button aria-label="清除搜索" onClick={() => { setQuery(""); searchInput.current?.focus(); }} type="button">清除</button> : null}
        {exampleMode ? <button aria-pressed={category === "全部"} onClick={() => setCategory("全部")} type="button">全部来源</button> : <AppSelect aria-label="资料格式" value={category} onValueChange={newValue => setCategory(newValue)}><AppSelectOption value="全部">全部格式</AppSelectOption>{Array.from(new Set(items.map(item => item.content_type))).map(type => <AppSelectOption key={type} value={type}>{itemType(type)}</AppSelectOption>)}</AppSelect>}
        <button aria-label="刷新知识库" disabled={loading} onClick={onRefresh} type="button"><RefreshCw className={loading ? styles.spinning : undefined} size={16} /></button>
        <Link className={styles.addContent} href="/sources">添加内容 <Link2 size={14} /></Link>
      </div>
      {error ? <section className={styles.error} role="alert"><AlertCircle size={17} /><div><strong>{error}</strong><p>{items.length ? "以下为上次成功读取的资料；刷新成功后更新。" : "恢复服务或确认工作区授权后重试。"}</p><button onClick={onRefresh} type="button">重试</button></div></section> : null}
      <section className={styles.card}>
          <div className={styles.cardHeading}><div><h2>资料集合</h2><small>{loading ? "正在读取" : error ? "读取失败" : visibleItems.length + " 条" + (exampleMode ? "示例" : "当前工作区") + "结果"} · 选择资料查看正文</small></div><DisplayModeToggle label="资料列表" value={sourcesView} onChange={setSourcesView} /></div>
          {loading && !items.length ? <div className={styles.empty}><LoaderCircle className={styles.spinning} size={18} />正在读取知识库…</div>
            : visibleItems.length ? <div className={`${styles.grid} ${styles.sourceList}${sourcesView === "list" ? ` ${styles.list}` : ""}`}>{visibleItems.map((item) => <button className={styles.cover} aria-pressed={selectedId === item.id} data-open-details key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                <span className={styles.coverArt}>{itemIcon(item.content_type)}<small>{itemType(item.content_type)}</small></span>
                <strong>{item.title}</strong><small>{exampleMode ? item.exampleCategory : itemType(item.content_type)} · 版本 {item.version}</small>
                <span className={styles.coverStatus}>{exampleMode ? item.exampleIssue ?? "示例状态" : visibilityLabel(item.visibility)}</span>
              </button>)}</div>
            : error ? null : <div className={styles.empty}>{items.length ? "没有匹配的来源" : "知识库还没有内容"}{!items.length ? <Link href="/sources">去信息源添加内容</Link> : null}</div>}
      </section>
      {loading && items.length ? <p role="status" className={styles.notWired}>正在更新资料列表…</p> : null}
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

function KnowledgeInspector({ item, detail, detailError, exampleMode, viewVersion, onViewVersion, onVisibilityChanged, onContentSaved }: { item: KnowledgeDisplayItem; detail: KnowledgeItemDetail | null; detailError: string | null; exampleMode: boolean; viewVersion: string | null; onViewVersion: (version: string | null) => void; onVisibilityChanged: (updated: KnowledgeItem) => void; onContentSaved: (updated: KnowledgeItemDetail) => void }) {
  const [versionCardOpen, setVersionCardOpen] = useState(false);
  const [editDraft, setEditDraft] = useState<{ expected_version: string; title: string; body: string } | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [latestForReview, setLatestForReview] = useState<KnowledgeItemDetail | null>(null);
  async function saveContent() {
    if (!editDraft || editBusy) return;
    setEditBusy(true); setEditError(null);
    try {
      const updated = await editKnowledgeItem(item.id, editDraft);
      onContentSaved(updated); setEditDraft(null);
    } catch (cause) { setEditError(sourceErrorMessage(cause)); }
    finally { setEditBusy(false); }
  }
  async function readLatestForEdit() {
    if (!editDraft || editBusy) return;
    setEditBusy(true);
    try {
      const latest = await getKnowledgeItem(item.id);
      setLatestForReview(latest);
      setEditDraft(current => current ? { ...current, expected_version: latest.version } : current);
      setEditError(null);
    } catch (cause) { setEditError(sourceErrorMessage(cause)); }
    finally { setEditBusy(false); }
  }
  const [grantCardOpen, setGrantCardOpen] = useState(false);
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
  function referenceShown(locator?: string) {
    if (!detail || exampleMode) return;
    referenceAssistantKnowledge({ knowledge_item_id: item.id, version: shown.version, label: shown.title, ...(locator ? { locator } : {}) }, true);
  }
  return <div className={styles.inspector}>
    {exampleMode ? <div className={styles.exampleBanner}>示例详情 · 不会保存或引用到任务</div> : null}
    <header><strong>{shown.title}</strong><small>{itemType(item.content_type)} · 版本 {shown.version ?? item.version} · {formatTime(item.created_at)}</small></header>
    {!exampleMode ? <button type="button" disabled={!detail || Boolean(detailError)} onClick={() => referenceShown()}>引用当前查看版本到对话</button> : null}
    {versions.length ? <div className={styles.detailSection}><h3>版本与出处</h3><button onClick={() => setVersionCardOpen(true)} type="button">选择查看版本</button>{isHistorical ? <p>正在查看历史版本 {selectedVersion?.version}：正文、出处和参数都来自这一版，不是当前版本。<button onClick={() => onViewVersion(null)} type="button">回到当前版本</button></p> : null}</div> : null}
    {versionCardOpen ? <HomeCardDialog kind="config" title="版本与出处" onClose={() => setVersionCardOpen(false)}><div className={styles.inspector}><CardSelect label="查看版本" value={viewVersion ?? ""} options={[{ value: "", label: `当前版本 ${detail?.version}` }, ...versions.map(version => ({ value: version.version, label: `版本 ${version.version} · ${formatTime(version.saved_at)}` }))]} onChange={version => onViewVersion(version || null)} /><p>只切换查看版本，不修改资料或引用已绑定的版本。</p><p>原网址：{shownWebUrl ?? "无网页来源"}</p><p>抓取时间：{formatFetchTime(shownFetchedAt)}</p></div></HomeCardDialog> : null}
    {!exampleMode ? <button type="button" onClick={() => setGrantCardOpen(true)}>工作空间授权</button> : null}
    {!exampleMode && grantCardOpen ? <HomeCardDialog kind="config" title="工作空间授权" onClose={() => setGrantCardOpen(false)}><section className={styles.detailSection}><h3>授权给其他工作空间</h3>
      <p>保留原工作空间、所有者和版本。授权允许目标空间读取和引用；撤销后停止新访问与后续引用。</p>
      {grantState && grantState.target_resolution !== "resolved" ? <><p role="status">{grantState.target_resolution === "same_workspace" ? "当前只有同一工作空间的本地成员，无法授权其他工作空间。" : "暂时无法确定可授权的工作空间，请检查成员连接。"}</p><details><summary>授权诊断</summary><p>{grantState.target_resolution_message} · {grantState.cross_workspace_status}</p></details></> : null}
      {grantState?.can_manage ? <>
        {grantState.target_resolution === "resolved" ? <><label className={styles.grantTarget}><span>目标工作空间</span><AppSelect aria-label="目标工作空间" disabled={grantBusy} value={targetWorkspace} onValueChange={newValue => setTargetWorkspace(newValue)}>
          <AppSelectOption value="">请选择同设备工作空间</AppSelectOption>
          {grantState.targets.map(target => <AppSelectOption key={target.workspace_id} value={target.workspace_id}>{target.name}</AppSelectOption>)}
        </AppSelect></label>
        {!grantState.targets.length ? <p>当前没有其他可授权的同设备工作空间。</p> : null}
        <button disabled={grantBusy || !targetWorkspace} onClick={() => setGrantConfirmation({ workspaceId: targetWorkspace })} type="button">授予读取与引用</button>
        </> : null}
        <ul className={styles.grantList}>{grantState.grants.map(grant => <li key={grant.id}>
          <span>{grantState.targets.find(target => target.workspace_id === grant.target_workspace_id)?.name ?? grant.target_workspace_id} · {grant.revoked_at ? "已撤销" : "可读与引用"}</span>
          {!grant.revoked_at ? <button disabled={grantBusy} onClick={() => setGrantConfirmation({ grantId: grant.id })} type="button">撤销授权</button> : null}
        </li>)}</ul>
      </> : grantState ? <p>原工作空间：{grantState.source_workspace_id}。当前可读取资料，无权向其他空间授予或转移归属。</p> : !grantError ? <p>正在读取授权状态…</p> : null}
      {grantError ? <p role="alert">{grantError}</p> : null}
    </section>
    {grantConfirmation ? <HomeCardDialog kind="confirmation" title={"workspaceId" in grantConfirmation ? "跨空间授权确认" : "撤销跨空间授权"} onClose={() => setGrantConfirmation(null)} footer={<><button data-card-cancel type="button" disabled={grantBusy} onClick={() => setGrantConfirmation(null)}>取消</button><button type="button" disabled={grantBusy} onClick={async () => {
      if ("workspaceId" in grantConfirmation) await grantToWorkspace(grantConfirmation.workspaceId);
      else await revokeGrant(grantConfirmation.grantId);
      setGrantConfirmation(null);
    }}>{"workspaceId" in grantConfirmation ? "确认授予读取与引用" : "确认撤销授权"}</button></>}><p>资料：{item.title}</p><p>{"workspaceId" in grantConfirmation ? `目标空间：${grantState?.targets.find(target => target.workspace_id === grantConfirmation.workspaceId)?.name ?? grantConfirmation.workspaceId}。该空间将可读取与引用这条资料；原空间、所有者和版本保持不变。` : "撤销后，目标空间停止新访问与后续引用；已引用的固定版本依现有授权规则核验，不删除原资料。"}</p></HomeCardDialog> : null}
    </HomeCardDialog> : null}

    <div className={styles.compare}>
      <section><h3>{exampleMode ? "原始来源片段 · 示例" : "已保存正文"}</h3>{!sections.length ? <p>{shown.body || "这个条目没有可显示的正文。"}</p> : null}{sections.length ? <ol>{sections.map(section => <li key={section.locator}><strong>{section.locator}{section.title ? ` ${section.title}` : ""}</strong><pre>{section.text}</pre>{!exampleMode ? <button type="button" disabled={!detail || Boolean(detailError)} onClick={() => referenceShown(section.locator)}>引用{section.locator}</button> : null}</li>)}</ol> : null}</section>
      {exampleMode ? <section><h3>AI 摘要 · 示例</h3><p>{item.exampleSummary ?? "示例暂无摘要"}</p></section> : null}
    </div>
    <section className={styles.detailSection}><h3>参数与出处</h3>{detailError ? <p role="alert">{detailError}</p> : parameters.length ? <table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>出处</th></tr></thead><tbody>{parameters.map(parameter => <tr key={`${parameter.locator}-${parameter.key}`}><td>{parameter.key}</td><td>{parameter.value}</td><td>{parameter.unit ?? "—"}</td><td>{parameter.locator}</td></tr>)}</tbody></table> : <p>{exampleMode ? item.exampleParameters ?? "示例暂无参数" : "此版本未记录结构化参数。"}</p>}<details><summary>资料记录</summary><dl><div><dt>来源</dt><dd>{item.source_item_id ? "已关联信息源" : "当前条目无信息源关联"}</dd></div><div><dt>文件引用</dt><dd>{shown.file_reference || "无"}</dd></div><div><dt>{isHistorical ? "该版来源条目" : "内容指纹"}</dt><dd>{isHistorical ? (selectedVersion?.source_item_id ?? "无") : ((detail?.content_hash ?? item.content_hash) ?? "无")}</dd></div><div><dt>原网址</dt><dd>{shownWebUrl ? <a href={shownWebUrl} rel="noreferrer" target="_blank">{shownWebUrl}</a> : "无网页来源"}</dd></div><div><dt>{isHistorical ? "该版抓取时间" : "抓取时间"}</dt><dd>{formatFetchTime(shownFetchedAt)}</dd></div><div><dt>可见范围</dt><dd>{visibility === "shared" ? "团队共享" : "仅自己"}</dd></div><div><dt>归属</dt><dd>{item.ownership || "未记录"}</dd></div><div><dt>授权范围</dt><dd>{item.authorization || "未记录"}</dd></div><div><dt>版本</dt><dd>{shown.version ?? item.version}</dd></div></dl>{versions.length > 1 ? <ol>{versions.map(version => <li key={version.version}>版本 {version.version} · {formatTime(version.saved_at)} · {version.content_hash ?? "无指纹"}</li>)}</ol> : null}</details></section>
    <div className={styles.detailSection}><h3>可见范围</h3>{exampleMode ? <p>示例：可见范围设置不会保存。</p> : <><p>{grantState?.access_via_grant ? "原空间的可见范围保持不变；你通过显式授权读取和引用这条资料。" : visibility === "shared" ? "当前与团队成员共享，成员可读取这条资料。" : "当前仅自己可见，成员读不到这条资料。"}</p><div className={styles.detailActions}>{grantState?.can_manage ? visibility === "private" ? <button disabled={visibilityBusy} onClick={() => void changeVisibility("shared")} type="button">共享给团队</button> : <button disabled={visibilityBusy} onClick={() => void changeVisibility("private")} type="button">收回为仅自己可见</button> : null}</div>{visibilityError ? <p role="alert">{visibilityError}</p> : null}</>}</div>
    <section className={styles.detailSection}><h3>修订资料</h3>
      <p>核对原始来源后，可修订自己保存的标题与正文。保存新版本后，旧引用仍指向原来的版本。</p>
      <div className={styles.detailActions}>
        {!exampleMode && grantState?.can_manage && !isHistorical && detail ? <button onClick={() => { setEditDraft({ expected_version: detail.version, title: detail.title, body: detail.body ?? "" }); setEditError(null); setLatestForReview(null); }} type="button">编辑标题与正文</button> : <p>{isHistorical ? "历史版本只供回查，编辑请回到当前版本。" : "只有原工作区的资料所有者可以修订。"}</p>}
        {!exampleMode && detail ? <Link href={`/production?new=1&knowledge_item_id=${encodeURIComponent(item.id)}&knowledge_version=${encodeURIComponent(shown.version)}`}>用于新任务 <ArrowRight size={14} /></Link> : null}
        <Link href="/sources">查看资料来源 <ArrowRight size={14} /></Link>
      </div>
    </section>
    {editDraft ? <HomeCardDialog kind="config" title="修订资料" onClose={() => { if (!editBusy) setEditDraft(null); }} footer={<><button type="button" disabled={editBusy} onClick={() => setEditDraft(null)}>取消</button><button type="button" disabled={editBusy || !editDraft.title.trim() || !editDraft.body.trim()} onClick={() => void saveContent()}>{editBusy ? "正在保存…" : "保存新版本"}</button></>}>
      <div className={styles.editForm}><label><span>资料标题</span><input aria-label="资料标题" maxLength={200} value={editDraft.title} onChange={event => setEditDraft({ ...editDraft, title: event.target.value })} /></label>
      <label><span>资料正文</span><textarea aria-label="资料正文" maxLength={200000} value={editDraft.body} onChange={event => setEditDraft({ ...editDraft, body: event.target.value })} /></label>
      <p>参数可写成“- 重量: 20 kg”这样的列表或参数表格。修改后的段落重新定位，原文件页码保留在旧版本。</p>
      <p>正在修改版本 {editDraft.expected_version}。本次保存不修改源文件。</p>{editError ? <div role="alert"><p>{editError}</p><button type="button" disabled={editBusy} onClick={() => void readLatestForEdit()}>读取当前版本，保留我的修改</button></div> : null}{latestForReview ? <details open><summary>请核对当前版本 {latestForReview.version} 后保存</summary><strong>{latestForReview.title}</strong><p>{latestForReview.body}</p></details> : null}</div>
    </HomeCardDialog> : null}
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
    : source.status === "version_changed" ? `${source.title ?? "知识资料"} · 引用版本 ${source.version_or_identity ?? "未知"} · 当前版本或授权已变化` : `${source.title ?? "知识资料"} · 不可读`;
  const SourceStatusIcon = source.status === "version_changed" ? RotateCcw : source.status === "missing" ? CircleMinus : CircleCheck;

  return <li className={styles.citation}>
    <p className={styles.citationStatus}><SourceStatusIcon aria-hidden className={source.status === "version_changed" ? styles.statusUpdate : source.status === "missing" ? styles.statusUnavailable : styles.statusReady} size={14} />{statusLabel}{source.source_url ? <> · <a href={source.source_url} rel="noreferrer" target="_blank">原始来源</a></> : null}</p>
    <details><summary>技术诊断信息</summary><small>引用记录：{source.reference_id}</small></details>
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

type LibraryReusePending = { key: string; entry_key: string; entry_id: string; entry_version: number; entry_name: string; input: ScriptLibraryReuseInput };
export function ScriptLibraryPanel() {
  const router = useRouter();
  const [entries, setEntries] = useState<ScriptLibraryEntryHeader[]>([]);
  const [scriptsView, setScriptsView] = useState<DisplayMode>("grid");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<ScriptLibraryEntryHeader | null>(null);
  const [listCursors, setListCursors] = useState<(ScriptLibraryCursor | null)[]>([null]);
  const listCursor = listCursors[listCursors.length - 1];
  const [nextCursor, setNextCursor] = useState<ScriptLibraryCursor | null>(null);
  const [deepLinkRetry, setDeepLinkRetry] = useState(0);
  const [deepLinkError, setDeepLinkError] = useState<string | null>(null);
  const selectionEpoch = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const successfulFilter = useRef<{ query: string; category: string } | null>(null);
  const selectedKey = selected ? JSON.stringify([selected.id, selected.version, selected.updated_at]) : null;
  const [sourceResult, setSourceResult] = useState<{ key: string; sources: ScriptLibrarySource[] } | null>(null);
  const [sourceFailure, setSourceFailure] = useState<{ key: string; message: string } | null>(null);
  const [name, setName] = useState("");
  const [ratio, setRatio] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [duration, setDuration] = useState("60");
  const durationSeconds = Number(duration);
  const durationValid = Number.isSafeInteger(durationSeconds) && durationSeconds > 0;
  const [pendingReuse, setPendingReuse] = useWorkspaceViewState<Record<string, LibraryReusePending>>("library-reuse-inputs", {});
  const pendingReuseRef = useRef(pendingReuse);
  pendingReuseRef.current = pendingReuse;
  const [busyRequests, setBusyRequests] = useState(new Set<string>());
  const busyRef = useRef(busyRequests);
  const reuseActive = useRef(true);
  useEffect(() => { reuseActive.current = true; return () => { reuseActive.current = false; }; }, []);
  const [reuseReceipts, setReuseReceipts] = useState<Record<string, { original: LibraryReusePending; receipt: ScriptLibraryReuseReceipt }>>({});
  const [loading, setLoading] = useState(true);
  const [listRetry, setListRetry] = useState(0);
  const [sourceRetry, setSourceRetry] = useState(0);
  const [listError, setListError] = useState<string | null>(null);
  const [reuseError, setReuseError] = useState<{ original: LibraryReusePending; message: string; stale: boolean } | null>(null);
  const [confirmedKey, setConfirmedKey] = useState<string | null>(null);
  const [loadedFilter, setLoadedFilter] = useState<{ query: string; category: string } | null>(null);
  const selectedId = selected?.id ?? null;
  const previousSelectedKey = useRef<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const entryId = params.get("entry_id"), sourceTaskId = params.get("source_task_id");
    if (!entryId) return;
    let active = true;
    const epoch = selectionEpoch.current;
    setDeepLinkError(null);
    void getScriptLibraryEntry(entryId).then(entry => {
      if (!active || epoch !== selectionEpoch.current) return;
      if (entry.id !== entryId || sourceTaskId && entry.source_task_id !== sourceTaskId) {
        setDeepLinkError("文案版本与来源任务不匹配；没有打开其他版本。"); return;
      }
      setSelected(entry);
      setName(`${entry.name} · 新任务`);
    }).catch(cause => { if (active && epoch === selectionEpoch.current) setDeepLinkError(cause instanceof Error ? cause.message : "链接中的文案版本未能读回。"); });
    return () => { active = false; };
  }, [deepLinkRetry]);
  useEffect(() => {
    if (previousSelectedKey.current !== selectedKey) {
      previousSelectedKey.current = selectedKey;
      setConfirmedKey(null);
    }
  }, [selectedKey]);
  useEffect(() => {
    let active = true;
    setLoading(true); setListError(null);
    const observedSelection = selectedRef.current;
    const read = listCursor ? listScriptLibraryEntries(query, category, listCursor) : listScriptLibraryEntries(query, category);
    read.then(result => {
      if (active) {
        const previous = successfulFilter.current;
        const changedFilter = previous !== null && (previous.query !== query || previous.category !== category);
        successfulFilter.current = { query, category };
        setEntries(result.items);
        setNextCursor(result.next);
        setLoadedFilter({ query, category });
        setSelected(current => {
          if (!current) return null;
          if (observedSelection?.id !== current.id || observedSelection.version !== current.version || observedSelection.updated_at !== current.updated_at) return current;
          return result.items.find(entry => entry.id === current.id) ?? (changedFilter ? null : current);
        });
      }
    }).catch(cause => {
      if (active) setListError(cause instanceof Error ? cause.message : "文案库读取失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [query, category, listRetry, listCursor]);
  useEffect(() => {
    if (!selectedId || !selectedKey) return;
    let active = true;
    const key = selectedKey;
    getScriptLibrarySources(selectedId).then(sources => { if (active) { setSourceResult({ key, sources }); setSourceFailure(null); } })
      .catch(cause => { if (active) setSourceFailure({ key, message: cause instanceof Error ? cause.message : "来源核对失败" }); });
    return () => { active = false; };
  }, [selectedId, selectedKey, sourceRetry]);
  function select(entry: ScriptLibraryEntryHeader) {
    selectionEpoch.current += 1;
    setDeepLinkError(null);
    const sameSelection = selected?.id === entry.id && selected.version === entry.version && selected.updated_at === entry.updated_at;
    setSelected(entry);
    if (!sameSelection) {
      setConfirmedKey(null); setName(`${entry.name} · 新任务`); setReuseError(null);
    }
  }
  async function reloadSelected() {
    const original = selectedRef.current;
    if (!original) return;
    const epoch = selectionEpoch.current;
    const entry = await getScriptLibraryEntry(original.id);
    if (epoch !== selectionEpoch.current || selectedRef.current?.id !== original.id || selectedRef.current.updated_at !== original.updated_at || selectedRef.current.version !== original.version) return;
    if (entry.id !== original.id || entry.source_task_id !== original.source_task_id) throw new Error("文案回执与原版本归属不符，原选择保留。");
    setSelected(entry);
  }
  function requestBusy(requestId: string, busy: boolean) {
    const next = new Set(busyRef.current);
    if (busy) next.add(requestId); else next.delete(requestId);
    busyRef.current = next; setBusyRequests(next);
  }
  function removePending(original: LibraryReusePending) {
    if (pendingReuseRef.current[original.key]?.input.request_id !== original.input.request_id) return;
    const next = { ...pendingReuseRef.current }; delete next[original.key];
    pendingReuseRef.current = next; setPendingReuse(next);
  }
  function settleReuse(original: LibraryReusePending, receipt: ScriptLibraryReuseReceipt) {
    if (receipt.status !== 'saved' || receipt.request_id !== original.input.request_id || receipt.entry_id !== original.entry_id
        || receipt.entry_version !== original.entry_version || !receipt.task_id || !receipt.script_id) {
      throw new Error('原复用回执与请求或库稿版本不符，原 UUID 和当前新任务草稿保留。');
    }
    removePending(original);
    setReuseReceipts(current => ({ ...current, [receipt.request_id]: { original, receipt } }));
    setReuseError(null);
    if (currentReuse.current.key === original.key && currentReuse.current.confirmed) router.push(`/tasks/${encodeURIComponent(receipt.task_id)}`);
  }
  async function reuse() {
    if (!selected || !reuseKey || !confirmed || !readable || !name.trim() || !durationValid || pendingReuseRef.current[reuseKey]) return;
    const original: LibraryReusePending = { key: reuseKey, entry_id: selected.id, entry_version: selected.version, entry_name: selected.name,
      entry_key: selectedKey!, input: { request_id: crypto.randomUUID(), expected_entry_updated_at: selected.updated_at,
        name, aspect_ratio: ratio, duration_target_seconds: durationSeconds, requirements_confirmed: true } };
    const next = { ...pendingReuseRef.current, [original.key]: original };
    pendingReuseRef.current = next; setPendingReuse(next);
    requestBusy(original.input.request_id, true); setReuseError(null);
    try {
      const receipt = await reuseScriptLibraryEntry(original.entry_id, original.input);
      if (reuseActive.current) settleReuse(original, receipt);
    } catch (cause) {
      if (!reuseActive.current) return;
      const rejected = isScriptLibraryWriteRejected(cause);
      if (rejected) removePending(original);
      setReuseError({ original, message: cause instanceof Error ? cause.message : '原复用结果未能读回，当前草稿保留。', stale: rejected && cause instanceof CreationPlanApiError && cause.status === 409 });
    } finally { if (reuseActive.current) requestBusy(original.input.request_id, false); }
  }
  async function inspectReuse(original: LibraryReusePending) {
    if (busyRef.current.has(original.input.request_id)) return;
    requestBusy(original.input.request_id, true); setReuseError(null);
    try {
      const receipt = await getScriptLibraryRequest(original.input.request_id, 'reuse');
      if (reuseActive.current) settleReuse(original, receipt);
    } catch (cause) { if (reuseActive.current) setReuseError({ original, stale: false,
      message: cause instanceof CreationPlanApiError && cause.status === 404 ? '尚未查到原复用回执，不代表未提交；原 UUID 和当前新任务草稿继续保留。'
        : cause instanceof Error ? cause.message : '原复用回执未能读回，没有重新创建任务。' }); }
    finally { if (reuseActive.current) requestBusy(original.input.request_id, false); }
  }
  const sourcesChecked = Boolean(selectedKey && sourceResult?.key === selectedKey);
  const sources = sourcesChecked ? sourceResult!.sources : [];
  const sourceError = selectedKey && sourceFailure?.key === selectedKey ? sourceFailure.message : null;
  const confirmed = Boolean(selectedKey && confirmedKey === selectedKey);
  const readable = sourcesChecked && sources.every(source => source.status === "readable");
  const reuseKey = selectedKey && durationValid ? JSON.stringify([selectedKey, name, ratio, durationSeconds]) : null;
  const currentReuse = useRef({ key: reuseKey, confirmed });
  currentReuse.current = { key: reuseKey, confirmed };
  const currentPending = reuseKey ? pendingReuse[reuseKey] : null;
  const busy = Boolean(currentPending && busyRequests.has(currentPending.input.request_id));
  return <section className={styles.card} aria-label="文案库">
    <div className={styles.cardHeading}><div><h2>文案库</h2><small>已确认文案的命名快照与历史版本；复用需要新确认。</small></div><DisplayModeToggle label="文案列表" value={scriptsView} onChange={setScriptsView} /></div>
    <div className={styles.searchBar}>
      <label data-input-group><Search size={16} /><input aria-label="搜索文案" placeholder="搜索名称或正文" value={query} onChange={event => { selectionEpoch.current += 1; setListCursors([null]); setQuery(event.target.value); }} /></label>
      <label>分类<input aria-label="筛选文案分类" placeholder="输入分类，留空查看全部" value={category} onChange={event => { selectionEpoch.current += 1; setListCursors([null]); setCategory(event.target.value); }} /></label>
    </div>
    {deepLinkError ? <div role="alert"><p>{deepLinkError}</p><button type="button" onClick={() => setDeepLinkRetry(value => value + 1)}>重试读取链接文案</button></div> : null}
    {listError ? <div className={styles.error} role="alert"><div><strong>{listError}</strong>{entries.length ? <p>保留上次成功的结果：{loadedFilter ? `搜索“${loadedFilter.query || "全部"}”，分类“${loadedFilter.category || "全部"}”` : "尚无成功读取的筛选"}。</p> : null}<button onClick={() => setListRetry(value => value + 1)} type="button">重试读取文案</button></div></div> : null}
    {loading ? <p role="status">{loadedFilter && entries.length ? "正在读取；保留上次成功的结果…" : "正在读取文案…"}</p> : null}
    {loading && (loadedFilter?.query !== query || loadedFilter?.category !== category) ? null : listError && entries.length ? <div className={`${styles.grid} ${styles.scriptList}${scriptsView === "list" ? ` ${styles.list}` : ""}`}>{entries.map(entry =>
      <button className={styles.cover} key={entry.id} onClick={() => void select(entry)} type="button"><strong>{entry.name}</strong><small>{entry.category || "未分类"} · 版本 {entry.version}</small><span>{entry.title}</span></button>
    )}</div> : entries.length ? <div className={`${styles.grid} ${styles.scriptList}${scriptsView === "list" ? ` ${styles.list}` : ""}`}>{entries.map(entry =>
      <button className={styles.cover} key={entry.id} onClick={() => void select(entry)} type="button"><strong>{entry.name}</strong><small>{entry.category || "未分类"} · 版本 {entry.version}</small><span>{entry.title}</span></button>
    )}</div> : loading ? null : listError ? null : <p>没有匹配的可用文案。可从任务的已确认文案页保存；来源不可读或版本已变化的条目不会用于复用。</p>}
    <p>当前列表页 {entries.length} 条{nextCursor ? "；还有更多可用文案。" : "。"}</p>
    <div><button type="button" disabled={loading || listCursors.length === 1} onClick={() => setListCursors(current => current.slice(0, -1))}>上一页文案条目</button><button type="button" disabled={loading || Boolean(listError) || !nextCursor || loadedFilter?.query !== query || loadedFilter.category !== category} onClick={() => setListCursors(current => [...current, nextCursor])}>下一页文案条目</button></div>
    {selected ? <section className={styles.detailSection} aria-label="文案复用详情">
      <h3>{selected.name} · 版本 {selected.version}</h3>
      <Link href={`/tasks/${encodeURIComponent(selected.source_task_id)}`}>查看原任务</Link>
      <p>{selected.source_run_id ? "来源于已确认的文案记录。" : "复用或人工文案，没有模型生成记录。"}</p>
      <ScriptLibrarySections key={selectedKey} entry={selected} onReload={reloadSelected}/>
      <details><summary>技术诊断信息</summary><p>原稿记录：{selected.source_script_id || "无"} · 运行记录：{selected.source_run_id || "无"}</p></details>
      <h4>事实出处（实时核对）</h4>
      {sourceError ? <div role="alert"><p>{sourceError}</p><button onClick={() => { setSourceFailure(null); setSourceRetry(value => value + 1); }} type="button">重试核对来源</button></div> : !sourcesChecked ? <p role="status">正在核对来源…</p> : !sources.length ? <p>本人文案没有外部事实引用。</p> : <ul className={styles.citationList}>{sources.map(source => <CitationSource key={`${selected.id}:${selected.version}:${source.reference_id}:${source.status}:${source.version_or_identity}`} source={source} />)}</ul>}

      <label className={styles.correctionField}><span>新任务名称</span><input value={name} onChange={event => { setName(event.target.value); setConfirmedKey(null); }} /></label>
      <label className={styles.correctionField}><span>新任务画幅</span><AppSelect value={ratio} onValueChange={newValue => { setRatio(newValue as typeof ratio); setConfirmedKey(null); }}><AppSelectOption value="9:16">9:16</AppSelectOption><AppSelectOption value="16:9">16:9</AppSelectOption><AppSelectOption value="1:1">1:1</AppSelectOption></AppSelect></label>
      <label className={styles.correctionField}><span>目标时长（秒）</span><input type="number" min={1} step={1} value={duration} onChange={event => { setDuration(event.target.value); setConfirmedKey(null); }} /></label>
      <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmedKey(event.target.checked ? selectedKey : null)} />确认以上新任务要求，正文仍需重新确认</label>
      <p>仅复制文案到独立新draft，不复制旧确认、配音或成片，不调用模型。</p>
      <button disabled={busy || Boolean(currentPending) || !confirmed || !readable || !name.trim() || !durationValid} onClick={() => void reuse()} type="button">{busy ? "正在核对原复用请求…" : currentPending ? "原复用结果待核对" : "创建待确认新稿"}</button>
    </section> : null}
    {Object.values(pendingReuse).map(original => <div key={original.input.request_id} aria-label="原复用请求">
      <p role="status">「{original.entry_name}」版本 {original.entry_version} 的原复用请求 {original.input.request_id} 结果尚待核对；不重复创建任务。</p>
      <p>原新任务要求：{original.input.name} · {original.input.aspect_ratio} · {original.input.duration_target_seconds} 秒。</p>
      <button type="button" disabled={busyRequests.has(original.input.request_id)} onClick={() => void inspectReuse(original)}>查询原复用结果</button>
    </div>)}
    {reuseError ? <div role="alert"><p>原复用请求 {reuseError.original.input.request_id}（「{reuseError.original.entry_name}」版本 {reuseError.original.entry_version}）：{reuseError.message}</p>
      {reuseError.stale && selectedKey === reuseError.original.entry_key ? <button type="button" onClick={() => { void reloadSelected().then(() => setReuseError(null)).catch(cause => setReuseError(current => current ? { ...current, message: cause instanceof Error ? cause.message : "所选文案版本未能读回。" } : current)); }}>重新读取所选文案版本</button> : null}
    </div> : null}
    {Object.values(reuseReceipts).map(({ original, receipt }) => <div key={receipt.request_id} aria-label="原复用成功回执"><p role="status">原请求已从「{original.entry_name}」版本 {receipt.entry_version} 创建独立待确认新稿「{original.input.name}」，尚未采用。</p><Link href={`/tasks/${encodeURIComponent(receipt.task_id)}`}>打开原请求创建的任务</Link></div>)}
  </section>;
}

function ScriptLibrarySections({ entry, onReload }: { entry: ScriptLibraryEntryHeader; onReload: () => Promise<void> }) {
  const [cursors, setCursors] = useState([0]);
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState<ScriptLibrarySectionWindow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const active = useRef(true);
  const after = cursors[cursors.length - 1];
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setLoading(true); setError(null); setPage(null);
    void getScriptLibrarySections(entry.id, { after_ordinal: after, expected_updated_at: entry.updated_at }).then(value => {
      if (!current) return;
      if (value.entry_id !== entry.id || value.version !== entry.version || value.updated_at !== entry.updated_at) throw new Error("正文窗口与所选文案版本不符，请重新读取版本。");
      setPage(value);
    }).catch(cause => { if (current) setError(cause instanceof Error ? cause.message : "文案正文窗口未能读回。"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [entry.id, entry.version, entry.updated_at, after, attempt]);
  async function reload() {
    setReloading(true);
    try { await onReload(); if (active.current) { setCursors([0]); setAttempt(value => value + 1); } }
    catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : "文案版本未能重新读回。"); }
    finally { if (active.current) setReloading(false); }
  }
  return <section aria-label="库稿正文窗口">
    {loading ? <p role="status">正在读取文案正文窗口…</p> : null}
    {error ? <div role="alert"><p>{error}</p><button type="button" disabled={reloading} onClick={() => void reload()}>重新读取文案版本</button></div> : null}
    {page ? <><p>正文共 {page.total_count} 段{page.items.length ? ` · 本页第 ${page.items[0].ordinal}–${page.items[page.items.length - 1].ordinal} 段` : ""}</p>
      {page.items.map(({ ordinal, section }) => <article key={section.id}><span>第 {ordinal} 段</span><strong>{section.title}</strong><p>{section.content}</p>{section.reference_ids.length ? <details><summary>技术诊断信息</summary><small>引用记录：{section.reference_ids.join("、")}</small></details> : null}</article>)}
      {page.total_count > page.items.length ? <div><button type="button" disabled={cursors.length === 1} onClick={() => setCursors(current => current.slice(0, -1))}>上一页文案正文</button><button type="button" disabled={page.next_after_ordinal === null} onClick={() => setCursors(current => [...current, page.next_after_ordinal!])}>下一页文案正文</button></div> : null}
    </> : null}
  </section>;
}
