"use client";

import {
  AlertCircle,
  Check,
  ChevronRight,
  Clock3,
  Film,
  FolderPlus,
  FolderSearch,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  analyzeProductionMaterial,
  createMaterialCollection,
  getMaterialLibraryInsights,
  getProductionMaterials,
  reviewProductionMaterial,
  scanProductionMaterials,
  updateProductionMaterialSegment,
} from "@/lib/api";
import type {
  MaterialAsset,
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
  MaterialSegment,
} from "@/lib/types";

type Filter = "all" | "raw" | "approved" | "rejected";
type Busy =
  | "scan"
  | "search"
  | "load"
  | "analyze"
  | "review"
  | "segment"
  | "collection"
  | null;

function duration(value: number | null) {
  if (value === null) return "--:--";
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function roleLabel(asset: MaterialAsset) {
  const role =
    asset.usage_role === "unassigned"
      ? asset.suggested_usage_role
      : asset.usage_role;
  if (role === "a_roll") return "人物主镜头";
  if (role === "b_roll") return "B-roll";
  if (asset.usage_role === "exclude") return "不使用";
  return "待判断";
}

function reviewLabel(asset: MaterialAsset) {
  if (asset.review_status === "approved") return "已审核";
  if (asset.review_status === "rejected") return "已排除";
  if (asset.analysis_status === "ready") return "等待审核";
  if (asset.analysis_status === "failed" || asset.analysis_status === "blocked")
    return "分析受阻";
  return "等待分析";
}

export function MaterialLibraryWorkspace({
  initialLibrary,
  initialInsights,
  initialCollections,
}: {
  initialLibrary: MaterialLibrary;
  initialInsights: MaterialLibraryInsights;
  initialCollections: MaterialCollections;
}) {
  const [assets, setAssets] = useState(initialLibrary.assets);
  const [totalCount, setTotalCount] = useState(initialLibrary.total_count);
  const [hasMore, setHasMore] = useState(initialLibrary.has_more);
  const [insights, setInsights] = useState(initialInsights);
  const [collections, setCollections] = useState(
    initialCollections.collections,
  );
  const [selectedId, setSelectedId] = useState(
    initialLibrary.assets[0]?.id ?? "",
  );
  const [selectedSegmentId, setSelectedSegmentId] = useState(
    initialLibrary.assets[0]?.segments[0]?.id ?? "",
  );
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState("");
  const [tagDraft, setTagDraft] = useState(
    initialLibrary.assets[0]?.segments[0]?.user_tags.join(", ") ?? "",
  );
  const [collectionDraft, setCollectionDraft] = useState(
    initialLibrary.assets[0]?.segments[0]?.collection_ids[0] ?? "",
  );
  const [newCollectionName, setNewCollectionName] = useState("");

  const selected =
    assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null;
  const selectedSegment =
    selected?.segments.find((segment) => segment.id === selectedSegmentId) ??
    selected?.segments[0] ??
    null;
  const visible = assets;

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setBusy("search");
      getProductionMaterials({
        limit: initialLibrary.limit,
        offset: 0,
        query: query.trim(),
        reviewStatus: filter,
      })
        .then((result) => {
          if (!active) return;
          setAssets(result.assets);
          setTotalCount(result.total_count);
          setHasMore(result.has_more);
          if (
            result.assets[0] &&
            !result.assets.some((asset) => asset.id === selectedId)
          ) {
            selectAsset(result.assets[0]);
          }
        })
        .catch((cause) => {
          if (active)
            setError(cause instanceof Error ? cause.message : "搜索素材失败");
        })
        .finally(() => {
          if (active) setBusy(null);
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
    // selectedId and selectAsset are intentionally excluded: changing selection must not re-run search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, initialLibrary.limit, query]);

  function replaceAsset(updated: MaterialAsset) {
    setAssets((current) =>
      current.map((asset) => (asset.id === updated.id ? updated : asset)),
    );
  }

  function selectAsset(asset: MaterialAsset) {
    const first = asset.segments[0];
    setSelectedId(asset.id);
    setSelectedSegmentId(first?.id ?? "");
    setTagDraft(first?.user_tags.join(", ") ?? "");
    setCollectionDraft(first?.collection_ids[0] ?? "");
  }

  function selectSegment(segment: MaterialSegment) {
    setSelectedSegmentId(segment.id);
    setTagDraft(segment.user_tags.join(", "));
    setCollectionDraft(segment.collection_ids[0] ?? "");
  }

  async function refreshInsights() {
    try {
      setInsights(await getMaterialLibraryInsights());
    } catch {
      /* Keep last usable snapshot. */
    }
  }

  async function scan() {
    setBusy("scan");
    setError("");
    try {
      const result = await scanProductionMaterials();
      setAssets(result.assets);
      setTotalCount(result.total_count);
      setHasMore(result.has_more);
      setQuery("");
      setFilter("all");
      if (
        result.assets[0] &&
        !result.assets.some((asset) => asset.id === selectedId)
      ) {
        selectAsset(result.assets[0]);
      }
      await refreshInsights();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "扫描素材失败");
    } finally {
      setBusy(null);
    }
  }

  async function loadMore() {
    setBusy("load");
    setError("");
    try {
      const result = await getProductionMaterials({
        limit: initialLibrary.limit,
        offset: assets.length,
        query: query.trim(),
        reviewStatus: filter,
      });
      setAssets((current) => {
        const known = new Set(current.map((asset) => asset.id));
        return [
          ...current,
          ...result.assets.filter((asset) => !known.has(asset.id)),
        ];
      });
      setTotalCount(result.total_count);
      setHasMore(result.has_more);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载更多素材失败");
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selected) return;
    setBusy("analyze");
    setError("");
    try {
      const updated = await analyzeProductionMaterial(selected.id);
      replaceAsset(updated);
      if (updated.segments[0]) selectSegment(updated.segments[0]);
      await refreshInsights();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "素材分析失败");
    } finally {
      setBusy(null);
    }
  }

  async function review(
    status: "approved" | "rejected",
    role: "b_roll" | "exclude",
  ) {
    if (!selected) return;
    setBusy("review");
    setError("");
    try {
      replaceAsset(
        await reviewProductionMaterial(selected.id, {
          review_status: status,
          usage_role: role,
          rights_confirmed: status === "approved",
          brand_safe: status === "approved",
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "素材审核失败");
    } finally {
      setBusy(null);
    }
  }

  async function saveSegmentOrganization() {
    if (!selected || !selectedSegment) return;
    setBusy("segment");
    setError("");
    try {
      const tags = tagDraft
        .split(/[,\n]/)
        .map((tag) => tag.trim())
        .filter(Boolean);
      const updated = await updateProductionMaterialSegment(
        selected.id,
        selectedSegment.id,
        {
          description: selectedSegment.description,
          transcript: selectedSegment.transcript,
          review_status: selectedSegment.review_status,
          usage_role: selectedSegment.usage_role,
          user_tags: Array.from(new Set(tags)),
          collection_ids: collectionDraft ? [collectionDraft] : [],
        },
      );
      replaceAsset(updated);
      await refreshInsights();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存片段分类失败");
    } finally {
      setBusy(null);
    }
  }

  async function addCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    setBusy("collection");
    setError("");
    try {
      const created = await createMaterialCollection({ name });
      setCollections((current) => [...current, created]);
      setCollectionDraft(created.id);
      setNewCollectionName("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "新建标签夹失败");
    } finally {
      setBusy(null);
    }
  }

  const inspector = selected ? (
    <div className="library-inspector">
      <div className="library-preview">
        <video
          controls
          key={selected.id}
          preload="metadata"
          src={`/api/production/materials/${selected.id}/media`}
        />
        <span>
          <Play size={14} /> {duration(selected.duration_seconds)}
        </span>
      </div>
      <div className="library-inspector__title">
        <strong title={selected.file_name}>{selected.file_name}</strong>
        <span>
          {roleLabel(selected)} · {reviewLabel(selected)}
        </span>
      </div>
      <dl className="library-inspector__meta">
        <div>
          <dt>AI 建议</dt>
          <dd>
            {roleLabel(selected)}{" "}
            {selected.usage_role_confidence
              ? `${Math.round(selected.usage_role_confidence * 100)}%`
              : ""}
          </dd>
        </div>
        <div>
          <dt>版权确认</dt>
          <dd>{selected.rights_confirmed ? "已确认" : "未确认"}</dd>
        </div>
        <div>
          <dt>品牌安全</dt>
          <dd>{selected.brand_safe ? "已确认" : "未确认"}</dd>
        </div>
        <div>
          <dt>识别片段</dt>
          <dd>{selected.segments.length}</dd>
        </div>
      </dl>
      {selected.usage_role_reason ? (
        <div className="library-ai-reason">
          <Sparkles size={14} />
          <p>{selected.usage_role_reason}</p>
        </div>
      ) : null}
      <div className="library-inspector__actions">
        <button
          disabled={busy !== null}
          onClick={() => void analyze()}
          type="button"
        >
          <RefreshCw size={14} />
          {busy === "analyze" ? "分析中…" : "重新分析"}
        </button>
        <button
          className="is-primary"
          disabled={busy !== null}
          onClick={() => void review("approved", "b_roll")}
          type="button"
        >
          <Check size={14} />
          批准为 B-roll
        </button>
        <button
          className="is-danger"
          disabled={busy !== null}
          onClick={() => void review("rejected", "exclude")}
          type="button"
        >
          <X size={14} />
          排除素材
        </button>
      </div>
      {selected.segments.length ? (
        <div className="library-segments">
          <h3>识别片段</h3>
          {selected.segments.map((segment) => (
            <button
              className={
                selectedSegment?.id === segment.id ? "is-selected" : ""
              }
              key={segment.id}
              onClick={() => selectSegment(segment)}
              type="button"
            >
              <span>
                {duration(segment.start_seconds)}–
                {duration(segment.end_seconds)}
              </span>
              <p>
                {segment.description ||
                  segment.transcript ||
                  "等待补充片段描述"}
              </p>
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
      ) : null}
      {selectedSegment ? (
        <div className="library-segment-organizer">
          <div className="library-segment-heading">
            <strong>片段分类</strong>
            <span>
              已用 {selectedSegment.usage_count} 次
              {selectedSegment.last_used_at
                ? ` · 最近 ${new Date(selectedSegment.last_used_at).toLocaleDateString("zh-CN")}`
                : ""}
            </span>
          </div>
          <div className="library-tag-list" aria-label="系统标签">
            {selectedSegment.system_tags.map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
            {!selectedSegment.system_tags.length ? (
              <small>等待画面理解生成系统标签</small>
            ) : null}
          </div>
          <label>
            <span>我的标签</span>
            <input
              onChange={(event) => setTagDraft(event.target.value)}
              placeholder="人物, 产品演示, 夜景"
              value={tagDraft}
            />
          </label>
          <label>
            <span>标签夹</span>
            <select
              onChange={(event) => setCollectionDraft(event.target.value)}
              value={collectionDraft}
            >
              <option value="">未归类</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>
                  {collection.name}
                </option>
              ))}
            </select>
          </label>
          <div className="library-collection-create">
            <input
              aria-label="新标签夹名称"
              onChange={(event) => setNewCollectionName(event.target.value)}
              placeholder="新建标签夹"
              value={newCollectionName}
            />
            <button
              disabled={busy !== null || !newCollectionName.trim()}
              onClick={() => void addCollection()}
              type="button"
            >
              <FolderPlus size={14} />
              新建
            </button>
          </div>
          <button
            className="library-segment-save"
            disabled={busy !== null}
            onClick={() => void saveSegmentOrganization()}
            type="button"
          >
            {busy === "segment" ? "保存中…" : "保存片段分类"}
          </button>
        </div>
      ) : null}
    </div>
  ) : null;

  return (
    <AppShell inspector={inspector} inspectorTitle="素材详情">
      <div className="modern-library">
        <header className="modern-library__header">
          <div>
            <span>素材资产</span>
            <h1>素材库</h1>
            <p>理解长视频中的可用片段，管理覆盖、缺口和镜头复用。</p>
          </div>
          <button
            className="modern-primary-button"
            disabled={busy !== null}
            onClick={() => void scan()}
            type="button"
          >
            {busy === "scan" ? (
              <LoaderCircle className="is-spinning" size={16} />
            ) : (
              <FolderSearch size={16} />
            )}
            {busy === "scan" ? "正在扫描…" : "扫描素材目录"}
          </button>
        </header>

        <section
          className="library-coverage"
          aria-labelledby="library-coverage-title"
        >
          <div className="library-coverage__summary">
            <span id="library-coverage-title">素材覆盖</span>
            <strong>{insights.segment_count} 个片段</strong>
            <small>{insights.asset_count} 个文件</small>
          </div>
          <div className="library-coverage__tags">
            {insights.tags.slice(0, 6).map((entry) => (
              <span key={entry.tag}>
                <strong>{entry.tag}</strong>
                <small>
                  {entry.segment_count} 段 · 已用 {entry.usage_count} 次
                </small>
              </span>
            ))}
          </div>
          <div className="library-coverage__gaps">
            <span>建议补充</span>
            <p>
              {insights.recommended_gaps.length
                ? insights.recommended_gaps.join("、")
                : "当前未发现明显缺口"}
            </p>
          </div>
        </section>

        <div className="library-toolbar">
          <label className="library-search">
            <Search size={15} />
            <input
              aria-label="搜索素材"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件、画面、标签或标签夹…"
              value={query}
            />
          </label>
          <div
            className="library-filters"
            role="group"
            aria-label="审核状态筛选"
          >
            {(["all", "raw", "approved", "rejected"] as Filter[]).map(
              (value) => (
                <button
                  className={filter === value ? "is-active" : ""}
                  key={value}
                  onClick={() => setFilter(value)}
                  type="button"
                >
                  {
                    {
                      all: "全部",
                      raw: "待审核",
                      approved: "已批准",
                      rejected: "已排除",
                    }[value]
                  }
                </button>
              ),
            )}
          </div>
          <span className="library-count">
            已载入 {assets.length} / {totalCount}
          </span>
        </div>

        {error ? (
          <div className="library-error" role="alert">
            <AlertCircle size={15} />
            {error}
          </div>
        ) : null}

        {visible.length ? (
          <div className="library-grid">
            {visible.map((asset) => (
              <button
                className={`library-card${selected?.id === asset.id ? " is-selected" : ""}`}
                key={asset.id}
                onClick={() => selectAsset(asset)}
                type="button"
              >
                <span className="library-card__poster">
                  <Film size={22} />
                  <small>{duration(asset.duration_seconds)}</small>
                  <i
                    className={`library-card__state state-${asset.review_status === "approved" ? "approved" : asset.review_status === "rejected" ? "rejected" : asset.analysis_status}`}
                  >
                    {asset.review_status === "approved" ? (
                      <ShieldCheck size={13} />
                    ) : asset.analysis_status === "ready" ? (
                      <Sparkles size={13} />
                    ) : (
                      <Clock3 size={13} />
                    )}
                  </i>
                </span>
                <span className="library-card__copy">
                  <strong title={asset.file_name}>{asset.file_name}</strong>
                  <small>
                    {roleLabel(asset)} · {reviewLabel(asset)}
                  </small>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="library-empty">
            <Film size={23} />
            <div>
              <strong>
                {assets.length ? "没有匹配的素材" : "素材库还是空的"}
              </strong>
              <p>
                {assets.length
                  ? "调整搜索条件或审核状态。"
                  : "选择素材目录后，系统会识别长视频中的可用片段。"}
              </p>
            </div>
            {!assets.length ? (
              <button onClick={() => void scan()} type="button">
                扫描素材目录
              </button>
            ) : null}
          </div>
        )}
        {hasMore ? (
          <div className="library-load-more">
            <button
              disabled={busy !== null}
              onClick={() => void loadMore()}
              type="button"
            >
              {busy === "load" ? (
                <LoaderCircle className="is-spinning" size={15} />
              ) : null}
              {busy === "load"
                ? "正在载入…"
                : `载入更多（剩余 ${Math.max(0, totalCount - assets.length)}）`}
            </button>
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
