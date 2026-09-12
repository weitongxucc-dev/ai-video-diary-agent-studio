"use client";

import { useState, type RefObject, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Check, Lock, Unlock, Maximize, Pause, Play, Replace, Volume2, VolumeX } from "lucide-react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskMontageClip, CreationTaskExactEditInput, CreationTaskWorkspace, MontageClipCandidate } from "@/lib/types";
import ui from "./VideoEditor.module.css";
import { NarrationWaveformTrack } from "./NarrationWaveformTrack";

export const editorTime = (value: number) => `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toFixed(1).padStart(4, "0")}`;
const sourceUrl = (clip: CreationTaskMontageClip) => apiUrl(`/api/production/materials/${encodeURIComponent(clip.asset_id)}/media`);

export function ClipThumbnail({ clip, className }: { clip: CreationTaskMontageClip; className?: string }) {
  return <video className={className} aria-hidden muted playsInline preload="metadata" src={sourceUrl(clip)} onLoadedMetadata={event => { event.currentTarget.currentTime = clip.source_start_seconds; }} />;
}

export function VideoEditingStage({ workspace, selectedId, onSelect, onPosition, videoRef, initialTime, renderAction }: {
  workspace: CreationTaskWorkspace; selectedId: string | null; onSelect: (id: string) => void;
  onPosition: (video: HTMLVideoElement) => void; videoRef: RefObject<HTMLVideoElement | null>;
  initialTime: number; renderAction: ReactNode;
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
    <div className={ui.previewToolbar}><strong>{montage.output_url ? "成片预览" : "待渲染"}</strong><span>{workspace.plan.aspect_ratio} · {editorTime(montage.duration_seconds)}</span>{montage.low_confidence_clip_count > 0 ? <span className={ui.warning}>{montage.low_confidence_clip_count} 个镜头待确认</span> : null}<div>{renderAction}</div></div>
    <div className={ui.player}>
      {montage.output_url ? <video key={workspace.task.current_timeline_version_id} aria-label="剪辑成片预览" ref={videoRef} playsInline preload="metadata" src={apiUrl(`${montage.output_url}?version=${encodeURIComponent(workspace.task.current_timeline_version_id ?? "")}`)} muted={muted}
        onLoadedMetadata={event => { event.currentTarget.currentTime = initialTime; setPlaybackError(false); }}
        onTimeUpdate={event => position(event.currentTarget)} onSeeked={event => position(event.currentTarget)}
        onPlay={() => setPlaying(true)} onPause={event => { setPlaying(false); position(event.currentTarget); }}
        onEnded={() => setPlaying(false)} onError={() => setPlaybackError(true)} />
        : selected ? <video aria-label="源素材预览" controls key={selected.id} playsInline preload="metadata" src={sourceUrl(selected)} onLoadedMetadata={event => { event.currentTarget.currentTime = selected.source_start_seconds; }} />
          : <p>选择镜头查看源素材，确认后渲染成片。</p>}
      {!montage.output_url && selected ? <span className={ui.playerBadge}>源素材 · 当前修改尚未渲染</span> : null}
      {playbackError ? <p className={ui.playerError} role="alert">无法读取当前成片，请重新渲染。</p> : null}
    </div>
    {montage.output_url ? <div className={ui.transport}>
      <button className={ui.iconButton} aria-label={playing ? "暂停成片" : "播放成片"} onClick={() => { const video = videoRef.current; if (!video) return; if (video.paused) void video.play().catch(() => setPlaybackError(true)); else video.pause(); }} type="button">{playing ? <Pause size={17} /> : <Play size={17} />}</button>
      <button className={ui.iconButton} aria-label={muted ? "取消静音" : "静音"} onClick={() => setMuted(!muted)} type="button">{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>
      <input aria-label="成片播放位置" type="range" min={0} max={montage.duration_seconds} step={0.01} value={Math.min(time, montage.duration_seconds)} onChange={event => seek(Number(event.target.value))} />
      <time>{editorTime(time)} / {editorTime(montage.duration_seconds)}</time>
      <button className={ui.iconButton} aria-label="全屏预览" onClick={() => { void videoRef.current?.requestFullscreen(); }} type="button"><Maximize size={16} /></button>
    </div> : null}
    <div className={ui.storyboard} aria-label="镜头故事板">{montage.clips.map((clip, index) => <button key={clip.id} type="button" aria-label={`选择镜头 ${index + 1}`} aria-pressed={selectedId === clip.id} className={ui.shot} onClick={() => onSelect(clip.id)}>
      <span className={ui.shotImage}><ClipThumbnail clip={clip} />{clip.locked ? <Lock size={13} /> : clip.needs_confirmation ? <span className={ui.reviewDot} title="待确认" /> : null}</span>
      <span className={ui.shotLabel}><strong>镜头 {String(index + 1).padStart(2, "0")}</strong><small>{editorTime(clip.timeline_start_seconds)}–{editorTime(clip.timeline_end_seconds)}</small></span>
    </button>)}</div>
    <div className={ui.tracks} aria-label="配音与字幕时间范围">
      <div className={ui.ruler}><span>时间</span><div>{Array.from({ length: 5 }, (_, i) => <time key={i}>{editorTime(montage.duration_seconds * i / 4)}</time>)}</div></div>
      <NarrationWaveformTrack workspace={workspace} selectedId={selectedId} onSelect={onSelect} />
      <div className={ui.track}><span>字幕</span><div>{workspace.subtitle_tracks.find(track => track.language === "zh-CN")?.cues.map(cue => { const clip = montage.clips.find(item => cue.start_seconds >= item.timeline_start_seconds && cue.start_seconds < item.timeline_end_seconds); return clip ? <button title={cue.text} key={cue.id} type="button" style={{ flex: cue.end_seconds - cue.start_seconds }} aria-label={`定位字幕：${cue.text}`} aria-pressed={clip.id === selectedId} onClick={() => onSelect(clip.id)}>{cue.text}</button> : null; })}</div></div>
    </div>
  </div>;
}

export function ClipNarrationPreview({ audioUrl, start, duration }: { audioUrl: string; start: number; duration: number }) {
  const end = start + duration;
  return <audio aria-label="当前镜头配音" controls preload="metadata"
    src={`${apiUrl(audioUrl)}#t=${start.toFixed(3)},${end.toFixed(3)}`}
    onLoadedMetadata={event => { event.currentTarget.currentTime = start; }}
    onPlay={event => {
      const audio = event.currentTarget;
      if (audio.currentTime < start || audio.currentTime >= end) audio.currentTime = start;
    }}
    onSeeking={event => {
      const audio = event.currentTarget;
      if (audio.currentTime < start) audio.currentTime = start;
      else if (audio.currentTime > end) audio.currentTime = end;
    }}
    onTimeUpdate={event => {
      const audio = event.currentTarget;
      if (audio.currentTime >= end) { audio.pause(); if (audio.currentTime > end) audio.currentTime = end; }
    }} />;
}

export function VideoClipInspector({ clip, index, count, workspace, busy, onPreview, onDraftChange, onReplace, candidates, replacing, onPerson }: {
  clip: CreationTaskMontageClip; index: number; count: number; workspace: CreationTaskWorkspace; busy: boolean;
  onPreview: (input: CreationTaskExactEditInput) => void; onDraftChange: (changed: boolean) => void; onReplace: () => void;
  candidates: MontageClipCandidate[]; replacing: boolean; onPerson: (command: string) => void;
}) {
  const [tab, setTab] = useState("properties");
  const [start, setStart] = useState(clip.source_start_seconds);
  const [mode, setMode] = useState(clip.fit_mode ?? "fill");
  const [x, setX] = useState(clip.crop_x ?? .5);
  const [y, setY] = useState(clip.crop_y ?? .5);
  const cue = workspace.montage?.cues.find(item => item.id === clip.cue_id);
  const narration = workspace.narration?.segments.find(item => item.id === cue?.narration_segment_id);
  const cues = workspace.montage?.cues ?? [];
  const cueIndex = cues.findIndex(item => item.id === clip.cue_id);
  const narrationStart = cues.slice(0, cueIndex).filter(item => item.narration_segment_id === cue?.narration_segment_id).reduce((sum, item) => sum + item.duration_seconds, 0);
  const personRender = workspace.person_renders.find(item => item.id === clip.person_render_id);
  const framingChanged = mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5);
  return <div className={ui.inspector} aria-label="已选镜头详情">
    <ClipThumbnail clip={clip} className={ui.inspectorImage} />
    <nav className={ui.inspectorTabs} aria-label="镜头详情视图"><button aria-pressed={tab === "properties"} onClick={() => setTab("properties")} type="button">属性</button><button aria-pressed={tab === "source"} onClick={() => setTab("source")} type="button">来源</button></nav>
    {tab === "source" ? <div className={ui.source}><strong>{clip.source_reference.split("/").pop()}</strong><p>{editorTime(clip.source_start_seconds)} – {editorTime(clip.source_end_seconds)}</p><a href="/library">在素材库查看</a><h3>选片依据</h3><ul>{clip.match_reasons?.map(reason => <li key={reason}>{reason}</li>)}</ul><span>匹配 {Math.round(clip.match_score * 100)}%</span></div> : <>
      <dl className={ui.properties}><div><dt>画面用途</dt><dd>{clip.person_render_id ? "人物口型" : clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{editorTime(clip.timeline_start_seconds)} – {editorTime(clip.timeline_end_seconds)}</dd></div></dl>
      <section className={ui.propertySection}><h3>源素材裁剪</h3><div className={ui.twoFields}><label>起点（秒）<input aria-label={`镜头 ${index + 1} 裁剪起点`} type="number" min={0} step={.1} value={start} disabled={busy || clip.locked} onChange={event => { onDraftChange(Number(event.target.value) !== clip.source_start_seconds || framingChanged); setStart(Number(event.target.value)); }} /></label><label>终点（秒）<output>{(start + clip.timeline_end_seconds - clip.timeline_start_seconds).toFixed(1)}</output></label></div>{start !== clip.source_start_seconds ? <button className={ui.button} disabled={busy || clip.locked || start < 0} onClick={() => onPreview({ operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: start })} type="button">预览裁剪</button> : null}</section>
      <section className={ui.propertySection}><h3>画面适配</h3><label className={ui.field}>显示方式<select aria-label="画面显示方式" value={mode} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || event.target.value !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setMode(event.target.value as "fill" | "fit"); }}><option value="fill">填满画面</option><option value="fit">完整显示</option></select></label>{mode === "fill" ? <details><summary>裁切位置</summary><div className={ui.cropFields}><label>水平位置 <span>{Math.round(x * 100)}%</span><input aria-label="裁切水平位置" type="range" min={0} max={1} step={.05} value={x} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || Number(event.target.value) !== (clip.crop_x ?? .5) || y !== (clip.crop_y ?? .5)); setX(Number(event.target.value)); }} /></label><label>垂直位置 <span>{Math.round(y * 100)}%</span><input aria-label="裁切垂直位置" type="range" min={0} max={1} step={.05} value={y} disabled={busy || clip.locked} onChange={event => { onDraftChange(start !== clip.source_start_seconds || mode !== (clip.fit_mode ?? "fill") || x !== (clip.crop_x ?? .5) || Number(event.target.value) !== (clip.crop_y ?? .5)); setY(Number(event.target.value)); }} /></label></div></details> : null}<button className={ui.button} disabled={busy || clip.locked || !framingChanged} onClick={() => onPreview({ operation_type: "ChangeCrop", clip_id: clip.id, fit_mode: mode, crop_x: x, crop_y: y })} type="button">预览适配修改</button></section>
      <section className={ui.propertySection}><div className={ui.actionRow}><span>锁定镜头</span><button className={ui.iconButton} aria-label={clip.locked ? "解锁镜头" : "锁定镜头"} aria-pressed={clip.locked} disabled={busy} onClick={() => onPreview({ operation_type: "SetClipLock", clip_id: clip.id, locked: !clip.locked })} type="button">{clip.locked ? <Lock size={16} /> : <Unlock size={16} />}</button></div><div className={ui.actions}><button className={ui.button} aria-label="向前移动" disabled={busy || clip.locked || index === 0} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index - 1 })} type="button"><ArrowLeft size={14} />前移</button><button className={ui.button} aria-label="向后移动" disabled={busy || clip.locked || index === count - 1} onClick={() => onPreview({ operation_type: "ReorderClip", clip_id: clip.id, position: index + 1 })} type="button">后移<ArrowRight size={14} /></button><button className={ui.button} disabled={busy || clip.locked} onClick={onReplace} type="button"><Replace size={14} />替换素材</button></div>
      {replacing ? candidates.length ? <label className={ui.field}>替换为已审核镜头<select aria-label="替换为已审核镜头" disabled={busy} value="" onChange={event => { const candidate = candidates.find(item => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) onPreview({ operation_type: "ReplaceClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><option value="" disabled>选择候选，预览后确认</option>{candidates.map(item => <option key={`${item.asset_id}:${item.segment_id}`} value={`${item.asset_id}:${item.segment_id}`}>{item.file_name} · {item.description}</option>)}</select></label> : <p role="status">没有满足时长的其他可用镜头，请补充素材。</p> : null}
      {clip.needs_confirmation ? <button className={ui.button} disabled={busy} onClick={() => onPreview({ operation_type: "ConfirmClip", clip_id: clip.id })} type="button"><Check size={14} />确认镜头</button> : null}</section>
      {narration?.audio_url ? <section className={ui.propertySection}><h3>配音</h3><p>{cue?.text}</p><ClipNarrationPreview key={`${clip.id}:${narration.audio_url}`} audioUrl={narration.audio_url} start={narrationStart} duration={cue?.duration_seconds ?? 0} /></section> : null}
      <details className={ui.propertySection}><summary>人物口型</summary>{personRender ? <dl className={ui.properties}><div><dt>人物版本</dt><dd>{personRender.person_profile_id} · {personRender.person_version}</dd></div><div><dt>声音版本</dt><dd>{personRender.voice_profile_id} · {personRender.voice_version}</dd></div><div><dt>当前模型</dt><dd>{personRender.model_id}</dd></div></dl> : null}{!workspace.person_render_capability.available ? <p aria-label="人物口型不可用原因">{workspace.person_render_capability.message} {workspace.person_render_capability.repair_action}</p> : null}<button className={ui.button} disabled={busy || clip.locked || !workspace.person_render_capability.available} onClick={() => onPerson(clip.person_render_id ? "这里只重做口型，其他都不要动" : "这一段换成当前人物出镜")} type="button">{clip.person_render_id ? "重新对口型" : "换成当前人物出镜"}</button></details>
    </>}
  </div>;
}
