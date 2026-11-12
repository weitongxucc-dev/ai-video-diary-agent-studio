"use client";

import { useContext, type ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "../ui/icon-button";
import { WorkspacePanelContext } from "./context";
import { EditorPaneMount } from "./EditorPaneMount";
import styles from "./Workbench.module.css";

export function EditorResourcePane({ title, active, docked, onClose, children }: {
  title: string; active: boolean; docked: boolean; onClose: () => void; children: ReactNode;
}) {
  const panel = useContext(WorkspacePanelContext);
  const hidden = !active || docked && panel.inspectorOpen;
  const pane = <section className={styles.resourcePane} hidden={hidden} aria-label={docked ? `${title}辅助区` : undefined}>
    <header className={styles.resourceHeader} hidden={!docked}><span>{title}</span><IconButton label="返回作品" description="关闭资源面板" onClick={onClose}><X /></IconButton></header>
    {children}
  </section>;
  return <EditorPaneMount docked={docked} hidden={hidden} homeClassName={styles.resourceHome} mountClassName={styles.resourceMount}>{pane}</EditorPaneMount>;
}
