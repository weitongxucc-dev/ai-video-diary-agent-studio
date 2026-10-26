"use client";
import { useWorkspaceViewState } from "../workbench/context";
import { Tabs } from "@base-ui/react/tabs";
import Link from "next/link";
import { Check, LoaderCircle } from "lucide-react";
import { Button } from "../ui/button";
import { useEffect, useRef, useState } from "react";
import { transferAssistantReplyToScript, getAssistantScriptContext, saveAssistantScriptDraft, saveScriptLibraryEntry, getCreationPlan, getCreationTaskScriptSections } from "@/lib/api";
import type { CreationTask, CreationTaskScript, CreationTaskScriptSectionWindow, CreationTaskWorkspace as WorkspaceState } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
import { ScriptWindowEditor } from "./ScriptWindowEditor";
import { getScriptLibraryRequest, isScriptLibraryWriteRejected } from "@/lib/api/script-library";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
export function ConfirmedScriptView({ script }: {
    script: CreationTaskScript;
}) {
    return <ScriptSectionReader key={`${script.task_id}:${script.id}:${script.updated_at}`} script={script}/>;
}
function ScriptSectionReader({ script }: { script: CreationTaskScript }) {
    const [cursors, setCursors] = useState([0]);
    const [reload, setReload] = useState(0);
    const revision = useRef<string | undefined>(undefined);
    const [read, setRead] = useState<
        { status: "loading" } | { status: "failed"; message: string }
        | { status: "ready"; page: CreationTaskScriptSectionWindow }
    >({ status: "loading" });
    const after = cursors[cursors.length - 1];
    useEffect(() => {
        let active = true;
        setRead({ status: "loading" });
        void getCreationTaskScriptSections(script.task_id, script.id, {
            after_ordinal: after, expected_updated_at: revision.current,
        }).then((page) => {
            if (!active) return;
            revision.current = page.updated_at;
            setRead({ status: "ready", page });
        }).catch((cause) => {
            if (active) setRead({ status: "failed", message: cause instanceof Error ? cause.message : "文案段落没有读回。" });
        });
        return () => { active = false; };
    }, [script.task_id, script.id, after, reload]);
    function restart() {
        revision.current = undefined;
        setCursors([0]);
        setReload((value) => value + 1);
    }
    const page = read.status === "ready" ? read.page : null;
    return <section className={styles.workSection}>
        <div className={styles.sectionHeading}><h2>{page?.title ?? script.title}</h2><span className={styles.artifactStatus}>{(page?.selection_status ?? script.selection_status) === "confirmed" ? "已确认" : "待确认草稿"}</span></div>
        {read.status === "loading" ? <p role="status">正在读取文案段落…</p> : null}
        {read.status === "failed" ? <><p role="alert">{read.message}</p><Button variant="outline" onClick={restart}>重新读取文案</Button></> : null}
        {page ? <>
            <p>共 {page.total_count} 段{page.items.length ? ` · 第 ${page.items[0].ordinal}–${page.items[page.items.length - 1].ordinal} 段` : ""}</p>
            <div className={styles.scriptSections}>{page.items.map(({ ordinal, section }) => <article key={section.id}><header><span>第 {ordinal} 段</span><strong>{section.title}</strong></header><p className={styles.scriptCopy}>{section.content}</p><p><strong>画面建议：</strong>{section.visual_prompt}</p><details className={styles.traceDetails}><summary>来源证据</summary><p>{section.evidence_reference_ids.join("、") || "未单独标注"}</p></details></article>)}</div>
            {page.total_count > page.items.length ? <div><Button variant="outline" disabled={cursors.length === 1} onClick={() => setCursors((current) => current.slice(0, -1))}>上一页文案</Button><Button variant="outline" disabled={page.next_after_ordinal === null} onClick={() => setCursors((current) => [...current, page.next_after_ordinal!])}>下一页文案</Button></div> : null}
        </> : null}
    </section>;
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
type LibrarySavePending = { source_task_id: string; input: import('@/lib/types/script-library').ScriptLibrarySaveInput };
type LibrarySaveProps = { script: CreationTaskScript; onReloadSource?: () => Promise<void> };
export function ScriptLibrarySave(props: LibrarySaveProps) {
    return <ScriptLibrarySaveForm key={`${props.script.task_id}:${props.script.id}`} {...props}/>;
}
function ScriptLibrarySaveForm({ script, onReloadSource }: LibrarySaveProps) {
    const [form, setForm] = useWorkspaceViewState<{ name: string; category: string; pending: LibrarySavePending | null }>(`library-save-input:${script.task_id}:${script.id}`, () => ({ name: script.title, category: '', pending: null }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sourceStale, setSourceStale] = useState(false);
    const [saved, setSaved] = useState<import('@/lib/types/script-library').ScriptLibrarySaveReceipt | null>(null);
    const active = useRef(true);
    const pendingRef = useRef(form.pending);
    pendingRef.current = form.pending;
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    useEffect(() => { setSourceStale(false); setError(null); }, [script.updated_at]);
    function settle(original: LibrarySavePending, receipt: import('@/lib/types/script-library').ScriptLibrarySaveReceipt) {
        if (receipt.status !== 'saved' || receipt.request_id !== original.input.request_id
            || receipt.source_script_id !== original.input.source_script_id || receipt.source_task_id !== original.source_task_id
            || receipt.source_updated_at !== original.input.expected_source_updated_at
            || receipt.entry.id !== receipt.entry_id || receipt.entry.version !== receipt.version) {
            throw new Error('原入库回执与来源版本或请求不符，当前表单和原 UUID 保留。');
        }
        setSaved(receipt);
        pendingRef.current = null;
        setForm(current => current.pending?.input.request_id === original.input.request_id ? { ...current, pending: null } : current);
        setError(null);
    }
    async function save() {
        if (pendingRef.current) return;
        const original: LibrarySavePending = { source_task_id: script.task_id, input: {
            request_id: crypto.randomUUID(), expected_source_updated_at: script.updated_at,
            source_script_id: script.id, name: form.name, category: form.category,
        } };
        pendingRef.current = original;
        setForm(current => ({ ...current, pending: original }));
        setSaving(true); setError(null); setSourceStale(false);
        try {
            const receipt = await saveScriptLibraryEntry(original.input);
            if (active.current) settle(original, receipt);
        } catch (cause) {
            if (!active.current) return;
            if (isScriptLibraryWriteRejected(cause)) {
                pendingRef.current = null;
                setForm(current => ({ ...current, pending: null }));
                if (cause instanceof CreationPlanApiError && cause.status === 409) setSourceStale(true);
            }
            setError(cause instanceof Error ? cause.message : '入库结果未能读回，当前表单仍保留。');
        } finally { if (active.current) setSaving(false); }
    }
    async function inspect() {
        const original = form.pending;
        if (!original) return;
        setSaving(true); setError(null);
        try {
            const receipt = await getScriptLibraryRequest(original.input.request_id, 'save');
            if (active.current) settle(original, receipt);
        } catch (cause) { if (active.current) setError(cause instanceof CreationPlanApiError && cause.status === 404
            ? '尚未查到原入库回执，不代表未提交；原 UUID 和当前表单继续保留。'
            : cause instanceof Error ? cause.message : '原入库回执未能读回，没有重新提交。'); }
        finally { if (active.current) setSaving(false); }
    }
    return <section className={styles.workSection} aria-label="保存到文案库">
        <h3>保存为可复用文案</h3>
        <label className={styles.field}><span>文案名称</span><input value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))}/></label>
        <label className={styles.field}><span>文案分类</span><input placeholder="例如产品介绍" value={form.category} onChange={event => setForm(current => ({ ...current, category: event.target.value }))}/></label>
        <button className="modern-primary-button" disabled={saving || form.pending !== null || !form.name.trim()} onClick={() => void save()} type="button">{saving ? '正在核对入库请求…' : form.pending ? '入库结果待核对' : '存入文案库'}</button>
        {form.pending ? <><p role="status">原入库请求 {form.pending.input.request_id} 的结果尚待核对；来源版本 {form.pending.input.expected_source_updated_at} 与原输入已保留，不重复提交。</p><Button variant="outline" disabled={saving} onClick={() => void inspect()}>查询原入库结果</Button></> : null}
        {sourceStale && onReloadSource ? <Button variant="outline" disabled={saving} onClick={() => { setSaving(true); void onReloadSource().catch(cause => { if (active.current) setError(cause instanceof Error ? cause.message : '来源版本未能读回。'); }).finally(() => { if (active.current) setSaving(false); }); }}>读取当前来源版本</Button> : null}
        <Link href={saved ? `/knowledge?entry_id=${encodeURIComponent(saved.entry_id)}&source_task_id=${encodeURIComponent(saved.source_task_id)}` : '/knowledge'}>{saved ? `查看该条目 · 版本 ${saved.version}` : '查看文案库'}</Link>
        <p>同一原任务以同名再次保存会追加历史版本；复用的新稿仍需重新确认。</p>
        {saved ? <p role="status">原请求已保存「{saved.entry.name}」版本 {saved.version}；来源版本 {saved.source_updated_at}，原文案与成片不变。</p> : null}
        {error ? <p role="alert">{error}</p> : null}
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
    <p>保存为待确认文案草稿后，可继续补充画面建议；确认时会核查正文、画面建议及相关事实依据。</p>
    <button className={styles.secondaryButton} disabled={pending || busy} onClick={() => void transfer()} type="button">{pending ? "正在核对并保存…" : "保存这条回复为文案草稿"}</button>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
