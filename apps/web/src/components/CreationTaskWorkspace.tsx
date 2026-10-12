"use client";
import type { TaskMediaJob, TaskMediaJobPage, TaskMediaResultMap } from "@/lib/types";
import { Tabs } from "@base-ui/react/tabs";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { SubtitleWorkspace } from "./SubtitleWorkspace";
import { GraphicOverlayEditor } from "./GraphicOverlayEditor";
import { DesktopVoiceRecorder } from "./DesktopVoiceRecorder";
import { ExecutionQueueStatus } from "./ExecutionQueueStatus";
import { TimelineVersionHistory } from "./TimelineVersionHistory";

import Link from "next/link";
import NextImage from "next/image";
import { creationTaskFailureReason, creationTaskRepairAction, creationTaskStatusPresentation } from "@/lib/creation-task-status";
import {
  AlertCircle,
  Undo2,
  Redo2,
  Check,
  CheckCircle2,
  Captions,
  Clapperboard,
  ChevronDown,
  FileText,
  History,
  ImageIcon,
  Languages,
  LoaderCircle,
  Mic,
  Play,
  Save,
  Sparkles,
  UserRound,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { AssistantConversationPanel } from "@/components/AssistantConversationPanel";
import { useAssistantKnowledgeReference, useAssistantMaterialReference, useAssistantProfileReference } from "./AssistantConversationProvider";
import { useAssistantConversation } from "@/components/useAssistantConversation";
import { AppShell } from "@/components/AppShell";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import {
  apiUrl,
  getCreationTaskMediaJob,
  listCreationTaskMediaJobs,
  taskMediaJobMatchesContext,
  cancelCreationTaskMediaJob,
  resolveCreationTaskMediaResponse,
  taskMediaJobLabel,
  TaskMediaJobError,
  checkCreationTaskQuality,
  getCreationTaskQualityCheck,
  confirmCreationTaskEdit,
  confirmCreationTaskScript,
  transferAssistantReplyToScript,
  getAssistantScriptContext,
  saveAssistantScriptDraft,
  saveScriptLibraryEntry,
  confirmCreationTaskTopic,
  CreationPlanApiError,
  generateCreationTaskPersonRender,
  generateCreationTaskScript,
  generateCreationTaskTopics,
  getCreationTaskMontageClipCandidates,
  getCreationTaskTopics,
  getCreationPlan,
  getCreationTaskWorkspace,
  getCreationTaskEditPreview,
  setCreationPlanVisibility,
  setCreationTaskVisibility,
  getProductionMaterials,
  initializeCreationTaskTimeline,
  getCreationExecution,
  getTaskJobProgress,
  getCreationTaskAssistantHistory,
  previewCreationExecution,
  startCreationExecution,
  resumeCreationExecution,
  cancelCreationExecution,
  getRuntimeSettings,
  previewCreationTaskEdit,
  sendCreationTaskAssistantTurn,
  sendMaterialAssistantTurn,
  previewCompoundSelection,
  previewCreationTaskExactEdit,
  applyCreationTaskExactEdit,
  selectCreationTaskNarrationVoice,
  listCreationTaskRecordings,
  adoptCreationTaskRecording,
  undoCreationTaskTimeline,
  redoCreationTaskTimeline,
} from "@/lib/api";
import type {
  CreationTask,
  AssistantConversationTurn,
  CreationTaskQualityCheck,
  CreationTaskExactEditInput,
  CreationTaskEditPreviewResponse,
  CreationTaskAssistantTurnResponse,
  CreationTaskEditOperation,
  CreationTaskNarrationSegment,
  CreationTaskRecording,
  CreationTaskPersonRender,
  CreationTaskScript,
  CreationTaskSubtitleCue,
  CreationTaskSubtitleTrack,
  CreationTaskTopicsResponse,
  CreationTaskWorkspace as WorkspaceState,
  CreationExecutionPreview,
  CreationExecutionStatus,
  TaskJobProgress,
  CreationTaskAssistantHistory,
  AssistantTurnActivity,
  MaterialAsset,
  MaterialSegmentMatch,
  MontageClipCandidate,
} from "@/lib/types";
import styles from "./CreationTaskWorkspace.module.css";
import { AudioTimelineTracks, AudioTimelineInspector } from "./AudioTimelineEditor";
import { previewCreationTaskAudio, previewCreationTaskVisual, saveCreationTaskRecording } from "@/lib/api";
import type { AudioEditInput, VisualEditInput } from "@/lib/types";
import { SaveVideoTemplateForm } from "./SaveVideoTemplateForm";
import { VideoEditingStage, VideoClipInspector, VideoClipQuickEditor, createVideoClipDraft, editorTime, type VideoClipDraft } from "./VideoEditor";
import editorUi from "./VideoEditor.module.css";

type Props = {
  initialWorkspace: WorkspaceState;
  initialTopics: CreationTaskTopicsResponse | null;
  initialTasks: CreationTask[];
};

type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "reply-transfer" | "execution" | "voice-select" | "recording-adopt" | "person-render" | "clip" | "save" | "assistant" | "edit-preview" | "edit-confirm" | "undo";

type PendingLocalEdit = {
  taskId: string;
  jobId: string;
  operationType: CreationTaskEditOperation["operation_type"];
  targetRefs: string[];
};

function queuedEditNotice(operation: PendingLocalEdit["operationType"]): string {
  return operation === "RegenerateNarrationSegment"
    ? "配音修改已排队，取得设备许可并完成后才更新目标配音段；当前版本和音频保持不变。"
    : "人物修改已排队，取得设备许可并完成后才更新目标镜头；当前版本保持不变。";
}

type WorkspaceView = CreationTask["stage"] | "subtitle_cover" | "prepare" | "person" | "publish";

type SelectedEditContext = {
  kind: "narration" | "cue" | "clip" | "cover" | "audio" | "overlay";
  id: string;
  label: string;
  placeholder: string;
  ref: string;
};

const productStages = [
  { id: "prepare", label: "资料", title: "资料准备" },
  { id: "topic", label: "选题", title: "选题" },
  { id: "script", label: "文案", title: "文案" },
  { id: "narration", label: "配音", title: "配音" },
  { id: "person", label: "人物口型", title: "人物与口型" },
  { id: "montage", label: "剪辑", title: "视频剪辑" },
  { id: "subtitle_cover", label: "字幕封面", title: "字幕与封面" },
  { id: "review", label: "输出", title: "检查与输出" },
  { id: "publish", label: "发布", title: "发布与数据" },
] as const;

const stageLabels = {
  prepare: "资料",
  topic: "选题",
  script: "文案",
  narration: "配音",
  person: "人物与口型",
  montage: "视频剪辑",
  subtitle_cover: "字幕与封面",
  review: "检查成片",
  publish: "发布",
};

function defaultWorkspaceView(workspace: WorkspaceState): WorkspaceView {
  const hasValidSubtitle = workspace.subtitle_tracks.some((track) => track.status === "valid");
  const hasValidCover = workspace.cover_asset?.status === "valid";
  if (workspace.task.stage === "review" && workspace.montage?.output_url && hasValidSubtitle && !hasValidCover) {
    return "subtitle_cover";
  }
  return workspace.task.stage;
}

type ProductStageState = "done" | "skipped" | "current" | "upcoming";

// 人物与口型按 PRD 9.1 判断：露脸目标 0% 是明确跳过而不是完成；目标未记录不能当作 0%。
function personStageFacts(workspace: WorkspaceState): { state: ProductStageState; detail: string } {
  const target = workspace.plan.presenter_share_target ?? workspace.montage?.presenter_share_target ?? null;
  if (target === 0) return { state: "skipped", detail: "明确跳过 · 目标 0%" };

  // 只有属于当前配音、人物与声音版本仍是当前方案的口型才算本阶段的当前产物；
  // 旧人物版本留下的记录不代表这一阶段做完了。
  const person = workspace.plan.selected_person;
  const voice = workspace.plan.selected_voice;
  const segmentIds = new Set(workspace.narration?.segments.map((segment) => segment.id) ?? []);
  const renders = workspace.person_renders.filter((render) => segmentIds.has(render.narration_segment_id)
    && Boolean(person)
    && render.person_profile_id === person?.profile_id
    && render.person_version === person?.version
    && (!voice || (render.voice_profile_id === voice.profile_id && render.voice_version === voice.version)));
  // 本任务真正需要的口型由成片镜头引用决定；有失败或缺覆盖证据时不能算整个阶段完成。
  const required = (workspace.montage?.clips ?? [])
    .map((clip) => clip.person_render_id)
    .filter((id): id is string => Boolean(id))
    .map((id) => renders.find((render) => render.id === id));
  if (required.length && required.every((render) => render?.status === "ready")) return { state: "done", detail: "已完成" };

  const ready = renders.filter((render) => render.status === "ready").length;
  if (renders.some((render) => render.status === "failed")) {
    return { state: "current", detail: ready ? `已生成 ${ready}/${renders.length} 段 · 仍有口型未完成` : "有口型需要处理" };
  }
  if (renders.length) return { state: "current", detail: `已生成 ${ready}/${renders.length} 段 · 仍待处理` };
  if (!workspace.narration) return { state: "upcoming", detail: "待配音" };
  if (target === null) return { state: "upcoming", detail: "未记录露脸目标" };
  if (!person) return { state: "current", detail: "尚未选择人物版本" };
  if (!workspace.person_render_capability.available) return { state: "current", detail: "口型能力不可用" };
  return { state: "current", detail: "待生成口型" };
}

// 阶段状态只由任务事实决定，不用阶段序号推断前面的阶段已完成。
function productStageState(
  stageId: typeof productStages[number]["id"],
  workspace: WorkspaceState,
  subtitleCoverComplete: boolean,
): { state: ProductStageState; detail: string } {
  const { task, selected_topic: topic, scripts, narration, montage } = workspace;
  const rendered = Boolean(montage?.output_url);

  if (stageId === "prepare") {
    return task.plan_id ? { state: "done", detail: "已完成" } : { state: "upcoming", detail: "未确认方案" };
  }
  if (stageId === "publish") return { state: "upcoming", detail: "在发布工作区处理" };

  if (stageId === "topic") {
    if (topic) return { state: "done", detail: "已完成" };
    return task.stage === "topic" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
  }
  if (stageId === "script") {
    if (scripts[0]?.selection_status === "confirmed") return { state: "done", detail: "已完成" };
    if (scripts[0]?.selection_status === "draft" && scripts.some(script => script.id === task.confirmed_script_id && script.selection_status === "confirmed")) return { state: "current", detail: "有新草稿待确认" };
    if (task.stage === "script") return { state: "current", detail: "当前" };
    return scripts.length ? { state: "current", detail: "待确认" } : { state: "upcoming", detail: "未开始" };
  }
  if (stageId === "narration") {
    if (narration) {
      // 配音产物在生成中就已存在，必须按分段状态判定，不能因为对象存在就当成功。
      const segments = narration.segments;
      if (segments.some((segment) => segment.status === "failed")) return { state: "current", detail: "有配音段未完成" };
      if (segments.length && segments.every((segment) => segment.status === "ready")) return { state: "done", detail: "已完成" };
      if (segments.length) return { state: "current", detail: "配音生成中" };
    }
    return task.stage === "narration" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
  }
  if (stageId === "person") return personStageFacts(workspace);
  if (stageId === "montage") {
    if (rendered) return { state: "done", detail: "已完成" };
    if (montage) return { state: "current", detail: montage.status === "waiting_for_user" ? "待确认" : "故事板已生成" };
    return task.stage === "montage" ? { state: "current", detail: "当前" } : { state: "upcoming", detail: "未开始" };
  }
  if (stageId === "subtitle_cover") {
    if (subtitleCoverComplete) return { state: "done", detail: "已完成" };
    if (!rendered) return { state: "upcoming", detail: "未开始" };
    return { state: "current", detail: workspace.cover_asset?.status === "valid" ? "待补字幕" : "待补封面" };
  }
  if (!subtitleCoverComplete) return { state: "upcoming", detail: "待上一步" };
  if (task.stage === "review") return { state: "current", detail: "当前" };
  return rendered ? { state: "done", detail: "已完成" } : { state: "upcoming", detail: "未开始" };
}

function errorDetails(cause: unknown) {
  if (cause instanceof CreationPlanApiError) {
    return {
      message: cause.message,
      impact: cause.impact,
      repairAction: cause.repairAction,
      code: cause.code,
    };
  }
  return {
    message: cause instanceof Error ? cause.message : "操作没有完成。",
    impact: null,
    repairAction: null,
    code: null,
  };
}

function TaskIssue({ stage, reason, impact, repair, code, label, children }: {
  stage: string; reason: string | null; impact?: string | null; repair?: string | null;
  code?: string | null; label?: string; children?: ReactNode;
}) {
  return <section className={styles.blocker} role="alert" aria-label={label}>
    <AlertCircle size={16} /><div className={styles.issueBody}>
      <strong>{creationTaskFailureReason(reason ?? impact ?? null, stage) ?? "这次操作没有完成"}</strong>
      {children}
      <details className={styles.traceDetails}><summary>诊断详情</summary>
        {reason ? <p>原始原因：{reason}</p> : null}
        {impact ? <p>本次影响：{impact}</p> : null}
        {repair ? <p>处理建议：{creationTaskRepairAction(repair)}</p> : null}
        {repair ? <p>后台处理原文：{repair}</p> : null}
        {code ? <p>后台标识：{code}</p> : null}
      </details>
    </div>
  </section>;
}

function Inspector({ activeView, busy, workspace, onPreparePersonEdit, selectedContext }: { activeView: WorkspaceView; busy: boolean; workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null; onPreparePersonEdit: (clipId: string, command: string) => void; selectedContext: SelectedEditContext | null }) {
  const { task, plan, selected_topic: topic, narration, montage } = workspace;
  const latestScript = workspace.scripts[0] ?? null;
  const currentNarration = narration?.task.id === task.id && narration.script_id === task.confirmed_script_id
    ? narration
    : null;
  const viewingCurrentStage = activeView === task.stage;
  const selectedNarration = selectedContext?.kind === "narration" ? narration?.segments.find((segment) => segment.id === selectedContext.id) ?? null : null;
  const selectedClip = selectedContext?.kind === "clip" ? montage?.clips.find((clip) => clip.id === selectedContext.id) ?? null : null;
  const selectedPersonRender = selectedClip?.person_render_id
    ? workspace.person_renders.find((render) => render.id === selectedClip.person_render_id) ?? null
    : null;
  const selectedPersonNarration = selectedPersonRender
    ? narration?.segments.find((segment) => segment.id === selectedPersonRender.narration_segment_id) ?? null
    : null;
  const personRenderCapability = workspace.person_render_capability;
  const personRenderCanRegenerate = selectedPersonRender?.status === "ready" || selectedPersonRender?.status === "failed";
  const personRenderStatus = selectedPersonRender?.status === "ready"
    ? "已写入当前版本"
    : selectedPersonRender?.status === "failed"
      ? "生成失败，可重新对口型"
      : selectedPersonRender?.status === "running"
        ? "正在生成口型"
        : selectedPersonRender?.status === "pending"
          ? "等待生成口型"
          : selectedClip?.person_render_id
            ? "人物片段记录缺失"
            : "尚未写入";
  return (
    <div className={styles.inspector}>
      <section>
        <span className={styles.eyebrow}>{viewingCurrentStage ? "当前状态" : "正在回看"}</span>
        <h3>{stageLabels[activeView]}</h3>
        <p>{viewingCurrentStage ? (task.stage_status === "failed" ? "当前步骤需要处理" : task.stage_status === "waiting_for_user" ? "正在等待你的确认" : task.stage_status === "running" ? "正在处理" : "可以开始下一步") : "这是已经持久化的任务结果，不会因为回看而重新运行。"}</p>
      </section>
      {selectedNarration ? <section aria-label="已选配音详情"><span className={styles.eyebrow}>已选配音</span><h3>{selectedContext?.label}</h3><p>{selectedNarration.text}</p><dl><div><dt>片段时长</dt><dd>{selectedNarration.duration_seconds?.toFixed(1) ?? "—"} 秒</dd></div><div><dt>声音版本</dt><dd>{selectedNarration.voice_version}</dd></div><div><dt>模型</dt><dd>{selectedNarration.model_id}</dd></div><div><dt>状态</dt><dd>{narrationSegmentStatus(selectedNarration.status)}</dd></div></dl></section> : null}
      {selectedClip ? <section aria-label="已选镜头详情">
        <span className={styles.eyebrow}>已选镜头</span><h3>{selectedContext?.label}</h3>
        <dl>
          <div><dt>用途</dt><dd>{selectedClip.person_render_id ? "人物口型" : selectedClip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div>
          <div><dt>成片位置</dt><dd>{selectedClip.timeline_start_seconds.toFixed(1)}–{selectedClip.timeline_end_seconds.toFixed(1)} 秒</dd></div>
          <div><dt>人物版本</dt><dd>{selectedPersonRender ? `${selectedPersonRender.person_profile_id} · ${selectedPersonRender.person_version}` : selectedClip.person_render_id ? "未读取到人物片段记录" : plan.selected_person ? `${plan.selected_person.profile_id} · ${plan.selected_person.version}` : "尚未选择人物版本"}</dd></div>
          {selectedPersonRender ? <>
            <div><dt>声音版本</dt><dd>{selectedPersonRender.voice_profile_id} · {selectedPersonRender.voice_version}</dd></div>
            <div><dt>语言</dt><dd>{selectedPersonNarration?.language === "zh" ? "中文" : "未记录"}</dd></div>
            <div><dt>口型效果</dt><dd>{lipsyncLabel(selectedPersonRender.model_id)}</dd></div>
            <div><dt>动作</dt><dd>{selectedPersonRender.motion === "natural" ? "自然（固定）" : selectedPersonRender.motion}</dd></div>
            <div><dt>表情</dt><dd>{selectedPersonRender.expression === "neutral" ? "中性（固定）" : selectedPersonRender.expression}</dd></div>
            <div><dt>口型偏移</dt><dd>自动匹配（暂不可手动调整）</dd></div>
          </> : null}
          <div><dt>片段状态</dt><dd>{personRenderStatus}</dd></div>
          <div><dt>锁定</dt><dd>{selectedClip.locked ? "已锁定" : "可调整"}</dd></div>
          {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 ? <div><dt>当前口型</dt><dd>{lipsyncLabel(personRenderCapability.model_id)} · {personRenderCapability.available ? "可用" : "不可用"}</dd></div> : null}
        </dl>
        {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 && !personRenderCapability.available ? <div aria-label="人物口型不可用原因" className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{personRenderCapability.message}</p>{personRenderCapability.repair_action ? <small>处理方式：{personRenderCapability.repair_action}</small> : null}</div> : null}
        {selectedPersonRender ? <p className={styles.personCapability}>动作/表情沿用原人物素材，口型匹配当前配音。</p> : null}
        {(plan.presenter_share_target ?? montage?.presenter_share_target) !== 0 ? !selectedClip.person_render_id
          ? <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available} onClick={() => onPreparePersonEdit(selectedClip.id, "这一段换成当前人物出镜")} type="button"><UserRound size={15} />换成当前人物出镜</button>
          : <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available || !personRenderCanRegenerate} onClick={() => onPreparePersonEdit(selectedClip.id, "这里只重做口型，其他都不要动")} type="button"><UserRound size={15} />重新对口型</button>
          : null}
      </section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>{currentNarration?.source_kind === "recorded" ? "本人录音" : "声音与授权"}</span>
        {currentNarration?.source_kind === "recorded" ? <dl><div><dt>来源</dt><dd>本人录音 · 已采用</dd></div><div><dt>录音</dt><dd>{currentNarration.source_artifact_ref ?? "任务录音"}</dd></div><div><dt>实际时长</dt><dd>{currentNarration.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>声音模型</dt><dd>本人录音，不使用声音克隆模型</dd></div></dl>
          : <dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{currentNarration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl>}</section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>露脸</dt><dd>目标 {montage.presenter_share_target ?? 0}% · 实际 {montage.presenter_share_actual ?? 0}%</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div></dl></section> : null}
      {activeView === "prepare" ? <section><span className={styles.eyebrow}>本次素材</span><dl><div><dt>方案匹配</dt><dd>{plan.material_matches.length} 个片段</dd></div><div><dt>方案可用素材</dt><dd>{plan.approved_asset_count} 个</dd></div><div><dt>方案可用镜头</dt><dd>{plan.approved_clip_count} 个</dd></div></dl></section> : null}
      {activeView === "publish" ? <section><span className={styles.eyebrow}>发布能力</span><dl><div><dt>发布记录</dt><dd>在发布工作区读取</dd></div><div><dt>账号与确认</dt><dd>发布前核对</dd></div><div><dt>本任务结果</dt><dd>未返回发布结果</dd></div></dl></section> : null}
    </div>
  );
}

function capabilityLabel(available: boolean) {
  return available ? "可用" : "不可用";
}

function lipsyncLabel(modelId: string | null | undefined) {
  return modelId?.startsWith("latentsync-")
    ? "高精度 · LatentSync"
    : modelId?.toLowerCase().includes("musetalk") ? "低精度 · MuseTalk" : modelId ? "其他口型" : "未配置";
}

function SubtitleCoverInspector({
  selectedCover,
  selectedCue,
  selectedTrack,
  workspace,
}: {
  selectedCover: boolean;
  selectedCue: CreationTaskSubtitleCue | null;
  selectedTrack: CreationTaskSubtitleTrack | null;
  workspace: WorkspaceState;
}) {
  if (selectedCue && selectedTrack) {
    const translationCapability = workspace.subtitle_translation_capability;
    return (
      <div className={styles.inspector}>
        <section aria-label="已选字幕详情">
          <span className={styles.eyebrow}>{selectedTrack.language === "zh-CN" ? "中文字幕" : "英文字幕"}</span>
          <h3>{selectedCue.text}</h3>
          <dl>
            <div><dt>时间</dt><dd>{selectedCue.start_seconds.toFixed(1)}–{selectedCue.end_seconds.toFixed(1)} 秒</dd></div>
            <div><dt>样式</dt><dd>{selectedTrack.style.name === "emphasis" ? "重点强调" : "清爽底栏"}</dd></div>
            <div><dt>断行</dt><dd>{selectedCue.line_breaks.length ? `${selectedCue.line_breaks.length + 1} 行` : "单行"}</dd></div>
            <div><dt>强调</dt><dd>{selectedCue.emphasis.length ? `${selectedCue.emphasis.length} 处` : "无"}</dd></div>
            <div><dt>字幕产物</dt><dd>{selectedTrack.artifact_ref ?? "尚未生成"}</dd></div>
            <div><dt>运行证据</dt><dd>{selectedTrack.transform_id ?? "尚无"}</dd></div>
            {selectedTrack.language === "en-US" ? <div><dt>翻译能力</dt><dd>{capabilityLabel(translationCapability.available)} · {translationCapability.model_id ?? "未配置模型"}</dd></div> : null}
          </dl>
          {selectedTrack.language === "en-US" && !translationCapability.available && translationCapability.repair_action ? <p className={styles.inspectorRepair}>处理方式：{translationCapability.repair_action}</p> : null}
        </section>
      </div>
    );
  }

  if (selectedCover) {
    const cover = workspace.cover_asset;
    const frameCapability = workspace.cover_frame_capability;
    const imageCapability = workspace.image_generation_capability;
    return (
      <div className={styles.inspector}>
        <section aria-label="已选封面详情">
          <span className={styles.eyebrow}>封面参数</span>
          <h3>{cover?.title || "待生成封面"}</h3>
          <dl>
            <div><dt>真实画面</dt><dd>{cover ? `${cover.frame_time_seconds.toFixed(1)} 秒` : "尚未确认"}</dd></div>
            <div><dt>标题位置</dt><dd>{cover ? cover.layout.title_position === "center" ? "画面中部" : "画面底部" : "尚未确认"}</dd></div>
            <div><dt>安全区</dt><dd>{cover ? `${cover.safe_area.top}/${cover.safe_area.right}/${cover.safe_area.bottom}/${cover.safe_area.left}` : "默认安全区"}</dd></div>
            <div><dt>图片产物</dt><dd>{cover?.artifact_ref ?? "尚未生成"}</dd></div>
            <div><dt>运行证据</dt><dd>{cover?.transform_id ?? "尚无"}</dd></div>
            <div><dt>选帧能力</dt><dd>{capabilityLabel(frameCapability.available)} · {frameCapability.model_id ?? "未配置引擎"}</dd></div>
            <div><dt>AI 生图</dt><dd>{capabilityLabel(imageCapability.available)} · {imageCapability.model_id ?? "未配置模型"}</dd></div>
          </dl>
          {!frameCapability.available && frameCapability.repair_action ? <p className={styles.inspectorRepair}>处理方式：{frameCapability.repair_action}</p> : null}
        </section>
      </div>
    );
  }

  return null;
}

function ConfirmedTopicView({ workspace }: { workspace: WorkspaceState }) {
  const topic = workspace.selected_topic;
  if (!topic) return null;
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>{topic.title}</h2><p>{topic.angle}</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15} />已确认</span></div>
      <div className={styles.artifactGrid}>
        <section><strong>目标观众</strong><p>{topic.audience}</p></section>
        <section><strong>内容结构</strong><ol>{topic.outline.map((item) => <li key={item}>{item}</li>)}</ol></section>
      </div>
      <details className={styles.requirementDetails}><summary>来源证据</summary><div className={styles.evidenceList}>{workspace.topic_evidence.length ? workspace.topic_evidence.map((item) => <article key={item.id}><span>{item.kind === "knowledge" ? "知识库" : "信息源"}</span><div><strong>{item.title}</strong><small>{item.authorization_or_license}</small></div></article>) : <p>当前选题没有返回可展示的来源条目。</p>}</div></details>
    </section>
  );
}

