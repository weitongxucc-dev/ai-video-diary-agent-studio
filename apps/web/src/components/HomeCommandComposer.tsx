"use client";

import { AlertCircle } from "lucide-react";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { apiUrl, confirmCreationPlan, confirmCreationTaskEdit, getRuntimeSettings, previewCreationPlan, sendCreationTaskAssistantTurn, sendMaterialAssistantTurn, sendWorkspaceAssistantPlanTurn, undoCreationTaskTimeline, updateAssistantPlanPreview } from "@/lib/api";
import type { AssistantConversationTurn, CreationPlanPreview, CreationTask, CreationTaskEditPreviewResponse, MaterialAssistantResult, MaterialAssistantSelection, WorkspaceAssistantPlanTurnResponse } from "@/lib/types";
import { PreflightFacts } from "@/components/CreateProductionTaskForm";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { CreationRequestPanel } from "@/components/CreationRequestPanel";
import { PresenterShareSlider, type PresenterShare } from "@/components/PresenterShareSlider";
import { LipSyncQualitySelect, type LipSyncQuality } from "@/components/LipSyncQualitySelect";
import { useAssistantSelection, useAssistantKnowledgeReference, useAssistantMaterialReference, useAssistantProfileReference } from "./AssistantConversationProvider";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import { AssistantConversationPanel, AssistantFailureMessage } from "@/components/AssistantConversationPanel";

