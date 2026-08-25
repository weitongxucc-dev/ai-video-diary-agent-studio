import Link from "next/link";
import { Film } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { MontageReviewWorkspace } from "@/components/MontageReviewWorkspace";
import { getMontagePlans } from "@/lib/api.server";

export const dynamic = "force-dynamic";

export default async function EpisodeMontagePage({ params }: { params: Promise<{ batchId: string; episodeId: string }> }) {
  const { batchId, episodeId } = await params;
  try {
    const response = await getMontagePlans(batchId);
    const plan = response.plans.find((item) => item.episode_id === episodeId);
    if (plan) return <MontageReviewWorkspace initialPlan={plan} />;
  } catch { /* Render recoverable state below. */ }
  return <AppShell><div className="montage-route-empty"><Film size={24} /><div><h1>还没有混剪计划</h1><p>先回到生产批次，为这条 Episode 生成配音和混剪时间线。</p></div><Link href={`/production?batch=${batchId}`}>返回生产批次</Link></div></AppShell>;
}
