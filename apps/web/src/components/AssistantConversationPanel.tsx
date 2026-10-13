"use client";

import { useEffect, useLayoutEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { useAssistantKnowledgeReference, useAssistantMaterialReference, useAssistantProfileReference, useAssistantReferenceStatus } from "./AssistantConversationProvider";
import { HomeCardDialog } from "./HomeCardDialog";
import "./AssistantConversation.css";
import { Plus, History, Pencil, CircleHelp, ArrowUp, ArrowDown, MessageCircleDashed, LoaderCircle, Copy, Check, MoreHorizontal, Square, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { createPortal } from "react-dom";
import remarkGfm from "remark-gfm";
import Link from "next/link";
import { getAssistantCapabilities, taskMediaJobLabel } from "@/lib/api";
import type { AssistantConversationTurn, AssistantScriptToolReceipt, AssistantToolFailure } from "@/lib/types";
import type { useAssistantConversation } from "@/components/useAssistantConversation";
import { assistantTaskTurnResult, assistantTurnNeedsResultRefresh, assistantTurnHasUnknownRequest, assistantConversationGoal } from "./useAssistantConversation";

const commandDescriptions: Record<string, { title: string; description: string; icon: typeof Plus }> = {
  "/new": { title: "新对话", description: "保留历史，开始新一段对话", icon: Plus },
  "/resume": { title: "恢复对话", description: "选择已有对话继续", icon: History },
  "/name": { title: "命名对话", description: "为当前对话设置名称", icon: Pencil },
  "/help": { title: "查看命令", description: "查看已接通的对话操作", icon: CircleHelp },
};

type Conversation = ReturnType<typeof useAssistantConversation>;
type ReadingState = { get: (key: string, fallback: boolean) => boolean; save: (key: string, open: boolean) => void };
const isNearBottom = (node: HTMLDivElement) => node.scrollHeight - node.clientHeight - node.scrollTop <= 48;

function ReadingDetails({ reading, readingKey, defaultOpen = false, className, children }: {
  reading?: ReadingState; readingKey: string; defaultOpen?: boolean; className?: string; children: ReactNode;
}) {
  return <details className={className} data-reading-key={readingKey} open={reading?.get(readingKey, defaultOpen) ?? defaultOpen}
    onToggle={event => reading?.save(readingKey, event.currentTarget.open)}>{children}</details>;
}

type TurnBlock = { kind: "tool"; tool_id: number; label: string; state: "running" | "succeeded" | "failed" | "unreturned"; summary: string; failure?: AssistantToolFailure };

function turnTools(turn: AssistantConversationTurn): TurnBlock[] {
  const ordered: TurnBlock[] = [];
  for (const event of turn.events) {
    if (event.type === "tool_start") {
      ordered.push({ kind: "tool", tool_id: event.tool_id, label: event.label, state: "running", summary: "" });
      continue;
    }
    if (event.type !== "tool_end") continue;
    let index = -1;
    for (let candidate = ordered.length - 1; candidate >= 0; candidate -= 1) {
      const item = ordered[candidate];
      if (item.kind === "tool" && item.tool_id === event.tool_id) { index = candidate; break; }
    }
    if (index < 0) {
      ordered.push({ kind: "tool", tool_id: event.tool_id, label: event.label, state: event.ok ? "succeeded" : "failed", summary: event.summary, failure: event.failure });
    } else {
      const previous = ordered[index];
      if (previous.kind === "tool") ordered[index] = { ...previous, label: event.label, state: event.ok ? "succeeded" : "failed", summary: event.summary, failure: event.failure };
    }
  }
  return ordered.map(tool => tool.state === "running" && turn.status !== "running" ? {...tool, state: "unreturned"} : tool);
}

const toolStateLabels = {running: "进行中", succeeded: "已返回", failed: "未完成", unreturned: "未收到结果"};

function ToolResult({ tool, reading, readingKey }: { tool: TurnBlock; reading?: ReadingState; readingKey: string }) {
  if (tool.state === "failed" && tool.failure) return <div className="assistant-tool-failure">
    <p>{tool.failure.reason}</p>{tool.failure.repair_action ? <p>{tool.failure.repair_action}</p> : null}
  </div>;
  return <div><AssistantFailureMessage reading={reading} readingKey={readingKey}
    message={tool.summary || (tool.state === "running" ? "等待此操作返回。"
      : tool.state === "unreturned" ? "本轮已结束，此操作没有返回结果；不能据此判断后台作业是否完成。" : "操作没有返回摘要。")}/></div>;
}

const goalStateLabels: Record<string, string> = {
  running: "正在执行", waiting_result: "等待后台结果", waiting_adoption: "等待查看并采用修改",
  waiting_identity: "等待核验身份", waiting_unknown: "请求结果待核实", invalidated: "原目标已停止接续",
  no_progress: "接续未取得新进展", limit_reached: "接续已暂停",
};
const goalReasonLabels: Record<string, string> = {
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
  return value.goal.status === "no_progress" && value.goal.waiting_reason === "bounded_output_checkpoint_completed"
    ? value.goal.completed_output_checkpoint : undefined;
}

function GoalProgress({ value, reading, refreshPaused, onRead, readingResult }: {
  value: NonNullable<ReturnType<typeof assistantConversationGoal>>; reading: ReadingState;
  refreshPaused: boolean; onRead: () => void; readingResult: boolean;
}) {
  const {root, goal, turns} = value;
  const current = turns.find(turn => turn.id === goal.current_turn_id);
  const tools = turns.flatMap(turn => turnTools(turn).map(tool => ({turn, tool})));
  const currentTool = current && turnTools(current).reverse().find(tool => tool.state === "running");
  const execution = [...turns].reverse().map(turn => assistantTaskTurnResult(turn)?.response?.execution_status).find(Boolean);
  const job = execution?.job;
  const videoReady = goal.status !== "invalidated" && execution?.video_ready === true;
  const media = [...turns].reverse().map(turn => assistantTaskTurnResult(turn)?.response?.media_job).find(Boolean);
  const reason = goal.waiting_reason && goalReasonLabels[goal.waiting_reason];
  const completed = completedGoalOutput(value);
  const active = ["running", "waiting_result", "waiting_adoption", "waiting_identity", "waiting_unknown"].includes(goal.status);
  const currentStep = currentTool ? `${currentTool.label} · 进行中`
    : goal.status === "waiting_result" && job ? `${executionStageLabels[execution?.current_stage ?? ""] ?? "制作作业"} · ${executionStatusLabels[job.status] ?? "状态待核实"}`
      : goal.status === "waiting_result" && media ? `${String(media.result_kind) === "observation" ? "输出检查" : media.result_kind === "graphic_source" ? "准备图片" : "后台预览"} · ${executionStatusLabels[media.status] ?? "状态待核实"}`
        : current?.status === "running" ? "正在整理回复" : reason;
  return <ReadingDetails reading={reading} readingKey={`goal:${root.id}`} className="assistant-goal-progress">
    <summary aria-label="本次目标执行状态"><span>本次目标 · {completed ? "制作与技术检查已完成" : goalStateLabels[goal.status] ?? "状态待核实"}</span>
      {completed ? <small>检查范围与未检查内容可展开查看</small> : currentStep ? <small>{currentStep}</small> : null}</summary>
    <div className="assistant-goal-body">
      <p className="assistant-goal-instruction">{root.user_message}</p>
      {completed ? <div className="assistant-goal-receipt" aria-label="实际输出检查范围">
        <p>已核对当前成片与同版本、同文件的实际观察结果。</p>
        <p>{completed.measurement_scope === "actual_output_bounded_frames"
          ? "检查范围：实际成片的有限抽帧与媒体参数，未逐帧检查。" : "实际观察已返回；具体检查范围尚不能在这里核实。"}</p>
        {completed.unavailable.length ? <ul aria-label="本次未检查的内容">{completed.unavailable.map((item, index) => <li key={`${item.scope}:${index}`}>{item.reason}</li>)}</ul> : null}
        <p>这份技术检查不能替代对全部创作要求的验收。</p>
        {turns.some(turn => turn.status === "failed" || turnTools(turn).some(tool => tool.state === "failed" || tool.state === "unreturned"))
          ? <p>此前有中断或未完成调用；制作与技术检查已恢复完成，历史记录保留。</p> : null}
      </div> : null}
      {reason && reason !== currentStep ? <p>{reason}</p> : null}
      {tools.length ? <ol aria-label="本次目标的执行步骤">{tools.map(({turn, tool}, index) => <li key={`${turn.id}:${tool.tool_id}:${index}`} data-state={tool.state}>
        <strong>{tool.label} · {toolStateLabels[tool.state]}</strong>
        <ToolResult tool={tool} reading={reading} readingKey={`goal:${root.id}:${turn.id}:${tool.tool_id}:diagnostic`}/>
      </li>)}</ol> : <p>还没有可核实的工具调用记录。</p>}
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
      {completed || videoReady && job?.status === "succeeded"
        ? <Link className="assistant-text-action" href={`/tasks/${encodeURIComponent(goal.task_id)}`}>查看对应任务与成片</Link> : null}
      {refreshPaused && active ? <p>自动查询已暂停；后台状态仍以实际结果为准，可以手动查询。</p> : null}
      {active || job?.status === "interrupted" ? <button className="assistant-text-action" type="button" disabled={readingResult} onClick={onRead}>{readingResult ? "正在查询…" : "查询目标进展"}</button> : null}
    </div>
  </ReadingDetails>;
}

export function AssistantFailureMessage({ message, reading, readingKey = "diagnostic" }: { message: string; reading?: ReadingState; readingKey?: string }) {
  const diagnostic = /https?:\/\/|Traceback|HTTP\s?\d|\b(?:unreachable|ECONNREFUSED|ENOTFOUND|fetch failed|Connection refused)\b|\/(?:Users|Volumes|private)\/|\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|(?:不是|并非)\s*有效(?:的)?\s*JSON\b|^\s*[{[]/i.test(message);
  if (!diagnostic) return <>{message}</>;
  return <><span>这次操作没有完成，请打开诊断查看原因。</span><ReadingDetails reading={reading} readingKey={readingKey}><summary>诊断详情</summary><pre>{message}</pre></ReadingDetails></>;
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
  return <button className="assistant-copy assistant-icon-action" aria-label={label} title={label} onClick={() => void copy()} type="button">{copied ? <Check size={14}/> : <Copy size={14}/>}</button>;
}

function TurnContent({ turn, reading }: { turn: AssistantConversationTurn; reading?: ReadingState }) {
  const tools = turnTools(turn);
  const runningTools = tools.some((tool) => tool.state === "running");
  const failedTools = tools.some((tool) => tool.state === "failed" || tool.state === "unreturned");
  return <div className="assistant-markdown-wrap">
    {tools.length ? <ReadingDetails reading={reading} readingKey={`${turn.id}:tools`} className="assistant-turn-tools" defaultOpen={turn.status === "running"}>
      <summary>工具记录 · {tools.length} 项 · {runningTools ? "进行中" : failedTools ? "含失败记录" : "调用已结束"}</summary>
      <ol>{tools.map((tool, index) => <li data-state={tool.state} key={`${tool.tool_id}-${index}`}>
        <strong>{tool.label} · {toolStateLabels[tool.state]}</strong>
        <ToolResult tool={tool} reading={reading} readingKey={`${turn.id}:tool:${tool.tool_id}:diagnostic`}/>
      </li>)}</ol>
    </ReadingDetails> : null}
    {turn.assistant_text ? <div className="assistant-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.assistant_text}</ReactMarkdown></div> : null}
  </div>;
}

export function AssistantConversationHeaderActions({ state, busy, onConversationChange, onInput, children, legacyHistory }: {
  state: Conversation; busy: boolean; onConversationChange?: () => void; onInput: (value: string) => void; children?: ReactNode; legacyHistory?: ReactNode;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const running = state.conversation?.turns.at(-1)?.status === "running";
  const disabled = state.loading || busy || running;
  const fail = (cause: unknown) => state.setError(cause instanceof Error ? cause.message : "对话操作没有完成，请重试。");
  return <div className="assistant-header-actions">
    <button className="assistant-icon-action" type="button" aria-label="历史对话" title="历史对话" disabled={disabled}
      onClick={() => { void state.loadList().then(() => setHistoryOpen(true)).catch(fail); }}><History size={15}/></button>
    <button className="assistant-icon-action" type="button" aria-label="新对话" title="新对话" disabled={disabled}
      onClick={() => { void state.createNew().then(next => { onConversationChange?.(); onInput(next.draft_text); }).catch(fail); }}><Plus size={15}/></button>
    {children}
    {historyOpen ? <HomeCardDialog title="历史对话" onClose={() => setHistoryOpen(false)}><div className="assistant-resume-list">
      {state.conversations.map(item => <button type="button" key={item.id} aria-current={item.id === state.conversation?.id ? "true" : undefined}
        onClick={() => { void state.resume(item.id).then(next => { onConversationChange?.(); onInput(next.draft_text); setHistoryOpen(false); }).catch(fail); }}><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleString()}</small></button>)}
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
    {onTransferReply || onCreatePlanFromReply ? <button ref={trigger} className="assistant-icon-action" type="button" aria-label="回复操作" title="回复操作" aria-haspopup="menu" aria-expanded={open} onKeyDown={event => { if (!open && ["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setOpen(true); } }} onClick={() => setOpen(value => !value)}><MoreHorizontal size={15}/></button> : null}
    {open ? createPortal(<div ref={menu} style={position} className="assistant-reply-menu" role="menu" aria-label="回复操作">
      {onTransferReply ? <button type="button" role="menuitem" disabled={transferDraftState === "confirmed"} title={transferDraftState === "confirmed" ? "当前文案已确认，不能替换。" : undefined} onClick={useReply}>{transferDraftState === "draft" ? "替换当前草稿并带入回复" : transferDraftState === "confirmed" ? "文案已确认" : "带入文案草稿"}</button> : null}
      {onCreatePlanFromReply ? <button type="button" role="menuitem" onClick={() => { close(); onCreatePlanFromReply(turn); }}>带入新任务文案</button> : null}
    </div>, document.body) : null}
    {confirmReplace ? <HomeCardDialog kind="confirmation" title="替换待确认文案" onClose={() => setConfirmReplace(false)}
      footer={<><button type="button" data-card-cancel onClick={() => setConfirmReplace(false)}>取消</button><button type="button" onClick={() => { setConfirmReplace(false); onTransferReply?.(turn, true); }}>确认替换并带入回复</button></>}>
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

function TurnView({ turn, reading, onTransferReply, onCreatePlanFromReply, onOpenEditProposal, onOpenCreationRequest, transferDraftState, onContinue, continueReason, onReadResult, readingResult, refreshPaused, outputRecovered = false }: { turn: AssistantConversationTurn; reading?: ReadingState; onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void; onCreatePlanFromReply?: (turn: AssistantConversationTurn) => void; onOpenEditProposal?: (turn: AssistantConversationTurn) => void; onOpenCreationRequest?: (turn: AssistantConversationTurn) => void; transferDraftState: "none" | "draft" | "confirmed"; onContinue: (message: string) => void; continueReason: string | null; onReadResult: (turn: AssistantConversationTurn, openProposal?: boolean) => void; readingResult: boolean; refreshPaused: boolean; outputRecovered?: boolean }) {
  const businessContinuation = turn.events.some((event: unknown) => typeof event === "object" && event !== null
    && "type" in event && event.type === "goal_continuation_started"
    && "source_kind" in event && event.source_kind === "business_event");
  const result = turn.result;
  const taskResult = assistantTaskTurnResult(turn);
  const continuation = taskResult?.continuation;
  const job = taskResult?.response?.media_job;
  const request = taskResult?.response?.edit_request;
  const toolSummary = scriptToolSummary(taskResult?.response?.script_tool);
  const outcomeStatus = continuation?.status === "failed" && job?.status === "cancelled"
    ? job.status : continuation?.status ?? job?.status;
  const mediaJobLabel = job && (job.status === "queued" || job.status === "cancelled")
    ? taskMediaJobLabel(job) : null;
  const unknownRequest = assistantTurnHasUnknownRequest(turn);
  const pendingResult = assistantTurnNeedsResultRefresh(turn);
  const graphicSourceReady = job?.result_kind === "graphic_source" && job.status === "succeeded"
    && job.result && "source_ref" in job.result && "artifact_id" in job.result;
  const graphicSourceSummary = "图片来源已生成，可以继续预览它在视频里的效果；尚未应用到草稿或生成成片。";
  const businessSummary = taskResult ? unknownRequest
    ? "请求结果尚未核实；原请求定位已保留。先查询结果，不重复提交。"
    : graphicSourceReady ? graphicSourceSummary
    : outcomeStatus === "confirmed"
      ? continuation?.result_version_id ? "修改已应用并保存到草稿，可撤销；成片尚未重新生成。" : "已收到采用决定，保存结果仍待核实。"
      : outcomeStatus === "rejected" ? "修改方案已放弃，当前草稿保持不变。"
        : outcomeStatus === "cancelled" ? "后台处理已取消，未据此应用修改。"
          : outcomeStatus === "stale" || outcomeStatus === "expired" ? "原修改结果已过期，仅保留为历史，不能沿旧版本应用。"
            : outcomeStatus === "stopped" ? "本轮接续已停止，原结果仅保留为历史。"
              : outcomeStatus === "failed" ? "后台处理未完成；请查看实际原因，当前草稿未因此改变。"
                : outcomeStatus === "queued" ? "预览已安排，正在等待后台处理；尚未生成预览或应用修改。"
                  : outcomeStatus === "running" ? "后台正在准备预览；当前草稿尚未改变。"
                    : outcomeStatus === "interrupted" ? "后台处理已中断，等待明确决定；没有自动重试。"
                      : canOpenTurnProposal(turn) ? "实际修改预览已返回，等待你查看并决定是否采用；当前草稿尚未改变。"
                        : request?.status === "not_submitted" ? "请求没有提交；请处理具体原因后明确再试。"
                          : request?.status === "rejected" ? "本次请求已被拒绝，没有沿该请求继续应用。"
                            : null : null;
  const executionStatusLabels: Record<string, string> = {
    queued: "已排队", running: "后台处理中", succeeded: "已完成", failed: "失败",
    cancelled: "已取消", interrupted: "中断等待继续",
  };
  const resultSummary = businessSummary ?? toolSummary ?? (result?.kind === "plan_preview"
    ? (result.plan_preview as Record<string, unknown> | undefined)?.confirmable === true
      ? "方案已检查，可查看页面中的方案卡后决定是否创建。"
      : "方案检查已返回阻断项；请按页面中的真实原因处理。"
    : result?.kind === "creation_request"
      ? "制作目标已保存；打开创作目标可读取当前方案与任务。"
    : result?.kind === "material_result"
      ? `${result.scope === "segment" ? "当前片段" : "整条素材"}标签已回读${result.changed === true ? "，内容有变化。" : result.changed === false ? "，内容没有变化。" : "。"}`
      : result?.kind === "task_turn"
        ? [result.topic_candidates === true ? "已生成选题候选" : null,
          result.timeline_action === "undo" ? "已整体撤销上一步编辑" : result.timeline_action === "redo" ? "已重做上一步编辑" : result.timeline_action === "reject" ? "已取消待确认修改" : null,
          typeof result.script_draft === "string" ? "文案草稿已保存" : null,
          typeof result.execution_action === "string" ? `已提交${result.execution_action === "start" ? "开始制作" : result.execution_action === "resume" ? "继续制作" : "取消制作"}${typeof result.execution_status === "string" ? `（作业${executionStatusLabels[result.execution_status] ?? result.execution_status}）` : ""}` : null,
          result.edit_preview === true ? "已生成待确认修改提案" : null].filter(Boolean).join("；") || null
        : result?.kind === "failure" && typeof result.message === "string"
          ? result.message
          : null);
  return <article className="assistant-persisted-turn" data-turn-status={turn.status}>
    {turn.user_message.trim() ? <p className="assistant-user-message">{turn.user_message}</p> : null}
    {businessContinuation ? <p className="assistant-stream-status">继续完成原要求</p> : null}
    <TurnContent turn={turn} reading={reading} />
    {turn.status === "running" ? <p role="status" className="assistant-stream-status"><LoaderCircle aria-hidden size={12} className="is-spinning"/>正在回复…</p> : null}
    {resultSummary ? <ReadingDetails reading={reading} readingKey={`${turn.id}:result`} defaultOpen={Boolean(taskResult)} className="assistant-turn-result"><summary>本轮结果</summary><div><AssistantFailureMessage reading={reading} readingKey={`${turn.id}:result:diagnostic`} message={resultSummary}/>
      {toolSummary && toolSummary !== resultSummary ? <p>{toolSummary}</p> : null}
      {unknownRequest && graphicSourceReady ? <p>{graphicSourceSummary}</p> : null}
      {mediaJobLabel ? <p>{mediaJobLabel}</p> : continuation?.waiting_reason || job?.waiting_reason
        ? <p>{continuation?.waiting_reason ?? job?.waiting_reason}</p> : null}
      {job?.status !== "cancelled" && job?.failure_reason ? <p>{job.failure_reason}</p> : null}{job?.repair_action ? <p>{job.repair_action}</p> : null}
      {refreshPaused && pendingResult ? <p>自动查询已暂停；后台状态仍以实际结果为准，可以手动查询。</p> : null}
      {onOpenEditProposal && canOpenTurnProposal(turn) ? <button type="button" disabled={Boolean(continueReason) || readingResult} onClick={() => onReadResult(turn, true)}>{readingResult ? "正在查询…" : "查看修改方案"}</button> : null}
      {pendingResult || unknownRequest || job?.status === "interrupted" ? <button type="button" disabled={readingResult} onClick={() => onReadResult(turn)}>{readingResult ? "正在查询…" : "查询本轮结果"}</button> : null}
    </div></ReadingDetails> : null}
    {onOpenCreationRequest && turn.status !== "running" && result?.kind === "creation_request" && typeof result.creation_request_id === "string" ? <button className="assistant-text-action" type="button" disabled={Boolean(continueReason)} onClick={() => onOpenCreationRequest(turn)}>查看这次创作目标</button> : null}
    {turn.status === "failed" ? <p role="status">{outputRecovered ? "本轮回复曾中断；制作与技术检查已恢复完成，历史失败保留。"
      : businessContinuation ? "接续未完成；原要求和实际结果保留在记录中。" : "本轮未完成；本条要求保留在记录中。"}</p> : null}
    {turn.status === "stopped" ? <p role="status">本轮已停止；先前的业务动作不会因此撤销。</p> : null}
    {(turn.status === "failed" || turn.status === "stopped") && turn.user_message.trim() && !unknownRequest ? <button className="assistant-icon-action" aria-label="继续编辑本条要求" type="button" disabled={Boolean(continueReason)} title={continueReason ?? "继续编辑本条要求；只带入输入框。"} onClick={() => onContinue(turn.user_message)}><Pencil size={14}/></button> : null}
    {turn.status !== "running" && turn.assistant_text.trim() ? <ReplyActions turn={turn} onTransferReply={turn.status === "completed" ? onTransferReply : undefined} onCreatePlanFromReply={turn.status === "completed" ? onCreatePlanFromReply : undefined} transferDraftState={transferDraftState}/> : null}
  </article>;
}

export function AssistantConversationPanel({
  state, input, onInput, onSend, onConversationChange, onTransferReply, onCreatePlanFromReply, onOpenEditProposal, onOpenCreationRequest, transferDraftState = "none", busy, configured, scopeLabel, inputLabel = "对话要求", placeholder, compact = false, autoFocus = false, legacyHistory,
}: {
  state: Conversation;
  input: string;
  onInput: (value: string) => void;
  onSend: (text: string) => Promise<void>;
  onConversationChange?: () => void;
  onTransferReply?: (turn: AssistantConversationTurn, replace: boolean) => void;
  onCreatePlanFromReply?: (turn: AssistantConversationTurn) => void;
  onOpenEditProposal?: (turn: AssistantConversationTurn) => void; onOpenCreationRequest?: (turn: AssistantConversationTurn) => void;
  transferDraftState?: "none" | "draft" | "confirmed";
  busy: boolean;
  configured: boolean;
  scopeLabel: string;
  inputLabel?: string;
  placeholder?: string;
  compact?: boolean;
  autoFocus?: boolean;
  legacyHistory?: ReactNode;
}) {
  const selectedProfile = useAssistantProfileReference();
  const selectedMaterial = useAssistantMaterialReference();
  const selectedKnowledge = useAssistantKnowledgeReference();
  const referenceStatus = useAssistantReferenceStatus();
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof getAssistantCapabilities>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [diagnosticsExpanded, setDiagnosticsExpanded] = useState(false);
  const [menuClosedFor, setMenuClosedFor] = useState<string | null>(null);
  const [activeCommand, setActiveCommand] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
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
  const diagnosticsId = useId();
  useLayoutEffect(() => {
    setHeaderSlot(panelRef.current?.closest('[aria-label="创作助手"]')?.querySelector<HTMLElement>("[data-assistant-header-actions]") ?? null);
  }, []);
  const conversation = state.conversation;
  useEffect(() => { resultReadRevision.current += 1; setReadingResultTurn(null); }, [conversation?.id]);
  const readTurnResult = async (turn: AssistantConversationTurn, openProposal = false) => {
    const id = conversation?.id;
    if (!id) return;
    const read = ++resultReadRevision.current;
    setReadingResultTurn(turn.id);
    setError(null);
    try {
      const fresh = await state.refresh();
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
  const turns = conversation?.turns ?? [];
  const goalProgress = assistantConversationGoal(conversation);
  const historyContent = JSON.stringify(turns.map(turn => [turn.id, turn.status, turn.updated_at, turn.assistant_text,
    turn.events.length, assistantTaskTurnResult(turn)?.continuation, assistantTaskTurnResult(turn)?.response?.media_job?.status,
    assistantTaskTurnResult(turn)?.response?.edit_request?.status, assistantTaskTurnResult(turn)?.response?.execution_status,
    turn.result?.goal_continuation]));
  const latestHistoryContent = useRef(historyContent);
  latestHistoryContent.current = historyContent;
  const isLocalCommand = input.trim().startsWith("/");
  const commandDisabled = state.loading || !conversation || busy || currentRunning || composing;
  const sendLabel = currentRunning && !isLocalCommand ? "本轮运行中" : busy && !isLocalCommand ? "正在处理" : "发送";
  const sendDisabled = commandDisabled || !input.trim() || (!isLocalCommand && (referenceStatus.pending || Boolean(referenceStatus.reason) || !configured));
  const sendReason = state.loading || !conversation ? "正在恢复对话"
    : composing ? "中文组合输入中，请先完成输入"
    : !input.trim() ? "先填写对话要求"
      : currentRunning ? "此对话已有正在运行的轮次；可先停止本轮或切换到另一段对话"
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
  const menuOpen = commandToken && menuClosedFor !== input;
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
    const position = state.historyScrollPositions?.current.get(id) ?? 0;
    const contentAtEntry = latestHistoryContent.current;
    let restored = false;
    const frame = window.requestAnimationFrame(() => {
      node.scrollTop = position;
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
      node.querySelectorAll<HTMLDetailsElement>("details[data-reading-key]").forEach(detail => {
        expanded?.set(detail.dataset.readingKey!, detail.open);
      });
    };
  }, [conversation?.id, state.loading, state.historyScrollPositions, state.historyExpandedDetails, state.historyFollowBottom]);

  useLayoutEffect(() => {
    const id = conversation?.id;
    const node = historyRef.current;
    if (!node || !id || state.loading || restoredHistoryId.current !== id || !followBottom.current) return;
    const frame = window.requestAnimationFrame(() => {
      if (node.dataset.conversationId !== id || restoredHistoryId.current !== id || !followBottom.current) return;
      node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight);
      state.historyScrollPositions?.current.set(id, node.scrollTop);
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
  useEffect(() => {
    if (!focus) return;
    focus.style.height = "0px";
    focus.style.height = `${Math.max(50, Math.min(150, focus.scrollHeight))}px`;
  }, [input, focus]);
  useEffect(() => {
    if (autoFocus && !state.loading && conversation) focus?.focus();
  }, [autoFocus, conversation?.id, focus, state.loading]);

  async function submit(event?: FormEvent, commandText?: string) {
    event?.preventDefault();
    const text = (commandText ?? input).trim();
    if (!text || composing) return;
    const localCommand = text.startsWith("/");
    if (state.loading || !conversation) { setError(sendReason); return; }
    if (!localCommand && !configured) { setError(sendReason); return; }
    if (busy || currentRunning) { setError(localCommand ? "本轮运行中，可继续编辑草稿；停止后再执行。" : sendReason); return; }
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
    if (currentRunning || referenceStatus.pending || referenceStatus.reason) {
      setError(sendReason);
      return;
    }
    setError(null);
    try {
      await onSend(text);
      focus?.focus();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "对话没有完成。"); }
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

  const headerActions = <AssistantConversationHeaderActions state={state} busy={busy} onConversationChange={onConversationChange} onInput={onInput} legacyHistory={legacyHistory}>{capabilities ? <button className="assistant-icon-action" type="button" aria-expanded={diagnosticsExpanded} aria-controls={diagnosticsId} aria-label="技术能力状态（诊断）" title="技术能力状态（诊断）" onClick={() => setDiagnosticsExpanded(value => !value)}><MoreHorizontal size={15}/></button> : null}</AssistantConversationHeaderActions>;
  return <section ref={panelRef} className={`assistant-conversation-panel${compact ? " is-compact" : ""}`} aria-label={`助手对话：${conversation?.title ?? "新对话"}`}>
    {headerSlot ? createPortal(headerActions, headerSlot) : <div className="assistant-conversation-caption">{headerActions}</div>}
    {goalProgress ? <GoalProgress value={goalProgress} reading={reading} refreshPaused={state.businessRefreshPaused}
      onRead={() => void readTurnResult(goalProgress.root)} readingResult={readingResultTurn === goalProgress.root.id}/> : null}
    <div className="assistant-message-scroller">
    <div ref={historyRef} className="assistant-conversation-history" role="region" tabIndex={0} aria-label="持久对话记录" data-conversation-id={conversation?.id}
      onScroll={event => {
        if (!conversation) return;
        const node = event.currentTarget;
        followBottom.current = isNearBottom(node);
        setShowJumpToLatest(!followBottom.current);
        state.historyFollowBottom?.current.set(conversation.id, followBottom.current);
        state.historyScrollPositions?.current.set(conversation.id, node.scrollTop);
      }}>
      {turns.length ? <div className="assistant-transcript" role="log" aria-live="polite" aria-relevant="additions" aria-busy={currentRunning}>
        {turns.map((turn) => <TurnView key={turn.id} reading={reading} onTransferReply={onTransferReply} onCreatePlanFromReply={onCreatePlanFromReply} onOpenEditProposal={onOpenEditProposal} onOpenCreationRequest={onOpenCreationRequest} transferDraftState={transferDraftState} turn={turn} onContinue={message => { onInput(message); focus?.focus(); }} continueReason={busy || currentRunning ? "本轮运行中。" : input !== "" ? "当前已有草稿，请先处理当前草稿。" : null} onReadResult={readTurnResult} readingResult={readingResultTurn === turn.id} refreshPaused={state.businessRefreshPaused}
          outputRecovered={Boolean(goalProgress && completedGoalOutput(goalProgress) && goalProgress.turns.some(item => item.id === turn.id))}/>)}
      </div> : !state.loading ? <div className="assistant-empty" aria-label="空对话"><div className="assistant-empty-icon"><MessageCircleDashed aria-hidden="true" size={22}/></div><h2>想聊点什么？</h2><p title={scopeLabel}>说说你的想法，或告诉我想怎么改。</p></div> : null}
      {state.loading ? <p role="status">正在恢复对话…</p> : null}
    </div>
    {showJumpToLatest && turns.length ? <button className="assistant-jump-latest" type="button" aria-label="回到最新回复" title="回到最新回复" onClick={() => { const node = historyRef.current; if (!node || !conversation) return; node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight); followBottom.current = true; state.historyFollowBottom?.current.set(conversation.id, true); state.historyScrollPositions?.current.set(conversation.id, node.scrollTop); setShowJumpToLatest(false); }}><ArrowDown size={16} aria-hidden="true"/></button> : null}
    </div>
    {referenceStatus.reason ? <div role="alert"><p>引用核验未通过。固定版本保持原值，请重新查看或取消引用后发送。</p><button type="button" onClick={referenceStatus.cancel}>清除不可用引用</button><details><summary>引用诊断</summary><p>{referenceStatus.reason}</p></details></div> : null}
    <form ref={entryRef} className="assistant-conversation-entry" onSubmit={(event) => void submit(event)}>
      {selectedMaterial?.reference ? <p className="assistant-reference" aria-label="当前素材对话范围" title={selectedMaterial.reference.label}><span>{selectedMaterial.reference.label}</span><button className="assistant-icon-action" type="button" aria-label="取消素材引用" title="取消引用" onClick={() => { selectedMaterial.setReference(null); focus?.focus(); }}><X size={13}/></button></p> : null}
      {selectedKnowledge?.reference ? <p className="assistant-reference" title={`${selectedKnowledge.reference.label} · 版本 ${selectedKnowledge.reference.version}${selectedKnowledge.reference.locator ? ` · ${selectedKnowledge.reference.locator}` : ""}`}><span>{selectedKnowledge.reference.label} · 版本 {selectedKnowledge.reference.version}{selectedKnowledge.reference.locator ? ` · ${selectedKnowledge.reference.locator}` : ""}</span><button className="assistant-icon-action" type="button" aria-label="取消知识引用" title="取消引用" onClick={() => { selectedKnowledge.setReference(null); focus?.focus(); }}><X size={13}/></button></p> : null}
      {selectedProfile?.reference ? <p className="assistant-reference" title={`固定版本身份：${selectedProfile.reference.version}`}><span>{selectedProfile.reference.label}{/第\s*\d+\s*版|版本\s*\d+/.test(selectedProfile.reference.label) ? "" : /^\d+$/.test(selectedProfile.reference.version) ? ` · 版本 ${selectedProfile.reference.version}` : " · 固定版本"}</span><button className="assistant-icon-action" type="button" aria-label="取消档案引用" title="取消引用" onClick={() => { selectedProfile.setReference(null); focus?.focus(); }}><X size={13}/></button></p> : null}
      {menuOpen ? <div className="assistant-slash-menu" role="listbox" aria-label="对话命令" id={`${diagnosticsId}-commands`} style={{ maxHeight: menuMaxHeight }}>
        {filteredCommands.length ? filteredCommands.map((item, index) => { const Icon = item.icon; return <button role="option" aria-selected={activeCommand === index} key={item.name} type="button" onPointerDown={event => event.preventDefault()} onClick={() => chooseCommand(index)} disabled={commandDisabled} aria-disabled={commandDisabled}>
          <Icon size={16}/><span><strong>{item.title}</strong><small>{item.description}</small></span><code>{item.syntax}</code>
        </button>; }) : <p>{capabilities ? "没有匹配的已接通命令" : "正在读取可用命令…"}</p>}
        {busy || currentRunning ? <p>本轮运行中，可以编辑草稿，暂不能执行命令。</p> : null}
      </div> : null}
      <textarea ref={setFocus} aria-autocomplete="list" aria-controls={menuOpen ? `${diagnosticsId}-commands` : undefined} aria-describedby={`${diagnosticsId}-input-help`} aria-label={inputLabel} value={input} disabled={state.loading || !conversation} onChange={(event) => onInput(event.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onKeyDown={keyDown} placeholder={placeholder ?? "想做什么，或想怎么改？"} rows={2} />
      <div className="assistant-conversation-actions">
        <div className="assistant-input-tools">
          {!configured ? <a className="assistant-input-status" href="/settings" aria-label="连接文字模型" title="在设置中连接对话服务"><CircleHelp size={14}/>对话未连接</a> : referenceStatus.pending ? <span className="assistant-input-status" role="status">正在核验引用</span> : null}</div>
        {currentRunning && lastTurnId && !lastTurnId.startsWith("pending-") ? <button className="assistant-stop" type="button" aria-label="停止本轮" title="停止本轮回复；已提交的业务动作不撤销" onClick={() => void state.stop().catch((cause) => setError(cause.message))}><Square aria-hidden="true" size={14}/></button>
          : <button className="assistant-send" type="submit" aria-label={sendLabel} title={sendReason} disabled={sendDisabled}><ArrowUp aria-hidden="true" size={16} /></button>}
      </div>
      <span className="assistant-visually-hidden" id={`${diagnosticsId}-input-help`}>Enter 发送，Shift+Enter 换行；中文组合输入不会发送。{sendDisabled ? sendReason : "输入 / 查找命令。"}</span>
    </form>
      {capabilities && diagnosticsExpanded ? <HomeCardDialog title="对话诊断" onClose={() => setDiagnosticsExpanded(false)}><div className="assistant-capability-diagnostics" id={diagnosticsId}>
        {selectedMaterial?.reference ? <p>引用素材：{selectedMaterial.reference.selection.asset_id} / {selectedMaterial.reference.selection.scope}。</p> : null}
        {selectedKnowledge?.reference ? <p>引用知识：{selectedKnowledge.reference.knowledge_item_id} / {selectedKnowledge.reference.version} / {selectedKnowledge.reference.locator ?? "整条"}。</p> : null}
        {selectedProfile?.reference ? <p>引用档案：{selectedProfile.reference.kind} / {selectedProfile.reference.profile_id} / {selectedProfile.reference.version}（只读引用，不表示用于任务）。</p> : null}
        <p>{capabilities.pi_tui_commands.inventory_source}（命令清单基线 v{capabilities.pi_tui_commands.inventory_version}）：{capabilities.pi_tui_commands.commands.join("、")}</p>
        <p>本机 Pi 隔离版本检查：{capabilities.pi_tui_commands.runtime_version ?? "未确认"}（{capabilities.pi_tui_commands.runtime_status}）；{capabilities.pi_tui_commands.runtime_reason} {capabilities.pi_tui_commands.runtime_check}。{capabilities.pi_tui_commands.runtime_version === capabilities.pi_tui_commands.inventory_version ? "本机版本号与清单基线相同。" : "本机版本未确认或与清单基线不同；命令清单仅适用于所示基线。"}当前产品模式：{capabilities.pi_tui_commands.product_mode}；这些 TUI 命令未接通。</p>
        <p>扩展命令：{capabilities.extension_commands.available ? "可用" : capabilities.extension_commands.reason}；技能：{capabilities.skills.available ? "可用" : capabilities.skills.reason}；模板：{capabilities.prompt_templates.available ? "可用" : capabilities.prompt_templates.reason}</p>
        {Object.entries(capabilities.business_tools).map(([scope, tools]) => <p key={scope}>{scope} 工具：{tools.join("、")}</p>)}
      </div></HomeCardDialog> : null}
    {historyOpen ? <HomeCardDialog title="历史对话" onClose={() => setHistoryOpen(false)}><div className="assistant-resume-list">{state.conversations.map(item => <button type="button" key={item.id} onClick={() => void state.resume(item.id).then(next => { onConversationChange?.(); onInput(next.draft_text); setHistoryOpen(false); focus?.focus(); }).catch(cause => setError(cause.message))}><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleString()}</small></button>)}</div></HomeCardDialog> : null}
    {error || state.error ? <div className="assistant-conversation-error" role="alert"><AssistantFailureMessage message={error ?? state.error ?? ""}/></div> : null}
  </section>;
}
