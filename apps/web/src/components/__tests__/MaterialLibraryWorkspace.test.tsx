import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { MaterialAsset, MaterialIntakeStatus, MaterialSegment } from "@/lib/types";
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children, inspector }: { children: React.ReactNode; inspector: React.ReactNode }) => <>{children}{inspector}</> }));
vi.mock("@/lib/api", () => ({ getProductionMaterials: vi.fn(), getMaterialLibraryInsights: vi.fn(), getMaterialIntakeStatus: vi.fn(), bindMaterialIntake: vi.fn(), scanMaterialIntake: vi.fn(), retryMaterialIntake: vi.fn(), updateMaterialUseState: vi.fn(), updateProductionMaterialSegment: vi.fn() }));
import { getProductionMaterials, getMaterialLibraryInsights, getMaterialIntakeStatus, bindMaterialIntake, scanMaterialIntake, retryMaterialIntake, updateMaterialUseState, updateProductionMaterialSegment } from "@/lib/api";
import { MaterialLibraryWorkspace } from "@/components/MaterialLibraryWorkspace";
const asset: MaterialAsset & { user_tags: string[] } = { id:"asset", workspace_id:"workspace", file_name:"local.mp4", path:"/local.mp4", size_bytes:100, user_tags:[],
  modified_at:"2026-09-20", duration_seconds:4, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0,
  usage_role_reason:"", review_status:"approved", use_state:"available", analysis_status:"ready", analysis_message:"", segments:[], created_at:"2026-09-20", updated_at:"2026-09-20" };
const library={ assets:[asset], total_count:1, limit:24, offset:0, has_more:false };
const insights={ asset_count:1, segment_count:0, tags:[], recommended_gaps:[] };
const segment: MaterialSegment = { id:"segment", asset_id:"asset", start_seconds:1, end_seconds:3, description:"画面说明", transcript:"", visual_status:"ready", visual_model_id:"", visual_sample_count:0, usage_role:"b_roll", suggested_usage_role:"b_roll", usage_role_confidence:0, usage_role_reason:"", review_status:"approved", system_tags:[], user_tags:["旧标签"], collection_ids:[], usage_count:0, last_used_at:null, created_at:"2026-09-20", updated_at:"2026-09-20" };
const taggedAsset = { ...asset, segments: [segment] };
const taggedLibrary = { ...library, assets: [taggedAsset] };
const otherSegment = { ...segment, id:"segment-b", asset_id:"asset-b", user_tags:[] };
const otherAsset = { ...taggedAsset, id:"asset-b", file_name:"自造素材B.mp4", segments:[otherSegment] };
const twoAssetLibrary = { ...library, assets:[taggedAsset, otherAsset], total_count:2 };
const intakeReady: MaterialIntakeStatus = {
  bound_to_current_workspace: false, binding_available: true, configured_directory: "/Volumes/Test/media",
  bound_directory: null, directory_state: "ready", directory_message: "素材目录可访问；文件稳定后会进入分析。",
  repair_action: null, scan_interval_seconds: 60,
  file_counts: { observing: 0, running: 0, ready: 0, failed: 0, blocked_disk: 0 },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProductionMaterials).mockResolvedValue(library);
  vi.mocked(getMaterialLibraryInsights).mockResolvedValue(insights);
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue(intakeReady);
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

it("keeps a failed tag save attached to its original asset after selecting another", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let rejectSave!: (reason: Error) => void;
  vi.mocked(updateProductionMaterialSegment).mockImplementation(() => new Promise((_, reject) => { rejectSave = reject; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value:"A片段标签" } });
  fireEvent.keyDown(input, { key:"Enter" });
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  rejectSave(new Error("A片段暂时无法保存"));
  await waitFor(() => expect(screen.getByLabelText("添加标签")).not.toBeDisabled());
  expect(screen.queryByText("A片段暂时无法保存")).not.toBeInTheDocument();
  expect(screen.getByText(/当前片段：0:01–0:03/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByLabelText("添加标签")).toHaveValue("A片段标签");
  expect(screen.getByText("A片段暂时无法保存")).toBeInTheDocument();
});

