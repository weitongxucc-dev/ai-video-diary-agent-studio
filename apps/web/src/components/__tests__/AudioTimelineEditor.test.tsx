import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import type { CreationTaskWorkspace, TimelineAudioTrack } from "@/lib/types";
import { listenCreationTaskAudio, getCreationTaskMediaJob } from "@/lib/api";
import { AudioTimelineInspector, AudioTimelineTracks } from "../AudioTimelineEditor";

vi.mock("@/lib/api", async importOriginal => ({ ...(await importOriginal<typeof import("@/lib/api")>()), apiUrl:(value:string)=>value, importCreationTaskAudio:vi.fn(),listenCreationTaskAudio:vi.fn(),getCreationTaskMediaJob:vi.fn() }));
afterEach(()=>vi.unstubAllGlobals());
const track: TimelineAudioTrack={ id:"music",name:"音乐",role:"music",gain_db:0,muted:false,locked:false,duck_under_voice:true,
  clips:[{id:"clip",name:"背景音乐",source_ref:"artifacts/music",source_start_ms:1000,source_end_ms:3000,timeline_start_ms:4000,
    gain_db:0,muted:false,locked:false,fade_in_ms:0,fade_out_ms:0,denoise_db:0,gain_points:[],envelope_offset_ms:0,envelope_duration_ms:null}] };

it("selects an actual audio range and keeps solo in listening requests",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({peaks:[.1,.5,.8,.4],seconds_per_peak:1}))));
  vi.mocked(listenCreationTaskAudio).mockResolvedValue({audio_url:"/preview.wav"});
  const workspace={task:{id:"task",current_timeline_version_id:"version"},montage:{duration_seconds:10,audio_timeline:{format_version:1,tracks:[track]}}} as unknown as CreationTaskWorkspace;
  const select=vi.fn(),preview=vi.fn(),position=vi.fn();
  render(<AudioTimelineTracks taskId={workspace.task.id} versionId={workspace.task.current_timeline_version_id} audioTimeline={workspace.montage!.audio_timeline!} durationSeconds={workspace.montage!.duration_seconds} selectedId={null} onSelect={select} onListenPosition={position} onPreview={preview} busy={false} />);
  fireEvent.click(screen.getByRole("button",{name:"选择音频：背景音乐"}));
  expect(select).toHaveBeenCalledWith("clip",4);
  fireEvent.click(screen.getByRole("button",{name:"独听音乐"}));
  fireEvent.click(screen.getByRole("button",{name:"音频工具"}));
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

it.each([
  ["时间线起点（秒）", "0.967", "timeline_start_ms", 967],
  ["源音频终点（秒）", "0.233", "source_end_ms", 233],
  ["源音频起点（秒）", "1.001", "source_start_ms", 1001],
])("submits %s in integer milliseconds", (label, seconds, key, milliseconds) => {
  const preview = vi.fn();
  const clip = { ...track.clips[0], source_start_ms: 0, source_end_ms: 3000, timeline_start_ms: 960 };
  render(<AudioTimelineInspector track={track} clip={clip} versionId="version" playhead={0}
    busy={false} onPreview={preview} onDraftChange={vi.fn()} />);
  expect(screen.getByText("0.960–3.960 秒 · 音乐")).toBeVisible();
  const input = screen.getByRole<HTMLInputElement>("spinbutton", { name: label });
  input.focus();
  fireEvent.change(input, { target: { value: seconds } });
  expect(input.checkValidity()).toBe(true);
  fireEvent.blur(input);
  expect(preview).toHaveBeenCalledExactlyOnceWith({
    base_version_id: "version", target_id: "clip", action: "update", values: { [key]: milliseconds },
  });
});

it.each(["clip", "track"])("keeps %s gain controls synchronized and submits only after leaving the slider", async target => {
  const preview = vi.fn();
  const user = userEvent.setup();
  render(<AudioTimelineInspector track={track} clip={target === "clip" ? track.clips[0] : null} versionId="version" playhead={5} busy={false} onPreview={preview} onDraftChange={vi.fn()} />);
  const slider = screen.getByRole("slider", { name: "音量调节（dB）" });
  const precise = screen.getByRole("spinbutton", { name: "音量（dB）" });
  await user.tab();
  expect(slider).toHaveFocus();
  for (const gain of [-20, -18, -12]) fireEvent.change(slider, { target: { value: String(gain) } });
  expect(precise).toHaveValue(-12);
  expect(slider).toHaveAttribute("aria-valuetext", "-12 dB");
  expect(preview).not.toHaveBeenCalled();
  await user.tab();
  expect(precise).toHaveFocus();
  expect(preview).toHaveBeenCalledExactlyOnceWith({ base_version_id: "version", target_id: target === "clip" ? "clip" : "music", action: target === "clip" ? "update" : "track", values: { gain_db: -12 } });
  fireEvent.change(precise, { target: { value: "-6" } });
  expect(slider).toHaveValue("-6");
});

