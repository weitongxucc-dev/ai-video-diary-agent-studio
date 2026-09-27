"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import type { CreationTaskWorkspace } from "@/lib/types";
import ui from "./VideoEditor.module.css";

type Waveform = { source_id: string; duration_seconds: number; seconds_per_peak: number; peaks: number[] };

export function NarrationWaveformTrack({ workspace, selectedId, onSelect }: {
  workspace: CreationTaskWorkspace; selectedId: string | null; onSelect: (id: string) => void;
}) {
  const taskId = workspace.task.id;
  const cacheKey = workspace.task.narration_cache_key;
  const [result, setResult] = useState<{ key: string; waveform?: Waveform; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const key = `${taskId}:${cacheKey}`;
  useEffect(() => {
    if (!cacheKey) return;
    const controller = new AbortController();
    fetch(apiUrl(`/api/creation-tasks/${encodeURIComponent(taskId)}/narration/waveform`), { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("波形读取失败");
        return response.json() as Promise<Waveform>;
      })
      .then(waveform => { if (!controller.signal.aborted) setResult({ key, waveform }); })
      .catch(() => { if (!controller.signal.aborted) setResult({ key, error: "波形读取失败" }); });
    return () => controller.abort();
  }, [taskId, cacheKey, key, retry]);
  if (!cacheKey) return null;
  const current = result?.key === key ? result : null;
  const waveform = current?.waveform;
  const montage = workspace.montage!;
  return <div className={ui.track} aria-label="真实配音波形">
    <span>配音</span>
    <div>{waveform ? montage.clips.map(clip => {
      const cue = montage.cues.find(item => item.id === clip.cue_id);
      const start = clip.timeline_start_seconds;
      const duration = clip.timeline_end_seconds - start;
      const first = Math.floor(start / waveform.seconds_per_peak);
      const last = Math.min(waveform.peaks.length, Math.ceil((start + duration) / waveform.seconds_per_peak));
      const path = waveform.peaks.slice(first, last).map((peak, index) => {
        const x = Math.max(0, (first + index) * waveform.seconds_per_peak - start);
        return `M${x},${12 - peak * 11}V${12 + peak * 11}`;
      }).join(" ");
      return <button className={ui.waveformClip} key={clip.id} type="button" style={{ flex: duration }}
        title={cue?.text} aria-label={`定位配音：${cue?.text ?? "镜头配音"}`} aria-pressed={selectedId === clip.id} onClick={() => onSelect(clip.id)}>
        <svg aria-hidden viewBox={`0 0 ${duration} 24`} preserveAspectRatio="none"><path d={path} vectorEffect="non-scaling-stroke" /></svg>
      </button>;
    }) : current?.error ? <button type="button" onClick={() => { setResult(null); setRetry(value => value + 1); }}>波形读取失败 · 重试</button>
      : <span role="status">正在读取音频波形…</span>}</div>
  </div>;
}
