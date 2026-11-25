"use client";

import Link from "next/link";
import { useContext, type ReactNode } from "react";
import { X } from "lucide-react";
import { IconButton } from "../ui/icon-button";
import { WorkspacePanelContext, useWorkspaceSearchParams } from "./context";
import { EditorPaneMount } from "./EditorPaneMount";
import styles from "./Workbench.module.css";

export function EditorResourcePane({ title, active, docked, onClose, children, leftDock, showDrawerLink = true }: {
  title: string; active: boolean; docked: boolean; onClose: () => void; children: ReactNode;
  leftDock?: { host: HTMLElement | null; manageHref: string; manageLabel?: string };
  showDrawerLink?: boolean;
}) {
  const panel = useContext(WorkspacePanelContext);
  const hidden = !active || docked && (leftDock ? panel.resourceInspectorOpen : panel.inspectorOpen) === true;
  const searchParams = useWorkspaceSearchParams();
  const panelMode = searchParams.get("mode") === "panel";
  const pane = <section className={styles.resourcePane} hidden={hidden} aria-label={docked ? `${title}辅助区` : undefined} onClick={event => {
    const opener = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-open-details]") : null;
    if (leftDock && opener) {
      event.stopPropagation();
      panel.openResourceInspector?.(opener);
    }
  }}>
    <header className={styles.resourceHeader} hidden={!docked && (panelMode || !showDrawerLink)}>
      <span>{title}</span>
      {leftDock ? <Link className={styles.resourceLink} href={leftDock.manageHref}>{leftDock.manageLabel ?? "完整管理"}</Link> : null}
      {docked ? <IconButton label="返回作品" description="关闭资源面板" onClick={onClose}><X /></IconButton> : showDrawerLink ? <Link className={styles.resourceLink} href="/library?mode=panel&resource=materials">打开资源抽屉</Link> : null}
    </header>
    {children}
  </section>;
  return <EditorPaneMount docked={docked} hidden={hidden} dockHost={leftDock?.host} homeClassName={styles.resourceHome} mountClassName={styles.resourceMount}>{pane}</EditorPaneMount>;
}
