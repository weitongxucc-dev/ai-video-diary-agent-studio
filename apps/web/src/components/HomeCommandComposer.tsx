"use client";

import Link from "next/link";
import { ArrowUp, Check, LoaderCircle, Mic, Paperclip, Sparkles } from "lucide-react";
import { useState } from "react";
import { executeVoiceCommand, previewVoiceCommand } from "@/lib/api";
import type { VoiceCommandResponse } from "@/lib/types";

type EpisodeOption = { id: string; title: string; status: string };

export function HomeCommandComposer({ episodes }: { episodes: EpisodeOption[] }) {
  const [episodeId, setEpisodeId] = useState(episodes[0]?.id ?? "");
  const [transcript, setTranscript] = useState("");
  const [preview, setPreview] = useState<VoiceCommandResponse | null>(null);
  const [busy, setBusy] = useState<"preview" | "execute" | null>(null);
  const [error, setError] = useState("");

  async function run(mode: "preview" | "execute") {
    const text = transcript.trim();
    if (!text || !episodeId) return;
    setBusy(mode);
    setError("");
    try {
      const result =
        mode === "preview"
          ? await previewVoiceCommand(episodeId, text)
          : await executeVoiceCommand(episodeId, text);
      setPreview(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "指令处理失败，请稍后重试。");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="home-composer">
      <div className="home-composer__input-row">
        <Sparkles aria-hidden="true" size={18} />
        <textarea
          aria-label="制作指令"
          onChange={(event) => {
            setTranscript(event.target.value);
            setPreview(null);
          }}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void run("preview");
          }}
          placeholder="想制作什么内容？例如：根据已审核素材生成一条关于 AI 工作流的 60 秒视频"
          rows={3}
          value={transcript}
        />
      </div>

      <div className="home-composer__toolbar">
        <div className="home-composer__context">
          <button aria-label="添加素材" className="composer-icon-button" disabled type="button"><Paperclip size={16} /></button>
          <button aria-label="语音输入" className="composer-icon-button" disabled type="button"><Mic size={16} /></button>
          {episodes.length ? (
            <select aria-label="选择 Episode 上下文" onChange={(event) => setEpisodeId(event.target.value)} value={episodeId}>
              {episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title || "未命名 Episode"}</option>)}
            </select>
          ) : (
            <Link className="composer-context-link" href="/production?new=1">先创建生产批次</Link>
          )}
        </div>
        <button
          aria-label="预览指令"
          className="composer-submit"
          disabled={!transcript.trim() || !episodeId || busy !== null}
          onClick={() => void run("preview")}
          type="button"
        >
          {busy === "preview" ? <LoaderCircle className="is-spinning" size={17} /> : <ArrowUp size={17} />}
        </button>
      </div>

      {error ? <p className="home-composer__error" role="alert">{error}</p> : null}
      {preview ? (
        <div className="home-command-preview" aria-live="polite">
          <div>
            <span className={`command-preview-status command-preview-status--${preview.status}`}>
              {preview.can_execute ? <Check size={13} /> : null}
              {preview.status === "unknown" ? "需要更具体的指令" : preview.summary}
            </span>
            {preview.operations.length ? (
              <ol>{preview.operations.map((operation, index) => <li key={`${operation.kind}-${index}`}>{operation.label}</li>)}</ol>
            ) : null}
          </div>
          {preview.can_execute && preview.status !== "executed" ? (
            <button disabled={busy !== null} onClick={() => void run("execute")} type="button">
              {busy === "execute" ? "正在执行…" : "确认执行"}
            </button>
          ) : preview.next_url ? <Link href={preview.next_url}>打开相关页面</Link> : null}
        </div>
      ) : null}
    </div>
  );
}
