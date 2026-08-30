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
  return <AppShell><div className="montage-route-empty"><Film size={24} /><div><h1>还没有视频剪辑</h1><p>先回到创作任务，为这条内容生成旁白和视频剪辑。</p></div><Link href={`/production?batch=${batchId}`}>返回创作任务</Link></div></AppShell>;
}