type LoadedScript = Extract<CreationTaskScript, { sections_loaded: true }>;
type ScriptEditorProps = {
    taskId: string;
    script: CreationTaskScript;
    busy: boolean;
    repairing: boolean;
    onConfirm: (script: LoadedScript) => void;
    onWindowSaved?: () => Promise<void>;
};
export function ScriptEditor(props: ScriptEditorProps) {
    if (props.script.selection_status === "draft" && (!props.script.draft_complete || props.script.sections_total_count > 20)) {
        return <ScriptWindowEditor key={`${props.taskId}:${props.script.id}`} taskId={props.taskId} script={props.script} onSaved={async () => { await props.onWindowSaved?.(); }}/>;
    }
    if (props.script.sections_loaded) return <CompleteScriptEditor {...props} script={props.script}/>;
    return <ScriptEditorLoader key={`${props.taskId}:${props.script.id}:${props.script.updated_at}`} {...props}/>;
}
function ScriptEditorLoader(props: ScriptEditorProps) {
    const [loaded, setLoaded] = useState<LoadedScript | null>(null);
    const [reading, setReading] = useState(props.script.sections_total_count <= 20);
    const [error, setError] = useState<string | null>(null);
    const [retry, setRetry] = useState(0);
    const { taskId, script } = props;
    useEffect(() => {
        if (script.sections_total_count > 20) return;
        let active = true;
        setReading(true);
        setError(null);
        void getCreationTaskScriptSections(taskId, script.id, { expected_updated_at: script.updated_at }).then(page => {
            if (!active) return;
            if (page.total_count === 0 || page.next_after_ordinal !== null || page.items.length !== page.total_count) {
                throw new Error("本稿尚未完整载入，不能将当前窗口保存或确认为全文。请逐页查看文案。");
            }
            setLoaded({ ...script, title: page.title, language: page.language, selection_status: page.selection_status,
                updated_at: page.updated_at, sections_loaded: true, sections_total_count: page.total_count,
                sections: page.items.map(item => item.section) });
        }).catch(cause => {
            if (active) setError(cause instanceof Error ? cause.message : "文案没有完整读回，当前不可保存或确认。");
        }).finally(() => { if (active) setReading(false); });
        return () => { active = false; };
    }, [taskId, script, retry]);
    if (loaded) return <CompleteScriptEditor {...props} script={loaded}/>;
    return <>
        <p>文案共 {props.script.sections_total_count} 段，正文尚未完整载入；当前不可保存或确认。</p>
        {reading ? <p role="status">正在读取完整文案…</p> : null}
        {error ? <><p role="alert">{error}</p><Button variant="outline" disabled={props.busy || reading} onClick={() => setRetry(value => value + 1)}>重试读取文案</Button></> : null}
        {props.script.sections_total_count > 20 ? <><p>当前可逐页查看，分段修改尚未开放。</p><ConfirmedScriptView script={props.script}/></> : null}
    </>;
}
function CompleteScriptEditor({ taskId, script, busy, repairing, onConfirm }: Omit<ScriptEditorProps, 'script'> & { script: LoadedScript }) {
    const [draft, setDraft] = useWorkspaceViewState(`script:${script.id}:${script.updated_at}`+".draft", script);
    const [sourceRead, setSourceRead] = useState<
        { status: "loading" } | { status: "failed"; message: string }
        | { status: "ready"; sources: { reference_id: string; title: string; text: string }[] }
    >({ status: "loading" });
    const [savingDraft, setSavingDraft] = useState(false);
    const [draftError, setDraftError] = useState<string | null>(null);
    useEffect(() => {
        let active = true;
        setSourceRead({ status: "loading" });
        void getAssistantScriptContext(taskId).then((context) => { if (active)
            setSourceRead({ status: "ready", sources: context.sources }); })
            .catch((cause) => { if (active)
            setSourceRead({ status: "failed", message: cause instanceof Error ? cause.message : "无法读取当前任务资料。" }); });
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
                edit_mode: "replace_all",
            });
            setDraft((current) => current === submittedDraft
                ? saved
                : { ...current, updated_at: saved.updated_at, draft_complete: saved.draft_complete });
        }
        catch (cause) {
            setDraftError(cause instanceof Error ? cause.message : "草稿没有保存成功；当前编辑内容仍保留在本页。");
        }
        finally {
            setSavingDraft(false);
        }
    }
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}><div><h2>{repairing ? "修订文案" : "检查文案"}</h2><p>确认时会核查正文、画面建议及相关事实依据；通过后使用这份文案制作。</p></div></div>
      {script.reused_from_entry_id ? <p>从文案库复用的新稿，尚未确认；没有调用模型或复制旧成片。<Link href="/knowledge">查看文案来源</Link></p> : null}
      {script.source_reply_reference ? <p>来自已完成对话回复。当前内容是待确认草稿；可继续编辑画面建议和相关资料，确认时会核查完整内容。</p> : null}
      <label className={styles.field}><span>标题</span><input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })}/></label>
      <div className={styles.scriptSections}>{draft.sections.map((section, index) => <article key={section.id}><header><span>第 {index + 1} 段</span><strong>{section.title}</strong></header><textarea aria-label={`第 ${index + 1} 段正文`} rows={5} value={section.content} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, content: event.target.value } : item) })}/><label className={styles.field}><span>画面建议</span><textarea aria-label={`第 ${index + 1} 段画面建议`} rows={2} value={section.visual_prompt} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, visual_prompt: event.target.value } : item) })}/></label><fieldset className={styles.scriptSourceChoices}><legend>选择这段使用的当前资料</legend>{sourceRead.status === "loading" ? <p role="status">正在读取当前任务资料…</p> : sourceRead.status === "failed" ? <p>资料尚未读回，请检查读取错误。</p> : sourceRead.sources.length ? sourceRead.sources.map((source) => <label key={source.reference_id}><input type="checkbox" checked={section.evidence_reference_ids.includes(source.reference_id)} onChange={(event) => setDraft({ ...draft, sections: draft.sections.map((item) => item.id === section.id ? { ...item, evidence_reference_ids: event.target.checked ? [...item.evidence_reference_ids, source.reference_id] : item.evidence_reference_ids.filter((id) => id !== source.reference_id) } : item) })}/>{source.title}</label>) : <p>当前任务没有可选资料；涉及事实的内容仍需有效依据，确认时会核查。</p>}</fieldset></article>)}</div>
      {sourceRead.status === "failed" ? <p role="alert">{sourceRead.message}</p> : null}
      {draftError ? <p role="alert">{draftError}</p> : null}
      {script.selection_status === "draft" ? <Button variant="outline" className={styles.secondaryButton} disabled={busy || savingDraft || !draft.draft_complete || !draft.title.trim() || draft.sections.some((section) => !section.content.trim())} onClick={() => void saveDraft()} type="button">{savingDraft ? "正在保存草稿…" : "保存草稿"}</Button> : null}
      <div className={styles.primaryAction}><Button className="modern-primary-button" disabled={busy || savingDraft || !draft.draft_complete || !draft.title.trim() || draft.sections.some((section) => !section.content.trim() || !section.visual_prompt.trim())} onClick={() => onConfirm(draft)} type="button">{busy ? <LoaderCircle className="is-spinning" size={16}/> : <Check size={16}/>}{repairing ? "确认修订文案" : "确认文案"}</Button></div>
    </section>);
}
