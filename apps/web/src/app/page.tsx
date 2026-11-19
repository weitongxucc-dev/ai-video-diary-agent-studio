import { redirect } from "next/navigation";
import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { HomeDashboardLive } from "@/components/HomeWorkspace";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";
import { NEW_VIDEO_HREF } from "@/lib/preflight-draft-url";

export const dynamic = "force-dynamic";

/**
 * 首页。
 *
 * 真实数据一律在浏览器端读取：服务端渲染拿不到 localStorage 里的成员令牌，
 * 先按 owner 预取私有任务再在客户端隐藏，等于把别人的名字发到首帧。
 * 这里不预发任何 owner 私有任务、词云或成果，只渲染容器外壳。
 */
function HomeShell() {
  return <AppShell>
    <HomeDashboardLive />
  </AppShell>;
}

export default async function HomePage({ searchParams }: { searchParams?: Promise<{ new?: string }> }) {
  if ((await searchParams)?.new === "1") redirect(NEW_VIDEO_HREF);
  return <Suspense fallback={null}><WorkspaceRoute label="首页"><HomeShell /></WorkspaceRoute></Suspense>;
}
