"use client";

import { useEffect, useState, type ReactNode } from "react";
import { AudioLines, Film, Grid2X2, Music2, SlidersHorizontal, Sparkles, Sticker, Subtitles, Wand2 } from "lucide-react";
import styles from "./EditorResourceLibraryPanel.module.css";
import { readPreflightDraftScope } from "@/lib/preflight-draft";
import { ResourceVisualPreview } from "./resource-library/ResourceVisualAdoption";

export type EditorResourceCategory = "materials" | "skills" | "stickers" | "filters" | "effects" | "transitions" | "templates" | "subtitles" | "text" | "voice" | "music" | "sfx";

type CategoryState = "available" | "catalog_only" | "planned" | "failed";
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

function stateLabel(state: RemoteStatus) {
  switch (state) {
    case "available":
    case "connected": return "可用";
    case "catalog_only": return "目录中";
    case "planned": return "计划中";
    case "failed": return "失败";
  }
}

type ResourceFilters = { query: string; filter: string };
const defaultFilters: ResourceFilters = { query: "", filter: "all" };
const mediaFilters = ["all", "video", "image", "audio"];

function readFilters(key: string): ResourceFilters {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? "null") as Partial<ResourceFilters> | null;
    return {
      query: typeof saved?.query === "string" ? saved.query : "",
      filter: typeof saved?.filter === "string" && mediaFilters.includes(saved.filter) ? saved.filter : "all",
    };
  } catch {
    return defaultFilters;
  }
}

function resourceTags(resource: CatalogResource) {
  const tags = resource.runtime_requirements?.tags;
  return Array.isArray(tags) ? tags.filter((tag): tag is string => typeof tag === "string") : [];
}

