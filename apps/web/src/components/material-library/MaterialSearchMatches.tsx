"use client";

import { useId, useRef, useState, type SyntheticEvent } from "react";
import { Film, Volume2 } from "lucide-react";
import { Button } from "../ui/button";
import { WorkspaceAudio, WorkspaceVideo } from "../workbench/WorkspaceMedia";
import type { MaterialAsset, MaterialLibraryHit, MaterialSegment } from "@/lib/types/materials";
import { assetDuration, fileLabel } from "./material-labels";
import { useStateOptions } from "./material-state";
import styles from "./MaterialSearchMatches.module.css";

export type MaterialResultActions = {
  onSelect: (asset: MaterialAsset, segment?: MaterialSegment) => void;
  onReference?: (asset: MaterialAsset, segment?: MaterialSegment) => void;
  onReplace?: (asset: MaterialAsset, segment: MaterialSegment) => Promise<boolean>;
};

function sourceTime(seconds: number) {
  const centiseconds = Math.round(seconds * 100);
  return `${Math.floor(centiseconds / 6000)}:${(centiseconds % 6000 / 100).toFixed(2).padStart(5, "0")}`;
}

export function materialHitKey(asset: MaterialAsset, segment: MaterialSegment): string {
  return JSON.stringify([asset.id, segment.id, asset.updated_at, segment.updated_at]);
}

