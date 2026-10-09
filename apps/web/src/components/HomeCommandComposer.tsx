"use client";

import { AlertCircle } from "lucide-react";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { apiUrl, confirmCreationPlan, confirmCreationTaskEdit, getRuntimeSettings, previewCreationPlan, sendCreationTaskAssistantTurn, sendMaterialAssistantTurn, sendWorkspaceAssistantPlanTurn, undoCreationTaskTimeline, updateAssistantPlanPreview } from "@/lib/api";
import type { CreationPlanPreview, CreationTask, CreationTaskEditPreviewResponse, MaterialAssistantResult, MaterialAssistantSelection } from "@/lib/types";
import { PreflightFacts } from "@/components/CreateProductionTaskForm";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { PresenterShareSlider, type PresenterShare } from "@/components/PresenterShareSlider";
import { LipSyncQualitySelect, type LipSyncQuality } from "@/components/LipSyncQualitySelect";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import { AssistantConversationPanel } from "@/components/AssistantConversationPanel";

type HomeCommandComposerProps = {
  assignLocation?: (url: string) => void;
  autoFocus?: boolean;
  task?: CreationTask | null;
  onTaskUpdated?: () => void;
  materialContext?: {
    selection: MaterialAssistantSelection | null;
    label: string | null;
    onResult: (selection: MaterialAssistantSelection, result: MaterialAssistantResult) => void;
  };
};

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

