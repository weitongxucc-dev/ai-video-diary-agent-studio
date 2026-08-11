"use client";

import { Mic, MicOff } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { PixelIcon } from "@/components/PixelIcon";
import {
  executeVoiceCommand,
  getTodayEpisode,
  previewVoiceCommand,
  transcribeVoice,
} from "@/lib/api";
import type { VoiceCommandResponse } from "@/lib/types";
import { useVoiceRecorder } from "@/lib/useVoiceRecorder";

type VoiceCommandConsoleProps = {
  contextLabel?: string;
};

const statusLabels: Record<VoiceCommandResponse["status"], string> = {
  blocked: "需要人工处理",
  executed: "已执行",
  ready: "等待确认",
  unknown: "未识别",
};

function formatDuration(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const remainder = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function VoiceCommandConsole({ contextLabel = "全局工作区" }: VoiceCommandConsoleProps) {
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState<VoiceCommandResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transcribing, setTranscribing] = useState(false);

  const recorder = useVoiceRecorder();
  const { state: recorderState, audioBlob, duration, reset: recorderReset, startRecording: recorderStart, stopRecording: recorderStop } = recorder;
  const isRecording = recorderState === "recording";
  const isTranscribing = transcribing || recorderState === "transcribing";
  const recorderBusy = isRecording || isTranscribing;

  // Track the last-processed blob to prevent duplicate uploads
  const lastProcessedBlobRef = useRef<Blob | null>(null);

  // Upload + transcribe: recording done → call transcribeVoice → fill transcript
  useEffect(() => {
    if (recorderState !== "done" || !audioBlob) {
      return;
    }
    // Guard against duplicate processing of the same blob
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
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const episodeId = await currentEpisodeId();
      const response =
        mode === "preview"
          ? await previewVoiceCommand(episodeId, normalized)
          : await executeVoiceCommand(episodeId, normalized);
      setResult(response);
      if (mode === "execute" && response.status === "executed" && response.next_url) {
        window.location.assign(response.next_url);
      }
    } catch (commandError) {
      setError(commandError instanceof Error ? commandError.message : "指令执行失败");
      setResult(null);
    } finally {
      setBusy(false);
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
    }
  }

  const primaryOperation = result?.operations[0];
  const statusText = result ? statusLabels[result.status] : "等待输入";
  const intentText = result?.intent ?? "voice_command";
  const transcriptText = result?.summary ?? "输入：继续今天的流程 / 重写开头 / 把 retake-1 保留";

  const micLabel = useMemo(() => {
    if (isRecording) {
      return "停止录音";
    }
    if (isTranscribing) {
      return "正在转写";
    }
    return "开始录音";
  }, [isRecording, isTranscribing]);

  return (
    <section className="voice-command-console" aria-label="语音指令控制台">
      <div className="voice-command-status">
        <PixelIcon name="agent" />
        <div>
          <strong>COMMAND</strong>
          <span>语音指令 · {contextLabel}</span>
        </div>
      </div>

      <div className="voice-command-line">
        <label htmlFor="voice-command-input">&gt;</label>
        <input
          id="voice-command-input"
          name="transcript"
          type="text"
          value={transcript}
          onChange={(event) => setTranscript(event.target.value)}
          onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void runCommand("preview");
            }
          }}
          placeholder="说出或输入生产指令..."
          aria-label="语音指令文本"
        />
        <button
          aria-label={micLabel}
          aria-pressed={isRecording}
          className={`voice-command-mic${isRecording ? " recording" : ""}${isTranscribing ? " transcribing" : ""}`}
          disabled={isTranscribing}
          onClick={() => void toggleRecording()}
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
            <span className="voice-command-mic-timer" aria-live="off">
              {formatDuration(duration)}
            </span>
          ) : null}
        </button>
      </div>

      <div className="voice-command-preview" aria-label="语音指令解析预览">
        <div>
          <span>转写</span>
          <strong title={error ?? transcriptText}>{error ?? transcriptText}</strong>
        </div>
        <div>
          <span>解析</span>
          <strong title={primaryOperation?.label ?? intentText}>
            {primaryOperation?.label ?? intentText}
          </strong>
        </div>
        <div>
          <span>状态</span>
          <strong>
            {isTranscribing
              ? "转写中"
              : busy
                ? "处理中"
                : isRecording
                  ? `录音中 ${formatDuration(duration)}`
                  : statusText}
          </strong>
        </div>
      </div>

      <div className="voice-command-actions">
        <button
          className="pixel-button secondary"
          disabled={busy || recorderBusy}
          type="button"
          onClick={() => void runCommand("preview")}
        >
          预览
        </button>
        <button
          className="pixel-button"
          disabled={busy || recorderBusy || !transcript.trim()}
          type="button"
          onClick={() => void runCommand("execute")}
        >
          执行
        </button>
        <button
          className="pixel-button secondary"
          disabled={busy || recorderBusy}
          type="button"
          onClick={() => {
            setTranscript("");
            setResult(null);
            setError(null);
            recorderReset();
          }}
        >
          清空
        </button>
      </div>
    </section>
  );
}
