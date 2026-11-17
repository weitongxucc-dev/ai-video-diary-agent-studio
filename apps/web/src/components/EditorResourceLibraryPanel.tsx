"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AudioLines, Check, Film, Grid2X2, Image as ImageIcon, List, Music2, SlidersHorizontal, Sparkles, Sticker, Subtitles, Wand2 } from "lucide-react";
import type { MaterialAsset } from "@/lib/types";
import { fileLabel, mediaLabel, roleLabel, reviewLabel } from "./material-library/material-labels";
import styles from "./EditorResourceLibraryPanel.module.css";

export type EditorResourceCategory = "materials" | "skills" | "stickers" | "filters" | "effects" | "transitions" | "templates" | "subtitles" | "text" | "voice" | "music" | "sfx";

type CategoryState = "available" | "catalog_only" | "planned";
type RemoteStatus = CategoryState | "connected";
type Category = { id: EditorResourceCategory; label: string; description: string; state: CategoryState; nextStep: string; icon: typeof Film };

export const editorResourceCategories: Category[] = [
  { id: "materials", label: "素材", description: "工作区已接入的图片、视频和音频", state: "available", nextStep: "导入素材后即可预览和采用", icon: Film },
  { id: "skills", label: "技能", description: "已审核并随当前版本发布的创作技能", state: "available", nextStep: "通过曜核助手读取技能，并按当前任务工具边界执行", icon: Sparkles },
  { id: "stickers", label: "贴纸", description: "叠加到画面的贴纸资源", state: "available", nextStep: "通过当前任务的编辑提案采用", icon: Sticker },
  { id: "filters", label: "滤镜", description: "颜色与画面风格预设", state: "planned", nextStep: "滤镜应用能力计划中，当前不能采用", icon: SlidersHorizontal },
  { id: "effects", label: "特效", description: "画面动效与遮罩效果", state: "available", nextStep: "通过当前任务的编辑提案采用", icon: Sparkles },
  { id: "transitions", label: "转场", description: "片段之间的过渡效果", state: "available", nextStep: "通过当前任务的编辑提案采用", icon: Wand2 },
  { id: "templates", label: "模板", description: "内容结构与可复用视觉模板", state: "available", nextStep: "打开视频模板管理页查看已接入模板", icon: Grid2X2 },
  { id: "subtitles", label: "字幕", description: "字幕轨和字幕样式资源", state: "available", nextStep: "字幕编辑在当前任务的字幕工作区完成", icon: Subtitles },
  { id: "text", label: "字幕 / 文本", description: "字幕轨、文字卡和排版资源", state: "available", nextStep: "字幕编辑在当前任务的字幕工作区完成", icon: Subtitles },
  { id: "voice", label: "配音", description: "版本化声音档案与试听结果", state: "available", nextStep: "打开人物与声音管理页选择可用声音", icon: AudioLines },
  { id: "music", label: "音乐", description: "背景音乐资源", state: "available", nextStep: "选择音乐后通过当前任务的音轨编辑采用", icon: Music2 },
  { id: "sfx", label: "音效", description: "可追溯的短音效与声音过渡", state: "available", nextStep: "通过当前任务的音频编辑提案采用", icon: AudioLines },
];

export type CatalogResource = { id: string; category_id: string; name: string; version: number; status: RemoteStatus; resource_type: string; preview: { kind: string; duration_seconds?: number | null; thumbnail_ref?: string | null }; provenance: Array<{ repository: string; revision: string; path: string; license: string }>; runtime_requirements?: Record<string, unknown>; entrypoints: string[] };
const apiCategoryId: Record<EditorResourceCategory, string> = { materials: "materials", skills: "skills", stickers: "stickers", filters: "filters", effects: "effects", transitions: "transitions", templates: "templates", subtitles: "subtitles_text", text: "subtitles_text", voice: "voice", music: "music", sfx: "sfx" };

function stateLabel(state: CategoryState) {
  return state === "available" ? "可用" : state === "catalog_only" ? "目录中" : "计划中";
}

function resourceStateLabel(status: RemoteStatus) {
  return status === "connected" ? "可用" : status === "catalog_only" ? "目录中" : "计划中";
}

function assetMatches(asset: MaterialAsset, query: string, filter: string) {
  if (filter !== "all" && asset.media_type !== filter) return false;
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [asset.file_name, asset.user_description, asset.upload_context, ...asset.user_tags].join(" ").toLocaleLowerCase().includes(needle);
}

