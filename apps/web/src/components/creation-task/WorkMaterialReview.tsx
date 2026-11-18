"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { confirmMaterialSegmentUsage, getProductionMaterial, getProductionMaterials } from "@/lib/api/materials";
import { ApiRequestError } from "@/lib/api/transport";
import type { MaterialAsset, MaterialLibrary, MaterialSegment, MaterialSegmentMatch } from "@/lib/types";
import { HomeCardDialog } from "../HomeCardDialog";
import { visibleError } from "../material-library/material-errors";
import { duration } from "../material-library/material-labels";
import { Button } from "../ui/button";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import styles from "./WorkMaterialReview.module.css";

const PAGE_SIZE = 12;
const roles = {
  unassigned: "待判断",
  a_roll: "人物主镜头",
  b_roll: "补充画面",
  exclude: "不使用",
} as const;

type Role = MaterialSegment["usage_role"];
type Props = {
  matches?: MaterialSegmentMatch[];
  disabled?: boolean;
  onSaved?: (asset: MaterialAsset) => void;
};

function timeRange(segment: MaterialSegment) {
  return `${duration(segment.start_seconds)}–${duration(segment.end_seconds)}`;
}

function unavailableReason(asset: MaterialAsset, segment: MaterialSegment) {
  if (asset.analysis_status !== "ready") return "素材尚未解析完成。";
  if (asset.review_status === "rejected" || segment.review_status === "rejected" ||
      asset.usage_role === "exclude" || segment.usage_role === "exclude") return "此素材或片段已排除。";
  if (asset.use_state === "excluded" || asset.use_state === "reference_only" ||
      asset.use_state === "not_for_automation") return "此素材的使用范围不允许方案自动选用。";
  return null;
}

function SegmentReview({ initialAsset, initialSegmentId, onBack, onSaved, setBusy }: {
  initialAsset: MaterialAsset;
  initialSegmentId: string;
  onBack: () => void;
  onSaved?: Props["onSaved"];
  setBusy: (busy: boolean) => void;
}) {
  const [asset, setAsset] = useState(initialAsset);
  const [segmentId, setSegmentId] = useState(initialSegmentId);
  const [roleDraft, setRoleDraft] = useState<Role | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [previewRead, setPreviewRead] = useState(0);
  const pending = useRef(false);
  const segment = asset.segments.find(item => item.id === segmentId);
  const blocked = segment ? unavailableReason(asset, segment) : null;
  const role = roleDraft ?? segment?.usage_role;

  function selectSegment(id: string) {
    setSegmentId(id);
    setRoleDraft(null);
    setMessage("");
    setError("");
    setConflict(false);
    setPreviewError(false);
  }

  function setPending(value: boolean) {
    pending.current = value;
    setSaving(value);
    setBusy(value);
  }

  async function readLatest() {
    if (pending.current) return;
    setPending(true);
    try {
      const latest = await getProductionMaterial(asset.id);
      setAsset(latest);
      selectSegment(latest.segments.some(item => item.id === segmentId) ? segmentId : latest.segments[0]?.id ?? "");
      setMessage("已读取当前版本，请重新核对片段。");
    } catch (cause) {
      setError(visibleError(cause, "片段读取失败，请重试。"));
    } finally {
      setPending(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current || conflict || blocked || previewError || !segment || !role || role === segment.usage_role) return;
    setPending(true);
    setError("");
    setMessage("");
    try {
      const updated = await confirmMaterialSegmentUsage(asset.id, segment.id, {
        usage_role: role,
        expected_asset_updated_at: asset.updated_at,
        expected_segment_updated_at: segment.updated_at,
      });
      setAsset(updated);
      setRoleDraft(null);
      setMessage(`已保存片段用途：${roles[role]}。`);
      onSaved?.(updated);
    } catch (cause) {
      setError(visibleError(cause, "片段用途保存失败，请重试。"));
      setConflict(cause instanceof ApiRequestError && cause.status === 409);
    } finally {
      setPending(false);
    }
  }

  function seekStart(media: HTMLVideoElement) {
    if (segment) media.currentTime = segment.start_seconds;
  }

  return <div className={styles.review}>
    <button className={styles.back} type="button" disabled={saving} onClick={onBack}>返回素材列表</button>
    <div className={styles.heading}><strong>{asset.file_name}</strong></div>
    {!segment ? <p role="status">此素材暂无已解析片段。</p> : <>
      <label className={styles.field}><span>选择片段</span>
        <select value={segment.id} disabled={saving} onChange={event => selectSegment(event.target.value)}>
          {asset.segments.map(item => <option key={item.id} value={item.id}>{timeRange(item)} · {roles[item.usage_role]}</option>)}
        </select>
      </label>
      <WorkspaceVideo key={`${asset.id}:${segment.id}:${previewRead}`} aria-label="原片段预览"
        src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} className={styles.preview}
        controls preload="metadata"
        onLoadedMetadata={event => seekStart(event.currentTarget)}
        onPlay={event => {
          const media = event.currentTarget;
          if (media.currentTime < segment.start_seconds || media.currentTime >= segment.end_seconds) seekStart(media);
        }}
        onTimeUpdate={event => {
          const media = event.currentTarget;
          if (media.currentTime < segment.start_seconds) seekStart(media);
          else if (media.currentTime > segment.end_seconds) {
            media.pause();
            media.currentTime = segment.end_seconds;
          }
        }}
        onError={() => setPreviewError(true)} />
      {previewError ? <p role="alert">暂时无法预览原片。<Button type="button" variant="ghost" onClick={() => {
        setPreviewError(false);
        setPreviewRead(value => value + 1);
      }}>重新读取预览</Button></p> : null}
      <p>{timeRange(segment)}{segment.description || segment.understanding?.summary ?
        ` · ${segment.description || segment.understanding?.summary}` : ""}</p>
      {blocked ? <p>{blocked}</p> : <form aria-label="确认片段用途" onSubmit={save}>
        <fieldset className={styles.roles} disabled={saving || conflict || previewError}>
          <legend>片段用途</legend>
          {Object.entries(roles).map(([value, label]) =>
            <label key={value}><input type="radio" name="usage_role" value={value} checked={role === value}
              onChange={() => { setRoleDraft(value as Role); setMessage(""); setError(""); }} />{label}</label>)}
        </fieldset>
        <p className={styles.note}>用途保存到素材库，供后续方案选片。</p>
        <Button type="submit" disabled={saving || conflict || previewError || role === segment.usage_role}>{saving ? "处理中…" : "保存片段用途"}</Button>
      </form>}
    </>}
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert" className={styles.error}>{error}</p> : null}
    {conflict ? <Button type="button" variant="outline" disabled={saving} onClick={() => void readLatest()}>重新读取片段</Button> : null}
  </div>;
}

