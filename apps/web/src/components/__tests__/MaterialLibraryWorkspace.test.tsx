import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { MaterialAsset } from "@/lib/types";
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
vi.mock("@/lib/api", () => ({ getProductionMaterials: vi.fn(), getMaterialLibraryInsights: vi.fn(), updateMaterialUseState: vi.fn() }));
import { getProductionMaterials, getMaterialLibraryInsights, updateMaterialUseState } from "@/lib/api";
import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
const asset: MaterialAsset = { id:"asset", workspace_id:"workspace", file_name:"local.mp4", path:"/local.mp4", size_bytes:100,
  modified_at:"2026-09-20", duration_seconds:4, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0,
  usage_role_reason:"", review_status:"approved", use_state:"available", analysis_status:"ready", analysis_message:"", segments:[], created_at:"2026-09-20", updated_at:"2026-09-20" };
const library={ assets:[asset], total_count:1, limit:24, offset:0, has_more:false };
const insights={ asset_count:1, segment_count:0, tags:[], recommended_gaps:[] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProductionMaterials).mockResolvedValue(library);
  vi.mocked(getMaterialLibraryInsights).mockResolvedValue(insights);
});
it("persists the selected restriction and removes obsolete proof displays", async () => {
  vi.mocked(updateMaterialUseState).mockResolvedValue({...asset,use_state:"reference_only"});
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("素材使用状态"),{target:{value:"reference_only"}});
  await waitFor(() => expect(updateMaterialUseState).toHaveBeenCalledWith("asset","reference_only"));
  await waitFor(() => expect(screen.getByLabelText("素材使用状态")).toHaveValue("reference_only"));
  expect(screen.queryByText("版权确认")).not.toBeInTheDocument();
  expect(screen.queryByText("品牌安全")).not.toBeInTheDocument();
});
it("retains the saved restriction and shows failure if persistence fails", async () => {
  vi.mocked(updateMaterialUseState).mockRejectedValue(new Error("外接盘暂不可写"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.change(screen.getByLabelText("素材使用状态"),{target:{value:"excluded"}});
  await screen.findByText("外接盘暂不可写");
  expect(screen.getByLabelText("素材使用状态")).toHaveValue("available");
});