export function EditorResourceLibraryPanel({
  assets,
  selectedId,
  initialCategory = "materials",
  onSelect,
  onReferenceMaterial,
  renderResourceAction,
}: {
  assets: MaterialAsset[];
  selectedId: string | null;
  initialCategory?: EditorResourceCategory;
  onSelect: (asset: MaterialAsset) => void;
  onReferenceMaterial: (asset: MaterialAsset) => void;
  renderResourceAction?: (resource: CatalogResource) => ReactNode;
}) {
  const [categoryId, setCategoryId] = useState<EditorResourceCategory>(initialCategory);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [categories, setCategories] = useState(editorResourceCategories);
  const [catalogResources, setCatalogResources] = useState<CatalogResource[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [catalogLoading, setCatalogLoading] = useState(true);
  useEffect(() => { setCategoryId(initialCategory); }, [initialCategory]);
  useEffect(() => {
    let active = true;
    setCatalogError(null);
    setCatalogLoading(true);
    setCatalogResources([]);
    void fetch(`/api/editing-resource-catalog?category=${encodeURIComponent(apiCategoryId[categoryId])}`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error("资源目录暂不可用")))
      .then((payload: { categories?: Array<{ id: string; status: RemoteStatus }>; resources?: CatalogResource[] }) => {
        if (!active) return;
        setCatalogResources(payload.resources ?? []);
        setCategories(current => current.map(item => {
          const remote = payload.categories?.find(candidate => candidate.id === apiCategoryId[item.id]);
          return remote ? { ...item, state: remote.status === "connected" ? "available" : remote.status } : item;
        }));
      })
      .catch(() => { if (active) { setCatalogResources([]); setCatalogError("资源目录暂时无法读取，请重试；当前页面不会把它当成空目录。"); } })
      .finally(() => { if (active) setCatalogLoading(false); });
    return () => { active = false; };
  }, [categoryId, catalogRetry]);
  const category = categories.find(item => item.id === categoryId) ?? categories[0];
  const visibleAssets = useMemo(() => assets.filter(asset => assetMatches(asset, query, filter)), [assets, filter, query]);
  const selected = assets.find(asset => asset.id === selectedId) ?? visibleAssets[0] ?? null;

  return <section className={styles.panel} aria-label="编辑器资源面板">
    <header className={styles.header}>
      <div><span className={styles.eyebrow}>资源面板</span><h2>{category.label}</h2><p>{category.description}</p></div>
      <span className={`${styles.state} ${styles[`state_${category.state}`]}`}>{stateLabel(category.state)}</span>
    </header>
    <div className={styles.body}>
      <nav className={styles.rail} aria-label="资源类别">
        <span className={styles.railLabel}>功能资源</span>
        {categories.map(item => { const Icon = item.icon; return <button key={item.id} type="button" className={item.id === categoryId ? styles.activeCategory : ""} aria-pressed={item.id === categoryId} onClick={() => { setCategoryId(item.id); setQuery(""); setFilter("all"); }}><Icon size={15} /><span>{item.label}</span><small>{stateLabel(item.state)}</small></button>; })}
      </nav>
      <div className={styles.content}>
        <div className={styles.toolbar}>
          <label className={styles.search}><span>搜索{category.label}</span><input aria-label={`搜索${category.label}`} type="search" placeholder={`搜索${category.label}`} value={query} onChange={event => setQuery(event.target.value)} /></label>
          <select aria-label="资源类型筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部类型</option><option value="video">视频</option><option value="image">图片</option><option value="audio">音频</option></select>
          <div className={styles.viewToggle} role="group" aria-label="资源显示方式"><button type="button" aria-label="网格视图" aria-pressed={view === "grid"} onClick={() => setView("grid")}><Grid2X2 size={15} /></button><button type="button" aria-label="列表视图" aria-pressed={view === "list"} onClick={() => setView("list")}><List size={15} /></button></div>
        </div>
        {catalogError ? <div className={styles.catalogError} role="alert"><p>{catalogError}</p><button type="button" onClick={() => setCatalogRetry(value => value + 1)}>重新读取资源目录</button></div> : categoryId === "materials" ? <div className={`${styles.materialArea} ${view === "list" ? styles.list : ""}`}>
          <div className={styles.results} aria-label="素材结果">{visibleAssets.length ? visibleAssets.map(asset => <button key={asset.id} type="button" className={`${styles.card} ${selected?.id === asset.id ? styles.selected : ""}`} aria-pressed={selected?.id === asset.id} onClick={() => onSelect(asset)}><span className={styles.thumb}>{asset.media_type === "image" ? <img src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} alt="" /> : asset.media_type === "video" ? <Film size={22} /> : <AudioLines size={22} />}</span><span className={styles.cardCopy}><strong>{fileLabel(asset.file_name)}</strong><small>{mediaLabel(asset.media_type)} · {reviewLabel(asset)}</small><small>{asset.user_tags.slice(0, 2).join(" · ") || roleLabel(asset)}</small></span></button>) : <p className={styles.empty}>没有匹配的已接入素材。请调整搜索或先导入素材。</p>}</div>
          {selected ? <aside className={styles.detail} aria-label="资源详情"><div className={styles.detailPreview}>{selected.media_type === "image" ? <img src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} alt={`预览：${fileLabel(selected.file_name)}`} /> : selected.media_type === "video" ? <video controls width={640} height={360} preload="metadata" src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} aria-label={`预览：${fileLabel(selected.file_name)}`} /> : <audio controls preload="metadata" src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} aria-label={`预览：${fileLabel(selected.file_name)}`} />}</div><h3>{fileLabel(selected.file_name)}</h3><p>{selected.user_description || "暂无用户说明"}</p><dl><div><dt>状态</dt><dd>{selected.analysis_status === "ready" ? "已分析" : selected.analysis_status === "pending" ? "等待分析" : "暂不可用"}</dd></div><div><dt>来源</dt><dd>{selected.source_url || selected.path || "本地素材"}</dd></div><div><dt>版本</dt><dd>{selected.updated_at}</dd></div></dl>{selected.analysis_status === "ready" && selected.use_state === "available" ? <button type="button" className={styles.adopt} onClick={() => onReferenceMaterial(selected)}><Check size={15} />引用到对话</button> : <p className={styles.unavailable}>当前资源不能直接采用；请先完成分析或调整使用状态。</p>}</aside> : <div className={styles.detail}><ImageIcon size={28} /><p>选择一个素材查看预览、来源和采用状态。</p></div>}
        </div> : catalogLoading ? <p>正在读取资源目录…</p> : catalogResources.length ? <CatalogResourceResults key={categoryId} resources={catalogResources.filter(resource => (!query.trim() || resource.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && (filter === "all" || resource.preview.kind === filter))} renderResourceAction={renderResourceAction} /> : <UnavailableCategory category={category} />}
      </div>
    </div>
  </section>;
}