function ConfirmedScriptView({ script }: { script: CreationTaskScript }) {
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><h2>{script.title}</h2><span className={styles.artifactStatus}>{script.selection_status === "confirmed" ? "已确认" : "待确认草稿"}</span></div>
      <div className={styles.scriptSections}>{script.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><p className={styles.scriptCopy}>{section.content}</p><p><strong>画面建议：</strong>{section.visual_prompt}</p><details className={styles.traceDetails}><summary>来源证据</summary><p>{section.evidence_reference_ids.join("、") || "未单独标注"}</p></details></article>)}</div>
    </section>
  );
}

function ReadonlyScriptsView({ workspace }: { workspace: WorkspaceState }) {
  const latest = workspace.scripts[0];
  if (!latest) return null;
  const confirmed = workspace.scripts.find(script => script.id === workspace.task.confirmed_script_id && script.selection_status === "confirmed");
  if (!confirmed || confirmed.id === latest.id || latest.selection_status !== "draft") return <ConfirmedScriptView script={latest} />;
  return <Tabs.Root defaultValue="confirmed" className={styles.scriptReview}>
    <Tabs.List className={styles.reviewTabs} aria-label="文案版本"><Tabs.Tab value="confirmed">当前确认稿</Tabs.Tab><Tabs.Tab value="draft">新草稿</Tabs.Tab></Tabs.List>
    <Tabs.Panel value="confirmed" keepMounted><ConfirmedScriptView script={confirmed} /></Tabs.Panel>
    <Tabs.Panel value="draft" keepMounted><ConfirmedScriptView script={latest} /></Tabs.Panel>
  </Tabs.Root>;
}

function narrationSegmentStatus(status: CreationTaskNarrationSegment["status"]) {
  if (status === "ready") return "可试听";
  if (status === "generating") return "正在生成";
  if (status === "pending") return "等待生成";
  return "生成失败";
}

function personRenderStageDetail(status: CreationTaskPersonRender["status"]) {
  if (status === "ready") return "口型已生成";
  if (status === "failed") return "口型生成失败";
  return "口型生成中";
}

function PersonRenderPanel({ busy, onGenerate, selectedRender, selectedSegmentId, workspace }: {
  busy: boolean;
  onGenerate: (segmentId: string) => void;
  selectedRender: CreationTaskPersonRender | null;
  selectedSegmentId: string | null;
  workspace: WorkspaceState;
}) {
  return (
    <div className={styles.personRenderPanel}>
      <div>
        <UserRound size={18} />
        <div><strong>人物口型预览</strong><p>选择一段配音，用当前人物版本检查口型效果；确认编辑后才会进入成片时间线。</p></div>
      </div>
      {!workspace.plan.selected_person ? <span role="status">当前方案没有选择人物版本；请先在制作方案中选择人物。</span> : null}
      {!selectedSegmentId ? <span>先选择上方一段配音</span> : null}
      {selectedSegmentId && !selectedRender && workspace.plan.selected_person ? <button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId)} type="button">生成人物口型预览</button> : null}
      {selectedRender?.status === "running" || selectedRender?.status === "pending" ? <span><LoaderCircle className="is-spinning" size={15} />正在生成人物口型预览</span> : null}
      {selectedRender?.status === "failed" ? <div className={styles.personRenderFailure}><strong>生成没有完成</strong><p>{selectedRender.impact ?? selectedRender.failure_reason}</p>{selectedRender.repair_action ? <small>{selectedRender.repair_action}</small> : null}<button className="modern-primary-button" disabled={busy} onClick={() => onGenerate(selectedSegmentId!)} type="button">重新生成</button></div> : null}
      {selectedRender?.status === "ready" && selectedRender.media_url ? <div className={styles.personRenderPreview}><video controls preload="metadata" src={apiUrl(selectedRender.media_url)} /><p>{selectedRender.model_id} · {selectedRender.duration_seconds?.toFixed(1)} 秒 · 人物 {selectedRender.person_version}</p></div> : null}
    </div>
  );
}

function NarrationResultView({ busy, onGeneratePersonRender, onSelect, selectedSegmentId, workspace }: {
  busy: boolean;
  onGeneratePersonRender: (segmentId: string) => void;
  onSelect: (segmentId: string) => void;
  selectedSegmentId: string | null;
  workspace: WorkspaceState;
}) {
  const narration = workspace.narration;
  if (!narration) return null;
  const ready = narration.segments.length > 0 && narration.segments.every(segment => segment.status === "ready");
  const selectedRender = workspace.person_renders.find(
    (render) => render.narration_segment_id === selectedSegmentId,
  ) ?? null;
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>试听当前声音来源</h2><p>{narration.model_id ?? "本机录音"} · {narration.duration_seconds.toFixed(1)} 秒{narration.sample_rate ? ` · ${narration.sample_rate} Hz` : ""}</p></div><span className={styles.artifactStatus}>{ready ? "已采用" : narration.segments.some(segment => segment.status === "failed") ? "有段落未完成" : "等待配音完成"}</span></div>
      {ready ? <audio className={styles.audioPreview} controls preload="metadata" src={apiUrl(`/api/creation-tasks/${workspace.task.id}/narration/audio`)} /> : null}
      <div className={styles.narrationSegments}>
        {narration.segments.map((segment, index) => (
          <article className={selectedSegmentId === segment.id ? styles.selectedSegment : ""} key={segment.id}>
            <button
              aria-label={`选择配音 ${index + 1}`}
              aria-pressed={selectedSegmentId === segment.id}
              disabled={busy}
              onClick={() => onSelect(segment.id)}
              type="button"
            >
              <span>第 {index + 1} 段</span>
              <strong>{segment.text}</strong>
              <small>{segment.duration_seconds !== null ? `${segment.duration_seconds.toFixed(1)} 秒 · ` : ""}{narrationSegmentStatus(segment.status)}</small>
            </button>
            {segment.audio_url ? <audio aria-label={`试听配音 ${index + 1}`} controls preload="metadata" src={apiUrl(segment.audio_url)} /> : null}
          </article>
        ))}
      </div>
      <PersonRenderPanel busy={busy} onGenerate={onGeneratePersonRender} selectedRender={selectedRender} selectedSegmentId={selectedSegmentId} workspace={workspace} />
    </section>
  );
}

function PersonStageView({ busy, onGenerate, onOpenNarration, onSelect, selectedSegmentId, workspace }: {
  busy: boolean;
  onGenerate: (segmentId: string) => void;
  onOpenNarration: () => void;
  onSelect: (segmentId: string) => void;
  selectedSegmentId: string | null;
  workspace: WorkspaceState;
}) {
  const { montage, narration, plan, person_render_capability: capability } = workspace;
  const renders = workspace.person_renders;
  const presenterShareTarget = plan.presenter_share_target ?? montage?.presenter_share_target ?? null;
  const selectedRender = renders.find((render) => render.narration_segment_id === selectedSegmentId) ?? null;
  const summary = presenterShareTarget === 0
    ? "本方案的露脸目标为 0%，自动口型阶段已跳过。"
    : renders.some((render) => render.status === "ready")
      ? `已有 ${renders.filter((render) => render.status === "ready").length} 段人物口型预览。`
      : renders.some((render) => render.status === "failed")
        ? "有口型没有生成完成，需要处理后才能进入成片。"
        : renders.length
          ? "口型正在生成中。"
          : "还没有生成任何口型；系统只为选中的露脸段生成口型。";
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>当前口型</h2><p>{summary}</p></div><span className={styles.artifactStatus}><UserRound size={15} />{presenterShareTarget === 0 ? "明确跳过" : "按露脸段生成"}</span></div>
      <details className={styles.requirementDetails}><summary>人物与口型要求</summary><div className={styles.artifactGrid}>
        <section><strong>露脸目标</strong><p>{presenterShareTarget === null ? "当前方案没有记录露脸占比。" : `${presenterShareTarget}% · 实际 ${montage?.presenter_share_actual == null ? "未返回" : `${montage.presenter_share_actual}%`}`}</p></section>
        <section><strong>口型档位</strong><p>{plan.lipsync_quality === "high" ? "高精度 · LatentSync" : plan.lipsync_quality === "fast" ? "低精度 · MuseTalk" : "方案未选择档位"}</p></section>
        <section><strong>人物素材</strong><p>{workspace.portrait_available === undefined ? "未返回该事实" : workspace.portrait_available ? "已就绪" : "没有可用人物素材"}</p></section>
        {presenterShareTarget !== 0 ? <section><strong>当前口型能力</strong><p>{lipsyncLabel(capability.model_id)} · {capabilityLabel(capability.available)}</p></section> : null}
      </div>
      </details>
      {presenterShareTarget !== 0 && !capability.available ? <div className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{capability.message}</p>{capability.repair_action ? <small>处理方式：{capability.repair_action}</small> : null}</div> : null}
      {!narration ? <div className={styles.emptyAction}><UserRound size={30} /><h2>还没有配音事实</h2><p>口型由最终配音驱动。先生成配音，才能为选中的露脸段制作口型。</p><button className="modern-primary-button" disabled={busy} onClick={onOpenNarration} type="button">前往配音</button></div> : null}
      {narration && presenterShareTarget === null ? <div className={styles.emptyAction}><UserRound size={30} /><h2>未记录露脸占比</h2><p>当前方案的露脸占比没有记录，不能按 0% 当作跳过处理。请先在制作方案中确认露脸占比，再回到本阶段生成口型。</p></div> : null}
      {narration && presenterShareTarget === 0 ? <div className={styles.emptyAction}><UserRound size={30} /><p>方案目标不会覆盖当前成片的实际露脸结果。{montage?.presenter_share_actual == null ? "当前未返回实际占比。" : `当前成片实际露脸 ${montage.presenter_share_actual}%。`}</p></div> : null}
      {narration && presenterShareTarget !== null && presenterShareTarget > 0 && !plan.selected_person
        ? <div className={styles.emptyAction}><UserRound size={30} /><h2>尚未选择当前人物</h2><p>声音档案中的关联人物不能替代本任务的已选人物。请先在制作方案中选择人物版本，再为配音片段生成口型。</p></div>
        : null}
      {presenterShareTarget === 0 && renders.length ? <details className={styles.requirementDetails}><summary>已保存口型（{renders.length} 段）</summary>{renders.map(render => <article key={render.id}><p>{personRenderStageDetail(render.status)}</p>{render.status === "ready" && render.media_url ? <video controls preload="metadata" aria-label="已保存口型预览" className={styles.materialPreview} src={apiUrl(render.media_url)} /> : null}</article>)}</details> : null}
      {narration && presenterShareTarget !== null && presenterShareTarget > 0 && plan.selected_person ? <>
        <div className={styles.narrationSegments}>
          {narration.segments.map((segment, index) => (
            <article className={selectedSegmentId === segment.id ? styles.selectedSegment : ""} key={segment.id}>
              <button
                aria-label={`选择配音 ${index + 1}`}
                aria-pressed={selectedSegmentId === segment.id}
                disabled={busy}
                onClick={() => onSelect(segment.id)}
                type="button"
              >
                <span>第 {index + 1} 段</span>
                <strong>{segment.text}</strong>
                <small>{segment.duration_seconds !== null ? `${segment.duration_seconds.toFixed(1)} 秒 · ` : ""}{narrationSegmentStatus(segment.status)}{renders.some((render) => render.narration_segment_id === segment.id) ? ` · ${personRenderStageDetail(renders.find((render) => render.narration_segment_id === segment.id)!.status)}` : " · 未生成口型"}</small>
              </button>
            </article>
          ))}
        </div>
        <PersonRenderPanel busy={busy} onGenerate={onGenerate} selectedRender={selectedRender} selectedSegmentId={selectedSegmentId} workspace={workspace} />
      </> : null}
    </section>
  );
}

function ScriptLibrarySave({ script }: { script: CreationTaskScript }) {
  const [name, setName] = useState(script.title);
  const [category, setCategory] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [savedEntry, setSavedEntry] = useState<{ id: string; source_task_id: string; version: number } | null>(null);
  async function save() {
    setSaving(true); setFeedback(null); setFailed(false);
    try {
      const entry = await saveScriptLibraryEntry({ source_script_id: script.id, name, category });
      setSavedEntry(entry);
      setFeedback(`已保存「${entry.name}」版本 ${entry.version}；原文案与成片不变。`);
    } catch (cause) {
      setFailed(true); setFeedback(cause instanceof Error ? cause.message : "文案保存失败");
    } finally { setSaving(false); }
  }
  return <section className={styles.workSection} aria-label="保存到文案库">
    <h3>保存为可复用文案</h3>
    <label className={styles.field}><span>文案名称</span><input value={name} onChange={event => setName(event.target.value)} /></label>
    <label className={styles.field}><span>文案分类</span><input placeholder="例如产品介绍" value={category} onChange={event => setCategory(event.target.value)} /></label>
    <button className="modern-primary-button" disabled={saving || !name.trim()} onClick={() => void save()} type="button">{saving ? "正在保存…" : "存入文案库"}</button>
    <Link href={savedEntry ? `/knowledge?entry_id=${encodeURIComponent(savedEntry.id)}&source_task_id=${encodeURIComponent(savedEntry.source_task_id)}` : "/knowledge"}>{savedEntry ? `查看该条目 · 版本 ${savedEntry.version}` : "查看文案库"}</Link>
    <p>同一原任务以同名再次保存会追加历史版本；复用的新稿仍需重新确认。</p>
    {feedback ? <p role={failed ? "alert" : "status"}>{feedback}</p> : null}
  </section>;
}

export function PlanReplyDraftAction({ task, busy = false, onComplete }: { task: CreationTask; busy?: boolean; onComplete: () => Promise<void> }) {
  const [origin, setOrigin] = useState<{ conversation_id: string; turn_id: string; request_id: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void getAssistantScriptContext(task.id).then((context) => {
      if (active) setOrigin(context.reply_origin);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "无法读取回复来源。");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [task.id]);

  async function transfer() {
    if (!origin || pending || busy) return;
    setPending(true); setError(null);
    try {
      const key = `ai-video-diary.plan-reply-transfer.v1:${task.id}:${origin.request_id}`;
      const saved = window.sessionStorage.getItem(key);
      const requestId = saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(saved)
        ? saved : crypto.randomUUID();
      if (!saved) window.sessionStorage.setItem(key, requestId);
      const plan = await getCreationPlan(task.plan_id);
      await transferAssistantReplyToScript(task.id, {
        conversation_id: origin.conversation_id, turn_id: origin.turn_id,
        request_id: requestId, plan_conversion_request_id: origin.request_id,
        expected_plan_updated_at: plan.updated_at, expected_task_updated_at: task.updated_at,
      });
      await onComplete();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "回复草稿没有保存成功，请重新检查后重试。");
    } finally { setPending(false); }
  }
  if (loading || !origin) return error ? <p role="alert">{error}</p> : null;
  return <section className={styles.workSection} aria-label="已选择的首页回复">
    <h3>已选择的对话回复</h3>
    <p>保存为待确认文案草稿后，仍需逐段补充当前资料引用和画面建议；不会跳过选题确认。</p>
    <button className={styles.secondaryButton} disabled={pending || busy} onClick={() => void transfer()} type="button">{pending ? "正在核对并保存…" : "保存这条回复为文案草稿"}</button>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}

