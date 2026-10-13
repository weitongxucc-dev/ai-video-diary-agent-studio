"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiUrl, importCreationTaskAudio, listenCreationTaskAudio, getCreationTaskAudioWaveform, resolveCreationTaskMediaResponse, cancelCreationTaskMediaJob, taskMediaJobLabel, TaskMediaJobError } from "@/lib/api";
import type { AudioEditInput, CreationTaskWorkspace, TimelineAudioClip, TimelineAudioTrack, TaskMediaJob } from "@/lib/types";
import ui from "./VideoEditor.module.css";

function AudioWave({ taskId, versionId, clip }: { taskId: string; versionId: string | null; clip: TimelineAudioClip }) {
  const identity = JSON.stringify([taskId, versionId, clip.id, clip.source_ref]);
  const [wave, setWave] = useState<{ identity: string; peaks: number[]; seconds_per_peak: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = wave?.identity === identity ? wave : null;
  const [job, setJob] = useState<{ identity: string; value: TaskMediaJob } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getCreationTaskAudioWaveform(taskId, clip.id, controller.signal)
      .then(response => resolveCreationTaskMediaResponse(taskId, response, "waveform", {
        signal: controller.signal, baseVersionId: versionId,
        onUpdate: value => { if (!controller.signal.aborted) setJob({ identity, value }); },
      }))
      .then(value => { if (!controller.signal.aborted) { setWave({ ...value, identity }); setError(null); } })
      .catch(cause => { if (!controller.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(identity); });
    return () => controller.abort();
  }, [taskId, versionId, clip.id, clip.source_ref, identity]);
  if (error === identity) return <span>波形读取失败</span>;
  if (!data) return <span role="status">{job?.identity === identity ? taskMediaJobLabel(job.value) : "读取波形…"}</span>;
  const first = Math.floor(clip.source_start_ms / 1000 / data.seconds_per_peak);
  const last = Math.min(data.peaks.length, Math.ceil(clip.source_end_ms / 1000 / data.seconds_per_peak));
  const stride = Math.max(1, Math.ceil((last-first)/240));
  const points = [];
  for (let i=first; i<last; i+=stride) {
    const peak = Math.max(...data.peaks.slice(i, Math.min(i+stride,last)));
    const x = (i-first) / Math.max(1,last-first) * 240;
    points.push(`M${x},${16-peak*15}V${16+peak*15}`);
  }
  return <svg viewBox="0 0 240 32" preserveAspectRatio="none" aria-hidden><path d={points.join(" ")} vectorEffect="non-scaling-stroke" /></svg>;
}

