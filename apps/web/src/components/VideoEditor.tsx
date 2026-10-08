"use client";

import { GraphicOverlayEditor } from "./GraphicOverlayEditor";
import { VisualEffectsInspector } from "./VisualEffectsInspector";

import { useState, type RefObject, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Check, Lock, Unlock, Maximize, Pause, Play, Plus, Replace, Volume2, VolumeX } from "lucide-react";
import { apiUrl, listenCreationTaskAudio } from "@/lib/api";
import type { CreationTaskMontageClip, CreationTaskExactEditInput, CreationTaskWorkspace, MontageClipCandidate } from "@/lib/types";
import ui from "./VideoEditor.module.css";
import quickUi from "./VideoClipQuickEditor.module.css";
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

export function ClipThumbnail({ clip, taskId, className }: { clip: CreationTaskMontageClip; taskId: string; className?: string }) {
  return <video className={className} aria-hidden muted playsInline preload="metadata" src={sourceUrl(clip, taskId)} onLoadedMetadata={event => { event.currentTarget.currentTime = clip.source_start_seconds; }} />;
}

function CanvasControls({ ratio, busy, onPreview, onDraftChange }: {ratio:"9:16"|"16:9"|"1:1";busy:boolean;onPreview:(input:CreationTaskExactEditInput)=>void;onDraftChange:()=>void}) {
  const [draft,setDraft]=useState(ratio);
  const [fit,setFit]=useState<""|"fill"|"fit">("");
  const [open,setOpen]=useState(false);
  return <><button className={ui.button} onClick={()=>setOpen(true)} type="button">画幅设置</button>{open ? <HomeCardDialog kind="config" title="成片画幅" onClose={()=>setOpen(false)}><div className={ui.canvasFields}>
    <label className={ui.field}>画幅<CardSelect label="成片画幅" disabled={busy} value={draft} onChange={value=>{onDraftChange();setDraft(value as typeof draft);}} options={[{value:"9:16",label:"9:16 竖屏"},{value:"16:9",label:"16:9 横屏"},{value:"1:1",label:"1:1 方形"}]} /></label>
    <label className={ui.field}>画面<CardSelect label="整片画面适配" disabled={busy} value={fit} onChange={value=>{onDraftChange();setFit(value as typeof fit);}} options={[{value:"",label:"各镜头原样"},{value:"fit",label:"完整画面"},{value:"fill",label:"填满画面"}]} /></label>
    <small>填满：可能裁切 · 完整：可能留边</small>
    <button className={ui.button} disabled={busy || (draft===ratio && !fit)} onClick={()=>onPreview({operation_type:"ChangeCanvas",aspect_ratio:draft,...(fit?{fit_mode:fit}:{})})} type="button">预览画幅</button></div>
  </HomeCardDialog> : null}</>;
}

