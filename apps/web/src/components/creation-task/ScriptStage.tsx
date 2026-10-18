"use client";
import { useWorkspaceViewState } from "../workbench/context";
import { Tabs } from "@base-ui/react/tabs";
import Link from "next/link";
import { Check, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { transferAssistantReplyToScript, getAssistantScriptContext, saveAssistantScriptDraft, saveScriptLibraryEntry, getCreationPlan } from "@/lib/api";
import type { CreationTask, CreationTaskScript, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
export function ConfirmedScriptView({ script }: {
    script: CreationTaskScript;
}) {
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><h2>{script.title}</h2><span className={styles.artifactStatus}>{script.selection_status === "confirmed" ? "已确认" : "待确认草稿"}</span></div>
      <div className={styles.scriptSections}>{script.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><p className={styles.scriptCopy}>{section.content}</p><p><strong>画面建议：</strong>{section.visual_prompt}</p><details className={styles.traceDetails}><summary>来源证据</summary><p>{section.evidence_reference_ids.join("、") || "未单独标注"}</p></details></article>)}</div>
    </section>);
}
export function ReadonlyScriptsView({ scripts, confirmedScriptId }: {
    scripts: WorkspaceState["scripts"];
    confirmedScriptId: string | null;
}) {
    const latest = scripts[0];
    if (!latest)
        return null;
    const confirmed = scripts.find(script => script.id === confirmedScriptId && script.selection_status === "confirmed");
    if (!confirmed || confirmed.id === latest.id || latest.selection_status !== "draft")
        return <ConfirmedScriptView script={latest}/>;
    return <Tabs.Root defaultValue="confirmed" className={styles.scriptReview}>
    <Tabs.List className={styles.reviewTabs} aria-label="文案版本"><Tabs.Tab value="confirmed">当前确认稿</Tabs.Tab><Tabs.Tab value="draft">新草稿</Tabs.Tab></Tabs.List>
    <Tabs.Panel value="confirmed" keepMounted><ConfirmedScriptView script={confirmed}/></Tabs.Panel>
    <Tabs.Panel value="draft" keepMounted><ConfirmedScriptView script={latest}/></Tabs.Panel>
  </Tabs.Root>;
}
export function ScriptLibrarySave({ script }: {
    script: CreationTaskScript;
}) {
    const [name, setName] = useState(script.title);
    const [category, setCategory] = useState("");
    const [saving, setSaving] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    const [failed, setFailed] = useState(false);
    const [savedEntry, setSavedEntry] = useState<{
        id: string;
        source_task_id: string;
        version: number;
    } | null>(null);
    async function save() {
        setSaving(true);
        setFeedback(null);
        setFailed(false);
        try {
            const entry = await saveScriptLibraryEntry({ source_script_id: script.id, name, category });
            setSavedEntry(entry);
            setFeedback(`已保存「${entry.name}」版本 ${entry.version}；原文案与成片不变。`);
        }
        catch (cause) {
            setFailed(true);
            setFeedback(cause instanceof Error ? cause.message : "文案保存失败");
        }
        finally {
            setSaving(false);
        }
    }
    return <section className={styles.workSection} aria-label="保存到文案库">
    <h3>保存为可复用文案</h3>
    <label className={styles.field}><span>文案名称</span><input value={name} onChange={event => setName(event.target.value)}/></label>
    <label className={styles.field}><span>文案分类</span><input placeholder="例如产品介绍" value={category} onChange={event => setCategory(event.target.value)}/></label>
    <button className="modern-primary-button" disabled={saving || !name.trim()} onClick={() => void save()} type="button">{saving ? "正在保存…" : "存入文案库"}</button>
    <Link href={savedEntry ? `/knowledge?entry_id=${encodeURIComponent(savedEntry.id)}&source_task_id=${encodeURIComponent(savedEntry.source_task_id)}` : "/knowledge"}>{savedEntry ? `查看该条目 · 版本 ${savedEntry.version}` : "查看文案库"}</Link>
    <p>同一原任务以同名再次保存会追加历史版本；复用的新稿仍需重新确认。</p>
    {feedback ? <p role={failed ? "alert" : "status"}>{feedback}</p> : null}
  </section>;
}
export function PlanReplyDraftAction({ task, busy = false, onComplete }: {
    task: CreationTask;
    busy?: boolean;
    onComplete: () => Promise<void>;
}) {
    const [origin, setOrigin] = useState<{
        conversation_id: string;
        turn_id: string;
        request_id: string;
    } | null>(null);
    const [loading, setLoading] = useState(true);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        let active = true;
        void getAssistantScriptContext(task.id).then((context) => {
            if (active)
                setOrigin(context.reply_origin);
        }).catch((cause) => {
            if (active)
                setError(cause instanceof Error ? cause.message : "无法读取回复来源。");
        }).finally(() => { if (active)
            setLoading(false); });
        return () => { active = false; };
    }, [task.id]);
    async function transfer() {
        if (!origin || pending || busy)
            return;
        setPending(true);
        setError(null);
        try {
            const key = `ai-video-diary.plan-reply-transfer.v1:${task.id}:${origin.request_id}`;
            const saved = window.sessionStorage.getItem(key);
            const requestId = saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(saved)
                ? saved : crypto.randomUUID();
            if (!saved)
                window.sessionStorage.setItem(key, requestId);
            const plan = await getCreationPlan(task.plan_id);
            await transferAssistantReplyToScript(task.id, {
                conversation_id: origin.conversation_id, turn_id: origin.turn_id,
                request_id: requestId, plan_conversion_request_id: origin.request_id,
                expected_plan_updated_at: plan.updated_at, expected_task_updated_at: task.updated_at,
            });
            await onComplete();
        }
        catch (cause) {
            setError(cause instanceof Error ? cause.message : "回复草稿没有保存成功，请重新检查后重试。");
        }
        finally {
            setPending(false);
        }
    }
    if (loading || !origin)
        return error ? <p role="alert">{error}</p> : null;
    return <section className={styles.workSection} aria-label="已选择的首页回复">
    <h3>已选择的对话回复</h3>
    <p>保存为待确认文案草稿后，仍需逐段补充当前资料引用和画面建议；不会跳过选题确认。</p>
    <button className={styles.secondaryButton} disabled={pending || busy} onClick={() => void transfer()} type="button">{pending ? "正在核对并保存…" : "保存这条回复为文案草稿"}</button>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
