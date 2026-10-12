"use client";

import { useEffect, useRef, useState } from "react";
import type { CreationRequest } from "@/lib/types";
import { confirmCreationRequestWindow, getCreationRequest, getCreationRequestItems, prepareCreationRequestWindow, replanCreationRequestWindow, updateCreationRequest, type CreationRequestItems } from "@/lib/creation-request-api";
import { listVoiceProfiles, mediaProfilesErrorMessage, type VoiceProfile } from "@/lib/media-profiles-api";
import { AppSelect, AppSelectOption } from "./AppSelect";
import { creationTaskShortStatus } from "@/lib/creation-task-status";
import { PreflightFacts } from "./CreateProductionTaskForm";
import { AssistantFailureMessage } from "./AssistantConversationPanel";
import ui from "./CreationRequestPanel.module.css";

const relations = [
  { value: "different_topics", label: "不同主题" },
  { value: "variants", label: "同主题不同版本" },
  { value: "series", label: "系列分集" },
] as const;
const voiceKey = (voice: { id: string; version: string }) => JSON.stringify([voice.id, voice.version]);
const voiceLanguages = { zh: "中文", en: "英文", "zh-en": "中英混合" };
const requestStates = { awaiting_clarification: "需要补充条件", awaiting_plan: "待规划", awaiting_confirmation: "方案待确认", active: "已建立任务" };
const taskStates = { created: "待开始", running: "处理中", waiting_for_user: "等待确认", completed: "已完成", failed: "需要处理" };

