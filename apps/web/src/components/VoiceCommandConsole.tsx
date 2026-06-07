"use client";

import type { KeyboardEvent } from "react";
import { useState } from "react";
import { PixelIcon } from "@/components/PixelIcon";
import {
  executeVoiceCommand,
  getTodayEpisode,
  previewVoiceCommand,
} from "@/lib/api";
import type { VoiceCommandResponse } from "@/lib/types";

type VoiceCommandConsoleProps = {
  contextLabel?: string;
};

const statusLabels: Record<VoiceCommandResponse["status"], string> = {
  blocked: "需要人工处理",
  executed: "已执行",
  ready: "等待确认",
  unknown: "未识别",
};

export function VoiceCommandConsole({ contextLabel = "全局工作区" }: VoiceCommandConsoleProps) {
  const [transcript, setTranscript] = useState("");
  const [result, setResult] = useState<VoiceCommandResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const primaryOperation = result?.operations[0];
  const statusText = result ? statusLabels[result.status] : "等待输入";
  const intentText = result?.intent ?? "voice_command";
  const transcriptText = result?.summary ?? "输入：继续今天的流程 / 重写开头 / 把 retake-1 保留";

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
          <strong>{busy ? "处理中" : statusText}</strong>
        </div>
      </div>

      <div className="voice-command-actions">
        <button
          className="pixel-button secondary"
          disabled={busy}
          type="button"
          onClick={() => void runCommand("preview")}
        >
          预览
        </button>
        <button
          className="pixel-button"
          disabled={busy || !transcript.trim()}
          type="button"
          onClick={() => void runCommand("execute")}
        >
          执行
        </button>
        <button
          className="pixel-button secondary"
          disabled={busy}
          type="button"
          onClick={() => {
            setTranscript("");
            setResult(null);
            setError(null);
          }}
        >
          清空
        </button>
      </div>
    </section>
  );
}
