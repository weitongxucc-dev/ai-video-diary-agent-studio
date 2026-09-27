import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClipTimelineAudioPreview } from "../VideoEditor";
import { listenCreationTaskAudio } from "@/lib/api";

vi.mock("@/lib/api", () => ({ apiUrl: (path: string) => path, listenCreationTaskAudio: vi.fn() }));

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
