import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { adoptNarrationDuration, readNarrationDuration, type NarrationDurationStatus } from "@/lib/api/narration-duration";
import { NarrationDurationReviewPanel } from "../creation-task/NarrationStage";

vi.mock("@/lib/api/narration-duration", () => ({
  adoptNarrationDuration: vi.fn(),
  readNarrationDuration: vi.fn(),
}));

function progress(taskId: string, completed: number, measured: number): NarrationDurationStatus {
  return {
    task_id: taskId, completed_sections: completed, total_sections: 36, measured_seconds: measured,
    target_seconds: 151, minimum_delivery_seconds: measured, exceeds_target: false, review_id: null,
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});

it("retains the last confirmed count during a read failure and clears the error on recovery", async () => {
  vi.mocked(readNarrationDuration)
    .mockResolvedValueOnce(progress("english", 18, 84.48))
    .mockRejectedValueOnce(new Error("暂时无法读取配音进度。"))
    .mockResolvedValueOnce(progress("english", 19, 89.28));
  await act(async () => {
    render(<NarrationDurationReviewPanel taskId="english" />);
  });
  expect(screen.getByText(/18 \/ 36 段.*84\.48 秒/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "采用完整配音时长" })).not.toBeInTheDocument();

  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText(/18 \/ 36 段.*84\.48 秒/)).toBeInTheDocument();
  expect(screen.getByText("暂时无法读取配音进度。")).toBeInTheDocument();

  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText(/19 \/ 36 段.*89\.28 秒/)).toBeInTheDocument();
  expect(screen.queryByText("暂时无法读取配音进度。")).not.toBeInTheDocument();
});

it("ignores a late result for the work that was closed", async () => {
  let finishEnglish!: (value: NarrationDurationStatus) => void;
  const englishRead = new Promise<NarrationDurationStatus>(resolve => { finishEnglish = resolve; });
  vi.mocked(readNarrationDuration).mockImplementation(id => id === "english"
    ? englishRead : Promise.resolve(progress("chinese", 1, 4.48)));

  const view = render(<NarrationDurationReviewPanel key="english:script" taskId="english" />);
  await act(async () => {
    view.rerender(<NarrationDurationReviewPanel key="chinese:script" taskId="chinese" />);
  });
  expect(screen.getByText(/1 \/ 36 段.*4\.48 秒/)).toBeInTheDocument();
  await act(async () => { finishEnglish(progress("english", 20, 94.8)); });
  expect(screen.queryByText(/20 \/ 36 段/)).not.toBeInTheDocument();
  expect(screen.getByText(/1 \/ 36 段.*4\.48 秒/)).toBeInTheDocument();
});

it("continues to use the original adoption request after an unknown result", async () => {
  const review = { ...progress("english", 36, 165), exceeds_target: true, review_id: "original-review" };
  let finishStaleRead!: (value: NarrationDurationStatus) => void;
  const staleRead = new Promise<NarrationDurationStatus>(resolve => { finishStaleRead = resolve; });
  vi.mocked(readNarrationDuration).mockResolvedValueOnce(review).mockReturnValueOnce(staleRead);
  vi.mocked(adoptNarrationDuration)
    .mockRejectedValueOnce(new Error("connection closed"))
    .mockImplementationOnce(async (taskId, input) => ({
      task_id: taskId, request_id: input.request_id, job_id: "original-job",
      plan_updated_at: "2026-11-19T14:00:00Z", duration_target_seconds: 165,
      measured_seconds: 165, completed_sections: 36,
    }));
  await act(async () => { render(<NarrationDurationReviewPanel taskId="english" />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "采用完整配音时长" })); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "查询原保存结果" })); });
  expect(vi.mocked(adoptNarrationDuration).mock.calls[1]).toEqual(vi.mocked(adoptNarrationDuration).mock.calls[0]);
  await act(async () => { finishStaleRead(review); });
  expect(screen.getByText(/36 \/ 36 段.*目标 165\.00 秒/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "采用完整配音时长" })).not.toBeInTheDocument();
});
