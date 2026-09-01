"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Circle,
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  confirmCreationTaskTopic,
  CreationPlanApiError,
  generateCreationTaskTopics,
  getCreationTaskTopics,
} from "@/lib/api";
import type {
  CreationPlan,
  CreationTask,
  CreationTaskTopicCandidate,
  CreationTaskTopicsResponse,
} from "@/lib/types";
import styles from "./CreationTaskTopicWorkspace.module.css";

type TopicError = {
  message: string;
  impact: string | null;
  repairAction: string | null;
  reason: string | null;
  stateReloadMessage?: string | null;
};

type Props = {
  plan: CreationPlan;
  creationTasks: CreationTask[];
  initialTopics: CreationTaskTopicsResponse | null;
  initialError: TopicError | null;
};

const stages = ["资料准备", "选题", "文案", "配音", "人物与口型", "视频剪辑", "字幕与封面", "检查与发布", "数据监控"];
const stageClasses: Record<CreationTask["stage_status"], string> = {
  not_started: styles.stageNotStarted,
  running: styles.stageRunning,
  waiting_for_user: styles.stageWaitingForUser,
  completed: styles.stageCompleted,
  failed: styles.stageFailed,
};

function connectionLabel(availability: CreationTaskTopicsResponse["route_preflight"]["availability"]) {
  return {
    unconfigured: "未配置",
    unverified: "尚未验证",
    succeeded: "已验证",
    failed: "连接失败",
  }[availability];
}

function stageLabel(status: CreationTask["stage_status"]) {
  return {
    not_started: "等待开始",
    running: "生成中",
    waiting_for_user: "等待确认",
    completed: "已完成",
    failed: "需要处理",
  }[status];
}

function failureTitle(value: string) {
  const labels: Record<string, string> = {
    capability_unavailable: "当前文字模型不可用",
    content_sources_missing: "缺少可用于选题的资料",
    creation_plan_relation_invalid: "创作任务与已确认方案不一致",
    knowledge_reference_missing: "知识库资料已不可用",
    knowledge_reference_stale: "知识库资料已更新",
    knowledge_authorization_invalid: "知识库资料缺少授权",
    knowledge_text_unavailable: "知识库资料没有可用文字摘录",
    source_reference_missing: "信息源条目已不可用",
    source_reference_stale: "信息源条目已更新",
    source_license_unverified: "信息源许可尚未确认",
    source_text_unavailable: "信息源没有可用文字摘录",
    provider_output_invalid: "文字模型返回的选题无法使用",
  };
  if (labels[value]) return labels[value];
  return /^[a-z0-9_]+$/i.test(value) ? "当前选题暂时无法继续" : value;
}

function errorFrom(error: unknown): TopicError {
  if (error instanceof CreationPlanApiError) {
    return {
      message: error.message,
      impact: error.impact,
      repairAction: error.repairAction,
      reason: error.reason,
      stateReloadMessage: null,
    };
  }
  return { message: "本地服务未返回选题状态。", impact: null, repairAction: "检查本地服务后重新检查。", reason: null, stateReloadMessage: null };
}

function authorizationLabel(value: string) {
  const normalized = value.trim().toLowerCase();
  if (["authorized", "approved", "granted", "allowed", "已授权", "已确认", "允许使用"].includes(normalized)) return "已确认授权";
  return "授权情况待确认";
}

function licenseLabel(value: string) {
  const normalized = value.trim().toLowerCase();
  if (["authorized", "approved", "granted", "allowed", "permitted", "licensed", "已授权", "已确认", "允许使用"].includes(normalized)) return "许可已确认";
  return "许可待确认";
}

function workspaceStatusFor(topics: CreationTaskTopicsResponse | null, error: TopicError | null) {
  if (error?.stateReloadMessage) return { kind: "attention" as const, label: "选题状态重新读取失败" };
  if (!topics) return { kind: "attention" as const, label: error?.stateReloadMessage ? "选题状态重新读取失败" : "选题状态暂时不可读" };
  if (topics.egress_preflight.reason || topics.route_preflight.availability === "unconfigured") return { kind: "attention" as const, label: "选题准备存在阻断" };
  if (topics.task.stage_status === "failed") return { kind: "attention" as const, label: "选题需要处理" };
  if (topics.task.stage_status === "running") return { kind: "connected" as const, label: "选题生成中" };
  if (topics.task.stage_status === "waiting_for_user") return { kind: "connected" as const, label: "等待确认选题" };
  if (topics.task.stage_status === "completed") return { kind: "connected" as const, label: "选题已完成" };
  return { kind: "connected" as const, label: "选题等待开始" };
}

function safeLink(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? value : null;
  } catch {
    return null;
  }
}

