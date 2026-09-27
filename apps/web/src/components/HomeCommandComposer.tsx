"use client";

import { AlertCircle, ArrowUp, Sparkles } from "lucide-react";
import Image from "next/image";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { apiUrl, confirmCreationPlan, confirmCreationTaskEdit, getRuntimeSettings, previewCreationPlan, sendCreationTaskAssistantTurn, sendWorkspaceAssistantPlanTurn, undoCreationTaskTimeline } from "@/lib/api";
import type { CreationPlanPreview, CreationTask, CreationTaskEditPreviewResponse } from "@/lib/types";
import { PreflightFacts } from "@/components/CreateProductionTaskForm";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { PresenterShareSlider, type PresenterShare } from "@/components/PresenterShareSlider";
import { LipSyncQualitySelect, type LipSyncQuality } from "@/components/LipSyncQualitySelect";

type HomeCommandComposerProps = { assignLocation?: (url: string) => void; autoFocus?: boolean; task?: CreationTask | null; onTaskUpdated?: () => void };

const issueNames: Record<string, string> = {
  content_sources_missing: "缺少资料",
  template_voice_unavailable: "模板声音缺失",
  voice_language_unavailable: "声音不匹配",
  voice_authorization_missing: "缺少声音",
  voice_missing: "缺少声音",
  voice_model_unavailable: "配音未就绪",
  voice_reference_wav_unavailable: "缺少样本",
  approved_material_clips_missing: "缺少镜头",
  template_material_matches_missing: "镜头不匹配",
  person_render_unavailable: "口型未就绪",
};

function assignDocumentLocation(url: string) {
  window.location.assign(url);
}

function displayEditSummary(summary: string) {
  return summary.replace(/zh-CN字幕轨/g, "中文字幕").replace(/en-US字幕轨/g, "英文字幕");
}

function displayInvalidatedRefs(refs: string[]) {
  return [...new Set(refs.map((ref) => ref.endsWith(".srt") ? "字幕" : ref.endsWith(".mp4") ? "成片" : ref.endsWith(".png") || ref.endsWith(".jpg") ? "封面" : ref.endsWith(".wav") || ref.endsWith(".mp3") ? "声音" : "关联内容"))].join("、");
}

function voiceIsReady(preview: CreationPlanPreview) {
  return Boolean(preview.selected_voice) && !preview.blockers.some((item) => item.stage === "narration");
}

function EditMedia({ item, label }: { item: CreationTaskEditPreviewResponse["proposal"]["preview"]["after"]; label: string }) {
  return <>
    {item.image_url ? <Image alt={label} className="home-edit-preview__image" height={240} src={apiUrl(item.image_url)} unoptimized width={360} /> : null}
    {item.video_url ? <video aria-label={label} className="home-edit-preview__video" controls playsInline preload="metadata" src={apiUrl(item.video_url)} /> : null}
    {item.audio_url ? <audio aria-label={label} controls preload="metadata" src={apiUrl(item.audio_url)} /> : null}
  </>;
}

