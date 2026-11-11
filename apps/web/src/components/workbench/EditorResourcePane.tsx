"use client";

import { useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { IconButton } from "../ui/icon-button";
import { WorkspacePanelContext } from "./context";
import { useActiveEditorDock } from "./EditorDock";
import styles from "./Workbench.module.css";

export function EditorResourcePane({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const host = useActiveEditorDock("auxiliary");
  const panel = useContext(WorkspacePanelContext);
  const pane = <section className={styles.resourcePane} hidden={panel.inspectorOpen} aria-label={`${title}辅助区`}>
    <header className={styles.resourceHeader}><span>{title}</span><IconButton label="返回作品" description="关闭资源面板" onClick={onClose}><X /></IconButton></header>
    {children}
  </section>;
  return host ? createPortal(pane, host) : pane;
}
