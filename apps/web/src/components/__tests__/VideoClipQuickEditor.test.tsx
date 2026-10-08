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

it("keeps a clip draft across categories and applies valid changes on completion", () => {
  const onPreview = vi.fn();
  const onOpenDetails = vi.fn();
  const onDraftChange = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(createVideoClipDraft(clip));
    return <VideoClipQuickEditor clip={clip} index={0} busy={false} draft={draft} onDraftStateChange={setDraft} onDraftChange={onDraftChange} onPreview={onPreview} onOpenDetails={onOpenDetails} />;
  }
  render(<Harness />);
  const trimStart = screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" });
  fireEvent.change(trimStart, { target:{ value:"" } });
  fireEvent.blur(trimStart);
  expect(onPreview).not.toHaveBeenCalled();
  fireEvent.change(trimStart, { target:{ value:"1.5" } });
  expect(onPreview).not.toHaveBeenCalled();
  fireEvent.blur(trimStart);
  expect(onPreview).toHaveBeenCalledWith({ operation_type:"TrimClip", clip_id:"clip-1", source_start_seconds:1.5 });
  fireEvent.click(screen.getByRole("button", { name:"画面" }));
  const cropX = screen.getByRole("slider", { name:"镜头 1 快速裁切水平位置" });
  fireEvent.change(cropX, { target:{ value:"0.7" } });
  expect(screen.getByRole("slider", { name:"镜头 1 快速裁切水平位置" })).toHaveValue("0.7");
  expect(onPreview).toHaveBeenCalledTimes(1);
  fireEvent.pointerUp(cropX);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeCrop", clip_id:"clip-1", fit_mode:"fill", crop_x:.7, crop_y:.5 });
  fireEvent.click(screen.getByRole("button", { name:"镜头 1 快速画面方式" }));
  fireEvent.click(screen.getByRole("option", { name:"完整显示" }));
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeCrop", clip_id:"clip-1", fit_mode:"fit", crop_x:.7, crop_y:.5 });
  fireEvent.click(screen.getByRole("button", { name:"裁剪" }));
  expect(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" })).toHaveValue(1.5);
  fireEvent.click(screen.getByRole("button", { name:"换素材" }));
  fireEvent.click(screen.getByRole("button", { name:"查看候选" }));
  expect(onOpenDetails).toHaveBeenCalledTimes(1);
  expect(onPreview).toHaveBeenCalledTimes(3);
  expect(onDraftChange).toHaveBeenCalledTimes(4);
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

it("submits split and copy directly while keeping deletion explicitly confirmable", () => {
  const onPreview = vi.fn();
  const workspace = { task:{ id:"task-1" }, montage:{ clips:[clip, { ...clip, id:"clip-2", timeline_start_seconds:3, timeline_end_seconds:6 }], duration_seconds:6, graphic_overlays:[] }, subtitle_tracks:[], person_renders:[], person_render_capability:{ available:false, message:"", repair_action:null } } as unknown as CreationTaskWorkspace;
  render(<VideoClipInspector clip={clip} index={0} count={2} workspace={workspace} busy={false} onPreview={onPreview} onDraftChange={() => {}} onReplace={() => {}} candidates={[]} replacing={false} onPerson={() => {}} />);
  fireEvent.click(screen.getByText("更多剪辑"));
  fireEvent.click(screen.getByRole("button", { name:"复制镜头" }));
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"DuplicateClip", clip_id:"clip-1" });
  fireEvent.change(screen.getByRole("spinbutton", { name:"镜头分割位置" }), { target:{ value:"1.5" } });
  fireEvent.click(screen.getByRole("button", { name:"切开镜头" }));
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"SplitClip", clip_id:"clip-1", split_time_seconds:1.5 });
  expect(screen.getByRole("button", { name:"删除镜头…" })).toBeInTheDocument();
  expect(onPreview).toHaveBeenCalledTimes(2);
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
  expect(screen.getByText("完成输入或拖动后直接应用到草稿")).toBeInTheDocument();
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
  expect(screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" })).toBeDisabled();
});

