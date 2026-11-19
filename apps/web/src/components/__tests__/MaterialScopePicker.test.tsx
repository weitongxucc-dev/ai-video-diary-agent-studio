import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { MaterialScopePicker } from "../creation-task/MaterialScopePicker";
import { saveMaterialScope } from "@/lib/api/material-scope";

vi.mock("@/lib/api/materials", () => ({
  getProductionMaterials: async () => ({ assets: [], has_more: false }),
  getProductionMaterial: async (id: string) => ({
    id, file_name: id + ".mp4", updated_at: "revision-" + id, media_type: "video",
    analysis_status: "ready", review_status: "approved", duration_seconds: 20,
    use_state: "available", segments: [{ usage_role: "unassigned" }],
  }),
}));
vi.mock("@/lib/api/material-scope", () => ({
  saveMaterialScope: vi.fn(),
  readMaterialScopeReceipt: vi.fn(),
}));

it("saves the chosen reference order and each video's source start on the current work", async () => {
  const onSaved = vi.fn();
  vi.mocked(saveMaterialScope).mockResolvedValue({
    selected_material_asset_ids: ["B", "A"], request_id: "r1", task_id: "task",
    plan_id: "plan", plan_updated_at: "new-revision", selection_mode: "source_order",
    source_start_seconds_by_asset: { B: 8.033333 }, status: "saved",
  });
  render(<MaterialScopePicker taskId="task" planUpdatedAt="revision" selectedAssetIds={["A", "B"]}
    selectionMode="automatic" onSaved={onSaved} />);
  fireEvent.click(screen.getByRole("button", { name: /选材范围/ }));
  const select = await screen.findByLabelText("镜头顺序");
  await waitFor(() => expect(select).toBeEnabled());
  fireEvent.change(select, { target: { value: "source_order" } });
  fireEvent.click(screen.getByRole("button", { name: "上移 B.mp4" }));
  const start = screen.getByRole("spinbutton", { name: "B.mp4 起点（秒）" });
  const save = screen.getByRole("button", { name: "保存范围" });
  fireEvent.change(start, { target: { value: "20" } });
  expect(save).toBeDisabled();
  fireEvent.change(start, { target: { value: "8.033333" } });
  fireEvent.click(save);
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(saveMaterialScope).toHaveBeenCalledWith("task", {
    request_id: expect.any(String), expected_plan_updated_at: "revision", selection_mode: "source_order",
    assets: [
      { asset_id: "B", expected_asset_updated_at: "revision-B", source_start_seconds: 8.033333 },
      { asset_id: "A", expected_asset_updated_at: "revision-A", source_start_seconds: 0 },
    ],
  });
});
