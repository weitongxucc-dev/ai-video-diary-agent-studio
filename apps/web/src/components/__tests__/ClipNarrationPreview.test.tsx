import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClipNarrationPreview } from "../VideoEditor";

vi.mock("@/lib/api", () => ({ apiUrl: (path: string) => path }));

describe("current shot narration preview", () => {
  it("starts within a shared narration segment, stops at the shot end and replays its range", () => {
    render(<ClipNarrationPreview audioUrl="/audio/section.wav" start={4} duration={3} />);
    const audio = screen.getByLabelText("当前镜头配音") as HTMLAudioElement;
    const pause = vi.spyOn(audio, "pause").mockImplementation(() => undefined);
    expect(audio.getAttribute("src")).toBe("/audio/section.wav#t=4.000,7.000");
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBe(4);
    audio.currentTime = 7.1;
    fireEvent.timeUpdate(audio);
    expect(pause).toHaveBeenCalledOnce();
    expect(audio.currentTime).toBe(7);
    fireEvent.play(audio);
    expect(audio.currentTime).toBe(4);
    audio.currentTime = 1;
    fireEvent.seeking(audio);
    expect(audio.currentTime).toBe(4);
    audio.currentTime = 9;
    fireEvent.seeking(audio);
    expect(audio.currentTime).toBe(7);
  });
});
