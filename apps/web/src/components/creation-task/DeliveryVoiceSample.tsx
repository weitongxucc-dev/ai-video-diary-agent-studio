"use client";

import { useEffect, useId, useRef, useState } from "react";
import { apiUrl } from "@/lib/api";
import { readLocalMemberToken } from "@/lib/local-member-auth";
import { createVoicePreview, listMediaPreviewAttempts, mediaProfilesErrorMessage, type MediaPreview, type SavedVoiceSection } from "@/lib/media-profiles-api";
import { WorkspaceAudio } from "../workbench/WorkspaceMedia";
import { useVoiceSamplePreparation } from "./useVoiceSamplePreparation";
import ui from "../CreationRequestPanel.module.css";

export type AuditionVoice = {
  language: "zh" | "en";
  profileId: string;
  version: string;
};

type Props = AuditionVoice & {
  savedSection?: { sourceKey: string; text: string; reference: SavedVoiceSection };
};

const statusLabels = {
  queued: "已排队，等待设备", running: "正在生成样句", succeeded: "样句已生成",
  failed: "样句生成失败", blocked: "样句生成受阻", unavailable: "当前设备无法生成样句", cancelled: "试听已取消",
};

const preparationLabels: Record<string, string> = {
  waiting: "已排队，等待本机", blocked: "暂时无法领取设备，仍在等待",
  granted: "正在合成样句并测量资源", yield_requested: "正在等待释放设备",
  done: "测量完成，正在核验启用", failed: "本次准备未完成", cancelled: "本次准备已取消",
};

function needsPreparation(message: string) {
  return /独立声音预览同类资源预算尚未标定|实际声音、来源、参考文案、语言或发音词典与已准备形状不一致|实际配音后端与模型准备回执不一致|声音参考超过本次已标定的单次输入范围|模型准备许可不能作为独立声音试听的执行档/.test(message);
}

function isPreparationRejection(run: MediaPreview, sentence: string) {
  return run.status === "failed" && run.reason === "device_submission_failed"
    && run.preview_text === sentence && needsPreparation(run.repair_action ?? "");
}

export function DeliveryVoiceSample(props: Props) {
  return <VoiceSample key={JSON.stringify([props.profileId, props.version, props.language, props.savedSection?.sourceKey])} {...props} />;
}

