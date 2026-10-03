import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopVoiceRecorder } from "../DesktopVoiceRecorder";

const trackStop = vi.fn();
const stream = {
  getTracks: () => [{ stop: trackStop }],
  getAudioTracks: () => [{ label: "桌面麦克风" }],
} as unknown as MediaStream;

class TestMediaRecorder {
  static instances: TestMediaRecorder[] = [];
  static isTypeSupported = vi.fn((type: string) => type === "audio/webm");
  state: RecordingState = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(public stream: MediaStream, public options?: MediaRecorderOptions) {
    TestMediaRecorder.instances.push(this);
  }

  start() { this.state = "recording"; }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["voice"], { type: "audio/webm" }) } as BlobEvent);
    this.onstop?.();
  }
}

function microphone(getUserMedia = vi.fn(async () => stream), inputs = [{ kind: "audioinput", deviceId: "mic-1", label: "桌面麦克风" }]) {
  const enumerateDevices = vi.fn(async () => inputs);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia, enumerateDevices } });
  return { getUserMedia, enumerateDevices };
}

async function beginRecording(buttonName = "开始录音") {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fireEvent.click(screen.getByRole("button", { name: buttonName }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText("3 秒后开始录音")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  vi.useRealTimers();
  expect(screen.getByRole("button", { name: "停止录音" })).toBeInTheDocument();
}

describe("DesktopVoiceRecorder", () => {
  beforeEach(() => {
    TestMediaRecorder.instances = [];
    trackStop.mockClear();
    vi.stubGlobal("MediaRecorder", TestMediaRecorder);
    vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:voice-preview"), revokeObjectURL: vi.fn() });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("records, releases the microphone, previews, and sends the blob only after confirmation", async () => {
    const { getUserMedia } = microphone();
    const onRecorded = vi.fn();
    let clock = 1000;
    const now = vi.spyOn(Date, "now").mockImplementation(() => clock);
    render(<DesktopVoiceRecorder onRecorded={onRecorded} onCancel={vi.fn()} scriptText="今天的文案" />);

    await beginRecording();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(TestMediaRecorder.instances[0].options).toEqual({ mimeType: "audio/webm" });
    expect(screen.getByText("今天的文案")).toBeInTheDocument();
    expect(onRecorded).not.toHaveBeenCalled();

    clock = 3400;
    fireEvent.click(screen.getByRole("button", { name: "停止录音" }));
    expect(trackStop).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("试听本次录音")).toHaveAttribute("src", "blob:voice-preview");
    expect(onRecorded).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "使用录音" }));
    await waitFor(() => expect(onRecorded).toHaveBeenCalledOnce());
    const [blob, metadata] = onRecorded.mock.calls[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(metadata).toEqual({ mimeType: "audio/webm", durationMs: 2400, deviceLabel: "桌面麦克风" });
    expect(await screen.findByText("录音已交给当前任务，保存结果以任务提示为准。")).toBeInTheDocument();
    now.mockRestore();
  });

  it("shows permission refusal and missing device without claiming a recording", async () => {
    const { getUserMedia } = microphone(vi.fn().mockRejectedValueOnce(new DOMException("denied", "NotAllowedError")).mockRejectedValueOnce(new DOMException("missing", "NotFoundError")));
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("没有麦克风权限");
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("没有找到可用麦克风"));
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(screen.queryByLabelText("试听本次录音")).not.toBeInTheDocument();
  });

  it("releases a late permission result after cancel", async () => {
    let grant!: (value: MediaStream) => void;
    microphone(vi.fn(() => new Promise<MediaStream>((resolve) => { grant = resolve; })));
    const onCancel = vi.fn();
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await act(async () => { grant(stream); });
    expect(trackStop).toHaveBeenCalledOnce();
    expect(TestMediaRecorder.instances).toHaveLength(0);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("asks for microphone access before counting down and starts only after three seconds", async () => {
    let grant!: (value: MediaStream) => void;
    const { getUserMedia } = microphone(vi.fn(() => new Promise<MediaStream>((resolve) => { grant = resolve; })));
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(screen.getByText("正在连接麦克风…")).toBeInTheDocument();
    expect(screen.queryByText(/秒后开始录音/)).not.toBeInTheDocument();

    await act(async () => { grant(stream); });
    expect(screen.getByText("3 秒后开始录音")).toBeInTheDocument();
    expect(TestMediaRecorder.instances[0].state).toBe("inactive");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText("2 秒后开始录音")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText("1 秒后开始录音")).toBeInTheDocument();
    expect(TestMediaRecorder.instances[0].state).toBe("inactive");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("button", { name: "停止录音" })).toBeInTheDocument();
    expect(TestMediaRecorder.instances[0].state).toBe("recording");
  });

  it("cancels the countdown, releases the granted device, and cannot start later", async () => {
    microphone();
    const onCancel = vi.fn();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("3 秒后开始录音")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(trackStop).toHaveBeenCalledOnce();
    expect(onCancel).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(TestMediaRecorder.instances[0].state).toBe("inactive");
    expect(screen.queryByRole("button", { name: "停止录音" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始录音" })).toBeInTheDocument();
  });

  it("re-records without retaining the old preview and releases an active stream on unmount", async () => {
    microphone();
    const { unmount } = render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    await beginRecording();
    fireEvent.click(screen.getByRole("button", { name: "停止录音" }));
    await beginRecording("重新录音");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:voice-preview");
    unmount();
    expect(trackStop).toHaveBeenCalledTimes(2);
  });

  it("keeps the preview when the parent rejects the recording", async () => {
    microphone();
    render(<DesktopVoiceRecorder onRecorded={vi.fn().mockRejectedValue(new Error("任务未准备好"))} onCancel={vi.fn()} />);
    await beginRecording();
    fireEvent.click(screen.getByRole("button", { name: "停止录音" }));
    fireEvent.click(screen.getByRole("button", { name: "使用录音" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("保存录音失败：任务未准备好");
    expect(screen.getByLabelText("试听本次录音")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "使用录音" })).toBeEnabled();
  });

  it("uses an explicitly selected microphone without a dropdown", async () => {
    const { getUserMedia } = microphone(vi.fn(async () => stream), [
      { kind: "audioinput", deviceId: "mic-1", label: "桌面麦克风" },
      { kind: "audioinput", deviceId: "mic-2", label: "耳机麦克风" },
    ]);
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    const choice = await screen.findByRole("button", { name: "耳机麦克风" });
    fireEvent.click(choice);
    expect(choice).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await beginRecording();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: { deviceId: { exact: "mic-2" } } });
  });

  it("reports unsupported capture without opening a fake device", () => {
    microphone();
    vi.stubGlobal("MediaRecorder", undefined);
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录音" }));
    expect(screen.getByRole("alert")).toHaveTextContent("当前环境不能使用麦克风录音");
    expect(trackStop).not.toHaveBeenCalled();
  });

  it("reports an unexpected recorder stop instead of offering a partial clip", async () => {
    microphone();
    render(<DesktopVoiceRecorder onRecorded={vi.fn()} onCancel={vi.fn()} />);
    await beginRecording();
    act(() => { TestMediaRecorder.instances[0].stop(); });
    expect(screen.getByRole("alert")).toHaveTextContent("录音意外中断");
    expect(screen.queryByLabelText("试听本次录音")).not.toBeInTheDocument();
    expect(trackStop).toHaveBeenCalledOnce();
  });

  it("keeps the panel open and disables both cancel controls until delayed save succeeds", async () => {
    microphone();
    let finishSave!: () => void;
    const onRecorded = vi.fn(() => new Promise<void>((resolve) => { finishSave = resolve; }));
    const onCancel = vi.fn();
    render(<DesktopVoiceRecorder onRecorded={onRecorded} onCancel={onCancel} />);
    await beginRecording();
    fireEvent.click(screen.getByRole("button", { name: "停止录音" }));
    fireEvent.click(screen.getByRole("button", { name: "使用录音" }));

    expect(screen.getByText("正在保存录音，请等待结果…")).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "取消录音" });
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(close).toBeDisabled();
    expect(cancel).toBeDisabled();
    fireEvent.click(close);
    fireEvent.click(cancel);
    expect(onCancel).not.toHaveBeenCalled();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(trackStop).toHaveBeenCalledOnce();

    await act(async () => { finishSave(); });
    expect(screen.getByText("录音已交给当前任务，保存结果以任务提示为准。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:voice-preview");
  });

  it("restores retry and cancel after a delayed save failure", async () => {
    microphone();
    let failSave!: (error: Error) => void;
    const onRecorded = vi.fn()
      .mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { failSave = reject; }))
      .mockResolvedValueOnce(undefined);
    const onCancel = vi.fn();
    render(<DesktopVoiceRecorder onRecorded={onRecorded} onCancel={onCancel} />);
    await beginRecording();
    fireEvent.click(screen.getByRole("button", { name: "停止录音" }));
    fireEvent.click(screen.getByRole("button", { name: "使用录音" }));
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

    await act(async () => { failSave(new Error("连接中断")); });
    expect(screen.getByRole("alert")).toHaveTextContent("保存录音失败：连接中断");
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "使用录音" })).toBeEnabled();
    expect(screen.getByLabelText("试听本次录音")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "使用录音" }));
    await waitFor(() => expect(onRecorded).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("录音已交给当前任务，保存结果以任务提示为准。")).toBeInTheDocument();
  });
});

it("uses material-note wording without changing the default task wording", async () => {
  const { getUserMedia } = microphone();
  const props = { onRecorded: vi.fn(), onCancel: vi.fn() };
  const view = render(<DesktopVoiceRecorder {...props} purpose="material-notes" />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole("heading", { name: "为已入库素材补口述说明" })).toBeInTheDocument();
  expect(screen.getByText(/核对后另行保存本素材说明/)).toBeInTheDocument();
  expect(getUserMedia).not.toHaveBeenCalled();
  view.rerender(<DesktopVoiceRecorder {...props} />);
  expect(screen.getByRole("heading", { name: "为这条视频录音" })).toBeInTheDocument();
  expect(screen.getByText("录完先试听，确认后再交给当前任务。")).toBeInTheDocument();
});
