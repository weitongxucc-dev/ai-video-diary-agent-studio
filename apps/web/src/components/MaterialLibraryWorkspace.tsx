"use client";

import {
  AlertCircle,
  Check,
  ChevronRight,
  Clock3,
  Film,
  FolderSearch,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  analyzeProductionMaterial,
  reviewProductionMaterial,
  scanProductionMaterials,
} from "@/lib/api";
import type { MaterialAsset, MaterialLibrary } from "@/lib/types";

type Filter = "all" | "raw" | "approved" | "rejected";

function duration(value: number | null) {
  if (value === null) return "--:--";
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function roleLabel(asset: MaterialAsset) {
  const role = asset.usage_role === "unassigned" ? asset.suggested_usage_role : asset.usage_role;
  if (role === "a_roll") return "人物主镜头";
  if (role === "b_roll") return "B-roll";
  if (asset.usage_role === "exclude") return "不使用";
  return "待判断";
}

function reviewLabel(asset: MaterialAsset) {
  if (asset.review_status === "approved") return "已审核";
  if (asset.review_status === "rejected") return "已排除";
  if (asset.analysis_status === "ready") return "等待审核";
  if (asset.analysis_status === "failed" || asset.analysis_status === "blocked") return "分析受阻";
  return "等待分析";
}

export function MaterialLibraryWorkspace({ initialLibrary }: { initialLibrary: MaterialLibrary }) {
  const [assets, setAssets] = useState(initialLibrary.assets);
  const [selectedId, setSelectedId] = useState(initialLibrary.assets[0]?.id ?? "");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<"scan" | "analyze" | "review" | null>(null);
  const [error, setError] = useState("");

  const selected = assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null;
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return assets.filter((asset) => {
      if (filter !== "all" && asset.review_status !== filter) return false;
      if (!normalized) return true;
      return asset.file_name.toLowerCase().includes(normalized) || asset.segments.some((segment) => `${segment.description} ${segment.transcript}`.toLowerCase().includes(normalized));
    });
  }, [assets, filter, query]);

  function replaceAsset(updated: MaterialAsset) {
    setAssets((current) => current.map((asset) => asset.id === updated.id ? updated : asset));
  }

  async function scan() {
    setBusy("scan");
    setError("");
    try {
      const result = await scanProductionMaterials();
      setAssets(result.assets);
      if (!selectedId && result.assets[0]) setSelectedId(result.assets[0].id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "扫描素材失败");
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selected) return;
    setBusy("analyze");
    setError("");
    try { replaceAsset(await analyzeProductionMaterial(selected.id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "素材分析失败"); }
    finally { setBusy(null); }
  }

  async function review(status: "approved" | "rejected", role: "b_roll" | "exclude") {
    if (!selected) return;
    setBusy("review");
    setError("");
    try {
      replaceAsset(await reviewProductionMaterial(selected.id, {
        review_status: status,
        usage_role: role,
        rights_confirmed: status === "approved",
        brand_safe: status === "approved",
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "素材审核失败");
    } finally {
      setBusy(null);
    }
  }

  const inspector = selected ? (
    <div className="library-inspector">
      <div className="library-preview">
        <video controls key={selected.id} preload="metadata" src={`/api/production/materials/${selected.id}/media`} />
        <span><Play size={14} /> {duration(selected.duration_seconds)}</span>
      </div>
      <div className="library-inspector__title"><strong title={selected.file_name}>{selected.file_name}</strong><span>{roleLabel(selected)} · {reviewLabel(selected)}</span></div>
      <dl className="library-inspector__meta">
        <div><dt>AI 建议</dt><dd>{roleLabel(selected)} {selected.usage_role_confidence ? `${Math.round(selected.usage_role_confidence * 100)}%` : ""}</dd></div>
        <div><dt>版权确认</dt><dd>{selected.rights_confirmed ? "已确认" : "未确认"}</dd></div>
        <div><dt>品牌安全</dt><dd>{selected.brand_safe ? "已确认" : "未确认"}</dd></div>
        <div><dt>识别片段</dt><dd>{selected.segments.length}</dd></div>
      </dl>
      {selected.usage_role_reason ? <div className="library-ai-reason"><Sparkles size={14} /><p>{selected.usage_role_reason}</p></div> : null}
      <div className="library-inspector__actions">
        <button disabled={busy !== null} onClick={() => void analyze()} type="button"><RefreshCw size={14} />{busy === "analyze" ? "分析中…" : "重新分析"}</button>
        <button className="is-primary" disabled={busy !== null} onClick={() => void review("approved", "b_roll")} type="button"><Check size={14} />批准为 B-roll</button>
        <button className="is-danger" disabled={busy !== null} onClick={() => void review("rejected", "exclude")} type="button"><X size={14} />排除素材</button>
      </div>
      {selected.segments.length ? <div className="library-segments"><h3>识别片段</h3>{selected.segments.map((segment) => <button key={segment.id} type="button"><span>{duration(segment.start_seconds)}–{duration(segment.end_seconds)}</span><p>{segment.description || segment.transcript || "等待补充片段描述"}</p><ChevronRight size={14} /></button>)}</div> : null}
    </div>
  ) : null;

  return (
    <AppShell inspector={inspector} inspectorTitle="素材详情">
      <div className="modern-library">
        <header className="modern-library__header">
          <div><span>素材资产</span><h1>素材库</h1><p>识别长视频中的可用片段，为自动混剪准备经过审核的 B-roll。</p></div>
          <button className="modern-primary-button" disabled={busy !== null} onClick={() => void scan()} type="button">
            {busy === "scan" ? <LoaderCircle className="is-spinning" size={16} /> : <FolderSearch size={16} />}
            {busy === "scan" ? "正在扫描…" : "扫描素材目录"}
          </button>
        </header>

        <div className="library-toolbar">
          <label className="library-search"><Search size={15} /><input aria-label="搜索素材" onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名、画面描述或转写…" value={query} /></label>
          <div className="library-filters" role="group" aria-label="审核状态筛选">
            {(["all", "raw", "approved", "rejected"] as Filter[]).map((value) => <button className={filter === value ? "is-active" : ""} key={value} onClick={() => setFilter(value)} type="button">{{ all: "全部", raw: "待审核", approved: "已批准", rejected: "已排除" }[value]}</button>)}
          </div>
          <span className="library-count">{visible.length} / {assets.length}</span>
        </div>

        {error ? <div className="library-error" role="alert"><AlertCircle size={15} />{error}</div> : null}

        {visible.length ? (
          <div className="library-grid">
            {visible.map((asset) => (
              <button className={`library-card${selected?.id === asset.id ? " is-selected" : ""}`} key={asset.id} onClick={() => setSelectedId(asset.id)} type="button">
                <span className="library-card__poster">
                  <Film size={22} />
                  <small>{duration(asset.duration_seconds)}</small>
                  <i className={`library-card__state state-${asset.review_status === "approved" ? "approved" : asset.review_status === "rejected" ? "rejected" : asset.analysis_status}`}>
                    {asset.review_status === "approved" ? <ShieldCheck size={13} /> : asset.analysis_status === "ready" ? <Sparkles size={13} /> : <Clock3 size={13} />}
                  </i>
                </span>
                <span className="library-card__copy"><strong title={asset.file_name}>{asset.file_name}</strong><small>{roleLabel(asset)} · {reviewLabel(asset)}</small></span>
              </button>
            ))}
          </div>
        ) : (
          <div className="library-empty"><Film size={23} /><div><strong>{assets.length ? "没有匹配的素材" : "素材库还是空的"}</strong><p>{assets.length ? "调整搜索条件或审核状态。" : "选择素材目录后，系统会识别长视频中的可用片段。"}</p></div>{!assets.length ? <button onClick={() => void scan()} type="button">扫描素材目录</button> : null}</div>
        )}
      </div>
    </AppShell>
  );
}
