import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTaskMontageClip, CreationTaskWorkspace } from "@/lib/types";
import { ClipThumbnail, VideoClipInspector, VideoClipQuickEditor, VideoEditingStage } from "../VideoEditor";

vi.mock("../NarrationWaveformTrack", () => ({ NarrationWaveformTrack: () => <div>配音</div> }));

let createdVideos: HTMLVideoElement[];
const drawImage = vi.fn();
beforeEach(() => {
  createdVideos = [];
  drawImage.mockReset();
  const create = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag, options) => {
    const element = create(tag, options);
    if (tag === "video") createdVideos.push(element as HTMLVideoElement);
    return element;
  });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({drawImage} as unknown as CanvasRenderingContext2D);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const thumbnailVideo = () => createdVideos.filter(video => !video.isConnected).at(-1)!;

const clip: CreationTaskMontageClip = {
  id: "clip-1", asset_id: "asset-1", source_reference: "workspace://synthetic.mp4",
  source_start_seconds: 2, source_end_seconds: 5, timeline_start_seconds: 0, timeline_end_seconds: 3,
  match_score: .9, role: "b_roll", locked: false, needs_confirmation: false,
} as CreationTaskMontageClip;

function mediaState(video: HTMLVideoElement, readyState: number, seeking = false) {
  Object.defineProperty(video, "readyState", { configurable: true, value: readyState });
  Object.defineProperty(video, "seeking", { configurable: true, value: seeking });
  if (!video.videoWidth) {
    Object.defineProperty(video, "videoWidth", {configurable: true, value: 1920});
    Object.defineProperty(video, "videoHeight", {configurable: true, value: 1080});
  }
}
function workspace(selected = clip, output: string | null = null, version = "timeline-1", ratio = "9:16"): CreationTaskWorkspace {
  return { task: { id: "task-1", current_timeline_version_id: version },
    montage: { clips: [selected], duration_seconds: 3, aspect_ratio: ratio, output_url: output, low_confidence_clip_count: 0 },
    subtitle_tracks: [] } as unknown as CreationTaskWorkspace;
}
function stage(data = workspace(), ref = createRef<HTMLVideoElement>()) {
  return <VideoEditingStage workspace={data} selectedId={clip.id} onSelect={vi.fn()} onPosition={vi.fn()} videoRef={ref} initialTime={0} renderAction={null} />;
}

it("uses the saved plan ratio when an older montage has no canvas ratio",()=>{
  const data=workspace();data.montage!.aspect_ratio=undefined;data.plan={aspect_ratio:"16:9"} as CreationTaskWorkspace["plan"];
  const view=render(stage(data));
  expect(view.container.querySelector('[data-editor-player]')).toHaveAttribute("data-editor-aspect-ratio","16:9");
});

