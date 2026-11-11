"use client";

import { Crosshair } from "lucide-react";
import { useState } from "react";
import type { CreationTaskMontage } from "@/lib/types";
import { locateAssistantReference, type SelectedAssistantReference } from "@/lib/assistant-reference";
import { Button } from "./button";
import { Popover, PopoverContent, PopoverTrigger, PopoverTitle } from "./popover";

export type MaterialUsageContext = {
  taskId: string; taskName: string; versionId: string;
  clips: CreationTaskMontage["clips"]; selectedClipId: string | null;
};

export function matchingMaterialClips(reference: SelectedAssistantReference, context: MaterialUsageContext) {
  return reference.kind !== "material" ? [] : context.clips.flatMap((clip, index) =>
    clip.asset_id === reference.selection.asset_id && (reference.selection.scope !== "segment" || clip.segment_id === reference.selection.segment_id)
      ? [{ clip, index }] : []);
}

function time(seconds: number) {
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

/** Current adopted timeline facts, never inferred from model wording or past selections. */
export function MaterialUsage({ reference, context }: { reference: SelectedAssistantReference; context?: MaterialUsageContext }) {
  const [open, setOpen] = useState(false);
  if (!context || reference.kind !== "material") return null;
  const uses = matchingMaterialClips(reference, context);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger render={<Button variant="ghost"/>} className="assistant-material-uses" aria-label={`查看${reference.label}在当前作品中的${uses.length}处使用`}>
      <Crosshair size={13} aria-hidden/>{uses.length ? `${uses.length}处` : "未使用"}
    </PopoverTrigger>
    <PopoverContent className="assistant-reference-popover">
      <PopoverTitle>{context.taskName}</PopoverTitle>
      <p className="assistant-source-scope">当前时间线中的使用位置</p>
      {uses.length ? <ul className="assistant-material-use-list">{uses.map(({ clip, index }) => <li key={clip.id}>
        <Button variant="ghost" aria-pressed={context.selectedClipId === clip.id} onClick={() => { setOpen(false); locateAssistantReference({
          kind: "shot", task_id: context.taskId, version: context.versionId, object_id: clip.id, label: `镜头 ${index + 1}`,
          start_ms: Math.round(clip.timeline_start_seconds * 1000), end_ms: Math.round(clip.timeline_end_seconds * 1000),
        }); }}>
          <span>镜头 {String(index + 1).padStart(2, "0")} · {time(clip.timeline_start_seconds)}–{time(clip.timeline_end_seconds)}</span>
          <small>源片 {time(clip.source_start_seconds)}–{time(clip.source_end_seconds)}</small>
        </Button>
      </li>)}</ul> : <p>当前作品还没有使用这条素材；引用到对话不会自动加入时间线。</p>}
    </PopoverContent>
  </Popover>;
}
