"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronRight, LoaderCircle } from "lucide-react";
import { getAssistantTurnTrace } from "@/lib/api";
import type { AssistantConversationTurn, AssistantToolFailure } from "@/lib/types";
import type { AssistantTraceEvent, AssistantTurnTrace } from "@/lib/types/assistant";
import { Button } from "./ui/button";

export type TurnTool = {
  id: number;
  label: string;
  state: "running" | "succeeded" | "failed" | "unreturned";
  summary?: string;
  failure?: AssistantToolFailure;
};

export function turnTools(turn: AssistantConversationTurn): TurnTool[] {
  const tools: TurnTool[] = [];
  const positions = new Map<number, number>();
  for (const event of turn.events) {
    if (event.type === "tool_start") {
      positions.set(event.tool_id, tools.length);
      tools.push({ id: event.tool_id, label: event.label, state: turn.status === "running" ? "running" : "unreturned" });
    } else if (event.type === "tool_end") {
      const position = positions.get(event.tool_id) ?? tools.length;
      positions.set(event.tool_id, position);
      tools[position] = { id: event.tool_id, label: event.label, state: event.ok ? "succeeded" : "failed",
        summary: event.summary, failure: event.failure };
    }
  }
  return tools;
}

// Only user-facing tool receipts reach this surface. Keep diagnostic blobs and
// paths out of the DOM even when an older tool returned them as a summary.
export function publicExecutionText(value?: string | null) {
  const text = value?.trim();
  return text && !/(?:https?:\/\/|file:\/\/|Traceback|ECONNREFUSED|ENOTFOUND|\/(?:Users|Volumes|private|tmp|home|var)\/|[a-z]:\\|(?:api[_ -]?key|authorization)\s*[:=]|\bsk-(?:proj-)?[a-z0-9_-]{12,}|^\s*[{[])/i.test(text) ? text : null;
}

export function TurnActivity({ turn, tools, continuation = false }: {
  turn: AssistantConversationTurn; tools: TurnTool[]; continuation?: boolean;
}) {
  if (turn.status !== "running") return null;
  const active = [...tools].reverse().find(tool => tool.state === "running");
  const label = publicExecutionText(active?.label);
  const activity = label ? `正在${label}` : turn.assistant_text.trim() ? "正在回复"
    : continuation ? "继续完成原要求" : turn.sequence === 0 ? "正在发送要求" : "正在等待回复";
  return <p role="status" className="assistant-stream-status"><LoaderCircle aria-hidden size={14} className="is-spinning"/>{activity}</p>;
}

const toolStateLabels = { running: "进行中", succeeded: "已返回", failed: "未完成", unreturned: "未收到结果" };
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
  const label = publicExecutionText(event.label ?? event.tool_name);
  const failure = event.failure_class ? failureLabels[event.failure_class] ?? "有失败记录" : null;
  return <li>
    <span>{eventLabels[event.type] ?? "记录事件"}{label ? `：${label}` : ""}</span>
    <small>{[Number.isFinite(time) && formatTime.format(time), event.status && statusLabels[event.status],
      event.ok === false && "未完成", event.ok === true && "已返回", elapsed(event.duration_ms), failure].filter(Boolean).join("，")}</small>
  </li>;
}

function TurnUsage({ trace, model }: { trace: AssistantTurnTrace; model: string | null }) {
  const usage = trace.provider_usage;
  const source = trace.usage_source ? usageSources[trace.usage_source] : null;
  const counts = usage ? ([
    ["总计", usage.totalTokens], ["输入", usage.input], ["输出", usage.output],
    ["缓存读取", usage.cacheRead], ["缓存写入", usage.cacheWrite],
  ] as const).filter((entry): entry is readonly [typeof entry[0], number] => entry[1] != null) : [];
  return <dl className="assistant-trace-usage" aria-label="本轮用量">
    {model ? <div><dt>模型</dt><dd>{model}</dd></div> : null}
    <div><dt>用量来源</dt><dd>{trace.usage_status === "unknown" ? "未提供" : source ?? "来源未注明"}</dd></div>
    {counts.length ? <div><dt>Token</dt><dd>{counts.map(([label, value]) => <span key={label}>{label} {formatNumber.format(value)}</span>)}</dd></div> : null}
    <div><dt>耗时</dt><dd>{elapsed(trace.duration_ms) ?? "未提供"}</dd></div>
    <div><dt>费用</dt><dd>未提供</dd></div>
  </dl>;
}

export function AssistantTurnExecutionRecord({ turn, tools, model, surfacedFailure, savedOpen = false, onOpenChange }: {
  turn: AssistantConversationTurn; tools: TurnTool[]; model: string | null; surfacedFailure?: AssistantToolFailure; savedOpen?: boolean; onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(savedOpen);
  const [trace, setTrace] = useState<AssistantTurnTrace | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [earlier, setEarlier] = useState<"idle" | "loading" | "error">("idle");
  const loaded = useRef(false);
  const running = turn.status === "running";
  const readingKey = `${turn.id}:trace`;
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
    && (!tools.length || !["tool_start", "tool_end"].includes(event.type))) ?? [];
  return <details className="assistant-trace" data-reading-key={readingKey} open={open}
    onToggle={event => { const next = event.currentTarget.open; setOpen(next); onOpenChange?.(next); }}>
    <summary><ChevronRight aria-hidden size={14}/><span>执行记录</span></summary>
    {open ? <div className="assistant-trace-body">
      {tools.length ? <ol className="assistant-trace-tools" aria-label="操作">
        {tools.map((tool, index) => {
          const label = publicExecutionText(tool.label) ?? "工具调用";
          const inReply = tool.failure && tool.failure === surfacedFailure;
          const detail = inReply ? null : publicExecutionText(tool.failure?.reason ?? tool.summary);
          const impact = publicExecutionText(tool.failure?.impact);
          const repair = inReply ? null : publicExecutionText(tool.failure?.repair_action);
          return <li key={`${tool.id}:${index}`}>
            <div className="assistant-trace-tool-heading"><span>{label}</span><span>{toolStateLabels[tool.state]}</span></div>
            {detail ? <p>{detail}</p> : tool.state === "unreturned" ? <p>此调用没有返回，后台作业是否完成仍待核对。</p> : null}
            {impact && impact !== detail ? <p>{impact}</p> : null}
            {repair && repair !== detail && repair !== impact ? <p>{repair}</p> : null}
          </li>;
        })}
      </ol> : null}
      {running ? (!tools.length ? <p>暂无已返回的操作记录。</p> : null)
        : error ? <div role="alert"><p>执行记录暂时无法读取。</p><Button variant="ghost" type="button" onClick={() => setRetry(value => value + 1)}>重试读取记录</Button></div>
          : trace ? <>
            <TurnUsage trace={trace} model={model}/>
            {events.length || trace.next_before != null || trace.failure_class || trace.retry_count ? <details className="assistant-trace-technical">
              <summary>技术详情</summary>
              {trace.failure_class ? <p>运行记录包含{failureLabels[trace.failure_class] ?? "一次失败"}。</p> : null}
              {trace.retry_count ? <p>内部重试 {trace.retry_count} 次</p> : null}
              {events.length ? <ol className="assistant-trace-events" aria-label="技术事件">
                {events.map((event, index) => <TraceEvent key={event.event_id ?? `${event.type}:${event.step ?? index}`} event={event}/>)}
              </ol> : null}
              {earlier === "error" ? <p role="alert">较早的记录暂时无法读取。</p> : null}
              {trace.next_before != null ? <Button variant="ghost" type="button" disabled={earlier === "loading"} onClick={() => void loadEarlier(trace.next_before!)}>{earlier === "loading" ? "正在读取…" : "加载更早记录"}</Button> : null}
            </details> : null}
          </> : <p role="status">正在读取执行记录…</p>}
    </div> : null}
  </details>;
}