export type HomeCommandComposerProps = {
  assignLocation?: (url: string) => void;
  autoFocus?: boolean;
  task?: CreationTask | null;
  onTaskUpdated?: () => void;
  pageContext?: { pathname: string };
  taskContext?: {
    stage: string;
    selectedObjectRefs: string[];
    getPlayheadMs: () => number | null;
    onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
    transferDraftState?: "none" | "draft" | "confirmed";
    onOpenEditProposal?: (turn: AssistantConversationTurn) => void;
  };
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

export function HomeCommandComposer({ assignLocation = assignDocumentLocation, autoFocus = false, task = null, onTaskUpdated, materialContext, pageContext, taskContext }: HomeCommandComposerProps) {
  const selectionState = useAssistantSelection();
  const selectionSnapshot = JSON.stringify([selectionState?.version, selectionState?.items]);
  const latestSelectionSnapshot = useRef(selectionSnapshot);
  latestSelectionSnapshot.current = selectionSnapshot;
  const materialReference = useAssistantMaterialReference();
  const profileReference = useAssistantProfileReference()?.reference;
  const knowledgeReference = useAssistantKnowledgeReference()?.reference;
  const effectiveMaterialContext = materialReference?.reference ? {
    selection: materialReference.reference.selection, label: materialReference.reference.label,
    onResult: materialContext?.onResult ?? (() => undefined),
  } : materialReference && materialContext ? { ...materialContext, selection: null, label: null } : materialContext;
  materialContext = effectiveMaterialContext;
  const materialSelectionKey = materialContext
    ? materialContext.selection
      ? materialContext.selection.scope === "asset"
        ? `asset:${materialContext.selection.asset_id}`
        : `segment:${materialContext.selection.asset_id}:${materialContext.selection.segment_id}`
      : "browse"
    : null;
  const scopeKind = task ? "task" as const : "home" as const;
  const scopeId = task?.id ?? "home";
  const assistant = useAssistantConversation({ scope_kind: "home", scope_id: "home" });
  const [preview, setPreview] = useState<CreationPlanPreview | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [requestBinding, setRequestBinding] = useState<{ id: string; conversationId: string; revision: number } | null>(null);
  const [requestOpen, setRequestOpen] = useState(false);
  const [requestGoalsDraft, setRequestGoalsDraft] = useState<{ key: string; text: string } | null>(null);
  const [shareValue, setShareValue] = useState<PresenterShare>(20);
  const [qualityValue, setQualityValue] = useState<LipSyncQuality>("high");
  const [activePlanTurnId, setActivePlanTurnId] = useState<string | null>(null);
  const [shareUpdating, setShareUpdating] = useState(false);
  const shareRevision = useRef(0);
  const [taskPreview, setTaskPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [taskPreviewOpen, setTaskPreviewOpen] = useState(false);
  const latestTaskFacts = useRef(task);
  latestTaskFacts.current = task;
  // A command return is not a task read; retain a read barrier until new immutable facts arrive.
  const [taskAwaitingRead, setTaskAwaitingRead] = useState<CreationTask | null>(null);
  const awaitingTaskRead = Boolean(task && taskAwaitingRead === task);
  const undoAvailable = task?.can_undo === true && !awaitingTaskRead;
  const [modelConfigured, setModelConfigured] = useState(false);
  const [pending, setPending] = useState<"chat" | "confirm" | "undo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [taskActionStatus, setTaskActionStatus] = useState<string | null>(null);
  const scopeRevision = useRef(0);
  const restoredPlanKey = useRef<string | null>(null);
  const replyPlanRequests = useRef(new Map<string, string>());
  useEffect(() => {
    scopeRevision.current += 1;
    setPreview(null); setPreviewOpen(false);
    setRequestBinding(null); setRequestOpen(false);
    setActivePlanTurnId(null);
    shareRevision.current += 1; setShareValue(20); setQualityValue("high"); setShareUpdating(false);
    setTaskPreview(null); setTaskPreviewOpen(false); setTaskAwaitingRead(null);
    setError(null); setTaskActionStatus(null);
    restoredPlanKey.current = null;
    return () => { scopeRevision.current += 1; shareRevision.current += 1; };
  }, [task?.id, materialSelectionKey, pageContext?.pathname]);
  useEffect(() => {
    const saved = assistant.conversation;
    if (!saved) return;
    const planTurn = [...saved.turns].reverse().find((turn) =>
      (turn.result?.kind === "creation_request" && typeof turn.result.creation_request_id === "string")
      || (turn.result?.kind === "plan_preview"
      && typeof turn.result.plan_preview === "object"
      && turn.result.plan_preview !== null),
    );
    const key = `${saved.id}:${planTurn?.id ?? "none"}:${planTurn?.updated_at ?? ""}`;
    if (restoredPlanKey.current === key) return;
    const previousKey = restoredPlanKey.current;
    restoredPlanKey.current = key;
    const restored = planTurn?.result?.plan_preview as CreationPlanPreview | undefined;
    const requestId = planTurn?.result?.kind === "creation_request" ? planTurn.result.creation_request_id as string : null;
    setRequestBinding(requestId && scopeKind === "home" ? { id: requestId, conversationId: saved.id, revision: scopeRevision.current } : null);
    if (!previousKey?.startsWith(`${saved.id}:`)) setRequestOpen(false);
    setActivePlanTurnId(planTurn?.id ?? null);
    setPreview(restored ?? null);
    if (!previousKey?.startsWith(`${saved.id}:`)) setPreviewOpen(false);
    setShareValue(restored?.presenter_share_target ?? 20);
    setQualityValue(restored?.lipsync_quality ?? "high");
  }, [assistant.conversation, scopeId, scopeKind, pageContext?.pathname]);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setModelConfigured(true);
      }
    }).catch(() => undefined);
  }, []);

  async function sendToAssistant(rawMessage: string) {
    const command = rawMessage.trim();
    if (!command || pending) return;
    const referenceSnapshot = latestSelectionSnapshot.current;
    const revision = scopeRevision.current;
    setPending("chat");
    setError(null);
    setTaskActionStatus(null);
    setPreview(null);
    setPreviewOpen(false);
    setRequestBinding(null); setRequestOpen(false);
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
      const expectedDraftVersion = await turn.draftCheckpoint;
      if (!assistant.isCurrentConversation(turn.conversationId) || referenceSnapshot !== latestSelectionSnapshot.current) {
        const message = "会话或引用已切换；本轮没有发送，原要求已保留为草稿。";
        await assistant.failUnpersistedTurn(turn.conversationId, turn.localTurnId,
          message);
        if (assistant.isCurrentConversation(turn.conversationId)) setError(message);
        return;
      }
      if (task) {
        const result = await sendCreationTaskAssistantTurn(task.id, {
          conversation_id: turn.conversationId,
          message: command, context_stage: taskContext?.stage ?? task.stage,
          client_request_id: turn.clientRequestId,
          expected_draft_version: expectedDraftVersion,
          expected_reference_version: selectionState?.version,
          expected_timeline_version_id: task.current_timeline_version_id, expected_timeline_sequence: task.timeline_sequence,
          ...(profileReference ? {selected_profile_reference:{kind:profileReference.kind,profile_id:profileReference.profile_id,version:profileReference.version}} : {}),
          ...(knowledgeReference ? {selected_knowledge_reference:{knowledge_item_id:knowledgeReference.knowledge_item_id,version:knowledgeReference.version,...(knowledgeReference.locator ? {locator:knowledgeReference.locator} : {})}} : {}),
          // With a saved selection, the API derives refs from its frozen CAS.
          // Removing a chip must not resurrect a page's previous local selection.
          selected_object_refs: selectionState ? [] : taskContext?.selectedObjectRefs ?? [], playhead_ms: taskContext?.getPlayheadMs() ?? null,
        }, onStreamEvent);
        if (assistant.isCurrentConversation(turn.conversationId)) await assistant.refresh();
        if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
        setTaskPreview(result.edit_preview);
        setTaskPreviewOpen(Boolean(result.edit_preview));
        if (result.execution_action || result.topic_candidates || result.timeline_action || result.script_draft) onTaskUpdated?.();
      } else {
        const result = (materialContext
          ? await sendMaterialAssistantTurn(command, turn.conversationId, materialContext.selection,
            turn.clientRequestId, expectedDraftVersion, onStreamEvent, selectionState?.version)
          : await sendWorkspaceAssistantPlanTurn(command, turn.conversationId,
          turn.clientRequestId, expectedDraftVersion, onStreamEvent, profileReference ? {kind:profileReference.kind,profile_id:profileReference.profile_id,version:profileReference.version} : undefined, knowledgeReference ? {knowledge_item_id:knowledgeReference.knowledge_item_id,version:knowledgeReference.version,...(knowledgeReference.locator ? {locator:knowledgeReference.locator} : {})} : undefined, selectionState?.version)) as WorkspaceAssistantPlanTurnResponse & { material_result?: MaterialAssistantResult };
        if (assistant.isCurrentConversation(turn.conversationId)) await assistant.refresh();
        if (assistant.isCurrentConversation(turn.conversationId) && materialContext?.selection && result.material_result) materialContext.onResult(materialContext.selection, result.material_result);
        if (revision !== scopeRevision.current || !assistant.isCurrentConversation(turn.conversationId)) return;
        setRequestBinding(result.creation_request ? { id: result.creation_request.id, conversationId: turn.conversationId, revision } : null);
        setRequestOpen(Boolean(result.creation_request));
        setPreview(result.creation_request ? null : result.plan_preview);
        setActivePlanTurnId(result.turn_id ?? null);
        setShareValue(result.plan_preview?.presenter_share_target ?? 20);
        setQualityValue(result.plan_preview?.lipsync_quality ?? "high");
        setPreviewOpen(!result.creation_request && Boolean(result.plan_preview));
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
      if (!requestConversationId || assistant.isCurrentConversation(requestConversationId)) setPending(null);
    }
  }

  async function confirmTaskPreview() {
    if (!task || !taskPreview || pending) return;
    const revision = scopeRevision.current;
    setPending("confirm");
    setError(null);
    try {
      const result = await confirmCreationTaskEdit(task.id, taskPreview.proposal.id);
      if (revision !== scopeRevision.current || latestTaskFacts.current?.id !== task.id) return;
      setTaskPreview(null);
      setTaskPreviewOpen(false);
      const queued = "status" in result;
      if (!queued) setTaskAwaitingRead(latestTaskFacts.current);
      setTaskActionStatus(queued ? `修改已排队，作业 ID：${result.job_id}。队列回执不代表制作完成，当前版本保持不变。` : null);
      onTaskUpdated?.();
    } catch (cause) {
      if (revision === scopeRevision.current && latestTaskFacts.current?.id === task.id) {
        setError(cause instanceof Error ? cause.message : "修改未完成，请重新预览。");
        setTaskPreview(null);
        setTaskPreviewOpen(false);
        setTaskAwaitingRead(latestTaskFacts.current);
        onTaskUpdated?.();
      }
    } finally {
      if (revision === scopeRevision.current) setPending(null);
    }
  }

  async function undoTaskEdit() {
    if (!task || !undoAvailable || pending) return;
    const revision = scopeRevision.current;
    setPending("undo");
    setError(null);
    try {
      await undoCreationTaskTimeline(task.id);
      if (revision !== scopeRevision.current || latestTaskFacts.current?.id !== task.id) return;
      setTaskAwaitingRead(latestTaskFacts.current);
      onTaskUpdated?.();
    } catch (cause) {
      if (revision === scopeRevision.current && latestTaskFacts.current?.id === task.id) {
        setError(cause instanceof Error ? cause.message : "撤销未完成，请到任务页查看。");
        setTaskAwaitingRead(latestTaskFacts.current);
        onTaskUpdated?.();
      }
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

  function startPlanFromReply(turn: AssistantConversationTurn) {
    const conversation = assistant.conversation;
    if (scopeKind !== "home" || task || !conversation
      || !assistant.isCurrentConversation(conversation.id) || turn.status !== "completed"
      || !turn.assistant_text.trim() || !conversation.turns.some(item => item.id === turn.id && item.status === "completed")) {
      setError("当前首页对话或完成回复已变化，没有带入其他内容。");
      return;
    }
    const key = `${conversation.id}:${turn.id}`;
    let requestId = replyPlanRequests.current.get(key);
    if (!requestId) {
      const storageKey = `ai-video-diary.reply-plan-request.v1:${conversation.id}:${turn.id}`;
      try {
        const saved = window.sessionStorage.getItem(storageKey);
        const savedRequestId = saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(saved)
          ? saved : null;
        requestId = savedRequestId ?? crypto.randomUUID();
        if (!savedRequestId) window.sessionStorage.setItem(storageKey, requestId);
      } catch {
        setError("浏览器暂存不可用，没有开始新任务；请恢复暂存后重试。");
        return;
      }
      replyPlanRequests.current.set(key, requestId);
    }
    const query = new URLSearchParams({
      new: "1", reply_conversation_id: conversation.id, reply_turn_id: turn.id,
      reply_request_id: requestId,
    });
    assignLocation(`/production?${query.toString()}`);
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

  const pathname = pageContext?.pathname ?? "/";
  const requestScopeKey = requestBinding ? `${scopeKind}:${scopeId}:${requestBinding.conversationId}:${requestBinding.revision}:${requestBinding.id}` : "";

  return (
    <div className="home-composer">
      {!materialReference && materialContext?.selection ? <p className="home-composer__context" aria-label="当前素材对话范围">
        {materialContext.selection ? `当前范围：${materialContext.label ?? (materialContext.selection.scope === "asset" ? "整条素材" : "当前片段")}` : "当前未选中素材或片段。"}
      </p> : null}

      <AssistantConversationPanel
        autoFocus={autoFocus}
        busy={pending !== null}
        configured={modelConfigured}
        input={assistant.draftText}
        inputLabel={task ? "任务对话要求" : materialContext ? "素材对话" : "制作要求"}
        onInput={(value) => { assistant.setDraftText(value); setPreview(null); setPreviewOpen(false); }}
        onConversationChange={() => { scopeRevision.current += 1; shareRevision.current += 1; setRequestBinding(null); setRequestOpen(false); setPending(null); setShareUpdating(false); setError(null); }}
        onSend={sendToAssistant}
        onTransferReply={taskContext?.onTransferReply}
        transferDraftState={taskContext?.transferDraftState}
        onOpenEditProposal={taskContext?.onOpenEditProposal}
        onCreatePlanFromReply={scopeKind === "home" && !task ? startPlanFromReply : undefined}
        onOpenCreationRequest={scopeKind === "home" ? turn => {
          const id = turn.result?.creation_request_id;
          const conversationId = assistant.conversation?.id;
          if (typeof id !== "string" || !conversationId) return;
          setRequestBinding({ id, conversationId, revision: scopeRevision.current });
          setRequestOpen(true);
        } : undefined}
        scopeLabel={task ? task.name : materialContext ? (materialContext.label ?? (materialContext.selection ? "当前素材" : "素材库")) : pathname === "/" ? "首页创作" : "工作区对话"}
        state={assistant}
      />
      {taskActionStatus ? <p role="status" aria-live="polite">{taskActionStatus}</p> : null}
      {requestBinding && requestOpen && scopeKind === "home" && assistant.conversation?.id === requestBinding.conversationId ? <HomeCardDialog onClose={() => setRequestOpen(false)} title="创作目标"><CreationRequestPanel key={`${requestBinding.conversationId}:${requestBinding.revision}:${requestBinding.id}`} requestId={requestBinding.id} scopeKey={requestScopeKey} goalsDraft={requestGoalsDraft?.key === requestScopeKey ? requestGoalsDraft.text : ""} onGoalsDraftChange={text => setRequestGoalsDraft({ key: requestScopeKey, text })} isCurrentScope={() => requestBinding.revision === scopeRevision.current && assistant.isCurrentConversation(requestBinding.conversationId)} /></HomeCardDialog> : null}
      {preview && !requestBinding ? <button className="home-composer__open-card" onClick={() => setPreviewOpen(true)} type="button">查看方案</button> : null}
      {preview && previewOpen && !requestBinding ? <HomeCardDialog onClose={() => setPreviewOpen(false)} title="制作方案"><section className="home-composer__result" aria-label="制作方案">
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
      {error ? <div className="home-composer__error" role="alert"><AssistantFailureMessage message={error}/></div> : null}
      {awaitingTaskRead ? <p role="status">正在回读当前草稿，撤销暂不可用。</p> : null}
      {task && undoAvailable ? <div className="home-composer__footer"><button disabled={pending !== null} onClick={() => void undoTaskEdit()} type="button">{pending === "undo" ? "撤销中" : "撤销修改"}</button></div> : null}
    </div>
  );
}