export function VideoEditingStage({ workspace, selectedId, onSelect, onPosition, videoRef, initialTime, renderAction, audioTracks, busy = false, onPreview, onDraftChange = () => {}, selectedOverlayId, onSelectOverlay }:  {
  selectedOverlayId?: string|null; onSelectOverlay?: (id:string)=>void;
  workspace: CreationTaskWorkspace; selectedId: string | null; onSelect: (id: string) => void;
  onPosition: (video: HTMLVideoElement) => void; videoRef: RefObject<HTMLVideoElement | null>;
  initialTime: number; renderAction: ReactNode; audioTracks?: ReactNode; busy?: boolean; onDraftChange?:()=>void; onPreview?: (input:CreationTaskExactEditInput)=>void;
}) {
  const montage = workspace.montage!;
  const [time, setTime] = useState(initialTime);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [playbackError, setPlaybackError] = useState(false);
  const selected = montage.clips.find(clip => clip.id === selectedId);
  function position(video: HTMLVideoElement) { setTime(video.currentTime); onPosition(video); }
  function seek(value: number) { if (videoRef.current) { videoRef.current.currentTime = value; position(videoRef.current); } }
  return <div className={ui.stage}>
    <div className={ui.previewToolbar}><strong>{montage.output_url ? "成片预览" : "未出片"}</strong><span>{montage.aspect_ratio ?? "9:16"} · {editorTime(montage.duration_seconds)}</span>{montage.low_confidence_clip_count > 0 ? <span className={ui.warning}>{montage.low_confidence_clip_count} 处待看</span> : null}<div className={ui.previewActions}>{onPreview ? <CanvasControls key={workspace.task.current_timeline_version_id} ratio={montage.aspect_ratio ?? "9:16"} busy={busy} onPreview={onPreview} onDraftChange={onDraftChange} /> : null}{renderAction}</div></div>
    <div className={ui.player} data-editor-player data-editor-aspect-ratio={montage.aspect_ratio ?? "9:16"}>
      {montage.output_url ? <video key={workspace.task.current_timeline_version_id} aria-label="剪辑成片预览" ref={videoRef} playsInline preload="metadata" src={apiUrl(`${montage.output_url}?version=${encodeURIComponent(workspace.task.current_timeline_version_id ?? "")}`)} muted={muted}
        onLoadedMetadata={event => { event.currentTarget.currentTime = initialTime; setPlaybackError(false); }}
        onTimeUpdate={event => position(event.currentTarget)} onSeeked={event => position(event.currentTarget)}
        onPlay={() => setPlaying(true)} onPause={event => { setPlaying(false); position(event.currentTarget); }}
        onEnded={() => setPlaying(false)} onError={() => setPlaybackError(true)} />
        : selected ? <video aria-label="源素材预览" controls key={selected.id} playsInline preload="metadata" src={sourceUrl(selected, workspace.task.id)} onLoadedMetadata={event => { event.currentTarget.currentTime = selected.source_start_seconds; }} />
          : <p>先选镜头</p>}
      {!montage.output_url && selected ? <span className={ui.playerBadge}>原始画面</span> : null}
      {playbackError ? <p className={ui.playerError} role="alert">画面打不开，请重新出片。</p> : null}
    </div>
    {montage.output_url ? <div className={ui.transport} data-editor-transport>
      <button className={ui.iconButton} aria-label={playing ? "暂停成片" : "播放成片"} onClick={() => { const video = videoRef.current; if (!video) return; if (video.paused) void video.play().catch(() => setPlaybackError(true)); else video.pause(); }} type="button">{playing ? <Pause size={17} /> : <Play size={17} />}</button>
      <button className={ui.iconButton} aria-label={muted ? "取消静音" : "静音"} onClick={() => setMuted(!muted)} type="button">{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>
      <input aria-label="成片播放位置" type="range" min={0} max={montage.duration_seconds} step={0.01} value={Math.min(time, montage.duration_seconds)} onChange={event => seek(Number(event.target.value))} />
      <time>{editorTime(time)} / {editorTime(montage.duration_seconds)}</time>
      <button className={ui.iconButton} aria-label="全屏预览" onClick={() => { void videoRef.current?.requestFullscreen(); }} type="button"><Maximize size={16} /></button>
    </div> : null}
    {onSelectOverlay && montage.graphic_overlays?.length ? <div className={ui.overlayList} aria-label="图文叠加层">{montage.graphic_overlays.map((item,index)=><button className={ui.button} type="button" key={item.id} disabled={busy} aria-pressed={selectedOverlayId===item.id} onClick={()=>onSelectOverlay(item.id)}>{item.kind==="image"?"图片":item.kind==="arrow"?"箭头":"标题"}{index+1} · {item.kind==="image"||item.kind==="arrow"?item.name:item.text}</button>)}</div> : null}
    <div className={ui.storyboard} aria-label="镜头故事板">{montage.clips.map((clip, index) => <button key={clip.id} type="button" aria-label={`选择镜头 ${index + 1}`} aria-pressed={selectedId === clip.id} className={ui.shot} onClick={() => onSelect(clip.id)}>
      <span className={ui.shotImage}><ClipThumbnail clip={clip} taskId={workspace.task.id} />{clip.locked ? <Lock size={13} /> : clip.needs_confirmation ? <span className={ui.reviewDot} title="待确认" /> : null}</span>
      <span className={ui.shotLabel}><strong>镜头 {String(index + 1).padStart(2, "0")}</strong><small>{editorTime(clip.timeline_start_seconds)}–{editorTime(clip.timeline_end_seconds)}</small></span>
    </button>)}</div>
    <div className={ui.tracks} aria-label="配音与字幕时间范围">
      <div className={ui.ruler}><span>时间</span><div>{Array.from({ length: 5 }, (_, i) => <time key={i}>{editorTime(montage.duration_seconds * i / 4)}</time>)}</div></div>
      {audioTracks ?? <NarrationWaveformTrack workspace={workspace} selectedId={selectedId} onSelect={onSelect} />}
      <div className={ui.track}><span>字幕</span><div className={ui.subtitleLane}>{(workspace.subtitle_tracks.find(track => track.language === "zh-CN" && track.status === "valid" && track.style.enabled !== false) ?? workspace.subtitle_tracks.find(track => track.status === "valid" && track.style.enabled !== false))?.cues.map(cue => { const clip = montage.clips.find(item => cue.start_seconds >= item.timeline_start_seconds && cue.start_seconds < item.timeline_end_seconds); return clip ? <button title={cue.text} key={cue.id} type="button" style={{ left: `${cue.start_seconds / montage.duration_seconds * 100}%`, width: `${(cue.end_seconds - cue.start_seconds) / montage.duration_seconds * 100}%` }} aria-label={`定位字幕：${cue.text}`} aria-pressed={clip.id === selectedId} onClick={() => { onSelect(clip.id); seek(cue.start_seconds); }}>{cue.text}</button> : null; })}</div></div>
    </div>
  </div>;
}

export function ClipTimelineAudioPreview({ taskId, versionId, start, end, busy }: {
  taskId: string; versionId: string | null; start: number; end: number; busy: boolean;
}) {
  const [audio, setAudio] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function listen() {
    setWorking(true); setError(null);
    try {
      const result = await listenCreationTaskAudio(taskId, { base_version_id: versionId,
        start_seconds: start, end_seconds: end, solo_track_ids: [] });
      setAudio(result.audio_url);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "镜头混音试听失败"); }
    finally { setWorking(false); }
  }
  return <>
    {audio ? <audio aria-label="当前镜头混音" controls preload="metadata" src={apiUrl(audio)} />
      : <button className={ui.button} type="button" disabled={busy || working} onClick={() => void listen()}>{working ? "生成试听…" : "试听声音"}</button>}
    {error ? <p role="alert">{error}</p> : null}
  </>;
}

