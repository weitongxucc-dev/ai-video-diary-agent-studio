import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";

import styles from "./TimelineVersionHistory.module.css";
import { Button } from './ui/button';
import {
  CreationPlanApiError,
  getCreationTaskTimelineVersion,
  listCreationTaskTimelineVersions,
} from "@/lib/api";
import type {
  CreationTaskTimelineVersionArtifactKind,
  CreationTaskTimelineVersionDetail,
  CreationTaskTimelineVersionList,
} from "@/lib/types";

export type TimelineVersionHistoryProps = {
  taskId: string;
  open: boolean;
  currentVersionId: string | null;
  restoreBusy?: boolean;
  onRestore?: (operationId: string, redo: boolean) => void;
};

const artifactLabels: Record<CreationTaskTimelineVersionArtifactKind, string> = {
  video: "成片",
  subtitles: "字幕文件",
  cover: "封面",
};

const trackStatusLabels: Record<string, string> = {
  failed: "生成失败",
  unavailable: "已失效",
  superseded: "已被新版本替代",
};

function versionTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function seconds(value: number) {
  return `${value.toFixed(1)}s`;
}

function errorText(error: unknown) {
  if (error instanceof CreationPlanApiError) return error.message;
  return "历史版本暂时读不出来。";
}

export function TimelineVersionHistory({ taskId, open, currentVersionId, restoreBusy, onRestore }: TimelineVersionHistoryProps) {
  const [versions, setVersions] = useState<CreationTaskTimelineVersionList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CreationTaskTimelineVersionDetail | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const detailRequest = useRef(0);
  const currentTask=useRef(taskId);currentTask.current=taskId;
  const [pages,setPages]=useState<(number|undefined)[]>([undefined]);
  const beforeSequence=pages.at(-1);
  useEffect(()=>{detailRequest.current++;setSelectedId(null);setDetail(null);setPages([undefined]);},[taskId]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingList(true);
    (beforeSequence===undefined?listCreationTaskTimelineVersions(taskId):listCreationTaskTimelineVersions(taskId,beforeSequence))
      .then((loaded) => {
        if (cancelled) return;
        if (loaded.task_id!==taskId) throw new Error('历史版本不属于当前任务。');
        setVersions(loaded);
        setListError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setListError(errorText(error));
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, open, currentVersionId,beforeSequence]);

  const loadDetail = useCallback((versionId: string) => {
    // 每次选择自增一次令牌：响应只允许作用于仍是最新选择的那一次，否则快速点 A 再 B 时 A 会覆盖 B
    const request = detailRequest.current + 1;
    detailRequest.current = request;
    setSelectedId(versionId);
    setDetail(null);
    setLoadingDetail(true);
    setDetailError(null);
    getCreationTaskTimelineVersion(taskId, versionId)
      .then((loaded) => {
        if (currentTask.current!==taskId || detailRequest.current !== request) return;
        if (loaded.task_id!==taskId || loaded.version.id!==versionId) throw new Error('历史版本详情身份不一致。');
        setDetail(loaded);
        setDetailError(null);
      })
      .catch((error: unknown) => {
        if (detailRequest.current !== request) return;
        setDetail(null);
        setDetailError(errorText(error));
      })
      .finally(() => {
        if (detailRequest.current !== request) return;
        setLoadingDetail(false);
      });
  }, [taskId]);

  return <section className={styles.history} aria-label="历史时间线版本">
    <p className={styles.note}>查看历史不会改变当前作品。指定撤销或重做作用于原修改，是否可执行由实际依赖判断。</p>

    {loadingList ? <p className={styles.note}><LoaderCircle size={14} aria-hidden /> 正在读取版本…</p> : null}
    {listError ? <p className={styles.error}><AlertCircle size={14} aria-hidden /> {listError}</p> : null}

    {versions && versions.total_count === 1 && !listError
      ? <p className={styles.note}>这个任务目前只有一个版本，还没有更早的版本可以查看。</p>
      : null}

    {versions && versions.items.length > 0 ? <ol className={styles.list}>
      {versions.items.slice().reverse().map((version) => (
        <li key={version.id}>
          <button
            type="button"
            className={selectedId === version.id ? styles.itemActive : styles.item}
            aria-current={selectedId === version.id ? "true" : undefined}
            onClick={() => loadDetail(version.id)}
          >
            <span className={styles.itemTitle}>
              第 {version.sequence} 版
              {version.is_current ? <em className={styles.current}>当前版本</em> : null}
            </span>
            <span className={styles.itemMeta}>
              <time dateTime={version.created_at}>{versionTime(version.created_at)}</time>
              {" · "}{version.shot_count} 个镜头
              {version.subtitle_track_count ? ` · ${version.subtitle_track_count} 条字幕轨` : ""}
            </span>
          </button>
        </li>
      ))}
    </ol> : null}
    {pages.length>1?<Button variant="outline" disabled={loadingList} onClick={()=>setPages(value=>value.slice(0,-1))}>较新版本</Button>:null}
    {versions?.truncated && typeof versions.next_before_sequence==='number'?<Button variant="outline" disabled={loadingList} onClick={()=>setPages(value=>[...value,versions.next_before_sequence!])}>更早版本</Button>:null}

    {loadingDetail ? <p className={styles.note}><LoaderCircle size={14} aria-hidden /> 正在读取这一版…</p> : null}
    {detailError ? <p className={styles.error}><AlertCircle size={14} aria-hidden /> {detailError}</p> : null}

    {detail ? <div className={styles.detail}>
      <h3 className={styles.detailTitle}>第 {detail.version.sequence} 版 · {versionTime(detail.version.created_at)}</h3>
      {onRestore && detail.version.operation_id && !['UndoTimelineVersion','RedoTimelineVersion'].includes(detail.version.operation_type ?? '')?<div>
        <Button variant="outline" disabled={restoreBusy} onClick={()=>onRestore(detail.version.operation_id!,false)}>撤销这项修改</Button>
        <Button variant="outline" disabled={restoreBusy} onClick={()=>onRestore(detail.version.operation_id!,true)}>重做这项修改</Button>
      </div>:null}

      <h4 className={styles.sectionTitle}>镜头（{detail.shots.length}）</h4>
      {detail.shots.length ? <ol className={styles.shots}>
        {detail.shots.map((shot) => (
          <li key={shot.id} className={styles.shot}>
            <span className={styles.shotRange}>{seconds(shot.start_seconds)} – {seconds(shot.end_seconds)}</span>
            <span className={styles.shotText}>{shot.narration_text ?? "（这一镜头没有对应文案）"}</span>
            {shot.description ? <span className={styles.shotNote}>{shot.description}</span> : null}
          </li>
        ))}
      </ol> : <p className={styles.note}>这一版没有记录镜头。</p>}

      <h4 className={styles.sectionTitle}>字幕（{detail.subtitle_tracks.length}）</h4>
      {detail.subtitle_tracks.length ? detail.subtitle_tracks.map((track) => (
        <div key={track.id} className={styles.track}>
          <span className={styles.trackHead}>
            {track.language === "zh-CN" ? "中文轨" : "英文轨"} · {track.cues.length} 条
            {track.status !== "valid" ? ` · ${trackStatusLabels[track.status] ?? track.status}` : ""}
          </span>
          <ul className={styles.cues}>
            {track.cues.map((cue) => <li key={cue.id}>{cue.text}</li>)}
          </ul>
        </div>
      )) : <p className={styles.note}>这一版没有字幕轨。</p>}

      <h4 className={styles.sectionTitle}>产物</h4>
      <ul className={styles.artifacts}>
        {detail.artifacts.map((artifact) => (
          <li key={artifact.kind} className={styles.artifact}>
            <span className={styles.artifactLabel}>{artifactLabels[artifact.kind]}</span>
            {artifact.status === "available" && artifact.url
              ? <a className={styles.artifactLink} href={artifact.url} target="_blank" rel="noreferrer">查看这一版的{artifactLabels[artifact.kind]}</a>
              : <span className={styles.artifactReason}>{artifact.reason ?? "这一版没有可查看的产物。"}</span>}
          </li>
        ))}
      </ul>
    </div> : null}
  </section>;
}
