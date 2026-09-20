"use client";

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
  Play,
  Save,
  Sparkles,
  UserRound,
  Volume2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
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
  previewCreationTaskEdit,
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
  MontageClipCandidate,
} from "@/lib/types";
import styles from "./CreationTaskWorkspace.module.css";
import { AudioTimelineTracks, AudioTimelineInspector } from "./AudioTimelineEditor";
import { previewCreationTaskAudio, previewCreationTaskVisual } from "@/lib/api";
import type { AudioEditInput, VisualEditInput } from "@/lib/types";
import { SaveVideoTemplateForm } from "./SaveVideoTemplateForm";
import { VideoEditingStage, VideoClipInspector, editorTime } from "./VideoEditor";
import editorUi from "./VideoEditor.module.css";

type Props = {
  initialWorkspace: WorkspaceState;
  initialTopics: CreationTaskTopicsResponse | null;
  initialTasks: CreationTask[];
};

type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "voice-select" | "narration" | "person-render" | "plan" | "clip" | "render" | "save" | "edit-preview" | "edit-confirm" | "undo";

type WorkspaceView = CreationTask["stage"] | "subtitle_cover";

type SelectedEditContext = {
  kind: "narration" | "cue" | "clip" | "cover" | "audio";
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
] as const;

const stageLabels = {
  topic: "选题",
  script: "文案",
  narration: "配音",
  montage: "视频剪辑",
  subtitle_cover: "字幕与封面",
  review: "检查成片",
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
    };
  }
  return {
    message: cause instanceof Error ? cause.message : "操作没有完成。",
    impact: null,
    repairAction: null,
  };
}

function Inspector({ activeView, busy, workspace, error, onPreparePersonEdit, selectedContext }: { activeView: CreationTask["stage"]; busy: boolean; workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null; onPreparePersonEdit: (clipId: string, command: string) => void; selectedContext: SelectedEditContext | null }) {
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
      {selectedClip ? <section aria-label="已选镜头详情"><span className={styles.eyebrow}>已选镜头</span><h3>{selectedContext?.label}</h3><dl><div><dt>用途</dt><dd>{selectedClip.person_render_id ? "人物口型" : selectedClip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{selectedClip.timeline_start_seconds.toFixed(1)}–{selectedClip.timeline_end_seconds.toFixed(1)} 秒</dd></div><div><dt>人物版本</dt><dd>{selectedPersonRender ? `${selectedPersonRender.person_profile_id} · ${selectedPersonRender.person_version}` : selectedClip.person_render_id ? "未读取到人物片段记录" : plan.selected_voice ? `${plan.selected_voice.person_profile_id} · ${plan.selected_voice.person_version}` : "当前任务人物"}</dd></div>{selectedPersonRender ? <><div><dt>声音版本</dt><dd>{selectedPersonRender.voice_profile_id} · {selectedPersonRender.voice_version}</dd></div><div><dt>语言</dt><dd>{selectedPersonNarration?.language === "zh" ? "中文" : "未记录"}</dd></div><div><dt>当前模型</dt><dd>{selectedPersonRender.model_id}</dd></div><div><dt>动作</dt><dd>{selectedPersonRender.motion === "natural" ? "自然（固定）" : selectedPersonRender.motion}</dd></div><div><dt>表情</dt><dd>{selectedPersonRender.expression === "neutral" ? "中性（固定）" : selectedPersonRender.expression}</dd></div><div><dt>口型偏移</dt><dd>自动匹配（暂不可手动调整）</dd></div></> : null}<div><dt>片段状态</dt><dd>{personRenderStatus}</dd></div><div><dt>锁定</dt><dd>{selectedClip.locked ? "已锁定" : "可调整"}</dd></div><div><dt>模型能力</dt><dd>{personRenderCapability.available ? "可用" : "不可用"} · {personRenderCapability.model_id ?? "未配置模型"}</dd></div></dl>{!personRenderCapability.available ? <div aria-label="人物口型不可用原因" className={styles.personCapabilityIssue} role="status"><strong>人物口型暂不可用</strong><p>{personRenderCapability.message}</p>{personRenderCapability.repair_action ? <small>处理方式：{personRenderCapability.repair_action}</small> : null}</div> : null}{selectedPersonRender ? <p className={styles.personCapability}>动作/表情沿用原人物素材，口型匹配当前配音。</p> : null}{!selectedClip.person_render_id ? <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available} onClick={() => onPreparePersonEdit(selectedClip.id, "这一段换成当前人物出镜")} type="button"><UserRound size={15} />换成当前人物出镜</button> : <button className={`modern-primary-button ${styles.personAction}`} disabled={busy || !personRenderCapability.available || !personRenderCanRegenerate} onClick={() => onPreparePersonEdit(selectedClip.id, "这里只重做口型，其他都不要动")} type="button"><UserRound size={15} />重新对口型</button>}</section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>声音与授权</span><dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{narration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl></section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div></dl></section> : null}
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
      <div className={styles.sectionHeading}><div><span>{repairing ? "文案修订" : "文案编辑"}</span><h2>{repairing ? "压缩并重新确认文案" : "检查事实和口播表达"}</h2><p>{repairing ? "文字模型没有完成重写。你可以直接修改已确认文案，重新确认后继续配音。" : "编辑不会再次调用模型；只有“确认文案”会推进到真实配音。"}</p></div></div>
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })} /><p><strong>画面建议：</strong>{section.visual_prompt}</p><p><strong>来源证据：</strong>{section.evidence_reference_ids.join("、")}</p></article>)}</div>
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}{repairing ? "确认修订文案" : "确认文案"}</button></div>
    </section>
  );
}