export function AudioTimelineTracks({ workspace, selectedId, onSelect, onPreview, busy, playhead = 0, onListenPosition, sharedTimeScale = false, subtitleTrack }: {
  workspace: CreationTaskWorkspace; selectedId: string | null; playhead?: number; onSelect: (id: string, time?: number) => void;
  onPreview: (input: AudioEditInput) => void; busy: boolean; onListenPosition?: (time: number) => void; sharedTimeScale?: boolean; subtitleTrack?: ReactNode;
}) {
  const graph = workspace.montage!.audio_timeline!;
  const duration = workspace.montage!.duration_seconds;
  const [zoom, setZoom] = useState(1);
  const [role, setRole] = useState("audio-music");
  const [solo, setSolo] = useState<string[]>([]);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(Math.min(10,duration));
  const [audio, setAudio] = useState<{url:string;start:number;end:number;soloKey:string;context:string} | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const listenContext = JSON.stringify([workspace.task.id, workspace.task.current_timeline_version_id, selectedId, start, end, solo]);
  const currentListenContext = useRef({ value: listenContext, revision: 0 });
  if (currentListenContext.current.value !== listenContext) currentListenContext.current = { value: listenContext, revision: currentListenContext.current.revision + 1 };
  const listenController = useRef<AbortController | null>(null);
  const [job, setJob] = useState<{ context: string; value: TaskMediaJob } | null>(null);
  useEffect(() => {
    setWorking(false);
    return () => listenController.current?.abort();
  }, [listenContext]);
  async function importAudio(selected: File) {
    const context = currentListenContext.current;
    const controller = new AbortController();
    listenController.current?.abort(); listenController.current = controller;
    setWorking(true); setError(null); setJob(null);
    try {
      const response = await importCreationTaskAudio(workspace.task.id, selected);
      const source = await resolveCreationTaskMediaResponse(workspace.task.id, response, "audio_source", {
        signal: controller.signal, baseVersionId: workspace.task.current_timeline_version_id,
        onUpdate: value => { if (!controller.signal.aborted && currentListenContext.current === context) setJob({ context: listenContext, value }); },
      });
      if (controller.signal.aborted || currentListenContext.current !== context) return;
      onPreview({ base_version_id: workspace.task.current_timeline_version_id, action: "add", target_id: role,
        values: { source_ref: source.source_ref, name: source.name, source_start_ms: 0,
          source_end_ms: Math.min(source.duration_ms, Math.round(duration*1000)), timeline_start_ms: 0 } });
    } catch (cause) { if (!controller.signal.aborted && currentListenContext.current === context && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(cause instanceof Error ? cause.message : "音频导入失败"); }
    finally { if (listenController.current === controller && !controller.signal.aborted) setWorking(false); }
  }
  async function listen() {
    const context = currentListenContext.current;
    const controller = new AbortController();
    listenController.current?.abort(); listenController.current = controller;
    setWorking(true); setError(null); setAudio(null); setJob(null);
    try {
      const response = await listenCreationTaskAudio(workspace.task.id, { base_version_id: workspace.task.current_timeline_version_id,
        start_seconds: start, end_seconds: end, solo_track_ids: solo });
      const result = await resolveCreationTaskMediaResponse(workspace.task.id, response, "audio_listen", {
        signal: controller.signal, baseVersionId: workspace.task.current_timeline_version_id,
        onUpdate: value => { if (!controller.signal.aborted && currentListenContext.current === context) setJob({ context: listenContext, value }); },
      });
      if (!controller.signal.aborted && currentListenContext.current === context) setAudio({url:result.audio_url,start,end,soloKey:solo.join(","),context:listenContext});
    } catch (cause) {
      if (!controller.signal.aborted && currentListenContext.current === context && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(cause instanceof Error ? cause.message : "试听失败");
    } finally { if (listenController.current === controller && !controller.signal.aborted) setWorking(false); }
  }
  async function cancelListen() {
    if (!job || job.context !== listenContext) return;
    const context = currentListenContext.current;
    try {
      const result = await cancelCreationTaskMediaJob(workspace.task.id, job.value.job_id);
      if (currentListenContext.current !== context) return;
      setJob({ context: listenContext, value: result });
      if (result.status === "cancelled") { listenController.current?.abort(); setWorking(false); }
    } catch (cause) { if (currentListenContext.current === context) setError(cause instanceof Error ? cause.message : "试听取消未完成"); }
  }
  const importControls = (
<div className={ui.audioToolbar}>
      <AppSelect aria-label="音频导入轨道" value={role} onValueChange={newValue => setRole(newValue)} disabled={busy || working}>{graph.tracks.map(track => <AppSelectOption key={track.id} value={track.id}>{track.name}</AppSelectOption>)}</AppSelect>
      <button className={ui.button} type="button" disabled={busy || working} onClick={() => file.current?.click()}>导入音频</button>
      <input ref={file} aria-label="选择音频文件" type="file" hidden accept=".wav,.mp3,.m4a,.aac,.flac" onChange={event => { const selected = event.target.files?.[0]; if (selected) void importAudio(selected); event.target.value=""; }} />
      {!sharedTimeScale ? <label>缩放<input aria-label="音轨缩放" type="range" min={1} max={4} step={.5} value={zoom} onChange={event => setZoom(Number(event.target.value))} /></label> : null}
    </div>
  );
  const listenControls = (
<div className={ui.audioToolbar}><label>试听（秒）<input aria-label="试听起点" type="number" min={0} max={duration} step={.1} value={start} onChange={event => { setStart(Number(event.target.value)); setAudio(null); }} /></label>
      <label>至<input aria-label="试听终点" type="number" min={0} max={duration} step={.1} value={end} onChange={event => { setEnd(Number(event.target.value)); setAudio(null); }} /></label>
      <button className={ui.button} disabled={busy || working || start >= end} type="button" onClick={() => void listen()}>{working ? "处理中…" : solo.length ? "试听独听轨道" : "试听混音"}</button>
      {solo.length ? <small>独听只影响试听</small> : null}
    </div>
  );
  return <section className={ui.audioTimeline} aria-label="多轨音频">
    <div className={sharedTimeScale ? undefined : ui.audioScroll}><div style={{ width: `${(sharedTimeScale ? 1 : zoom)*100}%` }}>{graph.tracks.filter(track => track.clips.length).map(track => <div className={ui.audioTrack} key={track.id}>
      <div className={ui.audioTrackLabel}><button type="button" aria-pressed={selectedId === track.id} onClick={() => onSelect(track.id)}>{track.name}{track.muted ? " · 静音" : ""}</button>
        <button type="button" aria-label={`独听${track.name}`} aria-pressed={solo.includes(track.id)} onClick={() => { setSolo(solo.includes(track.id) ? solo.filter(id => id !== track.id) : [...solo,track.id]); setAudio(null); }}>独听</button></div>
      <div className={ui.audioLane}>{track.clips.map(clip => <button type="button" key={`${clip.id}:${clip.source_ref}`} className={ui.audioClip} aria-label={`选择音频：${clip.name}`} aria-pressed={selectedId === clip.id}
        style={{ left: `${clip.timeline_start_ms/1000/duration*100}%`, width: `${((clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1))/1000/duration*100}%`, opacity: clip.muted || track.muted ? .45 : 1 }}
        onClick={event => { const bounds=event.currentTarget.getBoundingClientRect();
          const fraction=event.detail && bounds.width ? Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width)) : 0;
          onSelect(clip.id,(clip.timeline_start_ms+fraction*((clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1)))/1000); setStart(clip.timeline_start_ms/1000); setEnd(Math.min(duration,(clip.timeline_start_ms+(clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1))/1000)); setAudio(null); }}>
        <AudioWave taskId={workspace.task.id} versionId={workspace.task.current_timeline_version_id} clip={clip} /><span>{clip.name}{clip.locked ? " · 已锁定" : ""}</span>
      </button>)}<span className={ui.audioPlayhead} style={{left:`${playhead/duration*100}%`}} aria-hidden /></div>
    </div>)}</div></div>
    {subtitleTrack}
    <div className={ui.audioControls} role="group" aria-label="音频导入与试听">
      {importControls}
      {listenControls}
    </div>
    {job?.context === listenContext && job.value.status !== "succeeded" ? <p role="status">{taskMediaJobLabel(job.value)}{["queued", "running", "interrupted"].includes(job.value.status) ? <button className={ui.button} type="button" onClick={() => void cancelListen()}>{job.value.result_kind === "audio_source" ? "取消音频处理" : "取消试听"}</button> : null}</p> : null}
    {audio && audio.context===listenContext && audio.start===start && audio.end===end && audio.soloKey===solo.join(",") ? <audio aria-label="选区音频试听" controls src={apiUrl(audio.url)} onPlay={event=>onListenPosition?.(audio.start+event.currentTarget.currentTime)} onTimeUpdate={event=>onListenPosition?.(audio.start+event.currentTarget.currentTime)} /> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}

export function AudioTimelineInspector({ track, clip, versionId, playhead, busy, onPreview, onDraftChange }: {
  track: TimelineAudioTrack; clip: TimelineAudioClip | null; versionId: string | null; playhead: number;
  busy: boolean; onPreview: (input: AudioEditInput) => void; onDraftChange: () => void;
}) {
  const target = clip ?? track;
  const [values, setValues] = useState<Record<string, unknown>>({});
  useEffect(() => { setValues({}); }, [versionId, target.id, target.gain_db, target.muted, clip?.timeline_start_ms,
    clip?.source_start_ms, clip?.source_end_ms, clip?.playback_rate, clip?.fade_in_ms, clip?.fade_out_ms,
    clip?.denoise_db, clip?.loudness_target_lufs, clip?.locked, track.duck_under_voice]);
  function update(key: string, value: unknown, apply = false) {
    const next = { ...values, [key]: value };
    onDraftChange(); setValues(next);
    if (apply) submit(clip ? "update" : "track", next);
  }
  function commit() {
    if (!busy && Object.keys(values).length && Object.values(values).every(value => value !== ""))
      submit(clip ? "update" : "track", values);
  }
  function submit(action: AudioEditInput["action"], changes: Record<string,unknown>) { onPreview({ base_version_id:versionId, target_id:target.id, action,values:changes }); }
  const locked = track.locked || Boolean(clip?.locked);
  const number = (key: string, initial: number, label: string, factor=1) => <label className={ui.field}>{label}<input aria-label={label} type="number" step={factor===1000 ? .1 : 1} disabled={busy || locked} required value={values[key] === "" ? "" : Number(values[key] ?? initial)/factor} onChange={event => update(key, event.target.value === "" ? "" : Number(event.target.value)*factor)} onBlur={event => {if(event.currentTarget.validity.valid) commit();}} /></label>;
  return <form className={ui.inspector} aria-label="已选音频详情" onSubmit={event => { event.preventDefault(); submit(clip ? "update":"track",values); }}>
    <h3>{target.name}</h3>
    <p>{clip ? `${(clip.timeline_start_ms/1000).toFixed(1)}–${((clip.timeline_start_ms+(clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1))/1000).toFixed(1)} 秒 · ${track.name}` : "整条音轨"}</p>
    {number("gain_db",target.gain_db,"音量（dB）")}
    <label className={ui.actionRow}>静音<input aria-label="音频静音" type="checkbox" disabled={busy || locked} checked={Boolean(values.muted ?? target.muted)} onChange={event => update("muted",event.target.checked,true)} /></label>
    {clip ? <>
      {number("timeline_start_ms",clip.timeline_start_ms,"时间线起点（秒）",1000)}
      <div className={ui.twoFields}>{number("source_start_ms",clip.source_start_ms,"源音频起点（秒）",1000)}{number("source_end_ms",clip.source_end_ms,"源音频终点（秒）",1000)}</div>
      <div className={ui.twoFields}>{number("fade_in_ms",clip.fade_in_ms,"淡入（秒）",1000)}{number("fade_out_ms",clip.fade_out_ms,"淡出（秒）",1000)}</div>
      <details><summary>声音处理与音量关键点</summary>{number("denoise_db",clip.denoise_db,"降噪强度（0–24）")}
        <label className={ui.field}>响度目标<AppSelect aria-label="响度目标" disabled={busy || locked} value={String("loudness_target_lufs" in values ? values.loudness_target_lufs ?? "off" : clip.loudness_target_lufs ?? "off")} onValueChange={newValue =>update("loudness_target_lufs",newValue==="off" ? null:Number(newValue),true)}><AppSelectOption value="off">保留原响度</AppSelectOption><AppSelectOption value="-16">−16 LUFS</AppSelectOption><AppSelectOption value="-23">−23 LUFS</AppSelectOption></AppSelect></label>
        <p>适合稳定背景噪声；先试听人声细节。</p>
        <button className={ui.button} type="button" disabled={busy || locked} onClick={() => {
          const points=(values.gain_points as TimelineAudioClip["gain_points"]) ?? clip.editor_gain_points ?? clip.gain_points;
          const offset=Math.max(0,Math.min((clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1),Math.round(playhead*1000)-clip.timeline_start_ms));
          if (!points.some(point=>point.offset_ms===offset)) update("gain_points",[...points,{offset_ms:offset,gain_db:0}].sort((a,b)=>a.offset_ms-b.offset_ms),true);
        }}>在播放位置添加音量点</button>
        {((values.gain_points as TimelineAudioClip["gain_points"]) ?? clip.editor_gain_points ?? clip.gain_points).map((point,index,points)=><label className={ui.field} key={index}>{(point.offset_ms/1000).toFixed(1)} 秒<input aria-label={`音量点 ${index+1}`} type="number" required onBlur={event=>{if(event.currentTarget.validity.valid)commit();}} value={point.gain_db} disabled={busy || locked} onChange={event => update("gain_points",points.map((item,i)=>i===index ? {...item,gain_db:Number(event.target.value)}:item))} /></label>)}
        {((values.gain_points as TimelineAudioClip["gain_points"]) ?? clip.editor_gain_points ?? clip.gain_points).length ? <button className={ui.button} type="button" disabled={busy || locked} onClick={()=>update("gain_points",[],true)}>清除音量点</button>:null}
      </details>
    </> : track.role === "music" ? <label className={ui.actionRow}>说话时压低音乐<input aria-label="说话时压低音乐" type="checkbox" disabled={busy || locked} checked={Boolean(values.duck_under_voice ?? track.duck_under_voice)} onChange={event=>update("duck_under_voice",event.target.checked,true)} /></label> : null}
    <button className={ui.primary} disabled={busy || locked || !Object.keys(values).length} type="submit">保存音频草稿</button>
    <div className={ui.actions}><button className={ui.button} disabled={busy || (Boolean(clip) && track.locked)} type="button" onClick={()=>submit(clip ? "update":"track",{locked:!target.locked})}>{target.locked ? "解锁":"锁定"}</button>
      {clip ? <><button className={ui.button} type="button" disabled={busy || locked} onClick={()=>submit("split",{at_ms:Math.round(playhead*1000)})}>在播放位置分割</button><button className={ui.button} type="button" disabled={busy || locked} onClick={()=>submit("delete",{})}>删除片段</button><button className={ui.button} type="button" disabled={busy || locked} onClick={()=>submit("duplicate",{timeline_start_ms:clip.timeline_start_ms+(clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1)})}>复制到后方</button></>:null}</div>
  </form>;
}
