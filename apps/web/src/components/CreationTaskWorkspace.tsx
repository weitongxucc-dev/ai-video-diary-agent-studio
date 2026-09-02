"use client";

import Link from "next/link";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Check,
  CheckCircle2,
  Clapperboard,
  FileText,
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
  applyCreationTaskExactEdit,
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
  renderCreationTaskMontage,
  selectCreationTaskNarrationVoice,
  undoCreationTaskTimeline,
} from "@/lib/api";
import type {
  CreationTask,
  CreationTaskExactEditInput,
  CreationTaskEditPreviewResponse,
  CreationTaskNarrationSegment,
  CreationTaskScript,
  CreationTaskTopicsResponse,
  CreationTaskWorkspace as WorkspaceState,
  MontageClipCandidate,
} from "@/lib/types";
import styles from "./CreationTaskWorkspace.module.css";

type Props = {
  initialWorkspace: WorkspaceState;
  initialTopics: CreationTaskTopicsResponse | null;
  initialTasks: CreationTask[];
};

type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "voice-select" | "narration" | "person-render" | "plan" | "clip" | "render" | "save" | "edit-preview" | "edit-confirm" | "exact-edit" | "undo";

type SelectedEditContext = {
  kind: "narration" | "cue" | "clip";
  id: string;
  label: string;
  placeholder: string;
  ref: string;
};

const stageOrder = ["topic", "script", "narration", "montage", "review"] as const;
const productStages = [
  { id: "prepare", label: "资料", title: "资料准备" },
  { id: "topic", label: "选题", title: "选题" },
  { id: "script", label: "文案", title: "文案" },
  { id: "narration", label: "配音", title: "配音" },
  { id: "montage", label: "剪辑", title: "视频剪辑" },
  { id: "review", label: "成片", title: "检查成片" },
] as const;

const stageLabels = {
  topic: "选题",
  script: "文案",
  narration: "配音",
  montage: "视频剪辑",
  review: "检查成片",
};

function stageIndex(stage: CreationTask["stage"]) {
  return stageOrder.indexOf(stage);
}

type ProductStageState = "done" | "current" | "upcoming";

