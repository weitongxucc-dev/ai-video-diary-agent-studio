import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClipTimelineAudioPreview } from "../VideoEditor";
import { listenCreationTaskAudio } from "@/lib/api";

vi.mock("@/lib/api", async importOriginal => ({ ...(await importOriginal<typeof import("@/lib/api")>()), apiUrl: (path: string) => path, listenCreationTaskAudio: vi.fn() }));

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("current shot audio preview", () => {
  it("requests the current timeline range and all mixed tracks", async () => {
    vi.mocked(listenCreationTaskAudio).mockResolvedValue({audio_url:"/actual-mix.wav"});
    render(<ClipTimelineAudioPreview taskId="task" versionId="version" start={4} end={7} busy={false} />);
    fireEvent.click(screen.getByRole("button",{name:"试听声音"}));
    const audio=await screen.findByLabelText("当前镜头混音");
    expect(listenCreationTaskAudio).toHaveBeenCalledWith("task",{base_version_id:"version",start_seconds:4,end_seconds:7,solo_track_ids:[]});
    expect(audio.getAttribute("src")).toBe("/actual-mix.wav");
  });
  it("reports a failure without presenting a fake player",async () => {
    vi.mocked(listenCreationTaskAudio).mockRejectedValue(new Error("时间线已经变化"));
    render(<ClipTimelineAudioPreview taskId="task" versionId="old" start={4} end={7} busy={false} />);
    fireEvent.click(screen.getByRole("button",{name:"试听声音"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("时间线已经变化");
    expect(screen.queryByLabelText("当前镜头混音")).not.toBeInTheDocument();
  });
});

it("discards a late mix after the task or base version changes", async () => {
  let finish!: (value: { audio_url: string }) => void;
  vi.mocked(listenCreationTaskAudio).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const { rerender } = render(<ClipTimelineAudioPreview taskId="old-task" versionId="old-version" start={4} end={7} busy={false} />);
  fireEvent.click(screen.getByRole("button", { name: "试听声音" }));
  rerender(<ClipTimelineAudioPreview taskId="new-task" versionId="new-version" start={4} end={7} busy={false} />);
  await act(async () => finish({ audio_url: "/old-mix.wav" }));
  expect(screen.queryByLabelText("当前镜头混音")).not.toBeInTheDocument();
});

it("hides a successful mix when its base changes", async () => {
  vi.mocked(listenCreationTaskAudio).mockResolvedValueOnce({ audio_url: "/old-mix.wav" });
  const { rerender } = render(<ClipTimelineAudioPreview taskId="task" versionId="old-version" start={4} end={7} busy={false} />);
  fireEvent.click(screen.getByRole("button", { name: "试听声音" }));
  await screen.findByLabelText("当前镜头混音");
  rerender(<ClipTimelineAudioPreview taskId="task" versionId="new-version" start={4} end={7} busy={false} />);
  expect(screen.queryByLabelText("当前镜头混音")).not.toBeInTheDocument();
});

it("shows resource waiting without a player until the persistent mix succeeds", async () => {
  const job = { operation: "audio_listen", target_refs: [], input_digest: "opaque", created_at: "2026-10-11T10:00:00Z", job_id: "mix-job", status: "queued" as const, result_kind: "audio_listen" as const, result_url: "/api/creation-tasks/task/media-jobs/mix-job", device_request_id: null, waiting_reason: "等待设备空闲", base_version_id: "version", result: null, failure_reason: null, repair_action: null };
  vi.mocked(listenCreationTaskAudio).mockResolvedValueOnce(job);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...job, status: "succeeded", result: { audio_url: "/persisted-mix.wav" } }))));
  render(<ClipTimelineAudioPreview taskId="task" versionId="version" start={4} end={7} busy={false} />);
  fireEvent.click(screen.getByRole("button", { name: "试听声音" }));
  expect(await screen.findByRole("status")).toHaveTextContent("等待设备空闲");
  expect(screen.queryByLabelText("当前镜头混音")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(await screen.findByLabelText("当前镜头混音", {}, { timeout: 3500 })).toHaveAttribute("src", "/persisted-mix.wav");
});

it("cancels the persistent mix without showing an error or player", async () => {
  const job = { operation: "audio_listen", target_refs: [], input_digest: "opaque", created_at: "2026-10-11T10:00:00Z", job_id: "mix-job", status: "queued" as const, result_kind: "audio_listen" as const, result_url: "/api/creation-tasks/task/media-jobs/mix-job", device_request_id: null, waiting_reason: null, base_version_id: "version", result: null, failure_reason: null, repair_action: null };
  vi.mocked(listenCreationTaskAudio).mockResolvedValueOnce(job);
  const fetch = vi.fn(async () => new Response(JSON.stringify({ ...job, status: "cancelled" })));
  vi.stubGlobal("fetch", fetch);
  render(<ClipTimelineAudioPreview taskId="task" versionId="version" start={4} end={7} busy={false} />);
  fireEvent.click(screen.getByRole("button", { name: "试听声音" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消试听" }));
  expect(await screen.findByRole("status")).toHaveTextContent("预览已取消");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("当前镜头混音")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task/media-jobs/mix-job/cancel", expect.objectContaining({ method: "POST" }));
});
