"use client";
import { ChevronRight, MessageSquarePlus, Play } from "lucide-react";
import type { RefObject } from "react";
import type { MaterialAsset, MaterialSegment } from "@/lib/types";
import { duration, fileLabel, mediaLabel, roleLabel, reviewLabel, analysisSummary } from './material-labels';
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    asset: MaterialAsset;
    segment: MaterialSegment | null;
    segmentScope: boolean;
    selectedSegmentId: string | null;
    disabled: boolean;
    previewFailed: boolean;
    previewReadTick: number;
    sourceAudio: RefObject<HTMLAudioElement | null>;
    sourceVideo: RefObject<HTMLVideoElement | null>;
    onPreviewError: () => void;
    onRetryPreview: () => void;
    seekSelectedSegment: (media: HTMLMediaElement) => void;
    constrainSegmentPlayback: (media: HTMLMediaElement) => void;
    startSegmentPlayback: (media: HTMLMediaElement) => void;
    onSelectAsset: () => void;
    onSelectSegment: (segment: MaterialSegment) => void;
    onReferenceSegment?: (segment: MaterialSegment) => void;
};
export function MaterialAssetPreview({ asset, segment, segmentScope, selectedSegmentId, disabled, previewFailed, previewReadTick, sourceAudio, sourceVideo, onPreviewError, onRetryPreview, seekSelectedSegment, constrainSegmentPlayback, startSegmentPlayback, onSelectAsset, onSelectSegment, onReferenceSegment }: Props) {
    return <>
    <div className={`library-preview ${styles.preview}`}>
        {asset.media_type === "image" ? (<img alt={`原素材预览：${fileLabel(asset.file_name)}`} key={`${asset.id}:${previewReadTick}`} src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} onError={() => onPreviewError()}/>) : asset.media_type === "audio" ? (<audio ref={sourceAudio} onLoadedMetadata={event => seekSelectedSegment(event.currentTarget)} aria-label="原素材预览" onTimeUpdate={event => constrainSegmentPlayback(event.currentTarget)} onPlay={event => startSegmentPlayback(event.currentTarget)} controls key={`${asset.id}:${previewReadTick}`} preload="metadata" src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} onError={() => onPreviewError()}/>) : (<video ref={sourceVideo} aria-label="原素材预览" onTimeUpdate={event => constrainSegmentPlayback(event.currentTarget)} onPlay={event => startSegmentPlayback(event.currentTarget)} onLoadedMetadata={event => seekSelectedSegment(event.currentTarget)} controls key={`${asset.id}:${previewReadTick}`} preload="metadata" src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} onError={() => onPreviewError()}/>)}
        {asset.media_type === "image" ? null : (<span>
            <Play size={14}/> {duration(asset.duration_seconds)}
          </span>)}
      </div>
      <div className="library-inspector__title">
        <strong title={fileLabel(asset.file_name)}>{fileLabel(asset.file_name)}</strong>
        <span>
          {mediaLabel(asset.media_type)} · {roleLabel(asset)} · {reviewLabel(asset)}
        </span>
      </div>
      <div className={styles.rangeHeading}><span>{segmentScope && segment ? `当前范围：${duration(segment.start_seconds)}–${duration(segment.end_seconds)}` : "当前范围：整条素材"}</span>{segmentScope ? <button onClick={onSelectAsset} type="button">切回整条素材</button> : null}</div>
      {previewFailed ? <p className={styles.analysisMessage} role="alert">原素材暂时无法预览，请连接原盘或检查文件后重试。<button type="button" onClick={onRetryPreview}>重新读取预览</button></p> : null}
      <p className={styles.modalitySummary}>{analysisSummary(asset)}</p>
      {asset.analysis_status === "pending" ? (<div className={styles.analysisMessage}>
          <p role="status">等待分析，完成时间暂未知。</p>
          {asset.analysis_message.trim() ? (/[{}\n\r]|Traceback|Error:|\/Users\/|\/Volumes\/|[A-Za-z]:\\/.test(asset.analysis_message)
                ? <details><summary>后台分析记录</summary><p>{asset.analysis_message}</p></details>
                : <p>{asset.analysis_message}</p>) : null}
        </div>) : null}
      {asset.segments.length ? (<div className="library-segments">
          <h3>识别片段</h3>
          {asset.segments.map((segment) => (<div className={styles.referenceEntry} key={segment.id}><button className={segmentScope && selectedSegmentId === segment.id ? "is-selected" : ""} aria-pressed={segmentScope && selectedSegmentId === segment.id} disabled={disabled} onClick={() => onSelectSegment(segment)} type="button">
              <span>
                {duration(segment.start_seconds)}–
                {duration(segment.end_seconds)}
              </span>
              <p>
                {segment.description ||
                    segment.understanding?.summary ||
                    segment.transcript ||
                    "等待补充片段描述"}
              </p>
              <ChevronRight size={14}/>
            </button>{onReferenceSegment ? <button className={styles.referenceButton} type="button" title="引用到对话" aria-label={`引用到对话：${fileLabel(asset.file_name)} · 片段 ${duration(segment.start_seconds)}–${duration(segment.end_seconds)}`} onClick={() => onReferenceSegment(segment)}><MessageSquarePlus size={16} /></button> : null}</div>))}
        </div>) : null}
  </>;
}
