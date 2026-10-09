"use client";

import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getAssistantCapabilities } from "@/lib/api";
import type { AssistantConversationTurn, AssistantStreamEvent } from "@/lib/types";
import type { useAssistantConversation } from "@/components/useAssistantConversation";

type Conversation = ReturnType<typeof useAssistantConversation>;

type TurnBlock =
  | { kind: "text"; text: string }
  | { kind: "tool"; tool_id: number; label: string; state: "running" | "succeeded" | "failed"; summary: string };

function turnBlocks(turn: AssistantConversationTurn): TurnBlock[] {
  const ordered: TurnBlock[] = [];
  for (const event of turn.events) {
    if (event.type === "text_delta") {
      const last = ordered.at(-1);
      if (last?.kind === "text") last.text += event.delta;
      else ordered.push({ kind: "text", text: event.delta });
      continue;
    }
    if (event.type === "tool_start") {
      ordered.push({ kind: "tool", tool_id: event.tool_id, label: event.label, state: "running", summary: "" });
      continue;
    }
    let index = -1;
    for (let candidate = ordered.length - 1; candidate >= 0; candidate -= 1) {
      const item = ordered[candidate];
      if (item.kind === "tool" && item.tool_id === event.tool_id) { index = candidate; break; }
    }
    if (index < 0) {
      ordered.push({ kind: "tool", tool_id: event.tool_id, label: event.label, state: event.ok ? "succeeded" : "failed", summary: event.summary });
    } else {
      const previous = ordered[index];
      if (previous.kind === "tool") ordered[index] = { ...previous, label: event.label, state: event.ok ? "succeeded" : "failed", summary: event.summary };
    }
  }
  if (!ordered.some((item) => item.kind === "text") && turn.assistant_text) ordered.push({ kind: "text", text: turn.assistant_text });
  return ordered;
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
  return <button className="assistant-copy" onClick={() => void copy()} type="button">{copied ? "已复制" : copyError ? "复制未成功" : "复制回复"}</button>;
}

function TurnContent({ turn }: { turn: AssistantConversationTurn }) {
  const blocks = turnBlocks(turn);
  return <div className="assistant-markdown-wrap">
    {blocks.map((block, index) => block.kind === "text"
      ? <div className="assistant-markdown" key={`text-${index}`}><ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown></div>
      : <details className="assistant-turn-tools" key={`tool-${block.tool_id}-${index}`}>
        <summary>{block.label} · {block.state === "running" ? "进行中" : block.state === "succeeded" ? "已完成" : "未完成"}</summary>
        <p>{block.summary || (block.state === "running" ? "等待此操作返回。" : "操作没有返回摘要。")}</p>
      </details>)}
    {turn.assistant_text ? <AssistantText text={turn.assistant_text} /> : null}
  </div>;
}

function TurnView({ turn }: { turn: AssistantConversationTurn }) {
  const result = turn.result;
  const executionStatusLabels: Record<string, string> = {
    queued: "已排队", running: "后台处理中", succeeded: "已完成", failed: "失败",
    cancelled: "已取消", interrupted: "中断等待继续",
  };
  const resultSummary = result?.kind === "plan_preview"
    ? (result.plan_preview as Record<string, unknown> | undefined)?.confirmable === true
      ? "方案已检查，可查看页面中的方案卡后决定是否创建。"
      : "方案检查已返回阻断项；请按页面中的真实原因处理。"
    : result?.kind === "material_result"
      ? `${result.scope === "segment" ? "当前片段" : "整条素材"}标签已回读${result.changed === true ? "，内容有变化。" : result.changed === false ? "，内容没有变化。" : "。"}`
      : result?.kind === "task_turn"
        ? [result.topic_candidates === true ? "已生成选题候选" : null,
          typeof result.script_draft === "string" ? "文案草稿已保存" : null,
          typeof result.execution_action === "string" ? `已提交${result.execution_action === "start" ? "开始制作" : result.execution_action === "resume" ? "继续制作" : "取消制作"}${typeof result.execution_status === "string" ? `（作业${executionStatusLabels[result.execution_status] ?? result.execution_status}）` : ""}` : null,
          result.edit_preview === true ? "已生成待确认修改提案" : null].filter(Boolean).join("；") || "本轮对话完成，未记录领域写入"
        : result?.kind === "failure" && typeof result.message === "string"
          ? result.message
          : result?.kind === "conversation" ? "本轮对话完成，未记录领域写入" : null;
  return <article className="assistant-persisted-turn" data-turn-status={turn.status}>
    <p className="home-composer__command">{turn.user_message}</p>
    <TurnContent turn={turn} />
    {resultSummary ? <details className="assistant-turn-result"><summary>本轮结果</summary><p>{resultSummary}</p></details> : null}
    {turn.status === "failed" ? <p role="status">本轮未完成；可以修改下面的草稿后重新发送。</p> : null}
    {turn.status === "stopped" ? <p role="status">本轮已停止；先前的业务动作不会因此撤销。</p> : null}
  </article>;
}

