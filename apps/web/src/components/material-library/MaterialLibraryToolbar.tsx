"use client";

import { SlidersHorizontal } from "lucide-react";
import { useContext, useEffect, useState, type ReactNode } from "react";
import { MediaVisibilityContext } from "../ui/media-visibility";
import { IconButton } from "../ui/icon-button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "../ui/popover";
import styles from "./MaterialLibraryToolbar.module.css";

export function MaterialLibraryToolbar({ auxiliary, filtered, search, filters, viewActions, actions }: {
  auxiliary: boolean; filtered: boolean; search: ReactNode; filters: ReactNode; viewActions: ReactNode; actions: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const visible = useContext(MediaVisibilityContext);
  useEffect(() => { if (!visible) setOpen(false); }, [visible]);
  return <div className={`library-toolbar ${styles.toolbar}${auxiliary ? ` ${styles.auxiliary}` : ""}`}>
    {search}
    {auxiliary ? <Popover open={open} onOpenChange={setOpen}><PopoverTrigger render={<IconButton label="素材筛选与视图" description={filtered ? "调整筛选和视图，当前已启用筛选" : "调整素材筛选与视图"} />}>
      <SlidersHorizontal/><span className={styles.filterDot} hidden={!filtered}/>
    </PopoverTrigger><PopoverContent side="bottom" align="end" className={styles.filters}>
      <PopoverTitle>筛选与视图</PopoverTitle>{filters}<div className={styles.viewActions}>{viewActions}</div>
    </PopoverContent></Popover> : <>{filters}{viewActions}</>}
    {actions}
  </div>;
}
