"use client";
import { WorkspaceVideo, WorkspaceAudio } from "./workbench/WorkspaceMedia";
import { useWorkspaceViewState, useWorkspacePage } from "./workbench/context";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { GraphicOverlayEditor } from "./GraphicOverlayEditor";
import { VisualEffectsInspector } from "./VisualEffectsInspector";

import { useEffect, useId, useRef, useState, type RefObject, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Check, Lock, Unlock, Maximize, Pause, Play, Plus, Replace, Volume2, VolumeX } from "lucide-react";
import { apiUrl, listenCreationTaskAudio, resolveCreationTaskMediaResponse, cancelCreationTaskMediaJob, taskMediaJobLabel, TaskMediaJobError } from "@/lib/api";
import type { CreationTaskMontageClip, CreationTaskExactEditInput, CreationTaskMontage, CreationTaskSubtitleTrack, CreationTaskPersonRender, CreationTaskPersonRenderCapability, GraphicOverlay, MontageClipCandidate, TaskMediaJob } from "@/lib/types";
import ui from "./VideoEditor.module.css";
import quickUi from "./VideoClipQuickEditor.module.css";
import mediaUi from "./VideoEditorMedia.module.css";
import { CardSelect, HomeCardDialog } from "./HomeCardDialog";
import { NarrationWaveformTrack } from "./NarrationWaveformTrack";

export const editorTime = (value: number) => `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toFixed(1).padStart(4, "0")}`;
export type VideoClipDraft = { start: number; mode: "fill" | "fit"; x: number; y: number };
export function createVideoClipDraft(clip: CreationTaskMontageClip): VideoClipDraft {
  return { start: clip.source_start_seconds, mode: clip.fit_mode ?? "fill", x: clip.crop_x ?? .5, y: clip.crop_y ?? .5 };
}
const sourceUrl = (clip: CreationTaskMontageClip, taskId: string) => apiUrl(clip.person_render_id
  ? `/api/creation-tasks/${encodeURIComponent(taskId)}/person-renders/${encodeURIComponent(clip.person_render_id)}/media`
  : `/api/production/materials/${encodeURIComponent(clip.asset_id)}/media`);
const clipMediaKey = (clip: CreationTaskMontageClip, taskId: string) => JSON.stringify([taskId, clip.id, sourceUrl(clip, taskId), clip.source_reference, clip.source_start_seconds, clip.source_end_seconds]);

type ThumbnailRead = { start: () => void; queued: boolean };
const thumbnailReads: ThumbnailRead[] = [];
let activeThumbnailRead: ThumbnailRead | null = null;
function advanceThumbnailRead() {
  if (activeThumbnailRead) return;
  const next = thumbnailReads.shift();
  if (!next) return;
  next.queued = false;
  activeThumbnailRead = next;
  next.start();
}
function scheduleThumbnailRead(start: () => void) {
  const read: ThumbnailRead = {start, queued: true};
  thumbnailReads.push(read);
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    if (read.queued) thumbnailReads.splice(thumbnailReads.indexOf(read), 1);
    else if (activeThumbnailRead === read) activeThumbnailRead = null;
    queueMicrotask(advanceThumbnailRead);
  };
  return {start: advanceThumbnailRead, release};
}

export function ClipThumbnail({ clip, taskId, className, statusId }: { clip: CreationTaskMontageClip; taskId: string; className?: string; statusId?: string }) {
  return <StaticClipThumbnail key={clipMediaKey(clip, taskId)} clip={clip} taskId={taskId} className={className} statusId={statusId} />;
}