it.each(["busy", "track-locked", "clip-locked"])("disables both gain controls and pending blur submission when %s", state => {
  const preview = vi.fn();
  const props = { track, clip: track.clips[0], versionId: "version", playhead: 5, busy: false, onPreview: preview, onDraftChange: vi.fn() };
  const { rerender } = render(<AudioTimelineInspector {...props} />);
  const slider = screen.getByRole("slider", { name: "音量调节（dB）" });
  fireEvent.change(slider, { target: { value: "-9" } });
  rerender(<AudioTimelineInspector {...props} busy={state === "busy"} track={{ ...track, locked: state === "track-locked" }} clip={{ ...track.clips[0], locked: state === "clip-locked" }} />);
  expect(slider).toBeDisabled();
  expect(screen.getByRole("spinbutton", { name: "音量（dB）" })).toBeDisabled();
  fireEvent.blur(slider);
  expect(preview).not.toHaveBeenCalled();
});

it("does not present a late mix after the selected audio changes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ peaks: [.1, .5], seconds_per_peak: 1 }))));
  let finish!: (value: { audio_url: string }) => void;
  vi.mocked(listenCreationTaskAudio).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const workspace = { task: { id: "task", current_timeline_version_id: "version" }, montage: { duration_seconds: 10, audio_timeline: { format_version: 1, tracks: [track] } } } as unknown as CreationTaskWorkspace;
  const props = { taskId: workspace.task.id, versionId: workspace.task.current_timeline_version_id, audioTimeline: workspace.montage!.audio_timeline!, durationSeconds: workspace.montage!.duration_seconds, onSelect: vi.fn(), onPreview: vi.fn(), busy: false };
  const { rerender } = render(<AudioTimelineTracks {...props} selectedId="clip" />);
  fireEvent.click(screen.getByRole("button", { name: "音频工具" }));
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
  const { rerender } = render(<AudioTimelineTracks {...props} taskId={workspace.task.id} versionId={workspace.task.current_timeline_version_id} audioTimeline={workspace.montage!.audio_timeline!} durationSeconds={workspace.montage!.duration_seconds} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "选择音频：背景音乐" }).querySelector("svg")).not.toBeNull());
  const changed = { ...workspace, montage: { ...workspace.montage!, audio_timeline: { format_version: 1 as const, tracks: [{ ...track, clips: [{ ...track.clips[0], source_ref: "artifacts/new-music" }] }] } } };
  rerender(<AudioTimelineTracks {...props} taskId={changed.task.id} versionId={changed.task.current_timeline_version_id} audioTimeline={changed.montage.audio_timeline} durationSeconds={changed.montage.duration_seconds} />);
  expect(screen.getByText("读取波形…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择音频：背景音乐" }).querySelector("svg")).toBeNull();
});

it("keeps one audio controller when shared tracks place the subtitle before import and listening tools", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ peaks: [.1, .5], seconds_per_peak: 1 }))));
  vi.mocked(listenCreationTaskAudio).mockResolvedValue({ audio_url: "/shared-preview.wav" });
  const workspace = { task: { id: "task", current_timeline_version_id: "version" }, montage: { duration_seconds: 10,
    audio_timeline: { format_version: 1, tracks: [track] } } } as unknown as CreationTaskWorkspace;
  render(<AudioTimelineTracks taskId={workspace.task.id} versionId={workspace.task.current_timeline_version_id} audioTimeline={workspace.montage!.audio_timeline!} durationSeconds={workspace.montage!.duration_seconds} selectedId={null} onSelect={vi.fn()} onPreview={vi.fn()} busy={false}
    sharedTimeScale subtitleTrack={<button type="button" aria-label="定位测试字幕">测试字幕</button>} />);

  const audioClip = screen.getByRole("button", { name: "选择音频：背景音乐" });
  const subtitle = screen.getByRole("button", { name: "定位测试字幕" });
  expect(screen.queryByRole("group", { name: "音频导入与试听" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "音频工具" }));
  const tools = screen.getByRole("group", { name: "音频导入与试听" });
  expect(audioClip.compareDocumentPosition(subtitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  await waitFor(() => expect(tools).toBeVisible());
  expect(screen.getAllByLabelText("试听起点")).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "导入音频" })).toHaveLength(1);

  fireEvent.click(audioClip);
  fireEvent.click(screen.getByRole("button", { name: "独听音乐" }));
  fireEvent.click(screen.getByRole("button", { name: "音频工具" }));
  fireEvent.change(screen.getByLabelText("试听起点"), { target: { value: "4.5" } });
  fireEvent.click(subtitle);
  fireEvent.click(screen.getByRole("button", { name: "音频工具" }));
  expect(screen.getByLabelText("试听起点")).toHaveValue(4.5);
  fireEvent.click(screen.getByRole("button", { name: "试听独听轨道" }));
  await waitFor(() => expect(listenCreationTaskAudio).toHaveBeenLastCalledWith("task", {
    base_version_id: "version", start_seconds: 4.5, end_seconds: 6, solo_track_ids: ["music"],
  }));
  expect(await screen.findByLabelText("选区音频试听")).toHaveAttribute("src", "/shared-preview.wav");
});

