"use client";

import { useContext, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { IconButton } from "../ui/icon-button";
import { WorkspacePanelContext } from "./context";
import { useActiveEditorDock } from "./EditorDock";
import styles from "./Workbench.module.css";

export function EditorResourcePane({ title, active, docked, onClose, children }: {
  title: string; active: boolean; docked: boolean; onClose: () => void; children: ReactNode;
}) {
  const host = useActiveEditorDock("auxiliary");
  const panel = useContext(WorkspacePanelContext);
  const home = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const hidden = !active || docked && panel.inspectorOpen;
  useLayoutEffect(() => {
    const node = document.createElement("div");
    node.className = styles.resourceMount;
    setContainer(node);
    return () => node.remove();
  }, []);
  useLayoutEffect(() => {
    const destination = docked && host ? host : home.current;
    if (container && destination && container.parentElement !== destination) destination.appendChild(container);
    if (container) container.hidden = hidden;
  }, [container, docked, host, hidden]);
  // Move the destination, not the React subtree: imports and unsaved local inputs survive.
  const pane = <section className={styles.resourcePane} hidden={hidden} aria-label={`${title}辅助区`}>
    <header className={styles.resourceHeader} hidden={!docked}><span>{title}</span><IconButton label="返回作品" description="关闭资源面板" onClick={onClose}><X /></IconButton></header>
    {children}
  </section>;
  return <><div className={styles.resourceHome} ref={home} data-docked={docked || undefined} hidden={hidden || Boolean(docked && host)}/>
    {container ? createPortal(pane, container) : null}
  </>;
}
