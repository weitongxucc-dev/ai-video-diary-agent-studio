import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CreationTaskWorkspace, TimelineAudioTrack } from "@/lib/types";
import { listenCreationTaskAudio } from "@/lib/api";
import { AudioTimelineInspector, AudioTimelineTracks } from "../AudioTimelineEditor";

vi.mock("@/lib/api", async importOriginal => ({ ...(await importOriginal<typeof import("@/lib/api")>()), apiUrl:(value:string)=>value, importCreationTaskAudio:vi.fn(),listenCreationTaskAudio:vi.fn() }));
afterEach(()=>vi.unstubAllGlobals());
const track: TimelineAudioTrack={ id:"music",name:"音乐",role:"music",gain_db:0,muted:false,locked:false,duck_under_voice:true,
  clips:[{id:"clip",name:"背景音乐",source_ref:"artifacts/music",source_start_ms:1000,source_end_ms:3000,timeline_start_ms:4000,
    gain_db:0,muted:false,locked:false,fade_in_ms:0,fade_out_ms:0,denoise_db:0,gain_points:[],envelope_offset_ms:0,envelope_duration_ms:null}] };

it("selects an actual audio range and keeps solo in listening requests",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({peaks:[.1,.5,.8,.4],seconds_per_peak:1}))));
  vi.mocked(listenCreationTaskAudio).mockResolvedValue({audio_url:"/preview.wav"});
  const workspace={task:{id:"task",current_timeline_version_id:"version"},montage:{duration_seconds:10,audio_timeline:{format_version:1,tracks:[track]}}} as unknown as CreationTaskWorkspace;
  const select=vi.fn(),preview=vi.fn(),position=vi.fn();
  render(<AudioTimelineTracks workspace={workspace} selectedId={null} onSelect={select} onListenPosition={position} onPreview={preview} busy={false} />);
  fireEvent.click(screen.getByRole("button",{name:"选择音频：背景音乐"}));
  expect(select).toHaveBeenCalledWith("clip",4);
  fireEvent.click(screen.getByRole("button",{name:"独听音乐"}));
  fireEvent.click(screen.getByRole("button",{name:"试听独听轨道"}));
  await waitFor(()=>expect(listenCreationTaskAudio).toHaveBeenCalledWith("task",{base_version_id:"version",start_seconds:4,end_seconds:6,solo_track_ids:["music"]}));
  expect(preview).not.toHaveBeenCalled();
  expect(screen.getByLabelText("选区音频试听")).toHaveAttribute("src","/preview.wav");
  fireEvent.timeUpdate(screen.getByLabelText("选区音频试听"),{target:{currentTime:.5}});
  expect(position).toHaveBeenCalledWith(4.5);
});

it("sends an exact gain submission without changing the source range",()=>{
  const preview=vi.fn();
  render(<AudioTimelineInspector track={track} clip={track.clips[0]} versionId="version" playhead={5} busy={false} onPreview={preview} onDraftChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("音量（dB）"),{target:{value:"-12"}});
  fireEvent.click(screen.getByRole("button",{name:"保存音频草稿"}));
  expect(preview).toHaveBeenCalledWith({base_version_id:"version",target_id:"clip",action:"update",values:{gain_db:-12}});
  expect(track.clips[0].source_start_ms).toBe(1000);
});

it("does not present a late mix after the selected audio changes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ peaks: [.1, .5], seconds_per_peak: 1 }))));
  let finish!: (value: { audio_url: string }) => void;
  vi.mocked(listenCreationTaskAudio).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const workspace = { task: { id: "task", current_timeline_version_id: "version" }, montage: { duration_seconds: 10, audio_timeline: { format_version: 1, tracks: [track] } } } as unknown as CreationTaskWorkspace;
  const props = { workspace, onSelect: vi.fn(), onPreview: vi.fn(), busy: false };
  const { rerender } = render(<AudioTimelineTracks {...props} selectedId="clip" />);
  fireEvent.click(screen.getByRole("button", { name: "试听混音" }));
  rerender(<AudioTimelineTracks {...props} selectedId="music" />);
  await act(async () => finish({ audio_url: "/old-mix.wav" }));
  expect(screen.queryByLabelText("选区音频试听")).not.toBeInTheDocument();
});

it("clears the old waveform while a changed source is being read", async () => {
  const fetchWave = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ peaks: [.1, .5], seconds_per_peak: 1 }))).mockReturnValueOnce(new Promise(() => {}));
  vi.stubGlobal("fetch", fetchWave);
  const workspace = { task: { id: "task", current_timeline_version_id: "version" }, montage: { duration_seconds: 10, audio_timeline: { format_version: 1, tracks: [track] } } } as unknown as CreationTaskWorkspace;
  const props = { onSelect: vi.fn(), onPreview: vi.fn(), busy: false, selectedId: "clip" };
  const { rerender } = render(<AudioTimelineTracks {...props} workspace={workspace} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "选择音频：背景音乐" }).querySelector("svg")).not.toBeNull());
  const changed = { ...workspace, montage: { ...workspace.montage!, audio_timeline: { format_version: 1 as const, tracks: [{ ...track, clips: [{ ...track.clips[0], source_ref: "artifacts/new-music" }] }] } } };
  rerender(<AudioTimelineTracks {...props} workspace={changed} />);
  expect(screen.getByText("读取波形…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择音频：背景音乐" }).querySelector("svg")).toBeNull();
});

it("keeps one audio controller when shared tracks place the subtitle before import and listening tools", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ peaks: [.1, .5], seconds_per_peak: 1 }))));
  vi.mocked(listenCreationTaskAudio).mockResolvedValue({ audio_url: "/shared-preview.wav" });
  const workspace = { task: { id: "task", current_timeline_version_id: "version" }, montage: { duration_seconds: 10,
    audio_timeline: { format_version: 1, tracks: [track] } } } as unknown as CreationTaskWorkspace;
  render(<AudioTimelineTracks workspace={workspace} selectedId={null} onSelect={vi.fn()} onPreview={vi.fn()} busy={false}
    sharedTimeScale subtitleTrack={<button type="button" aria-label="定位测试字幕">测试字幕</button>} />);

  const audioClip = screen.getByRole("button", { name: "选择音频：背景音乐" });
  const subtitle = screen.getByRole("button", { name: "定位测试字幕" });
  const tools = screen.getByRole("group", { name: "音频导入与试听" });
  expect(audioClip.compareDocumentPosition(subtitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(subtitle.compareDocumentPosition(tools) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByLabelText("试听起点")).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "导入音频" })).toHaveLength(1);

  fireEvent.click(audioClip);
  fireEvent.click(screen.getByRole("button", { name: "独听音乐" }));
  fireEvent.change(screen.getByLabelText("试听起点"), { target: { value: "4.5" } });
  fireEvent.click(subtitle);
  expect(screen.getByLabelText("试听起点")).toHaveValue(4.5);
  fireEvent.click(screen.getByRole("button", { name: "试听独听轨道" }));
  await waitFor(() => expect(listenCreationTaskAudio).toHaveBeenLastCalledWith("task", {
    base_version_id: "version", start_seconds: 4.5, end_seconds: 6, solo_track_ids: ["music"],
  }));
  expect(await screen.findByLabelText("选区音频试听")).toHaveAttribute("src", "/shared-preview.wav");
});
