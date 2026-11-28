import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { FirstSubtitleCueEditor } from "../creation-task/FirstSubtitleCueEditor";

it("submits an explicitly chosen subtitle language and valid range without losing invalid input", () => {
  const onSave = vi.fn();
  const props = { versionId: "base", duration: 2, language: "zh-CN" as const,
    onSave, canRedo: false, onRedo: vi.fn(), busy: false };
  const { rerender } = render(<FirstSubtitleCueEditor {...props} />);
  const text = screen.getByLabelText("字幕文字");
  const start = screen.getByLabelText("开始（秒）");
  const end = screen.getByLabelText("结束（秒）");
  const save = screen.getByRole("button", { name: "保存字幕" });
  fireEvent.change(text, { target: { value: "Original audio" } });
  fireEvent.change(start, { target: { value: "1" } });
  fireEvent.click(screen.getByRole("radio", { name: "英文" }));
  expect(onSave).not.toHaveBeenCalled();
  fireEvent.click(save);
  expect(onSave).not.toHaveBeenCalled();
  expect(text).toHaveValue("Original audio");
  fireEvent.change(end, { target: { value: "1.25" } });
  fireEvent.click(save);
  expect(onSave).toHaveBeenCalledWith({
    operation_type: "AddFirstSubtitleCue", text: "Original audio",
    start_seconds: 1, end_seconds: 1.25, language: "en-US",
  });
  rerender(<FirstSubtitleCueEditor {...props} busy />);
  expect(save).toBeDisabled();
  expect(text).toHaveValue("Original audio");
});
