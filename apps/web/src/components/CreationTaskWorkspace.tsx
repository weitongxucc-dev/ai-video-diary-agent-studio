"use client";
import { GraphicOverlayEditor } from "./GraphicOverlayEditor";
import { DesktopVoiceRecorder } from "./DesktopVoiceRecorder";
import { ExecutionQueueStatus } from "./ExecutionQueueStatus";

import Link from "next/link";
import NextImage from "next/image";
import {
  AlertCircle,
  Undo2,
  Redo2,
  Check,
  CheckCircle2,
  Captions,
  Clapperboard,
  FileText,
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
import { useEffect, useMemo, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { AppShell } from "@/components/AppShell";
import {
  apiUrl,
  confirmCreationTaskEdit,
  confirmCreationTaskScript,
  confirmCreationTaskTopic,
  createCreationTaskMontagePlan,
  CreationPlanApiError,
  generateCreationTaskNarration,
  generateCreationTaskPersonRender,
  generateCreationTaskScript,
  generateCreationTaskTopics,
  getCreationTaskMontageClipCandidates,
  getCreationTaskTopics,
  getCreationTaskWorkspace,
  getProductionMaterials,
  initializeCreationTaskTimeline,
  getCreationExecution,
  getCreationTaskAssistantHistory,
  previewCreationExecution,
  startCreationExecution,
  resumeCreationExecution,
  cancelCreationExecution,
  getRuntimeSettings,
  previewCreationTaskEdit,
  sendCreationTaskAssistantTurn,
  previewCompoundSelection,
  previewCreationTaskExactEdit,
  renderCreationTaskMontage,
  selectCreationTaskNarrationVoice,
  undoCreationTaskTimeline,
  redoCreationTaskTimeline,
} from "@/lib/api";
import type {
  CreationTask,
  CreationTaskExactEditInput,
  CreationTaskEditPreviewResponse,
  CreationTaskNarrationSegment,
  CreationTaskScript,
  CreationTaskSubtitleCue,
  CreationTaskSubtitleTrack,
  CreationTaskTopicsResponse,
  CreationTaskWorkspace as WorkspaceState,
  CreationExecutionPreview,
  CreationExecutionStatus,
  CreationTaskAssistantHistory,
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

type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "execution" | "voice-select" | "narration" | "person-render" | "plan" | "clip" | "render" | "save" | "assistant" | "edit-preview" | "edit-confirm" | "undo";

type WorkspaceView = CreationTask["stage"] | "subtitle_cover" | "prepare" | "publish";

type SelectedEditContext = {
  kind: "narration" | "cue" | "clip" | "cover" | "audio" | "overlay";
  id: string;
  label: string;
  placeholder: string;
  ref: string;
};

const stageOrder: WorkspaceView[] = ["topic", "script", "narration", "montage", "subtitle_cover", "review"];
const productStages = [
  { id: "prepare", label: "资料", title: "资料准备" },
  { id: "topic", label: "选题", title: "选题" },
  { id: "script", label: "文案", title: "文案" },
  { id: "narration", label: "配音", title: "配音" },
  { id: "montage", label: "剪辑", title: "视频剪辑" },
  { id: "subtitle_cover", label: "字幕封面", title: "字幕与封面" },
  { id: "review", label: "输出", title: "检查与输出" },
  { id: "publish", label: "发布", title: "发布" },
] as const;

const stageLabels = {
  prepare: "资料",
  topic: "选题",
  script: "文案",
  narration: "配音",
  montage: "视频剪辑",
  subtitle_cover: "字幕与封面",
  review: "检查成片",
  publish: "发布",
};

function stageIndex(stage: WorkspaceView) {
  return stageOrder.indexOf(stage);
}

function defaultWorkspaceView(workspace: WorkspaceState): WorkspaceView {
  const hasValidSubtitle = workspace.subtitle_tracks.some((track) => track.status === "valid");
  const hasValidCover = workspace.cover_asset?.status === "valid";
  if (workspace.task.stage === "review" && workspace.montage?.output_url && hasValidSubtitle && !hasValidCover) {
    return "subtitle_cover";
  }
  return workspace.task.stage;
}

type ProductStageState = "done" | "current" | "upcoming";

function productStageState(
  stageId: typeof productStages[number]["id"],
  task: CreationTask,
  subtitleCoverComplete: boolean,
): { state: ProductStageState; detail: string } {
  if (stageId === "prepare") return { state: "done", detail: "已完成" };
  if (stageId === "publish") return { state: "upcoming", detail: "未接入" };

  if (task.stage === "review" && stageId === "subtitle_cover" && !subtitleCoverComplete) {
    return { state: "current", detail: "待补封面" };
  }
  if (task.stage === "review" && stageId === "review" && !subtitleCoverComplete) {
    return { state: "upcoming", detail: "待上一步" };
  }

  const targetIndex = stageIndex(stageId);
  const currentIndex = stageIndex(task.stage);
  if (targetIndex < currentIndex) return { state: "done", detail: "已完成" };
  if (targetIndex === currentIndex) return { state: "current", detail: "当前" };
  return { state: "upcoming", detail: "未开始" };
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

function Inspector({ activeView, busy, workspace, error, onPreparePersonEdit, selectedContext }: { activeView: WorkspaceView; busy: boolean; workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null; onPreparePersonEdit: (clipId: string, command: string) => void; selectedContext: SelectedEditContext | null }) {
  const { task, plan, selected_topic: topic, narration, montage } = workspace;
  const latestScript = workspace.scripts[0] ?? null;
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
  const lipsyncLabel = (modelId: string | null | undefined) => modelId?.startsWith("latentsync-")
    ? "高精度 · LatentSync"
    : modelId?.toLowerCase().includes("musetalk") ? "低精度 · MuseTalk" : modelId ? "其他口型" : "未配置";
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
      {viewingCurrentStage && error ? <section className={styles.blocker} role="alert"><AlertCircle size={17} /><div><strong>{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div></section> : null}
      {viewingCurrentStage && task.stage_reason ? <section className={styles.blocker}><AlertCircle size={17} /><div><strong>{task.stage_impact ?? task.stage_reason}</strong>{task.stage_repair_action ? <p>处理方式：{task.stage_repair_action}</p> : null}</div></section> : null}
      {selectedNarration ? <section aria-label="已选配音详情"><span className={styles.eyebrow}>已选配音</span><h3>{selectedContext?.label}</h3><p>{selectedNarration.text}</p><dl><div><dt>片段时长</dt><dd>{selectedNarration.duration_seconds?.toFixed(1) ?? "—"} 秒</dd></div><div><dt>声音版本</dt><dd>{selectedNarration.voice_version}</dd></div><div><dt>模型</dt><dd>{selectedNarration.model_id}</dd></div><div><dt>状态</dt><dd>{narrationSegmentStatus(selectedNarration.status)}</dd></div></dl></section> : null}
      {selectedClip ? <section aria-label="已选镜头详情"><span className={styles.eyebrow}>已选镜头</span><h3>{selectedContext?.label}</h3><dl><div><dt>用途</dt><dd>{selectedClip.person_render_id ? "人物口型" : selectedClip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{selectedClip.timeline_start_seconds.toFixed(1)}–{selectedClip.timeline_end_seconds.toFixed(1)} 秒</dd></div><div><dt>人物版本</dt><dd>{selectedPersonRender ? `${selectedPersonRender.person_profile_id} · ${selectedPersonRender.person_version}` : selectedClip.person_render_id ? "未读取到人物片段记录" : plan.selected_voice ? `${plan.selected_voice.person_profile_id} · ${plan.selected_voice.person_version}` : "当前任务人物"}</dd></div>{selectedPersonRender ? <><div><dt>声音版本</dt><dd>{selectedPersonRender.voice_profile_id} · {selectedPersonRender.voice_version}</dd></div><div><dt>语言</dt><dd>{selectedPersonNarration?.language === "zh" ? "中文" : "未记录"}</dd></div><div><dt>口型效果</dt><dd>{lipsyncLabel(selectedPersonRender.model_id)}</dd></div><div><dt>动作</dt><dd>{selectedPersonRender.motion === "natural" ? "自然（固定）" : selectedPersonRender.motion}</dd></div><div><dt>表情</dt><dd>{selectedPersonRender.expression === "neutral" ? "中性（固定）" : selectedPersonRender.expression}</dd></div><div><dt>口型偏移</dt><dd>自动匹配（暂不可手动调整）</dd></div></> : null}<div><dt>片段状态</dt><dd>{personRenderStatus}</dd></div><div><dt>锁定</dt><dd>{selectedClip.locked ? "已锁定" : "可调整"}</dd></div><div><dt>当前口型</dt><dd>{lipsyncLabel(personRenderCapability.model_id)} · {personRenderCapability.available ? "可用" : "不可用"}</dd></div></dl>{!personRenderCapability.available ? <div aria-label="人物口型不可用原因" className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{personRenderCapability.message}</p>{personRenderCapability.repair_action ? <small>处理方式：{personRenderCapability.repair_action}</small> : null}</div> : null}{selectedPersonRender ? <p className={styles.personCapability}>动作/表情沿用原人物素材，口型匹配当前配音。</p> : null}{!selectedClip.person_render_id ? <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available} onClick={() => onPreparePersonEdit(selectedClip.id, "这一段换成当前人物出镜")} type="button"><UserRound size={15} />换成当前人物出镜</button> : <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available || !personRenderCanRegenerate} onClick={() => onPreparePersonEdit(selectedClip.id, "这里只重做口型，其他都不要动")} type="button"><UserRound size={15} />重新对口型</button>}</section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>声音与授权</span><dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{narration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl></section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>露脸</dt><dd>目标 {montage.presenter_share_target ?? 0}% · 实际 {montage.presenter_share_actual ?? 0}%</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div></dl></section> : null}
      {activeView === "prepare" ? <section><span className={styles.eyebrow}>本次素材</span><dl><div><dt>方案匹配</dt><dd>{plan.material_matches.length} 个片段</dd></div><div><dt>已审核素材</dt><dd>{plan.approved_asset_count} 个</dd></div><div><dt>已审核镜头</dt><dd>{plan.approved_clip_count} 个</dd></div></dl></section> : null}
      {activeView === "publish" ? <section><span className={styles.eyebrow}>发布能力</span><dl><div><dt>平台发布</dt><dd>未接入</dd></div><div><dt>定时发布</dt><dd>未接入</dd></div><div><dt>发布后监控</dt><dd>未接入</dd></div></dl></section> : null}
    </div>
  );
}

function capabilityLabel(available: boolean) {
  return available ? "可用" : "不可用";
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
      <div className={styles.sectionHeading}><div><span>已确认选题</span><h2>{topic.title}</h2><p>{topic.angle}</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15} />已确认</span></div>
      <div className={styles.artifactGrid}>
        <section><strong>目标观众</strong><p>{topic.audience}</p></section>
        <section><strong>内容结构</strong><ol>{topic.outline.map((item) => <li key={item}>{item}</li>)}</ol></section>
      </div>
      <div className={styles.evidenceList}><strong>来源证据</strong>{workspace.topic_evidence.length ? workspace.topic_evidence.map((item) => <article key={item.id}><span>{item.kind === "knowledge" ? "知识库" : "信息源"}</span><div><strong>{item.title}</strong><small>{item.authorization_or_license}</small></div></article>) : <p>当前选题没有返回可展示的来源条目。</p>}</div>
    </section>
  );
}

function ConfirmedScriptView({ script }: { script: CreationTaskScript }) {
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><span>已确认文案</span><h2>{script.title}</h2><p>这里只回看已经持久化的版本；重新生成必须从明确的修订动作开始。</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15} />已确认</span></div>
      <div className={styles.scriptSections}>{script.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><p className={styles.scriptCopy}>{section.content}</p><p><strong>画面建议：</strong>{section.visual_prompt}</p><p><strong>来源证据：</strong>{section.evidence_reference_ids.join("、") || "未单独标注"}</p></article>)}</div>
    </section>
  );
}

