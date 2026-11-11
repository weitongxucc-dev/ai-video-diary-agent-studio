"use client";
import { createPortal } from "react-dom";
import { useActiveEditorDock } from "./workbench/EditorDock";
import { IconButton } from "./ui/icon-button";
import { WorkspaceAudio } from "./workbench/WorkspaceMedia";
import { useWorkspaceViewState } from "./workbench/context";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiUrl, importCreationTaskAudio, listenCreationTaskAudio, getCreationTaskAudioWaveform, getCreationTaskMediaJob, resolveCreationTaskMediaResponse, cancelCreationTaskMediaJob, taskMediaJobLabel, TaskMediaJobError } from "@/lib/api";
import type { AudioEditInput, AudioTimeline, TimelineAudioClip, TimelineAudioTrack, TaskMediaJob } from "@/lib/types";
import ui from "./VideoEditor.module.css";
import { HomeCardDialog } from "./HomeCardDialog";
import { AudioLines, CircleAlert, Headphones } from "lucide-react";

function AudioTimelineClip({ taskId, versionId, clip, duration, selected, muted, onSelect }: {
  taskId: string; versionId: string | null; clip: TimelineAudioClip; duration: number;
  selected: boolean; muted: boolean; onSelect: (time: number) => void;
}) {
  const identity = JSON.stringify([taskId, versionId, clip.id, clip.source_ref]);
  const [wave, setWave] = useState<{ identity: string; peaks: number[]; seconds_per_peak: number } | null>(null);
  const [error, setError] = useState<{ identity: string; message: string } | null>(null);
  const [job, setJob] = useState<{ identity: string; value: TaskMediaJob } | null>(null);
  const [details, setDetails] = useState(false);
  const [reading, setReading] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const detailsAnchor = useRef<HTMLButtonElement>(null);
  const data = wave?.identity === identity ? wave : null;
  const currentError = error?.identity === identity ? error.message : null;
  const currentJob = job?.identity === identity ? job.value : null;
  useEffect(() => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setReading(false);
    setDetails(false);
    getCreationTaskAudioWaveform(taskId, clip.id, controller.signal)
      .then(response => resolveCreationTaskMediaResponse(taskId, response, "waveform", {
        signal: controller.signal, baseVersionId: versionId,
        onUpdate: value => { if (!controller.signal.aborted) setJob({ identity, value }); },
      }))
      .then(value => { if (!controller.signal.aborted) { setWave({ ...value, identity }); setError(null); } })
      .catch(cause => { if (!controller.signal.aborted) setError({ identity, message: cause instanceof Error ? cause.message : "波形读取失败" }); });
    return () => controller.abort();
  }, [taskId, versionId, clip.id, clip.source_ref, identity]);
  async function reread() {
    if (!currentJob) return;
    const controller = new AbortController();
    controllerRef.current?.abort(); controllerRef.current = controller;
    setReading(true);
    try {
      const receipt = await getCreationTaskMediaJob(taskId, currentJob.job_id, controller.signal);
      const value = await resolveCreationTaskMediaResponse(taskId, receipt, "waveform", {
        signal: controller.signal, baseVersionId: versionId,
        onUpdate: next => { if (!controller.signal.aborted) setJob({ identity, value: next }); },
      });
      if (!controller.signal.aborted) { setWave({ ...value, identity }); setError(null); setDetails(false); }
    } catch (cause) {
      if (!controller.signal.aborted) setError({ identity, message: cause instanceof Error ? cause.message : "波形读取失败" });
    } finally { if (!controller.signal.aborted) setReading(false); }
  }
  useEffect(() => () => controllerRef.current?.abort(), []);
  let waveform = null;
  if (data) {
    const first = Math.floor(clip.source_start_ms / 1000 / data.seconds_per_peak);
    const last = Math.min(data.peaks.length, Math.ceil(clip.source_end_ms / 1000 / data.seconds_per_peak));
    const stride = Math.max(1, Math.ceil((last-first)/240));
    const points = [];
    for (let i=first; i<last; i+=stride) {
      const peak = Math.max(...data.peaks.slice(i, Math.min(i+stride,last)));
      const x = (i-first) / Math.max(1,last-first) * 240;
      points.push(`M${x},${16-peak*15}V${16+peak*15}`);
    }
    waveform = <svg viewBox="0 0 240 32" preserveAspectRatio="none" aria-hidden><path d={points.join(" ")} vectorEffect="non-scaling-stroke" /></svg>;
  }
  const state = currentError ? "波形未加载" : currentJob ? taskMediaJobLabel(currentJob) : "读取波形…";
  return <div className={ui.audioClip} data-selected={selected || undefined}
    style={{left:`${clip.timeline_start_ms/1000/duration*100}%`,width:`${((clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1))/1000/duration*100}%`,opacity:muted || clip.muted ? .45 : 1}}>
    <button type="button" className={ui.audioClipSelect} aria-label={`选择音频：${clip.name}`} aria-pressed={selected}
      onClick={event => { const bounds=event.currentTarget.getBoundingClientRect();
        const fraction=event.detail && bounds.width ? Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width)) : 0;
        onSelect((clip.timeline_start_ms+fraction*((clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1)))/1000); }}>
      <span className={ui.audioClipName}>{clip.name}{clip.locked ? " · 已锁定" : ""}</span>
      {waveform ?? <span className={ui.audioWaveState} role="status" title={currentError ?? state}>{state}</span>}
    </button>
    {currentError ? <button ref={detailsAnchor} className={ui.audioWaveDetails} type="button" aria-label={`查看波形问题：${clip.name}`} title="查看波形问题" onClick={() => setDetails(true)}><CircleAlert size={13}/></button> : null}
    {details ? <HomeCardDialog kind="selection" title="波形未加载" anchor={detailsAnchor} onClose={() => setDetails(false)}>
      <p role="alert">{currentError}</p>
      {currentJob ? <><button className={ui.button} disabled={reading} type="button" onClick={() => void reread()}>{reading ? "读取中…" : "重新读取波形"}</button><p>读取原请求的结果，不会重新生成配音。</p></> : <p>未取得原波形请求，请在运行详情中核对。</p>}
    </HomeCardDialog> : null}
  </div>;
}