function productStageState(
  stageId: typeof productStages[number]["id"],
  task: CreationTask,
): { state: ProductStageState; detail: string } {
  if (stageId === "prepare") return { state: "done", detail: "已完成" };

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

function Inspector({ activeView, busy, workspace, error, onChangePersonSegment, selectedContext }: { activeView: CreationTask["stage"]; busy: boolean; workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null; onChangePersonSegment: (clipId: string) => void; selectedContext: SelectedEditContext | null }) {
  const { task, plan, selected_topic: topic, narration, montage } = workspace;
  const latestScript = workspace.scripts[0] ?? null;
  const viewingCurrentStage = activeView === task.stage;
  const selectedNarration = selectedContext?.kind === "narration" ? narration?.segments.find((segment) => segment.id === selectedContext.id) ?? null : null;
  const selectedCue = selectedContext?.kind === "cue" ? montage?.cues.find((cue) => cue.id === selectedContext.id) ?? null : null;
  const selectedClip = selectedContext?.kind === "clip" ? montage?.clips.find((clip) => clip.id === selectedContext.id) ?? null : null;
  const selectedPersonRender = selectedClip?.person_render_id
    ? workspace.person_renders.find((render) => render.id === selectedClip.person_render_id) ?? null
    : null;
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
      {selectedCue ? <section aria-label="已选字幕详情"><span className={styles.eyebrow}>已选字幕</span><h3>{selectedContext?.label}</h3><p>{selectedCue.text}</p><dl><div><dt>对应配音</dt><dd>{selectedCue.narration_segment_id ? "已关联" : "未关联"}</dd></div><div><dt>显示时长</dt><dd>{selectedCue.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>修改影响</dt><dd>仅重渲染字幕与成片</dd></div></dl></section> : null}
      {selectedClip ? <section aria-label="已选镜头详情"><span className={styles.eyebrow}>已选镜头</span><h3>{selectedContext?.label}</h3><dl><div><dt>用途</dt><dd>{selectedClip.person_render_id ? "人物口型" : selectedClip.role === "a_roll" ? "人物主镜头" : "补充画面"}</dd></div><div><dt>成片位置</dt><dd>{selectedClip.timeline_start_seconds.toFixed(1)}–{selectedClip.timeline_end_seconds.toFixed(1)} 秒</dd></div><div><dt>人物版本</dt><dd>{selectedPersonRender ? `${selectedPersonRender.person_profile_id} · ${selectedPersonRender.person_version}` : "当前任务人物"}</dd></div><div><dt>口型状态</dt><dd>{selectedPersonRender?.status === "ready" ? "已写入当前版本" : "尚未写入"}</dd></div><div><dt>锁定</dt><dd>{selectedClip.locked ? "已锁定" : "可调整"}</dd></div></dl>{!selectedClip.person_render_id ? <button className="modern-primary-button" disabled={busy} onClick={() => onChangePersonSegment(selectedClip.id)} type="button"><UserRound size={15} />换成当前人物出镜</button> : null}</section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>声音与授权</span><dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{narration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl></section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>字幕</dt><dd>{montage.subtitle_url ? "已烧录并生成 SRT" : "可修改，渲染时烧录"}</dd></div></dl></section> : null}
    </div>
  );
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
  const barRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (preview) barRef.current?.scrollIntoView?.({ block: "end" });
  }, [preview]);

  return (
    <section
      aria-label="上下文命令"
      className={[styles.contextCommandBar, preview ? styles.hasPreview : ""].filter(Boolean).join(" ")}
      ref={barRef}
    >
      <div className={styles.commandRow}>
        <span className={selectedContextLabel ? styles.contextSelection : styles.contextEmpty}>
          {selectedContextLabel ? `已选 ${selectedContextLabel}` : "先选择要修改的内容"}
        </span>
        <label className={styles.commandInput}>
          <span>修改要求</span>
          <input
            aria-label="局部修改要求"
            disabled={busy !== null || !selectedContextLabel}
            onChange={(event) => onCommandChange(event.target.value)}
            placeholder={selectedContextPlaceholder}
            value={command}
          />
        </label>
        <button
          className="modern-primary-button"
          disabled={busy !== null || !selectedContextLabel || !command.trim()}
          onClick={onPreview}
          type="button"
        >
          {busy === "edit-preview" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}
          预览修改
        </button>
      </div>
      {preview ? (
        <div aria-label="变更预览" aria-live="polite" className={styles.changePreview}>
          <header><strong>{preview.proposal.summary}</strong><span>等待确认</span></header>
          <div className={styles.previewDiff}>
            <section><small>修改前</small><strong>{preview.proposal.preview.before.label}</strong><span>{preview.proposal.preview.before.detail}</span></section>
            <section><small>修改后</small><strong>{preview.proposal.preview.after.label}</strong><span>{preview.proposal.preview.after.detail}</span></section>
          </div>
          <div className={styles.previewEffects}>
            <span><strong>将失效</strong>{preview.proposal.invalidated_refs.join("、") || "无"}</span>
            <span><strong>外部影响</strong>{preview.proposal.external_effects.join("；") || "无"}</span>
          </div>
          <div className={styles.previewActions}>
            <button className={styles.secondaryButton} disabled={busy !== null} onClick={onCancel} type="button">取消</button>
            <button className="modern-primary-button" disabled={busy !== null || preview.proposal.status !== "proposed"} onClick={onConfirm} type="button">
              {busy === "edit-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}
              确认修改
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function CreationTaskWorkspace({ initialWorkspace, initialTopics, initialTasks }: Props) {
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [activeView, setActiveView] = useState<CreationTask["stage"]>(initialWorkspace.task.stage);
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
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const [selectedNarrationSegmentId, setSelectedNarrationSegmentId] = useState<string | null>(null);
  const [editCommand, setEditCommand] = useState("");
  const [editPreview, setEditPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const task = workspace.task;
  const latestScript = workspace.scripts[0] ?? null;
  const runnableVoices = workspace.available_voices.filter((voice) => voice.runnable);
  const shellTasks = useMemo(() => [task, ...tasks.filter((item) => item.id !== task.id)], [task, tasks]);
  const selectedClipIndex = workspace.montage?.clips.findIndex((clip) => clip.id === selectedClipId) ?? -1;
  const selectedCueIndex = workspace.montage?.cues.findIndex((cue) => cue.id === selectedCueId) ?? -1;
  const selectedNarrationSegmentIndex = workspace.narration?.segments.findIndex((segment) => segment.id === selectedNarrationSegmentId) ?? -1;
  const selectedEditContext = activeView === "narration" && selectedNarrationSegmentIndex >= 0
    ? {
        kind: "narration" as const,
        id: selectedNarrationSegmentId!,
        label: `配音 ${selectedNarrationSegmentIndex + 1}`,
        placeholder: "例如：这句读慢一点",
        ref: `narration:${selectedNarrationSegmentId}`,
      }
    : activeView === "montage" && selectedCueIndex >= 0
      ? {
          kind: "cue" as const,
          id: selectedCueId!,
          label: `字幕 ${selectedCueIndex + 1}`,
          placeholder: "例如：把这句字幕改得更简洁",
          ref: `cue:${selectedCueId}`,
        }
      : activeView === "montage" && selectedClipIndex >= 0
        ? {
            kind: "clip" as const,
            id: selectedClipId!,
            label: `镜头 ${selectedClipIndex + 1}`,
            placeholder: "例如：换成另一个镜头",
            ref: `clip:${selectedClipId}`,
          }
        : null;

  async function refresh(preserveActiveView = false) {
    const next = await getCreationTaskWorkspace(task.id);
    setWorkspace(next);
    if (!preserveActiveView) setActiveView(next.task.stage);
    setSelectedClipId((current) => next.montage?.clips.some((clip) => clip.id === current) ? current : null);
    setSelectedCueId((current) => next.montage?.cues.some((cue) => cue.id === current) ? current : null);
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
      setEditPreview(null);
    }
    try {
      await operation();
      await refresh();
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

  async function showReplacementCandidates(clipId: string) {
    setBusy("clip");
    setError(null);
    try {
      setReplacementCandidates(await getCreationTaskMontageClipCandidates(task.id, clipId));
      setReplacementClipId(clipId);
    } catch (cause) {
      setError(errorDetails(cause));
    } finally {
      setBusy(null);
    }
  }

  function selectClip(clipId: string) {
    if (selectedClipId !== clipId || selectedCueId || selectedNarrationSegmentId) setEditPreview(null);
    setSelectedClipId(clipId);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(null);
  }

  function selectCue(cueId: string) {
    if (selectedCueId !== cueId || selectedClipId || selectedNarrationSegmentId) setEditPreview(null);
    setSelectedClipId(null);
    setSelectedCueId(cueId);
    setSelectedNarrationSegmentId(null);
  }

  function selectNarrationSegment(segmentId: string) {
    if (selectedNarrationSegmentId !== segmentId || selectedClipId || selectedCueId) setEditPreview(null);
    setSelectedClipId(null);
    setSelectedCueId(null);
    setSelectedNarrationSegmentId(segmentId);
  }

  async function previewEdit() {
    const rawCommand = editCommand.trim();
    if (!selectedEditContext || !rawCommand) return;
    setBusy("edit-preview");
    setError(null);
    setNotice(null);
    try {
      const preview = await previewCreationTaskEdit(task.id, {
        raw_command: rawCommand,
        context_stage: activeView,
        selected_object_refs: [selectedEditContext.ref],
        playhead_ms: null,
      });
      setEditPreview(preview);
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
      setEditPreview(null);
      setEditCommand("");
      await refresh(true);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function applyExactEdit(input: CreationTaskExactEditInput) {
    setBusy("exact-edit");
    setError(null);
    setNotice(null);
    setEditPreview(null);
    try {
      await applyCreationTaskExactEdit(task.id, input);
      await refresh(true);
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh(true).catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  async function undoTimeline() {
    setBusy("undo");
    setError(null);
    setNotice(null);
    setEditPreview(null);
    try {
      await undoCreationTaskTimeline(task.id);
      setSelectedClipId(null);
      setSelectedCueId(null);
      setSelectedNarrationSegmentId(null);
      await refresh();
    } catch (cause) {
      setError(errorDetails(cause));
      await refresh().catch(() => undefined);
    } finally {
      setBusy(null);
    }
  }

  const workspaceStatus = task.stage_status === "failed"
    ? { kind: "failed" as const, label: "需要处理" }
    : task.stage_status === "waiting_for_user"
      ? { kind: "attention" as const, label: "等待确认" }
      : { kind: "connected" as const, label: task.stage_status === "running" ? "正在处理" : "本地任务" };

  function stageView(stageId: typeof productStages[number]["id"]): CreationTask["stage"] | null {
    return stageId === "prepare" ? null : stageId;
  }

  function canOpenStage(view: CreationTask["stage"] | null): view is CreationTask["stage"] {
    if (!view) return false;
    if (view === task.stage) return true;
    if (view === "topic") return Boolean(workspace.selected_topic);
    if (view === "script") return Boolean(latestScript);
    if (view === "narration") return Boolean(workspace.narration);
    if (view === "montage") return Boolean(workspace.montage);
    return view === "review" && Boolean(workspace.montage?.output_url);
  }

  return (
    <AppShell creationTasks={shellTasks} inspector={<Inspector activeView={activeView} busy={busy !== null} error={error} onChangePersonSegment={(clipId) => void applyExactEdit({ operation_type: "ChangePersonSegment", clip_id: clipId })} selectedContext={selectedEditContext} workspace={workspace} />} inspectorTitle="任务详情" workspaceStatus={workspaceStatus}>
      <div className={styles.workspace}>
        <header className={styles.taskHeader}><div><span className={styles.eyebrow}>创作任务</span><h1>{task.name}</h1><p>{task.scenario}</p></div><div className={styles.progress}><strong>{task.progress}%</strong><span>本地真成片进度</span></div></header>
        <nav className={styles.stageRail} aria-label="制作阶段"><ol>{productStages.map((stage, index) => {
          const presentation = productStageState(stage.id, task);
          const view = stageView(stage.id);
          const openable = canOpenStage(view);
          return <li aria-current={presentation.state === "current" ? "step" : undefined} className={styles[presentation.state]} key={stage.id} title={`${stage.title}：${presentation.detail}`}>
            <button aria-label={`${stage.title}：${presentation.detail}`} aria-pressed={openable && activeView === view} disabled={!openable} onClick={() => view && setActiveView(view)} type="button">
              <span className={styles.stageMarker}>{presentation.state === "done" ? <CheckCircle2 size={14} /> : index + 1}</span>
              <span className={styles.stageCopy}><strong>{stage.label}</strong><small>{presentation.detail}</small></span>
            </button>
          </li>;
        })}</ol></nav>
        {notice ? <div className={styles.notice} role="status"><CheckCircle2 size={17} />{notice}</div> : null}

        {activeView === "topic" && task.stage === "topic" ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>选题</span><h2>{topics?.candidates.length ? "选择一个要制作的方向" : "从授权资料生成选题"}</h2><p>模型只会看到预检中列出的文字摘录；确认后不会再次生成选题。</p></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。") } type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => setSelectedTopicId(candidate.id)} type="radio" /><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId} onClick={() => selectedTopicId && run("topic-confirm", () => confirmCreationTaskTopic(task.id, selectedTopicId), "选题已确认，下一步生成文案。") } type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认选题</button></div> : null}</section> : null}
        {activeView === "topic" && task.stage !== "topic" ? <ConfirmedTopicView workspace={workspace} /> : null}

        {activeView === "script" && task.stage === "script" && !latestScript ? <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>根据已确认选题生成中文文案</h2><p>这是本任务第二次、也是最后一次文字模型调用。生成后先编辑和确认，再进入配音。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("script", () => generateCreationTaskScript(task.id), "文案已生成，等待检查。") } type="button">{busy === "script" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section> : null}
        {activeView === "script" && task.stage === "script" && latestScript ? <ScriptEditor busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections }), "文案已确认，可以生成中文配音。") } repairing={task.stage_status === "failed" && latestScript.selection_status === "confirmed"} script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && latestScript ? <ConfirmedScriptView script={latestScript} /> : null}

        {activeView === "narration" && task.stage === "narration" && !workspace.narration ? <section className={styles.workSection}><div className={styles.emptyAction}><Volume2 size={30} /><h2>使用已授权声音生成中文配音</h2><p>必须由 Qwen3-TTS 0.6B 基于已授权 WAV 和参考文字生成；运行环境缺失会明确停止。</p>{workspace.plan.selected_voice ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("narration", () => generateCreationTaskNarration(task.id), "真实配音已生成，可以制作故事板。") } type="button">{busy === "narration" ? <LoaderCircle className="is-spinning" size={16} /> : <Volume2 size={16} />}生成真实配音</button> : runnableVoices.length ? <div className={styles.voiceSelection}><label className={styles.field}><span>用于本任务的声音</span><select onChange={(event) => setSelectedVoiceId(event.target.value)} value={selectedVoiceId ?? ""}>{runnableVoices.map((voice) => <option key={`${voice.profile_id}:${voice.version}`} value={voice.profile_id}>{voice.person_display_name} · {voice.version}</option>)}</select></label><button className="modern-primary-button" disabled={busy !== null || !selectedVoiceId} onClick={() => { const voice = runnableVoices.find((item) => item.profile_id === selectedVoiceId); if (voice) void run("voice-select", () => selectCreationTaskNarrationVoice(task.id, { voice_profile_id: voice.profile_id, voice_version: voice.version }), "声音已绑定，可以生成真实配音。"); }} type="button">{busy === "voice-select" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}使用这个声音继续</button></div> : <div className={styles.voiceMissing}><AlertCircle size={18}/><div><strong>还没有可用于中文配音的授权声音</strong><p>登记人物身份、导入已授权 WAV 并逐字填写参考文字后，可回到本任务继续，不需要重做选题或文案。</p></div><Link className="modern-primary-button" href="/profiles">去登记授权声音</Link></div>}</div></section> : null}
        {activeView === "narration" && workspace.narration ? <NarrationResultView busy={busy !== null} onGeneratePersonRender={(segmentId) => { const selectedVoice = workspace.plan.selected_voice; if (selectedVoice) void run("person-render", () => generateCreationTaskPersonRender(task.id, { narration_segment_id: segmentId, person_profile_id: selectedVoice.person_profile_id, person_version: selectedVoice.person_version, motion: "natural", expression: "neutral" }), "人物口型预览已生成；尚未写入成片时间线。"); }} onSelect={selectNarrationSegment} selectedSegmentId={selectedNarrationSegmentId} workspace={workspace} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>用已审核素材生成故事板</h2><p>系统只使用当前工作区中已审核并标注用途的镜头；低置信镜头必须由你确认。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("plan", () => createCreationTaskMontagePlan(task.id), "故事板已生成。") } type="button">{busy === "plan" ? <LoaderCircle className="is-spinning" size={16} /> : <Clapperboard size={16} />}生成故事板</button></div></section> : null}
        {activeView === "montage" && workspace.montage ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>故事板、剪辑与字幕</span><h2>检查镜头、字幕和裁剪</h2><p>{workspace.montage.message}</p></div><div className={styles.storyboardTools}><span className={styles.versionBadge} title={task.current_timeline_version_id ?? "尚无时间线版本"}>当前版本 {task.timeline_sequence}</span><button className={styles.secondaryButton} disabled={busy !== null || !task.can_undo} onClick={() => void undoTimeline()} type="button">{busy === "undo" ? <LoaderCircle className="is-spinning" size={15} /> : null}撤销</button>{workspace.montage.status === "ready" || (workspace.montage.status === "rendered" && !task.current_timeline_version_id) ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("render", () => renderCreationTaskMontage(task.id), !task.current_timeline_version_id ? "已生成可继续修改的成片版本。" : "带字幕初版成片已生成，等待检查。") } type="button">{busy === "render" ? <LoaderCircle className="is-spinning" size={16} /> : <Play size={16} />}{workspace.montage.status === "rendered" ? "生成可编辑版本" : "渲染带字幕成片"}</button> : null}</div></div><div className={styles.storyboard}>{workspace.montage.clips.map((clip, index) => <article className={[clip.needs_confirmation ? styles.needsReview : "", selectedClipId === clip.id ? styles.selectedClip : ""].filter(Boolean).join(" ")} key={clip.id} onClick={() => selectClip(clip.id)}><button aria-label={`选择镜头 ${index + 1}`} aria-pressed={selectedClipId === clip.id} className={styles.clipSelector} onClick={() => selectClip(clip.id)} type="button"><span className={styles.clipIndex}>{index + 1}</span></button><div><strong>{clip.person_render_id ? "人物口型" : clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</strong>{clip.match_reasons?.length ? <p className={styles.matchReasons}>匹配理由：{clip.match_reasons.join("；")}</p> : null}<div className={[styles.subtitleEditor, selectedCueId === clip.cue_id ? styles.selectedCue : ""].filter(Boolean).join(" ")} onClick={(event) => event.stopPropagation()}><div className={styles.subtitleHeader}><button aria-label={`选择字幕 ${index + 1}`} aria-pressed={selectedCueId === clip.cue_id} onClick={() => selectCue(clip.cue_id)} type="button">字幕 {index + 1}</button></div><textarea aria-label={`镜头 ${index + 1} 字幕`} defaultValue={workspace.montage?.cues.find((cue) => cue.id === clip.cue_id)?.text ?? ""} disabled={busy !== null} key={`${clip.cue_id}:${workspace.montage?.cues.find((cue) => cue.id === clip.cue_id)?.text ?? ""}`} onBlur={(event) => { const currentText = workspace.montage?.cues.find((cue) => cue.id === clip.cue_id)?.text ?? ""; const text = event.target.value; if (text !== currentText) void applyExactEdit({ operation_type: "UpdateSubtitleCue", cue_id: clip.cue_id, text }); }} onFocus={() => selectCue(clip.cue_id)} rows={2} /></div><small>字幕时间跟随当前旁白，不会改变配音 · {clip.source_start_seconds.toFixed(1)}–{clip.source_end_seconds.toFixed(1)} 秒 · 匹配 {Math.round(clip.match_score * 100)}%</small><label className={styles.trimControl}><span>裁剪起点（秒）</span><input aria-label={`镜头 ${index + 1} 裁剪起点`} defaultValue={clip.source_start_seconds.toFixed(1)} disabled={busy !== null || clip.locked} key={`${clip.id}:${clip.source_start_seconds}`} min="0" onBlur={(event) => { const nextStart = Number(event.target.value); if (Number.isFinite(nextStart) && Math.abs(nextStart - clip.source_start_seconds) >= 0.05) void applyExactEdit({ operation_type: "TrimClip", clip_id: clip.id, source_start_seconds: nextStart }); }} step="0.1" type="number" /></label>{replacementClipId === clip.id ? <label className={styles.replacement}><span>替换为已审核镜头</span><select defaultValue="" onChange={(event) => { const candidate = replacementCandidates.find((item) => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) void applyExactEdit({ operation_type: "ReplaceClip", clip_id: clip.id, asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }); }}><option disabled value="">选择镜头</option>{replacementCandidates.map((candidate) => <option key={`${candidate.asset_id}:${candidate.segment_id}`} value={`${candidate.asset_id}:${candidate.segment_id}`}>{candidate.file_name} · {candidate.start_seconds.toFixed(1)} 秒 · {Math.round(candidate.score * 100)}%</option>)}</select></label> : null}</div><div className={styles.clipActions}><button aria-label="向前移动" disabled={busy !== null || index === 0 || clip.locked} onClick={() => void applyExactEdit({ operation_type: "ReorderClip", clip_id: clip.id, position: index - 1 })} type="button"><ArrowUp size={15} /></button><button aria-label="向后移动" disabled={busy !== null || index === workspace.montage!.clips.length - 1 || clip.locked} onClick={() => void applyExactEdit({ operation_type: "ReorderClip", clip_id: clip.id, position: index + 1 })} type="button"><ArrowDown size={15} /></button><button disabled={busy !== null} onClick={() => void showReplacementCandidates(clip.id)} type="button">替换</button><button disabled={busy !== null} onClick={() => void applyExactEdit({ operation_type: "SetClipLock", clip_id: clip.id, locked: !clip.locked })} type="button">{clip.locked ? "解锁" : "锁定"}</button>{clip.needs_confirmation ? <button disabled={busy !== null} onClick={() => void applyExactEdit({ operation_type: "ConfirmClip", clip_id: clip.id })} type="button"><Check size={15} />确认镜头</button> : <span><CheckCircle2 size={15} />已确认</span>}</div></article>)}</div></section> : null}

        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>初版成片</span><h2>播放、检查字幕并保存</h2><p>中文字幕已经烧录进画面，同时保留独立 SRT；当前任务停在等待检查。</p></div><div className={styles.reviewActions}><button className={styles.secondaryButton} disabled={busy !== null} onClick={() => setActiveView("montage")} type="button">修改镜头与字幕</button><button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div></div><div className={styles.videoReview}><video controls preload="metadata" src={apiUrl(workspace.montage.output_url)} /><div><a href={apiUrl(workspace.montage.subtitle_url ?? "")} rel="noreferrer" target="_blank">查看 SRT 字幕</a><p>成片已通过视频流、音频流、尺寸和非空校验。</p></div></div></section> : null}
        {(activeView === "montage" && workspace.montage) || (activeView === "narration" && workspace.narration) ? <ContextCommandBar busy={busy} command={editCommand} onCancel={() => setEditPreview(null)} onCommandChange={(value) => { setEditCommand(value); setEditPreview(null); }} onConfirm={() => void confirmEdit()} onPreview={() => void previewEdit()} preview={editPreview} selectedContextLabel={selectedEditContext?.label ?? null} selectedContextPlaceholder={selectedEditContext?.placeholder ?? "先选择要修改的内容"} /> : null}
      </div>
    </AppShell>
  );
}
