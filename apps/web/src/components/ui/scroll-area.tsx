"use client";

// shadcn/ui base-nova ScrollArea (MIT), local CSS/token adaptation:
// https://ui.shadcn.com/r/styles/base-nova/scroll-area.json (2026-10-18).
import { ScrollArea as ScrollAreaPrimitive } from "@base-ui/react/scroll-area";
import styles from "./workbench-controls.module.css";

export function ScrollArea({ className = "", children, ...props }: ScrollAreaPrimitive.Root.Props) {
  return <ScrollAreaPrimitive.Root data-slot="scroll-area" className={`${styles.scrollArea} ${className}`} {...props}>
    <ScrollAreaPrimitive.Viewport data-slot="scroll-area-viewport" className={styles.scrollViewport}>{children}</ScrollAreaPrimitive.Viewport>
    <ScrollBar orientation="horizontal" />
    <ScrollAreaPrimitive.Corner />
  </ScrollAreaPrimitive.Root>;
}
export function ScrollBar({ className = "", orientation = "vertical", ...props }: ScrollAreaPrimitive.Scrollbar.Props) {
  return <ScrollAreaPrimitive.Scrollbar data-slot="scroll-area-scrollbar" orientation={orientation} className={`${styles.scrollbar} ${className}`} {...props}>
    <ScrollAreaPrimitive.Thumb data-slot="scroll-area-thumb" className={styles.scrollThumb} />
  </ScrollAreaPrimitive.Scrollbar>;
}
