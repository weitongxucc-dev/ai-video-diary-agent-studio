import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import type { CreationTaskMontageClip, CreationTaskWorkspace } from "@/lib/types";
import { ClipThumbnail, VideoEditingStage } from "../VideoEditor";

vi.mock("../NarrationWaveformTrack", () => ({ NarrationWaveformTrack: () => <div>配音</div> }));

const clip: CreationTaskMontageClip = {
  id: "clip-1", asset_id: "asset-1", source_reference: "workspace://synthetic.mp4",
  source_start_seconds: 2, source_end_seconds: 5, timeline_start_seconds: 0, timeline_end_seconds: 3,
  match_score: .9, role: "b_roll", locked: false, needs_confirmation: false,
} as CreationTaskMontageClip;

function mediaState(video: HTMLVideoElement, readyState: number, seeking = false) {
  Object.defineProperty(video, "readyState", { configurable: true, value: readyState });
  Object.defineProperty(video, "seeking", { configurable: true, value: seeking });
}
function workspace(selected = clip, output: string | null = null, version = "timeline-1", ratio = "9:16"): CreationTaskWorkspace {
  return { task: { id: "task-1", current_timeline_version_id: version },
    montage: { clips: [selected], duration_seconds: 3, aspect_ratio: ratio, output_url: output, low_confidence_clip_count: 0 },
    subtitle_tracks: [] } as unknown as CreationTaskWorkspace;
}
function stage(data = workspace(), ref = createRef<HTMLVideoElement>()) {
  return <VideoEditingStage workspace={data} selectedId={clip.id} onSelect={vi.fn()} onPosition={vi.fn()} videoRef={ref} initialTime={0} renderAction={null} />;
}

describe("clip thumbnail media facts", () => {
  it("waits for a decoded frame at the actual source start rather than metadata alone", () => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    const video = view.container.querySelector("video")!;
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
    expect(video).toHaveAttribute("aria-hidden", "true");
    expect(video).not.toHaveAttribute("autoplay");
  });

  it("shows an event-backed failure without guessing missing files, keys or permissions", () => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    fireEvent.error(view.container.querySelector("video")!);
    expect(view.container.querySelector('[data-media-state="error"]')).toBeInTheDocument();
    expect(screen.getByText("缩略图读取失败")).toBeInTheDocument();
    expect(screen.queryByText(/缺素材|丢失|权限|密钥|Key/)).not.toBeInTheDocument();
    expect(view.container.querySelector("button")).toBeNull();
  });

  it.each(["start", "end", "reference", "asset", "person", "task"])("resets on %s identity changes and ignores old media events", change => {
    const view = render(<ClipThumbnail clip={clip} taskId="task-1" />);
    const oldVideo = view.container.querySelector("video")!;
    fireEvent.error(oldVideo);
    const updated = { ...clip, ...(change === "start" ? { source_start_seconds: 3 } : change === "end" ? { source_end_seconds: 6 } : change === "reference" ? { source_reference: "workspace://updated.mp4" } : change === "asset" ? { asset_id: "asset-2" } : change === "person" ? { person_render_id: "person-render-2" } : {}) };
    view.rerender(<ClipThumbnail clip={updated} taskId={change === "task" ? "task-2" : "task-1"} />);
    const video = view.container.querySelector("video")!;
    expect(video).not.toBe(oldVideo);
    expect(screen.getByText("读取缩略图…")).toBeInTheDocument();
    fireEvent.error(oldVideo);
    fireEvent.loadedMetadata(oldVideo);
    expect(screen.queryByText("缩略图读取失败")).not.toBeInTheDocument();
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(updated.source_start_seconds);
    mediaState(video, 2);
    fireEvent.seeked(video);
    expect(view.container.querySelector('[data-media-state="ready"]')).toBeInTheDocument();
    if (change === "person") expect(video.getAttribute("src")).toContain("person-renders/person-render-2/media");
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
