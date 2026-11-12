"use client";

// Adapted from shadcn/ui base-nova (MIT), retrieved 2026-10-18:
// https://ui.shadcn.com/r/styles/base-nova/tabs.json
// CSS Modules map the official slots/variants to DESIGN.md without adding a
// second primitive library, Tailwind, cn or class-variance-authority.
import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import styles from "./workbench-controls.module.css";

export function Tabs({ className = "", orientation = "horizontal", ...props }: TabsPrimitive.Root.Props) {
  return <TabsPrimitive.Root data-slot="tabs" data-orientation={orientation} orientation={orientation} className={`${styles.tabs} ${className}`} {...props} />;
}
export function TabsList({ className = "", variant = "default", ...props }: TabsPrimitive.List.Props & { variant?: "default" | "line" | "workspace" }) {
  return <TabsPrimitive.List data-slot="tabs-list" data-variant={variant} className={`${styles.tabsList} ${className}`} {...props} />;
}
export function TabsTrigger({ className = "", ...props }: TabsPrimitive.Tab.Props) {
  return <TabsPrimitive.Tab data-slot="tabs-trigger" className={`${styles.tabsTrigger} ${className}`} {...props} />;
}
export function TabsContent({ className = "", ...props }: TabsPrimitive.Panel.Props) {
  return <TabsPrimitive.Panel data-slot="tabs-content" className={`${styles.tabsContent} ${className}`} {...props} />;
}