function ScriptEditor({ taskId, script, busy, repairing, onConfirm }: { taskId: string; script: CreationTaskScript; busy: boolean; repairing: boolean; onConfirm: (script: CreationTaskScript) => void }) {
  const [draft, setDraft] = useState(script);
  const [sources, setSources] = useState<{ reference_id: string; title: string; text: string }[]>([]);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void getAssistantScriptContext(taskId).then((context) => { if (active) setSources(context.sources); })
      .catch((cause) => { if (active) setDraftError(cause instanceof Error ? cause.message : "无法读取当前任务资料。"); });
    return () => { active = false; };
  }, [taskId]);
  async function saveDraft() {
    const submittedDraft = draft;
    setSavingDraft(true); setDraftError(null);
    try {
      const saved = await saveAssistantScriptDraft(taskId, {
        title: submittedDraft.title,
        sections: submittedDraft.sections,
        expected_updated_at: submittedDraft.updated_at,
      });
      setDraft((current) => current === submittedDraft
        ? saved
        : { ...current, updated_at: saved.updated_at });
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "草稿没有保存成功；当前编辑内容仍保留在本页。");
    } finally { setSavingDraft(false); }
  }
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>{repairing ? "修订文案" : "检查文案"}</h2><p>确认后使用这份正文和画面建议制作。</p></div></div>
      {script.reused_from_entry_id ? <p>从文案库复用的新稿，尚未确认；没有调用模型或复制旧成片。<Link href="/knowledge">查看文案来源</Link></p> : null}
      {script.source_reply_reference ? <p>来自已完成对话回复。当前内容是待确认草稿；来源和画面建议需要你逐段核对补充。</p> : null}
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea aria-label={`第 ${index + 1} 段正文`} rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })} /><label className={styles.field}><span>画面建议</span><textarea aria-label={`第 ${index + 1} 段画面建议`} rows={2} value={section.visual_prompt} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, visual_prompt: event.target.value } : item) })} /></label><fieldset className={styles.scriptSourceChoices}><legend>选择这段使用的当前资料</legend>{sources.length ? sources.map((source) => <label key={source.reference_id}><input type="checkbox" checked={section.evidence_reference_ids.includes(source.reference_id)} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, evidence_reference_ids: event.target.checked ? [...item.evidence_reference_ids, source.reference_id] : item.evidence_reference_ids.filter((id) => id !== source.reference_id) } : item) })} />{source.title}</label>) : <p>当前任务没有可选资料；请先补充并确认有效来源。</p>}</fieldset></article>)}</div>
      {draftError ? <p role="alert">{draftError}</p> : null}
      {script.selection_status === "draft" ? <button className={styles.secondaryButton} disabled={busy || savingDraft || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => void saveDraft()} type="button">{savingDraft ? "正在保存草稿…" : "保存草稿"}</button> : null}
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || savingDraft || !draft.title.trim() || draft.sections.some((section) => !section.content.trim() || !section.visual_prompt.trim() || !section.evidence_reference_ids.length)} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}{repairing ? "确认修订文案" : "确认文案"}</button></div>
    </section>
  );
}

const executionStageLabels: Record<string, string> = {
  narration: "生成配音",
  person_render: "人物口型",
  montage_plan: "安排镜头与人物口型",
  render: "生成成片和字幕",
  cover: "准备封面",
};

type AssistantTurnRecord = { command: string; reply: string; context: string; activity?: AssistantTurnActivity };

function assistantHistoryTurns(items: CreationTaskAssistantHistory["items"]): AssistantTurnRecord[] {
  const turns: AssistantTurnRecord[] = [];
  let pending: CreationTaskAssistantHistory["items"][number] | null = null;
  for (const item of items) {
    if (item.role === "user") {
      if (pending) turns.push({ command: pending.message, reply: "上次没有得到完整回复。", context: pending.stage });
      pending = item;
    } else {
      turns.push({ command: pending?.message ?? "", reply: item.message, context: item.stage });
      pending = null;
    }
  }
  if (pending) turns.push({ command: pending.message, reply: "上次没有得到完整回复。", context: pending.stage });
  return turns.slice(-8);
}

