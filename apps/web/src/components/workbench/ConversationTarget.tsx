"use client";

import { Clapperboard, CornerUpLeft, X } from "lucide-react";
import { IconButton } from "../ui/icon-button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu";

export function ConversationTarget({ name, onReturn, onClear }: { name: string; onReturn: () => void; onClear: () => void }) {
  return <DropdownMenu>
    <DropdownMenuTrigger render={<IconButton label={name} description={`当前作品：${name}`} />}><Clapperboard /></DropdownMenuTrigger>
    <DropdownMenuContent align="end" aria-label="当前操作目标">
      <DropdownMenuItem onClick={onReturn}><span><CornerUpLeft size={14}/> {name}</span></DropdownMenuItem>
      <DropdownMenuItem onClick={onClear}><span><X size={14}/> 取消当前作品目标</span></DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