export function ScriptEditor({ taskId, script, busy, repairing, onConfirm }: {
    taskId: string;
    script: CreationTaskScript;
    busy: boolean;
    repairing: boolean;
    onConfirm: (script: CreationTaskScript) => void;
}) {
    const [draft, setDraft] = useWorkspaceViewState(`script:${script.id}:${script.updated_at}`+".draft", script);
    const [sources, setSources] = useState<{
        reference_id: string;
        title: string;
        text: string;
    }[]>([]);
    const [savingDraft, setSavingDraft] = useState(false);
    const [draftError, setDraftError] = useState<string | null>(null);
    useEffect(() => {
        let active = true;
        void getAssistantScriptContext(taskId).then((context) => { if (active)
            setSources(context.sources); })
            .catch((cause) => { if (active)
            setDraftError(cause instanceof Error ? cause.message : "无法读取当前任务资料。"); });
        return () => { active = false; };
    }, [taskId]);
    async function saveDraft() {
        const submittedDraft = draft;
        setSavingDraft(true);
        setDraftError(null);
        try {
            const saved = await saveAssistantScriptDraft(taskId, {
                title: submittedDraft.title,
                sections: submittedDraft.sections,
                expected_updated_at: submittedDraft.updated_at,
            });
            setDraft((current) => current === submittedDraft
                ? saved
                : { ...current, updated_at: saved.updated_at });
        }
        catch (cause) {
            setDraftError(cause instanceof Error ? cause.message : "草稿没有保存成功；当前编辑内容仍保留在本页。");
        }
        finally {
            setSavingDraft(false);
        }
    }
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>{repairing ? "修订文案" : "检查文案"}</h2><p>确认后使用这份正文和画面建议制作。</p></div></div>
      {script.reused_from_entry_id ? <p>从文案库复用的新稿，尚未确认；没有调用模型或复制旧成片。<Link href="/knowledge">查看文案来源</Link></p> : null}
      {script.source_reply_reference ? <p>来自已完成对话回复。当前内容是待确认草稿；来源和画面建议需要你逐段核对补充。</p> : null}
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })}/></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea aria-label={`第 ${index + 1} 段正文`} rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })}/><label className={styles.field}><span>画面建议</span><textarea aria-label={`第 ${index + 1} 段画面建议`} rows={2} value={section.visual_prompt} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, visual_prompt: event.target.value } : item) })}/></label><fieldset className={styles.scriptSourceChoices}><legend>选择这段使用的当前资料</legend>{sources.length ? sources.map((source) => <label key={source.reference_id}><input type="checkbox" checked={section.evidence_reference_ids.includes(source.reference_id)} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, evidence_reference_ids: event.target.checked ? [...item.evidence_reference_ids, source.reference_id] : item.evidence_reference_ids.filter((id) => id !== source.reference_id) } : item) })}/>{source.title}</label>) : <p>当前任务没有可选资料；请先补充并确认有效来源。</p>}</fieldset></article>)}</div>
      {draftError ? <p role="alert">{draftError}</p> : null}
      {script.selection_status === "draft" ? <button className={styles.secondaryButton} disabled={busy || savingDraft || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => void saveDraft()} type="button">{savingDraft ? "正在保存草稿…" : "保存草稿"}</button> : null}
      <div className={styles.primaryAction}><button className="modern-primary-button" disabled={busy || savingDraft || !draft.title.trim() || draft.sections.some((section) => !section.content.trim() || !section.visual_prompt.trim() || !section.evidence_reference_ids.length)} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}{repairing ? "确认修订文案" : "确认文案"}</button></div>
    </section>);
}
