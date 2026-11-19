"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import { createVoicePreview, listMediaPreviewAttempts, mediaProfilesErrorMessage, type MediaPreview } from "@/lib/media-profiles-api";
import { WorkspaceAudio } from "../workbench/WorkspaceMedia";
import ui from "../CreationRequestPanel.module.css";

type Props = {
  language: "zh" | "en";
  profileId: string;
  version: string;
};

const statusLabels = {
  queued: "已排队，等待设备", running: "正在生成样句", succeeded: "样句已生成",
  failed: "样句生成失败", blocked: "样句生成受阻", unavailable: "当前设备无法生成样句", cancelled: "试听已取消",
};

export function DeliveryVoiceSample({ language, profileId, version }: Props) {
  const name = language === "zh" ? "中文" : "英文";
  const [text, setText] = useState(language === "zh" ? "欢迎来到包装车间。" : "Welcome to the packaging floor.");
  const [run, setRun] = useState<MediaPreview | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const inProgress = run?.status === "queued" || run?.status === "running";

  useEffect(() => {
    if (!run || !inProgress || error) return;
    let current = true;
    const timer = window.setTimeout(async () => {
      try {
        const result = await listMediaPreviewAttempts("voice", run.identity);
        const attempt = result.items.find(item => item.id === run.id);
        if (current && attempt) setRun(attempt);
        if (current && !attempt) setError("本次试听记录暂不可读。");
      } catch (cause) {
        if (current) setError(mediaProfilesErrorMessage(cause));
      }
    }, 1500);
    return () => { current = false; window.clearTimeout(timer); };
  }, [run, inProgress, error]);

  async function generate() {
    setSending(true);
    setError("");
    try {
      const retry = !!run && run.preview_text === text.trim() && run.status !== "succeeded" && !inProgress;
      setRun(await createVoicePreview({voice_profile_id: profileId, voice_version: version, language, preview_text: text.trim(), retry}));
    } catch (cause) {
      setError(mediaProfilesErrorMessage(cause));
    } finally {
      setSending(false);
    }
  }
  return <div className={ui.section} role="group" aria-label={name + "样句试听"}>
    <label>{name}试听文字
      <input value={text} maxLength={4000} disabled={sending || inProgress} onChange={event => setText(event.target.value)} />
    </label>
    <div className={ui.actions}>
      <button type="button" className={ui.button} disabled={sending || inProgress || !text.trim()} onClick={() => void generate()}>生成{name}试听</button>
      {error && inProgress ? <button type="button" className={ui.button} onClick={() => setError("")}>重新读取试听状态</button> : null}
    </div>
    {error ? <p role="alert">{error}</p> : null}
    {run ? <>
      <p role="status">{statusLabels[run.status]}{run.impact ? " · " + run.impact : ""} {run.repair_action}</p>
      {run.status === "succeeded" && run.output_url ? <>
        <p>{run.preview_text}</p>
        <WorkspaceAudio controls preload="metadata" aria-label={name + "样句录音"} src={apiUrl(run.output_url)} />
      </> : null}
    </> : null}
  </div>;
}
