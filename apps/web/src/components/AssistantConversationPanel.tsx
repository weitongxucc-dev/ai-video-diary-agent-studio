"use client";
import { InputGroup, InputGroupTextarea } from "./ui/input-group";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/tooltip";
import { segmentRevisionStatus, segmentAdoptionReason, editExecutionReadinessMessage } from './creation-task/stage-labels';

import { ConversationMessage } from "./ui/conversation-message";
import { AssistantTurnExecutionRecord, TurnActivity, TurnToolActivity, hasPrivateExecutionText, publicExecutionText, turnBlocks, type TurnReadingState } from "./AssistantTurnExecutionRecord";
import { ReferencePicker } from "./ui/reference-picker";
import type { MaterialUsageContext } from "./ui/material-usage";
import { ReferenceChips } from "./ui/reference-chips";

import { Children, Fragment, useEffect, useLayoutEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { useAssistantReferenceStatus, useAssistantSelection } from "./AssistantConversationProvider";
import { HomeCardDialog } from "./HomeCardDialog";
import "./AssistantConversation.css";
import { Plus, History, Pencil, CircleHelp, ArrowUp, ArrowDown, Copy, Check, MoreHorizontal, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { createPortal } from "react-dom";
import remarkGfm from "remark-gfm";
import Link from "next/link";
import { getAssistantCapabilities, taskMediaJobLabel, taskMediaResourceCheckLabel } from "@/lib/api";
import type { AssistantConversationTurn, AssistantScriptToolReceipt, ResourceAdmissionReceipt } from "@/lib/types";
import type { AssistantInputReceipt, AssistantStoryboardRegeneration, AssistantScriptWindowRequest, AssistantResultStage, OpenAssistantResult } from "@/lib/types/assistant";
import { savedAssistantResultLocation } from "./creation-task/assistant-result-location";
import { completedAudioReplyMatchesReceipt } from "./creation-task/assistant-result-audio";
import type { useAssistantConversation } from "@/components/useAssistantConversation";
import { assistantTaskTurnResult, assistantTurnNeedsResultRefresh, assistantTurnHasUnknownRequest, assistantConversationGoal, assistantTurnScriptWindowRequest, assistantScriptWindowReadKey, assistantTurnGoalRefreshState } from "./useAssistantConversation";

const commandDescriptions: Record<string, { title: string; description: string; icon: typeof Plus }> = {
  "/new": { title: "新对话", description: "保留历史，开始新一段对话", icon: Plus },
  "/resume": { title: "恢复对话", description: "选择已有对话继续", icon: History },
  "/name": { title: "命名对话", description: "为当前对话设置名称", icon: Pencil },
  "/help": { title: "查看命令", description: "查看已接通的对话操作", icon: CircleHelp },
};

type Conversation = ReturnType<typeof useAssistantConversation>;
type ReadingState = TurnReadingState;
const isNearBottom = (node: HTMLDivElement) => node.scrollHeight - node.clientHeight - node.scrollTop <= 48;

function ReadingDetails({ reading, readingKey, defaultOpen = false, className, children }: {
  reading?: ReadingState; readingKey: string; defaultOpen?: boolean; className?: string; children: ReactNode;
}) {
  return <details className={className} data-reading-key={readingKey} open={reading?.get(readingKey, defaultOpen) ?? defaultOpen}
    onToggle={event => reading?.save(readingKey, event.currentTarget.open)}>{children}</details>;
}

function TurnResult({ summary, reading, readingKey, showDetails, children }: {
  summary: string | null; reading?: ReadingState; readingKey: string; showDetails: boolean; children: ReactNode;
}) {
  const details = Children.toArray(children);
  if (!summary && !details.length) return null;
  return <div className="assistant-turn-result">
    {summary ? <div className="assistant-result-summary"><AssistantFailureMessage reading={reading} readingKey={`${readingKey}:diagnostic`} message={summary}/></div> : null}
    {details.length ? <ReadingDetails reading={reading} readingKey={readingKey} defaultOpen={showDetails} className="assistant-result-disclosure">
      <summary>回执详情</summary><div className="assistant-result-details">{details}</div>
    </ReadingDetails> : null}
  </div>;
}

const goalStateLabels: Record<string, string> = {
  running: "正在执行", waiting_result: "等待后台结果", waiting_adoption: "等待查看并采用修改",
  waiting_identity: "等待核验身份", waiting_unknown: "请求结果待核实", invalidated: "原目标已停止接续",
  no_progress: "接续未取得新进展", limit_reached: "接续已暂停",
  production_completed: "成片已生成", production_failed: "制作未完成", output_checked: "输出检查已返回",
};
const goalReasonLabels: Record<string, string> = {
  output_not_checked: "成片已生成；输出尚未检查。",
  await_real_adoption: "实际预览已返回，请查看修改方案后决定是否采用。",
  await_actual_preview_result: "正在等待原预览作业返回，草稿尚未改变。",
  read_original_request_result: "先查询原请求的实际结果，避免重复提交。",
  request_effect_not_verified: "原请求的影响尚未核实，先查询实际结果。",
  continue_after_real_adoption: "修改已实际采用，继续完成原要求。",
  original_execution_not_settled: "原制作作业尚未结束，继续等待实际结果。",
  original_media_job_not_settled: "原媒体作业尚未结束，继续等待实际结果。",
  interactive_identity_not_available: "当前身份尚未核验，暂不能继续操作。",
  current_task_or_member_not_authorized: "当前任务或成员权限已改变，原目标不再自动执行。",
  original_request_authority_or_version_rejected: "原请求的权限或版本核验未通过，需要重新确认当前对象。",
  original_execution_no_longer_current: "原制作作业已被替代，不能沿旧结果继续执行。",
  original_media_job_no_longer_authorized: "原媒体作业已不再可访问，不能继续采用它的结果。",
  unrelated_timeline_change: "剪辑版本已发生其他修改，原目标不再沿旧版本执行。",
  completed_source_no_longer_authorized_or_current: "原素材结果的权限或版本已改变，需要重新核验。",
  original_reference_authorization_changed: "原引用的权限已改变，暂不能继续操作。",
  adoption_result_not_current: "已采用的修改不再是当前版本，需要重新核验。",
  new_user_turn: "你已发送新要求，原目标停止自动接续。",
  new_user_instruction: "后续消息已结束这次自动接续；视频任务和素材仍保留。",
  continue_adopted_creation_task: "视频任务已创建，尚未开始制作。",
  no_new_business_action: "本轮没有取得新的业务动作；已有结果仍保留。",
};
const executionStageLabels: Record<string, string> = {
  admission: "制作条件", narration: "配音", person_render: "人物画面", montage_plan: "镜头编排", render: "生成成片", cover: "封面",
};
const executionStatusLabels: Record<string, string> = {
  queued: "已排队", pending: "待执行", running: "正在运行", succeeded: "已完成", failed: "未完成",
  completed: "已完成", not_started: "未开始", cancelled: "已取消", interrupted: "已中断", skipped: "未执行", stale: "旧结果失效",
};

function completedGoalOutput(value: NonNullable<ReturnType<typeof assistantConversationGoal>>) {
  return value.goal.status === "output_checked" || value.goal.status === "no_progress" && value.goal.waiting_reason === "bounded_output_checkpoint_completed"
    ? value.goal.completed_output_checkpoint : undefined;
}

function GoalProgress({ value, reading, refreshPaused, onRead, readingResult }: {
  value: NonNullable<ReturnType<typeof assistantConversationGoal>>; reading: ReadingState;
  refreshPaused: boolean; onRead: () => void; readingResult: boolean;
}) {
  const {root, goal, turns} = value;
  const execution = [...turns].reverse().map(turn => assistantTaskTurnResult(turn)?.response?.execution_status)
    .find(value => value && (!goal.production_result || value.job?.id === goal.production_result.job_id && value.job?.status === goal.production_result.status));
  const job = execution?.job;
  const videoReady = goal.status !== "invalidated" && (goal.production_result?.video_ready === true || execution?.video_ready === true);
  const media = [...turns].reverse().map(turn => assistantTaskTurnResult(turn)?.response?.media_job).find(Boolean);
  const reason = goal.waiting_reason && goalReasonLabels[goal.waiting_reason];
  const completed = completedGoalOutput(value);
  const active = ["running", "waiting_result", "waiting_adoption", "waiting_identity", "waiting_unknown"].includes(goal.status);
  const currentStep = goal.status === "waiting_result" && job ? `${executionStageLabels[execution?.current_stage ?? ""] ?? "制作作业"}：${executionStatusLabels[job.status] ?? "状态待核实"}`
    : goal.status === "waiting_result" && media ? `${String(media.result_kind) === "observation" ? "输出检查" : media.result_kind === "graphic_source" ? "准备图片" : "后台预览"}：${executionStatusLabels[media.status] ?? "状态待核实"}`
      : reason;
  return <ReadingDetails reading={reading} readingKey={`goal:${root.id}`} className="assistant-goal-progress">
    <summary aria-label="本次目标执行状态"><span>{completed ? "制作与技术检查已完成" : goalStateLabels[goal.status] ?? "状态待核实"}</span>
      {active && currentStep && currentStep !== reason ? <small>{currentStep}</small> : null}</summary>
    <div className="assistant-goal-body">
      {completed ? <div className="assistant-goal-receipt" aria-label="实际输出检查范围">
        <p>已核对当前成片与同版本、同文件的实际观察结果。</p>
        <p>{completed.measurement_scope === "actual_output_bounded_frames"
          ? "检查范围：实际成片的有限抽帧与媒体参数，未逐帧检查。" : "实际观察已返回；具体检查范围尚不能在这里核实。"}</p>
        {completed.unavailable.length ? <ul aria-label="本次未检查的内容">{completed.unavailable.map((item, index) => <li key={`${item.scope}:${index}`}>{item.reason}</li>)}</ul> : null}
        <p>这份技术检查不能替代对全部创作要求的验收。</p>
      </div> : null}
      {reason ? <p>{reason}</p> : null}
      {goal.status === "production_completed" && !completed ? <p>当前成片已生成，尚未完成输出检查；不能据此判断画面和声音质量。</p> : null}
      {goal.production_result && !execution ? <div className="assistant-goal-receipt" aria-label="实际制作结果"><p>{goal.production_result.status === "succeeded" && goal.production_result.video_ready ? "成片已生成，当前输出有效。" : "制作未完成，请查看原任务的实际原因。"}</p></div> : null}
      {goal.status === "production_failed" ? <p>制作未完成，原任务和已保存修改仍保留，可查看实际失败原因后继续。</p> : null}
      {execution && job ? <div className="assistant-goal-receipt" aria-label="实际制作结果">
        <p>{job.status === "succeeded" ? videoReady ? "成片已生成，当前输出有效。" : "制作作业已完成，有效成片仍待核实。"
          : `制作作业 · ${executionStatusLabels[job.status] ?? "状态待核实"}`}</p>
        {execution.waiting_reason ? <p><AssistantFailureMessage message={execution.waiting_reason}/></p> : null}
        {job.failure_reason ? <p><AssistantFailureMessage message={job.failure_reason}/></p> : null}
        {job.repair_action ? <p><AssistantFailureMessage message={job.repair_action}/></p> : null}
        {execution.stages?.length ? <ul aria-label="实际制作阶段">{execution.stages.map((stage, index) => <li key={`${stage.stage}:${index}`}>
          {executionStageLabels[stage.stage] ?? "制作步骤"} · {executionStatusLabels[stage.status] ?? "状态待核实"}
        </li>)}</ul> : null}
      </div> : null}
      {completed || videoReady && (goal.production_result?.status === "succeeded" || job?.status === "succeeded")
        ? <Link className="assistant-text-action" href={`/tasks/${encodeURIComponent(goal.task_id)}`}>查看对应任务与成片</Link> : null}
      {refreshPaused && active ? <p>自动查询已暂停；后台状态仍以实际结果为准，可以手动查询。</p> : null}
      {active || job?.status === "interrupted" ? <Button variant="ghost" className="assistant-text-action" type="button" disabled={readingResult} onClick={onRead}>{readingResult ? "正在查询…" : "查询目标进展"}</Button> : null}
    </div>
  </ReadingDetails>;
}

export function AssistantFailureMessage({ message, reading, readingKey = "diagnostic", summary = "这次操作没有完成，请打开诊断查看原因。" }: { message: string; reading?: ReadingState; readingKey?: string; summary?: string }) {
  if (hasPrivateExecutionText(message)) return <span>这次操作没有完成。请查询原请求的结果，或在设置中检查对话服务。</span>;
  const diagnostic = /https?:\/\/|Traceback|HTTP\s?\d|\b(?:unreachable|ECONNREFUSED|ENOTFOUND|fetch failed|Connection refused)\b|\/(?:Users|Volumes|private)\/|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|(?:不是|并非)\s*有效(?:的)?\s*JSON\b|^\s*[{[]/i.test(message);
  if (!diagnostic) return <>{message}</>;
  return <><span>{summary}</span><ReadingDetails reading={reading} readingKey={readingKey}><summary>诊断详情</summary><pre>{message}</pre></ReadingDetails></>;
}

function AssistantText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  if (!text) return null;
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true); setCopyError(false);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { setCopyError(true); }
  }
  const label = copied ? "已复制" : copyError ? "复制未成功" : "复制回复";
  return <Button variant="ghost" className="assistant-copy assistant-icon-action" aria-label={label} title={label} onClick={() => void copy()} type="button">{copied ? <Check size={14}/> : <Copy size={14}/>}</Button>;
}

const relativeTime = new Intl.RelativeTimeFormat("zh-CN", { numeric: "always" });

function TurnModel({ model, settledAt }: { model: string; settledAt?: string }) {
  const minutes = settledAt ? Math.max(0, Math.floor((Date.now() - Date.parse(settledAt)) / 60_000)) : NaN;
  const when = !Number.isFinite(minutes) ? null : minutes < 1 ? "刚刚" : minutes < 60
    ? relativeTime.format(-minutes, "minute") : minutes < 1440
      ? relativeTime.format(-Math.floor(minutes / 60), "hour") : relativeTime.format(-Math.floor(minutes / 1440), "day");
  return <span className="assistant-turn-model">
    {model}{when ? <time dateTime={settledAt}>{when}</time> : null}
  </span>;
}

function TurnContent({ turn, blocks = turnBlocks(turn), reading, latest, continuation, visibleResultNotes, recoveryToolId, recovery, receipt }: {
  turn: AssistantConversationTurn; blocks?: ReturnType<typeof turnBlocks>; reading?: ReadingState; latest: boolean;
  continuation?: boolean; visibleResultNotes?: string; recoveryToolId?: number; recovery?: ReactNode; receipt?: ReactNode;
}) {
  const goal = turn.result?.kind === "creation_task_adopted" ? assistantTurnGoalRefreshState(turn) : null;
  const turnTools = blocks.flatMap(block => block.kind === "tools" ? block.tools : []);
  const hasRunningTool = turnTools.some(tool => tool.state === "running");
  const surfacedElsewhere = [goal ? null : turn.assistant_text, visibleResultNotes].filter(Boolean).join("\n");
  const receiptIndex = blocks.findIndex(block => block.kind === "result");
  const lastBlock = blocks.at(-1);
  const useBeforeAnswer = receiptIndex < 0 && lastBlock?.kind === "text" && turnTools.length > 0 && turn.status !== "running";
  const activeTools = blocks.filter(block => block.kind === "tools").at(-1);
  const displayedNotices = new Set<string>();
  const toolFailureMessages = turnTools.flatMap(tool => tool.state === "failed"
    ? [tool.failure?.reason, tool.summary].map(publicExecutionText).filter(Boolean) : []);
  return <>
    {blocks.map(block => {
      if (block.kind === "result") return <Fragment key={block.key}>{receipt}</Fragment>;
      if (block.kind === "notice") {
        const message = publicExecutionText(block.message) ?? (block.state === "unknown"
          ? "这次请求的结果仍待核实。" : "这轮回复未能完成。");
        if (displayedNotices.has(message) || toolFailureMessages.includes(message) || surfacedElsewhere.includes(message)) return null;
        displayedNotices.add(message);
        return <div key={block.key} className="assistant-inline-failure" data-unknown={block.state === "unknown" || undefined}
          role={block.state === "unknown" ? "status" : "alert"}>
          <div className="assistant-activity-row">{message}</div>
          {block.state === "unknown" && !message.includes("查询") ? <p>先查询原请求。</p> : null}
        </div>;
      }
      if (block.kind === "tools") return <TurnToolActivity key={block.key} tools={block.tools} turnTools={turnTools} turn={turn}
        active={turn.status === "running" && activeTools === block}
        reading={reading} surfacedElsewhere={surfacedElsewhere} recoveryToolId={recoveryToolId} recovery={recovery}/>;
      if (goal && block === lastBlock) return null;
      if (hasPrivateExecutionText(block.text)) return null;
      const readingKey = `${turn.id}:message${block.key === "text:saved" ? "" : `:${block.key}`}`;
      return <Fragment key={block.key}>
        {useBeforeAnswer && block === lastBlock ? receipt : null}
        <div className="assistant-markdown-wrap" aria-busy={turn.status === "running"}>
        <ConversationMessage className="assistant-markdown" savedOpen={reading?.get(readingKey, latest) ?? latest}
          onOpenChange={open => reading?.save(readingKey, open)}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
        </ConversationMessage>
        </div>
      </Fragment>;
    })}
    {receiptIndex < 0 && (!useBeforeAnswer || goal) ? receipt : null}
    {!hasRunningTool ? <TurnActivity turn={turn} continuation={continuation}/> : null}
    {goal ? <div className="assistant-markdown-wrap">
      <p>{goalStateLabels[goal.status] ?? "任务状态待核实"}。</p>
      {goal.waiting_reason && goalReasonLabels[goal.waiting_reason] ? <p>{goalReasonLabels[goal.waiting_reason]}</p> : null}
      <Link className="assistant-text-action" href={`/tasks/${encodeURIComponent(goal.task_id)}`}>打开这个视频任务</Link>
    </div> : null}
  </>;
}

export function AssistantConversationHeaderActions({ state, busy, onConversationChange, onInput, children, legacyHistory }: {
  state: Conversation; busy: boolean; onConversationChange?: () => void; onInput: (value: string) => void; children?: ReactNode; legacyHistory?: ReactNode;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const running = state.conversation?.turns.at(-1)?.status === "running";
  const disabled = state.loading || busy || running;
  const fail = (cause: unknown) => state.setError(cause instanceof Error ? cause.message : "对话操作没有完成，请重试。");
  return <div className="assistant-header-actions">
    <Button variant="ghost" className="assistant-icon-action" type="button" aria-label="历史对话" title="历史对话" disabled={disabled}
      onClick={() => { void state.loadList().then(() => setHistoryOpen(true)).catch(fail); }}><History size={15}/></Button>
    <Button variant="ghost" className="assistant-icon-action" type="button" aria-label="新对话" title="新对话，保留当前作品" disabled={disabled}
      onClick={() => { void state.createNew().then(next => { onConversationChange?.(); onInput(next.draft_text); }).catch(fail); }}><Plus size={15}/></Button>
    {children}
    {historyOpen ? <HomeCardDialog title="历史对话" kind="detail" onClose={() => setHistoryOpen(false)}><p>同一作品可以有多段对话；切换对话不会新建视频任务。</p><div className="assistant-resume-list">
      {state.conversations.map(item => <Button variant="ghost" type="button" key={item.id} aria-current={item.id === state.conversation?.id ? "true" : undefined}
        onClick={() => { void state.resume(item.id).then(next => { onConversationChange?.(); onInput(next.draft_text); setHistoryOpen(false); }).catch(fail); }}><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleString()}</small></Button>)}
      {!state.conversations.length ? <p>还没有可恢复的对话。</p> : null}
    </div>{legacyHistory}</HomeCardDialog> : null}
  </div>;
}

function ReplyActions({ turn, onTransferReply, onCreatePlanFromReply, transferDraftState }: {
  turn: AssistantConversationTurn; onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
  onCreatePlanFromReply?: (turn: AssistantConversationTurn) => void; transferDraftState: "none" | "draft" | "confirmed";
}) {
  const [open, setOpen] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const group = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const menuItemCount = Number(Boolean(onTransferReply)) + Number(Boolean(onCreatePlanFromReply));
  const close = () => { setOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!group.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) setOpen(false); };
    const moved = (event: Event) => { if (!menu.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("scroll", moved, true);
    window.addEventListener("resize", moved);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("scroll", moved, true); window.removeEventListener("resize", moved); };
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !trigger.current) return;
    const rect = trigger.current.getBoundingClientRect();
    const height = menuItemCount * 32 + 8;
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - 220)),
      top: rect.bottom + height + 6 <= window.innerHeight - 12 ? rect.bottom + 6 : Math.max(12, rect.top - height - 6) });
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
  }, [open, menuItemCount]);
  const useReply = () => {
    close();
    if (transferDraftState === "draft") setConfirmReplace(true);
    else onTransferReply?.(turn, false);
  };
  return <div className="assistant-reply-actions" ref={group} onKeyDown={event => {
    if (!open) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node) && !menu.current?.contains(event.relatedTarget as Node)) setOpen(false); }}>
    <AssistantText text={turn.assistant_text}/>
    {onTransferReply || onCreatePlanFromReply ? <Button variant="ghost" ref={trigger} className="assistant-icon-action" type="button" aria-label="回复操作" title="回复操作" aria-haspopup="menu" aria-expanded={open} onKeyDown={event => { if (!open && ["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); } }} onClick={() => setOpen(value => !value)}><MoreHorizontal size={15}/></Button> : null}
    {open ? createPortal(<div ref={menu} style={position} className="assistant-reply-menu" role="menu" aria-label="回复操作">
      {onTransferReply ? <Button variant="ghost" type="button" role="menuitem" disabled={transferDraftState === "confirmed"} title={transferDraftState === "confirmed" ? "当前文案已确认，不能替换。" : undefined} onClick={useReply}>{transferDraftState === "draft" ? "替换当前草稿并带入回复" : transferDraftState === "confirmed" ? "文案已确认" : "带入文案草稿"}</Button> : null}
      {onCreatePlanFromReply ? <Button variant="ghost" type="button" role="menuitem" onClick={() => { close(); onCreatePlanFromReply(turn); }}>带入新任务文案</Button> : null}
    </div>, document.body) : null}
    {confirmReplace ? <HomeCardDialog kind="confirmation" title="替换待确认文案" onClose={() => setConfirmReplace(false)}
      footer={<><Button variant="ghost" type="button" data-card-cancel onClick={() => setConfirmReplace(false)}>取消</Button><Button variant="ghost" type="button" onClick={() => { setConfirmReplace(false); onTransferReply?.(turn, true); }}>确认替换并带入回复</Button></>}>
      <p>这会覆盖当前待确认文案；已确认的文案不会被替换。</p>
    </HomeCardDialog> : null}
  </div>;
}

function canOpenTurnProposal(turn: AssistantConversationTurn) {
  const result = assistantTaskTurnResult(turn);
  if (!result || turn.status === "running" || turn.status === "stopped") return false;
  if (result.continuation) return result.continuation.actionable;
  if (turn.status !== "completed") return false;
  return result.edit_preview === true && (!result.response?.edit_preview || result.response.edit_preview.proposal.status === "proposed");
}

const resultStageLabels: Record<AssistantResultStage, string> = {
  script: "查看当前文案", narration: "查看当前配音", montage: "查看当前剪辑",
};

function savedResultStage(turn: AssistantConversationTurn, taskId: string): AssistantResultStage | null {
  if (canOpenTurnProposal(turn) || assistantTurnHasUnknownRequest(turn, taskId)) return null;
  return savedAssistantResultLocation(turn, taskId)?.stage ?? null;
}

function storyboardRegenerationSummary(outcome: AssistantStoryboardRegeneration | null | undefined) {
  if (!outcome) return null;
  if (outcome.kind !== "storyboard_regeneration_receipt") return outcome.kind === "storyboard_regeneration_unknown"
    ? "故事板重算结果尚未核实；原请求定位已保留，请查询原请求，不重复提交。"
    : "故事板重算请求已被拒绝；未取得新故事板版本。";
  if (outcome.receipt.status === "succeeded") return outcome.action === "query"
    ? "原请求记录：故事板版本已保存；不代表当前版本或成片已更新。"
    : "新故事板版本已保存，可撤销；成片尚未重新生成。";
  const statusLabels = {
    queued: "故事板重算已排队；尚未保存新故事板版本。",
    running: "正在重算故事板；尚未保存新故事板版本。",
    interrupted: "故事板重算已中断；尚未核实新故事板版本是否保存。",
    failed: "故事板重算失败；原稿保留。",
    cancelled: "故事板重算已取消；未保存新故事板版本。",
  };
  return `${outcome.action === "query" ? "原故事板重算请求：" : ""}${statusLabels[outcome.receipt.status]}`;
}

function scriptWindowSummary(receipt: AssistantScriptWindowRequest | null) {
  if (!receipt) return null;
  if (receipt.status === "saved") return receipt.operation === "start" ? "文案首窗已保存，可继续补全文案；尚未完整核查并采用。" : "文案窗口已保存，其他已存段保留；尚未完整核查并采用。";
  if (receipt.status === "confirmed") return "已核查并采用完整文案。";
  if (receipt.status === "unknown" || receipt.status === "failed" || receipt.submission_effect === "unknown") return "原文案窗口请求结果尚未确认；保留原 UUID，先查询回执，不重复提交。";
  return receipt.status === "not_submitted" ? "本次文案窗口请求未提交，原稿保留。" : "本次文案窗口请求已被拒绝，原稿保留。";
}
function scriptToolSummary(receipt: AssistantScriptToolReceipt | null | undefined) {
  if (!receipt) return null;
  const action = {save: "保存", query: "查询", update: "更新", revoke: "停用"}[receipt.action];
  const name = typeof receipt.tool?.name === "string" ? `「${receipt.tool.name}」` : "";
  const version = receipt.tool_version ? `（v${receipt.tool_version}）` : "";
  if (receipt.status === "unknown") {
    return `工具${action}结果尚未核实；原请求和输入已保留，只查询结果，不重复写入。${receipt.action === "update" || receipt.action === "revoke"
      ? `当前观察到的工具版本${version}不能证明本次${action}成功。` : ""}`;
  }
  if (receipt.status === "rejected") return `工具${action}请求已被拒绝；本轮没有取得该操作的成功回执。`;
  if (receipt.status === "not_submitted") return `工具${action}请求没有提交；请查看具体原因。`;
  return receipt.action === "revoke" ? `工具${name}${version}已停用；已有图片和视频仍保留。`
    : receipt.action === "update" ? `工具${name}的新版本${version}已保存；已有图片和视频保持不变。`
      : receipt.action === "query" ? `已回读原工具${name}的登记结果${version}；不代表新输入已经保存。`
        : `可复用图片工具${name}${version}已保存；尚未应用图片或生成成片。`;
}

function ResourceWaitReceipt({ receipt, reading, readingKey }: {
  receipt: ResourceAdmissionReceipt; reading?: ReadingState; readingKey: string;
}) {
  const historical = receipt.context === "history";
  return <ReadingDetails reading={reading} readingKey={readingKey} defaultOpen={!historical}>
    <summary>{historical ? "历史资源检查" : "首次资源检查"} · {receipt.observation_kind === "first_unknown" ? "暂未核实" : "未通过"}</summary>
    <p>首次检查时间：<time dateTime={receipt.checked_at}>{new Date(receipt.checked_at).toLocaleString("zh-CN", { timeZone: "UTC", hour12: false })} UTC</time></p>
    <p>{receipt.summary}</p>
    {historical ? <p>这是该请求的历史检查记录，不能代表当前设备资源或执行许可。</p> : null}
    {receipt.observed_interval_seconds !== null ? <p>采样窗口：{receipt.observed_interval_seconds.toLocaleString("zh-CN", { maximumFractionDigits: 20 })} 秒。</p> : null}
    {receipt.checks.length ? <ul aria-label="首次资源检查数值">{receipt.checks.map(check => <li key={check.metric}>{taskMediaResourceCheckLabel(check)}</li>)}</ul> : null}
    {receipt.repair_action ? <p>{receipt.repair_action}</p> : null}
  </ReadingDetails>;
}

const inputStatusLabels = { accepted: "已接收，等待 Agent 处理", consumed: "Agent 正在处理", completed: "Agent 已处理此输入", withdrawn: "已撤回", rejected: "未执行", interrupted: "处理已中断" };

function InputReceipt({ item, reading, onWithdraw }: { item: AssistantInputReceipt; reading?: ReadingState; onWithdraw?: (id: string) => void }) {
  if (item.status === "completed" && !item.understanding && !item.business_effect && (!item.reason || item.reason === "waiting_agent")) {
    return <ConversationMessage className="assistant-user-message" savedOpen>{item.message}</ConversationMessage>;
  }
  return <div className="assistant-input-receipt" data-input-status={item.status}>
    <ReadingDetails reading={reading} readingKey={`input:${item.input_id}`} defaultOpen={item.status === "rejected" || item.status === "interrupted"}>
      <summary><span className="assistant-input-message">{item.message}</span><span className="assistant-input-state">{inputStatusLabels[item.status]}</span></summary>
      <div className="assistant-input-details">
        {item.understanding ? <p>{{query:"查询",clarification:"澄清",candidate:"尝试候选",effective_modification:"修改要求",adoption:"采用要求",independent_goal:"独立目标"}[item.understanding.kind]}：{item.understanding.summary}</p> : null}
        {item.understanding && !item.business_effect && item.understanding.kind === "effective_modification" ? <p>已理解要求，尚未收到业务生效回执。</p> : null}
        {item.business_effect ? <p>修改回执：{item.business_effect.status}</p> : null}
      </div>
    </ReadingDetails>
    {item.reason && item.reason !== "waiting_agent" ? <p>{item.reason === "turn_ended_before_input_receipt" ? "本轮已结束，这条追加要求尚未确认处理；原文已保留。" : item.reason}</p> : null}
    {item.status === "accepted" && onWithdraw ? <Button variant="ghost" type="button" onClick={() => onWithdraw(item.input_id)}>撤回</Button> : null}
  </div>;
}

function TurnView({ latest = false, turn, reading, onTransferReply, onCreatePlanFromReply, onOpenEditProposal, onOpenCreationRequest, onOpenResultStage, transferDraftState, onContinue, continueReason, onReadResult, readingResult, refreshPaused, windowReadError, taskScopeId, outputRecovered = false, productionRecovered = false, inputReceipts = [], goalReceipt }: { goalReceipt?: ReactNode; inputReceipts?: AssistantInputReceipt[]; turn: AssistantConversationTurn; latest?: boolean; reading?: ReadingState; onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void; onCreatePlanFromReply?: (turn: AssistantConversationTurn) => void; onOpenEditProposal?: (turn: AssistantConversationTurn) => void; onOpenCreationRequest?: (turn: AssistantConversationTurn) => void; onOpenResultStage?: OpenAssistantResult; transferDraftState: "none" | "draft" | "confirmed"; onContinue: (message: string) => void; continueReason: string | null; onReadResult: (turn: AssistantConversationTurn, openProposal?: boolean) => void; readingResult: boolean; refreshPaused: boolean; windowReadError?: string; taskScopeId?: string; outputRecovered?: boolean; productionRecovered?: boolean }) {
  const businessContinuation = turn.events.some((event: unknown) => typeof event === "object" && event !== null
    && "type" in event && event.type === "goal_continuation_started"
    && "source_kind" in event && event.source_kind === "business_event");
  const result = turn.result;
  const taskResult = assistantTaskTurnResult(turn);
  const [locationNotice, setLocationNotice] = useState<string | null>(null);
  const continuation = taskResult?.continuation;
  const job = taskResult?.response?.media_job;
  const request = taskResult?.response?.edit_request;
  const toolSummary = scriptToolSummary(taskResult?.response?.script_tool);
  const scriptWindow = assistantTurnScriptWindowRequest(turn, taskScopeId);
  const windowSummary = scriptWindowSummary(scriptWindow);
  const windowInputMismatch = taskResult?.response?.script_window_request && !scriptWindow;
  const storyboard = taskResult?.response?.storyboard_regeneration;
  const storyboardSummary = storyboardRegenerationSummary(storyboard);
  const segment = taskResult?.response?.segment_revision;
  const segmentSummary = segment?.revision ? segmentRevisionStatus(segment.revision)
    : segment?.failure?.submission_effect === 'not_submitted' ? '本段修改未提交'
      : segment?.failure?.submission_effect === 'rejected' ? '本段修改已被拒绝'
        : segment ? '本段修改结果尚未知；请先查询原请求' : null;
  const restore=taskResult?.response?.timeline_restore;
  const appliedEdit = taskResult?.response?.timeline_action === 'apply' && request?.phase === 'apply'
    && request.status === 'recorded' ? request.result : null;
  const readiness = restore?.receipt?.status === 'applied'
    ? restore.receipt.execution_readiness : appliedEdit && 'execution_readiness' in appliedEdit ? appliedEdit.execution_readiness : null;
  const readinessMessage = editExecutionReadinessMessage(readiness);
  const readinessAlreadyVisible = Boolean(readinessMessage && turn.assistant_text.includes(readinessMessage));
  const readinessBlocked = Boolean(readiness && typeof readiness === "object" && "status" in readiness && readiness.status === "not_ready");
  const restoreSummary=restore?.receipt?.status==='applied'?'原修改的撤销或重做已保存为新版本'
    : restore?.receipt?.status==='refused' || restore?.failure?.submission_effect==='rejected'?'原撤销或重做已被拒绝'
      : restore?.failure?.submission_effect==='not_submitted'?'撤销或重做未提交'
        : restore?'撤销或重做结果尚未知；请查询原请求':null;
  const storyboardFailure = storyboard?.kind === "storyboard_regeneration_receipt"
    ? storyboard.receipt.failure_reason : storyboard?.failure.reason;
  const outcomeStatus = continuation?.status ?? job?.status;
  const mediaJobLabel = job && job.status === "queued"
    ? taskMediaJobLabel(job) : null;
  const unknownRequest = assistantTurnHasUnknownRequest(turn, taskScopeId);
  const pendingResult = assistantTurnNeedsResultRefresh(turn, taskScopeId);
  const waitingForOriginal = pendingResult || ["queued", "running", "interrupted"].includes(outcomeStatus ?? "");
  const graphicSourceReady = job?.result_kind === "graphic_source" && job.status === "succeeded"
    && job.result && "source_ref" in job.result && "artifact_id" in job.result;
  const graphicSourceSummary = "图片来源已生成，可以继续预览它在视频里的效果；尚未应用到草稿或生成成片。";
  const businessSummary = taskResult ? unknownRequest
    ? "请求结果尚未核实；原请求定位已保留。先查询结果，不重复提交。"
    : graphicSourceReady ? graphicSourceSummary
    : request?.status === "rejected" ? request.phase === "observation"
      ? "本次输出检查已被拒绝；原成片保留。"
      : "本次请求已被拒绝，没有沿该请求继续应用。"
    : outcomeStatus === "confirmed"
      ? continuation?.result_version_id ? "修改已应用并保存到草稿，可撤销；成片尚未重新生成。" : "已收到采用决定，保存结果仍待核实。"
      : outcomeStatus === "rejected" ? "修改方案已放弃，当前草稿保持不变。"
        : outcomeStatus === "cancelled" ? "后台处理已取消，未据此应用修改。"
          : outcomeStatus === "stale" || outcomeStatus === "expired" ? job?.result_kind === "observation"
            ? "原输出检查已失效，仅保留历史；请按当前条件重新检查。"
            : "原修改结果已过期，仅保留为历史，不能沿旧版本应用。"
            : outcomeStatus === "stopped" ? "本轮接续已停止，原结果仅保留为历史。"
              : outcomeStatus === "failed" ? "后台处理未完成；请查看实际原因，当前草稿未因此改变。"
                : outcomeStatus === "queued" ? "预览已安排，正在等待后台处理；尚未生成预览或应用修改。"
                  : outcomeStatus === "running" ? "后台正在准备预览；当前草稿尚未改变。"
                    : outcomeStatus === "interrupted" ? "后台处理已中断，等待明确决定；没有自动重试。"
                      : canOpenTurnProposal(turn) ? "实际修改预览已返回，等待你查看并决定是否采用；当前草稿尚未改变。"
                        : request?.status === "not_submitted" ? "请求没有提交；请处理具体原因后明确再试。"
                          : null : null;
  const executionStatusLabels: Record<string, string> = {
    queued: "已排队", running: "后台处理中", succeeded: "已完成", failed: "失败",
    cancelled: "已取消", interrupted: "中断等待继续",
  };
  const resultSummary = windowInputMismatch ? null : windowSummary ?? restoreSummary ?? segmentSummary ?? businessSummary ?? storyboardSummary ?? toolSummary ?? (result?.kind === "plan_preview"
    ? (result.plan_preview as Record<string, unknown> | undefined)?.confirmable === true
      ? "方案已检查，可查看页面中的方案卡后决定是否创建。"
      : "方案检查已返回阻断项；请按页面中的真实原因处理。"
    : result?.kind === "creation_request"
      ? "制作目标已保存；打开创作目标可读取当前方案与任务。"
    : result?.kind === "material_result"
      ? `${result.scope === "segment" ? "当前片段" : "整条素材"}标签已回读${result.changed === true ? "，内容有变化。" : result.changed === false ? "，内容没有变化。" : "。"}`
      : result?.kind === "task_turn"
        ? [result.topic_candidates === true ? "已生成选题候选" : null,
          result.timeline_action === "apply" ? "修改已保存，可撤销；成片尚未重新生成" : result.timeline_action === "undo" ? "已整体撤销上一步编辑" : result.timeline_action === "redo" ? "已重做上一步编辑" : result.timeline_action === "reject" ? "已取消待确认修改" : null,
          typeof result.script_draft === "string" ? "文案草稿已保存" : null,
          typeof result.execution_action === "string" ? `已提交${result.execution_action === "start" ? "开始制作" : result.execution_action === "resume" ? "继续制作" : "取消制作"}${typeof result.execution_status === "string" ? `（作业${executionStatusLabels[result.execution_status] ?? result.execution_status}）` : ""}` : null,
          result.edit_preview === true ? "已生成待确认修改提案" : null].filter(Boolean).join("；") || null
        : result?.kind === "failure" && typeof result.message === "string"
          ? result.message
          : null);
  const otherResultNeedsAttention = pendingResult || unknownRequest || Boolean(windowReadError
    || segment?.failure || segment?.revision?.adoption?.reason || restore?.failure || restore?.receipt?.failure || storyboardFailure
    || scriptWindow?.failure || job?.failure_reason && job.status !== "cancelled" || job?.repair_action
    || job?.resource_wait?.context === "pending_request")
    || ["failed", "interrupted", "rejected"].includes(outcomeStatus ?? "");
  const resultNeedsAttention = otherResultNeedsAttention || readinessBlocked;
  const completedAudioReply = !otherResultNeedsAttention && !businessSummary && completedAudioReplyMatchesReceipt(turn);
  const resultSummaryRepeated = Boolean(resultSummary && turn.assistant_text.includes(resultSummary));
  const repeatedTimelineSave = continuation?.status === "confirmed" && Boolean(continuation.result_version_id)
    && /(?:已保存|已落到当前时间线)/.test(turn.assistant_text) && !resultNeedsAttention;
  const showResultSummary = !resultSummaryRepeated && !repeatedTimelineSave;
  const savedStage = taskScopeId && !windowInputMismatch ? savedResultStage(turn, taskScopeId) : null;
  const resultStage = onOpenResultStage ? savedStage : null;
  // The saved reply can include both the host's timeline receipt and its
  // standardized closing paragraph. Keep the former in the reading flow.
  const savedReply = turn.assistant_text.trimEnd();
  const closingReceipt = businessSummary ? `\n\n${businessSummary}` : null;
  const duplicateClosingReceipt = continuation?.phase === "apply" && continuation.status === "confirmed"
    && Boolean(continuation.result_version_id) && !unknownRequest && closingReceipt
    && savedReply.endsWith(closingReceipt)
    && savedReply.slice(0, -closingReceipt.length).includes("修改已保存到可撤销的时间线版本；本次尚未重新生成视频。");
  const readingTurn = duplicateClosingReceipt && closingReceipt
    ? {...turn, assistant_text: savedReply.slice(0, -closingReceipt.length)} : turn;
  const blocks = turnBlocks(readingTurn, continuation?.event_id);
  const tools = blocks.flatMap(block => block.kind === "tools" ? block.tools : []);
  const unknownTool = tools.find(tool => tool.failure?.submission_effect === "unknown" || tool.state === "unreturned");
  const lastTool = tools.at(-1);
  const failedTool = unknownRequest ? unknownTool ?? lastTool : lastTool;
  const recoveryToolId = failedTool && (failedTool.state === "failed" || failedTool.state === "unreturned")
    && !outputRecovered && !productionRecovered ? failedTool.id : undefined;
  const model = publicExecutionText(taskResult?.response?.model ?? (typeof result?.model === "string" ? result.model : null));
  const canContinue = (turn.status === "failed" || turn.status === "stopped")
    && Boolean(turn.user_message.trim()) && !waitingForOriginal && !unknownRequest && !unknownTool
    && !blocks.some(block => block.kind === "notice" && block.state === "unknown") && !savedStage && !productionRecovered && !outputRecovered;
  const hasReplyActions = turn.status !== "running" && Boolean(turn.assistant_text.trim());
  const queryResult = unknownRequest || waitingForOriginal && (turn.status !== "running" || refreshPaused)
    ? <Button variant="ghost" type="button" disabled={readingResult} onClick={() => onReadResult(turn)}>{readingResult ? "正在查询…" : unknownRequest ? "先查询原请求" : "查询本轮结果"}</Button> : null;
  const continueEditing = canContinue ? <Button variant="ghost" type="button" disabled={Boolean(continueReason)}
    title={continueReason ?? "只带入输入框，供你修改。"} onClick={() => onContinue(turn.user_message)}>继续编辑本条要求</Button> : null;
  const recovery = recoveryToolId !== undefined ? queryResult ?? continueEditing : null;
  const visibleResultNotes = [
    showResultSummary && recoveryToolId === undefined ? resultSummary : null,
    ...(recoveryToolId !== undefined ? [] : [segment?.failure?.reason, segment?.failure?.repair_action, readinessMessage,
    restore?.failure?.reason, restore?.receipt?.failure?.reason,
    restore?.failure?.repair_action, restore?.receipt?.failure?.repair_action,
    storyboardFailure, storyboard && storyboard.kind !== "storyboard_regeneration_receipt" ? storyboard.failure.repair_action : null,
    scriptWindow?.failure?.reason, scriptWindow?.failure?.impact, scriptWindow?.failure?.repair_action,
    job?.status !== "cancelled" ? job?.failure_reason : null, job?.repair_action]),
  ].filter(Boolean).join("\n");
  const receipt = <>
    {windowInputMismatch ? <p role="alert">窗口回执与本轮任务或输入不符，未更新本轮结果。</p> : null}
    {resultSummary && !completedAudioReply && !(unknownRequest && recoveryToolId !== undefined) ? <TurnResult
      summary={showResultSummary ? resultSummary : null} reading={reading} readingKey={`${turn.id}:result`}
      showDetails={(otherResultNeedsAttention || readinessBlocked && !readinessAlreadyVisible) && recoveryToolId === undefined}>
      {toolSummary && toolSummary !== resultSummary ? <p>{toolSummary}</p> : null}
      {storyboardSummary && storyboardSummary !== resultSummary ? <p>{storyboardSummary}</p> : null}
      {segment?.failure ? <div><AssistantFailureMessage reading={reading} readingKey={`${turn.id}:segment:diagnostic`} message={segment.failure.reason}/>{segment.failure.repair_action ? <p>{segment.failure.repair_action}</p> : null}</div> : null}
      {scriptWindow ? <section aria-label="文案窗口回执"><p>回执状态：{{ saved: "窗口已保存", confirmed: "完整采用", unknown: "结果未知", failed: "请求失败，提交结果仍待核对", rejected: "已拒绝", not_submitted: "未提交" }[scriptWindow.status]}</p><p>窗口操作：{{ start: "首窗保存", patch: "分窗修改", confirm: "完整采用" }[scriptWindow.operation]}</p><p>原请求：{scriptWindow.request_id}</p>{scriptWindow.script_id ? <p>稿件：{scriptWindow.script_id}</p> : null}{(scriptWindow.status === "saved" || scriptWindow.status === "confirmed") && scriptWindow.total_count != null ? <p>{scriptWindow.status === "confirmed" ? "完整采用" : "当前已存"} {scriptWindow.total_count} 段。</p> : null}{scriptWindow.updated_at ? <p>稿件版本：{scriptWindow.updated_at}</p> : null}
        {scriptWindow.failure?.reason ? <AssistantFailureMessage reading={reading} readingKey={`${turn.id}:script-window:diagnostic`} message={scriptWindow.failure.reason}/> : null}{scriptWindow.failure?.impact ? <p>{scriptWindow.failure.impact}</p> : null}{scriptWindow.failure?.repair_action ? <p>{scriptWindow.failure.repair_action}</p> : null}
      </section> : null}
      {windowReadError ? <p role="alert">{windowReadError}</p> : null}
      {segment?.revision?.adoption?.reason ? <p>{segmentAdoptionReason(segment.revision.adoption.reason)}</p> : null}
      {readinessMessage && !readinessAlreadyVisible ? <p>{readinessMessage}</p> : null}
      {restore?.failure || restore?.receipt?.failure ? <div><AssistantFailureMessage reading={reading} readingKey={`${turn.id}:restore:diagnostic`} message={restore.failure?.reason ?? restore.receipt?.failure?.reason ?? ''}/>{restore.failure?.repair_action || restore.receipt?.failure?.repair_action ? <p>{restore.failure?.repair_action ?? restore.receipt?.failure?.repair_action}</p> : null}</div> : null}
      {storyboardFailure ? <div><AssistantFailureMessage reading={reading} readingKey={`${turn.id}:storyboard:diagnostic`} message={storyboardFailure}/></div> : null}
      {storyboard && storyboard.kind !== "storyboard_regeneration_receipt" && storyboard.failure.repair_action ? <p>{storyboard.failure.repair_action}</p> : null}
      {unknownRequest && graphicSourceReady ? <p>{graphicSourceSummary}</p> : null}
      {mediaJobLabel ? <p>{mediaJobLabel}</p> : outcomeStatus !== "cancelled" && (continuation?.waiting_reason || job?.waiting_reason)
        ? <p>{continuation?.waiting_reason ?? job?.waiting_reason}</p> : null}
      {job?.status !== "cancelled" && job?.failure_reason ? <div><AssistantFailureMessage reading={reading} readingKey={`${turn.id}:job:diagnostic`} message={job.failure_reason}/></div> : null}{publicExecutionText(job?.repair_action) ? <p>{job?.repair_action}</p> : null}
      {job?.resource_wait ? <ResourceWaitReceipt receipt={job.resource_wait} reading={reading} readingKey={`${turn.id}:resource:${job.resource_wait.observation_id}:${job.resource_wait.context}`}/> : null}
      {refreshPaused && pendingResult ? <p>自动查询已暂停；后台状态仍以实际结果为准，可以手动查询。</p> : null}
    </TurnResult> : null}
    {completedAudioReply && readinessMessage && readinessBlocked ? <p role="status">{readinessMessage}</p> : null}
    {completedAudioReply && readinessMessage && !readinessBlocked ? <ReadingDetails reading={reading} readingKey={`${turn.id}:readiness`} className="assistant-turn-result">
      <summary>制作条件</summary><div className="assistant-result-details"><p>{readinessMessage}</p></div>
    </ReadingDetails> : null}
    <div className={onOpenEditProposal && canOpenTurnProposal(turn) ? "assistant-result-actions assistant-confirmation" : "assistant-result-actions"}>
      {resultStage ? <Button variant="ghost" className="assistant-text-action" type="button" onClick={() => setLocationNotice(onOpenResultStage?.(resultStage, turn) ?? null)}>{resultStageLabels[resultStage]}</Button> : null}
      {onOpenEditProposal && canOpenTurnProposal(turn) ? <Button variant="ghost" type="button" disabled={Boolean(continueReason) || readingResult} onClick={() => onReadResult(turn, true)}>{readingResult ? "正在查询…" : "查看修改方案"}</Button> : null}
      {recoveryToolId === undefined ? queryResult ?? continueEditing : null}
    </div>
    {locationNotice && resultStage ? <p className="assistant-stream-status" role="status">{locationNotice}</p> : null}
    {onOpenCreationRequest && turn.status !== "running" && result?.kind === "creation_request" && typeof result.creation_request_id === "string" ? <Button variant="ghost" className="assistant-text-action" type="button" disabled={Boolean(continueReason)} onClick={() => onOpenCreationRequest(turn)}>查看这次创作目标</Button> : null}
    {goalReceipt}
  </>;
  return <article className="assistant-persisted-turn" data-turn-status={turn.status} data-turn-id={turn.id}>
    {turn.user_message.trim() ? <ConversationMessage className="assistant-user-message" savedOpen={reading?.get(`${turn.id}:user`, false)} onOpenChange={open => reading?.save(`${turn.id}:user`, open)}>{turn.user_message}</ConversationMessage> : null}
    {businessContinuation && turn.status !== "running" ? <p className="assistant-stream-status">继续完成原要求</p> : null}
    {inputReceipts.length ? <div className="assistant-turn-inputs" aria-label="本轮补充要求">{inputReceipts.map(item => <InputReceipt key={item.input_id} item={item} reading={reading}/>)}</div> : null}
    <TurnContent turn={turn} blocks={blocks} reading={reading} latest={latest} continuation={businessContinuation}
      visibleResultNotes={visibleResultNotes} recoveryToolId={recoveryToolId} recovery={recovery} receipt={receipt}/>
    {turn.status === "failed" && (outputRecovered || productionRecovered || !blocks.some(block => block.kind === "notice")) ? <p role="status">{outputRecovered ? "本轮回复曾中断；制作与技术检查已恢复完成，历史失败保留。"
      : productionRecovered ? "本轮回复曾中断；成片已生成，输出尚未检查，历史记录保留。"
      : businessContinuation ? "接续未完成；原要求和实际结果保留在记录中。" : "本轮未完成；本条要求保留在记录中。"}</p> : null}
    {turn.status === "stopped" ? <p role="status">本轮已停止；先前的业务动作不会因此撤销。</p> : null}
    {turn.sequence > 0 || hasReplyActions ? <footer className="assistant-turn-footer">
      <div className="assistant-turn-actions">
        {hasReplyActions ? <ReplyActions turn={turn} onTransferReply={turn.status === "completed" ? onTransferReply : undefined}
          onCreatePlanFromReply={turn.status === "completed" ? onCreatePlanFromReply : undefined} transferDraftState={transferDraftState}/> : null}
      </div>
      {model ? <TurnModel model={model} settledAt={turn.status === "running" ? undefined : turn.updated_at}/> : null}
      {turn.sequence > 0 && turn.status !== "running" ? <AssistantTurnExecutionRecord turn={turn} hasPublicTools={tools.length > 0} model={model} reading={reading}/> : null}
    </footer> : null}
  </article>;
}

export function AssistantConversationPanel({
  state, input, onInput, onSend, onConversationChange, onTransferReply, onCreatePlanFromReply, onOpenEditProposal, onOpenCreationRequest, onOpenResultStage, transferDraftState = "none", busy, configured, scopeLabel, inputLabel = "对话要求", placeholder, compact = false, autoFocus = false, legacyHistory, composerActions, materialUsage,
}: {
  state: Conversation;
  input: string;
  onInput: (value: string) => void;
  onSend: (text: string) => Promise<void>;
  onConversationChange?: () => void;
  onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
  onCreatePlanFromReply?: (turn: AssistantConversationTurn) => void;
  onOpenEditProposal?: (turn: AssistantConversationTurn) => void; onOpenCreationRequest?: (turn: AssistantConversationTurn) => void;
  onOpenResultStage?: OpenAssistantResult;
  transferDraftState?: "none" | "draft" | "confirmed";
  busy: boolean;
  configured: boolean;
  scopeLabel: string;
  inputLabel?: string;
  placeholder?: string;
  compact?: boolean;
  autoFocus?: boolean;
  legacyHistory?: ReactNode;
  composerActions?: ReactNode;
  materialUsage?: MaterialUsageContext;
}) {
  const referenceStatus = useAssistantReferenceStatus();
  const selection = useAssistantSelection();
  const [inputMode,setInputMode]=useState<"steer"|"follow_up">("steer");
  const [sendingInput,setSendingInput]=useState(false);
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof getAssistantCapabilities>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [diagnosticsExpanded, setDiagnosticsExpanded] = useState(false);
  const [menuClosedFor, setMenuClosedFor] = useState<string | null>(null);
  const [activeCommand, setActiveCommand] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [referencePickerOpen, setReferencePickerOpen] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const restoredHistoryId = useRef<string | null>(null);
  const followBottom = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const entryRef = useRef<HTMLFormElement>(null);
  const [menuMaxHeight, setMenuMaxHeight] = useState(248);
  const [focus, setFocus] = useState<HTMLTextAreaElement | null>(null);
  const [readingResultTurn, setReadingResultTurn] = useState<string | null>(null);
  const resultReadRevision = useRef(0);
  const [stoppingTurn, setStoppingTurn] = useState<string | null>(null);
  const diagnosticsId = useId();
  useLayoutEffect(() => {
    setHeaderSlot(panelRef.current?.closest("[data-conversation-pane]")?.querySelector<HTMLElement>("[data-assistant-header-actions]") ?? null);
  }, []);
  const conversation = state.conversation;
  const conversationId = conversation?.id;
  useLayoutEffect(() => {
    resultReadRevision.current += 1;
    setReadingResultTurn(null);
    if (!state.loading) return;
    setError(null);
    setHistoryOpen(false);
    setReferencePickerOpen(false);
    setDiagnosticsExpanded(false);
    setComposing(false);
  }, [conversationId, state.loading]);
  const readTurnResult = async (turn: AssistantConversationTurn, openProposal = false) => {
    const id = conversation?.id;
    if (!id) return;
    const read = ++resultReadRevision.current;
    setReadingResultTurn(turn.id);
    setError(null);
    try {
      const fresh = await (assistantTurnScriptWindowRequest(turn, conversation?.scope_kind === "task" ? conversation.scope_id : undefined) ? state.refresh(turn.id) : state.refresh());
      if (!fresh || fresh.id !== id || read !== resultReadRevision.current || !state.isCurrentConversation(id)) return;
      const updated = fresh.turns.find(item => item.id === turn.id);
      if (openProposal && updated && canOpenTurnProposal(updated)) onOpenEditProposal?.(updated);
    } catch (cause) {
      if (read === resultReadRevision.current && state.isCurrentConversation(id)) setError(cause instanceof Error ? cause.message : "暂时无法查询本轮结果。");
    } finally { if (read === resultReadRevision.current) setReadingResultTurn(null); }
  };
  const reading: ReadingState = {
    get: (key, fallback) => state.historyExpandedDetails?.current.get(conversation?.id ?? "")?.get(key) ?? fallback,
    save: (key, open) => {
      if (!conversation || !state.historyExpandedDetails) return;
      let expanded = state.historyExpandedDetails.current.get(conversation.id);
      if (!expanded) { expanded = new Map(); state.historyExpandedDetails.current.set(conversation.id, expanded); }
      expanded.set(key, open);
    },
  };
  const currentRunning = conversation?.turns.at(-1)?.status === "running";
  const lastTurnId = conversation?.turns.at(-1)?.id;
  const stopping = Boolean(lastTurnId && stoppingTurn === lastTurnId);
  async function stopCurrentTurn() {
    if (!lastTurnId || stopping) return;
    setError(null);
    setStoppingTurn(lastTurnId);
    try { await state.stop(); }
    catch (cause) {
      if (conversationId && state.isCurrentConversation(conversationId)) {
        setError(cause instanceof Error ? cause.message : "暂时无法停止本轮。");
      }
    } finally { setStoppingTurn(current => current === lastTurnId ? null : current); }
  }
  const turns = conversation?.turns ?? [];
  // Task conversation projections may include the original creation handoff.
  const creationContexts = (conversation as (Conversation["conversation"] & {
    creation_contexts?: { conversation_id: string; turns: AssistantConversationTurn[] }[];
  }) | null)?.creation_contexts ?? [];
  const latestReplyId = turns.slice().reverse().find(turn => turn.assistant_text.trim())?.id;
  // The receipt identifies its original turn. Collapse repeated projections by
  // input ID without inferring a new turn or a business completion from the text.
  const inputs = new Map<string, { item: AssistantInputReceipt; owner: string }>();
  for (const turn of turns) for (const event of turn.events) {
    if (event.type !== "assistant_input") continue;
    const previous = inputs.get(event.input_id);
    if (!previous || previous.item.updated_at <= event.updated_at) inputs.set(event.input_id, {
      item: event, owner: turns.some(candidate => candidate.id === event.target_turn_id) ? event.target_turn_id : turn.id,
    });
  }
  const pendingInputs = [...inputs.values()].filter(({ item }) => item.status === "accepted" || item.status === "consumed");
  const goalProgress = assistantConversationGoal(conversation);
  const acceptsQueuedInput = currentRunning || Boolean(conversation?.scope_kind === "task" && goalProgress
    && ["running","waiting_result","waiting_adoption","waiting_identity","waiting_unknown"].includes(goalProgress.goal.status));
  const historyContent = JSON.stringify(turns.map(turn => [turn.id, turn.status, turn.updated_at, turn.assistant_text,
    turn.events.length, assistantTaskTurnResult(turn)?.continuation, assistantTaskTurnResult(turn)?.response?.media_job?.status,
    assistantTaskTurnResult(turn)?.response?.media_job?.resource_wait,
    assistantTaskTurnResult(turn)?.response?.edit_request?.status, assistantTaskTurnResult(turn)?.response?.execution_status,
    assistantTurnScriptWindowRequest(turn),
    turn.result?.goal_continuation]));
  const latestHistoryContent = useRef(historyContent);
  latestHistoryContent.current = historyContent;
  const isLocalCommand = input.trim().startsWith("/");
  const commandDisabled = state.loading || !conversation || busy || currentRunning || composing;
  const sendLabel = acceptsQueuedInput && !isLocalCommand ? inputMode === "follow_up" ? "排入后续" : "追加要求" : busy && !isLocalCommand ? "正在处理" : "发送";
  const sendDisabled = (isLocalCommand ? commandDisabled : state.loading || !conversation || composing || sendingInput || (acceptsQueuedInput && selection !== null && selection.version === undefined) || (busy && !acceptsQueuedInput)) || !input.trim() || (!isLocalCommand && (referenceStatus.pending || Boolean(referenceStatus.reason) || !configured));
  const sendReason = state.loading || !conversation ? "正在恢复对话"
    : composing ? "中文组合输入中，请先完成输入"
    : !input.trim() ? "先填写对话要求"
      : acceptsQueuedInput ? "要求将持久保存，等待 Agent 处理；已提交动作不会追改"
        : busy ? "本轮正在处理，请稍后再发送"
          : isLocalCommand ? "Enter 执行本地会话操作；无效命令不会发送给模型"
            : referenceStatus.pending ? "正在保存或核验引用"
              : referenceStatus.reason ? "引用当前不可用，请重新查看或取消引用"
                : !configured ? "请先配置对话服务"
              : "Enter 发送，Shift+Enter 换行";
  const commandHelp = useMemo(() => capabilities?.application_commands ?? [], [capabilities]);
  const commands = useMemo(() => commandHelp.map(syntax => ({ syntax, name: syntax.split(/\s/)[0], ...commandDescriptions[syntax.split(/\s/)[0]] })).filter(item => item.title), [commandHelp]);
  const commandToken = /^\s*\/[^\s]*$/.test(input);
  const commandQuery = input.trim().slice(1).toLowerCase();
  const filteredCommands = commands.filter(item => `${item.name} ${item.title} ${item.description} ${item.syntax}`.toLowerCase().includes(commandQuery));
  const menuOpen = !state.loading && commandToken && menuClosedFor !== input;
  useEffect(() => { setActiveCommand(0); }, [input]);
  useEffect(() => {
    if (!menuOpen) return;
    const resize = () => setMenuMaxHeight(Math.max(46, Math.min(248, (entryRef.current?.getBoundingClientRect().top ?? 294) - 70)));
    const close = (event: PointerEvent) => { if (!entryRef.current?.contains(event.target as Node)) setMenuClosedFor(input); };
    resize(); window.addEventListener("resize", resize); document.addEventListener("pointerdown", close);
    return () => { window.removeEventListener("resize", resize); document.removeEventListener("pointerdown", close); };
  }, [menuOpen, input]);
  useEffect(() => {
    entryRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [activeCommand]);
  useLayoutEffect(() => {
    const node = historyRef.current;
    const id = conversation?.id;
    if (!node || !id || state.loading) return;
    restoredHistoryId.current = null;
    const position = state.historyScrollPositions?.current.get(id);
    const contentAtEntry = latestHistoryContent.current;
    let restored = false;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = position ?? Math.max(0, node.scrollHeight - node.clientHeight);
      followBottom.current = state.historyFollowBottom?.current.get(id) ?? isNearBottom(node);
      setShowJumpToLatest(!isNearBottom(node));
      if (latestHistoryContent.current !== contentAtEntry && followBottom.current) {
        node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight);
      }
      restoredHistoryId.current = id;
      restored = true;
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (node.dataset.conversationId !== id) return;
      if (restored) {
        state.historyScrollPositions?.current.set(id, node.scrollTop);
        state.historyFollowBottom?.current.set(id, followBottom.current);
      }
      let expanded = state.historyExpandedDetails?.current.get(id);
      if (!expanded && state.historyExpandedDetails) { expanded = new Map(); state.historyExpandedDetails.current.set(id, expanded); }
      node.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]:not(.assistant-inline-completed)").forEach(detail => {
        expanded?.set(detail.dataset.readingKey!, detail.open);
      });
    };
  }, [conversation?.id, state.loading, state.historyScrollPositions, state.historyExpandedDetails, state.historyFollowBottom]);

  useLayoutEffect(() => {
    const id = conversation?.id;
    const node = historyRef.current;
    if (!node || !id || state.loading || restoredHistoryId.current !== id) return;
    const frame = window.requestAnimationFrame(() => {
      if (node.dataset.conversationId !== id || restoredHistoryId.current !== id) return;
      if (followBottom.current) {
        node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight);
        state.historyScrollPositions?.current.set(id, node.scrollTop);
      }
      setShowJumpToLatest(!isNearBottom(node));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [conversation?.id, state.loading, historyContent, state.historyScrollPositions]);

  function chooseCommand(index: number) {
    const item = filteredCommands[index];
    if (!item || commandDisabled) return;
    setMenuClosedFor(input);
    if (item.name === "/name") onInput("/name ");
    else if (item.name === "/resume") setHistoryOpen(true);
    else if (item.name === "/help") { onInput("/"); setMenuClosedFor(null); }
    else void submit(undefined, item.name);
    focus?.focus();
  }

  useEffect(() => { void getAssistantCapabilities().then(setCapabilities).catch(() => undefined); }, []);
  useLayoutEffect(() => {
    if (!focus) return;
    const resize = () => {
      // CSS owns the min/max height; recalculate wrapping when the panel narrows.
      focus.style.height = "0px";
      focus.style.height = `${focus.scrollHeight}px`;
    };
    resize();
    let width = focus.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const nextWidth = focus.getBoundingClientRect().width;
      if (nextWidth === width) return;
      width = nextWidth;
      resize();
    });
    observer.observe(focus);
    return () => observer.disconnect();
  }, [input, focus]);
  useEffect(() => {
    if (autoFocus && !state.loading && conversationId) focus?.focus();
  }, [autoFocus, conversationId, focus, state.loading]);

  async function submit(event?: FormEvent, commandText?: string) {
    event?.preventDefault();
    const text = (commandText ?? input).trim();
    if (!text || composing) return;
    const localCommand = text.startsWith("/");
    if (state.loading || !conversation || sendingInput || (busy && !acceptsQueuedInput)) { setError(sendReason); return; }
    if (!localCommand && !configured) { setError(sendReason); return; }
    if (localCommand && (busy || currentRunning)) { setError(localCommand ? "本轮运行中，可继续编辑草稿；停止后再执行。" : sendReason); return; }
    if (localCommand) {
      const [command, ...rest] = text.split(/\s+/);
      const value = rest.join(" ").trim();
      try {
        if (!commands.some(item => item.name === command)) {
          setError("此命令格式不正确或当前不可用，没有发送给模型。请用 /help 查看已接通的会话操作。");
        } else if (command === "/new" && !value) {
          const next = await state.createNew();
          onConversationChange?.();
          onInput(next.draft_text);
          setError(null);
        } else if (command === "/name" && value) {
          const conversationId = conversation?.id;
          const renamed = await state.rename(value);
          if (conversationId && state.isCurrentConversation(conversationId)) onInput(renamed.draft_text);
          setError(null);
        } else if (command === "/resume" && !value) {
          setHistoryOpen(true);
        } else if (command === "/resume" && value) {
          const resumed = await state.resume(value);
          onConversationChange?.();
          onInput(resumed.draft_text);
          setError(null);
        } else if (command === "/help" && !value) { onInput("/"); setMenuClosedFor(null); setError(null); }
        else setError("此命令格式不正确或当前不可用，没有发送给模型。请用 /help 查看已接通的会话操作。");
      } catch (cause) { setError(cause instanceof Error ? cause.message : "会话操作没有完成。"); }
      focus?.focus();
      return;
    }
    if (referenceStatus.pending || referenceStatus.reason) {
      setError(sendReason);
      return;
    }
    setError(null);
    try {
      if (acceptsQueuedInput) {
        if (selection?.version === undefined) throw new Error("引用版本尚未恢复，请稍后发送。");
        setSendingInput(true);
        await state.sendInput(text,inputMode,selection.version);
      } else await onSend(text);
      focus?.focus();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "对话没有完成。"); }
    finally { setSendingInput(false); }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (composing || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (menuOpen) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMenuClosedFor(input); return; }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && filteredCommands.length) {
        event.preventDefault();
        setActiveCommand(index => event.key === "Home" ? 0 : event.key === "End" ? filteredCommands.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + filteredCommands.length) % filteredCommands.length);
        return;
      }
      if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey) {
        if (!filteredCommands.length && event.key === "Tab") return;
        event.preventDefault();
        if (filteredCommands.length) chooseCommand(activeCommand);
        else void submit();
        return;
      }
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void submit();
  }

  if (state.loading && conversation) return <section ref={panelRef} className={`assistant-conversation-panel${compact ? " is-compact" : ""}`} aria-label="助手对话" data-assistant-loading>
    <p className="assistant-conversation-loading" role="status">正在切换对话…</p>
  </section>;

  const headerActions = <AssistantConversationHeaderActions state={state} busy={busy} onConversationChange={onConversationChange} onInput={onInput} legacyHistory={legacyHistory}>{capabilities ? <Button variant="ghost" className="assistant-icon-action" type="button" aria-expanded={diagnosticsExpanded} aria-controls={diagnosticsId} aria-label="技术能力状态（诊断）" title="技术能力状态（诊断）" onClick={() => setDiagnosticsExpanded(value => !value)}><MoreHorizontal size={15}/></Button> : null}</AssistantConversationHeaderActions>;
  return <section ref={panelRef} className={`assistant-conversation-panel${compact ? " is-compact" : ""}`} aria-label={`助手对话：${conversation?.title ?? "新对话"}`}>
    {headerSlot ? createPortal(headerActions, headerSlot) : <div className="assistant-conversation-caption">{headerActions}</div>}
    <div className="assistant-message-scroller">
    <div ref={historyRef} className="assistant-conversation-history" role="region" tabIndex={0} aria-label="持久对话记录" data-conversation-id={conversation?.id}
      onClickCapture={event => {
        if (!conversation || !(event.target instanceof Element)) return;
        const summary = event.target.closest("details > summary");
        const message = event.target.closest(".assistant-message > button[aria-expanded=false]");
        if ((!summary || summary.closest("details")?.open) && !message) return;
        const node = event.currentTarget;
        const id = conversation.id;
        followBottom.current = false;
        state.historyFollowBottom?.current.set(id, false);
        window.requestAnimationFrame(() => {
          if (historyRef.current === node && node.dataset.conversationId === id) setShowJumpToLatest(!isNearBottom(node));
        });
      }}
      onScroll={event => {
        if (!conversation) return;
        const node = event.currentTarget;
        followBottom.current = isNearBottom(node);
        setShowJumpToLatest(!followBottom.current);
        state.historyFollowBottom?.current.set(conversation.id, followBottom.current);
        state.historyScrollPositions?.current.set(conversation.id, node.scrollTop);
      }}>
      {turns.length || creationContexts.length ? <div className="assistant-transcript" role="log" aria-live="polite" aria-relevant="additions">
        {creationContexts.map(context => <ReadingDetails key={`${conversation?.id}:${context.conversation_id}`} reading={reading}
          readingKey={`creation:${context.conversation_id}`}
          className="assistant-creation-context">
          <summary>创建要求与制作记录</summary>
          {context.turns.map(turn => {
            const blocks = turnBlocks(turn);
            return <article key={turn.id} className="assistant-persisted-turn" data-turn-status={turn.status} data-turn-id={turn.id}>
              {turn.user_message ? <ConversationMessage className="assistant-user-message" savedOpen={reading.get(`${turn.id}:user`, false)}
                onOpenChange={open => reading.save(`${turn.id}:user`, open)}>{turn.user_message}</ConversationMessage> : null}
              <TurnContent turn={turn} blocks={blocks} reading={reading} latest={false}/>
              <footer className="assistant-turn-footer"><AssistantTurnExecutionRecord turn={turn} hasPublicTools={blocks.some(block => block.kind === "tools")} model={null} reading={reading}/></footer>
            </article>;
          })}
        </ReadingDetails>)}
        {turns.map((turn) => <TurnView latest={turn.id === latestReplyId} key={`${conversation?.id}:${turn.id}`} inputReceipts={[...inputs.values()].filter(({ item, owner }) => owner === turn.id && item.status !== "accepted" && item.status !== "consumed").map(({ item }) => item)} reading={reading} onTransferReply={onTransferReply} onCreatePlanFromReply={onCreatePlanFromReply} onOpenEditProposal={onOpenEditProposal} onOpenCreationRequest={onOpenCreationRequest} onOpenResultStage={onOpenResultStage} transferDraftState={transferDraftState} turn={turn} onContinue={message => { onInput(message); focus?.focus(); }} continueReason={busy || currentRunning ? "本轮运行中。" : input !== "" ? "当前已有草稿，请先处理当前草稿。" : null} onReadResult={readTurnResult} readingResult={readingResultTurn === turn.id} taskScopeId={conversation?.scope_kind === "task" ? conversation.scope_id : undefined} refreshPaused={state.businessRefreshPaused} windowReadError={conversation ? state.scriptWindowReadErrors?.[assistantScriptWindowReadKey(conversation.id, turn)] : undefined}
          goalReceipt={goalProgress?.root.id === turn.id ? <GoalProgress value={goalProgress} reading={reading} refreshPaused={state.businessRefreshPaused}
            onRead={() => void readTurnResult(goalProgress.root)} readingResult={readingResultTurn === goalProgress.root.id}/> : null}
          productionRecovered={Boolean(goalProgress?.goal.status === "production_completed" && goalProgress.goal.production_result?.status === "succeeded" && goalProgress.goal.production_result.video_ready && goalProgress.turns.some(item => item.id === turn.id))}
          outputRecovered={Boolean(goalProgress && completedGoalOutput(goalProgress) && goalProgress.turns.some(item => item.id === turn.id))}/>)}
        {!turns.length ? <div className="assistant-empty" aria-label="空对话"><p title={scopeLabel}>输入制作目标或编辑要求。</p></div> : null}
      </div> : <div className="assistant-empty" aria-label="空对话"><p title={scopeLabel}>输入制作目标或编辑要求。</p></div>}
    </div>
    {showJumpToLatest && turns.length ? <Button variant="ghost" className="assistant-jump-latest" type="button" aria-label="回到最新回复" title="回到最新回复" onClick={() => { const node = historyRef.current; if (!node || !conversation) return; node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight); followBottom.current = true; state.historyFollowBottom?.current.set(conversation.id, true); state.historyScrollPositions?.current.set(conversation.id, node.scrollTop); setShowJumpToLatest(false); node.focus({preventScroll:true}); }}><ArrowDown size={16} aria-hidden="true"/></Button> : null}
    </div>
    {referenceStatus.reason ? <div role="alert"><p>引用核验未通过。固定版本保持原值，请重新查看或取消引用后发送。</p><Button variant="ghost" type="button" onClick={referenceStatus.cancel}>清除不可用引用</Button><details><summary>引用诊断</summary><p>{referenceStatus.reason}</p></details></div> : null}
    {pendingInputs.length ? <section className="assistant-pending-inputs" aria-label="待处理追加要求">{pendingInputs.map(({ item }) => <InputReceipt key={item.input_id} item={item} reading={reading} onWithdraw={id => void state.withdrawInput(id).catch(cause => setError(cause.message))}/>)}</section> : null}
    <form ref={entryRef} className="assistant-conversation-entry" onSubmit={(event) => void submit(event)}><InputGroup data-composer aria-label="对话输入">
      {selection ? <ReferenceChips usage={materialUsage} items={selection.items} onRemove={item => { selection.remove(item); if (selection.items.length === 1) focus?.focus(); }}/> : null}
      {menuOpen ? <div className="assistant-slash-menu" role="listbox" aria-label="对话命令" id={`${diagnosticsId}-commands`} style={{ maxHeight: menuMaxHeight }}>
        {filteredCommands.length ? filteredCommands.map((item, index) => { const Icon = item.icon; return <Button variant="ghost" role="option" aria-selected={activeCommand === index} key={item.name} type="button" onPointerDown={event => event.preventDefault()} onClick={() => chooseCommand(index)} disabled={commandDisabled} aria-disabled={commandDisabled}>
          <Icon size={16}/><span><strong>{item.title}</strong><small>{item.description}</small></span><code>{item.syntax}</code>
        </Button>; }) : <p>{capabilities ? "没有匹配的已接通命令" : "正在读取可用命令…"}</p>}
        {busy || currentRunning ? <p>本轮运行中，可以编辑草稿，暂不能执行命令。</p> : null}
      </div> : null}
      <InputGroupTextarea ref={setFocus} aria-autocomplete="list" aria-controls={menuOpen ? `${diagnosticsId}-commands` : undefined} aria-describedby={`${diagnosticsId}-input-help`} aria-label={inputLabel} value={input} disabled={state.loading || !conversation} onChange={(event) => { onInput(event.target.value); if (event.target.value.endsWith("@") && !composing) setReferencePickerOpen(true); }} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onKeyDown={keyDown} placeholder={acceptsQueuedInput ? "还有什么要补充？" : placeholder ?? "想做什么，或想怎么改？"} rows={1} />
      <div className="assistant-conversation-actions">
        <div className="assistant-input-tools">
          <ReferencePicker open={referencePickerOpen} onOpenChange={setReferencePickerOpen} onAdded={()=>{if(input.endsWith("@"))onInput(input.slice(0,-1));focus?.focus();}}/>
          {composerActions}
          {acceptsQueuedInput ? <label className="assistant-input-mode"><span className="assistant-visually-hidden">追加要求</span><select aria-label="追加要求处理方式" title="补充当前要求，或等本轮结束后再处理" value={inputMode} onChange={event=>setInputMode(event.target.value as "steer"|"follow_up")}><option value="steer">补充当前要求</option><option value="follow_up">完成后处理</option></select></label> : null}
          {!configured ? <a className="assistant-input-status" href="/settings" aria-label="连接文字模型" title="在设置中连接对话服务"><CircleHelp size={14}/>对话未连接</a> : referenceStatus.pending ? <span className="assistant-input-status" role="status">正在核验引用</span> : null}</div>
        {currentRunning && lastTurnId && !lastTurnId.startsWith("pending-") ? <Button variant="ghost" className="assistant-stop" type="button" aria-label={stopping ? "正在请求停止" : "停止本轮"} disabled={stopping} title="停止本轮回复；已提交的业务动作不撤销" onClick={() => void stopCurrentTurn()}><Square aria-hidden="true" size={14}/></Button>
          : null}
        {!currentRunning || input.trim() ? <Tooltip label={sendReason}><Button variant="ghost" className="assistant-send" type="submit" aria-label={sendLabel} aria-description={sendReason} disabled={sendDisabled}><ArrowUp aria-hidden="true" size={16} /></Button></Tooltip> : null}
      </div>
      <span className="assistant-visually-hidden" id={`${diagnosticsId}-input-help`}>Enter 发送，Shift+Enter 换行；中文组合输入不会发送。{sendDisabled ? sendReason : "输入 / 查找命令。"}</span>
    </InputGroup></form>
      {capabilities && diagnosticsExpanded ? <HomeCardDialog title="对话诊断" onClose={() => setDiagnosticsExpanded(false)}><div className="assistant-capability-diagnostics" id={diagnosticsId}>
        <p>对话服务：{capabilities.runtime.status === "ready" ? "已就绪" : capabilities.runtime.reason}</p>
        <p>可用命令：{capabilities.application_commands.join("、")}</p>
        <p>创作技能：{capabilities.skills.available ? capabilities.skills.names.map(name => ({"source-speech":"原声口播", "narration-materials":"旁白配素材", "local-edit":"局部改片"}[name] ?? name)).join("、") : "当前不可用"}。{capabilities.skills.reason}</p>
        <p>{capabilities.prompt_templates.reason}</p>
        <p>引用会固定到选中时的版本；版本变化后需要重新选择。助手会区分工具回执与实际画面、声音的检查结果。</p>
      </div></HomeCardDialog> : null}
    {historyOpen ? <HomeCardDialog title="历史对话" onClose={() => setHistoryOpen(false)}><div className="assistant-resume-list">{state.conversations.map(item => <Button variant="ghost" type="button" key={item.id} onClick={() => void state.resume(item.id).then(next => { onConversationChange?.(); onInput(next.draft_text); setHistoryOpen(false); focus?.focus(); }).catch(cause => setError(cause.message))}><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleString()}</small></Button>)}</div></HomeCardDialog> : null}
    {(error || state.error) && !turns.at(-1)?.events.some(event =>
      (event.type === "error" || event.type === "unknown") && event.message.trim() === (error ?? state.error)?.trim())
      ? <div className="assistant-conversation-error" role="alert"><AssistantFailureMessage message={error ?? state.error ?? ""}/></div> : null}
  </section>;
}
