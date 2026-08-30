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

function Inspector({ workspace, error }: { workspace: WorkspaceState; error: ReturnType<typeof errorDetails> | null }) {
  const { task, plan, selected_topic: topic, narration, montage } = workspace;
  return (
    <div className={styles.inspector}>
      <section>
        <span className={styles.eyebrow}>当前状态</span>
        <h3>{stageLabels[task.stage]}</h3>
        <p>{task.stage_status === "failed" ? "当前步骤需要处理" : task.stage_status === "waiting_for_user" ? "正在等待你的确认" : task.stage_status === "running" ? "正在处理" : "可以开始下一步"}</p>
      </section>
      {error ? <section className={styles.blocker} role="alert"><AlertCircle size={17} /><div><strong>{error.message}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}</div></section> : null}
      {task.stage_reason ? <section className={styles.blocker}><AlertCircle size={17} /><div><strong>{task.stage_impact ?? task.stage_reason}</strong>{task.stage_repair_action ? <p>处理方式：{task.stage_repair_action}</p> : null}</div></section> : null}
      {task.stage === "topic" || task.stage === "script" ? <section><span className={styles.eyebrow}>事实来源</span><dl><div><dt>知识库</dt><dd>{plan.knowledge_references.length} 条</dd></div><div><dt>信息源</dt><dd>{plan.source_references.length} 条</dd></div><div><dt>已确认选题</dt><dd>{topic?.title ?? "尚未确认"}</dd></div></dl></section> : null}
      {task.stage === "narration" || narration ? <section><span className={styles.eyebrow}>声音与授权</span><dl><div><dt>声音版本</dt><dd>{plan.selected_voice?.version ?? "未选择"}</dd></div><div><dt>语言</dt><dd>中文</dd></div><div><dt>模型</dt><dd>{narration?.model_id ?? plan.selected_voice?.model_id ?? "未就绪"}</dd></div><div><dt>授权</dt><dd>{plan.selected_voice ? "已纳入方案" : "缺失"}</dd></div></dl></section> : null}
      {montage ? <section><span className={styles.eyebrow}>剪辑证据</span><dl><div><dt>镜头</dt><dd>{montage.clips.length} 个</dd></div><div><dt>需要确认</dt><dd>{montage.low_confidence_clip_count} 个</dd></div><div><dt>时长</dt><dd>{montage.duration_seconds.toFixed(1)} 秒</dd></div><div><dt>字幕</dt><dd>{montage.subtitle_url ? "已生成" : "渲染时生成"}</dd></div></dl></section> : null}
    </div>
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
  const currentIndex = stageIndex(task.stage);
  const shellTasks = useMemo(() => [task, ...tasks.filter((item) => item.id !== task.id)], [task, tasks]);

  async function refresh() {
    const next = await getCreationTaskWorkspace(task.id);
    setWorkspace(next);
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

  return (
    <AppShell creationTasks={shellTasks} inspector={<Inspector error={error} workspace={workspace} />} inspectorTitle="任务详情" workspaceStatus={workspaceStatus}>
      <div className={styles.workspace}>
        <header className={styles.taskHeader}><div><span className={styles.eyebrow}>创作任务</span><h1>{task.name}</h1><p>{task.scenario}</p></div><div className={styles.progress}><strong>{task.progress}%</strong><span>本地真成片进度</span></div></header>
        <nav className={styles.stageRail} aria-label="制作阶段"><span className={styles.prepared}><CheckCircle2 size={15} />资料准备</span>{stageOrder.map((stage, index) => <span className={index < currentIndex ? styles.done : index === currentIndex ? styles.current : ""} key={stage}>{index < currentIndex ? <CheckCircle2 size={15} /> : <span>{index + 1}</span>}{stageLabels[stage]}</span>)}</nav>
        {notice ? <div className={styles.notice} role="status"><CheckCircle2 size={17} />{notice}</div> : null}

        {task.stage === "topic" ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>选题</span><h2>{topics?.candidates.length ? "选择一个要制作的方向" : "从授权资料生成选题"}</h2><p>模型只会看到预检中列出的文字摘录；确认后不会再次生成选题。</p></div>{!topics?.candidates.length ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("topics", () => generateCreationTaskTopics(task.id), "选题已生成，等待选择。") } type="button">{busy === "topics" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成选题</button> : null}</div>{topics?.candidates.length ? <div className={styles.topicList}>{topics.candidates.map((candidate) => <label className={selectedTopicId === candidate.id ? styles.selected : ""} key={candidate.id}><input checked={selectedTopicId === candidate.id} name="topic" onChange={() => setSelectedTopicId(candidate.id)} type="radio" /><div><header><strong>{candidate.title}</strong><span>{candidate.score} 分</span></header><p>{candidate.angle}</p><small>适合：{candidate.audience}</small><ol>{candidate.outline.map((item) => <li key={item}>{item}</li>)}</ol></div></label>)}</div> : null}{topics?.candidates.length ? <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy !== null || !selectedTopicId} onClick={() => selectedTopicId && run("topic-confirm", () => confirmCreationTaskTopic(task.id, selectedTopicId), "选题已确认，下一步生成文案。") } type="button">{busy === "topic-confirm" ? <LoaderCircle className="is-spinning" size={16} /> : <Check size={16} />}确认选题</button></div> : null}</section> : null}

        {task.stage === "script" && !latestScript ? <section className={styles.workSection}><div className={styles.emptyAction}><FileText size={28} /><h2>根据已确认选题生成中文文案</h2><p>这是本任务第二次、也是最后一次文字模型调用。生成后先编辑和确认，再进入配音。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("script", () => generateCreationTaskScript(task.id), "文案已生成，等待检查。") } type="button">{busy === "script" ? <LoaderCircle className="is-spinning" size={16} /> : <Sparkles size={16} />}生成文案</button></div></section> : null}
        {task.stage === "script" && latestScript ? <ScriptEditor busy={busy !== null} key={latestScript.updated_at} onConfirm={(draft) => run("script-confirm", () => confirmCreationTaskScript(task.id, draft.id, { title: draft.title, sections: draft.sections }), "文案已确认，可以生成中文配音。") } script={latestScript} /> : null}

        {task.stage === "narration" ? <section className={styles.workSection}><div className={styles.emptyAction}><Volume2 size={30} /><h2>使用已授权声音生成中文配音</h2><p>必须由 Qwen3-TTS 0.6B 基于已授权 WAV 和参考文字生成；运行环境缺失会明确停止。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("narration", () => generateCreationTaskNarration(task.id), "真实配音已生成，可以制作故事板。") } type="button">{busy === "narration" ? <LoaderCircle className="is-spinning" size={16} /> : <Volume2 size={16} />}生成真实配音</button></div></section> : null}

        {task.stage === "montage" && !workspace.montage ? <section className={styles.workSection}><div className={styles.emptyAction}><Clapperboard size={30} /><h2>用已审核素材生成故事板</h2><p>系统只使用已确认用途、版权和品牌安全的本地镜头；低置信镜头必须由你确认。</p><button className="modern-primary-button" disabled={busy !== null} onClick={() => run("plan", () => createCreationTaskMontagePlan(task.id), "故事板已生成。") } type="button">{busy === "plan" ? <LoaderCircle className="is-spinning" size={16} /> : <Clapperboard size={16} />}生成故事板</button></div></section> : null}
        {task.stage === "montage" && workspace.montage ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>故事板与剪辑</span><h2>检查镜头、顺序和裁剪</h2><p>{workspace.montage.message}</p></div>{workspace.montage.status === "ready" ? <button className="modern-primary-button" disabled={busy !== null} onClick={() => run("render", () => renderCreationTaskMontage(task.id), "初版成片和字幕已生成，等待检查。") } type="button">{busy === "render" ? <LoaderCircle className="is-spinning" size={16} /> : <Play size={16} />}渲染初版成片</button> : null}</div><div className={styles.storyboard}>{workspace.montage.clips.map((clip, index) => <article className={clip.needs_confirmation ? styles.needsReview : ""} key={clip.id}><div className={styles.clipIndex}>{index + 1}</div><div><strong>{clip.role === "a_roll" ? "人物主镜头" : "补充画面"}</strong><p>{workspace.montage?.cues.find((cue) => cue.id === clip.cue_id)?.text}</p><small>{clip.source_start_seconds.toFixed(1)}–{clip.source_end_seconds.toFixed(1)} 秒 · 匹配 {Math.round(clip.match_score * 100)}%</small><label className={styles.trimControl}><span>裁剪起点（秒）</span><input aria-label={`镜头 ${index + 1} 裁剪起点`} defaultValue={clip.source_start_seconds.toFixed(1)} disabled={busy !== null || clip.locked} key={`${clip.id}:${clip.source_start_seconds}`} min="0" onBlur={(event) => { const nextStart = Number(event.target.value); if (Number.isFinite(nextStart) && Math.abs(nextStart - clip.source_start_seconds) >= 0.05) void run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { source_start_seconds: nextStart }), "裁剪起点已更新。"); }} step="0.1" type="number" /></label>{replacementClipId === clip.id ? <label className={styles.replacement}><span>替换为已审核镜头</span><select defaultValue="" onChange={(event) => { const candidate = replacementCandidates.find((item) => `${item.asset_id}:${item.segment_id}` === event.target.value); if (candidate) void run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { asset_id: candidate.asset_id, segment_id: candidate.segment_id, source_start_seconds: candidate.start_seconds }), "镜头已替换。") }}><option disabled value="">选择镜头</option>{replacementCandidates.map((candidate) => <option key={`${candidate.asset_id}:${candidate.segment_id}`} value={`${candidate.asset_id}:${candidate.segment_id}`}>{candidate.file_name} · {candidate.start_seconds.toFixed(1)} 秒 · {Math.round(candidate.score * 100)}%</option>)}</select></label> : null}</div><div className={styles.clipActions}><button aria-label="向前移动" disabled={busy !== null || index === 0 || clip.locked} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { position: index - 1 }))} type="button"><ArrowUp size={15} /></button><button aria-label="向后移动" disabled={busy !== null || index === workspace.montage!.clips.length - 1 || clip.locked} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { position: index + 1 }))} type="button"><ArrowDown size={15} /></button><button disabled={busy !== null} onClick={() => void showReplacementCandidates(clip.id)} type="button">替换</button>{clip.needs_confirmation ? <button disabled={busy !== null} onClick={() => run("clip", () => updateCreationTaskMontageClip(task.id, clip.id, { confirmed: true }))} type="button"><Check size={15} />确认镜头</button> : <span><CheckCircle2 size={15} />已确认</span>}</div></article>)}</div></section> : null}

        {task.stage === "review" && workspace.montage?.output_url ? <section className={styles.workSection}><div className={styles.sectionHeading}><div><span>初版成片</span><h2>播放、检查字幕并保存</h2><p>当前任务停在等待检查；没有虚构封面、发布或监控结果。</p></div><button className="modern-primary-button" disabled={busy !== null} onClick={() => void saveVideo()} type="button">{busy === "save" ? <LoaderCircle className="is-spinning" size={16} /> : <Save size={16} />}保存到本地</button></div><div className={styles.videoReview}><video controls preload="metadata" src={apiUrl(workspace.montage.output_url)} /><div><a href={apiUrl(workspace.montage.subtitle_url ?? "")} rel="noreferrer" target="_blank">查看 SRT 字幕</a><p>成片已通过视频流、音频流、尺寸和非空校验。</p></div></div></section> : null}
      </div>
    </AppShell>
  );
}