export function AssistantConversationPanel({
  state, input, onInput, onSend, onConversationChange, busy, configured, suggestions = [], scopeLabel, inputLabel = "对话要求", placeholder, compact = false, autoFocus = false,
}: {
  state: Conversation;
  input: string;
  onInput: (value: string) => void;
  onSend: (text: string) => Promise<void>;
  onConversationChange?: () => void;
  busy: boolean;
  configured: boolean;
  suggestions?: string[];
  scopeLabel: string;
  inputLabel?: string;
  placeholder?: string;
  compact?: boolean;
  autoFocus?: boolean;
}) {
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof getAssistantCapabilities>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [focus, setFocus] = useState<HTMLTextAreaElement | null>(null);
  const conversation = state.conversation;
  const currentRunning = conversation?.turns.at(-1)?.status === "running";
  const lastTurnId = conversation?.turns.at(-1)?.id;
  const turns = conversation?.turns ?? [];
  const commandHelp = useMemo(() => capabilities?.application_commands ?? ["/new", "/name <名称>", "/resume <会话ID>", "/help"], [capabilities]);

  useEffect(() => { void getAssistantCapabilities().then(setCapabilities).catch(() => undefined); }, []);
  useEffect(() => {
    if (autoFocus && !state.loading && conversation) focus?.focus();
  }, [autoFocus, conversation?.id, focus, state.loading]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || state.loading || (busy && !text.startsWith("/"))) return;
    if (text.startsWith("/")) {
      const [command, ...rest] = text.split(/\s+/);
      const value = rest.join(" ").trim();
      try {
        if (command === "/new" && !value) {
          const next = await state.createNew();
          onConversationChange?.();
          onInput(next.draft_text);
          setError(null);
        } else if (command === "/name" && value) {
          const conversationId = conversation?.id;
          const renamed = await state.rename(value);
          if (conversationId && state.isCurrentConversation(conversationId)) onInput(renamed.draft_text);
          setError(null);
        } else if (command === "/resume" && value) {
          const resumed = await state.resume(value);
          onConversationChange?.();
          onInput(resumed.draft_text);
          setError(null);
        } else if (command === "/help" && !value) setError(`可用会话命令：${commandHelp.join(" · ")}`);
        else setError("此命令格式不正确或当前不可用，没有发送给模型。请用 /help 查看已接通的会话操作。");
      } catch (cause) { setError(cause instanceof Error ? cause.message : "会话操作没有完成。"); }
      focus?.focus();
      return;
    }
    if (currentRunning) {
      setError("此对话已有正在运行的轮次。可先停止本轮，或切换到另一段对话；草稿会保留。");
      return;
    }
    setError(null);
    try {
      await onSend(text);
      focus?.focus();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "对话没有完成。"); }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || composing || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    event.preventDefault();
    void submit();
  }

  return <section className={`assistant-conversation-panel${compact ? " is-compact" : ""}`} aria-label={`助手对话：${scopeLabel}`}>
    <header className="assistant-conversation-head">
      <span>{scopeLabel}{conversation?.title && conversation.title !== "新对话" ? ` · ${conversation.title}` : ""}</span>
      <div>
        {conversation ? <button type="button" onClick={() => { void state.createNew().then((next) => { onConversationChange?.(); onInput(next.draft_text); }).catch((cause) => setError(cause instanceof Error ? cause.message : "新对话未能建立。")); }}>新对话</button> : null}
        {state.conversations.length > 1 ? <label>历史
          <select aria-label="恢复此范围的对话" value={conversation?.id ?? ""} onChange={(event) => { if (event.target.value) void state.resume(event.target.value).then((next) => { onConversationChange?.(); onInput(next.draft_text); }).catch((cause) => setError(cause.message)); }}>
            {state.conversations.map((item) => <option key={item.id} value={item.id}>{item.title} · {new Date(item.updated_at).toLocaleString()}</option>)}
          </select>
        </label> : null}
      </div>
    </header>
    <div className="assistant-conversation-history" aria-label="持久对话记录">
      {turns.map((turn) => <TurnView key={turn.id} turn={turn} />)}
      {!turns.length ? <div className="assistant-suggestions"><p>{scopeLabel}。对话和未发送草稿会保存在当前成员的这一范围内。</p>{suggestions.map((suggestion) => <button key={suggestion} type="button" disabled={busy} onClick={() => { onInput(suggestion); focus?.focus(); }}>{suggestion}</button>)}</div> : null}
      {state.loading ? <p role="status">正在恢复对话…</p> : null}
    </div>
    <form className="assistant-conversation-entry" onSubmit={(event) => void submit(event)}>
      <textarea ref={setFocus} aria-label={inputLabel} value={input} disabled={state.loading || !conversation} onChange={(event) => onInput(event.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onKeyDown={keyDown} placeholder={placeholder ?? "写下下一条要求；Enter 发送，Shift+Enter 换行"} rows={compact ? 2 : 3} />
      <div className="assistant-conversation-actions">
        {capabilities ? <>
          <details><summary>对话操作</summary><p>{commandHelp.join("、")}</p></details>
          <details className="assistant-capability-diagnostics"><summary>技术能力状态（诊断）</summary>
            <p>Pi TUI v{capabilities.pi_tui_commands.version}（未接通）：{capabilities.pi_tui_commands.commands.join("、")}</p>
            <p>扩展命令：{capabilities.extension_commands.available ? "可用" : capabilities.extension_commands.reason}；技能：{capabilities.skills.available ? "可用" : capabilities.skills.reason}；模板：{capabilities.prompt_templates.available ? "可用" : capabilities.prompt_templates.reason}</p>
            {Object.entries(capabilities.business_tools).map(([scope, tools]) => <p key={scope}>{scope} 工具：{tools.join("、")}</p>)}
          </details>
        </> : null}
        {currentRunning && lastTurnId && !lastTurnId.startsWith("pending-") ? <button type="button" onClick={() => void state.stop().catch((cause) => setError(cause.message))}>停止本轮</button> : null}
        <button type="submit" disabled={!configured || (busy && !input.trim().startsWith("/")) || state.loading || !input.trim() || (currentRunning && !input.trim().startsWith("/"))}><Send aria-hidden="true" size={14} />{currentRunning && !input.trim().startsWith("/") ? "本轮运行中" : busy && !input.trim().startsWith("/") ? "正在处理" : "发送"}</button>
      </div>
    </form>
    {error || state.error ? <p className="assistant-conversation-error" role="alert">{error ?? state.error}</p> : null}
  </section>;
}