function TopicInspector({ topics, selectedTopicId, error }: {
  topics: CreationTaskTopicsResponse | null;
  selectedTopicId: string | null;
  error: TopicError | null;
}) {
  if (!topics) {
    return <div className={styles.inspectorEmpty}>
      <AlertCircle size={18} />
      <strong>无法读取选题状态</strong>
      <p>{error ? failureTitle(error.reason ?? error.message) : "本地服务未返回选题预检。"}</p>
      {error?.stateReloadMessage ? <p>{error.stateReloadMessage}</p> : null}
      {error?.repairAction ? <p>处理方式：{error.repairAction}</p> : null}
    </div>;
  }

  const selectedEvidence = topics.evidence_references.filter((item) => item.candidate_id === selectedTopicId);
  const failure = error ?? (topics.task.stage_reason ? {
    message: topics.task.stage_reason,
    impact: topics.task.stage_impact,
    repairAction: topics.task.stage_repair_action,
    reason: topics.task.stage_reason,
    stateReloadMessage: null,
  } : null) ?? (topics.egress_preflight.reason ? {
    message: topics.egress_preflight.reason,
    impact: topics.egress_preflight.impact,
    repairAction: topics.egress_preflight.repair_action,
    reason: topics.egress_preflight.reason,
    stateReloadMessage: null,
  } : null);

  return <div className={styles.inspector}>
    <section className={styles.connection}>
      <span className={styles.eyebrow}>文字模型连接</span>
      <strong>{connectionLabel(topics.route_preflight.availability)}</strong>
      <p>{topics.route_preflight.execution_location === "cloud" ? "云端文字模型" : "本地文字模型"} · {topics.route_preflight.model_id}</p>
    </section>

    <dl className={styles.facts}>
      <div><dt>资料数量</dt><dd>{topics.egress_preflight.source_count}</dd></div>
      <div><dt>文字摘录</dt><dd>{topics.egress_preflight.text_excerpt_count}</dd></div>
      <div><dt>当前阶段</dt><dd>{stageLabel(topics.task.stage_status)}</dd></div>
    </dl>

    {failure ? <section className={styles.failure} role="alert">
      <AlertCircle size={16} />
      <div><strong>{failureTitle(failure.reason ?? failure.message)}</strong>{failure.impact ? <p>{failure.impact}</p> : null}{failure.repairAction ? <p>处理方式：{failure.repairAction}</p> : null}{failure.stateReloadMessage ? <p>{failure.stateReloadMessage}</p> : null}</div>
    </section> : null}

    <section className={styles.evidence}>
      <header><span className={styles.eyebrow}>当前选题依据</span><strong>{selectedEvidence.length ? `${selectedEvidence.length} 条资料` : "选择候选后查看"}</strong></header>
      {selectedEvidence.length ? <ul>{selectedEvidence.map((item) => {
        const href = safeLink(item.source_url);
        return <li key={item.id}>
          <div><strong>{item.title}</strong><span>{item.kind === "knowledge" ? "知识库" : "信息源"} · {item.kind === "knowledge" ? authorizationLabel(item.authorization_or_license) : licenseLabel(item.authorization_or_license)}</span></div>
          {href ? <a aria-label={`查看来源：${item.title}`} href={href} rel="noreferrer" target="_blank"><ExternalLink size={15} /></a> : null}
        </li>;
      })}</ul> : <p className={styles.emptyNote}>候选将显示与它对应的已授权资料。</p>}
    </section>
  </div>;
}

function CandidateCard({ candidate, evidenceCount, selected, onSelect }: {
  candidate: CreationTaskTopicCandidate;
  evidenceCount: number;
  selected: boolean;
  onSelect: () => void;
}) {
  return <article className={`${styles.candidate}${selected ? ` ${styles.candidateSelected}` : ""}`}>
    <header><button aria-label={`${selected ? "已选择" : "选择此选题"}：${candidate.title}`} aria-pressed={selected} className={styles.candidateChoice} onClick={onSelect} type="button">{selected ? <CircleDot size={17} /> : <Circle size={17} />}{selected ? "已选择" : "选择此选题"}</button></header>
    <strong>{candidate.title}</strong>
    <dl><div><dt>角度</dt><dd>{candidate.angle}</dd></div><div><dt>面向</dt><dd>{candidate.audience}</dd></div></dl>
    <ol>{candidate.outline.map((item, index) => <li key={`${candidate.id}-${index}`}>{item}</li>)}</ol>
    <footer><span>依据 {evidenceCount} 条资料</span><span>综合分 {candidate.score}</span></footer>
  </article>;
}