function narrationSegmentStatus(status: CreationTaskNarrationSegment["status"]) {
  if (status === "ready") return "可试听";
  if (status === "generating") return "正在生成";
  if (status === "pending") return "等待生成";
  return "生成失败";
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
  const selectedRender = workspace.person_renders.find(
    (render) => render.narration_segment_id === selectedSegmentId,
  ) ?? null;
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><span>已生成配音</span><h2>试听当前声音版本</h2><p>{narration.model_id} · {narration.duration_seconds.toFixed(1)} 秒 · {narration.sample_rate} Hz</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15} />已生成</span></div>
      <audio className={styles.audioPreview} controls preload="metadata" src={apiUrl(`/api/creation-tasks/${workspace.task.id}/narration/audio`)} />
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
      <div className={styles.personRenderPanel}>
        <div>
          <UserRound size={18} />
          <div><strong>人物口型预览</strong><p>选择一段配音，用当前人物版本检查口型效果；确认编辑后才会进入成片时间线。</p></div>
        </div>
        {!selectedSegmentId ? <span>先选择上方一段配音</span> : null}
        {selectedSegmentId && !selectedRender ? <button className="modern-primary-button" disabled={busy || !workspace.plan.selected_voice} onClick={() => onGeneratePersonRender(selectedSegmentId)} type="button">生成人物口型预览</button> : null}
        {selectedRender?.status === "running" || selectedRender?.status === "pending" ? <span><LoaderCircle className="is-spinning" size={15} />正在生成人物口型预览</span> : null}
        {selectedRender?.status === "failed" ? <div className={styles.personRenderFailure}><strong>生成没有完成</strong><p>{selectedRender.impact ?? selectedRender.failure_reason}</p>{selectedRender.repair_action ? <small>{selectedRender.repair_action}</small> : null}<button className="modern-primary-button" disabled={busy} onClick={() => onGeneratePersonRender(selectedSegmentId!)} type="button">重新生成</button></div> : null}
        {selectedRender?.status === "ready" && selectedRender.media_url ? <div className={styles.personRenderPreview}><video controls preload="metadata" src={apiUrl(selectedRender.media_url)} /><p>{selectedRender.model_id} · {selectedRender.duration_seconds?.toFixed(1)} 秒 · 人物 {selectedRender.person_version}</p></div> : null}
      </div>
    </section>
  );
}

function ScriptEditor({ script, busy, repairing, onConfirm }: { script: CreationTaskScript; busy: boolean; repairing: boolean; onConfirm: (script: CreationTaskScript) => void }) {
  const [draft, setDraft] = useState(script);
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><span>{repairing ? "文案修订" : "文案编辑"}</span><h2>{repairing ? "压缩并重新确认文案" : "检查事实和口播表达"}</h2><p>{repairing ? "调整正文后重新确认，再生成配音。" : "编辑不会再次调用模型；只有“确认文案”会推进到真实配音。"}</p></div></div>
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })} /><p><strong>画面建议：</strong>{section.visual_prompt}</p><p><strong>来源证据：</strong>{section.evidence_reference_ids.join("、")}</p></article>)}</div>
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}{repairing ? "确认修订文案" : "确认文案"}</button></div>
    </section>
  );
}

const executionStageLabels: Record<string, string> = {
  narration: "生成配音",
  montage_plan: "安排镜头与人物口型",
  render: "生成成片和字幕",
  cover: "准备封面",
};

type AssistantTurnRecord = { command: string; reply: string; context: string };

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

function CreationExecutionPanel({ preview, execution, busy, onStart, onResume, onCancel }: {
  preview: CreationExecutionPreview | null;
  execution: CreationExecutionStatus | null;
  busy: boolean;
  onStart: () => void;
  onResume: () => void;
  onCancel: () => void;
}) {
  const job = execution?.job;
  const active = job?.status === "queued" || job?.status === "running" || job?.status === "interrupted";
  const resumable = job?.status === "failed" || job?.status === "cancelled" || job?.status === "interrupted";
  const statusText = job?.status === "queued" ? "已排队" : job?.status === "running" ? "后台制作中"
    : job?.status === "failed" ? "制作停在失败步骤" : job?.status === "cancelled" ? "已在安全边界停止"
      : job?.status === "interrupted" ? "应用中断，等待继续" : job?.status === "succeeded" ? execution?.video_ready ? "成片已生成，等待检查" : "成片产物需要检查" : "等待开始";
  return <section aria-label="连续制作" className={styles.workSection}>
    <div className={styles.sectionHeading}><div><span>连续制作</span><h2>{statusText}</h2><p>开始后会依次处理配音、镜头和成片；每一步的结果以后台任务为准。</p></div></div>
    {!job && preview ? <>
      <div className={styles.scriptSections}><article><header><strong>这次使用的文案：{preview.script_title}</strong></header>{preview.script_sections.map((section, index) => <p key={index}>第 {index + 1} 段 · {section.title}：{section.content}</p>)}</article></div>
      <div className={styles.scriptSections}><article><header><strong>这次制作要求</strong></header>
        <p>画幅 {preview.requirements.aspect_ratio} · 目标时长 {preview.requirements.duration_target_seconds} 秒 · 露脸目标 {preview.requirements.presenter_share_target}%</p>
        <p>口型：{preview.requirements.lipsync_quality === "high" ? "高精度" : "较快档"} · 声音：{preview.requirements.selected_voice ?? "尚未选择"}</p>
        {Object.entries(preview.requirements.template_settings).length ? <details><summary>查看模板中的具体设置</summary><dl>{Object.entries(preview.requirements.template_settings).map(([name, value]) => <div key={name}><dt>{({ fit_mode: "画面适配", color_style: "调色", subtitle_style: "字幕样式" } as Record<string, string>)[name] ?? name}</dt><dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd></div>)}</dl></details> : null}
      </article></div>
      {!preview.requirements.selected_voice ? <p role="status">请先在下方选择已授权声音，再开始制作。</p> : null}
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !preview.requirements.selected_voice} onClick={onStart} type="button">{preview.requirements.selected_voice ? "确认以上文案和要求，开始制作" : "请先选择授权声音"}</button></div>
    </> : null}
    {job ? <div role="status"><ExecutionQueueStatus status={job.status} currentStage={execution?.current_stage ?? null} waitingReason={execution?.waiting_reason ?? null} statusUpdatedAt={execution?.status_updated_at ?? null} /><p>{job.failure_reason ? `原因：${job.failure_reason}` : ""}{job.repair_action ? ` 下一步：${job.repair_action}` : ""}</p>
      <ol>{execution?.stages.map((event, index) => <li key={`${event.at}-${index}`}>{executionStageLabels[event.stage] ?? event.stage}：{event.status === "running" ? "正在处理" : event.status === "completed" ? "已完成" : event.status === "reused" ? "沿用有效结果" : event.status === "failed" ? "失败" : "待完成"}{event.reason ? ` · ${event.reason}` : ""}</li>)}</ol>
      {execution?.video_ready ? <p>成片可检查；{execution.cover_status === "ready" ? "封面已准备好。" : execution.cover_message}</p> : null}
      {active && job.status !== "interrupted" ? <button className={styles.secondaryButton} disabled={busy} onClick={onCancel} type="button">停止制作</button> : null}
      {resumable ? <button className="modern-primary-button" disabled={busy} onClick={onResume} type="button">继续制作</button> : null}
    </div> : null}
  </section>;
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