export function HomeCommandComposer({ assignLocation = assignDocumentLocation, autoFocus = false, task = null, onTaskUpdated }: HomeCommandComposerProps) {
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<{ command: string; reply: string }[]>([]);
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [shareValue, setShareValue] = useState<PresenterShare>(20);
  const [qualityValue, setQualityValue] = useState<LipSyncQuality>("high");
  const [shareUpdating, setShareUpdating] = useState(false);
  const shareRevision = useRef(0);
  const [taskPreview, setTaskPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [taskPreviewOpen, setTaskPreviewOpen] = useState(false);
  const [undoReady, setUndoReady] = useState(false);
  const [model, setModel] = useState("");
  const [modelConfigured, setModelConfigured] = useState(false);
  const [pending, setPending] = useState<"chat" | "confirm" | "undo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const conversationId = useRef<string | null>(null);
  const scopeRevision = useRef(0);
  const commandInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (autoFocus) commandInput.current?.focus(); }, [autoFocus]);
  useEffect(() => {
    scopeRevision.current += 1;
    setMessage(""); setTurns([]); setPreview(null); setPreviewOpen(false);
    shareRevision.current += 1; setShareValue(20); setQualityValue("high"); setShareUpdating(false);
    setTaskPreview(null); setTaskPreviewOpen(false); setUndoReady(false);
    setPending(null); setError(null);
    conversationId.current = null;
  }, [task?.id]);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setModel(`${runtime.status.llm.provider} / ${runtime.status.llm.model}`);
        setModelConfigured(true);
      }
    }).catch(() => undefined);
  }, []);

  async function sendToAssistant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const command = message.trim();
    if (!command || pending) return;
    const revision = scopeRevision.current;
    setPending("chat");
    setError(null);
    setPreview(null);
    setPreviewOpen(false);
    setTaskPreview(null);
    setTaskPreviewOpen(false);
    try {
      if (task) {
        const result = await sendCreationTaskAssistantTurn(task.id, {
          message: command, context_stage: task.stage,
          selected_object_refs: [], playhead_ms: null,
        });
        if (revision !== scopeRevision.current) return;
        setTurns((current) => [...current, { command, reply: result.reply }].slice(-8));
        setTaskPreview(result.edit_preview);
        setTaskPreviewOpen(Boolean(result.edit_preview));
        setModel(`${result.provider} / ${result.model}`);
      } else {
        conversationId.current ??= crypto.randomUUID();
        const result = await sendWorkspaceAssistantPlanTurn(command, conversationId.current);
        if (revision !== scopeRevision.current) return;
        setTurns((current) => [...current, { command, reply: result.reply }].slice(-8));
        setPreview(result.plan_preview);
        setShareValue(result.plan_preview?.presenter_share_target ?? 20);
        setQualityValue(result.plan_preview?.lipsync_quality ?? "high");
        setPreviewOpen(Boolean(result.plan_preview));
        setModel(`${result.provider} / ${result.model}`);
      }
      setMessage("");
    } catch (cause) {
      if (revision === scopeRevision.current) setError(cause instanceof Error ? cause.message : "对话没有完成，请检查模型设置。");
    } finally {
      if (revision === scopeRevision.current) setPending(null);
    }
  }

  async function confirmTaskPreview() {
    if (!task || !taskPreview || pending) return;
    const revision = scopeRevision.current;
    setPending("confirm");
    setError(null);
    try {
      await confirmCreationTaskEdit(task.id, taskPreview.proposal.id);
      if (revision !== scopeRevision.current) { onTaskUpdated?.(); return; }
      setTaskPreview(null);
      setTaskPreviewOpen(false);
      setUndoReady(true);
      setTurns((current) => [...current, { command: "确认修改", reply: "修改已完成。" }].slice(-8));
      onTaskUpdated?.();
    } catch (cause) {
      if (revision === scopeRevision.current) {
        setError(cause instanceof Error ? cause.message : "修改未完成，请重新预览。");
        setTaskPreview(null);
        setTaskPreviewOpen(false);
      }
    } finally {
      if (revision === scopeRevision.current) setPending(null);
    }
  }

  async function undoTaskEdit() {
    if (!task || !undoReady || pending) return;
    const revision = scopeRevision.current;
    setPending("undo");
    setError(null);
    try {
      await undoCreationTaskTimeline(task.id);
      if (revision !== scopeRevision.current) { onTaskUpdated?.(); return; }
      setUndoReady(false);
      setTurns((current) => [...current, { command: "撤销", reply: "已撤销刚才的修改。" }].slice(-8));
      onTaskUpdated?.();
    } catch (cause) {
      if (revision === scopeRevision.current) setError(cause instanceof Error ? cause.message : "撤销未完成，请到任务页查看。");
    } finally {
      if (revision === scopeRevision.current) setPending(null);
    }
  }

  async function confirmPlan() {
    if (!preview?.confirmable || pending || shareUpdating || shareValue !== (preview.presenter_share_target ?? 20) || qualityValue !== (preview.lipsync_quality ?? "high")) return;
    const revision = scopeRevision.current;
    setPending("confirm");
    setError(null);
    try {
      const plan = await confirmCreationPlan({
        goal: preview.goal, name: preview.name, target_count: preview.target_count,
        language: preview.language, aspect_ratio: preview.aspect_ratio,
        duration_target_seconds: preview.duration_target_seconds,
        presenter_share_target: preview.presenter_share_target ?? 20,
        lipsync_quality: preview.lipsync_quality ?? "high",
        ...(preview.template_id ? { template_id: preview.template_id, template_version: preview.template_version } : {}),
        preflight_fingerprint: preview.preflight_fingerprint,
      });
      if (revision !== scopeRevision.current) { onTaskUpdated?.(); return; }
      assignLocation(`/tasks/${encodeURIComponent(plan.creation_task.id)}`);
    } catch (cause) {
      if (revision === scopeRevision.current) {
        setError(cause instanceof Error ? cause.message : "创建任务没有完成，请重新预检。");
        setPreview(null);
        setPreviewOpen(false);
      }
    } finally {
      if (revision === scopeRevision.current) setPending(null);
    }
  }

  async function changePlanOptions(value: PresenterShare, quality: LipSyncQuality) {
    if (!preview || pending) return;
    setShareValue(value);
    setQualityValue(quality);
    setShareUpdating(true);
    setError(null);
    const revision = ++shareRevision.current;
    try {
      const updated = await previewCreationPlan({
        goal: preview.goal, name: preview.name, target_count: preview.target_count,
        language: preview.language, aspect_ratio: preview.aspect_ratio,
        duration_target_seconds: preview.duration_target_seconds,
        presenter_share_target: value,
        lipsync_quality: quality,
        ...(preview.template_id ? { template_id: preview.template_id, template_version: preview.template_version } : {}),
      });
      if (revision === shareRevision.current) setPreview(updated);
    } catch (cause) {
      if (revision === shareRevision.current) {
        setError(cause instanceof Error ? cause.message : "制作方案未能检查，请重新查看方案。");
        setPreview(null);
        setPreviewOpen(false);
      }
    } finally {
      if (revision === shareRevision.current) setShareUpdating(false);
    }
  }

  return (
    <div className="home-composer">
      {!turns.length ? <div className="home-chat-start"><h2>{task ? "这条怎么改？" : "今天想做什么？"}</h2><p>{task ? "说说不满意的地方，先看修改，再决定。" : "说说要介绍什么，剩下的我们一起完成。"}</p>{(task ? ["让节奏快一点", "把字幕放大一点", "露脸占比调到四成"] : ["用我的素材，做一条产品介绍", "把客户常问的问题，做成讲解视频", "把今天拍的片段，剪成工作日常"]).map(text => <button type="button" key={text} onClick={() => { setMessage(text); commandInput.current?.focus(); }}>{text}</button>)}</div> : null}
      {turns.length ? <div className="home-composer__conversation" aria-label="对话记录">
        {turns.map((turn, index) => <div className="home-composer__turn" key={index}>
          <p className="home-composer__command">{turn.command}</p>
          <p className="home-composer__reply" aria-live={index === turns.length - 1 ? "polite" : undefined}>{turn.reply}</p>
        </div>)}
      </div> : null}
      {preview ? <button className="home-composer__open-card" onClick={() => setPreviewOpen(true)} type="button">查看方案</button> : null}
      {preview && previewOpen ? <HomeCardDialog onClose={() => setPreviewOpen(false)} title="制作方案"><section className="home-composer__result" aria-label="制作方案">
        <div className="home-composer__result-head"><strong>{preview.name.replace(/^创作任务[:：]\s*/, "")}</strong><span>{preview.aspect_ratio} · {preview.duration_target_seconds} 秒</span></div>
        <div className="home-composer__result-facts"><span>镜头 {preview.approved_clip_count}</span><span>{voiceIsReady(preview) ? "声音已就绪" : "声音待处理"}</span><span>{preview.language === "en-US" ? "英文" : preview.language === "zh-CN/en-US" ? "中英双语" : "中文"}</span></div>
        <PresenterShareSlider disabled={pending !== null} onChange={(value) => void changePlanOptions(value, qualityValue)} value={shareValue} />
        <LipSyncQualitySelect disabled={pending !== null || shareValue === 0} onChange={(value) => void changePlanOptions(shareValue, value)} value={qualityValue} />
        {preview.blockers.length ? <details className="home-composer__issues" open>
          <summary><AlertCircle size={15} />{preview.confirmable ? "可先建任务" : "先处理"}<span>{preview.blockers.map((item) => issueNames[item.reason] ?? "制作受限").join(" · ")}</span></summary>
          <div>{preview.blockers.map((item, index) => <article key={`${item.reason}-${index}`}>
            <strong>{issueNames[item.reason] ?? "制作受限"}</strong><p>{item.impact}</p><p>{item.repair_action}</p>
          </article>)}</div>
        </details> : null}
        <div className="home-composer__result-actions"><details className="home-composer__facts"><summary>查看依据</summary><PreflightFacts preview={preview} /></details>
          <button className="composer-submit" disabled={!preview.confirmable || pending !== null || shareUpdating || shareValue !== (preview.presenter_share_target ?? 20) || qualityValue !== (preview.lipsync_quality ?? "high")} onClick={() => void confirmPlan()} type="button">{pending === "confirm" ? "创建中" : shareUpdating ? "检查中" : "创建任务"}</button></div>
      </section></HomeCardDialog> : null}
      {taskPreview ? <button className="home-composer__open-card" onClick={() => setTaskPreviewOpen(true)} type="button">查看修改</button> : null}
      {taskPreview && taskPreviewOpen ? <HomeCardDialog onClose={() => setTaskPreviewOpen(false)} title="修改预览">
        <div className="home-edit-preview">
          <strong>{displayEditSummary(taskPreview.proposal.summary)}</strong>
          {taskPreview.proposal.preview.text_changes?.length ? <div className="home-edit-preview__text-changes">{taskPreview.proposal.preview.text_changes.map((change) => <div key={change.cue_id}><section><span>修改前</span><p>{change.before}</p></section><section><span>修改后</span><p>{change.after}</p></section></div>)}</div> : <div className="home-edit-preview__comparison">
            <section><span>修改前</span><strong>{taskPreview.proposal.preview.before.label}</strong><p>{taskPreview.proposal.preview.before.detail}</p><EditMedia item={taskPreview.proposal.preview.before} label="修改前效果" /></section>
            <section><span>修改后</span><strong>{taskPreview.proposal.preview.after.label}</strong><p>{taskPreview.proposal.preview.after.detail}</p><EditMedia item={taskPreview.proposal.preview.after} label="修改后效果" /></section>
          </div>}
          {taskPreview.proposal.invalidated_refs.length ? <p>需重做：{displayInvalidatedRefs(taskPreview.proposal.invalidated_refs)}</p> : null}
          {taskPreview.proposal.external_effects.length ? <details className="home-edit-preview__effects"><summary>其他影响</summary><p>{taskPreview.proposal.external_effects.join("；")}</p></details> : null}
          <div className="home-edit-preview__actions"><button onClick={() => setTaskPreviewOpen(false)} type="button">先不改</button><button className="composer-submit" disabled={pending !== null} onClick={() => void confirmTaskPreview()} type="button">{pending === "confirm" ? "修改中" : "确认修改"}</button></div>
        </div>
      </HomeCardDialog> : null}
      <form onSubmit={(event) => void sendToAssistant(event)}>
        <div className="home-composer__input-row">
          <Sparkles aria-hidden="true" size={18} />
          <textarea aria-label="制作指令" disabled={pending !== null} ref={commandInput}
            onChange={(event) => { setMessage(event.target.value); setPreview(null); setPreviewOpen(false); }}
            placeholder={task ? "说说怎么改" : "想做什么视频？"}
            rows={2} value={message} />
        </div>
        <div className="home-composer__toolbar">
          <div className="home-composer__meta">
            <span>{modelConfigured ? "仅传文字 · 可能收费" : "尚未连接"}</span>
            {modelConfigured ? <details><summary>服务详情</summary><p>当前服务：{model}。只发送这段话和相关文字，不发送原视频或声音。费用由服务方收取。</p></details> : <a href="/settings">去设置</a>}
          </div>
          <button aria-label="发送" className="composer-submit" disabled={!modelConfigured || !message.trim() || pending !== null} type="submit">
            <ArrowUp size={17} /><span>{pending === "chat" ? "发送中" : "发送"}</span>
          </button>
        </div>
      </form>
      {error ? <p className="home-composer__error" role="alert">{error}</p> : null}
      <div className="home-composer__footer">{task ? <><a href={`/tasks/${encodeURIComponent(task.id)}`}>打开任务</a>{undoReady ? <button disabled={pending !== null} onClick={() => void undoTaskEdit()} type="button">{pending === "undo" ? "撤销中" : "撤销修改"}</button> : null}</> : <a href="/production?new=1">手动创建</a>}</div>
    </div>
  );
}