function MatchPreview({ asset, segment, active }: {
  asset: MaterialAsset;
  segment: MaterialSegment;
  active: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const frameRequest = useRef<number | null>(null);
  const start = segment.start_seconds;
  const end = segment.end_seconds;
  const source = `/api/production/materials/${encodeURIComponent(asset.id)}/media#t=${start},${end}`;
  if (failed || !asset.source_available) {
    return active ? <p role="alert">原素材无法预览。请检查原盘或文件后重新打开试看。</p> : <Film aria-hidden />;
  }
  if (asset.media_type === "audio" && !active) return <Volume2 aria-hidden />;

  const positionAtStart = (media: HTMLMediaElement) => { media.currentTime = start; };
  function stopAtEnd(media: HTMLMediaElement) {
    if (media.currentTime < end) return;
    if (!media.paused) media.pause();
    if (media.currentTime > end) media.currentTime = end;
  }
  function trackVideoEnd(video: HTMLVideoElement) {
    if (!video.requestVideoFrameCallback) return;
    const checkFrame: VideoFrameRequestCallback = () => {
      frameRequest.current = null;
      if (video.paused) return;
      stopAtEnd(video);
      if (!video.paused) frameRequest.current = video.requestVideoFrameCallback(checkFrame);
    };
    frameRequest.current = video.requestVideoFrameCallback(checkFrame);
  }
  const player = {
    src: source, controls: active, autoPlay: active, preload: active ? "auto" : "metadata",
    "aria-label": active ? "命中区间试看" : undefined,
    "aria-hidden": active ? undefined : true,
    onLoadedMetadata: (event: SyntheticEvent<HTMLMediaElement>) => positionAtStart(event.currentTarget),
    onSeeking: (event: SyntheticEvent<HTMLMediaElement>) => {
      const media = event.currentTarget;
      if (media.currentTime < start) positionAtStart(media);
      else if (media.currentTime > end) media.currentTime = end;
    },
    onTimeUpdate: (event: SyntheticEvent<HTMLMediaElement>) => {
      if (active && !event.currentTarget.seeking) stopAtEnd(event.currentTarget);
    },
    onPlay: (event: SyntheticEvent<HTMLMediaElement>) => {
      const media = event.currentTarget;
      if (media.currentTime < start || media.currentTime >= end) positionAtStart(media);
    },
    onError: () => setFailed(true),
  };
  return asset.media_type === "audio"
    ? <WorkspaceAudio {...player} />
    : <WorkspaceVideo {...player} muted={!active} playsInline
        onPlay={event => {
          player.onPlay(event);
          trackVideoEnd(event.currentTarget);
        }}
        onPause={event => {
          if (frameRequest.current !== null) {
            event.currentTarget.cancelVideoFrameCallback(frameRequest.current);
            frameRequest.current = null;
          }
        }} />;
}

function MatchRow({ asset, segment, hit, disabled, previewKey, onPreviewChange, replacing, replaceNotice, onReplace, onSelect, onReference }: MaterialResultActions & {
  asset: MaterialAsset;
  segment: MaterialSegment;
  hit: MaterialLibraryHit;
  disabled: boolean;
  previewKey: string | null;
  onPreviewChange: (key: string | null) => void;
  replacing: string | null;
  replaceNotice: { key: string; message: string } | null;
}) {
  const previewId = useId();
  const key = materialHitKey(asset, segment);
  const active = previewKey === key && !disabled;
  const timeRange = `${sourceTime(segment.start_seconds)}–${sourceTime(segment.end_seconds)}`;
  return <li className={styles.match}>
    <div className={styles.matchBody}>
      {asset.source_available && !active ? <div className={styles.poster}><MatchPreview key={key} asset={asset} segment={segment} active={false} /></div> : null}
      <div className={styles.matchCopy}>
        <div className={styles.matchFacts}>
          <strong>{timeRange}</strong>
          <span>{hit.basis}</span>
          {!asset.source_available ? <span className={styles.restriction}>原素材离线</span> :
            hit.restriction ? <span className={styles.restriction}>{hit.restriction}</span> : null}
        </div>
        <p>{hit.excerpt}</p>
      </div>
    </div>
    <div className={styles.actions}>
      {asset.source_available ? <Button type="button" variant="ghost" disabled={disabled} aria-expanded={active} aria-controls={previewId} onClick={() => onPreviewChange(active ? null : key)}>{active ? "关闭试看" : "试看片段"}</Button> : null}
      {onReference && hit.reference_allowed ? <Button type="button" variant="ghost" disabled={disabled} onClick={() => onReference(asset, segment)}>引用此片段</Button> : null}
      {onReplace && hit.manual_use_allowed && asset.source_available && asset.media_type === "video" ? <Button
        type="button" variant="ghost" disabled={disabled || replacing !== null}
        title="保留当前镜头时长，从命中区间起点取用。源区间不足时会提示。"
        onClick={() => void onReplace(asset, segment)}>
        {replacing === key ? "正在替换…" : "替换当前镜头"}
      </Button> : null}
      <Button type="button" variant="ghost" data-open-details disabled={disabled} onClick={() => onSelect(asset, segment)}>片段详情</Button>
    </div>
    {replaceNotice?.key === key ? <p className={styles.notice} role="status">{replaceNotice.message}</p> : null}
    <div id={previewId} className={styles.player} hidden={!active}>
      {active ? <MatchPreview key={key} asset={asset} segment={segment} active /> : null}
    </div>
  </li>;
}

export function MaterialSearchMatches({ asset, ...row }: Omit<Parameters<typeof MatchRow>[0], "hit" | "segment">) {
  const [expanded, setExpanded] = useState(false);
  const titleId = useId();
  const listId = useId();
  const segments = new Map(asset.segments.map(segment => [segment.id, segment]));
  const results = (asset.search_hits ?? []).flatMap(hit => {
    const segment = segments.get(hit.segment_id);
    return segment ? [{ hit, segment }] : [];
  });
  return <section className={styles.file} aria-labelledby={titleId}>
    <header className={styles.fileHeading}>
      <strong id={titleId} title={asset.file_name}>{fileLabel(asset.file_name)}</strong>
      <span>{assetDuration(asset.duration_seconds)}</span>
      <span>{useStateOptions.find(option => option.value === asset.use_state)?.label}</span>
    </header>
    <ul className={styles.list} id={listId}>
      {results.slice(0, expanded ? undefined : 1).map(({ hit, segment }) => <MatchRow
        key={hit.segment_id} asset={asset} hit={hit} segment={segment} {...row} />)}
    </ul>
    {results.length > 1 ? <Button type="button" variant="ghost" aria-expanded={expanded} aria-controls={listId}
      onClick={() => setExpanded(value => !value)}>{expanded ? "收起其他命中" : `其他 ${results.length - 1} 个命中片段`}</Button> : null}
    <footer className={styles.actions}>
      <Button type="button" variant="ghost" data-open-details disabled={row.disabled} onClick={() => row.onSelect(asset)}>整条素材详情</Button>
      {row.onReference ? <Button type="button" variant="ghost" disabled={row.disabled} onClick={() => row.onReference?.(asset)}>引用整条素材</Button> : null}
    </footer>
  </section>;
}