it("uses an existing import track and requires a new choice when that track disappears",()=>{
  const original={...track,id:"field-recording",name:"现场原声",role:"original" as const,clips:[]};
  const props={taskId:"task",versionId:"version",durationSeconds:5,selectedId:null,onSelect:vi.fn(),onPreview:vi.fn(),busy:false};
  const view=render(<AudioTimelineTracks {...props} audioTimeline={{format_version:1,tracks:[original]}}/>);
  fireEvent.click(screen.getByRole("button",{name:"音频工具"}));
  expect(screen.getByRole("combobox",{name:"音频导入轨道"})).toHaveTextContent("现场原声");
  expect(screen.getByRole("button",{name:"导入音频"})).toBeEnabled();
  view.rerender(<AudioTimelineTracks {...props} audioTimeline={{format_version:1,tracks:[{...original,id:"new-recording"}]}}/>);
  expect(screen.getByRole("combobox",{name:"音频导入轨道"})).toHaveTextContent("选择导入轨道");
  expect(screen.getByRole("button",{name:"导入音频"})).toBeDisabled();
});


it("separates the source name from waveform failure and rereads only the original job", async () => {
  const failed = { job_id:"wave-original", result_kind:"waveform", base_version_id:"version", status:"failed", result:null, failure_reason:"波形资源检查未通过", repair_action:null } as import("@/lib/types").TaskMediaJob;
  const fetcher=vi.fn(async()=>new Response(JSON.stringify(failed)));
  vi.stubGlobal("fetch",fetcher);
  vi.mocked(getCreationTaskMediaJob).mockResolvedValue({...failed,status:"succeeded",result:{peaks:[.1,.5],seconds_per_peak:1}});
  render(<AudioTimelineTracks taskId="task" versionId="version" durationSeconds={10} audioTimeline={{format_version:1,tracks:[{...track,clips:[{...track.clips[0],name:"当前配音"}]}]}} selectedId={null} onSelect={vi.fn()} onPreview={vi.fn()} busy={false}/>);
  expect(await screen.findByText("波形不可用")).toBeInTheDocument();
  const selection=screen.getByRole("button",{name:"选择音频：当前配音"});
  expect(selection).toHaveTextContent("当前配音");
  const problem=screen.getByRole("button",{name:"查看波形问题：当前配音"});
  expect(selection.contains(problem)).toBe(false);
  fireEvent.click(problem);
  expect(await screen.findByRole("alert")).toHaveTextContent("波形资源检查未通过");
  fireEvent.click(screen.getByRole("button",{name:"重新读取波形"}));
  await waitFor(()=>expect(selection.querySelector("svg")).not.toBeNull());
  expect(getCreationTaskMediaJob).toHaveBeenCalledWith("task","wave-original",expect.any(AbortSignal));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button",{name:"查看波形问题：当前配音"})).not.toBeInTheDocument();
});

it("keeps the original request error without offering an unbound resubmission", async () => {
  vi.stubGlobal("fetch",vi.fn(async()=>{throw new Error("连接已断开");}));
  render(<AudioTimelineTracks taskId="task" versionId="version" durationSeconds={10} audioTimeline={{format_version:1,tracks:[track]}} selectedId={null} onSelect={vi.fn()} onPreview={vi.fn()} busy={false}/>);
  fireEvent.click(await screen.findByRole("button",{name:"查看波形问题：背景音乐"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("本地服务未接通。");
  expect(screen.queryByRole("button",{name:"重新读取波形"})).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "检查本机媒体准备" })).toHaveAttribute("href", "/settings");
});
