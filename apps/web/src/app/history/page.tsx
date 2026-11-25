import { PublishingRouteView } from "@/components/PublishingSummary";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";

export const dynamic = "force-dynamic";

export default function HistoryPage() {
  return <WorkspaceRoute label="发布与数据"><PublishingRouteView /></WorkspaceRoute>;
}