function CreationExecutionPanel({ workspace, preview, execution, busy, compact = false, onStart, onResume, onCancel, onOpenMontage }: {
  workspace: WorkspaceState;
  preview: CreationExecutionPreview | null;
  execution: CreationExecutionStatus | null;
  busy: boolean;
  compact?: boolean;
  onStart: () => void;
  onResume: () => void;
  onCancel: () => void;
  onOpenMontage: () => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const voice = workspace.plan.selected_voice;
  const selectedVoice = voice && preview?.requirements.selected_voice === voice.profile_id
    ? workspace.available_voices.find(item => item.profile_id === voice.profile_id && item.version === voice.version) : null;
  const voiceLabel = selectedVoice?.person_display_name || (preview?.requirements.selected_voice ? "已选择声音参考" : "尚未选择");
  const job = execution?.job;
  const needsOutput = job?.status === "succeeded" && !execution?.video_ready;
  const active = job?.status === "queued" || job?.status === "running" || job?.status === "interrupted";
  const resumable = needsOutput || job?.status === "failed" || job?.status === "cancelled" || job?.status === "interrupted";
  const statusText = job?.status === "queued" ? "已排队" : job?.status === "running" ? "后台制作中"
    : job?.status === "failed" ? "制作停在失败步骤" : job?.status === "cancelled" ? "已在安全边界停止"
      : job?.status === "interrupted" ? "应用中断，等待继续" : job?.status === "succeeded" ? execution?.video_ready ? "成片已生成，等待检查" : "成片产物需要检查" : "等待开始";
  const content = <section aria-label="连续制作" className={styles.workSection}>
    <div className={styles.operationHeading}><h2>{job ? "连续制作" : "制作要求"}</h2><span className={styles.stateBadge}>{statusText}</span></div>
    {needsOutput ? <p>沿用有效配音和故事板，重新输出当前时间线。</p> : null}
    {(!job || resumable) && preview ? <>
      <details className={styles.executionScript}><summary>这次使用的文案：{preview.script_title}<span>{preview.script_sections.length} 段</span></summary>{preview.script_sections.map((section, index) => <p key={index}>第 {index + 1} 段 · {section.title}：{section.content}</p>)}</details>
      <section className={styles.executionRequirements} aria-label="这次制作要求">
        <p>画幅 {preview.requirements.aspect_ratio} · 目标时长 {preview.requirements.duration_target_seconds} 秒 · 露脸目标 {preview.requirements.presenter_share_target}%</p>
        <p>口型：{preview.requirements.lipsync_quality === "high" ? "高精度" : "较快档"} · {preview.narration_source?.source_kind === "recorded"
          ? `配音来源：本人录音 · 已采用 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
          : preview.narration_source?.source_kind === "generated"
            ? `配音来源：已生成 · ${preview.narration_source.duration_seconds.toFixed(1)} 秒`
            : `声音：${voiceLabel}`}</p>
        {preview.requirements.selected_voice ? <details><summary>声音引用诊断</summary><p>声音身份：{preview.requirements.selected_voice}{voice?.profile_id === preview.requirements.selected_voice ? ` · 固定版本：${voice.version}` : ""}</p></details> : null}
        {Object.entries(preview.requirements.template_settings).length ? <details><summary>查看模板中的具体设置</summary><dl>{Object.entries(preview.requirements.template_settings).map(([name, value]) => <div key={name}><dt>{({ fit_mode: "画面适配", color_style: "调色", subtitle_style: "字幕样式" } as Record<string, string>)[name] ?? name}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details> : null}
      </section>
      {!preview.requirements.can_continue && preview.requirements.continuation_blocker ? <p role="status">{preview.requirements.continuation_blocker}</p> : null}
      {!job ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !preview.requirements.can_continue} onClick={onStart} type="button">{preview.requirements.can_continue ? "确认以上文案和要求，开始制作" : "暂不能开始制作"}</button></div> : null}
    </> : null}
    {job ? <div>
      {execution?.waiting_reason ? <p role="status">{creationTaskFailureReason(execution.waiting_reason, workspace.task.stage)}</p> : null}
      {!workspace.task.stage_reason && job.failure_reason ? <p>{creationTaskFailureReason(job.failure_reason, workspace.task.stage)}</p> : null}
      <details className={styles.traceDetails}><summary>步骤与后台记录</summary>
        <ExecutionQueueStatus status={job.status} currentStage={execution?.current_stage ?? null} waitingReason={execution?.waiting_reason ?? null} statusUpdatedAt={execution?.status_updated_at ?? null} />
        {job.failure_reason ? <p>原始原因：{job.failure_reason}</p> : null}{job.repair_action ? <p>后台处理原文：{job.repair_action}</p> : null}
        <ol>{execution?.stages.map((event, index) => <li key={`${event.at}-${index}`}>{executionStageLabels[event.stage] ?? event.stage}：{event.status === "running" ? "正在处理" : event.status === "completed" ? "已完成" : event.status === "reused" ? "沿用有效结果" : event.status === "failed" ? "失败" : "待完成"}{event.reason ? ` · ${event.reason}` : ""}</li>)}</ol>
      </details>
      {execution?.video_ready ? <p>成片可检查；{execution.cover_status === "ready" ? "封面已准备好。" : execution.cover_message}</p> : null}
      {needsOutput ? <><p>原成片不再对应当前时间线，请先核对以上文案和制作要求，再确认重新输出。</p><div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy} onClick={onOpenMontage} type="button">查看剪辑与已有产物</button></div></> : null}
      {active && job.status !== "interrupted" ? <button className={styles.secondaryButton} disabled={busy} onClick={onCancel} type="button">停止制作</button> : null}
      {resumable ? <button className="modern-primary-button" disabled={busy || !preview?.requirements.can_continue} onClick={onResume} type="button">{needsOutput ? "确认以上文案和要求，仅重新输出当前剪辑" : "确认以上文案和要求，继续制作"}</button> : null}
    </div> : null}
  </section>;
  if (!compact) return content;
  return <>
    <section aria-label="制作状态" className={editorUi.productionSummary}>
      <div>{job && job.status !== "succeeded" ? <strong>连续制作 · {statusText}{execution?.current_stage ? ` · ${executionStageLabels[execution.current_stage]}` : ""}</strong> : !job ? <strong>制作要求</strong> : null}
        {execution?.waiting_reason ? <p>{creationTaskFailureReason(execution.waiting_reason, workspace.task.stage)}</p> : null}
        {job?.failure_reason ? <p>{creationTaskFailureReason(job.failure_reason, workspace.task.stage)}</p> : null}
        {needsOutput ? <p>原成片不再对应当前时间线，请核对文案和要求后重新输出。</p> : null}
      </div>
      <button className={editorUi.button} type="button" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>{job ? "查看制作详情" : "核对制作要求"}</button>
      {active && job?.status !== "interrupted" && !detailsOpen ? <button className={editorUi.button} type="button" disabled={busy} onClick={onCancel}>停止制作</button> : null}
    </section>
    <HomeCardDialog kind="detail" title="制作文案与要求" open={detailsOpen} onClose={() => setDetailsOpen(false)} footer={<button type="button" onClick={() => setDetailsOpen(false)}>收起制作详情</button>}>
      <div className={styles.productionDetails}>{content}</div>
    </HomeCardDialog>
  </>;
}

export function PreviewVideo({ url, label, muted, pauseOthers }: {
  url: string;
  label: string;
  muted: boolean;
  pauseOthers: (current: HTMLVideoElement) => void;
}) {
  const frameRef = useRef<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const previousUrlRef = useRef(url);
  const match = /#t=(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)$/.exec(url);
  const range = match ? { start: Number(match[1]), end: Number(match[2]) } : null;
  useEffect(() => {
    if (previousUrlRef.current === url) return;
    previousUrlRef.current = url;
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (videoRef.current) {
      videoRef.current.pause();
      if (range) videoRef.current.currentTime = range.start;
    }
  }, [url, range]);
  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);
  const stopAtEnd = (video: HTMLVideoElement) => {
    if (!range) return;
    if (video.currentTime < range.start) video.currentTime = range.start;
    if (video.currentTime >= range.end) {
      video.pause();
      video.currentTime = range.end;
    }
  };
  const watchPlayback = (video: HTMLVideoElement) => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    const tick = () => {
      frameRef.current = null;
      stopAtEnd(video);
      if (!video.paused) frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
  };
  return <video
    ref={videoRef}
    className={editorUi.effectPreview}
    aria-label={label}
    controls
    muted={muted}
    playsInline
    preload="metadata"
    src={apiUrl(url)}
    onLoadedMetadata={event => { if (range) event.currentTarget.currentTime = range.start; }}
    onPlay={event => {
      pauseOthers(event.currentTarget);
      if (range && (event.currentTarget.currentTime < range.start || event.currentTarget.currentTime >= range.end)) {
        event.currentTarget.currentTime = range.start;
      }
      if (range) watchPlayback(event.currentTarget);
    }}
    onTimeUpdate={event => stopAtEnd(event.currentTarget)}
    onPause={event => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      stopAtEnd(event.currentTarget);
    }}
  />;
}

function EditPreviewCard({ preview, busy, compact = false, command, onCancel, onConfirm, kind, chosen = null, onChosen, onSelectionPreview }: {
  preview: CreationTaskEditPreviewResponse; busy: BusyAction | null; compact?: boolean;
  command: string; onCancel: () => void; onConfirm: () => void; kind: "modal" | "config" | "confirmation";
  chosen?: number[] | null; onChosen?: (indices: number[]) => void; onSelectionPreview?: (indices: number[]) => void;
}) {
  const ui = editorUi;
  const previewRef = useRef<HTMLDivElement>(null);
  const steps = preview.proposal.operation.operation_type === "EditCompoundTimeline" && preview.proposal.operation.payload.selection_enabled
    ? preview.proposal.operation.payload.steps as { index: number; command: string; enabled: boolean }[] : [];
  const originalSelection = steps.filter(step => step.enabled).map(step => step.index);
  const selectedSteps = chosen ?? originalSelection;
  const commandChanged = steps.length > 0 && command.trim() !== preview.intent.raw_command.trim();
  const selectionChanged = selectedSteps.length !== originalSelection.length || selectedSteps.some(index => !originalSelection.includes(index));
  return (
        <HomeCardDialog kind={kind} title="修改提案" onClose={onCancel} footer={
          <div className={ui.previewActions}>
            <button data-card-cancel className={compact ? editorUi.button : styles.secondaryButton} disabled={busy !== null} onClick={onCancel} type="button">{kind === "modal" ? "取消" : "返回详情"}</button>
            {selectionChanged ? <button className={compact ? editorUi.primary : "modern-primary-button"} type="button" disabled={busy !== null || commandChanged || !selectedSteps.length} onClick={() => onSelectionPreview?.(selectedSteps)}>重看所选</button> : null}
            <button className={(selectionChanged || commandChanged) ? (compact ? editorUi.button : styles.secondaryButton) : (compact ? editorUi.primary : "modern-primary-button")} disabled={busy !== null || selectionChanged || commandChanged || preview.proposal.status !== "proposed"} onClick={onConfirm} type="button">
              {busy === "edit-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}{preview.proposal.operation.operation_type.startsWith("Delete") || preview.proposal.operation.payload.overlay_action === "delete" || (preview.proposal.operation.operation_type === "EditAudioTimeline" && (preview.proposal.operation.payload.edit as AudioEditInput)?.action === "delete") ? "确认删除" : "应用到草稿"}
            </button>
          </div>
        }><div ref={previewRef} aria-label="变更预览" aria-live="polite" className={editorUi.dialogPreview}>
          <header><strong>{preview.proposal.summary}</strong><span>{(selectionChanged||commandChanged)?"重看效果":"待确认"}</span></header>
          {steps.length?<fieldset className={editorUi.propertySection}><legend>选择修改</legend>{steps.map(step=><label className={editorUi.actionRow} key={step.index}><input type="checkbox" aria-label={`保留修改${step.index+1}：${step.command}`} disabled={busy!==null||commandChanged} checked={selectedSteps.includes(step.index)} onChange={event=>onChosen?.(event.target.checked?[...selectedSteps,step.index]:selectedSteps.filter(index=>index!==step.index))}/><span>{step.index+1}. {step.command}</span></label>)}</fieldset>:null}
          {(selectionChanged||commandChanged)?<p role="status">内容已变，请重看效果。</p>:<div className={ui.previewDiff}>
            <section><small>修改前</small><strong>{preview.proposal.preview.before.label}</strong><span>{preview.proposal.preview.before.detail}</span>{preview.proposal.preview.before.image_url ? <a href={apiUrl(preview.proposal.preview.before.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改前字幕样式"><NextImage alt="修改前字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.before.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.before.video_url ? <PreviewVideo url={preview.proposal.preview.before.video_url} label="修改前画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { previewRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current) media.pause(); }); }} /> : null}{preview.proposal.preview.before.audio_url ? <audio aria-label="修改前音频" controls onPlay={event => { previewRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.before.audio_url)} /> : null}</section>
            <section><small>修改后</small><strong>{preview.proposal.preview.after.label}</strong><span>{preview.proposal.preview.after.detail}</span>{preview.proposal.preview.after.image_url ? <a href={apiUrl(preview.proposal.preview.after.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改后字幕样式"><NextImage alt="修改后字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.after.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.after.video_url ? <PreviewVideo url={preview.proposal.preview.after.video_url} label="修改后画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { previewRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current) media.pause(); }); }} /> : null}{preview.proposal.preview.after.audio_url ? <audio aria-label="修改后音频" controls onPlay={event => { previewRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.after.audio_url)} /> : null}</section>
          </div>}
          {preview.proposal.preview.text_changes?.length ? <details className={editorUi.textChanges} open><summary>字幕变化 · {preview.proposal.preview.text_changes.length}</summary><ol>{preview.proposal.preview.text_changes.map(change => <li key={change.cue_id}><span>修改前：{change.before}</span><span>修改后：{change.after}</span></li>)}</ol></details> : null}
          <div className={ui.previewEffects}>
            <span><strong>需要重做</strong>{preview.proposal.invalidated_refs.map(ref => compact ? ref.endsWith(".mp4") ? "当前成片" : ref.endsWith(".srt") ? "字幕输出" : ref : ref).join("、") || "无"}</span>
            <span><strong>其他影响</strong>{preview.proposal.external_effects.join("；") || "无"}</span>
          </div>
        </div></HomeCardDialog>
  );
}

function ContextCommandBar({
  localConfirmation = false,
  compact = false,
  scriptMode = false,
  busy,
  command,
  onCancel,
  onCommandChange,
  onConfirm,
  onExactPreview,
  onSelectionPreview,
  preview,
  assistantState,
  onTransferReply,
  onOpenEditProposal,
  transferDraftState,
  onAssistantSend,
  onConversationChange,
  previewCommand,
  assistantTurns,
  assistantConfigured,
  selectedContextLabel,
  materialContextLabel,
  onClearReference,
  selectedContextPlaceholder,
}: {
  localConfirmation?: boolean;
  compact?: boolean;
  scriptMode?: boolean;
  busy: BusyAction | null;
  command: string;
  onCancel: () => void;
  onCommandChange: (value: string) => void;
  onConfirm: () => void;
  onExactPreview: () => void;
  onSelectionPreview: (indices:number[])=>void;
  preview: CreationTaskEditPreviewResponse | null;
  assistantState: ReturnType<typeof useAssistantConversation>;
  onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
  onOpenEditProposal?: (turn: AssistantConversationTurn) => void;
  transferDraftState: "none" | "draft" | "confirmed";
  onAssistantSend: () => Promise<void>;
  onConversationChange: () => void;
  previewCommand: string;
  assistantTurns: { command: string; reply: string; context: string; activity?: AssistantTurnActivity }[];
  assistantConfigured: boolean;
  selectedContextLabel: string | null;
  materialContextLabel: string | null;
  onClearReference: () => void;
  selectedContextPlaceholder: string;
}) {
  const barRef = useRef<HTMLElement>(null);
  const [chosen,setChosen]=useState<number[]|null>(null);
  useEffect(() => {
    if (preview && !compact) barRef.current?.scrollIntoView?.({ block: "end" });
  }, [preview, compact]);
  useEffect(() => { setChosen(null); }, [preview?.proposal.id]);
  return (
    <section
      aria-label={scriptMode ? "文案对话" : "修改视频"}
      className={editorUi.chatCommand}
      ref={barRef}
    >
      {selectedContextLabel ? <p className="home-composer__context">引用：{selectedContextLabel}<button type="button" aria-label="取消任务对象引用" onClick={onClearReference}>取消引用</button></p> : null}
      <AssistantConversationPanel
        busy={busy !== null}
        compact={compact}
        configured={assistantConfigured}
        onTransferReply={onTransferReply}
        onOpenEditProposal={onOpenEditProposal}
        transferDraftState={transferDraftState}
        input={assistantState.draftText}
        inputLabel={scriptMode ? "文案要求" : "局部修改要求"}
        onInput={onCommandChange}
        onSend={async () => onAssistantSend()}
        onConversationChange={onConversationChange}
        placeholder={selectedContextPlaceholder}
        scopeLabel={materialContextLabel ? `当前素材 · ${materialContextLabel}` : scriptMode ? "当前任务 · 文案" : selectedContextLabel ? `当前任务 · ${selectedContextLabel}` : "当前任务"}
        state={assistantState}
        legacyHistory={assistantTurns.length ? <details className="assistant-history"><summary>旧版只读历史</summary><ol>{assistantTurns.map((turn, index) => <li key={index}><small>{turn.context}</small><p>{turn.command}</p><p>{turn.reply}</p></li>)}</ol></details> : undefined}
      />
      {selectedContextLabel && !scriptMode ? <button className={editorUi.composerPreview} disabled={busy !== null || !command.trim()} onClick={onExactPreview} type="button">精确预览修改</button> : null}
      {preview && !localConfirmation ? <EditPreviewCard key={preview.proposal.id} preview={preview} busy={busy} compact={compact} command={previewCommand} onCancel={onCancel} onConfirm={onConfirm} kind="modal" chosen={chosen} onChosen={setChosen} onSelectionPreview={onSelectionPreview} /> : null}
    </section>
  );
}

function materialAnalysisLabel(status: MaterialAsset["analysis_status"]) {
  if (status === "ready") return "已解析";
  if (status === "pending") return "待解析";
  if (status === "blocked") return "未解析";
  return "解析失败";
}

function materialUseLabel(status: MaterialAsset["use_state"]) {
  return { available: "可用于创作", reference_only: "仅参考", excluded: "不使用",
    not_for_automation: "不用于自动任务", not_for_publish: "不用于发布" }[status];
}

const PREPARE_MATERIALS_PAGE_SIZE = 100;

function PrepareMaterialsView({ workspace }: { workspace: WorkspaceState }) {
  const [materials, setMaterials] = useState<MaterialAsset[] | null>(null);
  const [previewAsset, setPreviewAsset] = useState<MaterialAsset | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const activeRef = useRef(true);
  const loadingRef = useRef(false);
  const loadPage = useCallback(async (offset: number, replace: boolean) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setLoadError(null);
    try {
      const library = await getProductionMaterials({ limit: PREPARE_MATERIALS_PAGE_SIZE, offset });
      if (!activeRef.current) return;
      setMaterials((current) => {
        const assets = replace ? [] : current ?? [];
        const byId = new Map(assets.map((asset) => [asset.id, asset]));
        for (const asset of library.assets) byId.set(asset.id, asset);
        return [...byId.values()];
      });
      setHasMore(library.has_more);
      setTotalCount(library.total_count);
      setNextOffset(library.offset + library.limit);
    } catch (cause: unknown) {
      if (!activeRef.current) return;
      setLoadError(cause instanceof Error ? cause.message : "素材读取失败");
    } finally {
      loadingRef.current = false;
      if (activeRef.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    activeRef.current = true;
    void loadPage(0, true);
    return () => { activeRef.current = false; };
  }, [loadPage]);
  const matchesByAsset = useMemo(() => {
    const map = new Map<string, MaterialSegmentMatch[]>();
    for (const match of workspace.plan.material_matches) {
      const id = match.asset_ref.startsWith("asset:") ? match.asset_ref.slice("asset:".length) : match.asset_ref;
      map.set(id, [...(map.get(id) ?? []), match]);
    }
    return map;
  }, [workspace.plan.material_matches]);
  const matchedAssets = useMemo(
    () => (materials ?? []).filter((asset) => matchesByAsset.has(asset.id)),
    [materials, matchesByAsset],
  );
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}>
        <div>
          <h2>本次任务素材</h2>
          <p>回看方案检索候选与当前使用范围；候选不代表已采用镜头。补充或调整素材后需重新检查真实条件。</p>
        </div>
        <span className={styles.artifactStatus}><CheckCircle2 size={15} />{workspace.plan.material_matches.length ? `方案已记录 ${workspace.plan.material_matches.length} 个检索候选` : "方案尚无匹配片段"}</span>
      </div>
      <div className={styles.artifactGrid}>
        <section><strong>检索候选</strong><p>{workspace.plan.material_matches.length} 个片段，需核对画面内容及本次用途。</p></section>
        <section><strong>方案可用内容</strong><p>{workspace.plan.approved_asset_count} 个素材 · {workspace.plan.approved_clip_count} 个补充镜头</p><small>这是创建方案时的可用数量；当前限制见下方素材状态。</small></section>
      </div>
      {loadError ? <div className={styles.blocker} role="alert"><AlertCircle size={17} /><span>素材列表读取失败：{loadError}；这不改变方案匹配或资料就绪状态。</span></div> : null}
      <div className={styles.evidenceList}>
        <strong>本次素材列表</strong>
        {loading && materials === null ? <p>正在读取素材…</p> : null}
        {materials !== null && matchedAssets.length === 0 && hasMore ? <p>当前已读 {materials.length} 条，尚未找到本方案匹配素材；可继续读取下一页。</p> : null}
        {materials !== null && matchedAssets.length === 0 && !hasMore ? <p>已读完素材列表；本方案暂无匹配素材，或素材解析状态缺失。</p> : null}
        {matchedAssets.map((asset) => {
          const matches = matchesByAsset.get(asset.id) ?? [];
          const tags = asset.user_tags ?? [];
          return (
            <article key={asset.id}>
              <span>{materialUseLabel(asset.use_state)}</span>
              <div>
                <strong>{asset.file_name}</strong>
                <small>{materialAnalysisLabel(asset.analysis_status)}{asset.duration_seconds !== null ? ` · ${asset.duration_seconds.toFixed(1)} 秒` : " · 时长未解析"}</small>
                <small>{tags.length ? tags.join(" · ") : "暂无标签"}{asset.analysis_message ? ` · ${asset.analysis_message}` : ""}</small>
                {matches.map(match => <p key={match.segment_ref}>{match.reasons.join("；") || "结构候选，需查看内容后判断"}</p>)}
                <button className={styles.secondaryButton} type="button" onClick={() => setPreviewAsset(asset)}>查看候选素材</button>
                <details><summary>候选诊断</summary>{matches.map(match => <p key={match.segment_ref}>{match.segment_ref} · 启发分值 {match.score}，用于排序，不是适配概率</p>)}</details>
              </div>
            </article>
          );
        })}
        {hasMore || loadError ? (
          <button type="button" disabled={loading} onClick={() => void loadPage(nextOffset, false)}>
            {loading ? "正在读取…" : loadError && nextOffset > 0 ? "重试读取下一页" : loadError ? "重试读取素材" : `读取更多素材（已读 ${materials?.length ?? 0}${totalCount === null ? "" : ` / ${totalCount}`}）`}
          </button>
        ) : null}
      </div>
      {previewAsset ? <HomeCardDialog kind="detail" title={previewAsset.file_name} onClose={() => setPreviewAsset(null)}>
        <p>{materialUseLabel(previewAsset.use_state)} · {materialAnalysisLabel(previewAsset.analysis_status)}</p>
        <p>原始素材预览；是否可采用仍由当前任务的真实媒体和使用条件检查决定。</p>
        {previewAsset.media_type === "image" ? <NextImage alt={previewAsset.file_name} src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)} width={720} height={1280} unoptimized className={styles.materialPreview} /> : previewAsset.media_type === "audio" ? <audio aria-label="候选素材预览" controls src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)} /> : <video aria-label="候选素材预览" className={styles.materialPreview} controls preload="metadata" src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)} />}
        <Link href="/library">在素材库检查来源与使用范围</Link>
      </HomeCardDialog> : null}
    </section>
  );
}

function PublishStageView({ workspace, onOpenReview }: { workspace: WorkspaceState; onOpenReview: () => void }) {
  const ready = Boolean(workspace.montage?.output_url);
  return <section className={styles.workSection}>
    <div className={styles.sectionHeading}><div><h2>核对发布目标</h2><p>账号、平台能力、成片版本和发布确认在发布工作区核对。本任务页不显示平台结果或发布数据。</p></div></div>
    <p>{ready ? "已有成片；进入发布工作区后选择本任务，重新核对当前版本与账号。" : "本任务还没有可用成片；发布工作区会保留未就绪条件，不能据此称为已发布。"}</p>
    <div className={styles.primaryAction}><Link className="modern-primary-button" href="/history">打开发布工作区</Link>{ready ? <button className={styles.secondaryButton} onClick={onOpenReview} type="button">回到检查与输出</button> : null}</div>
  </section>;
}

export function FinalVideoQualityCheck({ taskId, versionId, outputUrl, onLocate }: {
  taskId: string; versionId: string | null; outputUrl: string | null;
  onLocate: (finding: CreationTaskQualityCheck["findings"][number]) => void;
}) {
  const [result, setResult] = useState<CreationTaskQualityCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const revision = useRef(0);
  useEffect(() => {
    const current = ++revision.current;
    setResult(null); setError(null); setSubmitting(false);
    void getCreationTaskQualityCheck(taskId).then(value => {
      if (current === revision.current) setResult(value);
    }).catch(cause => { if (current === revision.current) setError(errorDetails(cause).message); });
    return () => { revision.current += 1; };
  }, [taskId, versionId, outputUrl]);
  useEffect(() => {
    if (result?.check_state !== "checking" || result.stale) return;
    const current = revision.current;
    const timer = window.setTimeout(() => {
      void getCreationTaskQualityCheck(taskId).then(value => {
        if (current === revision.current) setResult(value);
      }).catch(cause => { if (current === revision.current) setError(errorDetails(cause).message); });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [result, taskId]);
  async function check() {
    const current = revision.current;
    setSubmitting(true); setError(null);
    try {
      const next = await checkCreationTaskQuality(taskId);
      if (current === revision.current) setResult(next);
    } catch (cause) {
      if (current === revision.current) setError(errorDetails(cause).message);
    } finally {
      if (current === revision.current) setSubmitting(false);
    }
  }
  const stale = result && (result.stale || result.timeline_version_id !== versionId
    || (result.check_state === "done" && !outputUrl));
  const current = !error && !stale ? result : null;
  const label = error ? "检查结果读取失败" : stale ? "旧检查已失效，请检查当前成片"
    : !current ? "正在读取检查结果" : current.check_state === "checking" ? "成片检查中"
    : current.check_state === "failed" ? "成片检查失败"
    : current.result_status === "issues_found" ? "成片发现问题"
    : current.result_status === "unavailable" ? "部分检测不可用"
    : current.result_status === "clean" ? "未发现确定性媒体问题" : "当前成片尚未检查";
  return <section className={styles.qualityCheck} aria-label="成片质量检查">
    <div className={styles.qualityHeading}><strong role="status" aria-label="成片质量检查状态">{label}</strong>
      <button className={styles.secondaryButton} type="button"
        disabled={!outputUrl || submitting || current?.check_state === "checking"}
        onClick={() => void check()}>重新检查成片</button></div>
    <p>检查黑帧、尾部截断、音频削波和起点时间戳错位；内容质量仍需人工确认。</p>
    {error || current?.error ? <p role="alert">{error ?? current?.error}</p> : null}
    {current?.checked_at ? <small>检查时间：{new Date(current.checked_at).toLocaleString()}</small> : null}
    {current?.check_state === "done" && current.clipping_summary ? <div aria-label="音频削平摘要">
      <p>音频削平共 {current.clipping_summary.event_count} 段，累计 {current.clipping_summary.total_clipped_seconds} 秒。</p>
      {current.clipping_summary.findings_truncated ? <p>
        削平问题明细已限流至 {current.clipping_summary.findings_event_limit} 条，另有 {current.clipping_summary.findings_truncated_count} 段未逐条列出；总量包含这些事件。
      </p> : null}
      {current.clipping_summary.events_truncated ? <p>
        削平区间摘要仅保留 {current.clipping_summary.event_list_limit} 条，另有 {current.clipping_summary.truncated_event_count} 段未在区间摘要中列举；总量仍完整。
      </p> : null}
    </div> : null}
    {current?.findings.map((finding, index) => <article key={`${finding.kind}:${index}`}>
      <button type="button" onClick={() => onLocate(finding)}>
        定位 {finding.start_seconds.toFixed(2)}–{finding.end_seconds.toFixed(2)} 秒
      </button><span>{finding.detail}</span>
    </article>)}
    {current?.unavailable.map((item, index) => <p key={`${item.kind}:${index}`}>
      检测不可用：{item.reason}</p>)}
  </section>;
}

export function CreationTaskWorkspace({ initialWorkspace, initialTopics, initialTasks }: Props) {
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [activeView, setActiveView] = useState<WorkspaceView>(() => defaultWorkspaceView(initialWorkspace));
  const [reviewActionsOpen, setReviewActionsOpen] = useState(false);
  const reviewActionsTrigger = useRef<HTMLButtonElement>(null);
  const [reviewChoice, setReviewChoice] = useState<{ taskId: string; pane: "prepare" | "result" } | null>(null);
  const [topics, setTopics] = useState(initialTopics);
  const [selectedTopicId, setSelectedTopicId] = useState(initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id ?? null);
  const [topicTitleDraft, setTopicTitleDraft] = useState(
    () => initialTopics?.candidates.find((candidate) => candidate.id === (initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id))?.title ?? "",
  );
  const [tasks, setTasks] = useState(initialTasks);
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const currentBusy = useRef(busy);
  currentBusy.current = busy;
  const [error, setError] = useState<ReturnType<typeof errorDetails> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingLocalEdit, setPendingLocalEdit] = useState<PendingLocalEdit | null>(null);
  const pendingLocalEditRef = useRef(pendingLocalEdit);
  pendingLocalEditRef.current = pendingLocalEdit;
  const [pendingMediaJob, setPendingMediaJob] = useState<{ taskId: string; value: TaskMediaJob } | null>(null);
  const [recoveredMedia, setRecoveredMedia] = useState<{ taskId: string; job: TaskMediaJob; result: TaskMediaResultMap[keyof TaskMediaResultMap] } | null>(null);
  const pendingMediaJobRef = useRef(pendingMediaJob);
  pendingMediaJobRef.current = pendingMediaJob;
  const mediaController = useRef<AbortController | null>(null);
  useEffect(() => () => mediaController.current?.abort(), []);
  const currentTaskId = useRef(initialWorkspace.task.id);
  currentTaskId.current = workspace.task.id;
  const currentWorkspace = useRef(workspace);
  currentWorkspace.current = workspace;
  const navigationReadRevision = useRef(0);
  const pendingNavigationRead = useRef<{ taskId: string; inputRevision: number } | null>({ taskId: initialWorkspace.task.id, inputRevision: 0 });
  // 共享/收回只用接口回读的值：保存失败就保持原样，不显示成已保存。
  async function changeTaskVisibility(next: "private" | "shared") {
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      const saved = await setCreationTaskVisibility(task.id, next);
      setWorkspace((current) => ({ ...current, task: saved.task }));
    } catch (err) {
      setVisibilityError(err instanceof Error ? err.message : "共享设置没有保存成功，请再试一次。");
    } finally {
      setVisibilityBusy(false);
    }
  }
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(
    initialWorkspace.plan.selected_voice?.profile_id
      ?? initialWorkspace.available_voices.find((voice) => voice.runnable)?.profile_id
      ?? null,
  );
  const [replacementClipId, setReplacementClipId] = useState<string | null>(null);
  const [replacementCandidates, setReplacementCandidates] = useState<MontageClipCandidate[]>([]);
  const [inserting, setInserting] = useState(false);
  const [editorDraft, setEditorDraft] = useState(false);
  const currentEditorDraft = useRef(editorDraft);
  currentEditorDraft.current = editorDraft;
  const [clipDrafts, setClipDrafts] = useState<Record<string, VideoClipDraft>>({});
  const [audioPlayhead, setAudioPlayhead] = useState(0);
  const [selectedAudioId, setSelectedAudioId] = useState<string | null>(null);
  const [selectedOverlayId, setSelectedOverlayId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const montageVideoRef = useRef<HTMLVideoElement>(null);
  const playheadRef = useRef<number | null>(null);
  const editContextRevision = useRef(0);
  const previewRequestSequence = useRef(0);
  const assistantRequestSequence = useRef(0);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const [selectedCover, setSelectedCover] = useState(false);
  const [activeSubtitleTrackId, setActiveSubtitleTrackId] = useState<string | null>(
    initialWorkspace.subtitle_tracks.find((track) => track.language === "zh-CN")?.id
      ?? initialWorkspace.subtitle_tracks[0]?.id
      ?? null,
  );
  const [selectedNarrationSegmentId, setSelectedNarrationSegmentId] = useState<string | null>(null);
  const [editPreview, setEditPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const currentEditPreview = useRef(editPreview);
  currentEditPreview.current = editPreview;
  const [assistantPreview, setAssistantPreview] = useState(false);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const [recordings, setRecordings] = useState<CreationTaskRecording[]>([]);
  const [recordingsError, setRecordingsError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [assistantTurns, setAssistantTurns] = useState<AssistantTurnRecord[]>([]);
  const [assistantConfigured, setAssistantConfigured] = useState(false);
  const [executionPreview, setExecutionPreview] = useState<CreationExecutionPreview | null>(null);
  const [execution, setExecution] = useState<CreationExecutionStatus | null>(null);
  const [executionError, setExecutionError] = useState<ReturnType<typeof errorDetails> | null>(null);
  const executionSnapshot = useRef("");
  const pollExecutionRef = useRef<() => void>(() => undefined);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setAssistantConfigured(true);
      }
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    let active = true;
    if (!workspace.task.confirmed_script_id) {
      setRecordings([]);
      setRecordingsError(null);
      return () => { active = false; };
    }
    void listCreationTaskRecordings(workspace.task.id).then(({ items }) => {
      if (active) {
        setRecordings(items);
        setRecordingsError(null);
      }
    }).catch((cause) => {
      if (active) setRecordingsError(cause instanceof Error ? cause.message : "无法读取本任务已保存的录音。");
    });
    return () => { active = false; };
  }, [workspace.task.id, workspace.task.confirmed_script_id, workspace.task.stage]);
  const task = workspace.task;
  const assistantConversation = useAssistantConversation({ scope_kind: "task", scope_id: task.id });
  const selectedProfileReference = useAssistantProfileReference()?.reference;
  const materialReference = useAssistantMaterialReference();
  const selectedMaterialReference = materialReference?.reference;
  const selectedKnowledgeReference = useAssistantKnowledgeReference()?.reference;
  const replyTransferRequests = useRef(new Map<string, string>());
  const editCommand = assistantConversation.draftText;
  const setEditCommand = assistantConversation.setDraftText;
  const [detachedReference, setDetachedReference] = useState<string | null>(null);
  const [sharingOpen, setSharingOpen] = useState(false);
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<string | null>(null);
  // 方案可见范围回读值：接口返回什么就显示什么。
  const [planVisibility, setPlanVisibility] = useState<"private" | "shared" | null>(null);

  // 方案可见范围以方案自身的读取结果为准；读不到就显示未知，不假装是私有。
  useEffect(() => {
    let cancelled = false;
    void getCreationPlan(task.plan_id)
      .then((plan) => { if (!cancelled) setPlanVisibility(plan.visibility); })
      .catch(() => { if (!cancelled) setPlanVisibility(null); });
    return () => { cancelled = true; };
  }, [task.plan_id]);

  async function changePlanVisibility(next: "private" | "shared") {
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      const saved = await setCreationPlanVisibility(task.plan_id, next);
      setPlanVisibility(saved.visibility);
    } catch (err) {
      setVisibilityError(err instanceof Error ? err.message : "方案共享设置没有保存成功，请再试一次。");
    } finally {
      setVisibilityBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    setAssistantTurns([]);
    void getCreationTaskAssistantHistory(task.id).then((history) => {
      if (!active) return;
      const turns = assistantHistoryTurns(history.items);
      setAssistantTurns(turns);
    }).catch((cause) => { if (active) setError(errorDetails(cause)); });
    return () => { active = false; };
  }, [task.id]);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    executionSnapshot.current = "";
    setExecution(null);
    setExecutionPreview(null);
    setExecutionError(null);
    if (task.confirmed_script_id) {
      void previewCreationExecution(task.id).then((preview) => {
        if (active) setExecutionPreview(preview);
      }).catch((cause) => { if (active) setExecutionError(errorDetails(cause)); });
    }
    async function poll() {
      window.clearTimeout(timer);
      let nextDelay = 15000;
      try {
        const current = await getCreationExecution(task.id);
        if (!active) return;
        setExecution(current);
        if (current.job?.status === "queued" || current.job?.status === "running") nextDelay = 2500;
        const snapshot = `${current.job?.id ?? "none"}:${current.job?.status ?? "none"}:${current.stages.length}`;
        if (current.job && snapshot !== executionSnapshot.current) {
          executionSnapshot.current = snapshot;
          const next = await getCreationTaskWorkspace(task.id);
          if (!active) return;
          setWorkspace(next);
          setTasks((items) => [next.task, ...items.filter((item) => item.id !== next.task.id)]);
        }
      } catch (cause) {
        if (active) setExecutionError(errorDetails(cause));
      }
      if (active) timer = window.setTimeout(() => void poll(), nextDelay);
    }
    pollExecutionRef.current = () => void poll();
    void poll();
    return () => { active = false; pollExecutionRef.current = () => undefined; window.clearTimeout(timer); };
  }, [task.id, task.confirmed_script_id, task.current_timeline_version_id, workspace.plan.selected_voice?.profile_id, workspace.plan.selected_voice?.version]);
  const latestScript = workspace.scripts[0] ?? null;
  const runnableVoices = workspace.available_voices.filter((voice) => voice.runnable);
  const selectedTopicCandidate = topics?.candidates.find((candidate) => candidate.id === selectedTopicId) ?? null;
  const zhSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "zh-CN" && track.status === "valid");
  const enSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "en-US" && track.status === "valid");
  const coverValid = workspace.cover_asset?.status === "valid";
  const zhSubtitleVisible = workspace.subtitle_tracks.some(track => track.language === "zh-CN" && track.status === "valid" && track.style.enabled !== false);
  const enSubtitleVisible = workspace.subtitle_tracks.some(track => track.language === "en-US" && track.status === "valid" && track.style.enabled !== false);
  const subtitleSummary = zhSubtitleVisible ? enSubtitleVisible ? "中英双语轨已就绪" : "中文轨已就绪"
    : enSubtitleVisible ? "英文轨已就绪" : zhSubtitleValid || enSubtitleValid ? "当前成片已关闭字幕" : "当前没有有效字幕轨";
  const reviewChecks = [
    { title: "字幕", ready: zhSubtitleVisible || enSubtitleVisible, detail: subtitleSummary },
    { title: "讲解与音乐音量", ready: false, detail: "需人工试听确认，当前无自动音量校验" },
    { title: "封面", ready: coverValid, detail: coverValid ? "封面已生成" : "封面尚未生成或待确认" },
  ];
  const reviewReadyCount = reviewChecks.filter((item) => item.ready).length;
  const shellTasks = useMemo(() => [task, ...tasks.filter((item) => item.id !== task.id)], [task, tasks]);
  const selectedClipIndex = workspace.montage?.clips.findIndex((clip) => clip.id === selectedClipId) ?? -1;
  const activeSubtitleTrack = workspace.subtitle_tracks.find((track) => track.id === activeSubtitleTrackId)
    ?? workspace.subtitle_tracks.find((track) => track.language === "zh-CN")
    ?? workspace.subtitle_tracks[0]
    ?? null;
  const selectedCueIndex = activeSubtitleTrack?.cues.findIndex((cue) => cue.id === selectedCueId) ?? -1;
  const selectedSubtitleCue = selectedCueIndex >= 0 ? activeSubtitleTrack?.cues[selectedCueIndex] ?? null : null;
  const selectedNarrationSegmentIndex = workspace.narration?.segments.findIndex((segment) => segment.id === selectedNarrationSegmentId) ?? -1;
  const selectedAudioTrack = workspace.montage?.audio_timeline?.tracks.find(track => track.id === selectedAudioId || track.clips.some(clip => clip.id === selectedAudioId));
  const selectedAudioClip = selectedAudioTrack?.clips.find(clip => clip.id === selectedAudioId) ?? null;
  const selectedOverlayIndex = workspace.montage?.graphic_overlays?.findIndex(item=>item.id===selectedOverlayId) ?? -1;
  const selectedOverlay = selectedOverlayIndex>=0 ? workspace.montage!.graphic_overlays![selectedOverlayIndex] : null;
  const selectedEditContext = activeView === "montage" && selectedOverlay
    ? {kind:"overlay" as const,id:selectedOverlay.id,label:`${selectedOverlay.kind==="image"?"图片":selectedOverlay.kind==="arrow"?"箭头":"标题"}${selectedOverlayIndex+1}`,
       placeholder:selectedOverlay.kind==="image"?"例如：当前图片不透明度改为50%":selectedOverlay.kind==="arrow"?"例如：当前箭头颜色改为红":"例如：当前标题字号改为96",ref:`overlay:${selectedOverlay.id}`}
    : activeView === "montage" && selectedAudioTrack
    ? { kind: "audio" as const, id: selectedAudioId!, label: selectedAudioClip?.name ?? selectedAudioTrack.name,
        placeholder: "例如：音量小一点，或指定淡出秒数", ref: `audio:${selectedAudioId}` }
    : activeView === "narration" && selectedNarrationSegmentIndex >= 0
    ? {
        kind: "narration" as const,
        id: selectedNarrationSegmentId!,
        label: `配音 ${selectedNarrationSegmentIndex + 1}`,
        placeholder: "例如：这句读慢一点",
        ref: `narration:${selectedNarrationSegmentId}`,
      }
    : activeView === "subtitle_cover" && selectedCueIndex >= 0
      ? {
          kind: "cue" as const,
          id: selectedCueId!,
          label: `字幕 ${selectedCueIndex + 1}`,
          placeholder: "例如：把这句字幕改得更简洁",
          ref: `cue:${selectedCueId}`,
        }
      : activeView === "subtitle_cover" && selectedCover
        ? {
            kind: "cover" as const,
            id: workspace.cover_asset?.id ?? task.id,
            label: "封面",
            placeholder: "例如：用第三个镜头做封面，标题写“别只讲概念”",
            ref: `cover:${workspace.cover_asset?.id ?? task.id}`,
          }
      : (activeView === "montage" || activeView === "review") && selectedClipIndex >= 0
        ? {
            kind: "clip" as const,
            id: selectedClipId!,
            label: `镜头 ${selectedClipIndex + 1}`,
            placeholder: "例如：换成另一个镜头",
            ref: `clip:${selectedClipId}`,
          }
        : null;

  const selectedTaskReference = selectedEditContext && selectedEditContext.ref !== detachedReference ? selectedEditContext : null;
  useEffect(() => {
    // A material reference restored after the editor selection must not leave two
    // active targets. The provider owns its existing CAS and late-read protection.
    if (selectedTaskReference && selectedMaterialReference) materialReference?.setReference(null);
  }, [selectedTaskReference?.ref, selectedMaterialReference, materialReference?.setReference]);

  function invalidateEditPreview() {
    mediaController.current?.abort();
    mediaController.current = null;
    setPendingMediaJob(null);
    editContextRevision.current += 1;
    setEditPreview(null);
    setAssistantPreview(false);
  }

  async function resolveEditMedia(response: CreationTaskEditPreviewResponse | TaskMediaJob, revision: number, conversationId?: string) {
    const baseVersionId = task.current_timeline_version_id;
    if (currentTaskId.current !== task.id || currentWorkspace.current.task.current_timeline_version_id !== baseVersionId
      || editContextRevision.current !== revision || (conversationId && !assistantConversation.isCurrentConversation(conversationId))) {
      throw new DOMException("预览范围已变化", "AbortError");
    }
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === revision
      && (!conversationId || assistantConversation.isCurrentConversation(conversationId));
    return resolveCreationTaskMediaResponse(task.id, response, "edit_preview", {
      signal: controller.signal, baseVersionId,
      onUpdate: value => { if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
    });
  }

  async function cancelMediaPreview() {
    const pending = pendingMediaJob;
    if (!pending || pending.taskId !== currentTaskId.current) return;
    const controller = mediaController.current;
    try {
      const result = await cancelCreationTaskMediaJob(pending.taskId, pending.value.job_id);
      if (currentTaskId.current !== pending.taskId || pendingMediaJobRef.current?.value.job_id !== pending.value.job_id || mediaController.current !== controller) return;
      if (result.status === "cancelled") {
        mediaController.current?.abort(); setPendingMediaJob({ taskId: pending.taskId, value: result });
        setNotice("预览已取消，当前草稿保持不变。");
      }
    } catch (cause) { if (currentTaskId.current === pending.taskId) setError(errorDetails(cause)); }
  }

  async function restoreMediaPreview(candidate: TaskMediaJob) {
    const selectedRefs = selectedTaskReference ? [selectedTaskReference.ref,
      ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])] : [];
    if (busy !== null || !taskMediaJobMatchesContext(candidate, task.current_timeline_version_id, selectedRefs)) return;
    const revision = ++editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const baseVersionId = task.current_timeline_version_id;
    const conversationId = assistantConversation.conversation?.id;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === revision
      && (!conversationId || assistantConversation.isCurrentConversation(conversationId));
    setBusy("edit-preview"); setError(null); setNotice(null);
    try {
      const response = await getCreationTaskMediaJob(task.id, candidate.job_id, controller.signal);
      if (!stillCurrent()) return;
      if (!taskMediaJobMatchesContext(response, baseVersionId, selectedRefs)) {
        setNotice("这次预览已不符合当前输入与选择，未恢复；请重新预览。");
        return;
      }
      let settledJob = response;
      const result = await resolveCreationTaskMediaResponse(task.id, response, response.result_kind, {
        signal: controller.signal, baseVersionId,
        onUpdate: value => { settledJob = value; if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
      });
      if (!stillCurrent()) return;
      if (response.result_kind === "edit_preview") {
        setEditPreview(result as CreationTaskEditPreviewResponse);
        setAssistantPreview(true);
      } else {
        setRecoveredMedia({ taskId: task.id, job: settledJob, result });
        if (response.result_kind === "recording") {
          const recording = result as CreationTaskRecording;
          setRecordings(current => [recording, ...current.filter(item => item.source_ref !== recording.source_ref)]);
          setNotice("录音结果已读取；尚未采用，当前配音与草稿保持不变。");
        }
      }
    } catch (cause) {
      if (stillCurrent() && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
    } finally {
      if (currentTaskId.current === task.id && sequence === previewRequestSequence.current) setBusy(null);
    }
  }

  async function refresh(preserveActiveView = false, stillCurrent?: (next: WorkspaceState) => boolean, retainProposal = false) {
    const contextRevision = editContextRevision.current;
    const next = await getCreationTaskWorkspace(task.id);
    if (stillCurrent && !stillCurrent(next)) return;
    const selectionChanged = contextRevision !== editContextRevision.current;
    pendingNavigationRead.current = null;
    const proposal = currentEditPreview.current?.proposal;
    const sameProposalBase = retainProposal && proposal?.status === "proposed"
      && proposal.task_id === next.task.id && proposal.workspace_id === next.task.workspace_id
      && proposal.operation.payload.base_version_id === next.task.current_timeline_version_id
      && !selectionChanged;
    if (!sameProposalBase) invalidateEditPreview();
    setReplacementClipId(null);
    setReplacementCandidates([]);
    const retainedClip = next.montage?.clips.find(clip => clip.id === selectedClipId);
    const retainedOverlay=next.montage?.graphic_overlays?.find(item=>item.id===selectedOverlayId);
    if (!selectionChanged) {
      playheadRef.current = retainedOverlay ? (retainedOverlay.start_seconds+retainedOverlay.end_seconds)/2 : selectedAudioId ? audioPlayhead : retainedClip && next.montage?.output_url
        ? (retainedClip.timeline_start_seconds + retainedClip.timeline_end_seconds) / 2 : null;
    }
    setEditorDraft(false);
    setSelectedOverlayId(current=>next.montage?.graphic_overlays?.some(item=>item.id===current)?current:null);
    setWorkspace(next);
    // 保留原视图只在该视图按既有门禁仍可呈现时成立。字幕确认会作废 output_url，
    // 使 subtitle_cover 不可呈现；此时保留它会让主区没有任何视图命中（空白且丢失撤销入口）。
    // 这里按既有可用/默认规则回落，不新增视图、不放开任何门禁。
    setActiveView(current => preserveActiveView && canOpenStage(current, next) ? current : defaultWorkspaceView(next));
    setSelectedClipId((current) => next.montage?.clips.some((clip) => clip.id === current) ? current : null);
    setSelectedAudioId(current => next.montage?.audio_timeline?.tracks.some(track => track.id === current || track.clips.some(clip => clip.id === current)) ? current : null);
    setActiveSubtitleTrackId((current) => next.subtitle_tracks.some((track) => track.id === current)
      ? current
      : next.subtitle_tracks.find((track) => track.language === "zh-CN")?.id ?? next.subtitle_tracks[0]?.id ?? null);
    setSelectedCueId((current) => next.subtitle_tracks.some((track) => track.cues.some((cue) => cue.id === current)) ? current : null);
    setSelectedCover((current) => current && (Boolean(next.cover_asset) || next.cover_frame_capability.available));
    setSelectedNarrationSegmentId((current) => next.narration?.segments.some((segment) => segment.id === current) ? current : null);
    setTasks((current) => [next.task, ...current.filter((item) => item.id !== next.task.id)]);
    if (next.task.stage === "topic") {
      const appliedRevision = editContextRevision.current;
      const nextTopics = await getCreationTaskTopics(task.id);
      if (task.id !== currentTaskId.current || appliedRevision !== editContextRevision.current) return;
      setTopics(nextTopics);
      setSelectedTopicId(nextTopics.selected_topic_id ?? nextTopics.candidates[0]?.id ?? null);
    }
  }

  useEffect(() => {
    if (pendingNavigationRead.current?.taskId !== task.id || busy !== null) return;
    let active = true;
    const entered = currentWorkspace.current;
    const readRevision = pendingNavigationRead.current.inputRevision;
    const unchanged = () => active && currentTaskId.current === task.id
      && currentWorkspace.current === entered && readRevision === navigationReadRevision.current
      && currentBusy.current === null && !currentEditorDraft.current;
    void refresh(true, next => {
      const current = unchanged() && next.task.id === task.id
        && Date.parse(next.task.updated_at) >= Date.parse(entered.task.updated_at);
      if (current && next === entered) pendingNavigationRead.current = null;
      return current && next !== entered;
    }, true).catch(cause => {
      if (unchanged()) setError(errorDetails(cause));
    });
    return () => { active = false; };
    // Selection and view changes retain their latest state while facts refresh. Unsaved
    // input still owns its old version; a busy action either refreshes facts itself or
    // lets this pending entry read resume when it settles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, busy]);

  async function run(action: BusyAction, operation: () => Promise<unknown>, success?: string) {
    setBusy(action);
    setError(null);
    setNotice(null);
    if (action === "clip") {
      invalidateEditPreview();
    }
    try {
      await operation();
      await refresh(activeView === "montage");
      if (success) setNotice(success);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function changeExecution(operation: () => Promise<CreationExecutionStatus>) {
    setBusy("execution");
    setExecutionError(null);
    try {
      const next = await operation();
      setExecution(next);
      pollExecutionRef.current();
      const updated = await getCreationTaskWorkspace(task.id);
      setWorkspace(updated);
      setTasks((items) => [updated.task, ...items.filter((item) => item.id !== updated.task.id)]);
    } catch (cause) {
      setExecutionError(errorDetails(cause));
      if (task.confirmed_script_id) {
        void previewCreationExecution(task.id).then(setExecutionPreview).catch(() => undefined);
      }
    } finally {
      setBusy(null);
    }
  }

  async function saveVideo() {
    setBusy("save");
    setError(null);
    setNotice(null);
    const saver = window.electronAPI?.saveRenderedVideo;
    try {
      if (!saver) throw new Error("请在桌面客户端中使用原生保存功能。浏览器预览不会接收文件路径。");
      const result = await saver(task.id);
      if (result.error) throw new Error(result.error);
      if (result.saved) setNotice(`已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`);
      else if (result.canceled) setNotice("已取消保存成片。");
    } catch (cause) {
      const details = errorDetails(cause);
      // 保存失败必须同时给普通人三件能判断的事：没保存成、为什么、接下来怎么做。
      // 桌面端返回的原始报错保留在 details.message 里，不被通用文案吞掉。
      setError(saver
        ? { ...details, message: `成片没有保存到本地。${details.message}`, repairAction: "请先检查当前成片和本地服务是否可用（成片是否还在、本地服务是否已停止或返回错误），再检查保存位置是否可写（例如外接盘仍已连接、目录还有剩余空间）；问题恢复后再次点击「保存到本地」。" }
        : { ...details, message: `成片没有保存到本地。${details.message}` });
    } finally {
      setBusy(null);
    }
  }

  async function saveCover() {
    const coverId = workspace.cover_asset?.id;
    if (!coverId) return;
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const saver = window.electronAPI?.saveCoverImage;
      if (!saver) throw new Error("请在桌面客户端中保存封面；浏览器预览不支持本地保存。");
      const result = await saver(task.id, coverId);
      if (result.error) throw new Error(result.error);
      if (result.saved) setNotice(`封面已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`);
      else if (result.canceled) setNotice("已取消保存封面。");
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  async function saveSubtitle(mode: "zh" | "en" | "zh-en") {
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const versionId = workspace.task.current_timeline_version_id;
      const usableTrack = (language: "zh-CN" | "en-US") =>
        [activeSubtitleTrack, ...workspace.subtitle_tracks].find(
          (track) => track?.language === language && track.status === "valid" && track.cues.length > 0,
        );
      const chinese = usableTrack("zh-CN");
      const english = usableTrack("en-US");
      if (!versionId || (mode !== "en" && !chinese) || (mode !== "zh" && !english)) {
        throw new Error("当前版本缺少所选语言的有效字幕，请先检查或生成字幕。");
      }
      const saver = window.electronAPI?.saveSubtitleFile;
      if (!saver) throw new Error("请在桌面客户端中保存字幕；浏览器预览不支持本地保存。");
      const result = await saver({
        taskId: task.id,
        expectedVersionId: versionId,
        mode,
        ...(mode !== "en" ? { zhTrackId: chinese!.id } : {}),
        ...(mode !== "zh" ? { enTrackId: english!.id } : {}),
      });
      if (result.error) throw new Error(result.error);
      if (result.saved) setNotice(`字幕已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`);
      else if (result.canceled) setNotice("已取消保存字幕。");
    } catch (cause) {
      const details = errorDetails(cause);
      setError({ ...details, message: `字幕没有保存到本地。${details.message}`, repairAction:
        "当前任务与字幕草稿未修改；检查保存位置后可直接重试保存。" });
    } finally {
      setBusy(null);
    }
  }

  // 插入用与「换素材」同一对既有端点取候选，不新开端点。InsertClip 的锚点就是
  // 当前选中镜头（clip_id），落点是「插在它之后」；后端 _preview_exact_montage_edit
  // 对每个操作都要求 clip_id，缺了直接 409。
  async function showInsertCandidates(clipId: string) {
    setBusy("clip");
    setError(null);
    try {
      const clip = workspace.montage?.clips.find((item) => item.id === clipId);
      const candidates = await getCreationTaskMontageClipCandidates(task.id, clipId);
      // 「添加片段」是增加，不是替换：当前镜头自己也在候选里，要排除掉。
      setReplacementCandidates(candidates.filter((candidate) => candidate.asset_id !== clip?.asset_id || candidate.segment_id !== clip?.segment_id));
      setInserting(true);
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  async function showReplacementCandidates(clipId: string) {
    setBusy("clip");
    setError(null);
    try {
      const candidates = await getCreationTaskMontageClipCandidates(task.id, clipId);
      const clip = workspace.montage?.clips.find((item) => item.id === clipId);
      setReplacementCandidates(candidates.filter((candidate) => candidate.asset_id !== clip?.asset_id || candidate.segment_id !== clip?.segment_id));
      setReplacementClipId(clipId);
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  function activateTaskSelection() {
    if (detachedReference !== null) invalidateEditPreview();
    setDetachedReference(null);
    if (selectedMaterialReference) materialReference?.setReference(null);
  }

  function selectOverlay(id: string|null) {
    if (id) activateTaskSelection();
    invalidateEditPreview(); setEditorDraft(false); setSelectedOverlayId(id); setSelectedAudioId(null);
    const item=workspace.montage?.graphic_overlays?.find(value=>value.id===id);
    if(item){const time=(item.start_seconds+item.end_seconds)/2;playheadRef.current=time;
      if(montageVideoRef.current){montageVideoRef.current.pause();montageVideoRef.current.currentTime=time;}}
  }

  function selectClip(clipId: string) {
    activateTaskSelection();
    if(selectedOverlayId){setEditorDraft(false);invalidateEditPreview();}
    setSelectedOverlayId(null);
    setSelectedAudioId(null);
    setInserting(false);
    if (selectedClipId !== clipId || selectedCueId || selectedNarrationSegmentId || selectedAudioId) { setEditorDraft(false); invalidateEditPreview(); }
    setSelectedClipId(clipId);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
    const clip = workspace.montage?.clips.find((item) => item.id === clipId);
    if (clip && workspace.montage?.output_url && montageVideoRef.current) {
      const position = (clip.timeline_start_seconds + clip.timeline_end_seconds) / 2;
      montageVideoRef.current.currentTime = position;
      playheadRef.current = position;
    }
  }

  function locateQualityFinding(finding: CreationTaskQualityCheck["findings"][number]) {
    const clip = workspace.montage?.clips.find(item => finding.clip_ids.includes(item.id));
    if (clip) selectClip(clip.id);
    playheadRef.current = finding.start_seconds;
    if (montageVideoRef.current) {
      montageVideoRef.current.pause();
      montageVideoRef.current.currentTime = finding.start_seconds;
    }
  }

  function selectPlayingClip(video: HTMLVideoElement) {
    playheadRef.current = video.currentTime;
    if (selectedOverlayId) return;
    if (selectedAudioId) { setAudioPlayhead(video.currentTime); return; }
    const clips = workspace.montage?.clips ?? [];
    const clip = clips.find((item, index) => video.currentTime >= item.timeline_start_seconds
      && (video.currentTime < item.timeline_end_seconds
        || (index === clips.length - 1 && video.currentTime === item.timeline_end_seconds)));
    if (!clip) return;
    if (selectedClipId !== clip.id) { setEditorDraft(false); invalidateEditPreview(); }
    setSelectedClipId(clip.id);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
  }

  function selectCue(cueId: string) {
    activateTaskSelection();
    setSelectedAudioId(null);
    if (selectedCueId !== cueId || selectedClipId || selectedNarrationSegmentId || selectedAudioId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(cueId);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
  }

  function selectNarrationSegment(segmentId: string) {
    activateTaskSelection();
    if (selectedNarrationSegmentId !== segmentId || selectedClipId || selectedCueId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(segmentId);
    setSelectedCover(false);
  }

  function selectCover() {
    activateTaskSelection();
    if (!selectedCover || selectedClipId || selectedCueId || selectedNarrationSegmentId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(true);
  }

  function selectSubtitleTrack(trackId: string) {
    if (activeSubtitleTrackId !== trackId) invalidateEditPreview();
    setActiveSubtitleTrackId(trackId);
    setSelectedCueId(null);
    setSelectedCover(false);
  }

  function openView(view: WorkspaceView) {
    setSelectedOverlayId(null);
    setSavingTemplate(false);
    setActiveView(view);
    if (!(["montage", "review"].includes(activeView) && ["montage", "review"].includes(view))) {
      setSelectedClipId(null);
      playheadRef.current = null;
    }
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
    invalidateEditPreview();
  }

  function generatePersonRender(segmentId: string) {
    const selectedPerson = workspace.plan.selected_person;
    if (!selectedPerson) return;
    void run(
      "person-render",
      () => generateCreationTaskPersonRender(task.id, {
        narration_segment_id: segmentId,
        person_profile_id: selectedPerson.profile_id,
        person_version: selectedPerson.version,
        motion: "natural",
        expression: "neutral",
      }),
      "人物口型预览已生成；尚未写入成片时间线。",
    );
  }

  function preparePersonEdit(clipId: string, command: string) {
    selectClip(clipId);
    setEditCommand(command);
    invalidateEditPreview();
  }

  async function previewEdit(commandOverride?: string) {
    const rawCommand = (commandOverride ?? editCommand).trim();
    if (!selectedEditContext || !rawCommand) return;
    if(commandOverride === undefined && editPreview?.proposal.operation.operation_type==="EditCompoundTimeline" && editPreview.proposal.operation.payload.selection_enabled){
      await reviseCompoundSelection(rawCommand);return;
    }
    if (commandOverride !== undefined) setEditCommand(rawCommand);
    if (montageVideoRef.current && !montageVideoRef.current.paused) montageVideoRef.current.pause();
    invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    setBusy("edit-preview");
    setError(null);
    setNotice(null);
    try {
      const response = await previewCreationTaskEdit(task.id, {
        raw_command: rawCommand,
        context_stage: activeView,
        selected_object_refs: [selectedEditContext.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])],
        playhead_ms: selectedEditContext.kind === "clip" && playheadRef.current !== null
          ? Math.round(playheadRef.current * 1000) : null,
      });
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) setEditPreview(preview);
    } catch (cause) {
      if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
    } finally {
      if (ownsRequest()) setBusy(null);
    }
  }

  async function askAssistant() {
    const message = editCommand.trim();
    if (!message) return;
    const selectedReference = selectedTaskReference;
    const materialSnapshot = selectedReference ? null : selectedMaterialReference?.selection ?? null;
    const snapshotPlayhead = selectedReference?.kind === "clip" && playheadRef.current !== null ? Math.round(playheadRef.current * 1000) : null;
    if (montageVideoRef.current && !montageVideoRef.current.paused) montageVideoRef.current.pause();
    invalidateEditPreview();
    const revision = editContextRevision.current;
    const requestSequence = ++assistantRequestSequence.current;
    const ownsRequest = () => requestSequence === assistantRequestSequence.current && task.id === currentTaskId.current;
    const releaseBusy = () => {
      setBusy(current => ownsRequest() && current === "assistant" ? null : current);
    };
    let requestConversationId: string | null = null;
    let requestLocalTurnId: string | null = null;
    setBusy("assistant");
    setError(null);
    try {
      if (!assistantConversation.conversation) await assistantConversation.createNew();
      const turn = assistantConversation.beginTurn(message);
      requestConversationId = turn.conversationId;
      requestLocalTurnId = turn.localTurnId;
      const onStreamEvent = assistantConversation.bindTurnEvents(turn.conversationId, turn.localTurnId);
      const expectedDraftVersion = await turn.draftCheckpoint;
      if (!ownsRequest() || revision !== editContextRevision.current || !assistantConversation.isCurrentConversation(turn.conversationId)) {
        await assistantConversation.failUnpersistedTurn(turn.conversationId, turn.localTurnId,
          "编辑范围已切换；本轮没有发送，原要求已保留为草稿。");
        releaseBusy();
        return;
      }
      if (materialSnapshot) {
        await sendMaterialAssistantTurn(message, turn.conversationId, materialSnapshot, turn.clientRequestId, expectedDraftVersion, onStreamEvent);
        if (assistantConversation.isCurrentConversation(turn.conversationId)) await assistantConversation.refresh();
        return;
      }
      const result = await sendCreationTaskAssistantTurn(task.id, {
        conversation_id: turn.conversationId,
        client_request_id: turn.clientRequestId,
        expected_draft_version: expectedDraftVersion,
        expected_timeline_version_id: task.current_timeline_version_id,
        expected_timeline_sequence: task.timeline_sequence,
        ...(selectedKnowledgeReference ? { selected_knowledge_reference: { knowledge_item_id: selectedKnowledgeReference.knowledge_item_id, version: selectedKnowledgeReference.version, ...(selectedKnowledgeReference.locator ? { locator: selectedKnowledgeReference.locator } : {}) } } : {}),
        ...(selectedProfileReference ? {selected_profile_reference:{kind:selectedProfileReference.kind,profile_id:selectedProfileReference.profile_id,version:selectedProfileReference.version}} : {}),
        message,
        context_stage: activeView,
        selected_object_refs: selectedReference
          ? [selectedReference.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])]
          : [],
        playhead_ms: snapshotPlayhead,
      }, onStreamEvent);
      if (assistantConversation.isCurrentConversation(turn.conversationId)) await assistantConversation.refresh();
      if (!ownsRequest() || revision !== editContextRevision.current || !assistantConversation.isCurrentConversation(turn.conversationId)) return;
      if (result.execution_status) setExecution(result.execution_status);
      pollExecutionRef.current();
      if (result.script_draft) await refresh(true, () => ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId));
      if (result.timeline_action) await refresh(true, () => ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId));
      if (ownsRequest() && revision === editContextRevision.current && assistantConversation.isCurrentConversation(turn.conversationId)) {
        const preview = result.media_job ? await resolveEditMedia(result.media_job, revision, turn.conversationId) : result.edit_preview;
        if (!ownsRequest() || revision !== editContextRevision.current || !assistantConversation.isCurrentConversation(turn.conversationId)) return;
        setEditPreview(preview);
        if (preview) setAssistantPreview(true);
      }
    } catch (cause) {
      if (requestConversationId && requestLocalTurnId && !(cause instanceof DOMException && cause.name === "AbortError") && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) {
        const failure = errorDetails(cause);
        await assistantConversation.failUnpersistedTurn(requestConversationId, requestLocalTurnId,
          [failure.message, failure.impact, failure.repairAction].filter(Boolean).join(" "));
      }
      if (ownsRequest() && revision === editContextRevision.current && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status)) && (!requestConversationId || assistantConversation.isCurrentConversation(requestConversationId))) {
        setError(errorDetails(cause));
        if (activeView === "script") {
          await refresh(false, () => ownsRequest() && revision === editContextRevision.current && (!requestConversationId || assistantConversation.isCurrentConversation(requestConversationId))).catch(() => undefined);
          const history = await getCreationTaskAssistantHistory(task.id).catch(() => null);
          if (history && ownsRequest() && revision === editContextRevision.current) {
            const turns = assistantHistoryTurns(history.items);
            setAssistantTurns(turns);
          }
        }
      }
    } finally {
      releaseBusy();
    }
  }

  async function openSavedEditProposal(turn: AssistantConversationTurn) {
    const saved = turn.result?.response as CreationTaskAssistantTurnResponse | undefined;
    const proposal = saved?.edit_preview?.proposal;
    if (!proposal || proposal.task_id !== task.id) {
      setError(errorDetails(new Error("这份修改属于另一个任务，请在原任务查看。")));
      return;
    }
    const conversationId = assistantConversation.conversation?.id;
    if (!conversationId) return;
    const revision = editContextRevision.current;
    setBusy("edit-preview");
    setError(null);
    try {
      const latest = await getCreationTaskEditPreview(task.id, proposal.id);
      if (currentTaskId.current !== task.id || editContextRevision.current !== revision
          || !assistantConversation.isCurrentConversation(conversationId)) return;
      setEditPreview(latest);
      setAssistantPreview(true);
    } catch (cause) {
      if (currentTaskId.current === task.id && editContextRevision.current === revision) setError(errorDetails(cause));
    } finally {
      if (currentTaskId.current === task.id) setBusy(null);
    }
  }

  async function transferReplyToDraft(turn: AssistantConversationTurn, replace: boolean) {
    const conversationId = assistantConversation.conversation?.id;
    if (!conversationId || !assistantConversation.isCurrentConversation(conversationId)
      || turn.status !== "completed" || !turn.assistant_text.trim()) return;
    if (latestScript?.selection_status === "confirmed") {
      setError(errorDetails(new Error("当前文案已确认，不能被对话回复替换。")));
      return;
    }
    if (latestScript?.selection_status === "draft" && !replace) {
      setError(errorDetails(new Error("当前已有待确认文案，请明确选择替换后再带入。")));
      return;
    }
    if (replace && latestScript?.selection_status !== "draft") {
      setError(errorDetails(new Error("没有可替换的待确认文案。")));
      return;
    }
    const requestKey = `${conversationId}:${turn.id}:${replace ? latestScript?.id : "new"}`;
    const requestId = replyTransferRequests.current.get(requestKey) ?? crypto.randomUUID();
    replyTransferRequests.current.set(requestKey, requestId);
    setBusy("reply-transfer");
    setError(null);
    setNotice(null);
    try {
      const plan = await getCreationPlan(task.plan_id);
      if (!assistantConversation.isCurrentConversation(conversationId)) return;
      await transferAssistantReplyToScript(task.id, {
        conversation_id: conversationId,
        turn_id: turn.id,
        request_id: requestId,
        expected_plan_updated_at: plan.updated_at,
        expected_task_updated_at: task.updated_at,
        ...(replace && latestScript ? {
          replace_existing_script_id: latestScript.id,
          expected_script_updated_at: latestScript.updated_at,
        } : {}),
      });
      replyTransferRequests.current.delete(requestKey);
      await refresh(true);
      setNotice("回复已保存为待确认文案草稿；请补充来源和画面建议，再确认文案。");
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  async function localEditSettled(jobId: string, status: string) {
    const pending = pendingLocalEditRef.current;
    if (!pending || pending.jobId !== jobId || pending.taskId !== currentTaskId.current) return;
    const stillCurrent = () => pendingLocalEditRef.current === pending && pending.taskId === currentTaskId.current;
    await refresh(true, stillCurrent);
    if (!stillCurrent()) return;
    setPendingLocalEdit(null);
    if (pending.operationType === "RegenerateNarrationSegment") {
      setNotice(status === "succeeded"
        ? "配音修改已完成，目标配音段和当前版本已更新；成片尚未重新生成。"
        : status === "cancelled"
          ? "配音修改已取消，当前版本和音频保持不变；请查看作业原因。"
          : "配音修改未完成，当前版本和音频保持不变；请查看作业原因。");
    } else {
      setNotice(status === "succeeded" ? "人物修改已完成，目标镜头和当前版本已更新；成片尚未重新生成。"
        : "人物修改未完成，当前版本保持不变；请查看作业原因。");
    }
  }

  async function confirmEdit() {
    if (!editPreview) return;
    const proposal = editPreview.proposal;
    const preserveNextDraft = assistantPreview;
    setBusy("edit-confirm");
    setError(null);
    setNotice(null);
    try {
      const result = await confirmCreationTaskEdit(task.id, proposal.id);
      if ("status" in result) {
        setPendingLocalEdit({ taskId: task.id, jobId: result.job_id,
          operationType: proposal.operation.operation_type, targetRefs: [...proposal.target_refs] });
        setNotice(queuedEditNotice(proposal.operation.operation_type));
      }
      invalidateEditPreview();
      if (!preserveNextDraft) setEditCommand("");
      await refresh(true);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  function selectAudio(id: string, time?: number) {
    activateTaskSelection();
    setSelectedOverlayId(null);
    invalidateEditPreview(); setEditorDraft(false); setSelectedAudioId(id);
    setAudioPlayhead(time ?? playheadRef.current ?? 0);
    if (time !== undefined) {
      playheadRef.current = time; setAudioPlayhead(time);
      if (montageVideoRef.current) { montageVideoRef.current.pause(); montageVideoRef.current.currentTime = time; }
    }
  }

  function positionAudioListen(time: number) {
    playheadRef.current=time;setAudioPlayhead(time);
    if(montageVideoRef.current){montageVideoRef.current.pause();montageVideoRef.current.currentTime=time;}
  }

  async function previewVisual(input: VisualEditInput) {
    setBusy("edit-preview"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    try {
      const response = await previewCreationTaskVisual(task.id,input);
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) {
        await confirmCreationTaskEdit(task.id, preview.proposal.id);
        if (!stillCurrent()) return;
        setEditorDraft(false);
        await refresh(true, stillCurrent);
      }
    } catch (cause) { if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function previewAudio(input: AudioEditInput) {
    setBusy("edit-preview"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    const stillCurrent = () => ownsRequest() && revision === editContextRevision.current;
    try {
      const response = await previewCreationTaskAudio(task.id,input);
      const preview = await resolveEditMedia(response, revision);
      if (stillCurrent()) {
        if (input.action === "delete") setEditPreview(preview);
        else {
          await confirmCreationTaskEdit(task.id, preview.proposal.id);
          if (!stillCurrent()) return;
          setEditorDraft(false);
          await refresh(true, stillCurrent);
        }
      }
    } catch (cause) { if (stillCurrent() && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function submitRecordedVoice(blob: Blob): Promise<void> {
    let recording: CreationTaskRecording;
    const recordingRevision = editContextRevision.current;
    const baseVersionId = task.current_timeline_version_id;
    const controller = new AbortController();
    mediaController.current?.abort(); mediaController.current = controller;
    const stillCurrent = () => !controller.signal.aborted && currentTaskId.current === task.id
      && currentWorkspace.current.task.current_timeline_version_id === baseVersionId
      && editContextRevision.current === recordingRevision;
    try {
      const response = await saveCreationTaskRecording(task.id, blob);
      recording = await resolveCreationTaskMediaResponse(task.id, response, "recording", {
        signal: controller.signal, baseVersionId,
        onUpdate: value => { if (stillCurrent()) setPendingMediaJob({ taskId: task.id, value }); },
      });
      if (!stillCurrent()) return;
    } catch (cause) {
      const details = errorDetails(cause);
      if (stillCurrent() && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(details);
      throw new Error(details.message);
    }
    setRecordings((current) => [recording, ...current.filter((item) => item.source_ref !== recording.source_ref)]);
    if (task.stage === "narration" && !workspace.narration) {
      setRecordingOpen(false);
      setNotice("录音已保存；尚未采用，选择“采用这段录音”后才会进入本任务配音。");
      return;
    }
    const narrationTrackId = workspace.montage?.audio_timeline?.tracks.find((track) => track.role === "narration")?.id ?? "audio-narration";
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    const revision = editContextRevision.current;
    try {
      const response = await previewCreationTaskAudio(task.id, {
        base_version_id: task.current_timeline_version_id,
        action: "replace_narration",
        target_id: narrationTrackId,
        values: { source_ref: recording.source_ref },
        raw_command: "用本机录音替换整条旁白",
      });
      const preview = await resolveEditMedia(response, revision);
      if (currentTaskId.current === task.id && revision === editContextRevision.current) {
        await confirmCreationTaskEdit(task.id, preview.proposal.id);
        setEditorDraft(false);
        setRecordingOpen(false);
        await refresh(true);
      }
    } catch (cause) {
      const details = errorDetails(cause);
      setError(details);
      throw new Error(details.message);
    }
  }

  async function adoptRecordedVoice(recording: CreationTaskRecording): Promise<void> {
    const script = workspace.scripts.find((item) => item.id === task.confirmed_script_id);
    if (!script || script.selection_status !== "confirmed" || script.sections.length !== 1) {
      setError(errorDetails(new Error("当前文案不是单段已确认版本，不能确定录音对应关系。")));
      return;
    }
    await run("recording-adopt", () => adoptCreationTaskRecording(task.id, {
      script_id: script.id,
      source_section_id: script.sections[0].id,
      source_ref: recording.source_ref,
    }), "本机录音已采用为当前文案的配音来源；可以按原任务继续制作。 ");
  }

  async function reviseCompoundSelection(selection: number[] | string) {
    if (!editPreview) return;
    const proposalId = editPreview.proposal.id;
    setBusy("edit-preview"); setError(null);
    const revision = ++editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    const ownsRequest = () => sequence === previewRequestSequence.current && currentTaskId.current === task.id;
    try {
      const response = await previewCompoundSelection(task.id, proposalId, selection);
      const next = await resolveEditMedia(response, revision);
      if (ownsRequest() && editContextRevision.current === revision) {
        setEditPreview(next);
        if (!assistantPreview) setEditCommand(next.intent.raw_command);
      }
    } catch (cause) { if (ownsRequest() && editContextRevision.current === revision && !mediaController.current?.signal.aborted) setError(errorDetails(cause)); }
    finally { if (ownsRequest()) setBusy(null); }
  }

  async function previewExactEdit(input: CreationTaskExactEditInput) {
    if (busy !== null) return false;
    setBusy("edit-confirm"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    const sequence = ++previewRequestSequence.current;
    let saved = false;
    try {
      if (input.operation_type.startsWith("Delete") || (input.operation_type === "EditGraphicOverlay" && input.overlay_action === "delete")) {
        const response = await previewCreationTaskExactEdit(task.id, input);
        const preview = await resolveEditMedia(response, revision);
        if (currentTaskId.current !== task.id || revision !== editContextRevision.current) return false;
        setEditPreview(preview);
        saved = true;
      } else {
        const result = await applyCreationTaskExactEdit(task.id, input);
        if ("status" in result) {
          setPendingLocalEdit({ taskId: task.id, jobId: result.job_id,
            operationType: input.operation_type, targetRefs: input.operation_type === "RegenerateNarrationSegment"
              ? [`narration:${input.narration_segment_id}`] : selectedEditContext ? [selectedEditContext.ref] : [] });
          setNotice(queuedEditNotice(input.operation_type));
          await refresh(true);
          return false;
        }
        saved = true;
        setClipDrafts(current => {
          const next = { ...current };
          if (selectedClipId) delete next[`${task.id}:${selectedClipId}`];
          return next;
        });
        setEditorDraft(false);
        await refresh(true);
      }
    } catch (cause) {
      if (currentTaskId.current === task.id && revision === editContextRevision.current && !mediaController.current?.signal.aborted && !(cause instanceof TaskMediaJobError && ["cancelled", "stale"].includes(cause.job.status))) setError(errorDetails(cause));
      return saved;
    } finally { if (currentTaskId.current === task.id && sequence === previewRequestSequence.current) setBusy(null); }
    return saved;
  }

  async function undoTimeline(redo = false) {
    setBusy("undo");
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    try {
      await (redo ? redoCreationTaskTimeline : undoCreationTaskTimeline)(task.id);
      setClipDrafts({});
      setSelectedCueId(null);
      setSelectedNarrationSegmentId(null);
      setSelectedCover(false);
      await refresh(true);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  const workspaceStatus = task.stage_status === "failed"
    ? { kind: "failed" as const, label: "需要处理" }
    : task.stage_status === "waiting_for_user"
      ? { kind: "attention" as const, label: "等待确认" }
      : { kind: "connected" as const, label: task.stage_status === "running" ? "正在处理" : "本地任务" };

  function stageView(stageId: typeof productStages[number]["id"]): WorkspaceView | null {
    return stageId;
  }

  // state 默认取当前闭包的 workspace；refresh 需要按刷新后的 next 事实判断，
  // 所以这里显式接受状态参数，而不是读刷新前的旧闭包。
  function canOpenStage(view: WorkspaceView | null, state: WorkspaceState = workspace): view is WorkspaceView {
    if (!view) return false;
    if (view === state.task.stage) return true;
    if (view === "prepare") return true;
    if (view === "topic") return Boolean(state.selected_topic);
    if (view === "script") return Boolean(state.scripts[0]);
    if (view === "narration") return Boolean(state.narration);
    if (view === "person") return Boolean(state.narration || state.person_renders.length);
    if (view === "montage") return Boolean(state.montage);
    if (view === "subtitle_cover") return Boolean(state.montage && state.subtitle_tracks.length);
    return (view === "review" || view === "publish") && Boolean(state.montage?.output_url);
  }

  const isEditor = activeView === "montage";
  const selectedClip = selectedClipIndex >= 0 ? workspace.montage?.clips[selectedClipIndex] : undefined;
  const clipDraftKey = selectedClip ? `${task.id}:${selectedClip.id}` : undefined;
  const savedClipDraft = selectedClip ? createVideoClipDraft(selectedClip) : undefined;
  const clipDraft = clipDraftKey && savedClipDraft ? clipDrafts[clipDraftKey] ?? savedClipDraft : undefined;
  const clipDraftChanged = Boolean(clipDraft && savedClipDraft && (
    clipDraft.start !== savedClipDraft.start || clipDraft.mode !== savedClipDraft.mode
    || clipDraft.x !== savedClipDraft.x || clipDraft.y !== savedClipDraft.y
  ));
  function updateClipDraft(next: VideoClipDraft) {
    if (!clipDraftKey || !savedClipDraft) return;
    setClipDrafts(current => ({ ...current, [clipDraftKey]: next }));
    setEditorDraft(next.start !== savedClipDraft.start || next.mode !== savedClipDraft.mode
      || next.x !== savedClipDraft.x || next.y !== savedClipDraft.y);
  }
  const quickInspector = isEditor && selectedClip && clipDraft && !selectedOverlay && !selectedAudioId
    ? (openDetails: () => void) => <VideoClipQuickEditor
      key={clipDraftKey}
      clip={selectedClip}
      index={selectedClipIndex}
      busy={busy !== null}
      draft={clipDraft}
      onDraftStateChange={updateClipDraft}
      onDraftChange={invalidateEditPreview}
      onPreview={input => void previewExactEdit(input)}
      onOpenDetails={openDetails}
    />
    : undefined;
  const inspector = isEditor
    ? selectedOverlay ? <GraphicOverlayEditor taskId={task.id} items={workspace.montage!.graphic_overlays??[]} selectedId={selectedOverlay.id} onSelect={selectOverlay} duration={workspace.montage!.duration_seconds} busy={busy!==null} onPreview={input=>void previewExactEdit(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedAudioTrack ? <AudioTimelineInspector track={selectedAudioTrack} clip={selectedAudioClip} versionId={task.current_timeline_version_id} playhead={audioPlayhead} busy={busy !== null} onPreview={input=>void previewAudio(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedClip ? <VideoClipInspector onSelectOverlay={selectOverlay} getPlayhead={() => playheadRef.current} onVisualPreview={input=>void previewVisual(input)} clip={selectedClip} index={selectedClipIndex} count={workspace.montage!.clips.length} workspace={workspace} busy={busy !== null} initialDraft={clipDraft} onDraftStateChange={updateClipDraft} onPreview={input => void previewExactEdit(input)} onDraftChange={changed => { invalidateEditPreview(); setEditorDraft(changed); }} onReplace={() => { setInserting(false); void showReplacementCandidates(selectedClip.id); }} candidates={replacementCandidates} replacing={replacementClipId === selectedClip.id} onAddSegment={() => { setReplacementClipId(null); void showInsertCandidates(selectedClip.id); }} inserting={inserting} onPerson={command => preparePersonEdit(selectedClip.id, command)} onAddOriginal={() => void previewEdit("提取当前镜头原声")} /> : undefined
    : activeView === "subtitle_cover"
    ? selectedSubtitleCue || selectedCover
      ? <SubtitleCoverInspector selectedCover={selectedCover} selectedCue={selectedSubtitleCue} selectedTrack={activeSubtitleTrack} workspace={workspace} />
      : undefined
    : <Inspector activeView={activeView} busy={busy !== null} error={error} onPreparePersonEdit={preparePersonEdit} selectedContext={selectedEditContext} workspace={workspace} />;

  // target_refs is the actual proposal scope; external_effects is explanatory
  // copy (even a local crop includes it), not a boolean permission/fee flag.
  const operation = editPreview?.proposal.operation;
  const previewCommand = assistantPreview && editPreview ? editPreview.intent.raw_command : editCommand;
  const ordinaryObjectEdit = operation && (["ReplaceClip", "ChangeCrop", "TrimClip", "ChangeClipSpeed", "SetClipLock", "ConfirmClip", "SetClipTransition", "AdjustVisualEffects", "UpdateSubtitleCue"].includes(operation.operation_type)
    || (operation.operation_type === "EditGraphicOverlay" && operation.payload.overlay_action === "update"));
  const localConfirmation = Boolean(ordinaryObjectEdit && selectedEditContext && editPreview
    && editPreview.proposal.task_id === task.id && editPreview.proposal.workspace_id === task.workspace_id
    && editPreview.proposal.target_refs.length > 0
    && editPreview.proposal.target_refs.every(ref => ref === selectedEditContext.ref));
  const inspectorConfirmation = localConfirmation && editPreview ? <EditPreviewCard key={editPreview.proposal.id} preview={editPreview} busy={busy} compact command={previewCommand} kind="config" onCancel={invalidateEditPreview} onConfirm={() => void confirmEdit()} /> : null;
  const sharingControls = (
    <div className={styles.sharingControls}>
      <div className={styles.taskVisibility}>
        <span>{task.visibility === "shared" ? "任务：团队共享" : "任务：仅自己可见"}</span>
        {task.visibility === "shared"
          ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changeTaskVisibility("private")} type="button">收回</button>
          : <button disabled={visibilityBusy || busy !== null} onClick={() => void changeTaskVisibility("shared")} type="button">共享给团队</button>}
      </div>
      <div className={styles.taskVisibility}>
        <span>方案：{planVisibility === null ? "状态未知" : planVisibility === "shared" ? "团队共享" : "仅自己可见"}</span>
        {planVisibility === "shared"
          ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changePlanVisibility("private")} type="button">方案收回</button>
          : planVisibility === "private"
            ? <button disabled={visibilityBusy || busy !== null} onClick={() => void changePlanVisibility("shared")} type="button">方案共享给团队</button>
            : null}
      </div>
      {visibilityError ? <p role="alert">{visibilityError}</p> : null}
    </div>
  );

  const hasReviewOutput = task.output_ready && workspace.montage?.status === "rendered" && Boolean(workspace.montage.output_url);
  const reviewPane = !hasReviewOutput ? "prepare" : reviewChoice?.taskId === task.id ? reviewChoice.pane : "result";
  const reviewTabKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!hasReviewOutput) return;
    const pane = event.key === "Home" ? "prepare" : event.key === "End" ? "result" : reviewPane === "prepare" ? "result" : "prepare";
    setReviewChoice({ taskId: task.id, pane });
    document.getElementById(`review-${pane}-${task.id}`)?.focus();
  };

  const hasContextConversation = !savingTemplate && (
    (activeView === "script" && task.stage === "script" && !task.confirmed_script_id)
    || ((activeView === "montage" || activeView === "review") && workspace.montage)
    || (activeView === "narration" && workspace.narration)
    || (activeView === "subtitle_cover" && activeSubtitleTrack)
  );
  const conversation = hasContextConversation
    ? <ContextCommandBar localConfirmation={localConfirmation} scriptMode={activeView === "script"} key={task.id} onSelectionPreview={indices => void reviseCompoundSelection(indices)} compact busy={busy} command={editCommand} previewCommand={previewCommand} onCancel={() => invalidateEditPreview()} onCommandChange={(value) => { setEditCommand(value); if (!assistantPreview && (editPreview?.proposal.operation.operation_type !== "EditCompoundTimeline" || !editPreview.proposal.operation.payload.selection_enabled)) invalidateEditPreview(); }} onConfirm={() => void confirmEdit()} onExactPreview={() => void previewEdit()} preview={editPreview} assistantState={assistantConversation} onAssistantSend={askAssistant} onOpenEditProposal={turn => void openSavedEditProposal(turn)} onTransferReply={activeView === "script" ? transferReplyToDraft : undefined} transferDraftState={latestScript?.selection_status === "confirmed" ? "confirmed" : latestScript?.selection_status === "draft" ? "draft" : "none"} onConversationChange={() => { assistantRequestSequence.current += 1; previewRequestSequence.current += 1; invalidateEditPreview(); setBusy(null); setError(null); }} assistantTurns={assistantTurns} assistantConfigured={assistantConfigured} onClearReference={() => setDetachedReference(selectedEditContext?.ref ?? null)} materialContextLabel={selectedTaskReference ? null : selectedMaterialReference?.label ?? null} selectedContextLabel={!selectedTaskReference ? null : isEditor && selectedOverlay ? `${selectedEditContext?.label} · ${editorTime(selectedOverlay.start_seconds)}–${editorTime(selectedOverlay.end_seconds)}` : isEditor && selectedClip && !selectedAudioId ? `${selectedEditContext?.label} · ${editorTime(selectedClip.timeline_start_seconds)}–${editorTime(selectedClip.timeline_end_seconds)}` : selectedEditContext?.label ?? null} selectedContextPlaceholder={selectedTaskReference ? "说说怎么改" : selectedMaterialReference ? "说说想怎样整理这条素材" : activeView === "script" ? "说说文案想怎么写" : "想问什么？"} />
    : <HomeCommandComposer task={task} onTaskUpdated={() => { void refresh(true); }} />;

  return (
    <AppShell inspectorConfirmation={inspectorConfirmation} inspectorConfirmationKey={localConfirmation ? editPreview?.proposal.id : undefined} conversation={conversation} creationTasks={shellTasks} inspector={inspector} quickInspector={quickInspector} quickInspectorKey={quickInspector ? clipDraftKey : undefined} quickInspectorLabel={quickInspector ? `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")} 快速编辑` : undefined} editorLayout={isEditor} toolbar={<div className={editorUi.toolbarTitle}><h1>{task.name}</h1><span>{busy ? "处理中" : editPreview ? "待确认" : editorDraft || quickInspector && clipDraftChanged ? "未应用" : isEditor ? "已保存" : ""}</span>
      <button aria-haspopup="dialog" aria-expanded={sharingOpen} className={editorUi.button} onClick={() => setSharingOpen(true)} type="button">共享设置</button>
      <HomeCardDialog kind="config" open={sharingOpen} title="任务与方案共享" onClose={() => setSharingOpen(false)}>{sharingControls}</HomeCardDialog></div>} toolbarActions={isEditor ? <div className={editorUi.toolbarActions}><button className={editorUi.iconButton} aria-label="撤销" title="撤销上一次修改" disabled={busy !== null || !task.can_undo} onClick={() => void undoTimeline()} type="button"><Undo2 size={17} /></button><button className={editorUi.iconButton} aria-label="重做" title="重做刚才撤销的修改" disabled={busy !== null || !task.can_redo} onClick={() => void undoTimeline(true)} type="button"><Redo2 size={17} /></button><button className={editorUi.iconButton} aria-label="电脑录音替换旁白" title="用本机录音替换整条旁白" disabled={busy !== null} onClick={() => setRecordingOpen(true)} type="button"><Mic size={17} /></button><button className={editorUi.iconButton} aria-label="历史时间线版本" title="查看这个任务的历史时间线版本" onClick={() => setHistoryOpen((open) => !open)} type="button"><History size={17} /></button></div> : undefined} inspectorTitle={isEditor ? selectedOverlay ? selectedEditContext?.label : selectedAudioTrack ? selectedAudioClip?.name ?? selectedAudioTrack.name : `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")}` : activeView === "subtitle_cover" ? "当前选择" : "任务详情"} workspaceStatus={workspaceStatus}>
      <Tabs.Root value={activeView} onValueChange={value => { const view = value as WorkspaceView; if (canOpenStage(view)) openView(view); }} onChangeCapture={() => { navigationReadRevision.current += 1; }} className={[isEditor ? editorUi.workspace : styles.workspace, activeView === "subtitle_cover" ? styles.subtitleWorkspace : ""].filter(Boolean).join(" ")}>
        <nav className={editorUi.stageRail} aria-label="制作阶段"><Tabs.List className={editorUi.stageList}>{productStages.map(stage => {
          const presentation = productStageState(stage.id, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid");
          const detail = task.stage_status === "failed" && stage.id === task.stage ? "需要处理" : presentation.detail;
          return <Tabs.Tab value={stage.id} data-stage-state={presentation.state} title={`${stage.title}：${detail}`} aria-label={`${stage.title}：${detail}`} disabled={!canOpenStage(stage.id)} key={stage.id}>{stage.label}</Tabs.Tab>;
        })}</Tabs.List></nav>
        <Tabs.Panel value={activeView} className={isEditor ? editorUi.editorBody : styles.workspaceBody}>
        <header className={styles.stageHeading} data-timeline-version={task.timeline_sequence}>
          <div><h2>{productStages.find(stage => stage.id === activeView)?.title}</h2><span className={styles.stateBadge}>{activeView === task.stage ? creationTaskStatusPresentation(task.stage_status, task.stage).label : productStageState(activeView, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid").detail}</span></div>
          {execution?.job && ["queued", "running"].includes(execution.job.status) ? <span className={styles.runtimeState} role="status">连续制作：{execution.job.status === "queued" ? "已排队" : "后台制作中"}{execution.current_stage ? ` · ${executionStageLabels[execution.current_stage]}` : ""}</span> : null}
          <JobProgressPanel selectedRefs={selectedTaskReference ? [selectedTaskReference.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])] : []} restoreBusy={busy !== null} onRestoreMedia={candidate => void restoreMediaPreview(candidate)} recoveredMedia={recoveredMedia?.taskId === task.id && recoveredMedia.job.base_version_id === task.current_timeline_version_id ? recoveredMedia : null} mediaJob={pendingMediaJob?.taskId === task.id ? pendingMediaJob.value : null} onCancelMedia={() => void cancelMediaPreview()} showLiveStatus={!execution?.job || !["queued", "running"].includes(execution.job.status)} key={task.id} task={task} taskId={task.id} pendingEditJobId={pendingLocalEdit?.taskId === task.id ? pendingLocalEdit.jobId : null} onEditSettled={localEditSettled} refreshToken={`${execution?.job?.id ?? "none"}:${execution?.job?.status ?? "none"}:${execution?.stages.length ?? 0}`} />
        </header>
        <HomeCardDialog kind="detail" title="历史时间线版本" open={historyOpen} onClose={() => setHistoryOpen(false)} footer={<button type="button" onClick={() => setHistoryOpen(false)}>收起历史版本</button>}>
          <TimelineVersionHistory taskId={task.id} open={historyOpen} currentVersionId={task.current_timeline_version_id} />
        </HomeCardDialog>
        {notice ? <div className={isEditor ? editorUi.feedback : styles.notice} role="status"><CheckCircle2 size={17} />{notice}</div> : null}
        {error ? <TaskIssue stage={activeView} reason={error.message} impact={error.impact} repair={error.repairAction} code={error.code}>{error.code === "timeline_version_required" ? <button className={editorUi.button} disabled={busy !== null} onClick={() => void run("clip", () => initializeCreationTaskTimeline(task.id), "已为当前故事板建立初始版本，可继续预览裁剪。") } type="button">保留当前故事板并建立版本</button> : null}</TaskIssue> : null}
        {executionError ? <TaskIssue stage={task.stage} reason={executionError.code === "device_budget_unavailable" ? "这台设备还没有完成制作资源标定，暂时不能开始制作。" : executionError.message} impact={executionError.impact} repair={executionError.repairAction} code={executionError.code}>{executionError.code === "device_budget_unavailable" ? <><Link href="/health">检查运行情况</Link><details className={styles.traceDetails}><summary>制作资源诊断</summary><p>{executionError.message}</p></details></> : null}{task.confirmed_script_id ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void changeExecution(async () => { const preview = await previewCreationExecution(task.id); setExecutionPreview(preview); return getCreationExecution(task.id); })} type="button">重新核对文案和制作要求</button> : null}</TaskIssue> : null}
        {!error && !executionError && task.stage_status === "failed" && activeView === task.stage && (task.stage_reason || task.stage_impact) ? <TaskIssue stage={task.stage} reason={task.stage_reason} impact={task.stage_impact} repair={task.stage_repair_action} label={`${stageLabels[activeView]}失败`} /> : null}
        {activeView === "review" ? <div className={styles.reviewTabs} role="tablist" aria-label="成片工作区">
          {([ ["prepare", "制作准备"], ["result", "成片检查"] ] as const).map(([pane, label]) => <button key={pane} id={`review-${pane}-${task.id}`} role="tab" disabled={pane === "result" && !hasReviewOutput} aria-selected={reviewPane === pane} aria-controls={`review-panel-${pane}-${task.id}`} tabIndex={reviewPane === pane ? 0 : -1} onKeyDown={reviewTabKey} onClick={() => setReviewChoice({ taskId: task.id, pane })} type="button">{label}</button>)}
        </div> : null}
        <div className={activeView === "review" ? styles.reviewPane : styles.flowContents} role={activeView === "review" ? "tabpanel" : undefined} id={`review-panel-prepare-${task.id}`} aria-labelledby={activeView === "review" ? `review-prepare-${task.id}` : undefined} hidden={activeView === "review" && reviewPane !== "prepare"}>
        {task.confirmed_script_id && (executionPreview || execution?.job) && activeView === "review" ? <CreationExecutionPanel workspace={workspace} compact={activeView !== "review"} preview={executionPreview} execution={execution} busy={busy !== null} onStart={() => executionPreview && void changeExecution(() => startCreationExecution(task.id, executionPreview))} onResume={() => void changeExecution(() => resumeCreationExecution(task.id))} onCancel={() => void changeExecution(() => cancelCreationExecution(task.id))} onOpenMontage={() => openView("montage")} /> : null}{activeView === "review" && !executionPreview && !execution?.job ? <p className={styles.notice}>还没有制作安排。可以先查看文案、配音与剪辑。</p> : null}
        </div>


        {activeView === "topic" && task.stage === "topic" ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><h2>{topics?.candidates.length ? "选择选题" : "生成选题"}</h2><p>{workspace.plan.knowledge_references.length} 项固定资料 · {workspace.plan.source_references.length} 条信息源</p><details className={styles.traceDetails}><summary>本次生成范围</summary><p>使用本任务预检列出的文字摘录；确认选题后不再生成新候选。</p></details></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。") } type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => { setSelectedTopicId(candidate.id); setTopicTitleDraft(candidate.title); }} type="radio" /><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{selectedTopicCandidate ? <label className={styles.field}><span>编辑标题</span><input aria-label="编辑标题" disabled={busy !== null} maxLength={80} onChange={(event) => setTopicTitleDraft(event.target.value)} placeholder="输入本选题的标题" value={topicTitleDraft} /><small className={styles.eyebrow}>确认时保存这个标题；后续文案将使用已保存的选题。</small></label> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId || !topicTitleDraft.trim()} onClick={() => selectedTopicId && run("topic-confirm", async () => { const saved = await confirmCreationTaskTopic(task.id, selectedTopicId, { title: topicTitleDraft.trim() }); const confirmed = saved.candidates.find(candidate => candidate.id === saved.selected_topic_id); setNotice(confirmed ? `选题「${confirmed.title}」已确认，下一步生成文案。` : "选题已确认，下一步生成文案。"); }) } type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认选题</button></div> : null}</section> : null}
        {activeView === "topic" && task.stage !== "topic" ? <ConfirmedTopicView workspace={workspace} /> : null}

        {activeView === "script" && task.stage === "script" && !latestScript ? <>
          <PlanReplyDraftAction busy={busy !== null} task={task} onComplete={async () => { await refresh(true); setNotice("已保存对话回复为待确认文案草稿；请补充当前资料引用和画面建议。"); }} />
          <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>生成文案</h2><p>使用已确认选题与固定资料。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("script", () => generateCreationTaskScript(task.id), "文案已生成，等待检查。") } type="button">{busy === "script" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section>
        </> : null}
        {activeView === "script" && (task.stage === "script" || task.can_revise_script) && latestScript ? <ScriptEditor taskId={task.id} busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections, expected_updated_at: draft.updated_at }), "文案已确认，可以生成配音。") } repairing={task.stage_status === "failed" && latestScript.selection_status === "confirmed"} script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && !task.can_revise_script && latestScript ? <ReadonlyScriptsView workspace={workspace} /> : null}

        {activeView === "script" && latestScript?.selection_status === "confirmed" ? <ScriptLibrarySave key={latestScript.id} script={latestScript} /> : null}

        {activeView === "narration" && task.stage === "narration" && !workspace.narration ? (
          <section className={styles.workSection}>
            <div className={styles.emptyAction}>
              <Volume2 size={30} />
              <h2>准备配音</h2>
              <p>可采用本人录音，或使用已选声音制作。</p>
              {latestScript?.selection_status === "confirmed" ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => setRecordingOpen(true)} type="button"><Mic size={16} />录一段旁白</button> : null}
              {task.can_revise_script ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => openView("script")} type="button">修改文案</button> : null}
              {recordingsError ? <p role="alert">已保存录音读取失败：{recordingsError}</p> : null}
              {recordings.map((recording) => (
                <article key={recording.source_ref}>
                  <strong>本机录音 · {(recording.duration_ms / 1000).toFixed(1)} 秒</strong>
                  <audio controls preload="metadata" src={apiUrl(recording.audio_url)} />
                  <span>{recording.applied ? "已采用为当前配音" : "已保存 · 尚未采用"}</span>
                  {!recording.applied ? (
                    latestScript?.selection_status === "confirmed" && latestScript.sections.length === 1 ?
                      <button className="modern-primary-button" disabled={busy !== null} onClick={() => void adoptRecordedVoice(recording)} type="button">{busy === "recording-adopt" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}采用这段录音</button>
                      : <p>当前文案不是单段确认稿，无法确定录音对应关系；请先整理为一个明确段落。</p>
                  ) : null}
                </article>
              ))}
              {!task.can_revise_script ? (
                workspace.plan.selected_voice ? <p>已选择授权声音。如需生成配音，可在连续制作中确认要求后继续；本机录音也可独立采用。</p>
                  : runnableVoices.length ? <div className={styles.voiceSelection}><label className={styles.field}><span>用于生成配音的授权声音</span><AppSelect onValueChange={newValue => setSelectedVoiceId(newValue)} value={selectedVoiceId ?? ""}>{runnableVoices.map((voice) => <AppSelectOption key={`${voice.profile_id}:${voice.version}`} value={voice.profile_id}>{voice.person_display_name} · {voice.version}</AppSelectOption>)}</AppSelect></label><button className="modern-primary-button" disabled={busy !== null || !selectedVoiceId} onClick={() => { const voice = runnableVoices.find((item) => item.profile_id === selectedVoiceId); if (voice) void run("voice-select", () => selectCreationTaskNarrationVoice(task.id, { voice_profile_id: voice.profile_id, voice_version: voice.version }), "声音已绑定，请确认文案和要求后开始连续制作。"); }} type="button">{busy === "voice-select" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}使用授权声音生成</button></div>
                    : <div className={styles.voiceMissing}><AlertCircle size={18}/><div><strong>还没有可用于生成配音的授权声音</strong><p>你仍可采用这次本人录音；使用声音模型时再登记获得授权的声音。</p></div><Link className="modern-primary-button" href={`/profiles?view=voices&task=${encodeURIComponent(task.id)}`}>添加声音</Link></div>
              ) : null}
            </div>
          </section>
        ) : null}
        {activeView === "narration" && workspace.narration ? <NarrationResultView busy={busy !== null} onGeneratePersonRender={generatePersonRender} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} workspace={workspace} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>准备视频剪辑</h2><p>在上方核对文案与要求后继续制作。</p></div></section> : null}
        {isEditor && workspace.montage ? <VideoEditingStage selectedOverlayId={selectedOverlayId} onSelectOverlay={selectOverlay} onDraftChange={()=>invalidateEditPreview()} busy={busy !== null} onPreview={input=>previewExactEdit(input)} audioTracks={workspace.montage.audio_timeline ? <AudioTimelineTracks sharedTimeScale onListenPosition={positionAudioListen} key={`${task.id}:${task.current_timeline_version_id}`} workspace={workspace} selectedId={selectedAudioId} playhead={audioPlayhead} onSelect={selectAudio} onPreview={input=>void previewAudio(input)} busy={busy !== null} /> : undefined} workspace={workspace} selectedId={selectedClipId} onSelect={selectClip} onPosition={selectPlayingClip} videoRef={montageVideoRef} initialTime={playheadRef.current ?? 0} renderAction={null} /> : null}

        {activeView === "subtitle_cover" && activeSubtitleTrack && workspace.montage ? <SubtitleWorkspace activeTrack={activeSubtitleTrack} busy={busy !== null} onDraftChange={() => invalidateEditPreview()} onPreviewExact={(input) => void previewExactEdit(input)} onSelectCover={selectCover} onSelectCue={selectCue} onSelectTrack={selectSubtitleTrack} onSaveCover={() => void saveCover()} onSaveSubtitle={(mode) => void saveSubtitle(mode)} onUndo={() => void undoTimeline()} onRedo={() => void undoTimeline(true)} selectedCover={selectedCover} selectedCueId={selectedCueId} workspace={workspace} /> : null}

        <div className={activeView === "review" ? styles.reviewPane : styles.flowContents} role={activeView === "review" ? "tabpanel" : undefined} id={`review-panel-result-${task.id}`} aria-labelledby={activeView === "review" ? `review-result-${task.id}` : undefined} hidden={activeView === "review" && reviewPane !== "result"}>
        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><h2>检查成片</h2><p>{subtitleSummary} · {workspace.cover_asset?.status === "valid" ? "封面已生成" : "封面尚待确认"}</p></div><div className={styles.reviewActions}><button ref={reviewActionsTrigger} className={styles.secondaryButton} aria-haspopup="dialog" aria-expanded={reviewActionsOpen} onClick={() => setReviewActionsOpen(true)} type="button">调整成片<ChevronDown size={14}/></button>
        <HomeCardDialog kind="selection" title="调整成片" anchor={reviewActionsTrigger} open={reviewActionsOpen} onClose={() => setReviewActionsOpen(false)}><div className={styles.reviewMenu}><button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); openView("montage"); }} type="button">修改镜头</button><button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); setRecordingOpen(true); }} type="button"><Mic size={16}/>电脑录音替换旁白</button>{activeSubtitleTrack ? <button disabled={busy !== null} onClick={() => { setReviewActionsOpen(false); openView("subtitle_cover"); }} type="button">字幕封面</button> : null}</div></HomeCardDialog><button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div></div><div className={styles.evidenceList}><strong>检查清单 · {reviewReadyCount}/{reviewChecks.length} 项已就绪</strong>{reviewChecks.map((item) => <article key={item.title}><span>{item.ready ? "已就绪" : "待确认"}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div></article>)}</div><div className={styles.videoReview}><video key={task.current_timeline_version_id} aria-label="成片预览" controls preload="metadata" ref={montageVideoRef} onPause={(event) => selectPlayingClip(event.currentTarget)} onSeeked={(event) => selectPlayingClip(event.currentTarget)} onTimeUpdate={(event) => selectPlayingClip(event.currentTarget)} onLoadedMetadata={(event) => { event.currentTarget.currentTime = playheadRef.current ?? 0; }} src={apiUrl(`${workspace.montage.output_url}?version=${encodeURIComponent(task.current_timeline_version_id ?? "")}`)} /><div><p>请逐项检查字幕、声音和封面。</p></div></div></section> : null}
        {(activeView === "review" || activeView === "montage") && workspace.montage ? <FinalVideoQualityCheck taskId={task.id} versionId={task.current_timeline_version_id} outputUrl={workspace.montage.output_url} onLocate={locateQualityFinding} /> : null}
        {activeView === "review" && workspace.montage?.status === "rendered" && task.current_timeline_version_id ? <SaveVideoTemplateForm key={task.current_timeline_version_id} taskId={task.id} onEditingChange={setSavingTemplate} /> : null}
        </div>
        {activeView === "prepare" ? <PrepareMaterialsView workspace={workspace} /> : null}
        {activeView === "person" ? <PersonStageView busy={busy !== null} onGenerate={generatePersonRender} onOpenNarration={() => openView("narration")} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} workspace={workspace} /> : null}
        {activeView === "publish" ? <PublishStageView workspace={workspace} onOpenReview={() => openView("review")} /> : null}
        {task.confirmed_script_id && (executionPreview || execution?.job) && ["script", "narration", "montage"].includes(activeView) ? <CreationExecutionPanel workspace={workspace} compact preview={executionPreview} execution={execution} busy={busy !== null} onStart={() => executionPreview && void changeExecution(() => startCreationExecution(task.id, executionPreview))} onResume={() => void changeExecution(() => resumeCreationExecution(task.id))} onCancel={() => void changeExecution(() => cancelCreationExecution(task.id))} onOpenMontage={() => openView("montage")} /> : null}
        {recordingOpen ? <HomeCardDialog title="电脑录音" onClose={() => setRecordingOpen(false)}><DesktopVoiceRecorder scriptText={latestScript?.sections.map((section) => section.content).join("\n")} onRecorded={submitRecordedVoice} onCancel={() => setRecordingOpen(false)} /></HomeCardDialog> : null}
        </Tabs.Panel>
      </Tabs.Root>
    </AppShell>
  );
}


