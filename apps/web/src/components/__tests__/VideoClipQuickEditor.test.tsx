import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useState } from "react";
import type { CreationTaskMontageClip, CreationTaskWorkspace } from "@/lib/types";
import { createVideoClipDraft, VideoClipInspector, VideoClipQuickEditor } from "@/components/VideoEditor";

const clip = {
  id: "clip-1", asset_id: "asset-1", source_reference: "workspace://clip.mp4",
  source_start_seconds: 1, source_end_seconds: 4, timeline_start_seconds: 0, timeline_end_seconds: 3,
  match_score: .9, role: "b_roll", locked: false, needs_confirmation: false,
} as CreationTaskMontageClip;

it("keeps a clip draft across categories and previews only typed changes", () => {
  const onPreview = vi.fn();
  const onOpenDetails = vi.fn();
  const onDraftChange = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(createVideoClipDraft(clip));
    return <VideoClipQuickEditor clip={clip} index={0} busy={false} draft={draft} onDraftStateChange={setDraft} onDraftChange={onDraftChange} onPreview={onPreview} onOpenDetails={onOpenDetails} />;
  }
  render(<Harness />);
  const preview = screen.getByRole("button", { name:"预览修改" });
  expect(preview).toBeDisabled();
  fireEvent.change(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" }), { target:{ value:"1.5" } });
  expect(preview).not.toBeDisabled();
  fireEvent.click(preview);
  expect(onPreview).toHaveBeenCalledWith({ operation_type:"TrimClip", clip_id:"clip-1", source_start_seconds:1.5 });
  fireEvent.click(screen.getByRole("button", { name:"画面" }));
  fireEvent.change(screen.getByRole("slider", { name:"镜头 1 快速裁切水平位置" }), { target:{ value:"0.7" } });
  expect(screen.getByRole("slider", { name:"镜头 1 快速裁切水平位置" })).toHaveValue("0.7");
  fireEvent.click(screen.getByRole("button", { name:"镜头 1 快速画面方式" }));
  fireEvent.click(screen.getByRole("option", { name:"完整显示" }));
  fireEvent.click(screen.getByRole("button", { name:"预览修改" }));
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeCrop", clip_id:"clip-1", fit_mode:"fit", crop_x:.7, crop_y:.5 });
  fireEvent.click(screen.getByRole("button", { name:"裁剪" }));
  expect(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" })).toHaveValue(1.5);
  fireEvent.click(screen.getByRole("button", { name:"换素材" }));
  fireEvent.click(screen.getByRole("button", { name:"查看候选" }));
  expect(onOpenDetails).toHaveBeenCalledTimes(1);
  expect(onPreview).toHaveBeenCalledTimes(2);
  expect(onDraftChange).toHaveBeenCalledTimes(3);
});

it("opens the full editor with the same crop draft", () => {
  const initialDraft = { ...createVideoClipDraft(clip), start: 1.5, mode:"fit" as const };
  const onDraftStateChange = vi.fn();
  const workspace = { task:{ id:"task-1" }, montage:{ clips:[clip], duration_seconds:3, graphic_overlays:[] }, subtitle_tracks:[], person_renders:[], person_render_capability:{ available:false, message:"", repair_action:null } } as unknown as CreationTaskWorkspace;
  render(<VideoClipInspector clip={clip} index={0} count={1} workspace={workspace} busy={false} initialDraft={initialDraft} onDraftStateChange={onDraftStateChange} onPreview={vi.fn()} onDraftChange={vi.fn()} onReplace={vi.fn()} candidates={[]} replacing={false} onPerson={vi.fn()} />);
  expect(screen.getByRole("spinbutton", { name:"镜头 1 裁剪起点" })).toHaveValue(1.5);
  expect(screen.getByRole("combobox", { name:"画面显示方式" })).toHaveValue("fit");
  fireEvent.change(screen.getByRole("spinbutton", { name:"镜头 1 裁剪起点" }), { target:{ value:"2" } });
  expect(onDraftStateChange).toHaveBeenLastCalledWith({ start:2, mode:"fit", x:.5, y:.5 });
});

it("labels the float card draft state honestly and never fakes a replacement preview", () => {
  const onPreview = vi.fn();
  const onOpenDetails = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(createVideoClipDraft(clip));
    return <VideoClipQuickEditor clip={clip} index={0} busy={false} draft={draft} onDraftStateChange={setDraft} onDraftChange={() => {}} onPreview={onPreview} onOpenDetails={onOpenDetails} />;
  }
  render(<Harness />);
  // 裁剪 tab 未改：已保存
  expect(screen.getByText("当前设置已保存")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" }), { target:{ value:"1.5" } });
  expect(screen.getByText("修改尚未应用")).toBeInTheDocument();
  // 换素材 tab：诚实委托详情，不伪造“预览修改”
  fireEvent.click(screen.getByRole("button", { name:"换素材" }));
  expect(screen.getByText("需在详情选择候选")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"预览修改" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name:"查看候选" }));
  expect(onOpenDetails).toHaveBeenCalledTimes(1);
  expect(onPreview).not.toHaveBeenCalled();
});

it("disables every fine-edit control for a locked clip", () => {
  const lockedClip = { ...clip, locked: true };
  function Harness() {
    const [draft, setDraft] = useState(createVideoClipDraft(lockedClip));
    return <VideoClipQuickEditor clip={lockedClip} index={0} busy={false} draft={draft} onDraftStateChange={setDraft} onDraftChange={() => {}} onPreview={vi.fn()} onOpenDetails={vi.fn()} />;
  }
  render(<Harness />);
  expect(screen.getByText("镜头已锁定")).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" })).toBeDisabled();
  expect(screen.getByRole("button", { name:"预览修改" })).toBeDisabled();
});