export function CreationTaskTopicWorkspace({ plan, creationTasks, initialTopics, initialError }: Props) {
  const router = useRouter();
  const [topics, setTopics] = useState(initialTopics);
  const [error, setError] = useState<TopicError | null>(initialError);
  const [action, setAction] = useState<"generate" | "confirm" | "refresh" | null>(null);
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(
    initialTopics?.selected_topic_id ?? null,
  );
  const actionLock = useRef(false);
  const task = topics?.task ?? plan.creation_task;
  const currentCreationTasks = creationTasks.some((item) => item.id === task.id)
    ? creationTasks.map((item) => item.id === task.id ? task : item)
    : [task, ...creationTasks];

  useEffect(() => {
    if (!topics) return;
    setSelectedTopicId((current) => {
      if (current && topics.candidates.some((item) => item.id === current)) return current;
      return topics.selected_topic_id && topics.candidates.some((item) => item.id === topics.selected_topic_id)
        ? topics.selected_topic_id
        : null;
    });
  }, [topics]);

  async function refreshTopics() {
    if (actionLock.current) return;
    actionLock.current = true;
    setAction("refresh");
    setError(null);
    try {
      const response = await getCreationTaskTopics(task.id);
      setTopics(response);
      router.refresh();
    } catch (nextError) {
      setError(errorFrom(nextError));
    } finally {
      actionLock.current = false;
      setAction(null);
    }
  }

  async function refreshAfterMutationFailure(mutationError: TopicError) {
    try {
      setTopics(await getCreationTaskTopics(task.id));
      setError(mutationError);
    } catch (reloadError) {
      const reload = errorFrom(reloadError);
      setError({
        ...mutationError,
        stateReloadMessage: `任务状态重新读取失败：${failureTitle(reload.reason ?? reload.message)}`,
      });
    }
  }

  async function generateTopics() {
    if (actionLock.current) return;
    actionLock.current = true;
    setAction("generate");
    setError(null);
    try {
      const response = await generateCreationTaskTopics(task.id);
      setTopics(response);
      router.refresh();
    } catch (nextError) {
      await refreshAfterMutationFailure(errorFrom(nextError));
    } finally {
      actionLock.current = false;
      setAction(null);
    }
  }

  async function confirmTopic() {
    if (!selectedTopicId || actionLock.current) return;
    actionLock.current = true;
    setAction("confirm");
    setError(null);
    try {
      const response = await confirmCreationTaskTopic(task.id, selectedTopicId);
      setTopics(response);
      router.refresh();
    } catch (nextError) {
      await refreshAfterMutationFailure(errorFrom(nextError));
    } finally {
      actionLock.current = false;
      setAction(null);
    }
  }

  const preflightBlocked = Boolean(topics?.egress_preflight.reason)
    || topics?.route_preflight.availability === "unconfigured";
  const canRetry = task.stage_status !== "failed" || task.stage_retryable !== false;
  const selectedCandidate = topics?.candidates.find((item) => item.id === (topics.selected_topic_id ?? selectedTopicId)) ?? null;

  const topicContent = !topics ? <section className={styles.unavailable} role="alert">
    <AlertCircle size={20} />
    <div><h2>无法读取选题预检</h2><p>{error ? failureTitle(error.reason ?? error.message) : "本地服务未返回当前任务的选题状态。"}</p>{error?.impact ? <p>{error.impact}</p> : null}{error?.repairAction ? <p>处理方式：{error.repairAction}</p> : null}{error?.stateReloadMessage ? <p>{error.stateReloadMessage}</p> : null}</div>
    <button className="modern-primary-button" disabled={action === "refresh"} onClick={refreshTopics} type="button"><RefreshCw size={16} />重新检查</button>
  </section> : task.stage_status === "waiting_for_user" ? <section className={styles.topicPanel}>
    <header className={styles.panelHeader}><div><span>选择一个方向</span><h2>选题候选</h2><p>候选和依据均已由本次服务端生成并保存。确认前可切换查看。</p></div><span className={styles.status}>等待确认</span></header>
    {topics.candidates.length ? <div className={styles.candidates}>{topics.candidates.map((candidate) => <CandidateCard candidate={candidate} evidenceCount={topics.evidence_references.filter((item) => item.candidate_id === candidate.id).length} key={candidate.id} onSelect={() => setSelectedTopicId(candidate.id)} selected={selectedTopicId === candidate.id} />)}</div> : <div className={styles.unavailable}><AlertCircle size={20} /><div><h2>没有可确认的选题</h2><p>服务端没有返回已保存候选，请重新检查任务状态。</p></div><button onClick={refreshTopics} type="button">重新检查</button></div>}
    <footer className={styles.actionBar}><p>{selectedTopicId ? "确认后会完成选题阶段，不会生成文案。" : "请选择一个选题后再确认。"}</p><button className="modern-primary-button" disabled={!selectedTopicId || action !== null} onClick={confirmTopic} type="button">{action === "confirm" ? <><LoaderCircle className="is-spinning" size={16} />正在确认</> : <><CheckCircle2 size={16} />确认这个选题</>}</button></footer>
  </section> : task.stage_status === "completed" ? <section className={styles.completed}>
    <CheckCircle2 size={21} />
    <div><span>选题已完成</span><h2>{selectedCandidate?.title ?? "已确认选题"}</h2><p>{selectedCandidate ? `${selectedCandidate.angle} · 面向 ${selectedCandidate.audience}` : "服务端已确认本次选题。"}</p><strong>下一步：生成文案（尚未接入）</strong></div>
  </section> : <section className={styles.topicPanel}>
    <header className={styles.panelHeader}><div><span>{task.stage_status === "failed" ? "需要恢复" : "开始前检查"}</span><h2>{task.stage_status === "failed" ? "选题未完成" : "开始生成选题"}</h2><p>{topics.route_preflight.execution_location === "cloud" ? `将使用云端文字模型，并只发送 ${topics.egress_preflight.text_excerpt_count} 条已授权文字摘录。` : `将使用本地文字模型处理 ${topics.egress_preflight.text_excerpt_count} 条已授权文字摘录，不会发送到云端。`}</p></div><span className={styles.status}>{stageLabel(task.stage_status)}</span></header>
    <div className={styles.preflightGrid}><article><span>模型</span><strong>{topics.route_preflight.model_id}</strong><p>{topics.route_preflight.execution_location === "cloud" ? "云端文字模型" : "本地文字模型"} · {connectionLabel(topics.route_preflight.availability)}</p></article><article><span>资料</span><strong>{topics.egress_preflight.source_count} 条已授权资料</strong><p>将读取 {topics.egress_preflight.text_excerpt_count} 条文字摘录。</p></article></div>
    <section className={styles.preflightSources}><span>本次选题将使用的已确认资料</span>{plan.knowledge_references.length || plan.source_references.length ? <ul>{plan.knowledge_references.map((item) => <li key={`${item.id}-${item.version}`}><strong>{item.title}</strong><small>知识库 · {authorizationLabel(item.authorization)}</small></li>)}{plan.source_references.map((item) => <li key={item.id}><strong>{item.title}</strong><small>信息源 · {licenseLabel(item.license)}</small></li>)}</ul> : <p>当前方案没有保存可用于选题的资料。</p>}</section>
    {preflightBlocked ? <div className={styles.blocked} role="alert"><AlertCircle size={18} /><div><strong>{topics.egress_preflight.reason ? failureTitle(topics.egress_preflight.reason) : "当前文字模型尚未配置"}</strong>{topics.egress_preflight.impact ? <p>{topics.egress_preflight.impact}</p> : null}{topics.egress_preflight.repair_action ? <p>处理方式：{topics.egress_preflight.repair_action}</p> : null}</div></div> : null}
    {error ? <div className={styles.blocked} role="alert"><AlertCircle size={18} /><div><strong>{failureTitle(error.reason ?? error.message)}</strong>{error.impact ? <p>{error.impact}</p> : null}{error.repairAction ? <p>处理方式：{error.repairAction}</p> : null}{error.stateReloadMessage ? <p>{error.stateReloadMessage}</p> : null}</div></div> : null}
    <footer className={styles.actionBar}><p>{topics.route_preflight.execution_location === "cloud" ? "点击即确认本次云端文字处理。" : "点击即开始本机文字处理。"}</p><button className="modern-primary-button" disabled={preflightBlocked || !canRetry || action !== null || task.stage_status === "running"} onClick={generateTopics} type="button">{action === "generate" || task.stage_status === "running" ? <><LoaderCircle className="is-spinning" size={16} />生成中</> : task.stage_status === "failed" ? <><RefreshCw size={16} />重新生成选题</> : <><Sparkles size={16} />开始选题</>}</button></footer>
  </section>;

  return <AppShell creationTasks={currentCreationTasks} inspector={<TopicInspector error={error} selectedTopicId={selectedTopicId} topics={topics} />} inspectorTitle="选题详情" workspaceStatus={workspaceStatusFor(topics, error)}>
    <div className={styles.workspace}>
      <header className={styles.header}><div><span>创作任务</span><h1>{plan.name}</h1><p>{plan.goal}</p></div><Link className={styles.backToList} href="/production">返回任务列表</Link></header>
      <ol className={styles.stages} aria-label="制作阶段">{stages.map((stage, index) => {
        const isTopic = index === 1;
        const status = index === 0 ? "completed" : isTopic ? task.stage_status : "not_started";
        return <li className={stageClasses[status]} key={stage}><span>{index + 1}</span><div><strong>{stage}</strong><small>{index === 0 ? "已完成" : isTopic ? stageLabel(task.stage_status) : "尚未开始"}</small></div></li>;
      })}</ol>
      {topicContent}
    </div>
  </AppShell>;
}
