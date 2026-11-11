"use client";

import { useContext } from "react";
import { usePathname } from "next/navigation";
import { WorkbenchRegistry, isWorkbenchManagedPath } from "@/components/workbench/context";
import { AppShell } from "@/components/AppShell";

export default function Loading() {
  const registry = useContext(WorkbenchRegistry);
  const pathname = usePathname() ?? "/";
  if (registry && isWorkbenchManagedPath(pathname)) return null;
  return <AppShell><section className="ui-panel" role="status" style={{ margin: 20, padding: 20 }}><h2 style={{ fontSize: 16, fontWeight: 500 }}>正在读取…</h2><p>你的素材和任务马上就来。</p></section></AppShell>;
}