it("submits ordinary inspector fields on valid completion without preview buttons", () => {
  const onPreview = vi.fn();
  const workspace = { task:{ id:"task-1" }, montage:{ clips:[clip], duration_seconds:3, graphic_overlays:[] }, subtitle_tracks:[], person_renders:[], person_render_capability:{ available:false, message:"", repair_action:null } } as unknown as CreationTaskWorkspace;
  render(<VideoClipInspector clip={clip} index={1} count={2} workspace={workspace} busy={false} onPreview={onPreview} onDraftChange={() => {}} onReplace={() => {}} candidates={[]} replacing={false} onPerson={() => {}} />);
  const trim = screen.getByRole("spinbutton", { name:"镜头 2 裁剪起点" });
  fireEvent.change(trim, { target:{ value:"1.5" } });
  fireEvent.blur(trim);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"TrimClip", clip_id:"clip-1", source_start_seconds:1.5 });
  expect(screen.queryByRole("button", { name:"预览裁剪" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("更多剪辑"));
  const rate = screen.getByRole("spinbutton", { name:"画面倍速" });
  fireEvent.change(rate, { target:{ value:"2" } });
  fireEvent.blur(rate);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeClipSpeed", clip_id:"clip-1", playback_rate:2 });
  const linkedRate = screen.getByRole("spinbutton", { name:"关联倍速" });
  fireEvent.change(linkedRate, { target:{ value:"0.5" } });
  fireEvent.blur(linkedRate);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeLinkedSpeed", clip_id:"clip-1", playback_rate:.5 });
  fireEvent.change(screen.getByRole("combobox", { name:"画面显示方式" }), { target:{ value:"fit" } });
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeCrop", clip_id:"clip-1", fit_mode:"fit", crop_x:.5, crop_y:.5 });
  fireEvent.change(screen.getByRole("combobox", { name:"画面显示方式" }), { target:{ value:"fill" } });
  fireEvent.click(screen.getByText("裁切位置"));
  const cropX = screen.getByRole("slider", { name:"裁切水平位置" });
  fireEvent.change(cropX, { target:{ value:"0.7" } });
  fireEvent.pointerUp(cropX);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"ChangeCrop", clip_id:"clip-1", fit_mode:"fill", crop_x:.7, crop_y:.5 });
  fireEvent.click(screen.getByText("镜头过渡"));
  const transition = screen.getByRole("spinbutton", { name:"叠化时长（秒）" });
  fireEvent.change(transition, { target:{ value:"0.4" } });
  fireEvent.blur(transition);
  expect(onPreview).toHaveBeenLastCalledWith({ operation_type:"SetClipTransition", clip_id:"clip-1", transition_seconds:.4 });
  expect(screen.queryByRole("button", { name:"预览速度" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"预览变速" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name:"预览画面" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name:"删除镜头…" })).toBeInTheDocument();
});

it("allows the same QuickEditor value to retry after its busy callback completes", () => {
  const onPreview = vi.fn();
  function Harness() {
    const [draft, setDraft] = useState(createVideoClipDraft(clip));
    const [busy, setBusy] = useState(false);
    return <><VideoClipQuickEditor clip={clip} index={0} busy={busy} draft={{ ...draft, start:1.5 }} onDraftStateChange={setDraft} onDraftChange={() => {}} onPreview={input => { onPreview(input); setBusy(true); }} onOpenDetails={() => {}} /><button type="button" onClick={() => setBusy(false)}>完成尝试</button></>;
  }
  render(<Harness />);
  const trim = screen.getByRole("spinbutton", { name:"镜头 1 快速裁剪起点" });
  fireEvent.blur(trim);
  expect(onPreview).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name:"完成尝试" }));
  fireEvent.blur(trim);
  expect(onPreview).toHaveBeenCalledTimes(2);
});
