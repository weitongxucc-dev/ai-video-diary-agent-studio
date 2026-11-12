"use client";

import { Clapperboard, CornerUpLeft, X } from "lucide-react";
import { Button } from "../ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu";

export function ConversationTarget({ name, onReturn, onClear }: { name: string; onReturn: () => void; onClear: () => void }) {
  return <DropdownMenu>
    <DropdownMenuTrigger render={<Button variant="ghost" className="conversation-work-target" title={name} aria-label={`当前作品：${name}`}/>}><Clapperboard size={14}/><span>当前作品 · {name}</span></DropdownMenuTrigger>
    <DropdownMenuContent align="end" aria-label="当前操作目标">
      <DropdownMenuItem onClick={onReturn}><span><CornerUpLeft size={14}/> {name}</span></DropdownMenuItem>
      <DropdownMenuItem onClick={onClear}><span><X size={14}/> 取消当前作品目标</span></DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