function StaticClipThumbnail({ clip, taskId, className, statusId }: { clip: CreationTaskMontageClip; taskId: string; className?: string; statusId?: string }) {
  const viewActive = useWorkspacePage()?.active ?? true;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const missing = !clip.person_render_id && !clip.asset_id;
  const url = sourceUrl(clip, taskId);
  useEffect(() => {
    if (!viewActive || missing || state === "ready") return;
    let video: HTMLVideoElement | null = null;
    let disposed = false;
    const destroyVideo = () => {
      if (!video) return;
      video.onloadedmetadata = null; video.onloadeddata = null; video.onseeked = null; video.oncanplay = null; video.onerror = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
      video = null;
    };
    const finish = (next: "ready" | "error") => {
      if (disposed) return;
      disposed = true;
      destroyVideo();
      setState(next);
      scheduled.release();
    };
    const scheduled = scheduleThumbnailRead(() => {
      if (disposed) return;
      video = document.createElement("video");
      video.muted = true; video.playsInline = true; video.preload = "auto";
      const frameReady = () => {
        const current = video;
        if (disposed || !current || current.readyState < 2 || current.seeking
          || Math.abs(current.currentTime - clip.source_start_seconds) > .05) return;
        const canvas = canvasRef.current;
        if (!canvas || current.videoWidth <= 0 || current.videoHeight <= 0) { finish("error"); return; }
        const scale = Math.min(1, 256 / Math.max(current.videoWidth, current.videoHeight));
        canvas.width = Math.max(1, Math.round(current.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(current.videoHeight * scale));
        try {
          const context = canvas.getContext("2d");
          if (!context) { finish("error"); return; }
          context.drawImage(current, 0, 0, canvas.width, canvas.height);
        }
        catch { finish("error"); return; }
        finish("ready");
      };
      video.onloadedmetadata = () => { if (!disposed && video) video.currentTime = clip.source_start_seconds; };
      video.onloadeddata = frameReady; video.onseeked = frameReady; video.oncanplay = frameReady;
      video.onerror = () => finish("error");
      video.src = url;
      video.load();
    });
    scheduled.start();
    return () => { disposed = true; destroyVideo(); scheduled.release(); };
  }, [viewActive, missing, url, clip.source_start_seconds]);
  const message = missing ? "未关联媒体" : state === "loading" ? "读取缩略图…" : state === "error" ? "缩略图读取失败" : "缩略图已定位";
  return <span className={`${mediaUi.thumbnail} ${className ?? mediaUi.thumbnailFill}`} data-media-state={missing ? "missing" : state}>
    <canvas ref={canvasRef} width={1} height={1} aria-hidden="true" />
    <span id={statusId} className={state === "ready" && !missing ? mediaUi.srOnly : mediaUi.thumbnailState} aria-live="off">{message}</span>
  </span>;
}

function ClipMedia({ clip, taskId, className, statusId, preview = false, bounded = false }: { clip: CreationTaskMontageClip; taskId: string; className?: string; statusId?: string; preview?: boolean; bounded?: boolean }) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);
  const [retry, setRetry] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const positioned = useRef(false);
  const seekTarget = useRef(clip.source_start_seconds);
  const missing = !clip.person_render_id && !clip.asset_id;
  const identity = clip.person_render_id ? (bounded ? "镜头人物口型预览" : "人物口型预览") : (bounded ? "镜头源素材预览" : "源素材预览");
  function frameReady(video: HTMLVideoElement) {
    if (video !== videoRef.current || video.readyState < 2 || video.seeking) return;
    if (!positioned.current && Math.abs(video.currentTime - seekTarget.current) > .05) return;
    positioned.current = true;
    setState("ready");
  }
  const video = missing ? null : <WorkspaceVideo key={retry} ref={videoRef} className={bounded ? mediaUi.sourceVideo : undefined} style={bounded && dimensions ? { aspectRatio: `${dimensions.width} / ${dimensions.height}` } : undefined} aria-hidden={preview ? undefined : true} aria-label={preview ? identity : undefined} positionKey={`${identity}:${clipMediaKey(clip, taskId)}`}
    controls={preview} muted={!preview} playsInline preload="metadata" src={sourceUrl(clip, taskId)}
    onLoadedMetadata={event => {
      const current = event.currentTarget;
      if (current !== videoRef.current) return;
      if (bounded && current.videoWidth > 0 && current.videoHeight > 0) setDimensions({ width: current.videoWidth, height: current.videoHeight });
      seekTarget.current = clip.source_start_seconds;
      current.currentTime = seekTarget.current;
    }}
    onSeeking={event => { if (event.currentTarget === videoRef.current) seekTarget.current = event.currentTarget.currentTime; }}
    onLoadedData={event => frameReady(event.currentTarget)} onSeeked={event => frameReady(event.currentTarget)} onCanPlay={event => frameReady(event.currentTarget)}
    onError={event => { if (event.currentTarget === videoRef.current) setState("error"); }} />;
  const message = missing ? "未关联媒体" : state === "loading" ? (preview ? "正在读取原片…" : "读取缩略图…") : state === "error" ? (preview ? "原片暂时无法读取" : "缩略图读取失败") : "缩略图已定位";
  const previewContent = <>{video}<span className={ui.playerBadge}>{clip.person_render_id ? "人物口型 · 非成片" : "源素材 · 原始画面，非成片"}</span>
    {missing || state !== "ready" ? <div className={mediaUi.playerState} role={state === "error" ? "alert" : "status"}>
      <span>{message}</span>{state === "error" && !missing ? <button className={ui.button} type="button" onClick={() => { positioned.current = false; setDimensions(null); setState("loading"); setRetry(value => value + 1); }}>重新读取原片</button> : null}
    </div> : null}</>;
  if (preview) return bounded ? <div className={`${mediaUi.sourcePreview} ${className ?? ""}`} data-media-state={missing ? "missing" : state}>{previewContent}</div> : previewContent;
  return <span className={`${mediaUi.thumbnail} ${className ?? mediaUi.thumbnailFill}`} data-media-state={missing ? "missing" : state}>
    {video}<span id={statusId} className={state === "ready" && !missing ? mediaUi.srOnly : mediaUi.thumbnailState} aria-live="off">{message}</span>
  </span>;
}

function CanvasControls({ ratio, fitMode, versionId, editError, busy, onPreview, onDraftChange }: {ratio:"9:16"|"16:9"|"1:1";fitMode:""|"fill"|"fit";versionId:string|null;editError?:string;busy:boolean;onPreview:(input:CreationTaskExactEditInput)=>Promise<boolean>;onDraftChange:()=>void}) {
  const [draft,setDraft]=useWorkspaceViewState(`canvas:${versionId}:${ratio}:${fitMode}.ratio`, ratio);
  const [fit,setFit]=useWorkspaceViewState<""|"fill"|"fit">(`canvas:${versionId}:${ratio}:${fitMode}.fit`, fitMode);
  const [open,setOpen]=useState(false);
  return <><button className={ui.button} onClick={()=>setOpen(true)} type="button">画幅设置</button>{open ? <HomeCardDialog kind="config" title="成片画幅" onClose={()=>setOpen(false)}><div className={ui.canvasFields}>
    <label className={ui.field}>画幅<CardSelect label="成片画幅" disabled={busy} value={draft} onChange={value=>{const next=value as typeof draft;onDraftChange();setDraft(next);void onPreview({operation_type:"ChangeCanvas",aspect_ratio:next,...(fit?{fit_mode:fit}:{})}).then(saved=>{if(!saved){setDraft(ratio);setFit(fitMode);}});}} options={[{value:"9:16",label:"9:16 竖屏"},{value:"16:9",label:"16:9 横屏"},{value:"1:1",label:"1:1 方形"}]} /></label>
    <label className={ui.field}>画面<CardSelect label="整片画面适配" disabled={busy} value={fit} onChange={value=>{const next=value as typeof fit;onDraftChange();setFit(next);void onPreview({operation_type:"ChangeCanvas",aspect_ratio:draft,...(next?{fit_mode:next}:{})}).then(saved=>{if(!saved){setDraft(ratio);setFit(fitMode);}});}} options={[{value:"",label:"各镜头原样"},{value:"fit",label:"完整画面"},{value:"fill",label:"填满画面"}]} /></label>
    <small>填满：可能裁切 · 完整：可能留边</small>
    {editError ? <p role="alert">{editError}</p> : null}
    </div>
  </HomeCardDialog> : null}</>;
}

