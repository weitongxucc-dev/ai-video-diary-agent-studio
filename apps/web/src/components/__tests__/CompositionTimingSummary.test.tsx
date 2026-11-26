import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { VideoTemplateSettings } from "@/lib/types/templates";
import { CompositionTimingSummary } from "../CompositionTimingSummary";

const timing: NonNullable<VideoTemplateSettings["composition_timing"]> = {
  duration_frames: 120,
  cut_frames: [30, 78],
  sound_cues: [{ role: "effect", start_ms: 1300, duration_ms: 300 }],
};

it("uses the saved edit length for frame-rounded suggestions and retains the fixed reference without it", () => {
  const view = render(<CompositionTimingSummary timing={timing} version={1} targetDurationSeconds={3} />);
  expect(screen.getByText("4.000 秒 · 120 帧")).toBeVisible();
  expect(screen.getByText("3.000 秒 · 90 帧")).toBeVisible();
  expect(screen.getByText("0.767 秒 · 第 23 帧")).toBeVisible();
  expect(screen.getByText("1.967 秒 · 第 59 帧")).toBeVisible();
  expect(screen.getByText("音效 0.967–1.200 秒")).toBeVisible();
  expect(screen.getByText(/切开镜头.*导入音频/)).toBeVisible();

  view.rerender(<CompositionTimingSummary timing={timing} version={1} targetDurationSeconds={4} />);
  expect(screen.getByText("音效 1.300–1.600 秒")).toBeVisible();
  view.rerender(<CompositionTimingSummary timing={timing} version={1} />);
  expect(screen.getByText("1.000 秒 · 第 30 帧")).toBeVisible();
  expect(screen.getByText("2.600 秒 · 第 78 帧")).toBeVisible();
  expect(screen.queryByText("本片时长")).not.toBeInTheDocument();
});

it("identifies collapsed and too-short cuts and keeps sound suggestions within the edit", () => {
  render(<CompositionTimingSummary targetDurationSeconds={1} timing={{
    duration_frames: 120,
    cut_frames: [1, 30, 31, 34, 119],
    sound_cues: [
      { role: "effect", start_ms: 1300, duration_ms: 2 },
      { role: "music", start_ms: 3800, duration_ms: 200 },
    ],
  }} />);
  expect(screen.getByText("0.000 秒 · 第 0 帧 · 需调整")).toBeVisible();
  expect(screen.getAllByText("0.267 秒 · 第 8 帧 · 需调整")).toHaveLength(2);
  expect(screen.getByText("0.300 秒 · 第 9 帧 · 需调整")).toBeVisible();
  expect(screen.getByText("1.000 秒 · 第 30 帧 · 需调整")).toBeVisible();
  expect(screen.getByText(/切点重合或不足 2 帧/)).toBeVisible();
  expect(screen.getByText(/音效.*起止点在同一帧/)).toBeVisible();
  expect(screen.getByText("音乐 0.967–1.000 秒")).toBeVisible();
});
