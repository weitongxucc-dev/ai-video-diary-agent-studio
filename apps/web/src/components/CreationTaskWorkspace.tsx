"use client";

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
  Volume2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  apiUrl,
  confirmCreationTaskScript,
  confirmCreationTaskTopic,
  createCreationTaskMontagePlan,
  CreationPlanApiError,
  generateCreationTaskNarration,
  generateCreationTaskScript,
  generateCreationTaskTopics,
  getCreationTaskMontageClipCandidates,
  getCreationTaskTopics,
  getCreationTaskWorkspace,
  renderCreationTaskMontage,
  updateCreationTaskMontageClip,
} from "@/lib/api";
import type {
  CreationTask,
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

type BusyAction = "topics" | "topic-confirm" | "script" | "script-confirm" | "narration" | "plan" | "clip" | "render" | "save";

const stageOrder = ["topic", "script", "narration", "montage", "review"] as const;
const productStages = [
  { id: "prepare", label: "资料", title: "资料准备" },
  { id: "topic", label: "选题", title: "选题" },
  { id: "script", label: "文案", title: "文案" },
  { id: "narration", label: "配音", title: "配音" },
  { id: "avatar", label: "口型", title: "人物与口型" },
  { id: "montage", label: "剪辑", title: "视频剪辑" },
  { id: "subtitle", label: "字幕", title: "字幕与封面" },
  { id: "publish", label: "检查", title: "检查与发布" },
  { id: "monitor", label: "监控", title: "数据监控" },
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

type ProductStageState = "done" | "current" | "upcoming" | "unavailable" | "partial";

function productStageState(
  stageId: typeof productStages[number]["id"],
  task: CreationTask,
  workspace: WorkspaceState,
): { state: ProductStageState; detail: string } {
  if (stageId === "prepare") return { state: "done", detail: "已完成" };
  if (stageId === "avatar") return { state: "unavailable", detail: "未接入" };
  if (stageId === "monitor") return { state: "unavailable", detail: "未接入" };
  if (stageId === "subtitle") {
    if (task.stage === "review" && workspace.montage?.subtitle_url) return { state: "partial", detail: "字幕已生成" };
    return { state: "unavailable", detail: "未接入" };
  }
  if (stageId === "publish") {
    return task.stage === "review"
      ? { state: "current", detail: "检查成片" }
      : { state: "upcoming", detail: "等待成片" };
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

function Inspector({ activeView, workspace, error }: { activeView: CreationTask["stage"]; workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null }) {
  const { task, plan, selected_topic: topic, narration, montage } = workspace;
  const latestScript = workspace.scripts[0] ?? null;
  const viewingCurrentStage = activeView === task.stage;
  return (
    <div className={styles.inspector}>
      <section>
        <span className={styles.eyebrow}>{viewingCurrentStage ? "当前状态" : "正在回看"}</span>
        <h3>{stageLabels[activeView]}</h3>
        <p>{viewingCurrentStage ? (task.stage_status === "failed" ? "当前步骤需要处理" : task.stage_status === "waiting_for_user" ? "正在等待你的确认" : task.stage_status === "running" ? "正在处理" : "可以开始下一步") : "这是已经持久化的任务结果，不会因为回看而重新运行。"}</p>
      </section>
      {viewingCurrentStage && error ? <section className={styles.blocker} role="alert"><AlertCircle size={17} /><div><strong>{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div></section> : null}
      {viewingCurrentStage && task.stage_reason ? <section className={styles.blocker}><AlertCircle size={17} /><div><strong>{task.stage_impact ?? task.stage_reason}</strong>{task.stage_repair_action ? <p>处理方式：{task.stage_repair_action}</p> : null}</div></section> : null}
      {activeView === "topic" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {activeView === "script" ? <section><span className={styles.eyebrow}>文案版本</span><dl><div><dt>标题</dt><dd>{latestScript?.title ?? "尚未生成"}</dd></div><div><dt>段落</dt><dd>{latestScript?.sections.length ?? 0} 段</dd></div><div><dt>状态</dt><dd>{latestScript?.selection_status === "confirmed" ? "已确认" : "等待确认"}</dd></div></dl></section> : null}
      {activeView === "narration" ? <section><span className={styles.eyebrow}>声音与授权</span><dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{narration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl></section> : null}
      {(activeView === "montage" || activeView === "review") && montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>字幕</dt><dd>{montage.subtitle_url ? "已生成" : "渲染时生成"}</dd></div></dl></section> : null}
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

function NarrationResultView({ workspace }: { workspace: WorkspaceState }) {
  const narration = workspace.narration;
  if (!narration) return null;
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><span>已生成配音</span><h2>试听当前声音版本</h2><p>{narration.model_id} · {narration.duration_seconds.toFixed(1)} 秒 · {narration.sample_rate} Hz</p></div><span className={styles.artifactStatus}><CheckCircle2 size={15} />已生成</span></div>
      <audio className={styles.audioPreview} controls preload="metadata" src={apiUrl(`/api/creation-tasks/${workspace.task.id}/narration/audio`)} />
    </section>
  );
}

function ScriptEditor({ script, busy, onConfirm }: { script: CreationTaskScript; busy: boolean; onConfirm: (script: CreationTaskScript) => void }) {
  const [draft, setDraft] = useState(script);
  return (
    <section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><span>文案编辑</span><h2>检查事实和口播表达</h2><p>编辑不会再次调用模型；只有“确认文案”会推进到真实配音。</p></div></div>
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })} /><p><strong>画面建议：</strong>{section.visual_prompt}</p><p><strong>来源证据：</strong>{section.evidence_reference_ids.join("、")}</p></article>)}</div>
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认文案</button></div>
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
  const [replacementClipId, setReplacementClipId] = useState<string | null>(null);
  const [replacementCandidates, setReplacementCandidates] = useState<MontageClipCandidate[]>([]);
  const task = workspace.task;
  const latestScript = workspace.scripts[0] ?? null;
  const shellTasks = useMemo(() => [task, ...tasks.filter((item) => item.id !== task.id)], [task, tasks]);

  async function refresh() {
    const next = await getCreationTaskWorkspace(task.id);
    setWorkspace(next);
    setActiveView(next.task.stage);
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

  const workspaceStatus = task.stage_status === "failed"
    ? { kind: "failed" as const, label: "需要处理" }
    : task.stage_status === "waiting_for_user"
      ? { kind: "attention" as const, label: "等待确认" }
      : { kind: "connected" as const, label: task.stage_status === "running" ? "正在处理" : "本地任务" };

  function stageView(stageId: typeof productStages[number]["id"]): CreationTask["stage"] | null {
    if (stageId === "topic" || stageId === "script" || stageId === "narration" || stageId === "montage") return stageId;
    if (stageId === "publish") return "review";
    return null;
  }

  function canOpenStage(view: CreationTask["stage"] | null): view is CreationTask["stage"] {
    if (!view) return false;
    if (view === task.stage) return true;
    if (view === "topic") return Boolean(workspace.selected_topic);
    if (view === "script") return Boolean(latestScript);
    if (view === "narration") return Boolean(workspace.narration);
    if (view === "montage") return task.stage === "montage";
    return view === "review" && Boolean(workspace.montage?.output_url);
  }

  return (
    <AppShell creationTasks={shellTasks} inspector={<Inspector activeView={activeView} error={error} workspace={workspace} />} inspectorTitle="任务详情" workspaceStatus={workspaceStatus}>
      <div className={styles.workspace}>
        <header className={styles.taskHeader}><div><span className={styles.eyebrow}>创作任务</span><h1>{task.name}</h1><p>{task.scenario}</p></div><div className={styles.progress}><strong>{task.progress}%</strong><span>本地真成片进度</span></div></header>
        <nav className={styles.stageRail} aria-label="制作阶段"><ol>{productStages.map((stage, index) => {
          const presentation = productStageState(stage.id, task, workspace);
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
        {activeView === "script" && task.stage === "script" && latestScript ? <ScriptEditor busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections }), "文案已确认，可以生成中文配音。") } script={latestScript} /> : null}
        {activeView === "script" && task.stage !== "script" && latestScript ? <ConfirmedScriptView script={latestScript} /> : null}

        {activeView === "narration" && task.stage === "narration" ? <section className={styles.workSection}><div className={styles.emptyAction}><Volume2 size={30} /><h2>使用已授权声音生成中文配音</h2><p>必须由 Qwen3-TTS 0.6B 基于已授权 WAV 和参考文字生成；运行环境缺失会明确停止。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("narration", () => generateCreationTaskNarration(task.id), "真实配音已生成，可以制作故事板。") } type="button">{busy === "narration" ? <LoaderCircle className="is-spinning" size={16} /> : <Volume2 size={16} />}生成真实配音</button></div></section> : null}
        {activeView === "narration" && task.stage !== "narration" ? <NarrationResultView workspace={workspace} /> : null}

        {activeView === "montage" && task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>用已审核素材生成故事板</h2><p>系统只使用已确认用途、版权和品牌安全的本地镜头；低置信镜头必须由你确认。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("plan", () => createCreationTaskMontagePlan(task.id), "故事板已生成。") } type="button">{busy === "plan" ? <LoaderCircle className="is-spinning" size={16} /> : <Clapperboard size={16} />}生成故事板</button></div></section> : null}
        {activeView === "montage" && task.stage === "montage" && workspace.montage ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>故事板与剪辑</span><h2>检查镜头、顺序和裁剪</h2><p>{workspace.montage.message}</p></div>{workspace.montage.status === "ready" ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("render", () => renderCreationTaskMontage(task.id), "初版成片和字幕已生成，等待检查。") } type="button">{busy === "render" ? <LoaderCircle className="is-spinning" size={16} /> : <Play size={16} />}渲染初版成片</button> : null}</div><div className={styles.storyboard}>{workspace.montage.clips.map((clip, index) => <article className={clip.needs_confirmation ? styles.needsReview : ""} key={clip.id}><div className={styles.clipIndex}>{index + 1}</div><div><strong>{clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</strong><p>{workspace.montage?.cues.find((cue) => cue.id === clip.cue_id)?.text}</p><small>{clip.source_start_seconds.toFixed(1)}–{clip.source_end_seconds.toFixed(1)} 秒 · 匹配 {Math.round(clip.match_score * 100)}%</small><label className={styles.trimControl}><span>裁剪起点（秒）</span><input aria-label={`镜头 ${index + 1} 裁剪起点`} defaultValue={clip.source_start_seconds.toFixed(1)} disabled={busy !== null || clip.locked} key={`${clip.id}:${clip.source_start_seconds}`} min="0" onBlur={(event) => { const nextStart = Number(event.target.value); if (Number.isFinite(nextStart) && Math.abs(nextStart - clip.source_start_seconds) >= 0.05) void run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { source_start_seconds: nextStart }), "裁剪起点已更新。"); }} step="0.1" type="number" /></label>{replacementClipId === clip.id ? <label className={styles.replacement}><span>替换为已审核镜头</span><select defaultValue="" onChange={(event) => { const candidate = replacementCandidates.find((item) => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) void run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }), "镜头已替换。") }}><option disabled value="">选择镜头</option>{replacementCandidates.map((candidate) => <option key={`${candidate.asset_id}:${candidate.segment_id}`} value={`${candidate.asset_id}:${candidate.segment_id}`}>{candidate.file_name} · {candidate.start_seconds.toFixed(1)} 秒 · {Math.round(candidate.score * 100)}%</option>)}</select></label> : null}</div><div className={styles.clipActions}><button aria-label="向前移动" disabled={busy !== null || index === 0 || clip.locked} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { position: index - 1 }))} type="button"><ArrowUp size={15} /></button><button aria-label="向后移动" disabled={busy !== null || index === workspace.montage!.clips.length - 1 || clip.locked} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { position: index + 1 }))} type="button"><ArrowDown size={15} /></button><button disabled={busy !== null} onClick={() => void showReplacementCandidates(clip.id)} type="button">替换</button>{clip.needs_confirmation ? <button disabled={busy !== null} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { confirmed: true }))} type="button"><Check size={15} />确认镜头</button> : <span><CheckCircle2 size={15} />已确认</span>}</div></article>)}</div></section> : null}

        {activeView === "review" && task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>初版成片</span><h2>播放、检查字幕并保存</h2><p>当前任务停在等待检查；没有虚构封面、发布或监控结果。</p></div><button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div><div className={styles.videoReview}><video controls preload="metadata" src={apiUrl(workspace.montage.output_url)} /><div><a href={apiUrl(workspace.montage.subtitle_url ?? "")} rel="noreferrer" target="_blank">查看 SRT 字幕</a><p>成片已通过视频流、音频流、尺寸和非空校验。</p></div></div></section> : null}
      </div>
    </AppShell>
  );
}
