"use client";

import Link from "next/link";
import Image from "next/image";
import { PanelLeft, Plus } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "./ui/button";
import styles from "./DesktopHeader.module.css";

/** One native drag band; navigation, open works and global actions share its height. */
export function DesktopHeader({ nativeMac, collapsed, onToggleNavigation, tabs, location, actions }: {
  nativeMac: boolean; collapsed: boolean; onToggleNavigation: () => void;
  tabs?: ReactNode; location: ReactNode; actions: ReactNode;
}) {
  return <header className={`${styles.header}${nativeMac ? ` ${styles.mac}` : ""}`}>
    <div className={styles.brand}>
      <Link href="/" aria-label="曜核AI剪辑，返回首页" title="曜核AI剪辑"><Image alt="" aria-hidden src="/yaohe-icon.png" width={20} height={20} unoptimized/><span>曜核AI剪辑</span></Link>
      <Link href="/?new=1" className={styles.icon} aria-label="新建视频" title="新建视频"><Plus size={16}/></Link>
    </div>
    <Button variant="ghost" className={styles.icon} aria-label={collapsed ? "展开导航栏" : "收起导航栏"} title={collapsed ? "展开导航栏" : "收起导航栏"} onClick={onToggleNavigation}><PanelLeft size={16}/></Button>
    {tabs ? <div className={styles.tabs}>{tabs}</div> : null}
    <div className={`${styles.location}${tabs ? ` ${styles.withTabs}` : ""}`}>{location}</div>
    <div className={`${styles.actions} desktop-toolbar__actions`}>{actions}</div>
  </header>;
}
