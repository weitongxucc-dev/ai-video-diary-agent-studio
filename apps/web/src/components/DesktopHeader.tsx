"use client";

import Link from "next/link";
import { PanelLeft } from "lucide-react";
import type { ReactNode, Ref } from "react";
import { IconButton } from "./ui/icon-button";
import styles from "./DesktopHeader.module.css";

/** One native drag band; navigation, open works and global actions share its height. */
export function DesktopHeader({ nativeMac, navigationExpanded, navigationId, navigationTrigger, onToggleNavigation, tabs, location, actions }: {
  nativeMac: boolean; navigationExpanded: boolean; navigationId: string; navigationTrigger: Ref<HTMLButtonElement>;
  onToggleNavigation: () => void;
  tabs?: ReactNode; location: ReactNode; actions: ReactNode;
}) {
  return <header className={`${styles.header}${nativeMac ? ` ${styles.mac}` : ""}`}>
    <div className={styles.brand}>
      <Link href="/" aria-label="曜核AI剪辑，返回首页" title="曜核AI剪辑"><span>曜核AI剪辑</span></Link>
      <IconButton className={styles.icon} label={navigationExpanded ? "收起导航栏" : "展开导航栏"} aria-expanded={navigationExpanded} aria-controls={navigationId} ref={navigationTrigger} onClick={onToggleNavigation}><PanelLeft size={16}/></IconButton>
    </div>
    {tabs ? <div className={styles.tabs}>{tabs}</div> : null}
    {!tabs ? <div className={styles.location}>{location}</div> : null}
    <div className={`${styles.actions} desktop-toolbar__actions`}>{actions}</div>
  </header>;
}
