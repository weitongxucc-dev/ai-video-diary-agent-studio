"use client";

import { AudioLines, Captions, Clapperboard, FileText, Film, Sparkles, UserRound, X } from "lucide-react";
import { useState } from "react";
import { assistantReferenceKey, locateAssistantReference, type SelectedAssistantReference } from "@/lib/assistant-reference";

const labels = { shot: "镜头", narration: "配音", audio: "音轨", subtitle: "字幕", effect: "效果", material: "素材", person: "人物", voice: "声音", knowledge: "资料" };
const icons = { shot: Clapperboard, narration: AudioLines, audio: AudioLines, subtitle: Captions, effect: Sparkles, material: Film, person: UserRound, voice: AudioLines, knowledge: FileText };
function time(ms: number) { return `${Math.floor(ms / 60000).toString().padStart(2,"0")}:${Math.floor(ms / 1000 % 60).toString().padStart(2,"0")}`; }

export function ReferenceChips({ items, onRemove }: { items: SelectedAssistantReference[]; onRemove: (item: SelectedAssistantReference)=>void }) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return null;
  const visible = expanded ? items : items.slice(0, 3);
  return <div className="assistant-reference-chips" aria-label="本轮选中引用">
    {visible.map(item => {
      const Icon = icons[item.kind];
      const range = "start_ms" in item && item.start_ms !== undefined && item.end_ms !== undefined ? ` · ${time(item.start_ms)}–${time(item.end_ms)}` : "";
      const name = `${labels[item.kind]} · ${item.label}${range}`;
      return <span className="assistant-reference-chip" key={assistantReferenceKey(item)}>
        <button type="button" title={name} aria-label={`定位${name}`} onClick={()=>locateAssistantReference(item)}><Icon size={12} aria-hidden/><span>{item.label}{range}</span></button>
        <button type="button" aria-label={`取消${labels[item.kind]}引用：${item.label}`} title="移除引用" onClick={()=>onRemove(item)}><X size={12} aria-hidden/></button>
      </span>;
    })}
    {items.length > 3 ? <button className="assistant-reference-overflow" type="button" aria-expanded={expanded} onClick={()=>setExpanded(value=>!value)}>{expanded ? "收起" : `+${items.length-3}`}</button> : null}
  </div>;
}