type VideoStageMontage = Pick<CreationTaskMontage, "clips" | "cues" | "duration_seconds" | "output_url" | "low_confidence_clip_count" | "aspect_ratio" | "graphic_overlays">;
export function VideoEditingStage({ taskId, versionId, narrationCacheKey, montage, planAspectRatio, subtitleTracks, selectedId, onSelect, onPosition, videoRef, initialTime, renderAction, audioTracks, busy = false, onPreview, onDraftChange = () => {}, selectedOverlayId, onSelectOverlay, editError }:  {
  editError?: string;
  selectedOverlayId?: string|null; onSelectOverlay?: (id:string)=>void;
  taskId: string; versionId: string | null; narrationCacheKey: string | null; montage: VideoStageMontage;
  planAspectRatio: NonNullable<CreationTaskMontage["aspect_ratio"]>; subtitleTracks: CreationTaskSubtitleTrack[];
  selectedId: string | null; onSelect: (id: string) => void;
  onPosition: (video: HTMLVideoElement) => void; videoRef: RefObject<HTMLVideoElement | null>;
  initialTime: number; renderAction: ReactNode; audioTracks?: (subtitleTrack: ReactNode) => ReactNode; busy?: boolean; onDraftChange?:()=>void; onPreview?: (input:CreationTaskExactEditInput)=>Promise<boolean>;
}) {
  const viewActive = useWorkspacePage()?.active ?? true;
  const [time, setTime] = useWorkspaceViewState("video.time", initialTime);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useWorkspaceViewState("video.muted", false);
  const outputIdentity = JSON.stringify([taskId, versionId, montage.output_url]);
  const [outputState, setOutputState] = useState<{ identity: string; state: "loading" | "ready" | "error" }>({ identity: outputIdentity, state: "loading" });
  const playbackState = outputState.identity === outputIdentity ? outputState.state : "loading";
  const [outputRetry, setOutputRetry] = useState(0);
  const storyboardId = useId();
  useEffect(() => { setPlaying(false); }, [outputIdentity]);
  const aspectRatio = montage.aspect_ratio ?? planAspectRatio;
  const previewRatio = aspectRatio === "16:9" ? 16 / 9 : aspectRatio === "1:1" ? 1 : 9 / 16;
  const canvasFitMode = montage.clips.length > 0 && montage.clips.every(clip => clip.fit_mode === "fit")
    ? "fit"
    : montage.clips.length > 0 && montage.clips.every(clip => clip.fit_mode === "fill")
      ? "fill"
      : "";
  const playerViewport = useRef<HTMLDivElement>(null);
  const [playerSize, setPlayerSize] = useState<{ width: number; height: number } | null>(null);
  const selected = montage.clips.find(clip => clip.id === selectedId);
  useEffect(() => {
    if (!viewActive) return;
    const viewport = playerViewport.current;
    if (!viewport) return;
    const resize = () => {
      const { width, height } = viewport.getBoundingClientRect();
      const fittedWidth = Math.min(width, height * previewRatio);
      const next = { width: fittedWidth, height: fittedWidth / previewRatio };
      setPlayerSize(previous => previous && Math.abs(previous.width - next.width) < .5 && Math.abs(previous.height - next.height) < .5 ? previous : next);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(viewport);
    resize();
    return () => observer.disconnect();
  }, [viewActive, previewRatio]);
  function position(video: HTMLVideoElement) { setTime(video.currentTime); onPosition(video); }
  function seek(value: number) { if (videoRef.current) { videoRef.current.currentTime = value; position(videoRef.current); } }
  function outputEvent(video: HTMLVideoElement, state: "loading" | "ready" | "error") {
    if (video === videoRef.current) setOutputState({ identity: outputIdentity, state });
  }
  const subtitleTrack = (
<div className={ui.track}><span>字幕</span><div className={ui.subtitleLane}>{(subtitleTracks.find(track => track.language === "zh-CN" && track.status === "valid" && track.style.enabled !== false) ?? subtitleTracks.find(track => track.status === "valid" && track.style.enabled !== false))?.cues.map(cue => { const clip = montage.clips.find(item => cue.start_seconds >= item.timeline_start_seconds && cue.start_seconds < item.timeline_end_seconds); return clip ? <button title={cue.text} key={cue.id} type="button" style={{ left: `${cue.start_seconds / montage.duration_seconds * 100}%`, width: `${(cue.end_seconds - cue.start_seconds) / montage.duration_seconds * 100}%` }} aria-label={`定位字幕：${cue.text}`} aria-pressed={clip.id === selectedId} onClick={() => { onSelect(clip.id); seek(cue.start_seconds); }}>{cue.text}</button> : null; })}</div></div>
  );
  if (!viewActive) return null;
  return <div className={ui.stage}>
    <div className={ui.previewToolbar}><strong>{montage.output_url ? "成片预览" : "未出片"}</strong><span>{aspectRatio} · {editorTime(montage.duration_seconds)}</span>{montage.low_confidence_clip_count > 0 ? <span className={ui.warning}>{montage.low_confidence_clip_count} 处待看</span> : null}<div className={ui.previewActions}>{onPreview ? <CanvasControls ratio={aspectRatio} fitMode={canvasFitMode} versionId={versionId} editError={editError} busy={busy} onPreview={onPreview} onDraftChange={onDraftChange} /> : null}{renderAction}</div></div>
    <div className={ui.playerViewport} ref={playerViewport}>
    <div className={ui.player} data-editor-player data-editor-aspect-ratio={aspectRatio} style={{ aspectRatio: previewRatio, ...(playerSize ?? {}) }}>
      {montage.output_url ? <WorkspaceVideo key={`${outputIdentity}:${outputRetry}`} aria-label="剪辑成片预览" ref={videoRef} playsInline preload="metadata" src={apiUrl(`${montage.output_url}?version=${encodeURIComponent(versionId ?? "")}`)} muted={muted}
        onLoadedMetadata={event => { if (event.currentTarget === videoRef.current) event.currentTarget.currentTime = initialTime; }}
        onLoadedData={event => { if (event.currentTarget.readyState >= 2) outputEvent(event.currentTarget, "ready"); }} onCanPlay={event => outputEvent(event.currentTarget, "ready")}
        onTimeUpdate={event => { if (event.currentTarget === videoRef.current) position(event.currentTarget); }} onSeeked={event => { if (event.currentTarget === videoRef.current) position(event.currentTarget); }}
        onPlay={event => { if (event.currentTarget === videoRef.current) setPlaying(true); }} onPause={event => { if (event.currentTarget === videoRef.current) { setPlaying(false); position(event.currentTarget); } }}
        onEnded={event => { if (event.currentTarget === videoRef.current) setPlaying(false); }} onError={event => outputEvent(event.currentTarget, "error")} />
        : selected ? <ClipMedia key={clipMediaKey(selected, taskId)} clip={selected} taskId={taskId} preview />
          : <p>先选镜头</p>}
      {montage.output_url ? <span className={ui.playerBadge}>成片画面</span> : null}
      {montage.output_url && playbackState !== "ready" ? <div className={mediaUi.playerState} role={playbackState === "error" ? "alert" : "status"}>
        <span>{playbackState === "error" ? "成片暂时无法播放" : "正在读取成片…"}</span>
        {playbackState === "error" ? <button className={ui.button} type="button" onClick={() => { setOutputState({ identity: outputIdentity, state: "loading" }); setPlaying(false); setOutputRetry(value => value + 1); }}>重新读取成片</button> : null}
      </div> : null}
    </div>
    </div>
    {montage.output_url ? <div className={ui.transport} data-editor-transport>
      <button className={ui.iconButton} aria-label={playing && playbackState === "ready" ? "暂停成片" : "播放成片"} onClick={() => { const video = videoRef.current; if (!video) return; if (video.paused) void video.play().catch(() => outputEvent(video, "error")); else video.pause(); }} type="button">{playing && playbackState === "ready" ? <Pause size={17} /> : <Play size={17} />}</button>
      <button className={ui.iconButton} aria-label={muted ? "取消静音" : "静音"} onClick={() => setMuted(!muted)} type="button">{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>
      <input aria-label="成片播放位置" type="range" min={0} max={montage.duration_seconds} step={0.01} value={Math.min(time, montage.duration_seconds)} onChange={event => seek(Number(event.target.value))} />
      <time>{editorTime(time)} / {editorTime(montage.duration_seconds)}</time>
      <button className={ui.iconButton} aria-label="全屏预览" onClick={() => { void videoRef.current?.requestFullscreen(); }} type="button"><Maximize size={16} /></button>
    </div> : null}
    <div className={ui.storyboard} data-workspace-scroll="storyboard" aria-label="镜头故事板">{montage.clips.map((clip, index) => <button key={clip.id} type="button" aria-label={`选择镜头 ${index + 1}`} aria-describedby={`${storyboardId}-thumbnail-${index}`} aria-pressed={selectedId === clip.id} className={ui.shot} onClick={() => onSelect(clip.id)}>
      <span className={ui.shotImage}><ClipThumbnail clip={clip} taskId={taskId} statusId={`${storyboardId}-thumbnail-${index}`} />{clip.locked ? <Lock size={13} /> : clip.needs_confirmation ? <span className={ui.reviewDot} title="待确认" /> : null}</span>
      <span className={ui.shotLabel}><strong>镜头 {String(index + 1).padStart(2, "0")}</strong><small>{editorTime(clip.timeline_start_seconds)}–{editorTime(clip.timeline_end_seconds)}</small></span>
    </button>)}</div>
    <div className={ui.tracks} aria-label="配音与字幕时间范围">
    {onSelectOverlay && montage.graphic_overlays?.length ? <div className={ui.overlayList} aria-label="图文叠加层">{montage.graphic_overlays.map((item,index)=><button className={ui.button} type="button" key={item.id} disabled={busy} aria-pressed={selectedOverlayId===item.id} onClick={()=>onSelectOverlay(item.id)}>{item.kind==="image"?"图片":item.kind==="arrow"?"箭头":"标题"}{index+1} · {item.kind==="image"||item.kind==="arrow"?item.name:item.text}</button>)}</div> : null}
      <div className={ui.ruler}><span>时间</span><div>{Array.from({ length: 5 }, (_, i) => <time key={i}>{editorTime(montage.duration_seconds * i / 4)}</time>)}</div></div>
      <div className={ui.track}><span>画面</span><div className={ui.videoLane}>{montage.clips.map((clip,index)=><button key={clip.id} type="button" className={ui.videoTimelineClip} style={{left:`${clip.timeline_start_seconds/montage.duration_seconds*100}%`,width:`${(clip.timeline_end_seconds-clip.timeline_start_seconds)/montage.duration_seconds*100}%`}} aria-label={`定位镜头 ${index+1}：${editorTime(clip.timeline_start_seconds)}–${editorTime(clip.timeline_end_seconds)}`} aria-pressed={clip.id===selectedId} onClick={()=>{onSelect(clip.id);seek(clip.timeline_start_seconds);}}>{index+1}</button>)}</div></div>
      {audioTracks ? audioTracks(subtitleTrack) : <><NarrationWaveformTrack taskId={taskId} narrationCacheKey={narrationCacheKey} clips={montage.clips} cues={montage.cues} selectedId={selectedId} onSelect={onSelect} />{subtitleTrack}</>}

    </div>
  </div>;
}

export function ClipTimelineAudioPreview({ taskId, versionId, start, end, busy }: {
  taskId: string; versionId: string | null; start: number; end: number; busy: boolean;
}) {
  const context = JSON.stringify([taskId, versionId, start, end]);
  const currentContext = useRef({ value: context, revision: 0 });
  if (currentContext.current.value !== context) currentContext.current = { value: context, revision: currentContext.current.revision + 1 };
  const [audio, setAudio] = useState<{ context: string; url: string } | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<{ context: string; message: string } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [job, setJob] = useState<{ context: string; value: TaskMediaJob } | null>(null);
  useEffect(() => { setWorking(false); return () => controllerRef.current?.abort(); }, [context]);
  async function listen() {
    const snapshot = currentContext.current;
    const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    setWorking(true); setError(null); setAudio(null); setJob(null);
    try {
      const response = await listenCreationTaskAudio(taskId, { base_version_id: versionId,
        start_seconds: start, end_seconds: end, solo_track_ids: [] });
      const result = await resolveCreationTaskMediaResponse(taskId, response, "audio_listen", {
        signal: controller.signal, baseVersionId: versionId,
        onUpdate: value => { if (!controller.signal.aborted && currentContext.current === snapshot) setJob({ context, value }); },
      });
      if (!controller.signal.aborted && currentContext.current === snapshot) setAudio({ context, url: result.audio_url });
    } catch (cause) {
      if (!controller.signal.aborted && currentContext.current === snapshot && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError({ context, message: cause instanceof Error ? cause.message : "镜头混音试听失败" });
    } finally { if (controllerRef.current === controller && !controller.signal.aborted) setWorking(false); }
  }
  async function cancelListen() {
    if (!job || job.context !== context) return;
    const snapshot = currentContext.current;
    try {
      const result = await cancelCreationTaskMediaJob(taskId, job.value.job_id);
      if (currentContext.current !== snapshot) return;
      setJob({ context, value: result });
      if (result.status === "cancelled") { controllerRef.current?.abort(); setWorking(false); }
    } catch (cause) { if (currentContext.current === snapshot) setError({ context, message: cause instanceof Error ? cause.message : "试听取消未完成" }); }
  }
  return <>
    {audio?.context === context ? <WorkspaceAudio aria-label="当前镜头混音" controls preload="metadata" src={apiUrl(audio.url)} />
      : <button className={ui.button} type="button" disabled={busy || working} onClick={() => void listen()}>{working ? "生成试听…" : "试听声音"}</button>}
    {job?.context === context && job.value.status !== "succeeded" ? <p role="status">{taskMediaJobLabel(job.value)}{["queued", "running", "interrupted"].includes(job.value.status) ? <button className={ui.button} type="button" onClick={() => void cancelListen()}>取消试听</button> : null}</p> : null}
    {error?.context === context ? <p role="alert">{error.message}</p> : null}
  </>;
}

export function VideoClipQuickEditor({ clip, index, busy, draft, onDraftStateChange, onDraftChange, onPreview, onOpenDetails }: {
  clip: CreationTaskMontageClip; index: number; busy: boolean; draft: VideoClipDraft;
  onDraftStateChange: (draft: VideoClipDraft) => void; onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void; onOpenDetails: () => void;
}) {
  const [tab, setTab] = useWorkspaceViewState<"trim" | "frame" | "replace">("video.tab", "trim");
  const latestDraft = useRef(draft);
  const lastApplied = useRef<string | null>(null);
  useEffect(() => { if (!busy) lastApplied.current = null; }, [busy]);
  latestDraft.current = draft;
  const trimChanged = draft.start !== clip.source_start_seconds;
  const frameChanged = draft.mode !== (clip.fit_mode ?? "fill") || draft.x !== (clip.crop_x ?? .5) || draft.y !== (clip.crop_y ?? .5);
  function update(next: VideoClipDraft) { latestDraft.current = next; lastApplied.current = null; onDraftStateChange(next); onDraftChange(); }
  function applyTrim() {
    const next = latestDraft.current;
    const submission = { operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: next.start } as const;
    const key = JSON.stringify(submission);
    if (!busy && !clip.locked && Number.isFinite(next.start) && next.start >= 0 && next.start !== clip.source_start_seconds && lastApplied.current !== key) {
      lastApplied.current = key;
      onPreview(submission);
    }
  }
  function applyFrame() {
    const next = latestDraft.current;
    const validCrop = Number.isFinite(next.x) && Number.isFinite(next.y) && next.x >= 0 && next.x <= 1 && next.y >= 0 && next.y <= 1;
    const changed = next.mode !== (clip.fit_mode ?? "fill") || next.x !== (clip.crop_x ?? .5) || next.y !== (clip.crop_y ?? .5);
    const submission = { operation_type: "ChangeCrop", clip_id: clip.id, fit_mode: next.mode, crop_x: next.x, crop_y: next.y } as const;
    const key = JSON.stringify(submission);
    if (!busy && !clip.locked && validCrop && changed && lastApplied.current !== key) {
      lastApplied.current = key;
      onPreview(submission);
    }
  }
  return <div className={quickUi.editor} aria-label={`镜头 ${index + 1} 快速编辑`}>
    <div className={quickUi.summary}><strong>镜头 {String(index + 1).padStart(2, "0")}</strong><span>成片 {editorTime(clip.timeline_start_seconds)}–{editorTime(clip.timeline_end_seconds)}</span></div>
    <nav className={quickUi.tabs} aria-label="快速编辑类别">
      <button aria-pressed={tab === "trim"} onClick={() => setTab("trim")} type="button">裁剪</button>
      <button aria-pressed={tab === "frame"} onClick={() => setTab("frame")} type="button">画面</button>
      <button aria-pressed={tab === "replace"} onClick={() => setTab("replace")} type="button">换素材</button>
    </nav>
    <div className={quickUi.body}>
      {tab === "trim" ? <section className={quickUi.controlGroup}>
        <h3>裁剪源画面</h3>
        <p>调整这段镜头从源素材的哪里开始，成片中的位置和时长保持不变。</p>
        <label>起点（秒）<input aria-label={`镜头 ${index + 1} 快速裁剪起点`} type="number" min={0} step={.1} value={Number.isFinite(draft.start) ? draft.start : ""} disabled={busy || clip.locked} onChange={event => update({ ...latestDraft.current, start: event.target.value === "" ? Number.NaN : Number(event.target.value) })} onBlur={applyTrim} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); applyTrim(); } }} /></label>
        <div className={quickUi.valueRow}><span>当前源画面</span><output>{Number.isFinite(draft.start) ? `${editorTime(draft.start)}–${editorTime(draft.start + clip.source_end_seconds - clip.source_start_seconds)}` : "请输入有效秒数"}</output></div>
      </section> : tab === "frame" ? <section className={quickUi.controlGroup}>
        <h3>画面适配</h3>
        <p>填满画面时可以调整裁切位置；完整显示会保留源画面的全部内容。</p>
        <label>显示方式<CardSelect label={`镜头 ${index + 1} 快速画面方式`} value={draft.mode} disabled={busy || clip.locked} onChange={value => { update({ ...latestDraft.current, mode: value as "fill" | "fit" }); applyFrame(); }} options={[{value:"fill",label:"填满画面"},{value:"fit",label:"完整显示"}]} /></label>
        {draft.mode === "fill" ? <div className={quickUi.cropControls}>
          <label>水平位置 <output>{Math.round(draft.x * 100)}%</output><input aria-label={`镜头 ${index + 1} 快速裁切水平位置`} type="range" min={0} max={1} step={.05} value={draft.x} disabled={busy || clip.locked} onChange={event => update({ ...latestDraft.current, x: Number(event.target.value) })} onPointerUp={applyFrame} onKeyUp={applyFrame} onBlur={applyFrame} /></label>
          <label>垂直位置 <output>{Math.round(draft.y * 100)}%</output><input aria-label={`镜头 ${index + 1} 快速裁切垂直位置`} type="range" min={0} max={1} step={.05} value={draft.y} disabled={busy || clip.locked} onChange={event => update({ ...latestDraft.current, y: Number(event.target.value) })} onPointerUp={applyFrame} onKeyUp={applyFrame} onBlur={applyFrame} /></label>
        </div> : null}
      </section> : <section className={quickUi.controlGroup}>
        <h3>换用其他素材</h3>
        <p>在镜头详情里选择已审核的候选。先预览替换结果，再决定是否应用。</p>
      </section>}
    </div>
    <footer className={quickUi.footer}>
      <small>{clip.locked ? "镜头已锁定" : tab === "trim" && trimChanged || tab === "frame" && frameChanged ? "完成输入或拖动后直接应用到草稿" : tab === "replace" ? "需在详情选择候选" : "当前设置已保存"}</small>
      {tab === "replace" ? <button onClick={onOpenDetails} type="button">查看候选</button> : null}
    </footer>
  </div>;
}