describe("clip thumbnail media facts", () => {
  it("waits for a decoded frame at the actual source start rather than metadata alone", () => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    const video = thumbnailVideo();
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(2);
    mediaState(video, 1);
    fireEvent.loadedData(video);
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    mediaState(video, 2, true);
    fireEvent.canPlay(video);
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    mediaState(video, 2);
    video.currentTime = 0;
    fireEvent.seeked(video);
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    video.currentTime = 2;
    fireEvent.seeked(video);
    expect(view.container.querySelector('[data-media-state="ready"]')).toBeInTheDocument();
    const canvas = view.container.querySelector("canvas")!;
    expect(canvas).toHaveAttribute("aria-hidden", "true");
    expect(canvas).toHaveAttribute("width", "256");
    expect(canvas).toHaveAttribute("height", "144");
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 256, 144);
    expect(video).not.toHaveAttribute("src");
    expect(video.pause).toHaveBeenCalledOnce();
    expect(video.load).toHaveBeenCalledTimes(2); // Load source, then clear the native decoder.
    expect(view.container.querySelector("video")).toBeNull();
    expect(video).not.toHaveAttribute("autoplay");
  });

  it("shows an event-backed failure without guessing missing files, keys or permissions", () => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    const video = thumbnailVideo();
    fireEvent.error(video);
    expect(view.container.querySelector('[data-media-state="error"]')).toBeInTheDocument();
    expect(screen.getByText("缩略图读取失败")).toBeInTheDocument();
    expect(screen.queryByText(/缺素材|丢失|权限|密钥|Key/)).not.toBeInTheDocument();
    expect(view.container.querySelector("button")).toBeNull();
    expect(video).not.toHaveAttribute("src");
  });

  it.each(["start", "end", "reference", "asset", "person", "task"])("resets on %s identity changes and ignores old media events", change => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    const oldVideo = thumbnailVideo();
    const oldFrameCallback = oldVideo.onseeked!;
    fireEvent.error(oldVideo);
    const updated = { ...clip, ...(change === "start" ? { source_start_seconds: 3 } : change === "end" ? { source_end_seconds: 6 } : change === "reference" ? { source_reference: "workspace://updated.mp4" } : change === "asset" ? { asset_id: "asset-2" } : change === "person" ? { person_render_id: "person-render-2" } : {}) };
    view.rerender(<ClipThumbnail clip={updated} taskId={change === "task" ? "task-2" : "task-1"} />);
    const video = thumbnailVideo();
    expect(video).not.toBe(oldVideo);
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    fireEvent.error(oldVideo);
    fireEvent.loadedMetadata(oldVideo);
    oldFrameCallback.call(oldVideo, new Event("seeked"));
    expect(drawImage).not.toHaveBeenCalled();
    expect(screen.queryByText("缩略图读取失败")).not.toBeInTheDocument();
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(updated.source_start_seconds);
    if (change === "person") expect(video.getAttribute("src")).toContain("person-renders/person-render-2/media");
    mediaState(video, 2);
    fireEvent.seeked(video);
    expect(view.container.querySelector('[data-media-state="ready"]')).toBeInTheDocument();
    expect(video).not.toHaveAttribute("src");
  });

  it("reads a 21-shot storyboard with one active decoder and keeps only bounded static frames", async () => {
    const clips = Array.from({length: 21}, (_, index) => ({...clip, id: `shot-${index}`, source_start_seconds: index,
      source_end_seconds: index + 1}));
    const view = render(<>{clips.map(item => <ClipThumbnail key={item.id} clip={item} taskId="task-1"/>)}</>);
    expect(createdVideos).toHaveLength(1);
    expect(view.container.querySelectorAll("video")).toHaveLength(0);
    for (let index = 0; index < clips.length; index += 1) {
      const active = createdVideos.filter(video => video.hasAttribute("src"));
      expect(active).toHaveLength(1);
      const video = active[0];
      expect(createdVideos).toHaveLength(index + 1);
      fireEvent.loadedMetadata(video);
      expect(video.currentTime).toBe(index);
      mediaState(video, 2);
      fireEvent.seeked(video);
      expect(video).not.toHaveAttribute("src");
      await act(async () => { await Promise.resolve(); });
    }
    expect(createdVideos.filter(video => video.hasAttribute("src"))).toHaveLength(0);
    expect(view.container.querySelectorAll('[data-media-state="ready"]')).toHaveLength(21);
    expect(drawImage).toHaveBeenCalledTimes(21);
    expect([...view.container.querySelectorAll("canvas")].every(canvas => Math.max(canvas.width, canvas.height) <= 256)).toBe(true);
  });

  it("releases the loading decoder and cancels queued reads when the storyboard unmounts", async () => {
    const view = render(<>{[1, 2, 3].map(index => <ClipThumbnail key={index} clip={{...clip, id: `shot-${index}`}} taskId="task-1"/>)}</>);
    const video = thumbnailVideo();
    expect(createdVideos).toHaveLength(1);
    view.unmount();
    await act(async () => { await Promise.resolve(); });
    expect(video).not.toHaveAttribute("src");
    expect(video.pause).toHaveBeenCalledOnce();
    expect(video.load).toHaveBeenCalledTimes(2);
    expect(createdVideos).toHaveLength(1);
    fireEvent.loadedData(video);
    expect(drawImage).not.toHaveBeenCalled();
  });

  it.each(["missing_canvas_context", "frame_capture_failed"])("releases an actual %s boundary failure and starts the next read without live-video fallback", async failure => {
    const view = render(<><ClipThumbnail clip={clip} taskId="task-1"/>
      <ClipThumbnail clip={{...clip, id: "next-shot"}} taskId="task-1"/></>);
    if (failure === "missing_canvas_context") vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValueOnce(null);
    else drawImage.mockImplementationOnce(() => { throw new Error("frame capture unavailable"); });
    const first = thumbnailVideo();
    fireEvent.loadedMetadata(first);
    mediaState(first, 2);
    fireEvent.seeked(first);
    expect(screen.getByText("缩略图读取失败")).toBeInTheDocument();
    expect(first).not.toHaveAttribute("src");
    await act(async () => { await Promise.resolve(); });
    expect(createdVideos).toHaveLength(2);
    expect(thumbnailVideo()).toHaveAttribute("src");
    expect(view.container.querySelector("video")).toBeNull();
  });

  it("only calls the reference missing when neither material nor person render is referenced", () => {
    const view = render(<ClipThumbnail clip={{ ...clip, asset_id: "" }} taskId="task-1" />);
    expect(screen.getByText("未关联媒体")).toBeInTheDocument();
    expect(view.container.querySelector("video")).toBeNull();
  });
});

