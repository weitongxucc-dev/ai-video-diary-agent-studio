"use client";

import { useWorkspaceSearchParams as useSearchParams } from "@/components/workbench/context";
import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeDashboardLive } from "@/components/HomeWorkspace";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";

export const dynamic = "force-dynamic";

/**
 * 首页。
 *
 * 真实数据一律在浏览器端读取：服务端渲染拿不到 localStorage 里的成员令牌，
 * 先按 owner 预取私有任务再在客户端隐藏，等于把别人的名字发到首帧。
 * 这里不预发任何 owner 私有任务、词云或成果，只渲染容器外壳。
 */
function HomeShell() {
  const params = useSearchParams();
  return <AppShell autoFocus={params.get("new") === "1"}>
    <HomeDashboardLive />
  </AppShell>;
}

export default function HomePage() {
  return <Suspense fallback={null}><WorkspaceRoute label="首页"><HomeShell /></WorkspaceRoute></Suspense>;
}