export function HomeCommandComposer({ assignLocation = assignDocumentLocation, autoFocus = false, task = null, onTaskUpdated, materialContext }: HomeCommandComposerProps) {
  const materialScopeKey = materialContext
    ? materialContext.selection
      ? materialContext.selection.scope === "asset"
        ? `asset:${materialContext.selection.asset_id}`
        : `segment:${materialContext.selection.asset_id}:${materialContext.selection.segment_id}`
      : "browse"
    : null;
  const scopeKind = materialScopeKey !== null ? "material" as const : task ? "task" as const : "home" as const;
  const scopeId = materialScopeKey ?? task?.id ?? "home";
  const assistant = useAssistantConversation({ scope_kind: scopeKind, scope_id: scopeId });
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [shareValue, setShareValue] = useState<PresenterShare>(20);
  const [qualityValue, setQualityValue] = useState<LipSyncQuality>("high");
  const [activePlanTurnId, setActivePlanTurnId] = useState<string | null>(null);
  const [shareUpdating, setShareUpdating] = useState(false);
  const shareRevision = useRef(0);
  const [taskPreview, setTaskPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [taskPreviewOpen, setTaskPreviewOpen] = useState(false);
  const [undoReady, setUndoReady] = useState(false);
  const [model, setModel] = useState("");
  const [modelConfigured, setModelConfigured] = useState(false);
  const [pending, setPending] = useState<"chat" | "confirm" | "undo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskActionStatus, setTaskActionStatus] = useState<string | null>(null);
  const scopeRevision = useRef(0);
  const restoredPlanKey = useRef<string | null>(null);
  useEffect(() => {
    scopeRevision.current += 1;
    setPreview(null); setPreviewOpen(false);
    setActivePlanTurnId(null);
    shareRevision.current += 1; setShareValue(20); setQualityValue("high"); setShareUpdating(false);
    setTaskPreview(null); setTaskPreviewOpen(false); setUndoReady(false);
    setPending(null); setError(null); setTaskActionStatus(null);
    restoredPlanKey.current = null;
  }, [task?.id, materialScopeKey]);
  useEffect(() => {
    const saved = assistant.conversation;
    if (!saved || saved.scope_kind !== scopeKind || saved.scope_id !== scopeId) return;
    const planTurn = [...saved.turns].reverse().find((turn) =>
      turn.result?.kind === "plan_preview"
      && typeof turn.result.plan_preview === "object"
      && turn.result.plan_preview !== null,
    );
    const key = `${saved.id}:${planTurn?.id ?? "none"}:${planTurn?.updated_at ?? ""}`;
    if (restoredPlanKey.current === key) return;
    restoredPlanKey.current = key;
    const restored = planTurn?.result?.plan_preview as CreationPlanPreview | undefined;
    setActivePlanTurnId(planTurn?.id ?? null);
    setPreview(restored ?? null);
    setPreviewOpen(Boolean(restored));
    setShareValue(restored?.presenter_share_target ?? 20);
    setQualityValue(restored?.lipsync_quality ?? "high");
  }, [assistant.conversation, scopeId, scopeKind]);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setModel(`${runtime.status.llm.provider} / ${runtime.status.llm.model}`);
        setModelConfigured(true);
      }
    }).catch(() => undefined);
  }, []);

  async function sendToAssistant(rawMessage: string) {
    const command = rawMessage.trim();
    if (!command || pending) return;
    if (materialContext && !materialContext.selection) {
      setError("请先从右侧选择整条素材或一个片段，再说明要怎么处理我的标签；这次没有发送。");
      return;
    }
    const revision = scopeRevision.current;
    setPending("chat");
    setError(null);
    setTaskActionStatus(null);
    setPreview(null);
    setPreviewOpen(false);
    setTaskPreview(null);
    setTaskPreviewOpen(false);
    let requestConversationId: string | null = null;
    let requestLocalTurnId: string | null = null;
    try {
      if (!assistant.conversation) await assistant.createNew();
      const turn = assistant.beginTurn(command);
      requestConversationId = turn.conversationId;
      requestLocalTurnId = turn.localTurnId;
      const onStreamEvent = assistant.bindTurnEvents(turn.conversationId, turn.localTurnId);
      await turn.draftCheckpoint;
      if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
      if (materialContext?.selection) {
        const selection = materialContext.selection;
        const result = await sendMaterialAssistantTurn(command, turn.conversationId, selection, (item) => {
          if (revision !== scopeRevision.current) return;
          onStreamEvent(item);
        });
        if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
        if (result.material_result) materialContext.onResult(selection, result.material_result);
        setModel(`${result.provider} / ${result.model}`);
      } else if (task) {
        const result = await sendCreationTaskAssistantTurn(task.id, {
          conversation_id: turn.conversationId,
          message: command, context_stage: task.stage,
          selected_object_refs: [], playhead_ms: null,
        }, (item) => {
          if (revision !== scopeRevision.current) return;
          onStreamEvent(item);
        });
        if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
        setTaskPreview(result.edit_preview);
        setTaskPreviewOpen(Boolean(result.edit_preview));
        setModel(`${result.provider} / ${result.model}`);
        if (result.execution_action || result.topic_candidates) onTaskUpdated?.();
      } else {
        const result = await sendWorkspaceAssistantPlanTurn(command, turn.conversationId, (item) => {
          if (revision !== scopeRevision.current) return;
          onStreamEvent(item);
        });
        if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
        setPreview(result.plan_preview);
        setActivePlanTurnId(result.turn_id ?? null);
        setShareValue(result.plan_preview?.presenter_share_target ?? 20);
        setQualityValue(result.plan_preview?.lipsync_quality ?? "high");
        setPreviewOpen(Boolean(result.plan_preview));
        setModel(`${result.provider} / ${result.model}`);
      }
      if (assistant.isCurrentConversation(turn.conversationId)) await assistant.refresh();
    } catch (cause) {
      if (requestConversationId && requestLocalTurnId) {
        await assistant.failUnpersistedTurn(requestConversationId, requestLocalTurnId,
          cause instanceof Error ? cause.message : "本轮没有建立持久轮次，请检查后可重试。");
      }
      if (revision === scopeRevision.current && (!requestConversationId || assistant.isCurrentConversation(requestConversationId))) {
        setError(cause instanceof Error ? cause.message : "对话没有完成，请检查模型设置。");
      }
    } finally {
      if (revision === scopeRevision.current && (!requestConversationId || assistant.isCurrentConversation(requestConversationId))) setPending(null);
    }
  }

  async function confirmTaskPreview() {
    if (!task || !taskPreview || pending) return;
    const revision = scopeRevision.current;
    setPending("confirm");
    setError(null);
    try {
      const result = await confirmCreationTaskEdit(task.id, taskPreview.proposal.id);
      if (revision !== scopeRevision.current) { onTaskUpdated?.(); return; }
      setTaskPreview(null);
      setTaskPreviewOpen(false);
      const queued = "status" in result;
      setUndoReady(!queued);
      setTaskActionStatus(queued ? `修改已排队，作业 ID：${result.job_id}。队列回执不代表制作完成，当前版本保持不变。` : null);
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
    const conversationId = assistant.conversation?.id;
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
      if (revision !== scopeRevision.current || (conversationId && !assistant.isCurrentConversation(conversationId))) return;
      assignLocation(`/tasks/${encodeURIComponent(plan.creation_task.id)}`);
    } catch (cause) {
      if (revision === scopeRevision.current && (!conversationId || assistant.isCurrentConversation(conversationId))) {
        setError(cause instanceof Error ? cause.message : "创建任务没有完成，请重新预检。");
        setPreview(null);
        setPreviewOpen(false);
      }
    } finally {
      if (revision === scopeRevision.current && (!conversationId || assistant.isCurrentConversation(conversationId))) setPending(null);
    }
  }

  async function changePlanOptions(value: PresenterShare, quality: LipSyncQuality) {
    if (!preview || pending) return;
    setShareValue(value);
    setQualityValue(quality);
    setShareUpdating(true);
    setError(null);
    const revision = ++shareRevision.current;
    const scopeAtStart = scopeRevision.current;
    const conversationId = assistant.conversation?.id;
    const planTurnId = activePlanTurnId;
    try {
      const input = {
        goal: preview.goal, name: preview.name, target_count: preview.target_count,
        language: preview.language, aspect_ratio: preview.aspect_ratio,
        duration_target_seconds: preview.duration_target_seconds,
        presenter_share_target: value,
        lipsync_quality: quality,
        ...(preview.template_id ? { template_id: preview.template_id, template_version: preview.template_version } : {}),
      };
      const updated = conversationId && planTurnId
        ? await updateAssistantPlanPreview(conversationId, planTurnId, input)
        : await previewCreationPlan(input);
      if (revision === shareRevision.current && scopeAtStart === scopeRevision.current
        && (!conversationId || assistant.isCurrentConversation(conversationId))) setPreview(updated);
    } catch (cause) {
      if (revision === shareRevision.current && scopeAtStart === scopeRevision.current
        && (!conversationId || assistant.isCurrentConversation(conversationId))) {
        setError(cause instanceof Error ? cause.message : "制作方案未能检查，请重新查看方案。");
        setPreview(null);
        setPreviewOpen(false);
      }
    } finally {
      if (revision === shareRevision.current && scopeAtStart === scopeRevision.current
        && (!conversationId || assistant.isCurrentConversation(conversationId))) setShareUpdating(false);
    }
  }

  return (
    <div className="home-composer">
      {materialContext ? <p className="home-composer__context" aria-label="当前素材对话范围">
        {materialContext.selection ? `当前范围：${materialContext.label ?? (materialContext.selection.scope === "asset" ? "整条素材" : "当前片段")}` : "尚未选择素材：请从右侧选择整条素材或一个片段。"}
      </p> : null}
      <AssistantConversationPanel
        autoFocus={autoFocus}
        busy={pending !== null}
        configured={modelConfigured}
        input={assistant.draftText}
        inputLabel={materialContext ? "素材标签要求" : task ? "任务对话要求" : "制作要求"}
        onInput={(value) => { assistant.setDraftText(value); setPreview(null); setPreviewOpen(false); }}
        onConversationChange={() => { scopeRevision.current += 1; shareRevision.current += 1; setPending(null); setShareUpdating(false); setError(null); }}
        onSend={sendToAssistant}
        scopeLabel={materialContext ? (materialContext.label ?? "当前素材") : task ? task.name : "首页创作"}
        state={assistant}
        suggestions={materialContext ? ["把我的标签改成产品演示", "再加上室内", "移除旧标签"] : task ? ["先解释目前这一步", "让节奏快一点", "字幕放大一点"] : ["用我的素材，做一条产品介绍", "把客户常问的问题做成讲解视频", "把今天拍的片段剪成工作日常"]}
      />
      {taskActionStatus ? <p role="status" aria-live="polite">{taskActionStatus}</p> : null}
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
      {modelConfigured ? <p className="home-composer__meta">当前服务：{model}。本轮只提交文字和当前页面明确选择的上下文，不上传原始素材。</p> : <a href="/settings">连接文字模型</a>}
      {error ? <p className="home-composer__error" role="alert">{error}</p> : null}
      {!materialContext ? <div className="home-composer__footer">{task ? <><a href={`/tasks/${encodeURIComponent(task.id)}`}>打开任务</a>{undoReady ? <button disabled={pending !== null} onClick={() => void undoTaskEdit()} type="button">{pending === "undo" ? "撤销中" : "撤销修改"}</button> : null}</> : <a href="/production?new=1">手动创建</a>}</div> : null}
    </div>
  );
}