export function VideoClipInspector({ clip, index, count, taskId, versionId, subtitleTracks, personRender, personRenderCapability, portraitAvailable, graphicOverlays, durationSeconds, allClipIds, hasOutput, busy, initialDraft, onDraftStateChange, onPreview, onDraftChange, onReplace, candidates, replacing, onAddSegment, inserting, onPerson, onAddOriginal, onVisualPreview, getPlayhead, onSelectOverlay }: {
  clip: CreationTaskMontageClip; index: number; count: number; taskId: string; versionId: string | null; subtitleTracks: CreationTaskSubtitleTrack[];
  personRender: CreationTaskPersonRender | undefined; personRenderCapability: CreationTaskPersonRenderCapability; portraitAvailable: boolean | undefined;
  graphicOverlays: GraphicOverlay[] | undefined; durationSeconds: number; allClipIds: string[]; hasOutput: boolean; busy: boolean;
  initialDraft?: VideoClipDraft; onDraftStateChange?: (draft: VideoClipDraft) => void;
  getPlayhead?: () => number | null;
  onSelectOverlay?: (id:string|null)=>void;
  onPreview: (input: CreationTaskExactEditInput) => void; onDraftChange: (changed: boolean) => void; onReplace: () => void;
  candidates: MontageClipCandidate[]; replacing: boolean; onAddSegment?: () => void; inserting?: boolean; onPerson: (command: string) => void; onAddOriginal?: () => void; onVisualPreview?: (input: import("@/lib/types").VisualEditInput) => void;
}) {
  const [tab, setTab] = useState("properties");
  const [start, setStart] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".start", initialDraft?.start ?? clip.source_start_seconds);
  const [transitionSeconds,setTransitionSeconds]=useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".transitionSeconds", clip.transition_in_seconds ?? 0);
  const [linkedRate, setLinkedRate] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".linkedRate", 1);
  const [rate, setRate] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".rate", Number(((clip.source_end_seconds - clip.source_start_seconds) / (clip.timeline_end_seconds - clip.timeline_start_seconds)).toFixed(3)));
  const [splitAt, setSplitAt] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".splitAt", Number(((clip.timeline_start_seconds + clip.timeline_end_seconds) / 2).toFixed(3)));
  const [mode, setMode] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".mode", initialDraft?.mode ?? clip.fit_mode ?? "fill");
  const [x, setX] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".x", initialDraft?.x ?? clip.crop_x ?? .5);
  const [y, setY] = useWorkspaceViewState(`clip:${clip.id}:${versionId}`+".y", initialDraft?.y ?? clip.crop_y ?? .5);

  const captionTrack = subtitleTracks.find(item => item.language === "zh-CN" && item.status === "valid")
    ?? subtitleTracks.find(item => item.status === "valid");
  const captions = captionTrack?.cues.filter(item => item.start_seconds < clip.timeline_end_seconds && item.end_seconds > clip.timeline_start_seconds) ?? [];
  const audioTimingDirty = start !== clip.source_start_seconds || linkedRate !== 1 || rate !== Number(((clip.source_end_seconds - clip.source_start_seconds) / (clip.timeline_end_seconds - clip.timeline_start_seconds)).toFixed(3));
  const framingChanged = mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5);
  function applyTrim(value = start) {
    if (!busy && !clip.locked && Number.isFinite(value) && value >= 0 && value !== clip.source_start_seconds) {
      onPreview({ operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: value });
    }
  }
  function applyFrame(nextMode = mode, nextX = x, nextY = y) {
    const valid = Number.isFinite(nextX) && Number.isFinite(nextY) && nextX >= 0 && nextX <= 1 && nextY >= 0 && nextY <= 1;
    const changed = nextMode !== (clip.fit_mode ?? "fill") || nextX !== (clip.crop_x ?? .5) || nextY !== (clip.crop_y ?? .5);
    if (!busy && !clip.locked && valid && changed) onPreview({ operation_type: "ChangeCrop", clip_id: clip.id, fit_mode: nextMode, crop_x: nextX, crop_y: nextY });
  }
  function applyRate(value: number, linked = false) {
    if (!busy && !clip.locked && Number.isFinite(value) && value >= .25 && value <= 4 && (linked ? value !== 1 : value !== Number(((clip.source_end_seconds - clip.source_start_seconds) / (clip.timeline_end_seconds - clip.timeline_start_seconds)).toFixed(3)))) {
      onPreview(linked
        ? { operation_type: "ChangeLinkedSpeed", clip_id: clip.id, playback_rate: value }
        : { operation_type: "ChangeClipSpeed", clip_id: clip.id, playback_rate: value });
    }
  }
  return <div className={ui.inspector} aria-label="已选镜头详情">
    <ClipMedia key={clipMediaKey(clip, taskId)} clip={clip} taskId={taskId} preview bounded className={ui.inspectorImage} />
    <nav className={ui.inspectorTabs} aria-label="镜头详情视图"><button aria-pressed={tab === "properties"} onClick={() => setTab("properties")} type="button">属性</button><button aria-pressed={tab === "source"} onClick={() => setTab("source")} type="button">来源</button></nav>
    {tab === "source" ? <div className={ui.source}><strong>{clip.person_render_id ? "当前人物口型" : clip.source_reference.split("/").pop()}</strong><p>{editorTime(clip.source_start_seconds)} – {editorTime(clip.source_end_seconds)}</p>{clip.person_render_id ? <span>根据当前任务配音制作</span> : <a href="/library">查看素材</a>}<h3>选片依据</h3><ul>{clip.match_reasons?.map(reason => <li key={reason}>{reason}</li>)}</ul><span>匹配程度 {clip.match_score.toFixed(2)}</span></div> : <>
      <dl className={ui.properties}><div><dt>画面用途</dt><dd>{clip.person_render_id ? "人物口型" : clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{editorTime(clip.timeline_start_seconds)} – {editorTime(clip.timeline_end_seconds)}</dd></div></dl>
      <section className={ui.propertySection}><h3>裁剪</h3><div className={ui.twoFields}><label>起点（秒）<input aria-label={`镜头 ${index + 1} 裁剪起点`} type="number" min={0} step={.1} value={Number.isFinite(start) ? start : ""} disabled={busy || clip.locked} onChange={event => { const value = event.target.value === "" ? Number.NaN : Number(event.target.value); onDraftChange(value !== clip.source_start_seconds || framingChanged); setStart(value); onDraftStateChange?.({ start: value, mode, x, y }); }} onBlur={() => applyTrim()} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); applyTrim(); } }} /></label><label>终点（秒）<output>{Number.isFinite(start) ? (start + clip.source_end_seconds - clip.source_start_seconds).toFixed(1) : "—"}</output></label></div></section>
      <details className={ui.advancedGroup}><summary>更多剪辑</summary><div className={ui.advancedContent}>
      {clip.role !== "a_roll" && !clip.person_render_id ? <section className={ui.propertySection}><h3>画面速度</h3><label className={ui.field}>倍速<input aria-label="画面倍速" type="number" min={.25} max={4} step={.25} value={Number.isFinite(rate) ? rate : ""} disabled={busy || clip.locked} onChange={event => { const value = event.target.value === "" ? Number.NaN : Number(event.target.value); setRate(value); onDraftChange(true); }} onBlur={() => applyRate(rate)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); applyRate(rate); } }} /></label><p>保持成片时长、声音和字幕；加速需要更多源素材。</p></section> : null}
      <section className={ui.propertySection}><h3>一起变速</h3><label className={ui.field}>倍速<input aria-label="关联倍速" type="number" min={.25} max={4} step={.25} value={Number.isFinite(linkedRate) ? linkedRate : ""} disabled={busy || clip.locked} onChange={event => { const value = event.target.value === "" ? Number.NaN : Number(event.target.value); setLinkedRate(value); onDraftChange(true); }} onBlur={() => applyRate(linkedRate, true)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); applyRate(linkedRate, true); } }} /></label><p>声音和字幕一起变速，改变成片时长；后续内容同步移动。</p></section>
      <section className={ui.propertySection}><h3>复制或删</h3><div className={ui.actions}><button className={ui.button} type="button" disabled={busy || clip.locked} onClick={() => onPreview({ operation_type: "DuplicateClip", clip_id: clip.id })}>复制镜头</button><button className={ui.button} type="button" disabled={busy || clip.locked || allClipIds.length < 2} onClick={() => onPreview({ operation_type: "DeleteClip", clip_id: clip.id })}>删除镜头…</button></div><p>同时处理这段时间内的声音和字幕，后续内容同步移动。</p></section>
      <section className={ui.propertySection}><h3>切开镜头</h3><label className={ui.field}>切开位置<input aria-label="镜头分割位置" type="number" min={clip.timeline_start_seconds + .04} max={clip.timeline_end_seconds - .04} step={.04} value={splitAt} disabled={busy || clip.locked} onChange={event => { setSplitAt(Number(event.target.value)); onDraftChange(true); }} /></label><div className={ui.actions}>{getPlayhead ? <button className={ui.button} type="button" disabled={busy || clip.locked || !hasOutput} onClick={() => { const time = getPlayhead(); if (time !== null) { setSplitAt(Number(time.toFixed(3))); onDraftChange(true); } }}>取播放点</button> : null}<button className={ui.button} type="button" disabled={busy || clip.locked || !Number.isFinite(splitAt) || splitAt - clip.timeline_start_seconds < .04 || clip.timeline_end_seconds - splitAt < .04} onClick={() => onPreview({ operation_type: "SplitClip", clip_id: clip.id, split_time_seconds: splitAt })}>切开镜头</button></div><p>保留总时长、配音和字幕。</p></section>
      </div></details>
      <section className={ui.propertySection}><h3>画面适配</h3><label className={ui.field}>显示方式<AppSelect aria-label="画面显示方式" value={mode} disabled={busy || clip.locked} onValueChange={newValue => { const nextMode = newValue as "fill" | "fit"; onDraftChange(start !== clip.source_start_seconds || nextMode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setMode(nextMode); onDraftStateChange?.({ start, mode: nextMode, x, y }); applyFrame(nextMode); }}><AppSelectOption value="fill">填满画面</AppSelectOption><AppSelectOption value="fit">完整显示</AppSelectOption></AppSelect></label>{mode === "fill" ? <details><summary>裁切位置</summary><div className={ui.cropFields}><label>水平位置 <span>{Math.round(x * 100)}%</span><input aria-label="裁切水平位置" type="range" min={0} max={1} step={.05} value={x} disabled={busy || clip.locked} onChange={event => { const nextX = Number(event.target.value); onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || nextX !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setX(nextX); onDraftStateChange?.({ start, mode, x: nextX, y }); }} onPointerUp={event => applyFrame(mode, Number(event.currentTarget.value), y)} onKeyUp={event => applyFrame(mode, Number(event.currentTarget.value), y)} /></label><label>垂直位置 <span>{Math.round(y * 100)}%</span><input aria-label="裁切垂直位置" type="range" min={0} max={1} step={.05} value={y} disabled={busy || clip.locked} onChange={event => { const nextY = Number(event.target.value); onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || nextY !== (clip.crop_y ?? .5)); setY(nextY); onDraftStateChange?.({ start, mode, x, y: nextY }); }} onPointerUp={event => applyFrame(mode, x, Number(event.currentTarget.value))} onKeyUp={event => applyFrame(mode, x, Number(event.currentTarget.value))} /></label></div></details> : null}</section>
      <section className={ui.propertySection}><div className={ui.actionRow}><span>锁定镜头</span><button className={ui.iconButton} aria-label={clip.locked ? "解锁镜头" : "锁定镜头"} aria-pressed={clip.locked} disabled={busy} onClick={() => onPreview({ operation_type: "SetClipLock", clip_id: clip.id, locked: !clip.locked })} type="button">{clip.locked ? <Lock size={16} /> : <Unlock size={16} />}</button></div><div className={ui.actions}><button className={ui.button} aria-label="向前移动" disabled={busy || clip.locked || index === 0} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index - 1 })} type="button"><ArrowLeft size={14} />前移</button><button className={ui.button} aria-label="向后移动" disabled={busy || clip.locked || index === count - 1} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index + 1 })} type="button">后移<ArrowRight size={14} /></button><button className={ui.button} disabled={busy || clip.locked} onClick={onReplace} type="button"><Replace size={14} />换素材</button>{onAddSegment ? <button className={ui.button} disabled={busy} onClick={onAddSegment} type="button"><Plus size={14} />添加片段</button> : null}</div>
      {onAddSegment && inserting ? candidates.length ? <label className={ui.field}>插在当前镜头之后<AppSelect aria-label="插在当前镜头之后" disabled={busy} value="" onValueChange={newValue => { const candidate = candidates.find(item => `${item.asset_id}:${item.segment_id}` === newValue); if (candidate) onPreview({ operation_type: "InsertClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><AppSelectOption value="" disabled>选择候选，应用到草稿</AppSelectOption>{candidates.map(item => <AppSelectOption key={`${item.asset_id}:${item.segment_id}`} value={`${item.asset_id}:${item.segment_id}`}>{item.file_name} · {item.description}</AppSelectOption>)}</AppSelect><small>插在当前镜头之后，时长继承该片段本身的时长。</small></label> : <p role="status">没有可插入的已审核镜头，请补充素材。</p> : null}
      {replacing ? candidates.length ? <label className={ui.field}>替换为已审核镜头<AppSelect aria-label="替换为已审核镜头" disabled={busy} value="" onValueChange={newValue => { const candidate = candidates.find(item => `${item.asset_id}:${item.segment_id}` === newValue); if (candidate) onPreview({ operation_type: "ReplaceClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><AppSelectOption value="" disabled>选择候选，应用到草稿</AppSelectOption>{candidates.map(item => <AppSelectOption key={`${item.asset_id}:${item.segment_id}`} value={`${item.asset_id}:${item.segment_id}`}>{item.file_name} · {item.description}</AppSelectOption>)}</AppSelect></label> : <p role="status">没有满足时长的其他可用镜头，请补充素材。</p> : null}
      {clip.needs_confirmation ? <button className={ui.button} disabled={busy} onClick={() => onPreview({ operation_type: "ConfirmClip", clip_id: clip.id })} type="button"><Check size={14} />确认镜头</button> : null}</section>
      {index>0 ? <details className={ui.propertySection}><summary>镜头过渡</summary>
        <label className={ui.field}>过渡秒数<input aria-label="叠化时长（秒）" type="number" min="0" max="2" step="0.1" value={Number.isFinite(transitionSeconds) ? transitionSeconds : ""} disabled={busy||clip.locked} onChange={event=>{onDraftChange(true);setTransitionSeconds(event.target.value === "" ? Number.NaN : Number(event.target.value));}} onBlur={() => { if (Number.isFinite(transitionSeconds) && transitionSeconds >= 0 && transitionSeconds <= 2 && !busy && !clip.locked && transitionSeconds !== (clip.transition_in_seconds ?? 0)) onPreview({operation_type:"SetClipTransition",clip_id:clip.id,transition_seconds:transitionSeconds}); }} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); if (Number.isFinite(transitionSeconds) && transitionSeconds >= 0 && transitionSeconds <= 2 && !busy && !clip.locked && transitionSeconds !== (clip.transition_in_seconds ?? 0)) onPreview({operation_type:"SetClipTransition",clip_id:clip.id,transition_seconds:transitionSeconds}); } }} /></label>
        <p>0 为硬切；从切点开始混合两段画面，需要前镜头的结尾素材余量。声音与字幕不移动。</p>
      </details>:null}
      <GraphicOverlayEditor onSelect={onSelectOverlay} taskId={taskId} items={graphicOverlays ?? []} duration={durationSeconds} busy={busy} onPreview={onPreview} onDraftChange={()=>onDraftChange(true)}/>
      {onVisualPreview ? <VisualEffectsInspector portraitAvailable={portraitAvailable} clip={clip} allClipIds={allClipIds} versionId={versionId} busy={busy} onPreview={onVisualPreview} onDraftChange={()=>onDraftChange(true)} />:null}
      {onAddOriginal && !clip.person_render_id ? <button className={ui.button} type="button" disabled={busy || clip.locked || audioTimingDirty} title={audioTimingDirty ? "请先确认裁切或变速修改" : undefined} onClick={onAddOriginal}>试听原声</button> : null}
      <section className={ui.propertySection}><h3>这段声音</h3>{captions.length ? <p>{captions.map(cue => cue.text).join(" ")}</p> : null}<ClipTimelineAudioPreview key={`${versionId}:${clip.id}`} taskId={taskId} versionId={versionId} start={clip.timeline_start_seconds} end={clip.timeline_end_seconds} busy={busy} /></section>
      <details className={ui.propertySection}><summary>人物口型</summary>{personRender ? <dl className={ui.properties}><div><dt>人物版本</dt><dd>{personRender.person_profile_id} · {personRender.person_version}</dd></div><div><dt>声音版本</dt><dd>{personRender.voice_profile_id} · {personRender.voice_version}</dd></div><div><dt>当前模型</dt><dd>{personRender.model_id}</dd></div></dl> : null}{!personRenderCapability.available ? <p aria-label="人物口型不可用原因">{personRenderCapability.message} {personRenderCapability.repair_action}</p> : null}<button className={ui.button} disabled={busy || clip.locked || !personRenderCapability.available} onClick={() => onPerson(clip.person_render_id ? "这里只重做口型，其他都不要动" : "这一段换成当前人物出镜")} type="button">{clip.person_render_id ? "重新对口型" : "换成当前人物出镜"}</button></details>
    </>}
  </div>;
}