function VoiceSample({ language, profileId, version, savedSection }: Props) {
  const name = language === "zh" ? "中文" : "英文";
  const initialText = savedSection?.text ?? (language === "zh" ? "欢迎来到包装车间。" : "Welcome to the packaging floor.");
  const [text, setText] = useState(initialText);
  const [run, setRun] = useState<MediaPreview | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [recoveryText, setRecoveryText] = useState<string | null>(null);
  const previewInFlight = useRef(false);
  const uncertainPreview = useRef(false);
  const rejectedPreviewText = useRef<string | null>(null);
  const mounted = useRef(false);
  const scopeBefore = useRef<string | undefined>(undefined);
  const previewToken = useRef<string | null>(null);
  const hint = useId();
  const preparation = useVoiceSamplePreparation({ language, profileId, version, sourceKey: savedSection?.sourceKey });
  const { status, attempt, receipt } = preparation;
  const scope = status?.actor_scope;
  const visibleRun = previewToken.current === readLocalMemberToken() ? run : null;
  const inProgress = visibleRun?.status === "queued" || visibleRun?.status === "running";
  const pendingPreparation = Boolean(attempt && !attempt.settlement);
  const prepared = attempt?.settlement === "ready" && attempt.payload.shape.text === text.trim()
    && status?.available_operations.includes("narration") && recoveryText !== text.trim();
  const preparationNeeded = !prepared && (recoveryText === text.trim()
    || Boolean(status?.preparation_configured && status.profile_status === "available"
      && !status.available_operations.includes("narration")));
  const showPreparation = pendingPreparation || prepared || preparationNeeded
    || (attempt?.settlement === "failed" || attempt?.settlement === "rejected") && !visibleRun;
  const preparationAvailable = Boolean(status?.preparation_configured && status.profile_status === "available");

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (scopeBefore.current && scopeBefore.current !== scope) {
      setRun(null);
      setError("");
      setRecoveryText(null);
      setText(initialText);
      uncertainPreview.current = false;
      rejectedPreviewText.current = null;
    }
    scopeBefore.current = scope;
  }, [initialText, scope]);

  const savedText = attempt?.payload.shape.text;
  // Restore the original text when returning to a submitted or settled preparation.
  useEffect(() => {
    if (!savedSection && savedText !== undefined) setText(savedText);
  }, [attempt?.payload.request_id, savedSection, savedText]);

  useEffect(() => {
    if (attempt?.settlement === "ready") setRecoveryText(null);
  }, [attempt?.payload.request_id, attempt?.settlement]);

  useEffect(() => {
    if (!visibleRun || !inProgress || error) return;
    let current = true;
    const token = readLocalMemberToken();
    const timer = window.setTimeout(async () => {
      try {
        const result = await listMediaPreviewAttempts("voice", visibleRun.identity);
        const updated = result.items.find(item => item.id === visibleRun.id);
        if (!current || token !== readLocalMemberToken()) return;
        if (updated) setRun(updated);
        else setError("本次试听记录暂不可读。");
      } catch (cause) {
        if (current && token === readLocalMemberToken()) setError(mediaProfilesErrorMessage(cause));
      }
    }, 1500);
    return () => { current = false; window.clearTimeout(timer); };
  }, [visibleRun, inProgress, error]);

  async function generate() {
    if (previewInFlight.current) return;
    previewInFlight.current = true;
    setSending(true);
    setError("");
    const sentence = text.trim();
    const token = readLocalMemberToken();
    const stillCurrent = () => mounted.current && token === readLocalMemberToken() && scopeBefore.current === scope;
    try {
      if (prepared) await preparation.refresh(scope);
      if (!stillCurrent()) return;
      const retry = !uncertainPreview.current && (
        rejectedPreviewText.current === sentence && prepared
        || Boolean(visibleRun && visibleRun.preview_text === sentence && visibleRun.status !== "succeeded" && !inProgress));
      uncertainPreview.current = true;
      const next = await createVoicePreview({
        voice_profile_id: profileId, voice_version: version, language, preview_text: sentence, retry,
        ...(savedSection && { saved_section: savedSection.reference }),
      });
      if (!stillCurrent()) return;
      uncertainPreview.current = false;
      previewToken.current = token;
      setRun(next);
      const rejected = needsPreparation(next.repair_action ?? "") ? sentence : null;
      // With retry:false the API may return the stored pre-calibration failure.
      const replayedRejection = prepared && !retry && isPreparationRejection(next, sentence);
      rejectedPreviewText.current = rejected;
      setRecoveryText(replayedRejection ? null : rejected);
    } catch (cause) {
      if (!stillCurrent()) return;
      const message = mediaProfilesErrorMessage(cause);
      if (needsPreparation(message)) {
        // The backend rejected admission before creating a device preview job.
        uncertainPreview.current = false;
        rejectedPreviewText.current = sentence;
        setRecoveryText(sentence);
      }
      setError(message);
    } finally {
      previewInFlight.current = false;
      if (mounted.current) setSending(false);
    }
  }

  function prepare(newAttempt = false) {
    setError("");
    if (visibleRun && visibleRun.status !== "succeeded") setRun(null);
    void preparation.submit(text.trim(), newAttempt);
  }

  const preparationProgress = prepared ? "本机试听准备已启用，可以生成试听。"
    : attempt?.settlement === "failed" ? "本次准备未完成，原请求已收尾。"
    : pendingPreparation && receipt ? preparationLabels[receipt.state] ?? "正在核对原准备请求"
    : pendingPreparation ? "正在核对原准备请求" : "";
  const failure = receipt?.failure_reason || receipt?.repair_action;
  const resumable = pendingPreparation && !attempt?.workId;
  const previewRepair = prepared && visibleRun && isPreparationRejection(visibleRun, text.trim())
    ? "本机试听准备已启用，可再次生成试听。"
    : visibleRun?.repair_action;

  return <div className={ui.section} role="group" aria-label={name + "样句试听"}>
    <label>{name}试听文字
      <input lang={language} value={text} maxLength={4000} readOnly={Boolean(savedSection)}
        disabled={sending || inProgress || pendingPreparation}
        onChange={event => { setText(event.target.value); setError(""); }} />
    </label>
    <div className={ui.actions}>
      {showPreparation && !prepared && !pendingPreparation && preparationAvailable ? (
        <button type="button" className={ui.button} aria-describedby={hint}
          disabled={preparation.busy || sending || inProgress || !text.trim()}
          onClick={() => prepare(Boolean(attempt?.settlement))}>
          {attempt?.settlement === "failed" || attempt?.settlement === "rejected" ? "重新准备本机试听" : "准备本机试听"}
        </button>
      ) : (
        <button type="button" className={ui.button} disabled={sending || inProgress || pendingPreparation || preparation.busy || !text.trim()}
          onClick={() => void generate()}>生成{name}试听</button>
      )}
      {showPreparation && (preparation.error || resumable) ? (
        <button type="button" className={ui.button} disabled={preparation.busy}
          onClick={() => resumable ? prepare() : void preparation.read()}>
          核对原准备状态
        </button>
      ) : null}
      {error && inProgress ? <button type="button" className={ui.button} onClick={() => setError("")}>重新读取试听状态</button> : null}
    </div>
    {showPreparation && !prepared && !pendingPreparation && preparationAvailable ? (
      <p id={hint} className={ui.note}>领取设备后会实际合成当前样句来测量资源。启用后可在这里生成试听。</p>
    ) : null}
    {preparationProgress ? <p role="status">{preparationProgress}</p> : null}
    {attempt?.settlement === "failed" && failure ? <p role="alert" className={ui.error}>{failure}</p> : null}
    {showPreparation && !preparationAvailable && !pendingPreparation && !prepared ? (
      <p className={ui.note}>请从桌面设置确认本机资源档后，再准备这句试听。</p>
    ) : null}
    {preparation.identityChanged ? <p role="status">账号、工作区或设备已切换。原准备请求仍保留。</p> : null}
    {showPreparation && preparation.error ? <p role="alert" className={ui.error}>{preparation.error}</p> : null}
    {error ? <p role="alert" className={ui.error}>{error}</p> : null}
    {visibleRun ? <>
      <p role="status">{statusLabels[visibleRun.status]}{visibleRun.impact ? " · " + visibleRun.impact : ""} {previewRepair}</p>
      {visibleRun.status === "succeeded" && visibleRun.output_url ? <>
        <p lang={language}>{visibleRun.preview_text}</p>
        <WorkspaceAudio controls preload="metadata" aria-label={name + "样句录音"} src={apiUrl(visibleRun.output_url)} />
      </> : null}
    </> : null}
  </div>;
}
