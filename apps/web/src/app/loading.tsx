"use client";

import { useContext } from "react";
import { usePathname } from "next/navigation";
import { WorkbenchRegistry, isWorkbenchManagedPath } from "@/components/workbench/context";
import { AppShell } from "@/components/AppShell";

export default function Loading() {
  const registry = useContext(WorkbenchRegistry);
  const pathname = usePathname() ?? "/";
  if (registry && isWorkbenchManagedPath(pathname)) return null;
  return <AppShell><section role="status" style={{ padding: 20 }}>正在读取…</section></AppShell>;
}