function formatDuration(seconds: number | null): string {
  if (seconds === null) return "未知";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function jobStatusLabel(status: string): string {
  if (status === "queued") return "已安排，等待设备";
  if (status === "running") return "正在处理";
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  if (status === "cancelled") return "已取消";
  if (status === "interrupted") return "已中断";
  return status;
}

/** 作业类型也用中文说明，不把内部标识直接丢给用户。 */
function jobKindLabel(kind: string): string {
  if (kind === "creation_task_montage_render") return "成片输出";
  if (kind === "creation_task_media") return "媒体预览与处理";
  if (kind === "creation_task_execution") return "整条制作";
  if (kind === "person_render") return "人物形象生成";
  if (kind === "creation_task_person_edit") return "局部人物修改";
  if (kind === "creation_task_narration_edit") return "局部配音修改";
  if (kind === "material_analysis") return "素材分析";
  return "其他作业";
}

/**
 * 作业进度读后台已经落库的作业行：重开应用读到同一批记录，页面关闭不改变它们。
 * 作业还在排队或执行时按固定间隔重新读取，结束后停止；切换任务时丢弃旧结果。
 * 读不到就明说并给出重试，不静默当成没有作业。
 */
function JobProgressPanel({ task, taskId, refreshToken, pendingEditJobId, onEditSettled, showLiveStatus, mediaJob, onCancelMedia, selectedRefs, restoreBusy, onRestoreMedia, recoveredMedia }: {
  selectedRefs: string[];
  restoreBusy: boolean;
  onRestoreMedia: (job: TaskMediaJob) => void;
  recoveredMedia: { job: TaskMediaJob; result: TaskMediaResultMap[keyof TaskMediaResultMap] } | null;
  mediaJob: TaskMediaJob | null;
  onCancelMedia: () => void;
  showLiveStatus: boolean;
  task: CreationTask;
  taskId: string;
  refreshToken: string;
  pendingEditJobId: string | null;
  onEditSettled: (jobId: string, status: string) => Promise<void>;
}) {
  const settledCallback = useRef(onEditSettled);
  settledCallback.current = onEditSettled;
  const [progress, setProgress] = useState<TaskJobProgress | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [mediaPage, setMediaPage] = useState<TaskMediaJobPage | null>(null);
  const [mediaReadError, setMediaReadError] = useState<string | null>(null);
  const [mediaCursor, setMediaCursor] = useState<string | null>(null);
  const [mediaRetry, setMediaRetry] = useState(0);
  const [mediaLoading, setMediaLoading] = useState(false);
  useEffect(() => {
    if (!detailsOpen) return;
    const controller = new AbortController();
    setMediaLoading(true); setMediaReadError(null);
    void listCreationTaskMediaJobs(taskId, mediaCursor, controller.signal).then(page => {
      if (!controller.signal.aborted) { setMediaPage(page); setMediaLoading(false); }
    }).catch(cause => {
      if (!controller.signal.aborted) { setMediaReadError(errorDetails(cause).message); setMediaLoading(false); }
    });
    return () => controller.abort();
  }, [taskId, detailsOpen, mediaCursor, mediaRetry]);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    setReadError(null);
    async function read() {
      window.clearTimeout(timer);
      try {
        const result = await getTaskJobProgress(taskId);
        if (!active || result.task_id !== taskId) return;
        setProgress(result);
        setReadError(null);
        const edit = result.items.find(item => item.id === pendingEditJobId);
        if (edit && ["succeeded", "failed", "cancelled"].includes(edit.status)) {
          await settledCallback.current(edit.id, edit.status);
          if (!active) return;
        }
        // 只有还在排队或执行的作业才继续读；结束或失败后停止轮询。
        const status = result.current?.status;
        if (status === "queued" || status === "running" || status === "interrupted" || edit?.status === "queued" || edit?.status === "running" || edit?.status === "interrupted") {
          timer = window.setTimeout(() => void read(), 2500);
        }
      } catch (cause) {
        if (!active) return;
        setReadError(errorDetails(cause).message);
      }
    }
    void read();
    return () => { active = false; window.clearTimeout(timer); };
    // 开始/重试制作后作业事实会变，重新读一次；空态不会一直轮询。
  }, [taskId, refreshToken, retryTick, pendingEditJobId]);
  async function cancelCurrentMedia() {
    if (!progress?.current) return;
    try { await cancelCreationTaskMediaJob(taskId, progress.current.id); setRetryTick(value => value + 1); }
    catch (cause) { setReadError(errorDetails(cause).message); }
  }
  const recoveryCandidates = mediaPage?.items.filter(item => taskMediaJobMatchesContext(item, task.current_timeline_version_id, selectedRefs)) ?? [];
  const current = progress?.current ?? null;
  const live = current?.status === "running" || current?.status === "queued";
  return <section aria-label="作业进度" className={styles.jobProgress}>
    {mediaJob && mediaJob.status !== "succeeded" ? <span role="status">{taskMediaJobLabel(mediaJob)}{["queued", "running", "interrupted"].includes(mediaJob.status) ? <button type="button" onClick={onCancelMedia}>取消预览</button> : null}</span> : null}
    {showLiveStatus && live ? <span>{jobKindLabel(current.kind)} · {jobStatusLabel(current.status)} · 进度 {current.progress}%</span> : showLiveStatus && (current?.status === "failed" || current?.status === "interrupted") ? <span>后台有步骤需要处理</span> : null}
    {readError ? <span role="status">后台记录更新未完成</span> : null}
    <button type="button" aria-haspopup="dialog" aria-expanded={detailsOpen} onClick={() => setDetailsOpen(true)}>运行详情</button>
    <HomeCardDialog kind="detail" title="运行详情" open={detailsOpen} onClose={() => setDetailsOpen(false)}>
      {readError ? <p role="alert" className={styles.jobRefreshError}>最新一次刷新失败：{readError} <button onClick={() => setRetryTick(value => value + 1)} type="button">重试</button></p> : null}
      <section aria-label="可恢复的媒体预览">
        <h3>恢复媒体预览</h3>
        {mediaLoading ? <p role="status">正在读取预览记录…</p> : null}
        {mediaReadError ? <p role="alert">预览记录未读取：{mediaReadError}<button type="button" onClick={() => setMediaRetry(value => value + 1)}>重读预览记录</button></p> : null}
        {!mediaLoading && !mediaReadError && mediaPage ? <>
          {recoveryCandidates.length ? <ol>{recoveryCandidates.map(item => <li key={item.job_id}>
            <span>{({ edit_preview: "修改预览", audio_listen: "音频试听", waveform: "音频波形", recording: "本机录音", audio_source: "导入音频" })[item.result_kind]} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("zh-CN")}</time> · {taskMediaJobLabel(mediaJob?.job_id === item.job_id ? mediaJob : item)}</span>
            <button type="button" disabled={restoreBusy} onClick={() => onRestoreMedia(item)}>恢复这次预览</button>
          </li>)}</ol> : <p>这一页没有符合当前版本与选择的预览记录。</p>}
          {mediaPage.next_cursor ? <button type="button" onClick={() => setMediaCursor(mediaPage.next_cursor)}>查看更多预览记录</button> : null}
          {mediaCursor ? <button type="button" onClick={() => setMediaCursor(null)}>回到最新预览记录</button> : null}
        </> : null}
        {recoveredMedia ? <RecoveredMediaResult job={recoveredMedia.job} result={recoveredMedia.result} /> : null}
      </section>
      {mediaJob ? <p>{taskMediaJobLabel(mediaJob)}{mediaJob.repair_action ? ` · ${mediaJob.repair_action}` : ""}</p> : null}
      {current ? <p>{jobKindLabel(current.kind)} · {jobStatusLabel(current.status)} · 进度 {current.progress}% · 已耗时 {formatDuration(current.elapsed_seconds)} · 更新于 {new Date(current.updated_at).toLocaleString("zh-CN")}</p> : <p>{progress ? "这个任务还没有后台作业记录。" : "正在读取后台记录…"}</p>}
      {live && current.kind === "creation_task_media" && current.id !== mediaJob?.job_id ? <button type="button" onClick={() => void cancelCurrentMedia()}>取消媒体处理</button> : null}
      {live ? <p>预计等待：{formatDuration(current.wait_estimate_seconds)} · 预计处理：{formatDuration(current.processing_estimate_seconds)}{current.estimate_basis ? `（依据：${current.estimate_basis}）` : current.wait_estimate_seconds === null && current.processing_estimate_seconds === null ? "（尚无可信样本，无法预计）" : ""}</p> : null}
      {current && (current.status === "failed" || current.status === "interrupted" || current.status === "cancelled" && Boolean(current.failure_reason || current.repair_action)) ? <div role="alert" className={styles.jobFailure} aria-label="当前作业失败信息"><strong>{current.status === "failed" ? "当前作业失败" : current.status === "interrupted" ? "当前作业已中断" : "当前作业未完成"}</strong>{current.failure_reason ? <p>失败原因：{creationTaskFailureReason(current.failure_reason, current.kind)}{current.impact ? ` · ${current.impact}` : ""}</p> : null}{current.repair_action ? <p>下一步：{creationTaskRepairAction(current.repair_action)}</p> : null}{current.failure_reason ? <details><summary>后台原始记录</summary><p>{current.failure_reason}</p></details> : null}</div> : null}
      {progress && progress.items.length > 1 ? <details><summary>查看历史作业（{progress.items.length} 条）</summary><ol>{progress.items.map(item => <li key={item.id}>{jobKindLabel(item.kind)} · {jobStatusLabel(item.status)} · 进度 {item.progress}% · 已耗时 {formatDuration(item.elapsed_seconds)} · 更新于 {new Date(item.updated_at).toLocaleString("zh-CN")}{item.failure_reason ? ` · 失败原因：${item.failure_reason}${item.repair_action ? ` · 下一步：${item.repair_action}` : ""}` : ""}</li>)}</ol></details> : null}
      <details className={styles.traceDetails}><summary>任务版本与时间</summary><p>版本 {task.timeline_sequence}</p><p>最近更新：<time dateTime={task.updated_at}>{new Date(task.updated_at).toLocaleString("zh-CN")}</time></p><p>恢复记录：{task.recovery_point}</p></details>
    </HomeCardDialog>
  </section>;
}

