"use client";

import { Suspense, useContext } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { WorkbenchRegistry, isWorkbenchManagedPath } from "@/components/workbench/context";
import { AppShell } from "@/components/AppShell";
import { isPublicAuthPath } from "@/lib/auth/routing";

function StandaloneLoading() {
  return <AppShell><section role="status" style={{ padding: 20 }}>正在读取…</section></AppShell>;
}

function ProductionLoading() {
  const search = useSearchParams();
  return isWorkbenchManagedPath("/production", search) ? null : <StandaloneLoading />;
}

export default function Loading() {
  const registry = useContext(WorkbenchRegistry);
  const pathname = usePathname() ?? "/";
  if (isPublicAuthPath(pathname)) return <main className="auth-page"><p role="status">正在读取…</p></main>;
  if (registry && isWorkbenchManagedPath(pathname)) return null;
  if (registry && pathname === "/production") {
    return <Suspense fallback={null}><ProductionLoading /></Suspense>;
  }
  return <StandaloneLoading />;
}
