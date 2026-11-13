"use client";

import { useEffect, useMemo, useState } from "react";
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
  { id: "music", label: "音乐", description: "背景音乐资源", state: "catalog_only", nextStep: "音乐目录尚未接入；当前只能使用任务已有音轨", icon: Music2 },
  { id: "sfx", label: "音效", description: "短音效与声音过渡", state: "catalog_only", nextStep: "音效目录尚未接入，当前不能采用", icon: AudioLines },
];

type CatalogResource = { id: string; category_id: string; name: string; version: number; status: RemoteStatus; resource_type: string; preview: { kind: string; duration_seconds?: number | null }; provenance: Array<{ repository: string; revision: string; path: string; license: string }>; runtime_requirements?: Record<string, unknown>; entrypoints: string[] };
const apiCategoryId: Record<EditorResourceCategory, string> = { materials: "materials", skills: "skills", stickers: "stickers", filters: "filters", effects: "effects", transitions: "transitions", templates: "templates", subtitles: "subtitles_text", text: "subtitles_text", voice: "voice", music: "music", sfx: "sfx" };

function stateLabel(state: CategoryState) {
  return state === "available" ? "可用" : state === "catalog_only" ? "目录中" : "计划中";
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
  onAdopt,
  onOpenCatalog,
}: {
  assets: MaterialAsset[];
  selectedId: string | null;
  initialCategory?: EditorResourceCategory;
  onSelect: (asset: MaterialAsset) => void;
  onAdopt: (asset: MaterialAsset) => void;
  onOpenCatalog?: (category: EditorResourceCategory) => void;
}) {
  const [categoryId, setCategoryId] = useState<EditorResourceCategory>(initialCategory);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [categories, setCategories] = useState(editorResourceCategories);
  const [catalogResources, setCatalogResources] = useState<CatalogResource[]>([]);
  useEffect(() => {
    let active = true;
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
      .catch(() => { if (active) setCatalogResources([]); });
    return () => { active = false; };
  }, [categoryId]);
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
        {editorResourceCategories.map(item => { const Icon = item.icon; return <button key={item.id} type="button" className={item.id === categoryId ? styles.activeCategory : ""} aria-pressed={item.id === categoryId} onClick={() => { setCategoryId(item.id); onOpenCatalog?.(item.id); }}><Icon size={15} /><span>{item.label}</span><small>{stateLabel(item.state)}</small></button>; })}
      </nav>
      <div className={styles.content}>
        <div className={styles.toolbar}>
          <label className={styles.search}><span>搜索{category.label}</span><input aria-label={`搜索${category.label}`} type="search" placeholder={`搜索${category.label}`} value={query} onChange={event => setQuery(event.target.value)} /></label>
          <select aria-label="资源类型筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部类型</option><option value="video">视频</option><option value="image">图片</option><option value="audio">音频</option></select>
          <div className={styles.viewToggle} role="group" aria-label="资源显示方式"><button type="button" aria-label="网格视图" aria-pressed={view === "grid"} onClick={() => setView("grid")}><Grid2X2 size={15} /></button><button type="button" aria-label="列表视图" aria-pressed={view === "list"} onClick={() => setView("list")}><List size={15} /></button></div>
        </div>
        {categoryId === "materials" ? <div className={`${styles.materialArea} ${view === "list" ? styles.list : ""}`}>
          <div className={styles.results} aria-label="素材结果">{visibleAssets.length ? visibleAssets.map(asset => <button key={asset.id} type="button" className={`${styles.card} ${selected?.id === asset.id ? styles.selected : ""}`} aria-pressed={selected?.id === asset.id} onClick={() => onSelect(asset)}><span className={styles.thumb}>{asset.media_type === "image" ? <img src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} alt="" /> : asset.media_type === "video" ? <Film size={22} /> : <AudioLines size={22} />}</span><span className={styles.cardCopy}><strong>{fileLabel(asset.file_name)}</strong><small>{mediaLabel(asset.media_type)} · {reviewLabel(asset)}</small><small>{asset.user_tags.slice(0, 2).join(" · ") || roleLabel(asset)}</small></span></button>) : <p className={styles.empty}>没有匹配的已接入素材。请调整搜索或先导入素材。</p>}</div>
          {selected ? <aside className={styles.detail} aria-label="资源详情"><div className={styles.detailPreview}>{selected.media_type === "image" ? <img src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} alt={`预览：${fileLabel(selected.file_name)}`} /> : selected.media_type === "video" ? <video controls preload="metadata" src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} aria-label={`预览：${fileLabel(selected.file_name)}`} /> : <audio controls preload="metadata" src={`/api/production/materials/${encodeURIComponent(selected.id)}/media`} aria-label={`预览：${fileLabel(selected.file_name)}`} />}</div><h3>{fileLabel(selected.file_name)}</h3><p>{selected.user_description || "暂无用户说明"}</p><dl><div><dt>状态</dt><dd>{selected.analysis_status === "ready" ? "已分析" : selected.analysis_status === "pending" ? "等待分析" : "暂不可用"}</dd></div><div><dt>来源</dt><dd>{selected.source_url || selected.path || "本地素材"}</dd></div><div><dt>版本</dt><dd>{selected.updated_at}</dd></div></dl>{selected.analysis_status === "ready" && selected.use_state === "available" ? <button type="button" className={styles.adopt} onClick={() => onAdopt(selected)}><Check size={15} />采用到当前任务</button> : <p className={styles.unavailable}>当前资源不能直接采用；请先完成分析或调整使用状态。</p>}</aside> : <div className={styles.detail}><ImageIcon size={28} /><p>选择一个素材查看预览、来源和采用状态。</p></div>}
        </div> : category.state === "available" && catalogResources.length ? <CatalogResourceResults resources={catalogResources} onOpenCatalog={() => onOpenCatalog?.(category.id)} /> : <UnavailableCategory category={category} onOpenCatalog={() => onOpenCatalog?.(category.id)} />}
      </div>
    </div>
  </section>;
}

function CatalogResourceResults({ resources, onOpenCatalog }: { resources: CatalogResource[]; onOpenCatalog: () => void }) {
  return <div className={styles.catalogResults} aria-label="目录资源"><div className={styles.catalogHeading}><div><h3>已接入目录</h3><p>资源来自当前编辑资源 manifest，采用时仍需经过编辑提案和时间线版本。</p></div><button type="button" onClick={onOpenCatalog}>打开编辑入口</button></div>{resources.map(resource => <article className={styles.catalogCard} key={`${resource.id}:${resource.version}`}><div><strong>{resource.name}</strong><span>{resource.resource_type} · v{resource.version}{typeof resource.runtime_requirements?.manifest_version === "string" ? ` · ${resource.runtime_requirements.manifest_version}` : ""}</span></div><p>{resource.provenance[0] ? `来源：${resource.provenance[0].repository} · ${resource.provenance[0].path} · 修订 ${resource.provenance[0].revision}` : "来源待补充"}</p><small>{resource.provenance[0]?.license ?? "许可证待核对"} · {resource.entrypoints.join("、") || "暂无采用入口"}</small></article>)}</div>;
}

function UnavailableCategory({ category, onOpenCatalog }: { category: Category; onOpenCatalog: () => void }) {
  const Icon = category.icon;
  return <div className={styles.unavailableCategory}><Icon size={28} /><h3>{category.label}资源{stateLabel(category.state)}</h3><p>{category.nextStep}</p>{category.state === "catalog_only" ? <button type="button" onClick={onOpenCatalog}>查看接入说明</button> : <span>能力状态会在接入真实目录后更新。</span>}</div>;
}
