"use client";

import type { CreationTaskMontage, CreationTaskSubtitleTrack } from "@/lib/types";
import { Button } from "./ui/button";
import ui from "./VideoEditor.module.css";

export function SubtitleTimelineTrack({ track, montage, selectedCueId, onSelect, onEdit }: {
  track?: CreationTaskSubtitleTrack; montage: Pick<CreationTaskMontage, "clips" | "duration_seconds">; selectedCueId: string | null;
  onSelect: (cueId: string, time: number) => void; onEdit?: (cueId: string) => void;
}) {
  const cues = track?.cues.filter(cue => montage.clips.some(clip =>
    cue.start_seconds >= clip.timeline_start_seconds && cue.start_seconds < clip.timeline_end_seconds));
  const selected = cues?.find(cue => cue.id === selectedCueId);
  return <>
    <div className={ui.track}><span>字幕</span><div className={ui.subtitleLane}>{cues?.map(cue => (
      <button title={cue.text} key={cue.id} type="button" style={{ left: `${cue.start_seconds / montage.duration_seconds * 100}%`, width: `${(cue.end_seconds - cue.start_seconds) / montage.duration_seconds * 100}%` }}
        aria-label={`定位字幕：${cue.text}`} aria-pressed={selected?.id === cue.id} onClick={() => onSelect(cue.id, cue.start_seconds)}>
        <span className={ui.timelineCaption}>{cue.text}</span>
      </button>
    ))}</div></div>
    {selected ? <div className={ui.timelineSelection} aria-label="所选字幕内容"><span>{selected.text}</span>{onEdit ? <Button variant="ghost" onClick={() => onEdit(selected.id)}>编辑字幕</Button> : null}</div> : null}
  </>;
}
