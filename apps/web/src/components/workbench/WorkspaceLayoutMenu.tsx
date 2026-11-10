"use client";
import { useState } from "react";
import { Check, Columns2 } from "lucide-react";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "../ui/popover";
import styles from "../DesktopHeader.module.css";

export function WorkspaceLayoutMenu({ conversationHidden, workspaceHidden, onConversation, onWorkspace }: {
  conversationHidden:boolean; workspaceHidden:boolean; onConversation:()=>void; onWorkspace:()=>void;
}) {
  const [open,setOpen]=useState(false);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger render={<Button variant="ghost"/>} aria-label="布局" title="布局" className={styles.icon}><Columns2 size={16}/></PopoverTrigger>
    <PopoverContent className={styles.layoutMenu} side="bottom" align="end"><PopoverTitle>布局</PopoverTitle>
      {[{label:"对话栏",hidden:conversationHidden,toggle:onConversation},{label:"作品区",hidden:workspaceHidden,toggle:onWorkspace}].map(item=><Button variant="ghost" key={item.label} aria-pressed={!item.hidden} onClick={()=>{item.toggle();setOpen(false);}}><span>{item.label}</span>{!item.hidden?<Check size={14}/>:null}</Button>)}
    </PopoverContent>
  </Popover>;
}
