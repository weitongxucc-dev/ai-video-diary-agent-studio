import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CreationTaskWorkspace } from "@/lib/types";
import { NarrationWaveformTrack } from "../NarrationWaveformTrack";

vi.mock("@/lib/api", () => ({ apiUrl: (path: string) => path }));
afterEach(() => vi.unstubAllGlobals());

const workspace = {
  task: { id: "task", narration_cache_key: "audio-one" },
  montage: {
    clips: [{ id: "clip-two", cue_id: "cue-two", timeline_start_seconds: 1, timeline_end_seconds: 2 }],
    cues: [{ id: "cue-two", text: "接口特写" }],
  },
} as CreationTaskWorkspace;

it("draws the corresponding audio range and selects its shot; never keeps old peaks after audio changes", async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ source_id: "source", duration_seconds: 2, seconds_per_peak: .5, peaks: [0, 0, .5, 1] }) });
  vi.stubGlobal("fetch", fetcher);
  const select = vi.fn();
  const view = render(<NarrationWaveformTrack workspace={workspace} selectedId="clip-two" onSelect={select} />);
  const button = await screen.findByRole("button", { name: "定位配音：接口特写" });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(button.querySelector("path")?.getAttribute("d")).toBe("M0,6.5V17.5 M0.5,1V23");
  fireEvent.click(button);
  expect(select).toHaveBeenCalledWith("clip-two");
  fetcher.mockImplementation(() => new Promise(() => {}));
  view.rerender(<NarrationWaveformTrack workspace={{ ...workspace, task: { ...workspace.task, narration_cache_key: "audio-two" } }} selectedId={null} onSelect={select} />);
  expect(screen.queryByRole("button", { name: "定位配音：接口特写" })).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("正在读取");
});

it("shows a retry action on a failed read instead of invented waveform data", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  render(<NarrationWaveformTrack workspace={workspace} selectedId={null} onSelect={() => {}} />);
  const retry = await screen.findByRole("button", { name: "波形读取失败 · 重试" });
  expect(document.querySelector("svg")).toBeNull();
  await act(async () => fireEvent.click(retry));
  expect(fetch).toHaveBeenCalledTimes(2);
});
