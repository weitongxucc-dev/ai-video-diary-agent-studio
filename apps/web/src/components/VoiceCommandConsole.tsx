"use client";

import { Mic, MicOff } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { PixelIcon } from "@/components/PixelIcon";
import {
  PixelButton,
  PixelStatus,
  type PixelStatusKind,
} from "@/components/PixelPrimitives";
import {
  executeVoiceCommand,
  getTodayEpisode,
  previewVoiceCommand,
  transcribeVoice,
} from "@/lib/api";
import type { VoiceCommandResponse } from "@/lib/types";
import { useVoiceRecorder } from "@/lib/useVoiceRecorder";

export type VoiceCommandConsoleProps = {
  contextLabel?: string;
};

export type VoiceCommandViewState =
  | "idle"
  | "recording"
  | "transcribing"
  | "previewing"
  | "executing"
  | "waiting_for_user"
  | "blocked"
  | "failed"
  | "succeeded";

export type VoiceCommandPreview = {
  transcript: string;
  operation: string;
  scope: string;
};

export type VoiceCommandConsoleViewProps = {
  contextLabel: string;
  transcript: string;
  viewState: VoiceCommandViewState;
  duration?: number;
  expanded: boolean;
  preview: VoiceCommandPreview;
  onTranscriptChange: (value: string) => void;
  onPreview: () => void;
  onExecute: () => void;
  onToggleRecording: () => void;
  onToggleExpanded: () => void;
  onClear: () => void;
};

const responseLabels: Record<VoiceCommandResponse["status"], string> = {
  blocked: "需要人工处理",
  executed: "已执行",
  ready: "等待确认",
  unknown: "未识别",
};

const viewStateLabels: Record<VoiceCommandViewState, string> = {
  blocked: "需要人工处理",
  executing: "正在执行",
  failed: "需要修复",
  idle: "等待输入",
  previewing: "正在预览",
  recording: "录音中",
  succeeded: "已执行",
  transcribing: "正在转写",
  waiting_for_user: "等待确认",
};

const viewStateKinds: Record<VoiceCommandViewState, PixelStatusKind> = {
  blocked: "blocked",
  executing: "running",
  failed: "failed",
  idle: "idle",
  previewing: "running",
  recording: "running",
  succeeded: "succeeded",
  transcribing: "running",
  waiting_for_user: "waiting_for_user",
};

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function VoiceCommandConsoleView({
  contextLabel,
  transcript,
  viewState,
  duration = 0,
  expanded,
  preview,
  onTranscriptChange,
  onPreview,
  onExecute,
  onToggleRecording,
  onToggleExpanded,
  onClear,
}: VoiceCommandConsoleViewProps) {
  const isRecording = viewState === "recording";
  const isTranscribing = viewState === "transcribing";
  const isProcessing =
    isTranscribing || viewState === "previewing" || viewState === "executing";
  const statusLabel = isRecording
    ? `录音中 ${formatDuration(duration)}`
    : viewStateLabels[viewState];
  const micLabel = isRecording
    ? "停止录音"
    : isTranscribing
      ? "正在转写"
      : "开始录音";

  return (
    <section
      aria-label="语音指令控制台"
      className={`voice-command-console${expanded ? " is-expanded" : ""}`}
    >
      <div className="voice-command-status">
        <PixelIcon name="agent" />
        <div>
          <strong>COMMAND DOCK</strong>
          <span>{contextLabel}</span>
        </div>
      </div>

      <div className="voice-command-line">
        <label htmlFor="voice-command-input">&gt;</label>
        <input
          aria-label="语音指令文本"
          disabled={isProcessing}
          id="voice-command-input"
          name="transcript"
          onChange={(event) => onTranscriptChange(event.target.value)}
          onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Enter" && !isProcessing && !isRecording) {
              event.preventDefault();
              onPreview();
            }
          }}
          placeholder="说出或输入生产指令..."
          type="text"
          value={transcript}
        />
        <button
          aria-label={micLabel}
          aria-pressed={isRecording}
          className={`voice-command-mic${isRecording ? " recording" : ""}${isTranscribing ? " transcribing" : ""}`}
          disabled={isProcessing}
          onClick={onToggleRecording}
          title={
            isRecording
              ? `停止录音（${formatDuration(duration)}）`
              : isTranscribing
                ? "正在转写..."
                : "点击开始录音"
          }
          type="button"
        >
          {isTranscribing ? <MicOff aria-hidden="true" /> : <Mic aria-hidden="true" />}
          {isRecording ? (
            <span aria-live="off" className="voice-command-mic-timer">
              {formatDuration(duration)}
            </span>
          ) : null}
        </button>
      </div>

      <div className="voice-command-actions">
        <div aria-live="polite" role="status">
          <PixelStatus kind={viewStateKinds[viewState]} label={statusLabel} />
        </div>
        <PixelButton
          disabled={isProcessing || isRecording}
          loading={viewState === "previewing"}
          onClick={onPreview}
          variant="primary"
        >
          预览指令
        </PixelButton>
        <PixelButton
          aria-controls="voice-command-details"
          aria-expanded={expanded}
          onClick={onToggleExpanded}
          size="compact"
          variant="secondary"
        >
          {expanded ? "收起详情" : "展开详情"}
        </PixelButton>
      </div>

      <div
        className="voice-command-details"
        data-scroll-region="command-details-y"
        hidden={!expanded}
        id="voice-command-details"
      >
        <div aria-label="语音指令解析预览" className="voice-command-preview">
          <div>
            <span>转写</span>
            <strong title={preview.transcript}>{preview.transcript}</strong>
          </div>
          <div>
            <span>解析</span>
            <strong title={preview.operation}>{preview.operation}</strong>
          </div>
          <div>
            <span>范围与状态</span>
            <strong>{preview.scope}</strong>
          </div>
        </div>

        <div className="voice-command-confirm-actions">
          {viewState === "waiting_for_user" ? (
            <PixelButton
              disabled={!transcript.trim()}
              onClick={onExecute}
              variant="primary"
            >
              确认并执行
            </PixelButton>
          ) : null}
          <PixelButton
            disabled={isProcessing || isRecording}
            onClick={onClear}
            variant="secondary"
          >
            清除指令
          </PixelButton>
        </div>
      </div>
    </section>
  );
}