describe("source and output playback identity", () => {
  it("labels the source, responds to actual errors and retries without a nested storyboard button", () => {
    const view = render(stage());
    const video = screen.getByLabelText("源素材预览") as HTMLVideoElement;
    expect(screen.getByText("源素材 · 原始画面，非成片")).toBeInTheDocument();
    fireEvent.error(video);
    expect(screen.getByRole("alert")).toHaveTextContent("原片暂时无法读取");
    fireEvent.click(screen.getByRole("button", { name: "重新读取原片" }));
    const retried = screen.getByLabelText("源素材预览") as HTMLVideoElement;
    expect(retried).not.toBe(video);
    fireEvent.error(video);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.loadedMetadata(retried);
    mediaState(retried, 2);
    fireEvent.seeked(retried);
    expect(screen.queryByText("正在读取原片…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择镜头 1" })).toHaveAccessibleDescription("读取缩略图…");
    expect(view.container.querySelector("button button")).toBeNull();
    expect(view.container.querySelector('[data-editor-player]')).toHaveAttribute("data-editor-aspect-ratio", "9:16");
    expect(screen.getByText("画面")).toBeInTheDocument();
    expect(screen.getByText("配音")).toBeInTheDocument();
    expect(screen.getByText("字幕")).toBeInTheDocument();
  });

  it("repositions the same source after a trim and separates person render identity", () => {
    const view = render(stage());
    const oldVideo = screen.getByLabelText("源素材预览");
    fireEvent.error(oldVideo);
    view.rerender(stage(workspace({ ...clip, source_start_seconds: 3 })));
    const updated = screen.getByLabelText("源素材预览") as HTMLVideoElement;
    expect(updated).not.toBe(oldVideo);
    fireEvent.loadedMetadata(updated);
    expect(updated.currentTime).toBe(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.error(oldVideo);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    view.rerender(stage(workspace({ ...clip, person_render_id: "person-1" })));
    expect(screen.getByLabelText("人物口型预览").getAttribute("src")).toContain("person-renders/person-1/media");
    expect(screen.getByText("人物口型 · 非成片")).toBeInTheDocument();
  });

  it("clears output errors on version replacement, ignores old errors and retries the same output", () => {
    const view = render(stage(workspace(clip, "/output.mp4")));
    const oldVideo = screen.getByLabelText("剪辑成片预览");
    fireEvent.error(oldVideo);
    expect(screen.getByRole("alert")).toHaveTextContent("成片暂时无法播放");
    view.rerender(stage(workspace(clip, "/output.mp4", "timeline-2", "1:1")));
    const newVideo = screen.getByLabelText("剪辑成片预览") as HTMLVideoElement;
    expect(newVideo).not.toBe(oldVideo);
    expect(newVideo.getAttribute("src")).toContain("version=timeline-2");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.error(oldVideo);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.loadedMetadata(newVideo);
    expect(screen.getByText("正在读取成片…")).toBeInTheDocument();
    mediaState(newVideo, 2);
    fireEvent.loadedData(newVideo);
    expect(screen.queryByText("正在读取成片…")).not.toBeInTheDocument();
    fireEvent.error(newVideo);
    fireEvent.click(screen.getByRole("button", { name: "重新读取成片" }));
    expect(screen.getByLabelText("剪辑成片预览")).not.toBe(newVideo);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("成片画面")).toBeInTheDocument();
    expect(view.container.querySelector('[data-editor-player]')).toHaveAttribute("data-editor-aspect-ratio", "1:1");
    view.rerender(stage(workspace()));
    expect(screen.queryByText("成片暂时无法播放")).not.toBeInTheDocument();
    expect(screen.getByLabelText("源素材预览")).toBeInTheDocument();
  });

  it("does not carry an old rejected play promise into a new output version", async () => {
    const ref = createRef<HTMLVideoElement>();
    const view = render(stage(workspace(clip, "/output.mp4"), ref));
    let reject!: (error: Error) => void;
    vi.spyOn(ref.current!, "play").mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
    fireEvent.click(screen.getByRole("button", { name: "播放成片" }));
    view.rerender(stage(workspace(clip, "/output.mp4", "timeline-2"), ref));
    await act(async () => reject(new Error("controlled media rejection")));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("正在读取成片…")).toBeInTheDocument();
  });
});

