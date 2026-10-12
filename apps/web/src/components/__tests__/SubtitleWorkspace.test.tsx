import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ImgHTMLAttributes } from "react";
import type { CreationTaskSubtitleTrack, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
vi.mock("next/image",()=>({default:({alt,unoptimized,...props}:ImgHTMLAttributes<HTMLImageElement>&{unoptimized?:boolean})=>{void unoptimized;return <img alt={alt} {...props}/>;}}));
vi.mock("@/lib/api",()=>({apiUrl:(path:string)=>path}));
import { SubtitleWorkspace } from "../SubtitleWorkspace";
function track(language:"zh-CN"|"en-US",id:string):CreationTaskSubtitleTrack {
 return {id,workspace_id:"fixture",task_id:"fixture-task",timeline_version_id:"version-1",language,source_language:null,
 style:{name:"default",font_family:"PingFang SC",font_size:language==="zh-CN"?40:28,primary_color:"#FFFFFF",emphasis_color:"#FFD54F",position:"bottom",bilingual_order:"source_then_translation",enabled:true},
 cues:[{id:`${id}-cue-1`,source_cue_id:null,text:"自建字幕第一条",start_seconds:0,end_seconds:3,speaker:null,line_breaks:[],emphasis:[]},{id:`${id}-cue-2`,source_cue_id:null,text:"自建字幕第二条",start_seconds:3,end_seconds:6,speaker:null,line_breaks:[],emphasis:[]}],generation_method:"timeline",model_id:null,status:"valid",artifact_ref:"fixture-artifact",transform_id:null,failure_reason:null,impact:null,repair_action:null,created_at:"2026-10-10",updated_at:"2026-10-10"};
}
function fixture():WorkspaceState {
 return {task:{id:"fixture-task",current_timeline_version_id:"version-1",can_undo:true,can_redo:true},plan:{aspect_ratio:"9:16"},
 montage:{duration_seconds:10,aspect_ratio:"9:16",output_url:"/api/fixture-preview"},subtitle_tracks:[track("zh-CN","zh"),track("en-US","en")],
 cover_asset:{id:"cover-1",timeline_version_id:"version-1",frame_time_seconds:2.4,title:"自建封面",layout:{title_position:"bottom"},status:"valid",artifact_ref:"fixture-cover"},
 subtitle_translation_capability:{available:true,message:"本轮fixture",repair_action:null},cover_frame_capability:{available:true,message:"本轮fixture",repair_action:null}} as WorkspaceState;
}
function mount(workspace=fixture(),activeTrack=workspace.subtitle_tracks[0],busy=false){
 const callbacks={onDraftChange:vi.fn(),onPreviewExact:vi.fn(),onSelectCover:vi.fn(),onSelectCue:vi.fn(),onSelectTrack:vi.fn(),onSaveCover:vi.fn(),onSaveSubtitle:vi.fn(),onUndo:vi.fn(),onRedo:vi.fn()};
 const result=render(<SubtitleWorkspace {...callbacks} workspace={workspace} activeTrack={activeTrack} busy={busy} selectedCover={false} selectedCueId={null}/>);return {...result,...callbacks};
}
beforeEach(()=>{Object.defineProperty(window,"matchMedia",{configurable:true,value:()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})});});
describe("compact subtitle workspace real callbacks",()=>{
 it("keeps preview and playhead across view-only navigation without a mutation",async()=>{
  const c=mount();const user=userEvent.setup();const video=screen.getByLabelText("字幕成片预览") as HTMLVideoElement;video.currentTime=2.4;
  await user.click(screen.getByRole("tab",{name:"样式"}));expect(screen.queryByLabelText("当前编辑字幕轨道")).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab",{name:"封面"}));expect(screen.getByLabelText("字幕成片预览")).toBe(video);expect(video.currentTime).toBe(2.4);expect(screen.queryByRole("button",{name:"保存字幕"})).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab",{name:"字幕"}));expect(c.onPreviewExact).not.toHaveBeenCalled();
 });
 it("preserves the original blur auto-save when a real focused cue draft leaves for another view",async()=>{
  const c=mount();const user=userEvent.setup();const video=screen.getByLabelText("字幕成片预览") as HTMLVideoElement;
  const text=screen.getByLabelText("字幕 1 文本");await user.click(text);await user.clear(text);await user.type(text,"待保存草稿");video.currentTime=2.4;
  await user.click(screen.getByRole("tab",{name:"样式"}));expect(c.onPreviewExact).toHaveBeenCalledWith(expect.objectContaining({operation_type:"UpdateSubtitleCue",track_id:"zh",cue_id:"zh-cue-1",text:"待保存草稿"}));
  expect(screen.getByLabelText("字幕成片预览")).toBe(video);expect(video.currentTime).toBe(2.4);
  await user.click(screen.getByRole("tab",{name:"字幕"}));expect(screen.getByLabelText("字幕 1 文本")).toHaveValue("待保存草稿");
 });
 it("counts Unicode code points and blocks over96 or invalid times before real preview",()=>{
  const c=mount();const text=screen.getByLabelText("字幕 1 文本");fireEvent.change(text,{target:{value:"😀".repeat(97)}});expect(screen.getByLabelText("字幕 1 字数")).toHaveTextContent("97 / 96");expect(screen.getByRole("button",{name:"保存字幕草稿"})).toBeDisabled();
  fireEvent.change(text,{target:{value:"😀".repeat(96)}});expect(screen.getByRole("button",{name:"保存字幕草稿"})).toBeEnabled();
  fireEvent.change(screen.getByLabelText("字幕 1 结束时间"),{target:{value:"0"}});expect(screen.getByRole("button",{name:"保存字幕草稿"})).toBeDisabled();fireEvent.blur(text);expect(c.onPreviewExact).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("字幕 1 结束时间"),{target:{value:"2.5"}});fireEvent.click(screen.getByRole("button",{name:"保存字幕草稿"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"UpdateSubtitleCue",track_id:"zh",cue_id:"zh-cue-1",end_seconds:2.5,text:"😀".repeat(96)}));
 });
 it("edits the exact English cue with linebreak and emphasis without adding unchanged global style",()=>{
  const w=fixture();const c=mount(w,w.subtitle_tracks[1]);fireEvent.change(screen.getByLabelText("字幕 1 文本"),{target:{value:"Word\n😀"}});fireEvent.change(screen.getByLabelText("字幕 1 强调词"),{target:{value:"Word、Missing"}});fireEvent.click(screen.getByRole("button",{name:"保存字幕草稿"}));
  expect(c.onPreviewExact).toHaveBeenLastCalledWith(expect.objectContaining({track_id:"en",cue_id:"en-cue-1",line_breaks:[4],emphasis:["Word"]}));expect(c.onPreviewExact.mock.lastCall?.[0]).not.toHaveProperty("style");
 });
 it("preserves structural commands and the selected real cue identity",()=>{
  const c=mount();fireEvent.click(screen.getByText("拆分、合并与删除"));fireEvent.click(screen.getByRole("button",{name:"拆分字幕"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith(expect.objectContaining({operation_type:"SplitSubtitleCue",track_id:"zh",cue_id:"zh-cue-1",split_time_seconds:1.5}));
  fireEvent.click(screen.getByRole("button",{name:"与下一条合并"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith({operation_type:"MergeSubtitleCue",track_id:"zh",cue_id:"zh-cue-1"});fireEvent.click(screen.getByRole("button",{name:"删除字幕"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith({operation_type:"DeleteSubtitleCue",track_id:"zh",cue_id:"zh-cue-1"});
 });
 it("keeps current track selection distinct from showing languages and adjacent undo/redo",()=>{
  const c=mount();fireEvent.click(screen.getByRole("switch",{name:"中文轨开关"}));expect(c.onPreviewExact).toHaveBeenCalledWith({operation_type:"ConfigureSubtitleTrack",subtitle_display:"en"});expect(c.onSelectTrack).not.toHaveBeenCalled();
  const history=screen.getByRole("group",{name:"字幕历史编辑"});expect(within(history).getAllByRole("button")).toHaveLength(2);fireEvent.click(within(history).getByRole("button",{name:"撤销"}));fireEvent.click(within(history).getByRole("button",{name:"重做"}));expect(c.onUndo).toHaveBeenCalledOnce();expect(c.onRedo).toHaveBeenCalledOnce();
 });
 it("uses honest shared Chinese style even when an English track is currently selected",()=>{
  const w=fixture();const c=mount(w,w.subtitle_tracks[1]);fireEvent.click(screen.getByRole("tab",{name:"样式"}));expect(screen.getByText("全部字幕样式 · 中英文共用")).toBeVisible();expect(screen.getByLabelText("字幕字号")).toHaveValue(40);
  fireEvent.change(screen.getByLabelText("字幕字号"),{target:{value:"48"}});fireEvent.blur(screen.getByLabelText("字幕字号"));expect(c.onPreviewExact).toHaveBeenLastCalledWith({operation_type:"ConfigureSubtitleTrack",style:{font_size:48}});
 });
 it("opens a compact color picker and restores its trigger focus on Escape without changing content",async()=>{
  const c=mount();const user=userEvent.setup();await user.click(screen.getByRole("tab",{name:"样式"}));const trigger=screen.getByRole("button",{name:"字幕文字颜色"});await user.click(trigger);expect(await screen.findByRole("dialog",{name:"字幕文字颜色选择"})).toBeVisible();await user.keyboard("{Escape}");await waitFor(()=>expect(trigger).toHaveFocus());expect(c.onPreviewExact).not.toHaveBeenCalled();
 });
 it("applies one selected real color through the existing global-style operation",async()=>{
  const c=mount();const user=userEvent.setup();await user.click(screen.getByRole("tab",{name:"样式"}));await user.click(screen.getByRole("button",{name:"字幕文字颜色"}));await user.click(await screen.findByRole("button",{name:"字幕文字颜色 #4F8BFF"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith({operation_type:"ConfigureSubtitleTrack",style:{primary_color:"#4F8BFF"}});
 });
 it("keeps subtitle export language in its nearby settings popup and closes to the same trigger",async()=>{
  const w=fixture();const c=mount(w,w.subtitle_tracks[1]);const user=userEvent.setup();expect(screen.queryByLabelText("保存字幕语言")).not.toBeInTheDocument();await user.click(screen.getByRole("button",{name:"保存字幕"}));expect(c.onSaveSubtitle).toHaveBeenCalledWith("en");
  const trigger=screen.getByRole("button",{name:"字幕导出设置"});await user.click(trigger);expect(await screen.findByLabelText("保存字幕语言")).toHaveAttribute("data-selected-value","en");await user.keyboard("{Escape}");await waitFor(()=>expect(trigger).toHaveFocus());
 });
 it("keeps cover drafts across views and routes generation and saving to the actual existing callbacks",()=>{
  const c=mount();fireEvent.click(screen.getByRole("tab",{name:"封面"}));fireEvent.change(screen.getByLabelText("封面标题"),{target:{value:"新的自建标题"}});fireEvent.change(screen.getByLabelText("封面画面时间"),{target:{value:"4"}});fireEvent.click(screen.getByRole("tab",{name:"字幕"}));fireEvent.click(screen.getByRole("tab",{name:"封面"}));expect(screen.getByLabelText("封面标题")).toHaveValue("新的自建标题");fireEvent.click(screen.getByRole("button",{name:"从画面生成封面"}));expect(c.onPreviewExact).toHaveBeenLastCalledWith({operation_type:"GenerateCoverFromFrame",frame_time_seconds:4,title:"新的自建标题",title_position:"bottom"});fireEvent.click(screen.getByRole("button",{name:"保存封面"}));expect(c.onSaveCover).toHaveBeenCalledOnce();
 });
 it("retains translation and the genuine busy or missing-frame capability gates",()=>{
  const w=fixture();w.subtitle_tracks=[w.subtitle_tracks[0]];const c=mount(w);fireEvent.click(screen.getByRole("button",{name:"生成英文字幕"}));expect(c.onSelectTrack).toHaveBeenCalledWith("zh");expect(c.onPreviewExact).toHaveBeenCalledWith({operation_type:"ConfigureSubtitleTrack",track_id:"zh",target_language:"en-US"});c.unmount();
  w.cover_frame_capability.available=false;mount(w,w.subtitle_tracks[0],true);expect(screen.getByRole("button",{name:"保存字幕"})).toBeDisabled();fireEvent.click(screen.getByRole("tab",{name:"封面"}));expect(screen.getByRole("button",{name:"从画面生成封面"})).toBeDisabled();
 });
});