function ContextCommandBar({
  compact = false,
  scriptMode = false,
  busy,
  command,
  onCancel,
  onCommandChange,
  onConfirm,
  onPreview,
  onExactPreview,
  onSelectionPreview,
  preview,
  assistantReply,
  assistantTurns,
  assistantModel,
  assistantConfigured,
  selectedContextLabel,
  selectedContextPlaceholder,
}: {
  compact?: boolean;
  scriptMode?: boolean;
  busy: BusyAction | null;
  command: string;
  onCancel: () => void;
  onCommandChange: (value: string) => void;
  onConfirm: () => void;
  onPreview: () => void;
  onExactPreview: () => void;
  onSelectionPreview: (indices:number[])=>void;
  preview: CreationTaskEditPreviewResponse | null;
  assistantReply: string | null;
  assistantTurns: { command: string; reply: string; context: string }[];
  assistantModel: string;
  assistantConfigured: boolean;
  selectedContextLabel: string | null;
  selectedContextPlaceholder: string;
}) {
  const ui = editorUi;
  const barRef = useRef<HTMLElement>(null);
  const steps=preview?.proposal.operation.operation_type==="EditCompoundTimeline" && preview.proposal.operation.payload.selection_enabled
    ? preview.proposal.operation.payload.steps as {index:number;command:string;enabled:boolean}[] : [];
  const originalSelection=steps.filter(step=>step.enabled).map(step=>step.index);
  const [chosen,setChosen]=useState<number[]|null>(null);
  const selectedSteps=chosen??originalSelection;
  const commandChanged=steps.length>0 && command.trim()!==preview?.intent.raw_command.trim();
  const selectionChanged=selectedSteps.length!==originalSelection.length||selectedSteps.some(index=>!originalSelection.includes(index));


  useEffect(() => {
    if (preview && !compact) barRef.current?.scrollIntoView?.({ block: "end" });
  }, [preview, compact]);

  return (
    <section
      aria-label={scriptMode ? "文案对话" : "修改视频"}
      className={editorUi.chatCommand}
      ref={barRef}
    >
      {!assistantReply ? <div className={editorUi.chatSuggestions}><h2>{scriptMode ? "这段文案怎么写？" : "这条怎么改？"}</h2><p>{scriptMode ? "说说想突出什么，草稿会放在右侧等你确认。" : selectedContextLabel ? `已选中${selectedContextLabel}` : "点选右边的镜头，再告诉我想怎么改。"}</p>{(scriptMode ? [["写个开头", "为当前选题写一个简短开头"], ["说得简单些", "把当前文案改得更容易理解"], ["再短一点", "缩短当前文案，保留关键事实和出处"]] : [["换个镜头", "换一个更合适的镜头，保留声音"], ["亮一点", "把当前镜头调亮一点"], ["字幕大些", "把字幕字号调大一点"]]).map(([label, value]) => <button key={label} type="button" disabled={busy !== null} onClick={() => onCommandChange(value)}>{label}</button>)}</div> : null}
      <div className={editorUi.chatEntry}>
        <span className={selectedContextLabel ? ui.contextSelection : ui.contextEmpty}>
          {scriptMode ? "当前文案" : selectedContextLabel ?? "先选对象"}
        </span>
        <label className={ui.commandInput}>
          {!compact ? <span>{scriptMode ? "说说文案想怎么写" : "说说怎么改"}</span> : null}
          <input
            aria-label={scriptMode ? "文案要求" : "局部修改要求"}
            disabled={busy !== null || selectionChanged}
            onChange={(event) => onCommandChange(event.target.value)}
            placeholder={steps.length ? "想保留哪项？" : selectedContextPlaceholder}
            value={command}
          />
        </label>
        <button
          className={compact ? editorUi.primary : "modern-primary-button"}
            disabled={busy !== null || !assistantConfigured || (Boolean(preview)&&!commandChanged) || !command.trim()}
          onClick={onPreview}
          type="button"
        >
          {busy === "assistant" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}
          发送
        </button>
        {selectedContextLabel && !scriptMode ? <button className={compact ? editorUi.button : styles.secondaryButton} disabled={busy !== null || !command.trim()} onClick={onExactPreview} type="button">预览修改</button> : null}
      </div>
      <div className="assistant-meta">
        <span>{assistantConfigured ? "仅传文字 · 可能收费" : "尚未连接"}</span>
        {assistantConfigured ? <details><summary>服务详情</summary><p>当前服务：{assistantModel}。只发送这段话、当前选择和相关文字，不发送原视频或声音。费用由服务方收取。</p></details> : <a href="/settings">去设置</a>}
        {assistantTurns.length > 1 ? <details className="assistant-history"><summary>往前看</summary><ol>{assistantTurns.slice(0, -1).map((turn, index) => <li key={index}><small>{turn.context}</small><p>{turn.command}</p><p>{turn.reply}</p></li>)}</ol></details> : null}
      </div>
      {scriptMode && assistantTurns.length ? <p className="assistant-reply">我：{assistantTurns.at(-1)?.command}</p> : null}
      {assistantReply ? <p className="assistant-reply" aria-live="polite">{assistantReply}</p> : null}
      {preview ? (
        <HomeCardDialog title="预览修改" onClose={onCancel}><div aria-label="变更预览" aria-live="polite" className={editorUi.dialogPreview}>
          <header><strong>{preview.proposal.summary}</strong><span>{(selectionChanged||commandChanged)?"重看效果":"待确认"}</span></header>
          {steps.length?<fieldset className={editorUi.propertySection}><legend>选择修改</legend>{steps.map(step=><label className={editorUi.actionRow} key={step.index}><input type="checkbox" aria-label={`保留修改${step.index+1}：${step.command}`} disabled={busy!==null||commandChanged} checked={selectedSteps.includes(step.index)} onChange={event=>setChosen(event.target.checked?[...selectedSteps,step.index]:selectedSteps.filter(index=>index!==step.index))}/><span>{step.index+1}. {step.command}</span></label>)}</fieldset>:null}
          {(selectionChanged||commandChanged)?<p role="status">内容已变，请重看效果。</p>:<div className={ui.previewDiff}>
            <section><small>修改前</small><strong>{preview.proposal.preview.before.label}</strong><span>{preview.proposal.preview.before.detail}</span>{preview.proposal.preview.before.image_url ? <a href={apiUrl(preview.proposal.preview.before.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改前字幕样式"><NextImage alt="修改前字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.before.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.before.video_url ? <PreviewVideo url={preview.proposal.preview.before.video_url} label="修改前画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { barRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current) media.pause(); }); }} /> : null}{preview.proposal.preview.before.audio_url ? <audio aria-label="修改前音频" controls onPlay={event => { barRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.before.audio_url)} /> : null}</section>
            <section><small>修改后</small><strong>{preview.proposal.preview.after.label}</strong><span>{preview.proposal.preview.after.detail}</span>{preview.proposal.preview.after.image_url ? <a href={apiUrl(preview.proposal.preview.after.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改后字幕样式"><NextImage alt="修改后字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.after.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.after.video_url ? <PreviewVideo url={preview.proposal.preview.after.video_url} label="修改后画面" muted={!(["EditCompoundTimeline", "RemoveSilence", "TrimClip"].includes(preview.proposal.operation.operation_type))} pauseOthers={current => { barRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media => { if (media !== current) media.pause(); }); }} /> : null}{preview.proposal.preview.after.audio_url ? <audio aria-label="修改后音频" controls onPlay={event => { barRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.after.audio_url)} /> : null}</section>
          </div>}
          {preview.proposal.preview.text_changes?.length ? <details className={editorUi.textChanges} open><summary>字幕变化 · {preview.proposal.preview.text_changes.length}</summary><ol>{preview.proposal.preview.text_changes.map(change => <li key={change.cue_id}><span>修改前：{change.before}</span><span>修改后：{change.after}</span></li>)}</ol></details> : null}
          <div className={ui.previewEffects}>
            <span><strong>需要重做</strong>{preview.proposal.invalidated_refs.map(ref => compact ? ref.endsWith(".mp4") ? "当前成片" : ref.endsWith(".srt") ? "字幕输出" : ref : ref).join("、") || "无"}</span>
            <span><strong>其他影响</strong>{preview.proposal.external_effects.join("；") || "无"}</span>
          </div>
          <div className={ui.previewActions}>
            <button className={compact ? editorUi.button : styles.secondaryButton} disabled={busy !== null} onClick={onCancel} type="button">取消</button>
            {selectionChanged?<button className={compact?editorUi.primary:"modern-primary-button"} type="button" disabled={busy!==null||commandChanged||!selectedSteps.length} onClick={()=>onSelectionPreview(selectedSteps)}>重看所选</button>:null}
            <button className={(selectionChanged||commandChanged) ? (compact?editorUi.button:styles.secondaryButton) : (compact ? editorUi.primary : "modern-primary-button")} disabled={busy !== null || selectionChanged || commandChanged || preview.proposal.status !== "proposed"} onClick={onConfirm} type="button">
              {busy === "edit-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}
              确认修改
            </button>
          </div>
        </div></HomeCardDialog>
      ) : null}
    </section>
  );
}

function cueEmphasisTerms(cue: CreationTaskSubtitleCue) {
  return cue.emphasis.join("、");
}

function emphasisTerms(text: string, rawTerms: string) {
  return rawTerms
    .split(/[、,]/)
    .map((term) => term.trim())
    .filter(Boolean)
    .filter((term) => text.includes(term));
}

function lineBreakPositions(text: string) {
  const positions: number[] = [];
  let compactOffset = 0;
  for (const character of text) {
    if (character === "\n") {
      positions.push(compactOffset);
    } else {
      compactOffset += 1;
    }
  }
  return positions;
}

function cueTextWithLineBreaks(cue: CreationTaskSubtitleCue) {
  if (!cue.line_breaks.length || cue.text.includes("\n")) return cue.text;
  const positions = new Set(cue.line_breaks);
  return Array.from(cue.text).reduce((result, character, index) => {
    const separator = positions.has(index) ? "\n" : "";
    return `${result}${separator}${character}`;
  }, "") + (positions.has(Array.from(cue.text).length) ? "\n" : "");
}

function SubtitleCueEditor({
  busy,
  cue,
  index,
  onDraftChange,
  onPreview,
  onSelect,
  selected,
  style,
  track,
}: {
  busy: boolean;
  cue: CreationTaskSubtitleCue;
  index: number;
  onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void;
  onSelect: () => void;
  selected: boolean;
  style: CreationTaskSubtitleTrack["style"];
  track: CreationTaskSubtitleTrack;
}) {
  const storedText = cueTextWithLineBreaks(cue);
  const [text, setText] = useState(storedText);
  const [terms, setTerms] = useState(cueEmphasisTerms(cue));
  const [start, setStart] = useState(String(cue.start_seconds));
  const [end, setEnd] = useState(String(cue.end_seconds));
  const [splitOffset, setSplitOffset] = useState(Math.max(1, Math.floor(Array.from(cue.text).length / 2)));
  const [splitTime, setSplitTime] = useState(Number(((cue.start_seconds + cue.end_seconds) / 2).toFixed(3)));
  const validTime = start.trim() !== "" && end.trim() !== "" && Number(start) >= 0 && Number(end) > Number(start);
  const changed = Number(start) !== cue.start_seconds || Number(end) !== cue.end_seconds || text !== storedText || terms !== cueEmphasisTerms(cue) || style.name !== track.style.name;

  return (
    <article className={[styles.subtitleCue, selected ? styles.selectedSubtitleCue : ""].filter(Boolean).join(" ")}>
      <button aria-label={`选择${track.language === "zh-CN" ? "中文" : "英文"}字幕 ${index + 1}`} aria-pressed={selected} className={styles.cueTime} onClick={onSelect} type="button">
        <strong>{cue.start_seconds.toFixed(1)}–{cue.end_seconds.toFixed(1)}</strong>
      </button>
      <div className={styles.cueFields} onFocus={onSelect}>
        <label><span>出现时间（秒）</span><input aria-label={`字幕 ${index + 1} 出现时间`} type="number" min="0" step="0.01" disabled={busy} value={start} onChange={(event) => { onDraftChange(); setStart(event.target.value); }} /></label>
        <label><span>结束时间（秒）</span><input aria-label={`字幕 ${index + 1} 结束时间`} type="number" min="0" step="0.01" disabled={busy} value={end} onChange={(event) => { onDraftChange(); setEnd(event.target.value); }} /></label>
        <label>
          <span>字幕文本与断行</span>
          <textarea aria-label={`字幕 ${index + 1} 文本`} disabled={busy} onChange={(event) => { onDraftChange(); setText(event.target.value); }} rows={2} value={text} />
        </label>
        <label>
          <span>强调词（用顿号分隔）</span>
          <input aria-label={`字幕 ${index + 1} 强调词`} disabled={busy} onChange={(event) => { onDraftChange(); setTerms(event.target.value); }} value={terms} />
        </label>
      </div>
      <details className={editorUi.propertySection}>
        <summary>拆分、合并与删除</summary>
        <label className={editorUi.field}>第几个字后拆分<input aria-label="字幕拆分文字位置" type="number" min="1" max={Array.from(cue.text).length - 1} disabled={busy || changed} value={splitOffset} onChange={event => { onDraftChange(); setSplitOffset(Number(event.target.value)); }} /></label>
        <label className={editorUi.field}>拆分时间（秒）<input aria-label="字幕拆分时间" type="number" step="0.001" min={cue.start_seconds + .04} max={cue.end_seconds - .04} disabled={busy || changed} value={splitTime} onChange={event => { onDraftChange(); setSplitTime(Number(event.target.value)); }} /></label>
        <div className={styles.cueAction}>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed || splitOffset < 1 || splitOffset >= Array.from(cue.text).length || splitTime < cue.start_seconds + .04 || splitTime > cue.end_seconds - .04} onClick={() => onPreview({operation_type:"SplitSubtitleCue", track_id:track.id, cue_id:cue.id, split_offset:splitOffset, split_time_seconds:splitTime})}>预览拆分</button>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed || index >= track.cues.length - 1} onClick={() => onPreview({operation_type:"MergeSubtitleCue", track_id:track.id, cue_id:cue.id})}>预览与下一条合并</button>
          <button type="button" className={styles.secondaryButton} disabled={busy || changed} onClick={() => onPreview({operation_type:"DeleteSubtitleCue", track_id:track.id, cue_id:cue.id})}>预览删除字幕</button>
        </div>
        {changed ? <small>先保存或还原上方文字与时间修改，再调整结构。</small> : null}
      </details>
      <div className={styles.cueAction}>
        {changed ? <small>待预览</small> : <small>已保存</small>}
        <button className={styles.secondaryButton} disabled={busy || !changed || !text.trim() || !validTime} onClick={() => onPreview({
          operation_type: "UpdateSubtitleCue",
          track_id: track.id,
          cue_id: cue.id,
          start_seconds: Number(start),
          end_seconds: Number(end),
          text,
          line_breaks: lineBreakPositions(text),
          emphasis: emphasisTerms(text, terms),
          style,
        })} type="button">预览字幕修改</button>
      </div>
    </article>
  );
}

function SubtitleStyleEditor({ style, busy, onDraftChange, onPreview }: {
  style: CreationTaskSubtitleTrack["style"]; busy: boolean; onDraftChange: () => void;
  onPreview: (input: CreationTaskExactEditInput) => void;
}) {
  const initial = { ...style, stroke_width: style.stroke_width ?? 3, stroke_color: style.stroke_color ?? "#000000",
    background_color: style.background_color ?? "#000000", background_opacity: style.background_opacity ?? 158 / 255 };
  const [draft, setDraft] = useState(initial);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const change = (patch: Partial<typeof draft>) => { onDraftChange(); setDraft({ ...draft, ...patch }); };
  return <details className={editorUi.propertySection}>
    <summary>全部字幕样式</summary>
    <div className={styles.cueFields}>
      <label><span>本机字体</span><input aria-label="字幕字体" disabled={busy} value={draft.font_family} onChange={event => change({font_family:event.target.value})} /></label>
      <label><span>字号</span><input aria-label="字幕字号" type="number" min="1" max="1920" disabled={busy} value={draft.font_size} onChange={event => change({font_size:Number(event.target.value)})} /></label>
      <label><span>位置</span><select aria-label="字幕位置" disabled={busy} value={draft.position} onChange={event => change({position:event.target.value as typeof draft.position})}><option value="top">顶部</option><option value="middle">居中</option><option value="bottom">底部</option></select></label>
      <label><span>双语顺序</span><select aria-label="字幕双语顺序" disabled={busy} value={draft.bilingual_order} onChange={event => change({bilingual_order:event.target.value as typeof draft.bilingual_order})}><option value="source_then_translation">原文在上</option><option value="translation_then_source">译文在上</option></select></label>
      {([["primary_color","文字颜色"],["emphasis_color","强调颜色"],["stroke_color","描边颜色"],["background_color","背景颜色"]] as const).map(([key,label]) => <label key={key}><span>{label}</span><input aria-label={`字幕${label}`} type="color" disabled={busy} value={draft[key]} onChange={event => change({[key]:event.target.value})} /></label>)}
      <label><span>描边宽度</span><input aria-label="字幕描边宽度" type="number" min="0" max="12" disabled={busy} value={draft.stroke_width} onChange={event => change({stroke_width:Number(event.target.value)})} /></label>
      <label><span>背景不透明度 {Math.round(draft.background_opacity*100)}%</span><input aria-label="字幕背景不透明度" type="range" min="0" max="100" disabled={busy} value={Math.round(draft.background_opacity*100)} onChange={event => change({background_opacity:Number(event.target.value)/100})} /></label>
    </div>
    <button className={styles.secondaryButton} type="button" disabled={busy || !changed || !draft.font_family.trim()} onClick={() => {
      const patch = Object.fromEntries(Object.entries(draft).filter(([key,value]) => key !== "enabled" && initial[key as keyof typeof initial] !== value));
      onPreview({operation_type:"ConfigureSubtitleTrack", style:patch});
    }}>预览字幕样式</button>
  </details>;
}

function SubtitleCoverView({
  activeTrack,
  busy,
  onDraftChange,
  onPreviewExact,
  onSelectCover,
  onSelectCue,
  onSelectTrack,
  onSaveCover,
  onSaveSubtitle,
  onUndo,
  selectedCover,
  selectedCueId,
  workspace,
}: {
  activeTrack: CreationTaskSubtitleTrack;
  busy: boolean;
  onDraftChange: () => void;
  onPreviewExact: (input: CreationTaskExactEditInput) => void;
  onSelectCover: () => void;
  onSelectCue: (cueId: string) => void;
  onSelectTrack: (trackId: string) => void;
  onSaveCover: () => void;
  onSaveSubtitle: (mode: "zh" | "en" | "zh-en") => void;
  onUndo: () => void;
  selectedCover: boolean;
  selectedCueId: string | null;
  workspace: WorkspaceState;
}) {
  const { cover_asset: cover, montage, subtitle_tracks: tracks } = workspace;
  const sourceTrack = tracks.find((track) => track.language === "zh-CN") ?? activeTrack;
  const translationTrack = tracks.find((track) => track.language === "en-US") ?? null;
  const [frameTime, setFrameTime] = useState(cover?.frame_time_seconds ?? 0);
  const [coverTitle, setCoverTitle] = useState(cover?.title ?? "");
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [saveSubtitleMode, setSaveSubtitleMode] = useState<"zh" | "en" | "zh-en">(
    activeTrack.language === "en-US" ? "en" : "zh",
  );
  const videoRef = useRef<HTMLVideoElement>(null);
  const chineseVisible = sourceTrack.language === "zh-CN" && sourceTrack.style.enabled !== false;
  const englishVisible = translationTrack?.status === "valid" && translationTrack.style.enabled !== false;
  const chineseSavable = tracks.some((track) => track.language === "zh-CN" && track.status === "valid" && track.cues.length > 0);
  const englishSavable = tracks.some((track) => track.language === "en-US" && track.status === "valid" && track.cues.length > 0);
  const selectedSavable = saveSubtitleMode === "zh" ? chineseSavable : saveSubtitleMode === "en" ? englishSavable : chineseSavable && englishSavable;
  const displayMode = chineseVisible ? englishVisible ? "zh-en" : "zh" : englishVisible ? "en" : "none";
  const duration = montage?.duration_seconds ?? 0;
  const translationCapability = workspace.subtitle_translation_capability;
  const frameCapability = workspace.cover_frame_capability;
  const currentCueIndex = Math.max(0, activeTrack.cues.findIndex((cue) => cue.id === selectedCueId));
  const currentCue = activeTrack.cues[currentCueIndex];
  const coverChanged = coverTitle !== (cover?.title ?? "") || frameTime !== (cover?.frame_time_seconds ?? 0);

  useEffect(() => {
    setFrameTime(cover?.frame_time_seconds ?? 0);
    setCoverTitle(cover?.title ?? "");
  }, [cover?.id, cover?.timeline_version_id, cover?.frame_time_seconds, cover?.title]);

  function seekFrame(next: number) {
    const bounded = Math.max(0, Math.min(duration, next));
    setFrameTime(bounded);
    if (videoRef.current) videoRef.current.currentTime = bounded;
  }

  function enableBilingual() {
    onSelectTrack(sourceTrack.id);
    onPreviewExact({
      operation_type: "ConfigureSubtitleTrack",
      track_id: sourceTrack.id,
      target_language: "en-US",
    });
  }

  return (
    <section aria-label="字幕与封面工作区" className={styles.subtitleCoverWorkspace}>
      <div className={styles.subtitleCoverToolbar}>
        <div aria-label="字幕语言" className={styles.segmentedControl} role="group">
          {([['none', '无字幕'], ['zh', '中文'], ['en', '英文'], ['zh-en', '中英双语']] as const).map(([mode, label]) => <button key={mode} type="button" aria-pressed={displayMode === mode}
            disabled={busy || displayMode === mode || ((mode === "zh" || mode === "zh-en") && !tracks.some(track => track.language === "zh-CN" && track.status === "valid")) || ((mode === "en" || mode === "zh-en") && translationTrack?.status !== "valid")}
            onClick={() => onPreviewExact({operation_type: "ConfigureSubtitleTrack", subtitle_display: mode})}>{label}</button>)}
          {translationTrack?.status !== "valid" ? <button type="button" disabled={busy || !translationCapability.available} onClick={enableBilingual}><Languages size={15} />生成英文字幕</button> : null}
        </div>
        <button className={styles.secondaryButton} disabled={busy || !workspace.task.can_undo} onClick={onUndo} type="button">撤销</button>
        <label>保存字幕语言
          <select aria-label="保存字幕语言" disabled={busy} value={saveSubtitleMode} onChange={(event) => setSaveSubtitleMode(event.target.value as "zh" | "en" | "zh-en")}>
            <option value="zh">中文 SRT</option>
            <option value="en">英文 SRT</option>
            <option value="zh-en">中英双语 SRT（一个文件，中文在前）</option>
          </select>
        </label>
        <button className={styles.secondaryButton} disabled={busy || !selectedSavable} onClick={() => onSaveSubtitle(saveSubtitleMode)} type="button"><Save size={15} />保存字幕</button>
      </div>

      {!selectedSavable ? <p className={styles.capabilityLine}>所选语言暂无有效字幕轨，请先检查或生成字幕。</p> : null}
      {!translationCapability.available && !translationTrack ? <p className={styles.capabilityLine} role="status"><AlertCircle size={15} />中英双语暂不可用：{translationCapability.message}{translationCapability.repair_action ? ` 处理方式：${translationCapability.repair_action}` : ""}</p> : null}
      <SubtitleStyleEditor key={sourceTrack.timeline_version_id} style={sourceTrack.style} busy={busy} onDraftChange={onDraftChange} onPreview={onPreviewExact} />

      <div className={styles.previewGrid}>
        <section className={styles.mediaPanel}>
          <header><span><Play size={15} />当前成片</span><small>{duration.toFixed(1)} 秒</small></header>
          <video aria-label="字幕成片预览" controls onLoadedMetadata={() => seekFrame(frameTime)} preload="metadata" ref={videoRef} src={apiUrl(montage?.output_url ?? "")} />
        </section>
        <div className={styles.subtitleTracks}>
          <div className={styles.trackTabs} role="tablist" aria-label="字幕轨道">
            {tracks.map((track) => <button aria-selected={track.id === activeTrack.id} disabled={busy} key={track.id} onClick={() => onSelectTrack(track.id)} role="tab" type="button"><Captions size={15} />{track.language === "zh-CN" ? "中文轨" : "英文轨"}<small>{track.cues.length} 条</small></button>)}
          </div>
          <details className={editorUi.propertySection}><summary>全文查找替换</summary><label className={editorUi.field}>查找<input aria-label="字幕查找文字" maxLength={96} value={findText} disabled={busy} onChange={event => { setFindText(event.target.value); onDraftChange(); }} /></label><label className={editorUi.field}>替换为<input aria-label="字幕替换文字" maxLength={96} value={replaceText} disabled={busy} onChange={event => { setReplaceText(event.target.value); onDraftChange(); }} /></label><button className={editorUi.button} type="button" disabled={busy || !findText || findText === replaceText} onClick={() => onPreviewExact({ operation_type: "ReplaceSubtitleText", track_id: activeTrack.id, find_text: findText, replacement_text: replaceText })}>预览全文替换</button><p>仅当前语言轨，确认前逐条查看；配音不变。</p></details>
          <label className={styles.cueSelection}><span>选择字幕</span><select aria-label="选择字幕" disabled={busy} onChange={(event) => {
            const cue = activeTrack.cues.find((item) => item.id === event.target.value);
            if (cue) {
              onSelectCue(cue.id);
              if (videoRef.current) videoRef.current.currentTime = cue.start_seconds;
            }
          }} value={currentCue?.id ?? ""}>{activeTrack.cues.map((cue, index) => <option key={cue.id} value={cue.id}>{index + 1} · {cue.start_seconds.toFixed(1)}–{cue.end_seconds.toFixed(1)} 秒 · {cue.text}</option>)}</select></label>
          <div aria-label={`${activeTrack.language === "zh-CN" ? "中文" : "英文"}字幕列表`} className={styles.subtitleCueList} role="tabpanel">
            {currentCue ? <SubtitleCueEditor busy={busy} cue={currentCue} index={currentCueIndex} key={JSON.stringify([activeTrack.id, activeTrack.timeline_version_id, activeTrack.style, currentCue])} onDraftChange={onDraftChange} onPreview={onPreviewExact} onSelect={() => onSelectCue(currentCue.id)} selected={selectedCueId === currentCue.id} style={activeTrack.style} track={activeTrack} /> : null}
          </div>
        </div>
      </div>
        <section className={[styles.coverPanel, selectedCover ? styles.selectedCover : ""].filter(Boolean).join(" ")} onFocus={onSelectCover}>
          <header><span><ImageIcon size={15} />封面</span>{cover?.status === "valid" ? <small>已生成</small> : null}</header>
          <div className={styles.coverBody}>
          {cover?.status === "valid" && cover.artifact_ref ? <NextImage alt={cover.title} height={1280} src={apiUrl(`/api/creation-tasks/${workspace.task.id}/covers/${cover.id}/image`)} unoptimized width={720} /> : null}
          <div className={styles.coverControls}>
            <label><span>成片画面时间（秒）</span><input aria-label="封面画面时间" disabled={busy} max={duration} min="0" onChange={(event) => { onDraftChange(); seekFrame(Number(event.target.value)); }} step="0.1" type="number" value={frameTime} /></label>
            <label><span>封面标题</span><input aria-label="封面标题" disabled={busy} onChange={(event) => { onDraftChange(); setCoverTitle(event.target.value); }} value={coverTitle} /></label>
            {coverChanged ? <small className={styles.coverDraft}>选帧或标题待生成{cover?.status === "valid" ? "；保存使用已生成的封面" : ""}</small> : null}
            <div className={styles.coverActions}>
            {cover?.status === "valid" && cover.artifact_ref ? <button className={styles.secondaryButton} disabled={busy} onClick={onSaveCover} type="button"><Save size={15} />保存封面</button> : null}
            <button className={styles.secondaryButton} disabled={busy || !frameCapability.available || !coverTitle.trim() || duration <= 0} onClick={() => {
              onSelectCover();
              onPreviewExact({
                operation_type: "GenerateCoverFromFrame",
                frame_time_seconds: frameTime,
                title: coverTitle.trim(),
              });
            }} type="button">从画面生成封面</button>
            </div>
          </div>
          </div>
        </section>
    </section>
  );
}

function materialAnalysisLabel(status: MaterialAsset["analysis_status"]) {
  if (status === "ready") return "已解析";
  if (status === "pending") return "待解析";
  if (status === "blocked") return "未解析";
  return "解析失败";
}

function materialReviewLabel(status: MaterialAsset["review_status"]) {
  if (status === "approved") return "已通过";
  if (status === "rejected") return "已拒绝";
  return "未审核";
}

function PrepareMaterialsView({ workspace }: { workspace: WorkspaceState }) {
  const [materials, setMaterials] = useState<MaterialAsset[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  useEffect(() => {
    let active = true;
    getProductionMaterials({ limit: 200 })
      .then((library) => {
        if (!active) return;
        setMaterials(library.assets);
        setHasMore(library.has_more);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setLoadError(cause instanceof Error ? cause.message : "素材读取失败");
      });
    return () => { active = false; };
  }, []);
  const matchesByAsset = useMemo(() => {
    const map = new Map<string, MaterialSegmentMatch>();
    for (const match of workspace.plan.material_matches) {
      const id = match.asset_ref.startsWith("asset:") ? match.asset_ref.slice("asset:".length) : match.asset_ref;
      map.set(id, match);
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
          <span>资料</span>
          <h2>本次任务素材</h2>
          <p>只读回看方案匹配到的素材，不会在这里改动素材或重新跑匹配。</p>
        </div>
        <span className={styles.artifactStatus}><CheckCircle2 size={15} />资料就绪</span>
      </div>
      <div className={styles.artifactGrid}>
        <section><strong>方案匹配</strong><p>{workspace.plan.material_matches.length} 个素材片段匹配当前目标{workspace.plan.material_rule_summary ? ` · ${workspace.plan.material_rule_summary}` : ""}</p></section>
        <section><strong>审核进度</strong><p>已审核素材 {workspace.plan.approved_asset_count} 个 · 已审核镜头 {workspace.plan.approved_clip_count} 个</p></section>
      </div>
      {loadError ? <div className={styles.blocker} role="alert"><AlertCircle size={17} /><span>素材读取失败：{loadError}</span></div> : null}
      <div className={styles.evidenceList}>
        <strong>本次素材列表</strong>
        {materials === null && !loadError ? <p>正在读取素材…</p> : null}
        {materials !== null && matchedAssets.length === 0 ? <p>暂无：方案尚未匹配到可用素材，或素材解析状态缺失。</p> : null}
        {matchedAssets.map((asset) => {
          const match = matchesByAsset.get(asset.id);
          const tags = asset.user_tags ?? [];
          return (
            <article key={asset.id}>
              <span>{match ? `匹配 ${Math.round(match.score * 100)}%` : "未匹配"}</span>
              <div>
                <strong>{asset.file_name}</strong>
                <small>{materialAnalysisLabel(asset.analysis_status)}{asset.duration_seconds !== null ? ` · ${asset.duration_seconds.toFixed(1)} 秒` : " · 时长未解析"} · {materialReviewLabel(asset.review_status)}</small>
                <small>{tags.length ? tags.join(" · ") : "暂无标签"}{asset.analysis_message ? ` · ${asset.analysis_message}` : ""}</small>
              </div>
            </article>
          );
        })}
        {hasMore ? <p>素材较多，此处只展示前 200 条匹配片段。</p> : null}
      </div>
    </section>
  );
}

function PublishStageView({ workspace, onOpenReview }: { workspace: WorkspaceState; onOpenReview: () => void }) {
  const ready = Boolean(workspace.montage?.output_url);
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}>
        <div>
          <span>发布</span>
          <h2>发布</h2>
          <p>真实发布依赖平台接口，尚未接入；本页不会把本地成片计为已发布。</p>
        </div>
      </div>
      <div className={styles.emptyAction}>
        <AlertCircle size={28} />
        <h2>平台发布能力未接入</h2>
        <p>平台发布、定时发布和发布后监控尚未接入。{ready ? "成片已生成，可以先保存到本地，再到平台上传。" : "完成“检查与输出”后，成片会出现在这里。"}</p>
        {ready ? <button className="modern-primary-button" onClick={onOpenReview} type="button">回到检查与输出</button> : null}
      </div>
    </section>
  );
}

export function CreationTaskWorkspace({ initialWorkspace, initialTopics, initialTasks }: Props) {
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [activeView, setActiveView] = useState<WorkspaceView>(() => defaultWorkspaceView(initialWorkspace));
  const [topics, setTopics] = useState(initialTopics);
  const [selectedTopicId, setSelectedTopicId] = useState(initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id ?? null);
  const [topicTitleDraft, setTopicTitleDraft] = useState(
    () => initialTopics?.candidates.find((candidate) => candidate.id === (initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id))?.title ?? "",
  );
  const [tasks, setTasks] = useState(initialTasks);
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const [error, setError] = useState<ReturnType<typeof errorDetails> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedVoiceId, setSelectedVoiceId] = useState<string | null>(
    initialWorkspace.plan.selected_voice?.profile_id
      ?? initialWorkspace.available_voices.find((voice) => voice.runnable)?.profile_id
      ?? null,
  );
  const [replacementClipId, setReplacementClipId] = useState<string | null>(null);
  const [replacementCandidates, setReplacementCandidates] = useState<MontageClipCandidate[]>([]);
  const [inserting, setInserting] = useState(false);
  const [editorDraft, setEditorDraft] = useState(false);
  const [clipDrafts, setClipDrafts] = useState<Record<string, VideoClipDraft>>({});
  const [audioPlayhead, setAudioPlayhead] = useState(0);
  const [selectedAudioId, setSelectedAudioId] = useState<string | null>(null);
  const [selectedOverlayId, setSelectedOverlayId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const montageVideoRef = useRef<HTMLVideoElement>(null);
  const playheadRef = useRef<number | null>(null);
  const editContextRevision = useRef(0);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const [selectedCover, setSelectedCover] = useState(false);
  const [activeSubtitleTrackId, setActiveSubtitleTrackId] = useState<string | null>(
    initialWorkspace.subtitle_tracks.find((track) => track.language === "zh-CN")?.id
      ?? initialWorkspace.subtitle_tracks[0]?.id
      ?? null,
  );
  const [selectedNarrationSegmentId, setSelectedNarrationSegmentId] = useState<string | null>(null);
  const [editCommand, setEditCommand] = useState("");
  const [editPreview, setEditPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [recordingOpen, setRecordingOpen] = useState(false);
  const [assistantReply, setAssistantReply] = useState<string | null>(null);
  const [assistantTurns, setAssistantTurns] = useState<AssistantTurnRecord[]>([]);
  const [assistantModel, setAssistantModel] = useState("外部文字模型待连接");
  const [assistantConfigured, setAssistantConfigured] = useState(false);
  const [executionPreview, setExecutionPreview] = useState<CreationExecutionPreview | null>(null);
  const [execution, setExecution] = useState<CreationExecutionStatus | null>(null);
  const [executionError, setExecutionError] = useState<ReturnType<typeof errorDetails> | null>(null);
  const executionSnapshot = useRef("");
  const pollExecutionRef = useRef<() => void>(() => undefined);
  useEffect(() => {
    void getRuntimeSettings().then((runtime) => {
      if (runtime.status.llm.configured) {
        setAssistantModel(`${runtime.status.llm.provider} / ${runtime.status.llm.model}`);
        setAssistantConfigured(true);
      }
    }).catch(() => undefined);
  }, []);
  const task = workspace.task;
  useEffect(() => {
    let active = true;
    setAssistantTurns([]);
    setAssistantReply(null);
    void getCreationTaskAssistantHistory(task.id).then((history) => {
      if (!active) return;
      const turns = assistantHistoryTurns(history.items);
      setAssistantTurns(turns);
      setAssistantReply(turns.at(-1)?.reply ?? null);
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
  }, [task.id, task.confirmed_script_id, workspace.plan.selected_voice?.profile_id, workspace.plan.selected_voice?.version]);
  const latestScript = workspace.scripts[0] ?? null;
  const runnableVoices = workspace.available_voices.filter((voice) => voice.runnable);
  const selectedTopicCandidate = topics?.candidates.find((candidate) => candidate.id === selectedTopicId) ?? null;
  const zhSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "zh-CN" && track.status === "valid");
  const enSubtitleValid = workspace.subtitle_tracks.some((track) => track.language === "en-US" && track.status === "valid");
  const coverValid = workspace.cover_asset?.status === "valid";
  const reviewChecks = [
    { title: "字幕", ready: zhSubtitleValid, detail: zhSubtitleValid ? (enSubtitleValid ? "中英双语轨已就绪" : "中文轨已就绪") : "字幕尚未生成或待确认" },
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

  function invalidateEditPreview() {
    editContextRevision.current += 1;
    setEditPreview(null);
    setAssistantReply(null);
  }

  async function refresh(preserveActiveView = false) {
    const next = await getCreationTaskWorkspace(task.id);
    invalidateEditPreview();
    setReplacementClipId(null);
    setReplacementCandidates([]);
    const retainedClip = next.montage?.clips.find(clip => clip.id === selectedClipId);
    const retainedOverlay=next.montage?.graphic_overlays?.find(item=>item.id===selectedOverlayId);
    playheadRef.current = retainedOverlay ? (retainedOverlay.start_seconds+retainedOverlay.end_seconds)/2 : selectedAudioId ? audioPlayhead : retainedClip && next.montage?.output_url
      ? (retainedClip.timeline_start_seconds + retainedClip.timeline_end_seconds) / 2 : null;
    setEditorDraft(false);
    setSelectedOverlayId(current=>next.montage?.graphic_overlays?.some(item=>item.id===current)?current:null);
    setWorkspace(next);
    // 保留原视图只在该视图按既有门禁仍可呈现时成立。字幕确认会作废 output_url，
    // 使 subtitle_cover 不可呈现；此时保留它会让主区没有任何视图命中（空白且丢失撤销入口）。
    // 这里按既有可用/默认规则回落，不新增视图、不放开任何门禁。
    setActiveView(preserveActiveView && canOpenStage(activeView, next) ? activeView : defaultWorkspaceView(next));
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
      const nextTopics = await getCreationTaskTopics(task.id);
      setTopics(nextTopics);
      setSelectedTopicId(nextTopics.selected_topic_id ?? nextTopics.candidates[0]?.id ?? null);
    }
  }

  async function run(action: BusyAction, operation: () => Promise<unknown>, success?: string) {
    setBusy(action);
    setError(null);
    setNotice(null);
    if (action === "clip" || action === "render") {
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
    } catch (cause) {
      const details = errorDetails(cause);
      let refreshed = false;
      try {
        await refresh(true);
        refreshed = true;
      } catch {
        // Keep the original save failure visible when the workspace cannot refresh.
      }
      setError({ ...details, message: `字幕没有保存到本地。${details.message}`, repairAction: refreshed
        ? "已重新读取当前任务。请检查字幕和保存位置后，重新选择语言并再次点击「保存字幕」。"
        : "请先刷新任务并检查本地服务和保存位置，再重新选择语言保存字幕。" });
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

  function selectOverlay(id: string|null) {
    invalidateEditPreview(); setEditorDraft(false); setSelectedOverlayId(id); setSelectedAudioId(null);
    const item=workspace.montage?.graphic_overlays?.find(value=>value.id===id);
    if(item){const time=(item.start_seconds+item.end_seconds)/2;playheadRef.current=time;
      if(montageVideoRef.current){montageVideoRef.current.pause();montageVideoRef.current.currentTime=time;}}
  }

  function selectClip(clipId: string) {
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
    setSelectedAudioId(null);
    if (selectedCueId !== cueId || selectedClipId || selectedNarrationSegmentId || selectedAudioId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(cueId);
    setSelectedNarrationSegmentId(null);
    setSelectedCover(false);
  }

  function selectNarrationSegment(segmentId: string) {
    if (selectedNarrationSegmentId !== segmentId || selectedClipId || selectedCueId) invalidateEditPreview();
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(segmentId);
    setSelectedCover(false);
  }

  function selectCover() {
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
    setBusy("edit-preview");
    setError(null);
    setNotice(null);
    try {
      const preview = await previewCreationTaskEdit(task.id, {
        raw_command: rawCommand,
        context_stage: activeView,
        selected_object_refs: [selectedEditContext.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])],
        playhead_ms: selectedEditContext.kind === "clip" && playheadRef.current !== null
          ? Math.round(playheadRef.current * 1000) : null,
      });
      if (revision === editContextRevision.current) setEditPreview(preview);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function askAssistant() {
    const message = editCommand.trim();
    if (!message) return;
    const contextLabel = activeView === "script" ? "当前文案" : selectedEditContext?.label ?? "当前任务";
    if (montageVideoRef.current && !montageVideoRef.current.paused) montageVideoRef.current.pause();
    invalidateEditPreview();
    const revision = editContextRevision.current;
    setBusy("assistant");
    setError(null);
    setAssistantReply(null);
    try {
      const result = await sendCreationTaskAssistantTurn(task.id, {
        message,
        context_stage: activeView,
        selected_object_refs: selectedEditContext
          ? [selectedEditContext.ref, ...(activeView === "subtitle_cover" && activeSubtitleTrack ? [`subtitle-track:${activeSubtitleTrack.id}`] : [])]
          : [],
        playhead_ms: selectedEditContext?.kind === "clip" && playheadRef.current !== null
          ? Math.round(playheadRef.current * 1000) : null,
      });
      const contextStillCurrent = revision === editContextRevision.current;
      if (result.script_draft) await refresh(true);
      if (contextStillCurrent) {
        setAssistantReply(result.reply);
        setAssistantTurns((current) => [...current, { command: message, reply: result.reply, context: contextLabel }].slice(-8));
        setAssistantModel(`${result.provider} / ${result.model}`);
        setEditPreview(result.edit_preview);
        if (result.edit_preview) setEditCommand(result.edit_preview.intent.raw_command);
      }
    } catch (cause) {
      setError(errorDetails(cause));
      if (activeView === "script") {
        await refresh().catch(() => undefined);
        const history = await getCreationTaskAssistantHistory(task.id).catch(() => null);
        if (history) {
          const turns = assistantHistoryTurns(history.items);
          setAssistantTurns(turns);
          setAssistantReply(turns.at(-1)?.reply ?? null);
        }
      }
    } finally {
      setBusy(null);
    }
  }

  async function confirmEdit() {
    if (!editPreview) return;
    setBusy("edit-confirm");
    setError(null);
    setNotice(null);
    try {
      await confirmCreationTaskEdit(task.id, editPreview.proposal.id);
      invalidateEditPreview();
      setEditCommand("");
      await refresh(true);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  function selectAudio(id: string, time?: number) {
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
    const revision=editContextRevision.current;
    try {
      const preview=await previewCreationTaskVisual(task.id,input);
      if(revision===editContextRevision.current) setEditPreview(preview);
    } catch(cause) {setError(errorDetails(cause));}
    finally {setBusy(null);}
  }

  async function previewAudio(input: AudioEditInput) {
    setBusy("edit-preview"); setError(null); setNotice(null); invalidateEditPreview();
    const revision = editContextRevision.current;
    try {
      const preview = await previewCreationTaskAudio(task.id,input);
      if (revision === editContextRevision.current) setEditPreview(preview);
    } catch (cause) { setError(errorDetails(cause)); }
    finally { setBusy(null); }
  }

  async function submitRecordedVoice(blob: Blob): Promise<void> {
    const narrationTrackId = workspace.montage?.audio_timeline?.tracks.find((track) => track.role === "narration")?.id ?? "audio-narration";
    let sourceRef: string;
    try {
      const recording = await saveCreationTaskRecording(task.id, blob);
      sourceRef = recording.source_ref;
    } catch (cause) {
      const details = errorDetails(cause);
      setError(details);
      throw new Error(details.message);
    }
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    const revision = editContextRevision.current;
    try {
      const preview = await previewCreationTaskAudio(task.id, {
        base_version_id: task.current_timeline_version_id,
        action: "replace_narration",
        target_id: narrationTrackId,
        values: { source_ref: sourceRef },
        raw_command: "用本机录音替换整条旁白",
      });
      if (revision === editContextRevision.current) {
        setEditPreview(preview);
        setEditCommand(preview.intent.raw_command);
        setRecordingOpen(false);
      }
    } catch (cause) {
      const details = errorDetails(cause);
      setError(details);
      throw new Error(details.message);
    }
  }

  async function reviseCompoundSelection(selection:number[]|string){
    if(!editPreview)return;
    const proposalId=editPreview.proposal.id;
    setBusy("edit-preview");setError(null);
    const revision=++editContextRevision.current;
    try{const next=await previewCompoundSelection(task.id,proposalId,selection);if(editContextRevision.current===revision){setEditPreview(next);setEditCommand(next.intent.raw_command);}}
    catch(cause){setError(errorDetails(cause));}
    finally{setBusy(null);}
  }

  async function previewExactEdit(input: CreationTaskExactEditInput) {
    setBusy("edit-preview");
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    try {
      const revision = editContextRevision.current;
      const preview = await previewCreationTaskExactEdit(task.id, input);
      if (revision === editContextRevision.current) setEditPreview(preview);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function undoTimeline(redo = false) {
    setBusy("undo");
    setError(null);
    setNotice(null);
    invalidateEditPreview();
    try {
      await (redo ? redoCreationTaskTimeline : undoCreationTaskTimeline)(task.id);
      if (activeView !== "montage") setSelectedClipId(null);
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
    if (view === "montage") return Boolean(state.montage);
    if (view === "subtitle_cover") return Boolean(state.montage?.output_url && state.subtitle_tracks.length);
    return (view === "review" || view === "publish") && Boolean(state.montage?.output_url);
  }

  const isEditor = activeView === "montage";
  const selectedClip = selectedClipIndex >= 0 ? workspace.montage?.clips[selectedClipIndex] : undefined;
  const clipDraftKey = selectedClip ? `${task.current_timeline_version_id}:${selectedClip.id}` : undefined;
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
    ? selectedOverlay ? <GraphicOverlayEditor key={`${task.current_timeline_version_id}:${selectedOverlay.id}`} taskId={task.id} items={workspace.montage!.graphic_overlays??[]} selectedId={selectedOverlay.id} onSelect={selectOverlay} duration={workspace.montage!.duration_seconds} busy={busy!==null} onPreview={input=>void previewExactEdit(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedAudioTrack ? <AudioTimelineInspector key={`${task.current_timeline_version_id}:${selectedAudioId}`} track={selectedAudioTrack} clip={selectedAudioClip} versionId={task.current_timeline_version_id} playhead={audioPlayhead} busy={busy !== null} onPreview={input=>void previewAudio(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedClip ? <VideoClipInspector onSelectOverlay={selectOverlay} getPlayhead={() => playheadRef.current} onVisualPreview={input=>void previewVisual(input)} key={`${task.current_timeline_version_id}:${selectedClip.id}`} clip={selectedClip} index={selectedClipIndex} count={workspace.montage!.clips.length} workspace={workspace} busy={busy !== null} initialDraft={clipDraft} onDraftStateChange={updateClipDraft} onPreview={input => void previewExactEdit(input)} onDraftChange={changed => { invalidateEditPreview(); setEditorDraft(changed); }} onReplace={() => { setInserting(false); void showReplacementCandidates(selectedClip.id); }} candidates={replacementCandidates} replacing={replacementClipId === selectedClip.id} onAddSegment={() => { setReplacementClipId(null); void showInsertCandidates(selectedClip.id); }} inserting={inserting} onPerson={command => preparePersonEdit(selectedClip.id, command)} onAddOriginal={() => void previewEdit("提取当前镜头原声")} /> : undefined
    : activeView === "subtitle_cover"
    ? selectedSubtitleCue || selectedCover
      ? <SubtitleCoverInspector selectedCover={selectedCover} selectedCue={selectedSubtitleCue} selectedTrack={activeSubtitleTrack} workspace={workspace} />
      : undefined
    : <Inspector activeView={activeView} busy={busy !== null} error={error} onPreparePersonEdit={preparePersonEdit} selectedContext={selectedEditContext} workspace={workspace} />;

  return (
    <AppShell conversation={(!savingTemplate && ((activeView === "script" && task.stage === "script" && !task.confirmed_script_id) || (((activeView === "montage" || activeView === "review") && workspace.montage) || (activeView === "narration" && workspace.narration) || (activeView === "subtitle_cover" && activeSubtitleTrack)))) ? <ContextCommandBar scriptMode={activeView === "script"} key={editPreview?.proposal.id??task.id} onSelectionPreview={indices=>void reviseCompoundSelection(indices)} compact busy={busy} command={editCommand} onCancel={() => invalidateEditPreview()} onCommandChange={(value) => { setEditCommand(value); if(editPreview?.proposal.operation.operation_type!=="EditCompoundTimeline" || !editPreview.proposal.operation.payload.selection_enabled)invalidateEditPreview(); }} onConfirm={() => void confirmEdit()} onPreview={() => void askAssistant()} onExactPreview={() => void previewEdit()} preview={editPreview} assistantReply={assistantReply} assistantTurns={assistantTurns} assistantModel={assistantModel} assistantConfigured={assistantConfigured} selectedContextLabel={isEditor && selectedOverlay ? `${selectedEditContext?.label} · ${editorTime(selectedOverlay.start_seconds)}–${editorTime(selectedOverlay.end_seconds)}` : isEditor && selectedClip && !selectedAudioId ? `${selectedEditContext?.label} · ${editorTime(selectedClip.timeline_start_seconds)}–${editorTime(selectedClip.timeline_end_seconds)}` : selectedEditContext?.label ?? null} selectedContextPlaceholder={activeView === "script" ? "说说文案想怎么写" : selectedEditContext ? "说说怎么改" : "想问什么？"} /> : null} creationTasks={shellTasks} inspector={inspector} quickInspector={quickInspector} quickInspectorKey={quickInspector ? clipDraftKey : undefined} quickInspectorLabel={quickInspector ? `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")} 快速编辑` : undefined} editorLayout={isEditor} inspectorDismissKey={isEditor ? editPreview?.proposal.id : undefined} toolbar={<div className={editorUi.toolbarTitle}><h1>{task.name}</h1><span>{busy ? "处理中" : editPreview ? "待确认" : editorDraft || quickInspector && clipDraftChanged ? "未应用" : "已保存"} · 版本 {task.timeline_sequence}</span></div>} toolbarActions={isEditor ? <div className={editorUi.toolbarActions}><button className={editorUi.iconButton} aria-label="撤销" title="撤销上一次修改" disabled={busy !== null || !task.can_undo} onClick={() => void undoTimeline()} type="button"><Undo2 size={17} /></button><button className={editorUi.iconButton} aria-label="重做" title="重做刚才撤销的修改" disabled={busy !== null || !task.can_redo} onClick={() => void undoTimeline(true)} type="button"><Redo2 size={17} /></button><button className={editorUi.iconButton} aria-label="电脑录音替换旁白" title="用本机录音替换整条旁白" disabled={busy !== null} onClick={() => setRecordingOpen(true)} type="button"><Mic size={17} /></button></div> : undefined} inspectorTitle={isEditor ? selectedOverlay ? selectedEditContext?.label : selectedAudioTrack ? selectedAudioClip?.name ?? selectedAudioTrack.name : `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")}` : activeView === "subtitle_cover" ? "当前选择" : "任务详情"} workspaceStatus={workspaceStatus}>
      <div className={[isEditor ? editorUi.workspace : styles.workspace, activeView === "subtitle_cover" ? styles.subtitleWorkspace : ""].filter(Boolean).join(" ")}>
        <nav className={editorUi.stageRail} aria-label="制作阶段"><ol>{productStages.map((stage) => {
          const presentation = productStageState(
            stage.id,
            task,
            workspace.subtitle_tracks.some((track) => track.status === "valid")
              && workspace.cover_asset?.status === "valid",
          );
          const view = stageView(stage.id);
          const openable = canOpenStage(view);
          return <li aria-current={presentation.state === "current" ? "step" : undefined} className={styles[presentation.state]} key={stage.id} title={`${stage.title}：${presentation.detail}`}>
            <button aria-label={`${stage.title}：${presentation.detail}`} aria-pressed={openable && activeView === view} disabled={!openable} onClick={() => view && openView(view)} type="button">
              <span>{presentation.state === "done" ? <Check size={14} /> : null}</span>
              <span className={styles.stageCopy}><strong>{stage.label}</strong></span>
            </button>
          </li>;
        })}</ol></nav>
        {notice ? <div className={isEditor ? editorUi.feedback : styles.notice} role="status"><CheckCircle2 size={17} />{notice}</div> : null}
        {error && (isEditor || activeView === "subtitle_cover" || activeView === "review") ? <div className={isEditor ? editorUi.feedback : styles.blocker} role="alert"><AlertCircle size={17} /><span>{error.message}{error.repairAction ? ` ${error.repairAction}` : ""}</span>{error.code === "timeline_version_required" ? <button className={editorUi.button} disabled={busy !== null} onClick={() => void run("clip", () => initializeCreationTaskTimeline(task.id), "已为当前故事板建立初始版本，可继续预览裁剪。") } type="button">保留当前故事板并建立版本</button> : null}</div> : null}
        {executionError ? <div className={styles.blocker} role="alert"><AlertCircle size={17} /><span>{executionError.message}{executionError.repairAction ? ` ${executionError.repairAction}` : ""}</span></div> : null}
        {task.confirmed_script_id && (executionPreview || execution?.job) && (activeView === "script" || activeView === "narration") ? <CreationExecutionPanel preview={executionPreview} execution={execution} busy={busy !== null} onStart={() => executionPreview && void changeExecution(() => startCreationExecution(task.id, executionPreview))} onResume={() => void changeExecution(() => resumeCreationExecution(task.id))} onCancel={() => void changeExecution(() => cancelCreationExecution(task.id))} /> : null}
        {execution?.job && activeView !== "script" && activeView !== "narration" ? <div className={isEditor ? editorUi.feedback : styles.notice} role="status">连续制作：{execution.job.status === "running" ? "后台制作中" : execution.job.status === "failed" ? "有步骤失败" : execution.job.status === "succeeded" ? "成片已生成" : execution.job.status === "queued" ? "已排队" : "等待继续"}。<button className={styles.secondaryButton} onClick={() => openView("script")} type="button">查看步骤</button></div> : null}

        {activeView === "topic" && task.stage === "topic" ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>选题</span><h2>{topics?.candidates.length ? "选择一个要制作的方向" : "从授权资料生成选题"}</h2><p>模型只会看到预检中列出的文字摘录；确认后不会再次生成选题。</p></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。") } type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => { setSelectedTopicId(candidate.id); setTopicTitleDraft(candidate.title); }} type="radio" /><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{selectedTopicCandidate ? <label className={styles.field}><span>编辑标题</span><input aria-label="编辑标题" disabled={busy !== null} maxLength={80} onChange={(event) => setTopicTitleDraft(event.target.value)} placeholder="输入本选题的标题" value={topicTitleDraft} /><small className={styles.eyebrow}>确认选题时带上这个标题；当前落库以生成标题为准，覆盖需后端支持。</small></label> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId || !topicTitleDraft.trim()} onClick={() => selectedTopicId && run("topic-confirm", () => confirmCreationTaskTopic(task.id, selectedTopicId), `选题「${topicTitleDraft.trim()}」已确认，下一步生成文案。`) } type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认选题</button></div> : null}</section> : null}
        {activeView === "topic" && task.stage !== "topic" ? <ConfirmedTopicView workspace={workspace} /> : null}

        {activeView === "script" && task.stage === "script" && !latestScript ? <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>根据已确认选题生成中文文案</h2><p>这是本任务第二次、也是最后一次文字模型调用。生成后先编辑和确认，再进入配音。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("script", () => generateCreationTaskScript(task.id), "文案已生成，等待检查。") } type="button">{busy === "script" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section> : null}
        {activeView === "script" && (task.stage === "script" || task.can_revise_script) && latestScript ? <ScriptEditor busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections }), "文案已确认，可以生成配音。") } repairing={task.stage_status === "failed" && latestScript.selection_status === "confirmed"} script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && !task.can_revise_script && latestScript ? <ConfirmedScriptView script={latestScript} /> : null}

        {activeView === "narration" && task.stage === "narration" && !workspace.narration ? <section className={styles.workSection}><div className={styles.emptyAction}><Volume2 size={30} /><h2>使用已授权声音生成配音</h2><p>使用你已授权的声音，为这份文案配音。</p>{task.can_revise_script ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => openView("script")} type="button">修改文案</button> : workspace.plan.selected_voice ? <button className={executionPreview ? styles.secondaryButton : "modern-primary-button"} disabled={busy !== null || execution?.job?.status === "queued" || execution?.job?.status === "running"} onClick={() => run("narration", () => generateCreationTaskNarration(task.id), "真实配音已生成，可以制作故事板。") } type="button">{busy === "narration" ? <LoaderCircle className="is-spinning" size={16} /> : <Volume2 size={16} />}{executionPreview ? "单步生成配音" : "生成配音"}</button> : runnableVoices.length ? <div className={styles.voiceSelection}><label className={styles.field}><span>用于本任务的声音</span><select onChange={(event) => setSelectedVoiceId(event.target.value)} value={selectedVoiceId ?? ""}>{runnableVoices.map((voice) => <option key={`${voice.profile_id}:${voice.version}`} value={voice.profile_id}>{voice.person_display_name} · {voice.version}</option>)}</select></label><button className="modern-primary-button" disabled={busy !== null || !selectedVoiceId} onClick={() => { const voice = runnableVoices.find((item) => item.profile_id === selectedVoiceId); if (voice) void run("voice-select", () => selectCreationTaskNarrationVoice(task.id, { voice_profile_id: voice.profile_id, voice_version: voice.version }), "声音已绑定，可以生成配音。"); }} type="button">{busy === "voice-select" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}使用此声音</button></div> : <div className={styles.voiceMissing}><AlertCircle size={18}/><div><strong>还没有可用于中文配音的授权声音</strong><p>登记人物身份、导入已授权录音 并逐字填写参考文字后，可回到本任务继续，不需要重做选题或文案。</p></div><Link className="modern-primary-button" href="/profiles">添加声音</Link></div>}</div></section> : null}
        {activeView === "narration" && workspace.narration ? <NarrationResultView busy={busy !== null} onGeneratePersonRender={(segmentId) => { const selectedVoice = workspace.plan.selected_voice; if (selectedVoice) void run("person-render", () => generateCreationTaskPersonRender(task.id, { narration_segment_id: segmentId, person_profile_id: selectedVoice.person_profile_id, person_version: selectedVoice.person_version, motion: "natural", expression: "neutral" }), "人物口型预览已生成；尚未写入成片时间线。"); }} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} workspace={workspace} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>生成混剪故事板</h2><p>露脸目标 {workspace.plan.presenter_share_target ?? 0}%；只为露脸段制作口型，其余段落从已审核素材选镜头。</p><button className={execution?.job ? styles.secondaryButton : "modern-primary-button"} disabled={busy !== null || execution?.job?.status === "queued" || execution?.job?.status === "running"} onClick={() => run("plan", () => createCreationTaskMontagePlan(task.id), "故事板已生成。") } type="button">{busy === "plan" ? <LoaderCircle className="is-spinning" size={16} /> : <Clapperboard size={16} />}{execution?.job ? "单步生成故事板" : "生成故事板"}</button></div></section> : null}
        {isEditor && workspace.montage ? <VideoEditingStage selectedOverlayId={selectedOverlayId} onSelectOverlay={selectOverlay} onDraftChange={()=>invalidateEditPreview()} busy={busy !== null} onPreview={input=>void previewExactEdit(input)} audioTracks={workspace.montage.audio_timeline ? <AudioTimelineTracks onListenPosition={positionAudioListen} key={`${task.id}:${task.current_timeline_version_id}`} workspace={workspace} selectedId={selectedAudioId} playhead={audioPlayhead} onSelect={selectAudio} onPreview={input=>void previewAudio(input)} busy={busy !== null} /> : undefined} workspace={workspace} selectedId={selectedClipId} onSelect={selectClip} onPosition={selectPlayingClip} videoRef={montageVideoRef} initialTime={playheadRef.current ?? 0} renderAction={<button className={editPreview || editorDraft ? editorUi.button : editorUi.primary} disabled={busy !== null || execution?.job?.status === "queued" || execution?.job?.status === "running" || workspace.montage.status === "waiting_for_user" || (workspace.montage.status === "rendered" && Boolean(task.current_timeline_version_id))} onClick={() => void run("render", () => renderCreationTaskMontage(task.id), "成片已生成。")} type="button">{busy === "render" ? "制作中" : "生成成片"}</button>} /> : null}

        {activeView === "subtitle_cover" && activeSubtitleTrack && workspace.montage?.output_url ? <SubtitleCoverView activeTrack={activeSubtitleTrack} busy={busy !== null} key={`${task.current_timeline_version_id}:${workspace.cover_asset?.id ?? "no-cover"}`} onDraftChange={() => invalidateEditPreview()} onPreviewExact={(input) => void previewExactEdit(input)} onSelectCover={selectCover} onSelectCue={selectCue} onSelectTrack={selectSubtitleTrack} onSaveCover={() => void saveCover()} onSaveSubtitle={(mode) => void saveSubtitle(mode)} onUndo={() => void undoTimeline()} selectedCover={selectedCover} selectedCueId={selectedCueId} workspace={workspace} /> : null}

        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>当前成片</span><h2>检查成片</h2><p>{workspace.subtitle_tracks.some((track) => track.language === "en-US" && track.status === "valid") ? workspace.subtitle_tracks.some((track) => track.language === "zh-CN" && track.status === "valid") ? "中英双语轨已就绪" : "英文字幕轨已就绪" : "中文字幕轨已就绪"} · {workspace.cover_asset?.status === "valid" ? "封面已生成" : "封面尚待确认"}</p></div><div className={styles.reviewActions}><button className={styles.secondaryButton} disabled={busy !== null} onClick={() => openView("montage")} type="button">修改镜头</button><button className={styles.secondaryButton} disabled={busy !== null} onClick={() => setRecordingOpen(true)} type="button"><Mic size={16} />电脑录音替换旁白</button>{activeSubtitleTrack ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => openView("subtitle_cover")} type="button">字幕封面</button> : null}<button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div></div><div className={styles.evidenceList}><strong>检查清单 · {reviewReadyCount}/{reviewChecks.length} 项已就绪</strong>{reviewChecks.map((item) => <article key={item.title}><span>{item.ready ? "已就绪" : "待确认"}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div></article>)}</div><div className={styles.videoReview}><video aria-label="成片预览" controls preload="metadata" ref={montageVideoRef} onPause={(event) => selectPlayingClip(event.currentTarget)} onSeeked={(event) => selectPlayingClip(event.currentTarget)} onTimeUpdate={(event) => selectPlayingClip(event.currentTarget)} onLoadedMetadata={(event) => { event.currentTarget.currentTime = playheadRef.current ?? 0; }} src={apiUrl(workspace.montage.output_url)} /><div><p>成片可预览；请按上方清单逐项确认。</p></div></div></section> : null}
        {activeView === "review" && workspace.montage?.status === "rendered" && task.current_timeline_version_id ? <SaveVideoTemplateForm key={task.current_timeline_version_id} taskId={task.id} onEditingChange={setSavingTemplate} /> : null}
        {activeView === "prepare" ? <PrepareMaterialsView workspace={workspace} /> : null}
        {activeView === "publish" ? <PublishStageView workspace={workspace} onOpenReview={() => openView("review")} /> : null}
        {recordingOpen ? <div className="home-card-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setRecordingOpen(false); }}><DesktopVoiceRecorder scriptText={latestScript?.sections.map((section) => section.content).join("\n")} onRecorded={submitRecordedVoice} onCancel={() => setRecordingOpen(false)} /></div> : null}


      </div>
    </AppShell>
  );
}
