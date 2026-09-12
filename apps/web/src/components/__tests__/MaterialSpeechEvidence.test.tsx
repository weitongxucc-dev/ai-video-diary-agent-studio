import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import type { MaterialAsset, MaterialLibraryInsights } from "@/lib/types";
import { MaterialLibraryWorkspace } from "../MaterialLibraryWorkspace";
import { updateProductionMaterialSegment } from "@/lib/api";

vi.mock("../AppShell", () => ({ AppShell: ({ children, inspector }: { children: ReactNode; inspector: ReactNode }) => <>{children}{inspector}</> }));
vi.mock("@/lib/api", () => ({
  getProductionMaterials: vi.fn(() => new Promise(() => {})),
  getMaterialLibraryInsights: vi.fn(async () => ({ tags: [], recommended_gaps: [], asset_count: 1, segment_count: 1 })),
  updateProductionMaterialSegment: vi.fn(),
}));

it("seeks original speech evidence and saves a user correction without replacing the evidence", async () => {
  const asset = {
    id: "asset", file_name: "source.mp4", size_bytes: 1000, duration_seconds: 10,
    usage_role: "b_roll", review_status: "approved", analysis_status: "ready",
    speech_analysis: { status: "ready", observations: [{ start_ms: 6000, end_ms: 8000, text: "USB port" }], message: "本地转写" },
    segments: [{ id: "segment", start_seconds: 5, end_seconds: 10, description: "接口", transcript: "", usage_role: "b_roll",
      understanding: { status: "ready", summary: "桌面上的电脑", sample_times: [5.75, 7.5, 9.25],
        facts: [{ kind: "object", text: "电脑", sample_times: [7.5] }], unknowns: [], ocr_status: "ready",
        ocr_frames: [{ timestamp_seconds: 9.25, observations: [{ text: "模糊文字", confidence: .3, retrieval_eligible: false }] }] },
      review_status: "approved", user_tags: [], system_tags: [], collection_ids: [], usage_count: 0 }],
  } as unknown as MaterialAsset;
  vi.mocked(updateProductionMaterialSegment).mockResolvedValue(asset);
  render(<MaterialLibraryWorkspace initialLibrary={{ assets: [asset], total_count: 1, limit: 20, offset: 0, has_more: false }}
    initialInsights={{ tags: [], recommended_gaps: [], asset_count: 1, segment_count: 1 } as unknown as MaterialLibraryInsights}
    initialCollections={{ collections: [], total_count: 0 }} />);
  const video = screen.getByLabelText("原素材预览") as HTMLVideoElement;
  fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(5);
  fireEvent.click(screen.getByRole("button", { name: /USB port/ }));
  expect(video.currentTime).toBe(6);
  fireEvent.click(screen.getByRole("button", { name: "查看采样帧 7.50 秒" }));
  expect(video.currentTime).toBe(7.5);
  fireEvent.click(screen.getByText(/模糊文字 · 低置信度/));
  expect(video.currentTime).toBe(9.25);
  fireEvent.change(screen.getByLabelText("转写纠正（留空使用识别结果）"), { target: { value: "HDMI port" } });
  fireEvent.click(screen.getByRole("button", { name: "保存片段修改" }));
  await waitFor(() => expect(updateProductionMaterialSegment).toHaveBeenCalledWith("asset", "segment", expect.objectContaining({ transcript: "HDMI port", description: "接口" })));
  expect(screen.getByRole("button", { name: /USB port/ })).toBeTruthy();
});
