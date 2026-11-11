"use client";

import { useRef, useState, type ReactNode } from "react";
import { ChevronUp, X } from "lucide-react";
import { Button } from "../ui/button";
import { MediaVisibility } from "../ui/media-visibility";
import styles from "../CreationTaskWorkspace.module.css";

/** Keep operation state mounted while giving the preview its height back. */
export function EditingStatusTray({ compact, status, attention, revealReason, children, actionLabel = "制作与检查" }: {
  compact: boolean; status: string; attention: boolean; revealReason?: object | null; children: ReactNode; actionLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [dismissedReason, setDismissedReason] = useState<object | null>(null);
  const expanded = open || Boolean(revealReason && revealReason !== dismissedReason);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = () => { setOpen(false); setDismissedReason(revealReason ?? null); trigger.current?.focus(); };
  return <section className={compact ? styles.statusTray : styles.statusFlow} aria-label={compact ? "制作与检查" : undefined}>
    <Button ref={trigger} hidden={!compact} variant="ghost" className={styles.statusSummary} aria-expanded={expanded} onClick={() => expanded ? close() : setOpen(true)}>
      <span className={styles.statusDot} data-attention={attention || undefined} aria-hidden />
      <span>{status}</span><span className={styles.statusAction}>{actionLabel}<ChevronUp size={14}/></span>
    </Button>
    <div className={compact ? styles.statusContents : styles.statusFlow} hidden={compact && !expanded} onKeyDown={event => {
      if (event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); close(); }
    }}>
      <header hidden={!compact}><span>制作与检查</span><Button variant="ghost" size="icon" title="收起制作与检查" aria-label="收起制作与检查" onClick={close}><X size={16}/></Button></header>
      <MediaVisibility visible={!compact || expanded}>{children}</MediaVisibility>
    </div>
  </section>;
}
