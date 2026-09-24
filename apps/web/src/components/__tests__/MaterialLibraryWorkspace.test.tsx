import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { MaterialAsset, MaterialSegment } from "@/lib/types";
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
vi.mock("@/lib/api", () => ({ getProductionMaterials: vi.fn(), getMaterialLibraryInsights: vi.fn(), updateMaterialUseState: vi.fn(), updateProductionMaterialSegment: vi.fn() }));
import { getProductionMaterials, getMaterialLibraryInsights, updateMaterialUseState, updateProductionMaterialSegment } from "@/lib/api";
import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
const asset: MaterialAsset = { id:"asset", workspace_id:"workspace", file_name:"local.mp4", path:"/local.mp4", size_bytes:100,
  modified_at:"2026-09-20", duration_seconds:4, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0,
  usage_role_reason:"", review_status:"approved", use_state:"available", analysis_status:"ready", analysis_message:"", segments:[], created_at:"2026-09-20", updated_at:"2026-09-20" };
const library={ assets:[asset], total_count:1, limit:24, offset:0, has_more:false };
const insights={ asset_count:1, segment_count:0, tags:[], recommended_gaps:[] };
const segment: MaterialSegment = { id:"segment", asset_id:"asset", start_seconds:1, end_seconds:3, description:"画面说明", transcript:"", visual_status:"ready", visual_model_id:"", visual_sample_count:0, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0, usage_role_reason:"", review_status:"approved", system_tags:[], user_tags:["旧标签"], collection_ids:[], usage_count:0, last_used_at:null, created_at:"2026-09-20", updated_at:"2026-09-20" };
const taggedAsset = { ...asset, segments: [segment] };
const taggedLibrary = { ...library, assets: [taggedAsset] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProductionMaterials).mockResolvedValue(library);
  vi.mocked(getMaterialLibraryInsights).mockResolvedValue(insights);
});
it("persists the selected restriction and removes obsolete proof displays", async () => {
  vi.mocked(updateMaterialUseState).mockResolvedValue({...asset,use_state:"reference_only"});
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button",{name:"仅参考"}));
  await waitFor(() => expect(updateMaterialUseState).toHaveBeenCalledWith("asset","reference_only"));
  await waitFor(() => expect(screen.getByRole("button",{name:"仅参考"})).toHaveAttribute("aria-pressed","true"));
  expect(screen.queryByText("版权确认")).not.toBeInTheDocument();
  expect(screen.queryByText("品牌安全")).not.toBeInTheDocument();
});
it("retains the saved restriction and shows failure if persistence fails", async () => {
  vi.mocked(updateMaterialUseState).mockRejectedValue(new Error("外接盘暂不可写"));
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByRole("button",{name:"不使用"}));
  await screen.findByText("外接盘暂不可写");
  expect(screen.getByRole("button",{name:"可用于创作"})).toHaveAttribute("aria-pressed","true");
});

it("adds a tag only after the segment update succeeds and keeps the time range visible", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, user_tags: ["旧标签", "产品演示"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(screen.getByText(/当前片段：0:01–0:03/)).toBeInTheDocument();
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value: "产品演示" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ user_tags: ["旧标签", "产品演示"], description: "画面说明" })));
  await waitFor(() => expect(screen.getByLabelText("移除标签 产品演示")).toBeInTheDocument());
  expect(input).toHaveValue("");
});

it("keeps a saved tag when the separate insights refresh fails", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(getMaterialLibraryInsights).mockRejectedValue(new Error("统计暂不可用"));
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, user_tags: ["旧标签", "产品演示"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value: "产品演示" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("移除标签 产品演示")).toBeInTheDocument());
  await waitFor(() => expect(getMaterialLibraryInsights).toHaveBeenCalled());
  expect(input).toHaveValue("");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("does not submit while Chinese input is composing and preserves input on failure", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockRejectedValue(new Error('{"detail":"暂时无法保存"}'));
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "人物" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(updateProductionMaterialSegment).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter" });
  await screen.findByText("暂时无法保存");
  expect(input).toHaveValue("人物");
  expect(screen.queryByLabelText("移除标签 人物")).not.toBeInTheDocument();
});

it("removes a tag through the segment API and leaves it visible if saving fails", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockRejectedValue(new Error("暂时无法保存"));
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(screen.getByLabelText("移除标签 旧标签"));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ user_tags: [] })));
  await screen.findByText("暂时无法保存");
  expect(screen.getByLabelText("移除标签 旧标签")).toBeInTheDocument();
});

it("filters folders and saves the selected folder for the current segment", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(taggedLibrary);
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue({ ...taggedAsset, segments: [{ ...segment, collection_ids: ["folder-2"] }] });
  render(<MaterialLibraryWorkspace initialLibrary={taggedLibrary} initialInsights={insights} initialCollections={{ collections: [
    { id:"folder-1", workspace_id:"workspace", name:"人物", parent_id:null, created_at:"2026-09-20", updated_at:"2026-09-20" },
    { id:"folder-2", workspace_id:"workspace", name:"产品", parent_id:null, created_at:"2026-09-20", updated_at:"2026-09-20" },
  ], total_count:2 }} />);
  fireEvent.change(screen.getByRole("searchbox", { name:"标签夹" }), { target: { value:"产品" } });
  expect(screen.queryByRole("button", { name:"人物" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"产品" }));
  fireEvent.click(screen.getByRole("button", { name:"保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ collection_ids:["folder-2"] })));
});