export function AudioTimelineTracks({ taskId, versionId, audioTimeline: graph, durationSeconds: duration, selectedId, onSelect, onPreview, busy, playhead = 0, onListenPosition, sharedTimeScale = false, subtitleTrack }: {
  taskId: string; versionId: string | null; audioTimeline: AudioTimeline; durationSeconds: number;
  selectedId: string | null; playhead?: number; onSelect: (id: string, time?: number) => void;
  onPreview: (input: AudioEditInput) => void; busy: boolean; onListenPosition?: (time: number) => void; sharedTimeScale?: boolean; subtitleTrack?: ReactNode;
}) {
  const toolsHost = useActiveEditorDock("timelineTools");
  const [toolsOpen, setToolsOpen] = useState(false);
  const toolsAnchor = useRef<HTMLButtonElement>(null);
  const [zoom, setZoom] = useWorkspaceViewState("audio.zoom", 1);
  const [role, setRole] = useWorkspaceViewState("audio.role", graph.tracks.find(track => track.role === "music")?.id ?? graph.tracks[0]?.id ?? "");
  const importTrackId = graph.tracks.some(track => track.id === role) ? role : "";
  const [solo, setSolo] = useWorkspaceViewState<string[]>("audio.solo", []);
  const [start, setStart] = useWorkspaceViewState("audio.start", 0);
  const [end, setEnd] = useWorkspaceViewState("audio.end", Math.min(10,duration));
  const [audio, setAudio] = useState<{url:string;start:number;end:number;soloKey:string;context:string} | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const listenContext = JSON.stringify([taskId, versionId, selectedId, start, end, solo]);
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
      const response = await importCreationTaskAudio(taskId, selected);
      const source = await resolveCreationTaskMediaResponse(taskId, response, "audio_source", {
        signal: controller.signal, baseVersionId: versionId,
        onUpdate: value => { if (!controller.signal.aborted && currentListenContext.current === context) setJob({ context: listenContext, value }); },
      });
      if (controller.signal.aborted || currentListenContext.current !== context) return;
      onPreview({ base_version_id: versionId, action: "add", target_id: importTrackId,
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
      const response = await listenCreationTaskAudio(taskId, { base_version_id: versionId,
        start_seconds: start, end_seconds: end, solo_track_ids: solo });
      const result = await resolveCreationTaskMediaResponse(taskId, response, "audio_listen", {
        signal: controller.signal, baseVersionId: versionId,
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
      const result = await cancelCreationTaskMediaJob(taskId, job.value.job_id);
      if (currentListenContext.current !== context) return;
      setJob({ context: listenContext, value: result });
      if (result.status === "cancelled") { listenController.current?.abort(); setWorking(false); }
    } catch (cause) { if (currentListenContext.current === context) setError(cause instanceof Error ? cause.message : "试听取消未完成"); }
  }
  const audioToolAccess = <div className={ui.audioToolAccess}><IconButton ref={toolsAnchor} label="音频工具" type="button" aria-expanded={toolsOpen} aria-haspopup="dialog" onClick={() => setToolsOpen(value => !value)}><AudioLines size={14}/></IconButton>{working ? <span role="status">处理中…</span> : error ? <span>音频处理未完成</span> : null}</div>;
  const importControls = (
<div className={ui.audioToolbar}>
      <AppSelect aria-label="音频导入轨道" value={importTrackId} onValueChange={newValue => setRole(newValue)} disabled={busy || working}><AppSelectOption value="">选择导入轨道</AppSelectOption>{graph.tracks.map(track => <AppSelectOption key={track.id} value={track.id}>{track.name}</AppSelectOption>)}</AppSelect>
      <button className={ui.button} type="button" disabled={busy || working || !importTrackId} onClick={() => file.current?.click()}>导入音频</button>
      <input ref={file} aria-label="选择音频文件" type="file" disabled={busy || working || !importTrackId} hidden accept=".wav,.mp3,.m4a,.aac,.flac" onChange={event => { const selected = event.target.files?.[0]; if (selected) void importAudio(selected); event.target.value=""; }} />
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
    <div data-workspace-scroll="audio-tracks" className={sharedTimeScale ? undefined : ui.audioScroll}><div style={{ width: `${(sharedTimeScale ? 1 : zoom)*100}%` }}>{graph.tracks.filter(track => track.clips.length).map(track => <div className={ui.audioTrack} key={track.id}>
      <div className={ui.audioTrackLabel}><button type="button" aria-pressed={selectedId === track.id} onClick={() => onSelect(track.id)}>{track.name}{track.muted ? " · 静音" : ""}</button>
        <IconButton label={`独听${track.name}`} aria-pressed={solo.includes(track.id)} className={ui.audioSoloButton} onClick={() => { setSolo(solo.includes(track.id) ? solo.filter(id => id !== track.id) : [...solo,track.id]); setAudio(null); }}><Headphones size={12}/></IconButton></div>
      <div className={ui.audioLane}>{track.clips.map(clip => <AudioTimelineClip key={`${clip.id}:${clip.source_ref}`} taskId={taskId} versionId={versionId} clip={clip} duration={duration} selected={selectedId === clip.id} muted={track.muted}
        onSelect={time => { onSelect(clip.id,time); setStart(clip.timeline_start_ms/1000); setEnd(Math.min(duration,(clip.timeline_start_ms+(clip.source_end_ms-clip.source_start_ms)/(clip.playback_rate ?? 1))/1000)); setAudio(null); }}/>) }<span className={ui.audioPlayhead} style={{left:`${playhead/duration*100}%`}} aria-hidden /></div>
    </div>)}</div></div>
    {subtitleTrack}
    {toolsHost && sharedTimeScale ? createPortal(audioToolAccess, toolsHost) : audioToolAccess}
    {toolsOpen ? <HomeCardDialog kind="selection" title="音频工具" anchor={toolsAnchor} onClose={() => setToolsOpen(false)}><div className={ui.audioToolPanel}>
    <div className={ui.audioControls} role="group" aria-label="音频导入与试听">
      {importControls}
      {listenControls}
    </div>
    {job?.context === listenContext && job.value.status !== "succeeded" ? <p role="status">{taskMediaJobLabel(job.value)}{["queued", "running", "interrupted"].includes(job.value.status) ? <button className={ui.button} type="button" onClick={() => void cancelListen()}>{job.value.result_kind === "audio_source" ? "取消音频处理" : "取消试听"}</button> : null}</p> : null}
    {audio && audio.context===listenContext && audio.start===start && audio.end===end && audio.soloKey===solo.join(",") ? <WorkspaceAudio aria-label="选区音频试听" controls src={apiUrl(audio.url)} onPlay={event=>onListenPosition?.(audio.start+event.currentTarget.currentTime)} onTimeUpdate={event=>onListenPosition?.(audio.start+event.currentTarget.currentTime)} /> : null}
    {error ? <p role="alert">{error}</p> : null}
    </div></HomeCardDialog> : null}
  </section>;
}

export function AudioTimelineInspector({ track, clip, versionId, playhead, busy, onPreview, onDraftChange }: {
  track: TimelineAudioTrack; clip: TimelineAudioClip | null; versionId: string | null; playhead: number;
  busy: boolean; onPreview: (input: AudioEditInput) => void; onDraftChange: () => void;
}) {
  const target = clip ?? track;
  const [values, setValues] = useWorkspaceViewState<Record<string, unknown>>(`audio-inspector:${versionId}:${target.id}`+".values", {});

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
