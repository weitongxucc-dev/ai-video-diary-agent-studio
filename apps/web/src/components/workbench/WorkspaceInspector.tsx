"use client";
import Link from "next/link";
import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { MediaVisibility } from "../ui/media-visibility";
import { X } from "lucide-react";
import { Button } from "../ui/button";
import styles from "../EditorShell.module.css";
import resourceStyles from "./Workbench.module.css";

/** Persistent editing stays beside the work, without a modal backdrop. */
export function WorkspaceInspector({ title, open, onClose, children, manageHref, opener }: {
  title: string; open: boolean; onClose: () => void; children: ReactNode;
  manageHref?: string;
  opener?: RefObject<HTMLElement | null>;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (open) {
      returnFocus.current = opener?.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
      heading.current?.focus();
      return;
    }
    const target = returnFocus.current;
    returnFocus.current = null;
    if (!target?.isConnected) return;
    if (!target.closest("[hidden]")) {
      target.focus({ preventScroll: true });
      return;
    }
    // The resource list portal finishes unhiding in its own layout effect.
    if (!opener) return;
    const frame = requestAnimationFrame(() => {
      if (target.isConnected && !target.closest("[hidden]")) target.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [open, opener]);
  return <aside hidden={!open} className={styles.inspectorDock} aria-label={title} onKeyDown={event => {
    if (event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); onClose(); }
  }}>
    <header><h2 ref={heading} tabIndex={-1}>{title}</h2>{manageHref ? <Link className={resourceStyles.resourceLink} href={manageHref}>完整管理</Link> : null}<Button variant="ghost" size="icon" type="button" aria-label={manageHref ? "返回资源列表" : "关闭属性面板"} title={manageHref ? "返回资源列表" : "关闭属性面板"} onClick={onClose}><X size={16}/></Button></header>
    <MediaVisibility visible={open}><div className={styles.inspectorBody}>{children}</div></MediaVisibility>
  </aside>;
}
