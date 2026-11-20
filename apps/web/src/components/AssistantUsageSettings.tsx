"use client";

import { useEffect, useId, useState } from "react";
import { listAssistantConversations, listCreationTasks } from "@/lib/api";
import { isLocalAuthMode } from "@/lib/auth/routing";
import type { AssistantConversation, CreationTask } from "@/lib/types";
import type { AssistantConversationScope } from "@/lib/types/assistant";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { AssistantTurnDiagnostics, publicExecutionText } from "./AssistantTurnExecutionRecord";
import { assistantTaskTurnResult } from "./useAssistantConversation";
import { useWorkspaceBoot } from "./WorkspaceBootContext";
import "./AssistantConversation.css";

export function AssistantUsageSettings() {
  const { blocked } = useWorkspaceBoot();
  const { token, error } = useLocalMemberIdentity();
  const [open, setOpen] = useState(false);
  const ready = !blocked && (!isLocalAuthMode() || Boolean(token));
  return <details className="assistant-usage-settings" open={open} onToggle={event => {
    if (event.target === event.currentTarget) setOpen(event.currentTarget.open);
  }}>
    <summary>对话用量与诊断</summary>
    {open ? ready ? <div className="assistant-usage-selection"><UsageSelection key={token ?? "workspace"}/></div> : <p role={error ? "alert" : "status"}>{error ? "当前身份无法核对，请重新确认登录。" : "正在核对当前使用身份。"}</p> : null}
  </details>;
}

function UsageSelection() {
  const selectId = useId();
  const [kind, setKind] = useState<AssistantConversationScope["scope_kind"]>("home");
  return <>
    <label htmlFor={selectId}>对话范围 <select id={selectId} value={kind} onChange={event => setKind(event.target.value as typeof kind)}>
      <option value="home">工作区对话（含草稿）</option>
      <option value="task">作品对话</option>
      <option value="material">素材对话</option>
    </select></label>
    {kind === "task" ? <TaskSelection/> : <ConversationSelection key={kind} kind={kind} scopeId={kind === "home" ? "home" : "library"}/>}
  </>;
}

function TaskSelection() {
  const selectId = useId();
  const [tasks, setTasks] = useState<CreationTask[] | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void listCreationTasks().then(value => { if (active) setTasks(value.items); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  if (error) return <p role="alert">暂时无法读取作品。请切换范围后再试。</p>;
  if (!tasks) return <p role="status">正在读取作品…</p>;
  if (!tasks.length) return <p>当前没有可查看的作品。</p>;
  const task = tasks.find(item => item.id === selected);
  return <>
    <label htmlFor={selectId}>作品 <select id={selectId} value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">选择作品</option>
      <SavedOptions items={tasks} getName={item => item.name}/>
    </select></label>
    {task ? <ConversationSelection key={task.id} kind="task" scopeId={task.id} workspaceId={task.workspace_id}/> : null}
  </>;
}

function ConversationSelection({ kind, scopeId, workspaceId }: {
  kind: AssistantConversationScope["scope_kind"]; scopeId: string; workspaceId?: string;
}) {
  const selectId = useId();
  const [conversations, setConversations] = useState<AssistantConversation[] | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void listAssistantConversations({ scope_kind: kind, scope_id: scopeId }).then(value => {
      if (active) setConversations(value.items.filter(item => item.scope_kind === kind
        && (item.scope_id === scopeId || kind === "material" && scopeId === "library")
        && (!workspaceId || item.workspace_id === workspaceId)));
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [kind, scopeId, workspaceId]);
  if (error) return <p role="alert">暂时无法读取对话。请关闭后再打开诊断。</p>;
  if (!conversations) return <p role="status">正在读取对话…</p>;
  if (!conversations.length) return <p>当前范围没有已保存的对话。</p>;
  const conversation = conversations.find(item => item.id === selected);
  return <>
    <label htmlFor={selectId}>对话 <select id={selectId} value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">选择对话</option>
      <SavedOptions items={conversations} getName={item => item.title}/>
    </select></label>
    {conversation ? <TurnSelection key={conversation.id} conversation={conversation}/> : null}
  </>;
}

function SavedOptions<Item extends {id:string; updated_at:string}>({items, getName}: {
  items: Item[]; getName: (item: Item) => string;
}) {
  const labels = items.map(item => `${getName(item)} · ${new Date(item.updated_at).toLocaleString("zh-CN")}`);
  const counts = new Map<string, number>();
  for (const label of labels) counts.set(label, (counts.get(label) ?? 0) + 1);
  return items.map((item, index) => <option key={item.id} value={item.id}>
    {labels[index]}{(counts.get(labels[index]) ?? 0) > 1 ? ` · ${item.id.slice(-8)}` : ""}
  </option>);
}

function TurnSelection({ conversation }: { conversation: AssistantConversation }) {
  const selectId = useId();
  const [selected, setSelected] = useState("");
  const turns = conversation.turns.filter(turn => turn.sequence > 0);
  const turn = turns.find(item => item.id === selected);
  if (!turns.length) return <p>这段对话没有已保存的轮次。</p>;
  const model = turn ? publicExecutionText(assistantTaskTurnResult(turn)?.response?.model
    ?? (typeof turn.result?.model === "string" ? turn.result.model : null)) : null;
  return <>
    <label htmlFor={selectId}>轮次 <select id={selectId} value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">选择轮次</option>
      {turns.map(item => <option key={item.id} value={item.id}>
        第 {item.sequence} 轮 · {item.user_message.trim().slice(0, 60) || "接续原要求"}
      </option>)}
    </select></label>
    {turn ? <AssistantTurnDiagnostics key={turn.id} turn={turn} conversationId={conversation.id} model={model}
      hasPublicTools={turn.events.some(event => event.type === "tool_start" || event.type === "tool_end")}/> : null}
  </>;
}