function MaterialReviewSheet({ matches, onClose, onSaved }: {
  matches: MaterialSegmentMatch[];
  onClose: () => void;
  onSaved?: Props["onSaved"];
}) {
  const [filter, setFilter] = useState({ query: "", offset: 0 });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState<MaterialLibrary | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<MaterialAsset | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    setPage(null);
    setError("");
    void getProductionMaterials({ limit: PAGE_SIZE, offset: filter.offset, query: filter.query, mediaType: "video" }).then(result => {
      if (current) setPage(result);
    }).catch(cause => {
      if (current) setError(visibleError(cause, "读取素材失败，请重试。"));
    });
    return () => { current = false; };
  }, [filter]);

  function readMatches(asset: MaterialAsset) {
    return matches.filter(match => match.asset_ref === `asset:${asset.id}` || match.asset_ref === asset.id);
  }

  function accept(updated: MaterialAsset) {
    setPage(previous => previous ? { ...previous, assets: previous.assets.map(asset => asset.id === updated.id ? updated : asset) } : previous);
    onSaved?.(updated);
  }

  return <HomeCardDialog kind="detail" title="核实镜头与用途" onClose={() => { if (!busy) onClose(); }}>
    {selected ? <SegmentReview key={selected.id} initialAsset={selected}
      initialSegmentId={selected.segments.find(segment => readMatches(selected).some(match =>
        match.segment_ref === `segment:${segment.id}` || match.segment_ref === segment.id))?.id ?? selected.segments[0]?.id ?? ""}
      onBack={() => setSelected(null)} onSaved={accept} setBusy={setBusy} /> : <div className={styles.review}>
      <form aria-label="查找视频素材" className={styles.search} onSubmit={event => {
        event.preventDefault();
        setFilter({ query: search.trim(), offset: 0 });
      }}>
        <label className={styles.field}><span>搜索素材</span>
          <input type="search" name="q" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        <Button type="submit" variant="outline">搜索</Button>
      </form>
      {error ? <p role="alert" className={styles.error}>{error} <button type="button" onClick={() => setFilter({ ...filter })}>重新读取</button></p> : null}
      {!page && !error ? <p role="status">正在读取视频素材…</p> : null}
      {page && !page.assets.length ? <p role="status">没有找到视频素材。</p> : null}
      {page?.assets.length ? <ul className={styles.results}>{page.assets.map(asset =>
        <li key={asset.id}><button type="button" onClick={() => setSelected(asset)}>
          <strong>{asset.file_name}</strong>
          <span>{asset.segments.length} 个片段{readMatches(asset).length ? " · 含本组候选" : ""}</span>
        </button></li>)}</ul> : null}
      {page && (page.offset > 0 || page.has_more) ? <div className={styles.pages}>
        <Button type="button" variant="outline" disabled={page.offset === 0}
          onClick={() => setFilter({ ...filter, offset: Math.max(0, page.offset - PAGE_SIZE) })}>上一页</Button>
        <Button type="button" variant="outline" disabled={!page.has_more}
          onClick={() => setFilter({ ...filter, offset: page.offset + page.limit })}>下一页</Button>
      </div> : null}
    </div>}
  </HomeCardDialog>;
}

export function WorkMaterialReview({ matches = [], disabled, onSaved }: Props) {
  const [open, setOpen] = useState(false);
  return <>
    <Button type="button" variant="outline" disabled={disabled} onClick={() => setOpen(true)}>核实镜头与用途</Button>
    {open ? <MaterialReviewSheet matches={matches} onClose={() => setOpen(false)} onSaved={onSaved} /> : null}
  </>;
}
