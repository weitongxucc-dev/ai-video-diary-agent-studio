"use client";
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { MediaVisibilityContext } from "../ui/media-visibility";
import { X } from "lucide-react";
import { Button } from "../ui/button";
import styles from "../EditorShell.module.css";

/** Persistent editing stays beside the work, without a modal backdrop. */
export function WorkspaceInspector({ title, open, onClose, children }: {
  title: string; open: boolean; onClose: () => void; children: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (open) {
      returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      heading.current?.focus();
    } else {
      if (returnFocus.current?.isConnected) returnFocus.current.focus();
      returnFocus.current = null;
    }
  }, [open]);
  return <aside hidden={!open} className={styles.inspectorDock} aria-label={title} onKeyDown={event => {
    if (event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); onClose(); }
  }}>
    <header><h2 ref={heading} tabIndex={-1}>{title}</h2><Button variant="ghost" size="icon" type="button" aria-label="关闭属性面板" title="关闭属性面板" onClick={onClose}><X size={16}/></Button></header>
    <MediaVisibilityContext.Provider value={open}><div className={styles.inspectorBody}>{children}</div></MediaVisibilityContext.Provider>
  </aside>;
}