describe("complete source in clip details and replacement", () => {
  function inspector(selected = clip, onPreview = vi.fn()) {
    const data = workspace(selected, null, "timeline-1", "9:16");
    data.person_renders = [];
    data.person_render_capability = { available: false } as CreationTaskWorkspace["person_render_capability"];
    return <VideoClipInspector clip={selected} index={0} count={1} workspace={data} busy={false}
      onPreview={onPreview} onDraftChange={vi.fn()} onReplace={vi.fn()} candidates={[]} replacing onPerson={vi.fn()} />;
  }
  function dimensions(video: HTMLVideoElement, width: number, height: number) {
    Object.defineProperty(video, "videoWidth", { configurable: true, value: width });
    Object.defineProperty(video, "videoHeight", { configurable: true, value: height });
    fireEvent.loadedMetadata(video);
  }

  it("shows the real source span after linked speed and retains it for a trim draft", () => {
    const sped = { ...clip, source_start_seconds: 0, source_end_seconds: 6, timeline_start_seconds: 1, timeline_end_seconds: 4 };
    const view = render(inspector(sped));
    expect(screen.getByText("终点（秒）").querySelector("output")).toHaveTextContent("6.0");
    fireEvent.change(screen.getByLabelText("镜头 1 裁剪起点"), { target: { value: "1" } });
    expect(screen.getByText("终点（秒）").querySelector("output")).toHaveTextContent("7.0");
    view.unmount();
    const props = { clip: sped, index: 0, busy: false, draft: { start: 0, mode: "fill" as const, x: .5, y: .5 }, onDraftStateChange: vi.fn(), onDraftChange: vi.fn(), onPreview: vi.fn(), onOpenDetails: vi.fn() };
    const quick = render(<VideoClipQuickEditor {...props} />);
    expect(screen.getByText("当前源画面").parentElement).toHaveTextContent("00:00.0–00:06.0");
    quick.rerender(<VideoClipQuickEditor {...props} draft={{ ...props.draft, start: 1 }} />);
    expect(screen.getByText("当前源画面").parentElement).toHaveTextContent("00:01.0–00:07.0");
  });

  it.each([[1920, 1080], [1080, 1920], [1080, 1080]])("uses the actual %sx%s source independently of the output canvas and crop settings", (width, height) => {
    const onPreview = vi.fn();
    const selected = { ...clip, fit_mode: "fill" as const, crop_x: .1, crop_y: .9 };
    render(inspector(selected, onPreview));
    const video = screen.getByLabelText("镜头源素材预览") as HTMLVideoElement;
    expect(video.style.aspectRatio).toBe("");
    dimensions(video, width, height);
    expect(video.style.aspectRatio).toBe(`${width} / ${height}`);
    expect(video.currentTime).toBe(selected.source_start_seconds);
    expect(video).toHaveAttribute("controls");
    mediaState(video, 2);
    fireEvent.seeked(video);
    expect(screen.queryByText("正在读取原片…")).not.toBeInTheDocument();
    expect(screen.getByText("源素材 · 原始画面，非成片")).toBeInTheDocument();
    expect(screen.getByText("没有满足时长的其他可用镜头，请补充素材。")).toBeInTheDocument();
    expect(onPreview).not.toHaveBeenCalled();
    expect(selected).toMatchObject({ fit_mode: "fill", crop_x: .1, crop_y: .9 });
  });

  it("resets dimensions on failed retry and changed source, ignoring metadata from the previous video", () => {
    const view = render(inspector());
    const oldVideo = screen.getByLabelText("镜头源素材预览") as HTMLVideoElement;
    dimensions(oldVideo, 1080, 1920);
    fireEvent.error(oldVideo);
    expect(screen.getByRole("alert")).toHaveTextContent("原片暂时无法读取");
    fireEvent.click(screen.getByRole("button", { name: "重新读取原片" }));
    const retried = screen.getByLabelText("镜头源素材预览") as HTMLVideoElement;
    expect(retried.style.aspectRatio).toBe("");
    dimensions(oldVideo, 4000, 1000);
    expect(retried.style.aspectRatio).toBe("");
    dimensions(retried, 1920, 1080);
    expect(retried.style.aspectRatio).toBe("1920 / 1080");
    view.rerender(inspector({ ...clip, asset_id: "asset-2" }));
    const changed = screen.getByLabelText("镜头源素材预览") as HTMLVideoElement;
    expect(changed.style.aspectRatio).toBe("");
    dimensions(retried, 1080, 1920);
    expect(changed.style.aspectRatio).toBe("");
    dimensions(changed, 1080, 1080);
    expect(changed.style.aspectRatio).toBe("1080 / 1080");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
