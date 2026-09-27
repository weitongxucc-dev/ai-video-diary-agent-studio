import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CreationTaskWorkspace, TimelineAudioTrack } from "@/lib/types";
import { listenCreationTaskAudio } from "@/lib/api";
import { AudioTimelineInspector, AudioTimelineTracks } from "../AudioTimelineEditor";

vi.mock("@/lib/api",()=>({ apiUrl:(value:string)=>value, importCreationTaskAudio:vi.fn(),listenCreationTaskAudio:vi.fn() }));
afterEach(()=>vi.unstubAllGlobals());
const track: TimelineAudioTrack={ id:"music",name:"音乐",role:"music",gain_db:0,muted:false,locked:false,duck_under_voice:true,
  clips:[{id:"clip",name:"背景音乐",source_ref:"artifacts/music",source_start_ms:1000,source_end_ms:3000,timeline_start_ms:4000,
    gain_db:0,muted:false,locked:false,fade_in_ms:0,fade_out_ms:0,denoise_db:0,gain_points:[],envelope_offset_ms:0,envelope_duration_ms:null}] };

it("selects an actual audio range and keeps solo in listening requests",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>({ok:true,json:async()=>({peaks:[.1,.5,.8,.4],seconds_per_peak:1})})));
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

it("sends an exact gain proposal without changing the source range",()=>{
  const preview=vi.fn();
  render(<AudioTimelineInspector track={track} clip={track.clips[0]} versionId="version" playhead={5} busy={false} onPreview={preview} onDraftChange={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("音量（dB）"),{target:{value:"-12"}});
  fireEvent.click(screen.getByRole("button",{name:"预览音频修改"}));
  expect(preview).toHaveBeenCalledWith({base_version_id:"version",target_id:"clip",action:"update",values:{gain_db:-12}});
  expect(track.clips[0].source_start_ms).toBe(1000);
});