export function CreationRequestPanel({ requestId, scopeKey, isCurrentScope, goalsDraft = "", onGoalsDraftChange }: {
  requestId: string; scopeKey: string; isCurrentScope: () => boolean; goalsDraft?: string; onGoalsDraftChange?: (text: string) => void;
}) {
  const identity = `${scopeKey}:${requestId}`;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const scopeCheck = useRef(isCurrentScope);
  scopeCheck.current = isCurrentScope;
  const sequence = useRef(0);
  const [loaded, setLoaded] = useState<{ identity: string; value: CreationRequest } | null>(null);
  const value = loaded?.identity === identity ? loaded.value : null;
  const [items, setItems] = useState<CreationRequestItems | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [goalsText, setGoalsText] = useState(goalsDraft);
  const [voices, setVoices] = useState<{ identity: string; items: VoiceProfile[]; loading: boolean; error: string | null } | null>(null);
  const [voiceSelection, setVoiceSelection] = useState("");
  const [voiceRead, setVoiceRead] = useState(0);
  const [recheckGoals, setRecheckGoals] = useState<{ identity: string; revision: number; goals: { goal: string; name?: string }[] } | null>(null);
  function changeGoals(text: string) { setGoalsText(text); onGoalsDraftChange?.(text); }

  async function load() {
    if (!scopeCheck.current()) return;
    const operation = ++sequence.current;
    const current = () => operation === sequence.current && identity === currentIdentity.current && scopeCheck.current();
    setPending(true); setError(null);
    try {
      const next = await getCreationRequest(requestId);
      if (!current()) return;
      setLoaded({ identity, value: next });
      const page = await getCreationRequestItems(requestId);
      if (current()) setItems(page);
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : "创作目标读取失败，请重新读取。");
    } finally { if (current()) setPending(false); }
  }
  useEffect(() => {
    setLoaded(null); setItems(null); setGoalsText(goalsDraft); setVoices(null); setVoiceSelection(""); setRecheckGoals(null);
    void load();
    return () => { sequence.current += 1; };
  }, [identity]);

  async function mutate(action: () => Promise<CreationRequest>, clearGoals = false) {
    if (pending || !value || !scopeCheck.current()) return;
    const operation = ++sequence.current;
    const current = () => operation === sequence.current && identity === currentIdentity.current && scopeCheck.current();
    setPending(true); setError(null);
    try {
      const next = await action();
      if (!current()) return;
      setLoaded({ identity, value: next });
      if (clearGoals) changeGoals("");
      const page = await getCreationRequestItems(requestId);
      if (current()) setItems(page);
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : "操作没有完成，请重新读取当前状态后再试。");
    } finally { if (current()) setPending(false); }
  }
  async function readPage(after: number) {
    if (pending || !scopeCheck.current()) return;
    const operation = ++sequence.current;
    const current = () => operation === sequence.current && identity === currentIdentity.current && scopeCheck.current();
    setPending(true); setError(null);
    try { const page = await getCreationRequestItems(requestId, after); if (current()) setItems(page); }
    catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : "任务列表读取失败，请重试。"); }
    finally { if (current()) setPending(false); }
  }

  const window = value?.current_window;
  const canChooseVoice = window?.status === "proposed" && value?.confirmed_count === 0;
  const boundVoice = value?.requirements.selected_voice;
  const boundVoiceKey = boundVoice ? voiceKey({ id: boundVoice.profile_id, version: boundVoice.version }) : "";
  useEffect(() => {
    if (!canChooseVoice || !scopeCheck.current()) return;
    let active = true;
    const current = () => active && identity === currentIdentity.current && scopeCheck.current();
    setVoices({ identity, items: [], loading: true, error: null }); setVoiceSelection("");
    void listVoiceProfiles().then(result => {
      if (!current()) return;
      const items = result.items.filter(voice => voice.runnable);
      setVoices({ identity, items, loading: false, error: null });
      setVoiceSelection(items.some(voice => voiceKey(voice) === boundVoiceKey) ? boundVoiceKey : "");
    }).catch(cause => {
      if (current()) setVoices({ identity, items: [], loading: false, error: mediaProfilesErrorMessage(cause) });
    });
    return () => { active = false; };
  }, [identity, canChooseVoice, boundVoiceKey, voiceRead]);
  const voiceChoices = voices?.identity === identity ? voices : null;
  const chosenVoice = voiceChoices?.items.find(voice => voiceKey(voice) === voiceSelection);
  const retryGoals = recheckGoals?.identity === identity && recheckGoals.revision === value?.revision ? recheckGoals.goals : null;

  async function saveVoiceAndRecheck() {
    if (pending || !value || !window || !canChooseVoice || !chosenVoice || !scopeCheck.current()) return;
    const goals = window.entries.map(entry => ({ goal: entry.plan!.goal, name: entry.plan!.name ?? undefined }));
    const operation = ++sequence.current;
    const current = () => operation === sequence.current && identity === currentIdentity.current && scopeCheck.current();
    setPending(true); setError(null);
    let saved = false; let prepared = false;
    try {
      const updated = await updateCreationRequest(requestId, {
        expected_revision: value.revision, output_relation: value.output_relation,
        requirements: { ...value.requirements, selected_voice: { profile_id: chosenVoice.id, version: chosenVoice.version } },
      });
      if (!current()) return;
      saved = true;
      setLoaded({ identity, value: updated }); setItems(null);
      setRecheckGoals({ identity, revision: updated.revision, goals });
      const next = await prepareCreationRequestWindow(requestId, { expected_revision: updated.revision, goals });
      if (!current()) return;
      prepared = true; setLoaded({ identity, value: next }); setRecheckGoals(null);
      const page = await getCreationRequestItems(requestId);
      if (current()) setItems(page);
    } catch (cause) {
      if (current()) setError((saved && !prepared ? "声音已保存，本组方案尚未重新核对。" : "") + (cause instanceof Error ? cause.message : "请重新读取当前状态后再试。"));
    } finally { if (current()) setPending(false); }
  }
  const remaining = value ? value.target_count - value.expanded_count : 0;
  const single = value?.target_count === 1 && value.output_relation === "single";
  const goals = single && value ? [value.goal] : goalsText.split("\n").map(line => line.trim()).filter(Boolean);
  const limit = Math.min(8, remaining);
  const validGoals = goals.length > 0 && goals.length <= limit && new Set(goals).size === goals.length;
  const tasks = items?.items.filter(item => item.task !== null) ?? [];

  return <section className={ui.panel} aria-label="创作目标">
    {!value ? <p role="status">{pending ? "正在读取创作目标…" : "尚未读取当前创作目标。"}</p> : <>
      <div className={ui.heading}><strong>{value.goal}</strong><p className={ui.summary}>目标 {value.target_count.toLocaleString()} 条视频 · 已建 {value.confirmed_count.toLocaleString()} 条任务</p><p className={ui.note}>{requestStates[value.status]}</p><p className={ui.note}>每条 {value.requirements.duration_target_seconds} 秒 · {value.requirements.aspect_ratio}</p>{value.confirmed_count > 0 ? <p className={ui.note}>{Object.entries(taskStates).filter(([status]) => value.task_status_counts[status] > 0).map(([status, label]) => `${label} ${value.task_status_counts[status]} 条`).join(" · ")}</p> : null}</div>
      {value.clarification_questions.length ? <section className={ui.section}><h3>需要补充</h3><ul className={ui.questions}>{value.clarification_questions.map((question, index) => <li key={index}>{question}</li>)}</ul>{value.material_scope.kind === "today_received" ? <><p className={ui.note}>来源范围需要核实；也可以明确选择当前素材库中的可用素材。</p><button className={ui.button} type="button" disabled={pending || value.output_relation === null} onClick={() => void mutate(() => updateCreationRequest(requestId, { expected_revision: value.revision, output_relation: value.output_relation, material_scope: "workspace_library" }))}>使用当前素材库</button></> : null}</section> : null}
      {value.target_count > 1 ? <section className={ui.section}><h3>这些视频的关系</h3>{value.confirmed_count > 0 ? <p>{relations.find(relation => relation.value === value.output_relation)?.label}</p> : <div className={ui.choices}>{relations.map(relation => <button className={ui.button} key={relation.value} type="button" aria-pressed={value.output_relation === relation.value} disabled={pending || value.output_relation === relation.value} onClick={() => void mutate(() => updateCreationRequest(requestId, { expected_revision: value.revision, output_relation: relation.value }))}>{relation.label}</button>)}</div>}</section> : null}
      {canChooseVoice ? <section className={ui.section} aria-label="本请求声音"><h3>本请求的声音</h3>
        <p className={ui.note}>选择已登记的可用声音版本，保存后重新核对本组方案。</p>
        <button className={ui.button} type="button" disabled={pending || voiceChoices?.loading} onClick={() => setVoiceRead(value => value + 1)}>重新读取声音</button>
        {voiceChoices?.loading ? <p role="status">正在读取可用声音…</p> : voiceChoices?.error ? <p role="alert">{voiceChoices.error}</p> : voiceChoices?.items.length ? <>
          <label><span>声音版本</span><AppSelect aria-label="声音版本" value={voiceSelection} disabled={pending} onValueChange={setVoiceSelection}>
            <AppSelectOption value="">选择可用声音</AppSelectOption>
            {voiceChoices.items.map(voice => <AppSelectOption key={voiceKey(voice)} value={voiceKey(voice)}>{voice.display_name || "已保存声音"} · {voice.version} · {voiceLanguages[voice.language]}</AppSelectOption>)}
          </AppSelect></label>
          <button className={ui.button} type="button" disabled={pending || !chosenVoice || voiceSelection === boundVoiceKey || window?.entries.some(entry => !entry.plan)} onClick={() => void saveVoiceAndRecheck()}>保存声音并重新核对</button>
        </> : <p>当前没有可用声音版本。<a href="/profiles">前往人物与声音</a></p>}
      </section> : null}
      {retryGoals ? <section className={ui.section}><p>声音已保存，请重新核对本组方案后再确认任务。</p>
        <button className={ui.button} type="button" disabled={pending} onClick={() => void mutate(async () => {
          const next = await prepareCreationRequestWindow(requestId, { expected_revision: value.revision, goals: retryGoals });
          setRecheckGoals(previous => previous?.identity === identity ? null : previous); return next;
        })}>重新核对已保存声音的方案</button></section> : null}
      {window?.status === "proposed" ? <section className={ui.section} aria-label="本组制作方案"><h3>本组 {window.entries.length} 条方案</h3><div className={ui.entries}>{window.entries.map(entry => <article className={ui.entry} key={entry.ordinal}><strong>第 {entry.ordinal} 条 · {entry.plan?.name}</strong>{entry.plan ? <><p>{entry.plan.goal}</p><p className={ui.note}>{entry.plan.aspect_ratio} · {entry.plan.duration_target_seconds} 秒</p>{entry.plan.blockers.map((blocker, index) => <p key={index}>{blocker.impact} {blocker.repair_action}</p>)}<details><summary>查看可用内容与制作依据</summary><PreflightFacts preview={entry.plan} /></details></> : null}</article>)}</div><button className={ui.primary} type="button" disabled={pending || !window.entries.length || window.entries.some(entry => !entry.plan?.confirmable)} onClick={() => void mutate(() => confirmCreationRequestWindow(requestId, window.id, { expected_revision: value.revision, input_fingerprint: window.input_fingerprint }))}>确认创建本组任务</button><button className={ui.button} type="button" disabled={pending} onClick={() => void mutate(() => replanCreationRequestWindow(requestId, window.id, value.revision))}>重新核对本组方案</button></section> : remaining > 0 && value.status !== "awaiting_clarification" && value.output_relation !== null ? <section className={ui.section}><h3>{value.confirmed_count ? "准备下一组方案" : "准备方案"}</h3>{!single ? <><label htmlFor={`request-goals-${requestId}`}>每行写一条视频的具体目标</label><textarea className={ui.goals} id={`request-goals-${requestId}`} value={goalsText} onChange={event => changeGoals(event.target.value)} disabled={pending} /><p className={ui.note}>本次可准备 1–{limit} 条；分别写清主题、版本差异或分集内容。</p>{goals.length > limit ? <p role="alert">本次最多准备 {limit} 条，请减少本组目标。</p> : new Set(goals).size !== goals.length ? <p role="alert">请分别写出每条视频的具体目标。</p> : null}</> : null}<button className={ui.primary} type="button" disabled={pending || !validGoals} onClick={() => void mutate(() => prepareCreationRequestWindow(requestId, { expected_revision: value.revision, goals: goals.map(goal => ({ goal })) }), true)}>准备本组方案</button></section> : null}
      {tasks.length || items?.has_more ? <section className={ui.section} aria-label="已创建的任务"><h3>已创建的任务</h3><div className={ui.entries}>{tasks.map(entry => <article className={ui.entry} key={entry.ordinal}><a href={`/tasks/${encodeURIComponent(entry.task!.id)}`}>第 {entry.ordinal} 条 · {entry.task!.name}</a><p className={ui.note}>{creationTaskShortStatus(entry.task!.stage_status, entry.task!.stage)}</p></article>)}</div><div className={ui.actions}><button className={ui.button} disabled={pending} type="button" onClick={() => void readPage(0)}>返回首组任务</button>{items?.has_more && items.next_after_ordinal !== null ? <button className={ui.button} disabled={pending} type="button" onClick={() => void readPage(items.next_after_ordinal!)}>查看下一组任务</button> : null}</div></section> : null}
    </>}
    {error ? <div className={ui.error} role="alert"><AssistantFailureMessage message={error} /></div> : null}
    <div className={ui.actions}><button className={ui.button} disabled={pending} type="button" onClick={() => void load()}>{pending ? "读取中…" : "刷新当前状态"}</button></div>
  </section>;
}
