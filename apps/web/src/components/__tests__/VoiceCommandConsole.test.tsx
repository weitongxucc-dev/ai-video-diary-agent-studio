import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  VoiceCommandConsoleView,
  type VoiceCommandConsoleViewProps,
  type VoiceCommandViewState,
} from "@/components/VoiceCommandConsole";

function renderView(
  viewState: VoiceCommandViewState,
  overrides: Partial<VoiceCommandConsoleViewProps> = {},
) {
  const props: VoiceCommandConsoleViewProps = {
    contextLabel: "剪辑计划",
    duration: 7,
    expanded: true,
    onClear: vi.fn(),
    onExecute: vi.fn(),
    onPreview: vi.fn(),
    onToggleExpanded: vi.fn(),
    onToggleRecording: vi.fn(),
    onTranscriptChange: vi.fn(),
    preview: {
      operation: "保留片段",
      scope: "Episode #18",
      transcript: "保留第一段",
    },
    transcript: "保留第一段",
    viewState,
    ...overrides,
  };
  render(<VoiceCommandConsoleView {...props} />);
  return props;
}

describe("VoiceCommandConsoleView", () => {
  it("keeps an empty idle preview neutral and disabled", () => {
    const props = renderView("idle", { transcript: "   " });
    const preview = screen.getByRole("button", { name: "预览指令" });
    expect(preview).toBeDisabled();
    expect(preview).toHaveClass("command-submit-button");
    fireEvent.keyDown(screen.getByRole("textbox", { name: "语音指令文本" }), {
      key: "Enter",
    });
    expect(props.onPreview).not.toHaveBeenCalled();
  });

  it("renders recording state and lets the user stop recording", () => {
    const props = renderView("recording");
    expect(screen.getByRole("status")).toHaveTextContent("录音中 00:07");
    const mic = screen.getByRole("button", { name: "停止录音" });
    expect(mic).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(mic);
    expect(props.onToggleRecording).toHaveBeenCalledOnce();
    const preview = screen.getByRole("button", { name: "预览指令" });
    expect(preview).toBeDisabled();
    expect(preview).toHaveClass("command-submit-button");
  });

  it("keeps transcribing preview neutral and disabled", () => {
    renderView("transcribing");
    const preview = screen.getByRole("button", { name: "预览指令" });
    expect(preview).toBeDisabled();
    expect(preview).toHaveClass("command-submit-button");
  });

  it("renders previewing as a busy, non-executable state", () => {
    renderView("previewing");
    expect(screen.getByRole("status")).toHaveTextContent("正在预览");
    expect(screen.getByRole("button", { name: "预览指令" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByRole("button", { name: "预览指令" })).toHaveClass("command-submit-button");
    expect(screen.queryByRole("button", { name: "确认并执行" })).not.toBeInTheDocument();
  });

  it("shows the confirm action only while waiting for the user", () => {
    const props = renderView("waiting_for_user");
    expect(screen.getByRole("status")).toHaveTextContent("等待确认");
    const confirm = screen.getByRole("button", { name: "确认并执行" });
    expect(confirm).toHaveClass("command-confirm-button");
    expect(screen.getByRole("button", { name: "预览指令" })).toHaveClass("command-submit-button");
    fireEvent.click(confirm);
    expect(props.onExecute).toHaveBeenCalledOnce();
  });

  it.each([
    ["blocked", "需要人工处理"],
    ["failed", "需要修复"],
    ["succeeded", "已执行"],
  ] as const)("renders %s with explicit text feedback", (state, label) => {
    renderView(state);
    expect(screen.getByRole("status")).toHaveTextContent(label);
    expect(screen.queryByRole("button", { name: "确认并执行" })).not.toBeInTheDocument();
  });

  it("keeps transcript editing and Enter preview controlled by the owner", () => {
    const props = renderView("idle", { expanded: false });
    const input = screen.getByRole("textbox", { name: "语音指令文本" });
    fireEvent.change(input, { target: { value: "重写开头" } });
    expect(props.onTranscriptChange).toHaveBeenCalledWith("重写开头");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onPreview).toHaveBeenCalledOnce();
  });
});