export function VoiceCommandConsole({
  contextLabel = "全局工作区",
}: VoiceCommandConsoleProps) {
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState<VoiceCommandResponse | null>(null);
  const [busyMode, setBusyMode] = useState<"preview" | "execute" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const recorder = useVoiceRecorder();
  const {
    state: recorderState,
    audioBlob,
    duration,
    reset: recorderReset,
    startRecording: recorderStart,
    stopRecording: recorderStop,
  } = recorder;
  const isRecording = recorderState === "recording";
  const isTranscribing = transcribing || recorderState === "transcribing";

  const lastProcessedBlobRef = useRef<Blob | null>(null);

  useEffect(() => {
    if (recorderState !== "done" || !audioBlob) {
      return;
    }
    if (audioBlob === lastProcessedBlobRef.current) {
      return;
    }
    lastProcessedBlobRef.current = audioBlob;

    const blob = audioBlob;
    let cancelled = false;

    (async () => {
      setTranscribing(true);
      setError(null);
      setResult(null);
      try {
        const transcriptResult = await transcribeVoice(blob);
        if (cancelled) {
          return;
        }
        const recognized =
          typeof transcriptResult.transcript === "string"
            ? transcriptResult.transcript.trim()
            : "";
        if (recognized.length === 0) {
          setError("转写结果为空，请重新录制");
          setExpanded(true);
        } else {
          setTranscript(recognized);
        }
      } catch (transcribeError) {
        if (!cancelled) {
          setError(
            transcribeError instanceof Error
              ? transcribeError.message
              : "语音转写失败",
          );
          setExpanded(true);
        }
      } finally {
        if (!cancelled) {
          setTranscribing(false);
          lastProcessedBlobRef.current = null;
          recorderReset();
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [recorderState, audioBlob, recorderReset]);

  async function currentEpisodeId(): Promise<string> {
    const episode = await getTodayEpisode();
    return episode.id;
  }

  async function runCommand(mode: "preview" | "execute") {
    const normalized = transcript.trim();
    if (!normalized) {
      setError("请输入一句指令");
      setResult(null);
      setExpanded(true);
      return;
    }

    setBusyMode(mode);
    setError(null);
    try {
      const episodeId = await currentEpisodeId();
      const response =
        mode === "preview"
          ? await previewVoiceCommand(episodeId, normalized)
          : await executeVoiceCommand(episodeId, normalized);
      setResult(response);
      setExpanded(true);
      if (mode === "execute" && response.status === "executed" && response.next_url) {
        window.location.assign(response.next_url);
      }
    } catch (commandError) {
      setError(commandError instanceof Error ? commandError.message : "指令执行失败");
      setResult(null);
      setExpanded(true);
    } finally {
      setBusyMode(null);
    }
  }

  async function toggleRecording() {
    if (isTranscribing) {
      return;
    }
    try {
      if (isRecording) {
        await recorderStop();
      } else {
        setError(null);
        await recorderStart();
      }
    } catch (toggleError) {
      setError(
        toggleError instanceof Error
          ? toggleError.message
          : "麦克风操作失败",
      );
      setExpanded(true);
    }
  }

  const primaryOperation = result?.operations[0];
  const resultLabel = result ? responseLabels[result.status] : "等待输入";
  const intentText = result?.intent ?? "voice_command";
  const transcriptText =
    result?.summary ?? "输入：继续今天的流程 / 重写开头 / 把 retake-1 保留";

  const viewState = useMemo<VoiceCommandViewState>(() => {
    if (error || result?.status === "unknown") {
      return "failed";
    }
    if (isRecording) {
      return "recording";
    }
    if (isTranscribing) {
      return "transcribing";
    }
    if (busyMode === "preview") {
      return "previewing";
    }
    if (busyMode === "execute") {
      return "executing";
    }
    if (result?.status === "executed") {
      return "succeeded";
    }
    if (result?.status === "blocked") {
      return "blocked";
    }
    if (result?.status === "ready") {
      return "waiting_for_user";
    }
    return "idle";
  }, [busyMode, error, isRecording, isTranscribing, result?.status]);

  return (
    <VoiceCommandConsoleView
      contextLabel={contextLabel}
      duration={duration}
      expanded={expanded}
      onClear={() => {
        setTranscript("");
        setResult(null);
        setError(null);
        recorderReset();
      }}
      onExecute={() => void runCommand("execute")}
      onPreview={() => void runCommand("preview")}
      onToggleExpanded={() => setExpanded((open) => !open)}
      onToggleRecording={() => void toggleRecording()}
      onTranscriptChange={setTranscript}
      preview={{
        transcript: error ?? transcriptText,
        operation: primaryOperation?.label ?? intentText,
        scope: error ?? resultLabel,
      }}
      transcript={transcript}
      viewState={viewState}
    />
  );
}
