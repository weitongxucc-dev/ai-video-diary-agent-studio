import { DeviceProfileRouteView } from "@/components/workbench/DeviceProfileSummary";
import { WorkspaceRoute } from "@/components/workbench/WorkspaceRoute";

export default function DeviceStatusPage() {
  return <WorkspaceRoute label="设备状态"><DeviceProfileRouteView /></WorkspaceRoute>;
}