function RecoveredMediaResult({ job, result }: { job: TaskMediaJob; result: TaskMediaResultMap[keyof TaskMediaResultMap] }) {
  if (job.result_kind === "audio_listen") return <audio aria-label="恢复的音频试听" controls preload="metadata" src={apiUrl((result as TaskMediaResultMap["audio_listen"]).audio_url)} />;
  if (job.result_kind === "audio_source") {
    const source = result as TaskMediaResultMap["audio_source"];
    return <p role="status">已读取音频：{source.name} · {(source.duration_ms / 1000).toFixed(1)} 秒；尚未添加到音轨。</p>;
  }
  if (job.result_kind === "recording") return <p role="status">录音结果已读取；请在已有录音列表查看，尚未采用。</p>;
  if (job.result_kind === "waveform") {
    const wave = result as TaskMediaResultMap["waveform"];
    const stride = Math.max(1, Math.ceil(wave.peaks.length / 240));
    const points: string[] = [];
    for (let i = 0; i < wave.peaks.length; i += stride) {
      const peak = Math.max(...wave.peaks.slice(i, i + stride));
      const x = i / Math.max(1, wave.peaks.length) * 240;
      points.push(`M${x},${16 - peak * 15}V${16 + peak * 15}`);
    }
    return <div aria-label="恢复的音频波形"><svg viewBox="0 0 240 32" role="img" aria-label="实际音频波形"><path d={points.join(" ")} stroke="currentColor" /></svg><p>每个采样点覆盖 {wave.seconds_per_peak} 秒。</p></div>;
  }
  return null;
}
