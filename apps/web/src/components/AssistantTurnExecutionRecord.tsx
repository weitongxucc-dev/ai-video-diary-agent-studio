"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronRight, CircleAlert, LoaderCircle } from "lucide-react";
import { getAssistantTurnTrace } from "@/lib/api";
import type { AssistantConversationTurn, AssistantToolFailure } from "@/lib/types";
import type { AssistantTraceEvent, AssistantTurnTrace } from "@/lib/types/assistant";
import { Button } from "./ui/button";

type TurnTool = {
  id: number;
  label: string;
  target?: string;
  startedAt?: string;
  state: "running" | "succeeded" | "failed" | "unreturned";
  summary?: string;
  failure?: AssistantToolFailure;
};

type TurnBlock =
  | { kind: "text"; key: string; text: string }
  | { kind: "tools"; key: string; tools: TurnTool[] };

export function turnBlocks(turn: AssistantConversationTurn): TurnBlock[] {
  const blocks: TurnBlock[] = [];
  const tools = new Map<number, TurnTool>();
  let text = "";
  let textStart = 0;
  let streamed = "";

  function flushText() {
    if (text.trim()) blocks.push({kind: "text", key: `text:${textStart}`, text});
    text = "";
  }

  for (const [index, event] of turn.events.entries()) {
    if (event.type === "text_delta") {
      if (!text) textStart = index;
      text += event.delta;
      streamed += event.delta;
      continue;
    }
    if (event.type !== "tool_start" && event.type !== "tool_end") continue;
    let tool = tools.get(event.tool_id);
    if (!tool) {
      flushText();
      tool = {id: event.tool_id, label: event.label, state: turn.status === "running" ? "running" : "unreturned"};
      tools.set(tool.id, tool);
      const previous = blocks.at(-1);
      if (previous?.kind === "tools") previous.tools.push(tool);
      else blocks.push({kind: "tools", key: `tools:${tool.id}`, tools: [tool]});
      if (event.type === "tool_start") {
        tool.target = event.display_target;
        tool.startedAt = event.at;
      }
    }
    if (event.type === "tool_end") {
      tool.label ||= event.label;
      tool.state = event.ok ? "succeeded" : "failed";
      tool.summary = event.summary;
      tool.failure = event.failure;
      tool.target ??= event.display_target;
    }
  }

  // The saved body is authoritative, including any suffix added at settlement.
  const persisted = turn.assistant_text.trimEnd();
  if (!streamed.trim() || !persisted.trimStart().startsWith(streamed.trimStart().trimEnd())) {
    const activity: TurnBlock[] = blocks.filter(block => block.kind === "tools");
    if (persisted.trim()) activity.push({kind: "text", key: "text:saved", text: persisted});
    return activity;
  }
  if (persisted.trim() !== streamed.trim()) {
    const suffix = persisted.trimStart().slice(streamed.trim().length);
    if (!text) textStart = turn.events.length;
    text = text.trimEnd() + suffix;
  }
  flushText();
  return blocks;
}

export type TurnReadingState = {
  get: (key: string, fallback: boolean) => boolean;
  save: (key: string, open: boolean) => void;
};