export function EditorResourceLibraryPanel({
  materials,
  initialCategory = "materials",
  renderResourceAction,
}: {
  materials: ReactNode;
  initialCategory?: EditorResourceCategory;
  renderResourceAction?: (resource: CatalogResource) => ReactNode;
}) {
  const [categoryId, setCategoryId] = useState<EditorResourceCategory>(initialCategory);
  const [scope, setScope] = useState<string | null | undefined>(undefined);
  const [filtersByKey, setFiltersByKey] = useState<Record<string, ResourceFilters>>({});
  const filterKey = scope ? `editor-resource-panel:v2:${JSON.stringify([scope, categoryId])}` : categoryId;
  const { query, filter } = filtersByKey[filterKey] ?? defaultFilters;
  useEffect(() => {
    let active = true;
    void readPreflightDraftScope().then(
      ({ memberId, workspaceId }) => { if (active) setScope(JSON.stringify([workspaceId, memberId])); },
      () => { if (active) setScope(null); },
    );
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!scope || categoryId === "materials" || filtersByKey[filterKey]) return;
    const saved = readFilters(filterKey);
    setFiltersByKey(current => current[filterKey] ? current : { ...current, [filterKey]: saved });
  }, [categoryId, filterKey, filtersByKey, scope]);

  function changeFilters(next: ResourceFilters) {
    setFiltersByKey(current => ({ ...current, [filterKey]: next }));
    if (!scope) return;
    try {
      sessionStorage.setItem(filterKey, JSON.stringify(next));
    } catch {
      // The in-memory filters still apply if storage is unavailable.
    }
  }
  const [categories, setCategories] = useState(editorResourceCategories);
  const [catalogResources, setCatalogResources] = useState<CatalogResource[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogRetry, setCatalogRetry] = useState(0);
  const [catalogLoading, setCatalogLoading] = useState(true);
  useEffect(() => { setCategoryId(initialCategory); }, [initialCategory]);
  useEffect(() => {
    if (categoryId === "materials") return;
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
  return <section className={styles.panel} aria-label="编辑器资源面板">
    <header className={styles.header}>
      <div><span className={styles.eyebrow}>资源面板</span><h2>{category.label}</h2><p>{category.description}</p></div>
      <span className={`${styles.state} ${styles[`state_${category.state}`]}`}>{stateLabel(category.state)}</span>
    </header>
    <div className={styles.body}>
      <nav className={styles.rail} aria-label="资源类别">
        <span className={styles.railLabel}>功能资源</span>
        {categories.map(item => { const Icon = item.icon; return <button key={item.id} type="button" className={item.id === categoryId ? styles.activeCategory : ""} aria-pressed={item.id === categoryId} onClick={() => setCategoryId(item.id)}><Icon size={15} /><span>{item.label}</span><small>{stateLabel(item.state)}</small></button>; })}
      </nav>
      <div className={styles.content}>
        {categoryId === "materials" ? materials : <>
        <div className={styles.toolbar}>
          <label className={styles.search}><span>搜索{category.label}</span><input aria-label={`搜索${category.label}`} type="search" placeholder={`搜索${category.label}`} value={query} disabled={scope === undefined} onChange={event => changeFilters({ query: event.target.value, filter })} /></label>
          <select aria-label="资源类型筛选" value={filter} disabled={scope === undefined} onChange={event => changeFilters({ query, filter: event.target.value })}><option value="all">全部类型</option><option value="video">视频</option><option value="image">图片</option><option value="audio">音频</option></select>
        </div>
        {catalogError ? <div className={styles.catalogError} role="alert"><p>{catalogError}</p><button type="button" onClick={() => setCatalogRetry(value => value + 1)}>重新读取资源目录</button></div> : catalogLoading ? <p>正在读取资源目录…</p> : catalogResources.length ? <CatalogResourceResults key={categoryId} resources={catalogResources.filter(resource => (!query.trim() || resource.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) && (filter === "all" || resource.preview.kind === filter))} renderResourceAction={renderResourceAction} /> : <UnavailableCategory category={category} />}
        </>}
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
      const canAddVisual = resource.status === "connected" && resource.runtime_requirements?.engine === "openfilm-html-v1"
        && typeof resource.runtime_requirements.template_id === "string" && typeof resource.runtime_requirements.template_version === "number";
      return <article className={styles.catalogCard} key={key}>
        {preview && resource.preview.kind === "image" ? <img className={styles.stickerThumbnail} src={preview} alt={resource.name} width={64} height={64} /> : null}
        <div><strong>{resource.name}</strong><span>{stateLabel(resource.status)} · v{resource.version}</span></div>{resourceTags(resource).length ? <div className={styles.tagRow} aria-label="资源标签">{resourceTags(resource).map(tag => <span key={tag}>{tag}</span>)}</div> : null}
        <div className={styles.catalogActions}>
          <button type="button" aria-expanded={open} onClick={() => setPreviewId(open ? null : key)}>{open ? "收起预览" : "查看预览"}</button>
          {canAddAudio || canAddVisual ? renderResourceAction
            ? <button type="button" aria-expanded={adoptingId === key} onClick={() => setAdoptingId(adoptingId === key ? null : key)}>添加到作品</button>
            : <span>{canAddVisual ? "打开一个作品后可添加到画面" : "打开一个作品后可添加到音轨"}</span>
            : <span>{resource.status === "catalog_only" ? "可浏览，采用能力尚未接通" : resource.status === "planned" ? "计划中，暂不可采用" : resource.status === "failed" ? "资源失败，暂不可采用" : resource.resource_type === "skill" ? "由助手读取并按当前任务使用" : "在作品对应的编辑工具中使用"}</span>}
        </div>
        {open ? <div className={styles.catalogPreview} role="region" aria-label={`${resource.name}资源预览`}>
          {canAddVisual && resource.preview.kind === "html" ? <ResourceVisualPreview resource={resource} /> : null}
          {preview && resource.preview.kind === "image" ? <img className={styles.stickerPreview} src={preview} alt={`预览：${resource.name}`} width={160} height={160} /> : null}
          {preview && resource.preview.kind === "audio" ? <audio controls preload="none" src={preview} aria-label={`预览：${resource.name}`} /> : null}
          {preview && resource.preview.kind === "video" ? <video controls width={640} height={360} preload="none" poster={typeof resource.runtime_requirements?.poster_ref === "string" ? resource.runtime_requirements.poster_ref : undefined} src={preview} aria-label={`预览：${resource.name}`} /> : null}
          {resource.preview.duration_seconds != null ? <span>{resource.preview.duration_seconds} 秒</span> : null}
          <details><summary>来源、版本与回滚</summary>
            {typeof resource.runtime_requirements?.attribution === "string" ? <p>{resource.runtime_requirements.attribution}</p> : null}
            {resource.provenance.map((source, index) => <p key={index}>{source.repository} · {source.path} · 修订 {source.revision} · {source.license}</p>)}{resourceTags(resource).length ? <p>标签：{resourceTags(resource).join("、")}</p> : null}
            <p>固定版本 v{resource.version}。采用后可以从作品的版本历史撤销；浏览和试听不会修改作品。</p>
          </details>
        </div> : null}
        {adoptingId === key && (canAddAudio || canAddVisual) ? renderResourceAction?.(resource) : null}
      </article>;
    })}
  </div>;
}

function UnavailableCategory({ category }: { category: Category }) {
  const Icon = category.icon;
  return <div className={styles.unavailableCategory}><Icon size={28} /><h3>{category.label}资源{stateLabel(category.state)}</h3><p>{category.state === "failed" ? "资源目录暂不可用，请稍后重试。" : category.nextStep}</p></div>;
}