function CatalogResourceResults({ resources, renderResourceAction }: { resources: CatalogResource[]; renderResourceAction?: (resource: CatalogResource) => ReactNode }) {
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [adoptingId, setAdoptingId] = useState<string | null>(null);
  return <div className={styles.catalogResults} aria-label="目录资源">
    {!resources.length ? <p>没有匹配的资源，请调整搜索或类型筛选。</p> : null}
    {resources.map(resource => {
      const key = `${resource.id}:${resource.version}`;
      const open = previewId === key;
      const preview = resource.preview.thumbnail_ref;
      const canAddAudio = resource.status === "connected" && resource.preview.kind === "audio" && resource.runtime_requirements?.engine === "EditAudioTimeline";
      return <article className={styles.catalogCard} key={key}>
        <div><strong>{resource.name}</strong><span>{resourceStateLabel(resource.status)} · v{resource.version}</span></div>
        <div className={styles.catalogActions}>
          <button type="button" aria-expanded={open} onClick={() => setPreviewId(open ? null : key)}>{open ? "收起预览" : "查看预览"}</button>
          {canAddAudio ? renderResourceAction
            ? <button type="button" aria-expanded={adoptingId === key} onClick={() => setAdoptingId(adoptingId === key ? null : key)}>添加到作品</button>
            : <span>打开一个作品后可添加到音轨</span>
            : <span>{resource.status === "catalog_only" ? "可浏览，采用能力尚未接通" : resource.status === "planned" ? "计划中，暂不可采用" : resource.resource_type === "skill" ? "由助手读取并按当前任务使用" : "在作品对应的编辑工具中使用"}</span>}
        </div>
        {open ? <div className={styles.catalogPreview} role="region" aria-label={`${resource.name}资源预览`}>
          {preview && resource.preview.kind === "audio" ? <audio controls preload="none" src={preview} aria-label={`预览：${resource.name}`} /> : null}
          {preview && resource.preview.kind === "video" ? <video controls width={640} height={360} preload="none" poster={typeof resource.runtime_requirements?.poster_ref === "string" ? resource.runtime_requirements.poster_ref : undefined} src={preview} aria-label={`预览：${resource.name}`} /> : null}
          {resource.preview.duration_seconds != null ? <span>{resource.preview.duration_seconds} 秒</span> : null}
          <details><summary>来源、版本与回滚</summary>
            {resource.provenance.map((source, index) => <p key={index}>{source.repository} · {source.path} · 修订 {source.revision} · {source.license}</p>)}
            <p>固定版本 v{resource.version}。采用后可以从作品的版本历史撤销；浏览和试听不会修改作品。</p>
          </details>
        </div> : null}
        {adoptingId === key && canAddAudio ? renderResourceAction?.(resource) : null}
      </article>;
    })}
  </div>;
}

function UnavailableCategory({ category }: { category: Category }) {
  const Icon = category.icon;
  return <div className={styles.unavailableCategory}><Icon size={28} /><h3>{category.label}资源{stateLabel(category.state)}</h3><p>{category.nextStep}</p></div>;
}
