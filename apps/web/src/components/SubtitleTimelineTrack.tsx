"use client";

import { useState } from "react";
import type { CreationTaskMontage, CreationTaskSubtitleTrack } from "@/lib/types";
import { Button } from "./ui/button";
import ui from "./VideoEditor.module.css";

export function SubtitleTimelineTrack({ track, montage, selectedClipId, onSelect, onEdit }: {
  track?: CreationTaskSubtitleTrack; montage: Pick<CreationTaskMontage, "clips" | "duration_seconds">; selectedClipId: string | null;
  onSelect: (clipId: string, time: number) => void; onEdit?: (cueId: string) => void;
}) {
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const selectedCue = track?.cues.find(cue => cue.id === selectedCueId);
  const selected = selectedCue && montage.clips.some(clip => clip.id === selectedClipId && selectedCue.start_seconds >= clip.timeline_start_seconds && selectedCue.start_seconds < clip.timeline_end_seconds) ? selectedCue : null;
  return <>
    <div className={ui.track}><span>字幕</span><div className={ui.subtitleLane}>{track?.cues.map(cue => {
      const clip = montage.clips.find(item => cue.start_seconds >= item.timeline_start_seconds && cue.start_seconds < item.timeline_end_seconds);
      return clip ? <button title={cue.text} key={cue.id} type="button" style={{ left: `${cue.start_seconds / montage.duration_seconds * 100}%`, width: `${(cue.end_seconds - cue.start_seconds) / montage.duration_seconds * 100}%` }}
        aria-label={`定位字幕：${cue.text}`} aria-pressed={selected?.id === cue.id} onClick={() => { setSelectedCueId(cue.id); onSelect(clip.id, cue.start_seconds); }}>
        <span className={ui.timelineCaption}>{cue.text}</span>
      </button> : null;
    })}</div></div>
    {selected ? <div className={ui.timelineSelection} aria-label="所选字幕内容"><span>{selected.text}</span>{onEdit ? <Button variant="ghost" onClick={() => onEdit(selected.id)}>编辑字幕</Button> : null}</div> : null}
  </>;
}
