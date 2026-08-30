"use client";

import { Play, Sparkles } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { createMontagePlan, runProductionBatch, selectEpisodeVoice } from "@/lib/api";
import { type VoiceProfile, listVoiceProfiles } from "@/lib/media-profiles-api";

type EpisodeOption = { id: string; title: string };

type ProductionTaskControlsProps = {
  batchId: string;
  canRun: boolean;
  episodes: EpisodeOption[];
  runLabel: string;
  assignLocation?: (url: string) => void;
};

function assignDocumentLocation(url: string) {
  window.location.assign(url);
}

function errorMessage(cause: unknown) {
  return cause instanceof Error && cause.message ? cause.message : "本地服务未返回可用结果，请检查运行情况后重试。";
}

function voiceKey(voice: VoiceProfile) {
  return JSON.stringify([voice.id, voice.version]);
}

export function ProductionTaskControls({
  batchId,
  canRun,
  episodes,
  runLabel,
  assignLocation = assignDocumentLocation,
}: ProductionTaskControlsProps) {
  const [busy, setBusy] = useState<"run" | "plan" | "">("");
  const [error, setError] = useState("");
  const [voices, setVoices] = useState<VoiceProfile[]>([]);
  const [voiceKeyValue, setVoiceKeyValue] = useState("");
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const runLock = useRef(false);
  const planLock = useRef(false);
  const voicesRequested = useRef(false);

  async function loadVoices() {
    if (voicesRequested.current) return;
    voicesRequested.current = true;
    setVoicesLoading(true);
    setVoiceError("");
    try {
      const response = await listVoiceProfiles();
      setVoices(response.items);
    } catch (cause) {
      voicesRequested.current = false;
      setVoiceError(`无法读取声音档案：${errorMessage(cause)}`);
    } finally {
      setVoicesLoading(false);
    }
  }

  async function startTask() {
    if (runLock.current) return;
    runLock.current = true;
    setBusy("run");
    setError("");
    try {
      const batch = await runProductionBatch(batchId);
      const params = new URLSearchParams({
        batch: batch.id,
        toast: "success",
        toast_message: "已开始制作，任务会在完成当前步骤后更新。",
      });
      assignLocation(`/production?${params.toString()}`);
    } catch (cause) {
      setError(`开始制作失败：${errorMessage(cause)}`);
      runLock.current = false;
      setBusy("");
    }
  }

  async function createPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (planLock.current) return;

    const episodeId = String(new FormData(event.currentTarget).get("episode_id") ?? "");
    if (!episodeId) {
      setError("请选择已确认文案后再生成视频剪辑。");
      return;
    }
    const selectedVoice = voices.find((voice) => voiceKey(voice) === voiceKeyValue) ?? null;
    if (voiceKeyValue && !selectedVoice) {
      setError("所选声音版本已不可用，请重新选择后再生成视频剪辑。");
      return;
    }

    planLock.current = true;
    setBusy("plan");
    setError("");
    let selectionSaved = false;
    try {
      await selectEpisodeVoice(episodeId, {
        voice_profile_id: selectedVoice?.id ?? null,
        voice_version: selectedVoice?.version ?? null,
      });
      selectionSaved = true;
      const plan = await createMontagePlan(batchId, { episode_id: episodeId });
      const params = new URLSearchParams({
        toast: "success",
        toast_message: "视频剪辑已创建，请确认镜头后生成成片。",
      });
      assignLocation(`/production/${plan.batch_id}/episodes/${plan.episode_id}/montage?${params.toString()}`);
    } catch (cause) {
      setError(selectionSaved
        ? `声音选择已保存，但生成视频剪辑失败：${errorMessage(cause)}`
        : `生成视频剪辑失败：${errorMessage(cause)}`);
      planLock.current = false;
      setBusy("");
    }
  }

  return (
    <>
      {error ? <p className="queue-action-error" role="alert">{error}</p> : null}
      <div className="queue-row__actions">
        {canRun ? <button className="is-primary" disabled={Boolean(busy)} onClick={() => void startTask()} type="button"><Play size={14} />{busy === "run" ? "正在开始…" : runLabel}</button> : null}
      </div>
      {episodes.length ? (
        <details className="queue-plan-create" onToggle={(event) => { if (event.currentTarget.open) void loadVoices(); }}>
          <summary><Sparkles size={13} />为已确认文案生成视频剪辑</summary>
          <form onSubmit={(event) => void createPlan(event)}>
            <label><span>选择已确认文案</span><select defaultValue="" disabled={Boolean(busy)} name="episode_id" required><option disabled value="">请选择文案</option>{episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}</select></label>
            <label><span>本次配音</span><select disabled={Boolean(busy) || voicesLoading || Boolean(voiceError)} onChange={(event) => setVoiceKeyValue(event.target.value)} value={voiceKeyValue}><option value="">使用默认声音</option>{voices.map((voice) => <option key={voiceKey(voice)} value={voiceKey(voice)}>声音档案 · 版本 {voice.version}</option>)}</select></label>
            {voicesLoading ? <p className="queue-plan-create__hint">正在读取声音档案…</p> : null}
            {voiceError ? <p className="queue-action-error" role="alert">{voiceError}<button onClick={() => void loadVoices()} type="button">重试</button></p> : null}
            <p className="queue-plan-create__hint">旁白将由系统按已选声音档案和授权内容绑定。</p>
            <button disabled={Boolean(busy) || voicesLoading || Boolean(voiceError)} type="submit"><Sparkles size={14} />{busy === "plan" ? "正在创建…" : "生成视频剪辑"}</button>
          </form>
        </details>
      ) : null}
    </>
  );
}