export function VideoClipQuickEditor({ clip, index, busy, draft, onDraftStateChange, onDraftChange, onPreview, onOpenDetails }: {
  clip: CreationTaskMontageClip; index: number; busy: boolean; draft: VideoClipDraft;
  onDraftStateChange: (draft: VideoClipDraft) => void; onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void; onOpenDetails: () => void;
}) {
  const [tab, setTab] = useState<"trim" | "frame" | "replace">("trim");
  const trimChanged = draft.start !== clip.source_start_seconds;
  const frameChanged = draft.mode !== (clip.fit_mode ?? "fill") || draft.x !== (clip.crop_x ?? .5) || draft.y !== (clip.crop_y ?? .5);
  function update(next: VideoClipDraft) { onDraftStateChange(next); onDraftChange(); }
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
        <label>起点（秒）<input aria-label={`镜头 ${index + 1} 快速裁剪起点`} type="number" min={0} step={.1} value={draft.start} disabled={busy || clip.locked} onChange={event => update({ ...draft, start: Number(event.target.value) })} /></label>
        <div className={quickUi.valueRow}><span>当前源画面</span><output>{editorTime(draft.start)}–{editorTime(draft.start + clip.timeline_end_seconds - clip.timeline_start_seconds)}</output></div>
      </section> : tab === "frame" ? <section className={quickUi.controlGroup}>
        <h3>画面适配</h3>
        <p>填满画面时可以调整裁切位置；完整显示会保留源画面的全部内容。</p>
        <label>显示方式<CardSelect label={`镜头 ${index + 1} 快速画面方式`} value={draft.mode} disabled={busy || clip.locked} onChange={value => update({ ...draft, mode: value as "fill" | "fit" })} options={[{value:"fill",label:"填满画面"},{value:"fit",label:"完整显示"}]} /></label>
        {draft.mode === "fill" ? <div className={quickUi.cropControls}>
          <label>水平位置 <output>{Math.round(draft.x * 100)}%</output><input aria-label={`镜头 ${index + 1} 快速裁切水平位置`} type="range" min={0} max={1} step={.05} value={draft.x} disabled={busy || clip.locked} onChange={event => update({ ...draft, x: Number(event.target.value) })} /></label>
          <label>垂直位置 <output>{Math.round(draft.y * 100)}%</output><input aria-label={`镜头 ${index + 1} 快速裁切垂直位置`} type="range" min={0} max={1} step={.05} value={draft.y} disabled={busy || clip.locked} onChange={event => update({ ...draft, y: Number(event.target.value) })} /></label>
        </div> : null}
      </section> : <section className={quickUi.controlGroup}>
        <h3>换用其他素材</h3>
        <p>在镜头详情里选择已审核的候选。先预览替换结果，再决定是否应用。</p>
      </section>}
    </div>
    <footer className={quickUi.footer}>
      <small>{clip.locked ? "镜头已锁定" : tab === "trim" && trimChanged || tab === "frame" && frameChanged ? "修改尚未应用" : tab === "replace" ? "需在详情选择候选" : "当前设置已保存"}</small>
      {tab === "replace" ? <button onClick={onOpenDetails} type="button">查看候选</button> :
        <button disabled={busy || clip.locked || (tab === "trim" ? !trimChanged || !Number.isFinite(draft.start) || draft.start < 0 : !frameChanged)} onClick={() => onPreview(tab === "trim"
          ? { operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: draft.start }
          : { operation_type: "ChangeCrop", clip_id: clip.id, fit_mode: draft.mode, crop_x: draft.x, crop_y: draft.y })} type="button">预览修改</button>}
    </footer>
  </div>;
}