function ContextCommandBar({
  compact = false,
  busy,
  command,
  onCancel,
  onCommandChange,
  onConfirm,
  onPreview,
  preview,
  selectedContextLabel,
  selectedContextPlaceholder,
}: {
  compact?: boolean;
  busy: BusyAction | null;
  command: string;
  onCancel: () => void;
  onCommandChange: (value: string) => void;
  onConfirm: () => void;
  onPreview: () => void;
  preview: CreationTaskEditPreviewResponse | null;
  selectedContextLabel: string | null;
  selectedContextPlaceholder: string;
}) {
  const ui = compact ? editorUi : styles;
  const barRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (preview && !compact) barRef.current?.scrollIntoView?.({ block: "end" });
  }, [preview, compact]);

  return (
    <section
      aria-label="上下文命令"
      className={[ui.contextCommandBar, preview ? ui.hasPreview : ""].filter(Boolean).join(" ")}
      ref={barRef}
    >
      <div className={ui.commandRow}>
        <span className={selectedContextLabel ? ui.contextSelection : ui.contextEmpty}>
          {selectedContextLabel ? `已选 ${selectedContextLabel}` : "先选择要修改的内容"}
        </span>
        <label className={ui.commandInput}>
          {!compact ? <span>修改要求</span> : null}
          <input
            aria-label="局部修改要求"
            disabled={busy !== null || !selectedContextLabel}
            onChange={(event) => onCommandChange(event.target.value)}
            placeholder={selectedContextPlaceholder}
            value={command}
          />
        </label>
        <button
          className={compact ? editorUi.primary : "modern-primary-button"}
          disabled={busy !== null || Boolean(preview) || !selectedContextLabel || !command.trim()}
          onClick={onPreview}
          type="button"
        >
          {busy === "edit-preview" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}
          预览修改
        </button>
      </div>
      {preview ? (
        <div aria-label="变更预览" aria-live="polite" className={ui.changePreview}>
          <header><strong>{preview.proposal.summary}</strong><span>等待确认</span></header>
          <div className={ui.previewDiff}>
            <section><small>修改前</small><strong>{preview.proposal.preview.before.label}</strong><span>{preview.proposal.preview.before.detail}</span>{preview.proposal.preview.before.image_url ? <a href={apiUrl(preview.proposal.preview.before.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改前字幕样式"><NextImage alt="修改前字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.before.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.before.video_url ? <video className={editorUi.effectPreview} aria-label="修改前画面" controls muted playsInline preload="metadata" onPlay={event=>{barRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media=>{if(media!==event.currentTarget)media.pause();});}} src={apiUrl(preview.proposal.preview.before.video_url)} />:null}{preview.proposal.preview.before.audio_url ? <audio aria-label="修改前音频" controls onPlay={event => { barRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.before.audio_url)} /> : null}</section>
            <section><small>修改后</small><strong>{preview.proposal.preview.after.label}</strong><span>{preview.proposal.preview.after.detail}</span>{preview.proposal.preview.after.image_url ? <a href={apiUrl(preview.proposal.preview.after.image_url)} target="_blank" rel="noreferrer" aria-label="原尺寸修改后字幕样式"><NextImage alt="修改后字幕样式" className={editorUi.subtitleStylePreview} width={1080} height={1920} unoptimized src={apiUrl(preview.proposal.preview.after.image_url)} />查看原尺寸</a> : null}{preview.proposal.preview.after.video_url ? <video className={editorUi.effectPreview} aria-label="修改后画面" controls muted playsInline preload="metadata" onPlay={event=>{barRef.current?.querySelectorAll<HTMLMediaElement>("audio,video").forEach(media=>{if(media!==event.currentTarget)media.pause();});}} src={apiUrl(preview.proposal.preview.after.video_url)} />:null}{preview.proposal.preview.after.audio_url ? <audio aria-label="修改后音频" controls onPlay={event => { barRef.current?.querySelectorAll("audio").forEach(audio => { if (audio !== event.currentTarget) audio.pause(); }); }} src={apiUrl(preview.proposal.preview.after.audio_url)} /> : null}</section>
          </div>
          {preview.proposal.preview.text_changes?.length ? <details className={editorUi.textChanges} open><summary>查看 {preview.proposal.preview.text_changes.length} 条字幕变化</summary><ol>{preview.proposal.preview.text_changes.map(change => <li key={change.cue_id}><span>修改前：{change.before}</span><span>修改后：{change.after}</span></li>)}</ol></details> : null}
          <div className={ui.previewEffects}>
            <span><strong>将失效</strong>{preview.proposal.invalidated_refs.map(ref => compact ? ref.endsWith(".mp4") ? "当前成片" : ref.endsWith(".srt") ? "字幕输出" : ref : ref).join("、") || "无"}</span>
            <span><strong>外部影响</strong>{preview.proposal.external_effects.join("；") || "无"}</span>
          </div>
          <div className={ui.previewActions}>
            <button className={compact ? editorUi.button : styles.secondaryButton} disabled={busy !== null} onClick={onCancel} type="button">取消</button>
            <button className={compact ? editorUi.primary : "modern-primary-button"} disabled={busy !== null || preview.proposal.status !== "proposed"} onClick={onConfirm} type="button">
              {busy === "edit-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}
              确认修改
            </button>
          </div>
        </div>
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
  const videoRef = useRef<HTMLVideoElement>(null);
  const chineseVisible = sourceTrack.language === "zh-CN" && sourceTrack.style.enabled !== false;
  const englishVisible = translationTrack?.status === "valid" && translationTrack.style.enabled !== false;
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
      </div>

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

export function CreationTaskWorkspace({ initialWorkspace, initialTopics, initialTasks }: Props) {
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [activeView, setActiveView] = useState<WorkspaceView>(() => defaultWorkspaceView(initialWorkspace));
  const [topics, setTopics] = useState(initialTopics);
  const [selectedTopicId, setSelectedTopicId] = useState(initialTopics?.selected_topic_id ?? initialTopics?.candidates[0]?.id ?? null);
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
  const [editorDraft, setEditorDraft] = useState(false);
  const [audioPlayhead, setAudioPlayhead] = useState(0);
  const [selectedAudioId, setSelectedAudioId] = useState<string | null>(null);
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
  const task = workspace.task;
  const latestScript = workspace.scripts[0] ?? null;
  const runnableVoices = workspace.available_voices.filter((voice) => voice.runnable);
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
  const selectedEditContext = activeView === "montage" && selectedAudioTrack
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
  }

  async function refresh(preserveActiveView = false) {
    const next = await getCreationTaskWorkspace(task.id);
    invalidateEditPreview();
    setReplacementClipId(null);
    setReplacementCandidates([]);
    const retainedClip = next.montage?.clips.find(clip => clip.id === selectedClipId);
    playheadRef.current = selectedAudioId ? audioPlayhead : retainedClip && next.montage?.output_url
      ? (retainedClip.timeline_start_seconds + retainedClip.timeline_end_seconds) / 2 : null;
    setEditorDraft(false);
    setWorkspace(next);
    if (!preserveActiveView) setActiveView(defaultWorkspaceView(next));
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

  async function saveVideo() {
    setBusy("save");
    setError(null);
    setNotice(null);
    try {
      const saver = window.electronAPI?.saveRenderedVideo;
      if (!saver) throw new Error("请在桌面客户端中使用原生保存功能。浏览器预览不会接收文件路径。");
      const result = await saver(task.id);
      if (result.error) throw new Error(result.error);
      if (result.saved) setNotice(`已保存${result.fileName ? `：${result.fileName}` : "到所选位置"}`);
    } catch (cause) {
      setError(errorDetails(cause));
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

  function selectClip(clipId: string) {
    setSelectedAudioId(null);
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

  async function previewEdit() {
    const rawCommand = editCommand.trim();
    if (!selectedEditContext || !rawCommand) return;
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
    return stageId === "prepare" ? null : stageId;
  }

  function canOpenStage(view: WorkspaceView | null): view is WorkspaceView {
    if (!view) return false;
    if (view === task.stage) return true;
    if (view === "topic") return Boolean(workspace.selected_topic);
    if (view === "script") return Boolean(latestScript);
    if (view === "narration") return Boolean(workspace.narration);
    if (view === "montage") return Boolean(workspace.montage);
    if (view === "subtitle_cover") return Boolean(workspace.montage?.output_url && workspace.subtitle_tracks.length);
    return view === "review" && Boolean(workspace.montage?.output_url);
  }

  const isEditor = activeView === "montage";
  const selectedClip = selectedClipIndex >= 0 ? workspace.montage?.clips[selectedClipIndex] : undefined;
  const inspector = isEditor
    ? selectedAudioTrack ? <AudioTimelineInspector key={`${task.current_timeline_version_id}:${selectedAudioId}`} track={selectedAudioTrack} clip={selectedAudioClip} versionId={task.current_timeline_version_id} playhead={audioPlayhead} busy={busy !== null} onPreview={input=>void previewAudio(input)} onDraftChange={()=>{invalidateEditPreview();setEditorDraft(true);}} />
    : selectedClip ? <VideoClipInspector getPlayhead={() => playheadRef.current} onVisualPreview={input=>void previewVisual(input)} key={`${task.current_timeline_version_id}:${selectedClip.id}`} clip={selectedClip} index={selectedClipIndex} count={workspace.montage!.clips.length} workspace={workspace} busy={busy !== null} onPreview={input => void previewExactEdit(input)} onDraftChange={changed => { invalidateEditPreview(); setEditorDraft(changed); }} onReplace={() => void showReplacementCandidates(selectedClip.id)} candidates={replacementCandidates} replacing={replacementClipId === selectedClip.id} onPerson={command => preparePersonEdit(selectedClip.id, command)} onAddOriginal={() => void previewAudio({base_version_id:task.current_timeline_version_id,action:"add",target_id:"audio-original",values:{source_ref:selectedClip.source_reference,name:`镜头 ${selectedClipIndex+1} 原声`,source_start_ms:Math.round(selectedClip.source_start_seconds*1000),source_end_ms:Math.round(selectedClip.source_end_seconds*1000),timeline_start_ms:Math.round(selectedClip.timeline_start_seconds*1000)}})} /> : undefined
    : activeView === "subtitle_cover"
    ? selectedSubtitleCue || selectedCover
      ? <SubtitleCoverInspector selectedCover={selectedCover} selectedCue={selectedSubtitleCue} selectedTrack={activeSubtitleTrack} workspace={workspace} />
      : undefined
    : <Inspector activeView={activeView} busy={busy !== null} error={error} onPreparePersonEdit={preparePersonEdit} selectedContext={selectedEditContext} workspace={workspace} />;

  return (
    <AppShell creationTasks={shellTasks} inspector={inspector} editorLayout={isEditor} inspectorSelectionKey={isEditor ? selectedAudioId ?? selectedClipId : undefined} inspectorDismissKey={isEditor ? editPreview?.proposal.id : undefined} toolbar={isEditor ? <div className={editorUi.toolbarTitle}><h1>{task.name}</h1><span>{busy ? "处理中" : editPreview ? "待确认" : editorDraft ? "未应用" : "已保存"} · 版本 {task.timeline_sequence}</span></div> : undefined} toolbarActions={isEditor ? <div className={editorUi.toolbarActions}><button className={editorUi.iconButton} aria-label="撤销" title="撤销上一次修改" disabled={busy !== null || !task.can_undo} onClick={() => void undoTimeline()} type="button"><Undo2 size={17} /></button><button className={editorUi.iconButton} aria-label="重做" title="重做刚才撤销的修改" disabled={busy !== null || !task.can_redo} onClick={() => void undoTimeline(true)} type="button"><Redo2 size={17} /></button></div> : undefined} inspectorTitle={isEditor ? selectedAudioTrack ? selectedAudioClip?.name ?? selectedAudioTrack.name : `镜头 ${String(selectedClipIndex + 1).padStart(2, "0")}` : activeView === "subtitle_cover" ? "当前选择" : "任务详情"} workspaceStatus={workspaceStatus}>
      <div className={[isEditor ? editorUi.workspace : styles.workspace, activeView === "subtitle_cover" ? styles.subtitleWorkspace : ""].filter(Boolean).join(" ")}>
        {isEditor ? null : ["subtitle_cover", "review"].includes(activeView) ? <header className={styles.editingHeader}><h1>{task.name}</h1><span className={styles.versionBadge}>版本 {task.timeline_sequence}</span></header> : <header className={styles.taskHeader}><div><span className={styles.eyebrow}>创作任务</span><h1>{task.name}</h1><p>{task.scenario}</p></div><div className={styles.progress}><strong>{task.progress}%</strong><span>本地真成片进度</span></div></header>}
        <nav className={isEditor ? editorUi.stageRail : styles.stageRail} aria-label="制作阶段"><ol>{productStages.map((stage, index) => {
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
              <span className={isEditor ? undefined : styles.stageMarker}>{presentation.state === "done" ? <Check size={14} /> : isEditor ? null : index + 1}</span>
              <span className={styles.stageCopy}><strong>{stage.label}</strong>{!isEditor ? <small>{presentation.detail}</small> : null}</span>
            </button>
          </li>;
        })}</ol></nav>
        {notice ? <div className={isEditor ? editorUi.feedback : styles.notice} role="status"><CheckCircle2 size={17} />{notice}</div> : null}
        {error && (isEditor || activeView === "subtitle_cover") ? <div className={isEditor ? editorUi.feedback : styles.blocker} role="alert"><AlertCircle size={17} /><span>{error.message}{error.repairAction ? ` ${error.repairAction}` : ""}</span></div> : null}

        {activeView === "topic" && task.stage === "topic" ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>选题</span><h2>{topics?.candidates.length ? "选择一个要制作的方向" : "从授权资料生成选题"}</h2><p>模型只会看到预检中列出的文字摘录；确认后不会再次生成选题。</p></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。") } type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => setSelectedTopicId(candidate.id)} type="radio" /><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId} onClick={() => selectedTopicId && run("topic-confirm", () => confirmCreationTaskTopic(task.id, selectedTopicId), "选题已确认，下一步生成文案。") } type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认选题</button></div> : null}</section> : null}
        {activeView === "topic" && task.stage !== "topic" ? <ConfirmedTopicView workspace={workspace} /> : null}

        {activeView === "script" && task.stage === "script" && !latestScript ? <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>根据已确认选题生成中文文案</h2><p>这是本任务第二次、也是最后一次文字模型调用。生成后先编辑和确认，再进入配音。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("script", () => generateCreationTaskScript(task.id), "文案已生成，等待检查。") } type="button">{busy === "script" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section> : null}
        {activeView === "script" && task.stage === "script" && latestScript ? <ScriptEditor busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections }), "文案已确认，可以生成中文配音。") } repairing={task.stage_status === "failed" && latestScript.selection_status === "confirmed"} script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && latestScript ? <ConfirmedScriptView script={latestScript} /> : null}

        {activeView === "narration" && task.stage === "narration" && !workspace.narration ? <section className={styles.workSection}><div className={styles.emptyAction}><Volume2 size={30} /><h2>使用已授权声音生成中文配音</h2><p>必须由 Qwen3-TTS 0.6B 基于已授权 WAV 和参考文字生成；运行环境缺失会明确停止。</p>{workspace.plan.selected_voice ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("narration", () => generateCreationTaskNarration(task.id), "真实配音已生成，可以制作故事板。") } type="button">{busy === "narration" ? <LoaderCircle className="is-spinning" size={16} /> : <Volume2 size={16} />}生成真实配音</button> : runnableVoices.length ? <div className={styles.voiceSelection}><label className={styles.field}><span>用于本任务的声音</span><select onChange={(event) => setSelectedVoiceId(event.target.value)} value={selectedVoiceId ?? ""}>{runnableVoices.map((voice) => <option key={`${voice.profile_id}:${voice.version}`} value={voice.profile_id}>{voice.person_display_name} · {voice.version}</option>)}</select></label><button className="modern-primary-button" disabled={busy !== null || !selectedVoiceId} onClick={() => { const voice = runnableVoices.find((item) => item.profile_id === selectedVoiceId); if (voice) void run("voice-select", () => selectCreationTaskNarrationVoice(task.id, { voice_profile_id: voice.profile_id, voice_version: voice.version }), "声音已绑定，可以生成真实配音。"); }} type="button">{busy === "voice-select" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}使用这个声音继续</button></div> : <div className={styles.voiceMissing}><AlertCircle size={18}/><div><strong>还没有可用于中文配音的授权声音</strong><p>登记人物身份、导入已授权 WAV 并逐字填写参考文字后，可回到本任务继续，不需要重做选题或文案。</p></div><Link className="modern-primary-button" href="/profiles">去登记授权声音</Link></div>}</div></section> : null}
        {activeView === "narration" && workspace.narration ? <NarrationResultView busy={busy !== null} onGeneratePersonRender={(segmentId) => { const selectedVoice = workspace.plan.selected_voice; if (selectedVoice) void run("person-render", () => generateCreationTaskPersonRender(task.id, { narration_segment_id: segmentId, person_profile_id: selectedVoice.person_profile_id, person_version: selectedVoice.person_version, motion: "natural", expression: "neutral" }), "人物口型预览已生成；尚未写入成片时间线。"); }} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} workspace={workspace} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>用已审核素材生成故事板</h2><p>系统只使用当前工作区中已审核并标注用途的镜头；低置信镜头必须由你确认。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("plan", () => createCreationTaskMontagePlan(task.id), "故事板已生成。") } type="button">{busy === "plan" ? <LoaderCircle className="is-spinning" size={16} /> : <Clapperboard size={16} />}生成故事板</button></div></section> : null}
        {isEditor && workspace.montage ? <VideoEditingStage onDraftChange={()=>invalidateEditPreview()} busy={busy !== null} onPreview={input=>void previewExactEdit(input)} audioTracks={workspace.montage.audio_timeline ? <AudioTimelineTracks onListenPosition={positionAudioListen} key={`${task.id}:${task.current_timeline_version_id}`} workspace={workspace} selectedId={selectedAudioId} playhead={audioPlayhead} onSelect={selectAudio} onPreview={input=>void previewAudio(input)} busy={busy !== null} /> : undefined} workspace={workspace} selectedId={selectedClipId} onSelect={selectClip} onPosition={selectPlayingClip} videoRef={montageVideoRef} initialTime={playheadRef.current ?? 0} renderAction={<button className={editPreview || editorDraft ? editorUi.button : editorUi.primary} disabled={busy !== null || workspace.montage.status === "waiting_for_user" || (workspace.montage.status === "rendered" && Boolean(task.current_timeline_version_id))} onClick={() => void run("render", () => renderCreationTaskMontage(task.id), "当前版本成片已生成。")} type="button">{busy === "render" ? "正在渲染…" : "渲染成片"}</button>} /> : null}

        {activeView === "subtitle_cover" && activeSubtitleTrack && workspace.montage?.output_url ? <SubtitleCoverView activeTrack={activeSubtitleTrack} busy={busy !== null} key={`${task.current_timeline_version_id}:${workspace.cover_asset?.id ?? "no-cover"}`} onDraftChange={() => invalidateEditPreview()} onPreviewExact={(input) => void previewExactEdit(input)} onSelectCover={selectCover} onSelectCue={selectCue} onSelectTrack={selectSubtitleTrack} onSaveCover={() => void saveCover()} onUndo={() => void undoTimeline()} selectedCover={selectedCover} selectedCueId={selectedCueId} workspace={workspace} /> : null}

        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>当前成片</span><h2>播放并保存检查结果</h2><p>{workspace.subtitle_tracks.some((track) => track.language === "en-US" && track.status === "valid") ? workspace.subtitle_tracks.some((track) => track.language === "zh-CN" && track.status === "valid") ? "中英双语轨已就绪" : "英文字幕轨已就绪" : "中文字幕轨已就绪"} · {workspace.cover_asset?.status === "valid" ? "封面已生成" : "封面尚待确认"}</p></div><div className={styles.reviewActions}><button className={styles.secondaryButton} disabled={busy !== null} onClick={() => openView("montage")} type="button">修改镜头</button>{activeSubtitleTrack ? <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => openView("subtitle_cover")} type="button">修改字幕与封面</button> : null}<button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div></div><div className={styles.videoReview}><video aria-label="成片预览" controls preload="metadata" ref={montageVideoRef} onPause={(event) => selectPlayingClip(event.currentTarget)} onSeeked={(event) => selectPlayingClip(event.currentTarget)} onTimeUpdate={(event) => selectPlayingClip(event.currentTarget)} onLoadedMetadata={(event) => { event.currentTarget.currentTime = playheadRef.current ?? 0; }} src={apiUrl(workspace.montage.output_url)} /><div><p>成片已通过视频流、音频流、尺寸和非空校验。</p></div></div></section> : null}
        {activeView === "review" && workspace.montage?.status === "rendered" && task.current_timeline_version_id ? <SaveVideoTemplateForm key={task.current_timeline_version_id} taskId={task.id} onEditingChange={setSavingTemplate} /> : null}

        {(!savingTemplate && (((activeView === "montage" || activeView === "review") && workspace.montage) || (activeView === "narration" && workspace.narration) || (activeView === "subtitle_cover" && activeSubtitleTrack))) ? <ContextCommandBar compact={isEditor} busy={busy} command={editCommand} onCancel={() => invalidateEditPreview()} onCommandChange={(value) => { setEditCommand(value); invalidateEditPreview(); }} onConfirm={() => void confirmEdit()} onPreview={() => void previewEdit()} preview={editPreview} selectedContextLabel={isEditor && selectedClip && !selectedAudioId ? `${selectedEditContext?.label} · ${editorTime(selectedClip.timeline_start_seconds)}–${editorTime(selectedClip.timeline_end_seconds)}` : selectedEditContext?.label ?? null} selectedContextPlaceholder={selectedEditContext?.placeholder ?? "先选择要修改的内容"} /> : null}
      </div>
    </AppShell>
  );
}