export function hasPrivateExecutionText(text: string) {
  return /\b(?:authorization|api[_ -]?key|(?:access|refresh)[_ -]?token|password|(?:private[_ -]?)?reasoning(?:[_ -]?(?:content|text))?|thinking)\b["']?\s*[:=]|\bbearer\s+[a-z0-9._~-]{8,}|\bsk-(?:proj-)?[a-z0-9_-]{12,}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/i.test(text);
}

// Only user-facing tool receipts reach this surface. Older events can still
// carry transport errors or raw tool responses in a summary.
export function publicExecutionText(value?: string | null) {
  const text = value?.trim();
  return text && !hasPrivateExecutionText(text)
    && !/(?:https?:\/\/|file:\/\/|Traceback|ECONNREFUSED|ENOTFOUND|\/(?:Users|Volumes|private|tmp|home|var)\/|[a-z]:\\|^[{[])/i.test(text) ? text : null;
}

function ElapsedTime({ at }: { at?: string }) {
  const start = at ? Date.parse(at) : NaN;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!Number.isFinite(start)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [start]);
  const seconds = Math.max(0, Math.floor((now - start) / 1000));
  if (!Number.isFinite(seconds)) return null;
  const minutes = Math.floor(seconds / 60);
  return <span className="assistant-activity-time" aria-hidden="true">{minutes ? `${minutes} 分 ` : ""}{seconds % 60} 秒</span>;
}

export function TurnActivity({ turn, continuation }: { turn: AssistantConversationTurn; continuation?: boolean }) {
  if (turn.status !== "running") return null;
  let recent: AssistantConversationTurn["events"][number] | undefined;
  for (let index = turn.events.length - 1; index >= 0; index--) {
    const event = turn.events[index];
    if (event.type === "tool_end" || event.type === "text_delta") {
      recent = event;
      break;
    }
  }
  if (recent?.type === "text_delta" || !recent && turn.assistant_text.trim()) return null;
  const activity = recent?.type === "tool_end" ? "正在处理" : continuation ? "继续完成原要求" : turn.sequence === 0 ? "正在发送要求" : "正在等待回复";
  return <p className="assistant-stream-status">
    <LoaderCircle aria-hidden size={14} className="is-spinning"/>
    <span role="status">{activity}</span><ElapsedTime at={recent?.type === "tool_end" ? recent.at : turn.created_at}/>
  </p>;
}

function toolLabel(tool: TurnTool) {
  return publicExecutionText(tool.label) ?? "工具调用";
}

function completedToolSummary(tools: TurnTool[]) {
  const counts = new Map<string, number>();
  for (const tool of tools) {
    const label = toolLabel(tool);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const representatives = [...counts].slice(0, 2);
  const remaining = tools.length - representatives.reduce((sum, [, count]) => sum + count, 0);
  const labels = representatives.map(([label, count]) => count > 1 ? `${label} ×${count}` : label);
  if (remaining) labels.push(`另 ${remaining} 项`);
  return labels.join(" · ");
}

function ToolName({ tool, running = false }: { tool: TurnTool; running?: boolean }) {
  const label = toolLabel(tool);
  const activeLabel = label === "工具调用" ? "执行" : label;
  const name = running && !activeLabel.startsWith("正在") ? `正在${activeLabel}` : label;
  const target = publicExecutionText(tool.target);
  return <span className="assistant-activity-name">{name}{target && label !== target ? <span className="assistant-activity-target"> · {target}</span> : null}</span>;
}

function CompletedTools({ tools, turnId, anchorId, reading, reply }: {
  tools: TurnTool[]; turnId: string; anchorId: number; reading?: TurnReadingState; reply: string;
}) {
  function readingKey(tool: TurnTool) { return `${turnId}:tool:${tool.id}`; }
  const [open, setOpen] = useState(() => tools.some(tool => reading?.get(readingKey(tool), false)));
  // A parallel call may join this group after the user expands either receipt.
  const expanded = open || tools.some(tool => reading?.get(readingKey(tool), false));
  return <details className="assistant-inline-completed" data-reading-key={`${turnId}:tool:${anchorId}`} open={expanded} onToggle={event => {
    const next = event.currentTarget.open;
    setOpen(next);
    for (const tool of tools) reading?.save(readingKey(tool), next);
  }}>
    <summary><ChevronRight aria-hidden size={14}/>{tools.length === 1 ? <ToolName tool={tools[0]}/> : <span className="assistant-activity-name">{completedToolSummary(tools)}</span>}
      {tools.length === 1 ? <span className="assistant-activity-state">已返回</span> : null}</summary>
    <ol className="assistant-inline-receipts" aria-label="操作回执">{tools.map(tool => {
      const summary = publicExecutionText(tool.summary);
      return <li key={tool.id}>
        {tools.length > 1 ? <ToolName tool={tool}/> : null}
        {summary && !reply.includes(summary) ? <p>{summary}</p> : null}
      </li>;
    })}</ol>
  </details>;
}

export function TurnToolActivity({ tools, turn, reading, surfacedElsewhere = "", recoveryToolId, recovery }: {
  tools: TurnTool[]; turn: AssistantConversationTurn; reading?: TurnReadingState; surfacedElsewhere?: string;
  recoveryToolId?: number; recovery?: ReactNode;
}) {
  const rows: ReactNode[] = [];
  let completed: TurnTool[] = [];
  function addCompleted() {
    if (!completed.length) return;
    const anchor = completed.find(tool => reading?.get(`${turn.id}:tool:${tool.id}`, false)) ?? completed[0];
    rows.push(<CompletedTools key={anchor.id} anchorId={anchor.id} tools={completed} turnId={turn.id} reading={reading} reply={surfacedElsewhere}/>);
    completed = [];
  }
  for (const tool of tools) {
    if (tool.state === "succeeded") {
      completed.push(tool);
      continue;
    }
    addCompleted();
    if (tool.state === "running") {
      rows.push(<div key={tool.id} className="assistant-activity-row">
        <LoaderCircle aria-hidden size={14} className="is-spinning"/>
        <span className="assistant-activity-status" role="status"><ToolName tool={tool} running/></span>
        <ElapsedTime at={tool.startedAt}/>
      </div>);
      continue;
    }
    const reason = tool.state === "unreturned" ? "此调用没有返回，后台作业是否完成仍待核对。"
      : publicExecutionText(tool.failure?.reason) ?? publicExecutionText(tool.summary);
    const notes = [reason, publicExecutionText(tool.failure?.impact), publicExecutionText(tool.failure?.repair_action)]
      .filter((note): note is string => Boolean(note && !surfacedElsewhere.includes(note)));
    rows.push(<div key={tool.id} className="assistant-inline-failure" role={tool.failure && notes.length ? "alert" : undefined}>
      <div className="assistant-activity-row"><CircleAlert aria-hidden size={14}/><ToolName tool={tool}/>
        <span className="assistant-activity-state">{tool.state === "failed" ? "未完成" : "未收到结果"}</span></div>
      {[...new Set(notes)].map(note => <p key={note}>{note}</p>)}
      {tool.id === recoveryToolId ? recovery : null}
    </div>);
  }
  addCompleted();
  return <div className="assistant-inline-activity" role="group" aria-label="本轮操作">{rows}</div>;
}

const usageSources: Record<string, string> = { provider: "供应商实测", local_estimate: "本地估算", compaction: "仅上下文压缩记录" };
const eventLabels: Record<string, string> = {
  turn_start: "开始一轮回复", turn_end: "结束一轮回复", agent_start: "开始执行", agent_end: "结束执行",
  agent_settled: "运行已收尾", tool_start: "开始工具调用", tool_end: "工具调用返回", nested_tool: "业务工具返回",
  business_result: "收到业务回执", retry: "内部重试", context_compaction: "上下文压缩",
  compaction_start: "开始上下文压缩", compaction_end: "结束上下文压缩",
};
const statusLabels: Record<string, string> = {
  started: "已开始", running: "进行中", succeeded: "已完成", completed: "已完成", failed: "未完成",
  cancelled: "已取消", rejected: "已拒绝", stopped: "已停止", unknown: "待核实",
};
const failureLabels: Record<string, string> = {
  business_tool_failed: "业务工具失败", tool_failed: "工具调用失败", provider_error: "模型服务失败", timeout: "运行超时",
};
const formatNumber = new Intl.NumberFormat("zh-CN");
const formatTime = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const elapsed = (ms?: number | null) => ms == null ? null : ms < 1000 ? `${formatNumber.format(ms)} 毫秒`
  : `${formatNumber.format(Math.round(ms / 10) / 100)} 秒`;

function TraceEvent({ event }: { event: AssistantTraceEvent }) {
  const time = event.at ? Date.parse(event.at) : NaN;
  const label = publicExecutionText(event.label);
  const failure = event.failure_class ? failureLabels[event.failure_class] ?? "有失败记录" : null;
  return <li>
    <span>{eventLabels[event.type] ?? "记录事件"}{label ? `：${label}` : ""}</span>
    <small>{[Number.isFinite(time) && formatTime.format(time), event.status && statusLabels[event.status],
      event.ok === false && "未完成", event.ok === true && "已返回", elapsed(event.duration_ms), failure].filter(Boolean).join("，")}</small>
  </li>;
}

function TurnUsage({ trace, model }: { trace: AssistantTurnTrace; model: string | null }) {
  const usage = trace.usage_status === "unknown" ? null : trace.provider_usage;
  const source = trace.usage_source ? usageSources[trace.usage_source] : null;
  const counts = usage ? ([
    ["总计", usage.totalTokens], ["输入", usage.input], ["输出", usage.output],
    ["缓存读取", usage.cacheRead], ["缓存写入", usage.cacheWrite],
  ] as const).filter((entry): entry is readonly [typeof entry[0], number] => entry[1] != null) : [];
  return <dl className="assistant-trace-usage" aria-label="本轮用量">
    {model ? <div><dt>模型</dt><dd>{model}</dd></div> : null}
    <div><dt>用量来源</dt><dd>{trace.usage_status === "unknown" ? "未提供" : source ?? (trace.usage_status === "estimated" ? "估算，来源未注明" : "来源未注明")}</dd></div>
    {counts.length ? <div><dt>Token</dt><dd>{counts.map(([label, value]) => <span key={label}>{label} {formatNumber.format(value)}</span>)}</dd></div> : null}
    <div><dt>耗时</dt><dd>{elapsed(trace.duration_ms) ?? "未提供"}</dd></div>
    <div><dt>费用</dt><dd>未提供</dd></div>
  </dl>;
}

export function AssistantTurnExecutionRecord({ turn, hasPublicTools, model, reading }: {
  turn: AssistantConversationTurn; hasPublicTools: boolean; model: string | null; reading?: TurnReadingState;
}) {
  const readingKey = `${turn.id}:trace`;
  const [open, setOpen] = useState(() => reading?.get(readingKey, false) ?? false);
  const [trace, setTrace] = useState<AssistantTurnTrace | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [earlier, setEarlier] = useState<"idle" | "loading" | "error">("idle");
  const loaded = useRef(false);
  const running = turn.status === "running";
  useEffect(() => {
    if (!open || running || loaded.current) return;
    let active = true;
    setError(false);
    void getAssistantTurnTrace(turn.id).then(value => {
      if (value.turn_id !== turn.id) throw new Error("Unexpected turn trace");
      if (active) { loaded.current = true; setTrace(value); }
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [open, retry, running, turn.id]);

  async function loadEarlier(before: number) {
    if (earlier === "loading") return;
    setEarlier("loading");
    try {
      const value = await getAssistantTurnTrace(turn.id, before);
      if (value.turn_id !== turn.id) throw new Error("Unexpected turn trace");
      setTrace(current => current ? { ...value, events: [...value.events, ...current.events] } : value);
      setEarlier("idle");
    } catch { setEarlier("error"); }
  }

  const events = trace?.events.filter(event => Object.hasOwn(eventLabels, event.type)
    && (!hasPublicTools || !["tool_start", "tool_end", "nested_tool"].includes(event.type))) ?? [];
  return <details className="assistant-trace" data-reading-key={readingKey} open={open}
    onToggle={event => { const next = event.currentTarget.open; setOpen(next); reading?.save(readingKey, next); }}>
    <summary><ChevronRight aria-hidden size={14}/>用量与诊断</summary>
    {open ? <div className="assistant-trace-body">
      {running ? <p>本轮结束后可查看用量和技术详情。</p>
        : error ? <div role="alert"><p>执行记录暂时无法读取。</p><Button variant="ghost" type="button" onClick={() => setRetry(value => value + 1)}>重试读取记录</Button></div>
          : trace ? <>
            <TurnUsage trace={trace} model={model}/>
            {trace.failure_class ? <p>运行记录包含{failureLabels[trace.failure_class] ?? "一次失败"}。</p> : null}
            {trace.retry_count ? <p>内部重试 {trace.retry_count} 次</p> : null}
            {events.length ? <ol className="assistant-trace-events" aria-label="技术事件">
              {events.map((event, index) => <TraceEvent key={event.event_id ?? `${event.type}:${event.step ?? index}`} event={event}/>)}
            </ol> : null}
            {earlier === "error" ? <p role="alert">较早的记录暂时无法读取。</p> : null}
            {trace.next_before != null ? <Button variant="ghost" type="button" disabled={earlier === "loading"} onClick={() => void loadEarlier(trace.next_before!)}>{earlier === "loading" ? "正在读取…" : "加载更早记录"}</Button> : null}
          </> : <p role="status">正在读取执行记录…</p>}
    </div> : null}
  </details>;
}
