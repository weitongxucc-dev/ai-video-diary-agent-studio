"use client";

import Link from "next/link";
import { PanelLeft } from "lucide-react";
import type { ReactNode } from "react";
import { IconButton } from "./ui/icon-button";
import styles from "./DesktopHeader.module.css";

/** One native drag band; navigation, open works and global actions share its height. */
export function DesktopHeader({ nativeMac, collapsed, onToggleNavigation, tabs, location, actions }: {
  nativeMac: boolean; collapsed: boolean; onToggleNavigation: () => void;
  tabs?: ReactNode; location: ReactNode; actions: ReactNode;
}) {
  return <header className={`${styles.header}${nativeMac ? ` ${styles.mac}` : ""}`}>
    <div className={styles.brand}>
      <Link href="/" aria-label="曜核AI剪辑，返回首页" title="曜核AI剪辑"><span>曜核AI剪辑</span></Link>
      <IconButton className={styles.icon} label={collapsed ? "展开导航栏" : "收起导航栏"} onClick={onToggleNavigation}><PanelLeft size={16}/></IconButton>
    </div>
    {tabs ? <div className={styles.tabs}>{tabs}</div> : null}
    {!tabs ? <div className={styles.location}>{location}</div> : null}
    <div className={`${styles.actions} desktop-toolbar__actions`}>{actions}</div>
  </header>;
}