export function VideoClipInspector({ clip, index, count, workspace, busy, initialDraft, onDraftStateChange, onPreview, onDraftChange, onReplace, candidates, replacing, onAddSegment, inserting, onPerson, onAddOriginal, onVisualPreview, getPlayhead, onSelectOverlay }: {
  clip: CreationTaskMontageClip; index: number; count: number; workspace: CreationTaskWorkspace; busy: boolean;
  initialDraft?: VideoClipDraft; onDraftStateChange?: (draft: VideoClipDraft) => void;
  getPlayhead?: () => number | null;
  onSelectOverlay?: (id:string|null)=>void;
  onPreview: (input: CreationTaskExactEditInput) => void; onDraftChange: (changed: boolean) => void; onReplace: () => void;
  candidates: MontageClipCandidate[]; replacing: boolean; onAddSegment?: () => void; inserting?: boolean; onPerson: (command: string) => void; onAddOriginal?: () => void; onVisualPreview?: (input: import("@/lib/types").VisualEditInput) => void;
}) {
  const [tab, setTab] = useState("properties");
  const [start, setStart] = useState(initialDraft?.start ?? clip.source_start_seconds);
  const [transitionSeconds,setTransitionSeconds]=useState(clip.transition_in_seconds ?? 0);
  const [linkedRate, setLinkedRate] = useState(1);
  const [rate, setRate] = useState(Number(((clip.source_end_seconds - clip.source_start_seconds) / (clip.timeline_end_seconds - clip.timeline_start_seconds)).toFixed(3)));
  const [splitAt, setSplitAt] = useState(Number(((clip.timeline_start_seconds + clip.timeline_end_seconds) / 2).toFixed(3)));
  const [mode, setMode] = useState(initialDraft?.mode ?? clip.fit_mode ?? "fill");
  const [x, setX] = useState(initialDraft?.x ?? clip.crop_x ?? .5);
  const [y, setY] = useState(initialDraft?.y ?? clip.crop_y ?? .5);
  const captionTrack = workspace.subtitle_tracks.find(item => item.language === "zh-CN" && item.status === "valid")
    ?? workspace.subtitle_tracks.find(item => item.status === "valid");
  const captions = captionTrack?.cues.filter(item => item.start_seconds < clip.timeline_end_seconds && item.end_seconds > clip.timeline_start_seconds) ?? [];
  const personRender = workspace.person_renders.find(item => item.id === clip.person_render_id);
  const audioTimingDirty = start !== clip.source_start_seconds || linkedRate !== 1 || rate !== Number(((clip.source_end_seconds - clip.source_start_seconds) / (clip.timeline_end_seconds - clip.timeline_start_seconds)).toFixed(3));
  const framingChanged = mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5);
  return <div className={ui.inspector} aria-label="已选镜头详情">
    <ClipThumbnail clip={clip} taskId={workspace.task.id} className={ui.inspectorImage} />
    <nav className={ui.inspectorTabs} aria-label="镜头详情视图"><button aria-pressed={tab === "properties"} onClick={() => setTab("properties")} type="button">属性</button><button aria-pressed={tab === "source"} onClick={() => setTab("source")} type="button">来源</button></nav>
    {tab === "source" ? <div className={ui.source}><strong>{clip.person_render_id ? "当前人物口型" : clip.source_reference.split("/").pop()}</strong><p>{editorTime(clip.source_start_seconds)} – {editorTime(clip.source_end_seconds)}</p>{clip.person_render_id ? <span>根据当前任务配音制作</span> : <a href="/library">查看素材</a>}<h3>选片依据</h3><ul>{clip.match_reasons?.map(reason => <li key={reason}>{reason}</li>)}</ul><span>匹配程度 {clip.match_score.toFixed(2)}</span></div> : <>
      <dl className={ui.properties}><div><dt>画面用途</dt><dd>{clip.person_render_id ? "人物口型" : clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{editorTime(clip.timeline_start_seconds)} – {editorTime(clip.timeline_end_seconds)}</dd></div></dl>
      <section className={ui.propertySection}><h3>裁剪</h3><div className={ui.twoFields}><label>起点（秒）<input aria-label={`镜头 ${index + 1} 裁剪起点`} type="number" min={0} step={.1} value={start} disabled={busy || clip.locked} onChange={event => { onDraftChange(Number(event.target.value) !== clip.source_start_seconds || framingChanged); setStart(Number(event.target.value)); onDraftStateChange?.({ start: Number(event.target.value), mode, x, y }); }} /></label><label>终点（秒）<output>{(start + clip.timeline_end_seconds - clip.timeline_start_seconds).toFixed(1)}</output></label></div>{start !== clip.source_start_seconds ? <button className={ui.button} disabled={busy || clip.locked || start < 0} onClick={() => onPreview({ operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: start })} type="button">预览裁剪</button> : null}</section>
      <details className={ui.advancedGroup}><summary>更多剪辑</summary><div className={ui.advancedContent}>
      {clip.role !== "a_roll" && !clip.person_render_id ? <section className={ui.propertySection}><h3>画面速度</h3><label className={ui.field}>倍速<input aria-label="画面倍速" type="number" min={.25} max={4} step={.25} value={rate} disabled={busy || clip.locked} onChange={event => { setRate(Number(event.target.value)); onDraftChange(true); }} /></label><button className={ui.button} type="button" disabled={busy || clip.locked || !Number.isFinite(rate) || rate < .25 || rate > 4} onClick={() => onPreview({ operation_type: "ChangeClipSpeed", clip_id: clip.id, playback_rate: rate })}>预览速度</button><p>保持成片时长、声音和字幕；加速需要更多源素材。</p></section> : null}
      <section className={ui.propertySection}><h3>一起变速</h3><label className={ui.field}>倍速<input aria-label="关联倍速" type="number" min={.25} max={4} step={.25} value={linkedRate} disabled={busy || clip.locked} onChange={event => { setLinkedRate(Number(event.target.value)); onDraftChange(true); }} /></label><button className={ui.button} type="button" disabled={busy || clip.locked || !Number.isFinite(linkedRate) || linkedRate < .25 || linkedRate > 4 || linkedRate === 1} onClick={() => onPreview({ operation_type: "ChangeLinkedSpeed", clip_id: clip.id, playback_rate: linkedRate })}>预览变速</button><p>声音和字幕一起变速，改变成片时长；后续内容同步移动。</p></section>
      <section className={ui.propertySection}><h3>复制或删</h3><div className={ui.actions}><button className={ui.button} type="button" disabled={busy || clip.locked} onClick={() => onPreview({ operation_type: "DuplicateClip", clip_id: clip.id })}>预览复制</button><button className={ui.button} type="button" disabled={busy || clip.locked || (workspace.montage?.clips.length ?? 0) < 2} onClick={() => onPreview({ operation_type: "DeleteClip", clip_id: clip.id })}>预览删除</button></div><p>同时处理这段时间内的声音和字幕，后续内容同步移动。</p></section>
      <section className={ui.propertySection}><h3>切开镜头</h3><label className={ui.field}>切开位置<input aria-label="镜头分割位置" type="number" min={clip.timeline_start_seconds + .04} max={clip.timeline_end_seconds - .04} step={.04} value={splitAt} disabled={busy || clip.locked} onChange={event => { setSplitAt(Number(event.target.value)); onDraftChange(true); }} /></label><div className={ui.actions}>{getPlayhead ? <button className={ui.button} type="button" disabled={busy || clip.locked || !workspace.montage?.output_url} onClick={() => { const time = getPlayhead(); if (time !== null) { setSplitAt(Number(time.toFixed(3))); onDraftChange(true); } }}>取播放点</button> : null}<button className={ui.button} type="button" disabled={busy || clip.locked || !Number.isFinite(splitAt) || splitAt - clip.timeline_start_seconds < .04 || clip.timeline_end_seconds - splitAt < .04} onClick={() => onPreview({ operation_type: "SplitClip", clip_id: clip.id, split_time_seconds: splitAt })}>预览切开</button></div><p>保留总时长、配音和字幕。</p></section>
      </div></details>
      <section className={ui.propertySection}><h3>画面适配</h3><label className={ui.field}>显示方式<select aria-label="画面显示方式" value={mode} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || event.target.value !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setMode(event.target.value as "fill" | "fit"); onDraftStateChange?.({ start, mode: event.target.value as "fill" | "fit", x, y }); }}><option value="fill">填满画面</option><option value="fit">完整显示</option></select></label>{mode === "fill" ? <details><summary>裁切位置</summary><div className={ui.cropFields}><label>水平位置 <span>{Math.round(x * 100)}%</span><input aria-label="裁切水平位置" type="range" min={0} max={1} step={.05} value={x} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || Number(event.target.value) !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setX(Number(event.target.value)); onDraftStateChange?.({ start, mode, x: Number(event.target.value), y }); }} /></label><label>垂直位置 <span>{Math.round(y * 100)}%</span><input aria-label="裁切垂直位置" type="range" min={0} max={1} step={.05} value={y} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || Number(event.target.value) !== (clip.crop_y ?? .5)); setY(Number(event.target.value)); onDraftStateChange?.({ start, mode, x, y: Number(event.target.value) }); }} /></label></div></details> : null}<button className={ui.button} disabled={busy || clip.locked || !framingChanged} onClick={() => onPreview({ operation_type: "ChangeCrop", clip_id: clip.id, fit_mode: mode, crop_x: x, crop_y: y })} type="button">预览画面</button></section>
      <section className={ui.propertySection}><div className={ui.actionRow}><span>锁定镜头</span><button className={ui.iconButton} aria-label={clip.locked ? "解锁镜头" : "锁定镜头"} aria-pressed={clip.locked} disabled={busy} onClick={() => onPreview({ operation_type: "SetClipLock", clip_id: clip.id, locked: !clip.locked })} type="button">{clip.locked ? <Lock size={16} /> : <Unlock size={16} />}</button></div><div className={ui.actions}><button className={ui.button} aria-label="向前移动" disabled={busy || clip.locked || index === 0} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index - 1 })} type="button"><ArrowLeft size={14} />前移</button><button className={ui.button} aria-label="向后移动" disabled={busy || clip.locked || index === count - 1} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index + 1 })} type="button">后移<ArrowRight size={14} /></button><button className={ui.button} disabled={busy || clip.locked} onClick={onReplace} type="button"><Replace size={14} />换素材</button>{onAddSegment ? <button className={ui.button} disabled={busy} onClick={onAddSegment} type="button"><Plus size={14} />添加片段</button> : null}</div>
      {onAddSegment && inserting ? candidates.length ? <label className={ui.field}>插在当前镜头之后<select aria-label="插在当前镜头之后" disabled={busy} value="" onChange={event => { const candidate = candidates.find(item => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) onPreview({ operation_type: "InsertClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><option value="" disabled>选择候选，预览后确认</option>{candidates.map(item => <option key={`${item.asset_id}:${item.segment_id}`} value={`${item.asset_id}:${item.segment_id}`}>{item.file_name} · {item.description}</option>)}</select><small>插在当前镜头之后，时长继承该片段本身的时长。</small></label> : <p role="status">没有可插入的已审核镜头，请补充素材。</p> : null}
      {replacing ? candidates.length ? <label className={ui.field}>替换为已审核镜头<select aria-label="替换为已审核镜头" disabled={busy} value="" onChange={event => { const candidate = candidates.find(item => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) onPreview({ operation_type: "ReplaceClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><option value="" disabled>选择候选，预览后确认</option>{candidates.map(item => <option key={`${item.asset_id}:${item.segment_id}`} value={`${item.asset_id}:${item.segment_id}`}>{item.file_name} · {item.description}</option>)}</select></label> : <p role="status">没有满足时长的其他可用镜头，请补充素材。</p> : null}
      {clip.needs_confirmation ? <button className={ui.button} disabled={busy} onClick={() => onPreview({ operation_type: "ConfirmClip", clip_id: clip.id })} type="button"><Check size={14} />确认镜头</button> : null}</section>
      {index>0 ? <details className={ui.propertySection}><summary>镜头过渡</summary>
        <label className={ui.field}>过渡秒数<input aria-label="叠化时长（秒）" type="number" min="0" max="2" step="0.1" value={transitionSeconds} disabled={busy||clip.locked} onChange={event=>{onDraftChange(true);setTransitionSeconds(Number(event.target.value));}} /></label>
        <p>0 为硬切；从切点开始混合两段画面，需要前镜头的结尾素材余量。声音与字幕不移动。</p>
        <button className={ui.button} type="button" disabled={busy||clip.locked||transitionSeconds===(clip.transition_in_seconds ?? 0)} onClick={()=>onPreview({operation_type:"SetClipTransition",clip_id:clip.id,transition_seconds:transitionSeconds})}>预览转场</button>
      </details>:null}
      <GraphicOverlayEditor onSelect={onSelectOverlay} taskId={workspace.task.id} items={workspace.montage!.graphic_overlays ?? []} duration={workspace.montage!.duration_seconds} busy={busy} onPreview={onPreview} onDraftChange={()=>onDraftChange(true)}/>
      {onVisualPreview ? <VisualEffectsInspector portraitAvailable={workspace.portrait_available} clip={clip} allClipIds={workspace.montage!.clips.map(item=>item.id)} versionId={workspace.task.current_timeline_version_id} busy={busy} onPreview={onVisualPreview} onDraftChange={()=>onDraftChange(true)} />:null}
      {onAddOriginal && !clip.person_render_id ? <button className={ui.button} type="button" disabled={busy || clip.locked || audioTimingDirty} title={audioTimingDirty ? "请先确认裁切或变速修改" : undefined} onClick={onAddOriginal}>试听原声</button> : null}
      <section className={ui.propertySection}><h3>这段声音</h3>{captions.length ? <p>{captions.map(cue => cue.text).join(" ")}</p> : null}<ClipTimelineAudioPreview key={`${workspace.task.current_timeline_version_id}:${clip.id}`} taskId={workspace.task.id} versionId={workspace.task.current_timeline_version_id} start={clip.timeline_start_seconds} end={clip.timeline_end_seconds} busy={busy} /></section>
      <details className={ui.propertySection}><summary>人物口型</summary>{personRender ? <dl className={ui.properties}><div><dt>人物版本</dt><dd>{personRender.person_profile_id} · {personRender.person_version}</dd></div><div><dt>声音版本</dt><dd>{personRender.voice_profile_id} · {personRender.voice_version}</dd></div><div><dt>当前模型</dt><dd>{personRender.model_id}</dd></div></dl> : null}{!workspace.person_render_capability.available ? <p aria-label="人物口型不可用原因">{workspace.person_render_capability.message} {workspace.person_render_capability.repair_action}</p> : null}<button className={ui.button} disabled={busy || clip.locked || !workspace.person_render_capability.available} onClick={() => onPerson(clip.person_render_id ? "这里只重做口型，其他都不要动" : "这一段换成当前人物出镜")} type="button">{clip.person_render_id ? "重新对口型" : "换成当前人物出镜"}</button></details>
    </>}
  </div>;
}
