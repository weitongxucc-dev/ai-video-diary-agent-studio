"use client";

import { AudioLines, Captions, Clapperboard, FileText, Film, Paperclip, Sparkles, UserRound, X } from "lucide-react";
import { useEffect, useState } from "react";
import { assistantReferenceKey, locateAssistantReference, type SelectedAssistantReference } from "@/lib/assistant-reference";
import { Button } from "./button";
import { ReferencePreview } from "./reference-preview";
import { Popover, PopoverContent, PopoverTrigger, PopoverTitle, PopoverClose } from "./popover";

const labels = { shot: "镜头", narration: "配音", audio: "音轨", subtitle: "字幕", effect: "效果", material: "素材", person: "人物", voice: "声音", knowledge: "资料" };
const icons = { shot: Clapperboard, narration: AudioLines, audio: AudioLines, subtitle: Captions, effect: Sparkles, material: Film, person: UserRound, voice: AudioLines, knowledge: FileText };
function time(ms: number) { return `${Math.floor(ms / 60000).toString().padStart(2,"0")}:${Math.floor(ms / 1000 % 60).toString().padStart(2,"0")}`; }

export function ReferenceChips({ items, onRemove }: { items: SelectedAssistantReference[]; onRemove: (item: SelectedAssistantReference)=>void }) {
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { if (!items.length) setExpanded(false); }, [items.length]);
  if (!items.length) return null;
  return <div className="assistant-reference-chips" aria-label="本轮选中引用">
    <div className="assistant-reference-attached">{items.slice(0, 3).map(item => {
      const Icon = icons[item.kind];
      return <div className="assistant-reference-capsule" key={assistantReferenceKey(item)}>
        <Button variant="ghost" className="assistant-reference-open" title={`查看${labels[item.kind]}：${item.label}`} aria-label={`查看${labels[item.kind]}：${item.label}`} onClick={() => locateAssistantReference(item)}>
          {item.kind === "material" ? <ReferencePreview assetId={item.selection.asset_id}/> : <Icon size={16} aria-hidden/>}<span>{item.label}</span>
        </Button>
        <Button variant="ghost" size="icon" aria-label={`移除引用：${item.label}`} title="移除引用" onClick={()=>onRemove(item)}><X size={12}/></Button>
      </div>;
    })}</div>
    <Popover open={expanded} onOpenChange={setExpanded}>
      <PopoverTrigger render={<Button variant="ghost"/>} className="assistant-reference-summary" aria-label={`查看本轮 ${items.length} 项引用`}>
        <Paperclip size={14} aria-hidden/><span>{items.length > 3 ? `另 ${items.length - 3} 项` : "详情"}</span>
      </PopoverTrigger>
      <PopoverContent className="assistant-reference-popover">
        <div className="assistant-reference-heading"><PopoverTitle>本轮引用 · {items.length}</PopoverTitle><PopoverClose render={<Button variant="ghost" size="icon"/>} aria-label="关闭引用列表"><X size={16}/></PopoverClose></div>
        <ul className="assistant-reference-list">{items.map(item => {
      const Icon = icons[item.kind];
      const range = "start_ms" in item && item.start_ms != null && item.end_ms != null ? ` · ${time(item.start_ms)}–${time(item.end_ms)}` : "";
      const locator = item.kind === "knowledge" && item.locator ? ` · ${item.locator}` : "";
      const version = "version" in item ? " · 已固定版本" : "";
      const name = `${labels[item.kind]} · ${item.label}${locator}${range}${version}`;
      return <li className="assistant-reference-item" key={assistantReferenceKey(item)}>
        <Button variant="ghost" className="assistant-reference-locate" title={name} aria-label={`定位${name}`} onClick={()=>{ setExpanded(false); locateAssistantReference(item); }}><Icon size={16} aria-hidden/><span>{item.label}<small>{labels[item.kind]}{locator}{range}{version}</small></span></Button>
        <Button variant="ghost" size="icon" aria-label={`取消${labels[item.kind]}引用：${item.label}${locator}${range}`} title="移除引用" onClick={()=>onRemove(item)}><X size={16} aria-hidden/></Button>
      </li>;
    })}</ul>
      </PopoverContent>
    </Popover>
  </div>;
}