it("updates the original asset without clearing the newly selected asset after success", async () => {
  vi.mocked(getProductionMaterials).mockResolvedValue(twoAssetLibrary);
  let resolveSave!: (value: MaterialAsset) => void;
  vi.mocked(updateProductionMaterialSegment).mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
  render(<MaterialLibraryWorkspace initialLibrary={twoAssetLibrary} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  const input = screen.getByLabelText("添加标签");
  fireEvent.change(input, { target: { value:"A片段标签" } });
  fireEvent.keyDown(input, { key:"Enter" });
  fireEvent.click(screen.getByRole("button", { name:/自造素材B\.mp4/ }));
  resolveSave({ ...taggedAsset, segments:[{ ...segment, user_tags:["旧标签", "A片段标签"] }] });
  await waitFor(() => expect(screen.getByLabelText("添加标签")).not.toBeDisabled());
  expect(screen.queryByLabelText("移除标签 A片段标签")).not.toBeInTheDocument();
  expect(screen.getByLabelText("添加标签")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name:/local\.mp4/ }));
  expect(screen.getByLabelText("移除标签 A片段标签")).toBeInTheDocument();
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

it("requires confirmation of the configured directory before automatic intake", async () => {
  const bound = { ...intakeReady, bound_to_current_workspace: true, bound_directory: intakeReady.configured_directory, file_counts: { observing: 2, running: 1, ready: 3, failed: 0, blocked_disk: 0 } };
  vi.mocked(bindMaterialIntake).mockResolvedValue(bound);
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(await screen.findByText("/Volumes/Test/media")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"确认接入" }));
  await waitFor(() => expect(bindMaterialIntake).toHaveBeenCalledWith("/Volumes/Test/media"));
  expect(await screen.findByText("等待稳定 2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name:"立即检查" })).toBeInTheDocument();
});

it("does not reveal or bind a directory owned by another workspace", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, binding_available:false, configured_directory:null, directory_state:"unknown", directory_message:"素材目录已关联其他工作区；当前工作区不能读取。" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(await screen.findByText(/已接入其他工作区/)).toBeInTheDocument();
  expect(screen.queryByText("/Volumes/Test/media")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"确认接入" })).not.toBeInTheDocument();
  expect(bindMaterialIntake).not.toHaveBeenCalled();
});

it("requires a fresh confirmation if the configured directory changes during binding", async () => {
  vi.mocked(bindMaterialIntake).mockRejectedValue(new Error('{"detail":"素材目录配置已变化，请刷新后重新确认"}'));
  vi.mocked(getMaterialIntakeStatus)
    .mockResolvedValueOnce(intakeReady)
    .mockResolvedValue({ ...intakeReady, configured_directory:"/Volumes/Test/new-media" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(await screen.findByRole("button", { name:"确认接入" }));
  expect(await screen.findByText("素材目录配置已变化，请刷新后重新确认")).toBeInTheDocument();
  expect(await screen.findByText("/Volumes/Test/new-media")).toBeInTheDocument();
  expect(bindMaterialIntake).toHaveBeenCalledTimes(1);
});

it("blocks rebinding when the configured directory changed", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:"/Volumes/Old/media", directory_state:"changed", directory_message:"当前设置的素材目录与本工作区原绑定目录不同。", repair_action:"先检查目录设置并处理原绑定，再重新确认。" });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(await screen.findByText(/原接入|原绑定/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"确认接入" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
});

it("shows disk repair instead of offering a scan while the bound disk is missing", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory, directory_state:"unmounted", directory_message:"素材所在的外接盘没有挂载。", repair_action:"连接原数据盘后重试。", file_counts:{ observing:0, running:0, ready:0, failed:0, blocked_disk:1 } });
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect(await screen.findByText(/素材所在的外接盘没有挂载/)).toBeInTheDocument();
  expect(screen.getByText("等待磁盘 1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"立即检查" })).not.toBeInTheDocument();
});

it("keeps failed intake visible and retries the selected asset", async () => {
  const failed = { ...asset, analysis_status:"failed" as const, analysis_message:"本地分析服务暂不可用；检查服务后重试。" };
  const retried = { ...failed, analysis_status:"pending" as const, analysis_message:"已请求重试，等待素材稳定后重新分析。" };
  vi.mocked(getProductionMaterials).mockResolvedValue({ ...library, assets:[failed] });
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory, file_counts:{ observing:0, running:0, ready:0, failed:1, blocked_disk:0 } });
  vi.mocked(retryMaterialIntake).mockResolvedValue(retried);
  render(<MaterialLibraryWorkspace initialLibrary={{ ...library, assets:[failed] }} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  expect((await screen.findAllByText("本地分析服务暂不可用；检查服务后重试。")).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name:"重试分析" }));
  await waitFor(() => expect(retryMaterialIntake).toHaveBeenCalledWith("asset"));
  await waitFor(() => expect(screen.queryByRole("button", { name:"重试分析" })).not.toBeInTheDocument());
});

it("uses manual scan only as a secondary repair action for a bound ready directory", async () => {
  vi.mocked(getMaterialIntakeStatus).mockResolvedValue({ ...intakeReady, bound_to_current_workspace:true, bound_directory:intakeReady.configured_directory });
  vi.mocked(scanMaterialIntake).mockResolvedValue(library);
  render(<MaterialLibraryWorkspace initialLibrary={library} initialInsights={insights} initialCollections={{collections:[],total_count:0}} />);
  fireEvent.click(await screen.findByRole("button", { name:"立即检查" }));
  await waitFor(() => expect(scanMaterialIntake).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name:"找新素材" })).not.toBeInTheDocument();
});
