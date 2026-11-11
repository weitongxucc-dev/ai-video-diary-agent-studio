import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ASSISTANT_LOCATE_REFERENCE_EVENT } from "@/lib/assistant-reference";
import { MaterialUsage, type MaterialUsageContext } from "../ui/material-usage";

afterEach(cleanup);
const clip = { id: "clip-one", asset_id: "asset-one", segment_id: "segment-one", cue_id: "cue-one", source_reference: "workspace://source.mp4", source_start_seconds: 4, source_end_seconds: 6, timeline_start_seconds: 0, timeline_end_seconds: 2, match_score: 1, match_reasons: [], needs_confirmation: false, role: "b_roll" as const, locked: false, manually_selected: false };
const context: MaterialUsageContext = { taskId: "current-task", taskName: "当前作品", versionId: "current-version", selectedClipId: null, clips: [clip, { ...clip, id: "clip-two", segment_id: "segment-two", timeline_start_seconds: 2, timeline_end_seconds: 4 }] };

it("locates an actual usage in the current timeline without changing the frozen material reference", async () => {
  const reference = { kind: "material" as const, label: "原始素材", selection: { scope: "asset" as const, asset_id: "asset-one", expected_updated_at: "frozen-material-version" } };
  const locate = vi.fn(); window.addEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
  try {
    render(<MaterialUsage reference={reference} context={context}/>);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "查看原始素材在当前作品中的2处使用" }));
    await user.click(screen.getByRole("button", { name: /镜头 02/ }));
    expect(locate).toHaveBeenCalledOnce();
    expect((locate.mock.calls[0][0] as CustomEvent).detail).toMatchObject({ kind: "shot", task_id: "current-task", version: "current-version", object_id: "clip-two", start_ms: 2000, end_ms: 4000 });
    expect(reference.selection.expected_updated_at).toBe("frozen-material-version");
  } finally { window.removeEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate); }
});

it("distinguishes segment usage from another segment of the same asset", async () => {
  render(<MaterialUsage reference={{ kind: "material", label: "片段一", selection: { scope: "segment", asset_id: "asset-one", segment_id: "segment-one" } }} context={context}/>);
  await userEvent.setup().click(screen.getByRole("button", { name: "查看片段一在当前作品中的1处使用" }));
  expect(screen.getByRole("button", { name: /镜头 01/ })).toBeVisible();
  expect(screen.queryByRole("button", { name: /镜头 02/ })).not.toBeInTheDocument();
});
