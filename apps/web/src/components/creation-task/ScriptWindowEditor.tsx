"use client";
import { useEffect, useRef, useState } from "react";
import { CreationPlanApiError, getAssistantScriptContext } from "@/lib/api";
import { getCreationTaskScriptSections, getCreationTaskScriptEditSources, patchCreationTaskScriptSections, startCreationTaskScriptWindow, confirmSavedCreationTaskScript } from "@/lib/api/creation-tasks";
import type { CreationTaskScript, CreationTaskScriptSection, CreationTaskScriptSectionWindow, CreationTaskScriptSectionsPatchInput, CreationTaskScriptWindowSaved, CreationTaskScriptConfirmSavedInput, CreationTaskScriptConfirmed } from "@/lib/types/creation-tasks";
import { useWorkspaceViewState } from "../workbench/context";
import { Button } from "../ui/button";
import { ScriptRequestLookup } from "./ScriptRequestLookup";
import type { AuditionVoice } from "./DeliveryVoiceSample";
import { SavedScriptAudition } from "./SavedScriptAudition";
import { useScriptWindowRequestRecovery } from "./script-window-request-recovery";
import styles from "../CreationTaskWorkspace.module.css";

const WINDOW_LIMIT = 20;

function sameScriptRevision(left?: string, right?: string) {
    return left?.replace(/\+00:00$/, "Z") === right?.replace(/\+00:00$/, "Z");
}
type Source = { reference_id: string; title: string };
type Edit = { version: string; after: number; sections: CreationTaskScriptSection[] };
type Append = { anchor: string | null; version: string; after: number; sections: CreationTaskScriptSection[] };
type Form = { edit: Edit | null; append: Append | null };
function newSection(): CreationTaskScriptSection {
    return { id: crypto.randomUUID(), title: "", content: "", visual_prompt: "", evidence_reference_ids: [] };
}
function validSections(sections: CreationTaskScriptSection[]) {
    return sections.length > 0 && sections.every(section => section.title.trim() && section.content.trim());
}
function useSources(taskId: string, scriptId?: string) {
    const [sources, setSources] = useState<Source[]>([]);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        let active = true;
        setSources([]);
        setError(null);
        const read = scriptId ? getCreationTaskScriptEditSources(taskId, scriptId) : getAssistantScriptContext(taskId);
        void read.then(value => {
            if (active) setSources(value.sources);
        }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "当前资料未能读回。"); });
        return () => { active = false; };
    }, [taskId, scriptId]);
    return { sources, error };
}
function SectionFields({ section, ordinal, disabled, sources, onChange }: {
    section: CreationTaskScriptSection; ordinal: number | string; disabled: boolean; sources: Source[];
    onChange: (section: CreationTaskScriptSection) => void;
}) {
    return <article>
        <header>第 {ordinal} 段</header>
        <label className={styles.field}><span>段落标题</span><input aria-label={`第 ${ordinal} 段标题`} value={section.title} disabled={disabled} onChange={event => onChange({ ...section, title: event.target.value })}/></label>
        <label className={styles.field}><span>正文</span><textarea aria-label={`第 ${ordinal} 段正文`} value={section.content} disabled={disabled} onChange={event => onChange({ ...section, content: event.target.value })}/></label>
        <label className={styles.field}><span>画面建议</span><textarea aria-label={`第 ${ordinal} 段画面建议`} value={section.visual_prompt} disabled={disabled} onChange={event => onChange({ ...section, visual_prompt: event.target.value })}/></label>
        <fieldset className={styles.scriptSourceChoices} disabled={disabled}><legend>选择这段使用的当前资料</legend>{sources.map(source => <label key={source.reference_id}><input type="checkbox" checked={section.evidence_reference_ids.includes(source.reference_id)} onChange={event => onChange({ ...section, evidence_reference_ids: event.target.checked ? [...section.evidence_reference_ids, source.reference_id] : section.evidence_reference_ids.filter(id => id !== source.reference_id) })}/>{source.title}</label>)}</fieldset>
    </article>;
}

type SubtitleImportContinuation = {
    taskId: string;
    scriptId: string;
    updatedAt: string;
    totalCount: number;
};

function ScriptSubtitleImport({ disabled, sources, sections, continuation, onAppend }: {
    disabled: boolean;
    sources: Source[];
    sections: CreationTaskScriptSection[];
    continuation?: SubtitleImportContinuation;
    onAppend: (sections: CreationTaskScriptSection[]) => void;
}) {
    const [text, setText] = useState("");
    const [sourceId, setSourceId] = useState("");
    const [visualPrompt, setVisualPrompt] = useState("");
    const [readingFile, setReadingFile] = useState(false);
    const [checkingPrefix, setCheckingPrefix] = useState(false);
    const currentSections = useRef(sections);
    currentSections.current = sections;
    const [notice, setNotice] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const used = sections.filter(section =>
        section.title || section.content || section.visual_prompt || section.evidence_reference_ids.length);
    const available = WINDOW_LIMIT - used.length;

    async function importText() {
        setNotice(null);
        setError(null);
        const blocks = text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").trim().split(/\n[ \t]*\n+/);
        const cues: { number: number; content: string }[] = [];
        for (const block of blocks) {
            const [number, timing, ...lines] = block.split("\n");
            const content = lines.join("\n").trim();
            if (!/^\d+$/.test(number?.trim()) ||
                !/^\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}$/.test(timing?.trim()) || !content) {
                setError("请提供带编号、起止时间和正文的 SRT 段落。");
                return;
            }
            const cueNumber = Number(number.trim());
            if (!Number.isSafeInteger(cueNumber) || cueNumber <= 0 ||
                (cues.length > 0 && cueNumber !== cues[cues.length - 1].number + 1)) {
                setError("请使用编号连续的 SRT 字幕。");
                return;
            }
            if (content.length > 4000) {
                setError(`字幕第 ${cueNumber} 段正文超过 4000 字，请拆分后再导入。`);
                return;
            }
            cues.push({ number: cueNumber, content });
        }

        const storedCount = continuation?.totalCount ?? 0;
        const fromBeginning = cues[0].number === 1;
        if (!fromBeginning && cues[0].number !== storedCount + 1) {
            setError(`请选择完整 SRT，或选择从第 ${storedCount + 1} 句开始的续传批次。`);
            return;
        }
        const prefixLength = fromBeginning ? storedCount : 0;
        const offset = prefixLength + used.length;
        if (offset >= cues.length) {
            setError("文件没有剩余正文可填入。");
            return;
        }
        setCheckingPrefix(true);
        try {
            if (continuation && prefixLength) {
                let after = 0;
                while (after < prefixLength) {
                    const page = await getCreationTaskScriptSections(continuation.taskId, continuation.scriptId, {
                        after_ordinal: after, expected_updated_at: continuation.updatedAt,
                    });
                    if (!page.items.length || page.items.some(item => item.section.content !== cues[item.ordinal - 1]?.content)) {
                        throw new Error("这个 SRT 与已存文案的前段不一致。请检查所选文件，已存文案保留。");
                    }
                    after = page.items[page.items.length - 1].ordinal;
                }
            }
            if (currentSections.current !== sections ||
                used.some((section, index) => section.content !== cues[prefixLength + index]?.content)) {
                throw new Error("本窗已填写正文与所选 SRT 不一致，请先保存或撤回本窗修改。");
            }
            const batch = cues.slice(offset, offset + available);
            const additions = batch.map(cue => ({ ...newSection(), title: `第 ${cue.number} 句`,
                content: cue.content, visual_prompt: visualPrompt.trim(), evidence_reference_ids: [sourceId] }));
            onAppend([...used, ...additions]);
            const remaining = cues.length - offset - batch.length;
            setNotice(`已填入第 ${batch[0].number}–${batch[batch.length - 1].number} 句。${remaining
                ? `还有 ${remaining} 句，保存后可选择同一文件继续导入。`
                : "检查后保存当前窗口。"}`);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "未能核对已保存的文案前段。");
        } finally {
            setCheckingPrefix(false);
        }
    }

    async function readFile(file: File) {
        setError(null);
        setNotice(null);
        if (!file.name.toLowerCase().endsWith(".srt")) {
            setError("请选择扩展名为 .srt 的字幕文件。");
            return;
        }
        if (file.size > 400000) {
            setError("单次请选择不超过 400 KB 的 SRT 文件。");
            return;
        }
        setReadingFile(true);
        try {
            setText(await file.text());
            setNotice(`已读取 ${file.name}。`);
        } catch {
            setError("未能读取这个 SRT 文件。");
        } finally {
            setReadingFile(false);
        }
    }

    return <details className={styles.requirementDetails}>
        <summary>批量导入 SRT 文案</summary>
        <p>每句填入一段正文，本窗可填 {available} 段。可选择完整 SRT，保存后从同一文件继续。</p>
        <label className={styles.field}><span>选择 SRT 文件</span>
            <input type="file" accept=".srt" disabled={disabled || readingFile || checkingPrefix} onChange={event => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file) void readFile(file);
            }}/></label>
        <label className={styles.field}><span>或粘贴 SRT 正文</span>
            <textarea value={text} disabled={disabled || readingFile || checkingPrefix} onChange={event => setText(event.target.value)}/></label>
        {sources.length ? <label className={styles.field}><span>本批段落引用</span>
            <select value={sourceId} disabled={disabled} onChange={event => setSourceId(event.target.value)}>
                <option value="">选择本批引用的资料</option>
                {sources.map(source => <option key={source.reference_id} value={source.reference_id}>{source.title}</option>)}
            </select></label> : null}
        <label className={styles.field}><span>本批画面建议</span>
            <textarea value={visualPrompt} maxLength={1000} disabled={disabled}
                onChange={event => setVisualPrompt(event.target.value)}/></label>
        <Button variant="outline" type="button"
            disabled={disabled || readingFile || checkingPrefix || available === 0 || !text.trim() || !sourceId || !visualPrompt.trim()}
            onClick={() => void importText()}>{checkingPrefix ? "核对已保存的前段…" : "填入文案段落"}</Button>
        {used.some(section => !section.visual_prompt.trim()) ? <Button variant="outline" type="button"
            disabled={disabled || !visualPrompt.trim()} onClick={() => {
                onAppend(sections.map(section => section.content.trim() && !section.visual_prompt.trim()
                    ? { ...section, visual_prompt: visualPrompt.trim() } : section));
                setNotice("已补充未填写的画面建议。");
            }}>补充未填画面建议</Button> : null}
        {notice ? <p role="status">{notice}</p> : null}
        {error ? <p role="alert">{error}</p> : null}
    </details>;
}

type WindowEditorProps = {
    taskId: string;
    script: CreationTaskScript;
    auditionVoice?: AuditionVoice;
    onSaved: (receipt: CreationTaskScriptWindowSaved | CreationTaskScriptConfirmed) => Promise<void>;
};
export function ScriptWindowEditor(props: WindowEditorProps) {
    return <WindowEditorBody key={`${props.taskId}:${props.script.id}`} {...props}/>;
}
function WindowEditorBody({ taskId, script, auditionVoice, onSaved }: WindowEditorProps) {
    const [form, setForm] = useWorkspaceViewState<Form>(`script-window-inputs:${taskId}:${script.id}`, { edit: null, append: null });
    const recovery = useScriptWindowRequestRecovery(taskId, script.id);
    const [page, setPage] = useState<CreationTaskScriptSectionWindow | null>(null);
    const [cursors, setCursors] = useState([form.edit?.after ?? form.append?.after ?? 0]);
    const [version, setVersion] = useState<string | undefined>(form.edit?.version ?? form.append?.version ?? script.updated_at);
    const [readAttempt, setReadAttempt] = useState(0);
    const [reading, setReading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [readError, setReadError] = useState<string | null>(null);
    const [readConflict, setConflict] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmed, setConfirmed] = useState<CreationTaskScriptConfirmed | null>(null);
    const readRevision = useRef(0);
    const active = useRef(true);
    const currentVersion = useRef(version);
    const workspaceVersion = useRef(script.updated_at);
    const workspaceReadVersion = useRef<string | undefined>(undefined);
    const { sources, error: sourceError } = useSources(taskId, script.id);
    const after = cursors[cursors.length - 1];
    useEffect(() => { active.current = true; return () => { active.current = false; readRevision.current += 1; }; }, []);
    useEffect(() => {
        const previous = workspaceVersion.current;
        workspaceVersion.current = script.updated_at;
        if (sameScriptRevision(previous, script.updated_at) || sameScriptRevision(currentVersion.current, script.updated_at)) return;
        readRevision.current += 1;
        workspaceReadVersion.current = script.updated_at;
        setReadAttempt(value => value + 1);
    }, [script.updated_at]);
    useEffect(() => {
        const revision = ++readRevision.current;
        const savedVersion = workspaceReadVersion.current;
        // Keep the current page and DOM mounted during a saved-version read.
        if (!savedVersion) {
            setReading(true);
            setPage(null);
        }
        setReadError(null);
        void getCreationTaskScriptSections(taskId, script.id, { after_ordinal: after,
            expected_updated_at: savedVersion ?? currentVersion.current }).then(value => {
            if (!active.current || revision !== readRevision.current) return;
            if (savedVersion && (value.task_id !== taskId || value.script_id !== script.id
                || !sameScriptRevision(value.updated_at, savedVersion))) throw new Error("保存版本的文案窗口未能核实。");
            workspaceReadVersion.current = undefined;
            currentVersion.current = value.updated_at;
            setPage(value);
            setVersion(value.updated_at);
            setConflict(false);
        }).catch(cause => {
            if (!active.current || revision !== readRevision.current) return;
            setReadError(cause instanceof Error ? cause.message : "文案窗口未能读回。");
            if (cause instanceof CreationPlanApiError && cause.status === 409) setConflict(true);
        }).finally(() => { if (active.current && revision === readRevision.current) setReading(false); });
    }, [taskId, script.id, after, readAttempt]);
    const sections = form.edit?.sections ?? page?.items.map(item => item.section) ?? [];
    const dirty = form.edit !== null;
    const draftVersion = form.edit?.version ?? form.append?.version;
    const conflict = readConflict || (draftVersion !== undefined && !sameScriptRevision(draftVersion, version));
    const locked = saving || !recovery.ready || recovery.pending !== null || confirmed !== null;
    const canNavigate = !reading && !locked && !dirty && form.append === null && !conflict;
    async function settleSaved(receipt: CreationTaskScriptWindowSaved) {
        await recovery.clear(receipt);
        if (!active.current) return;
        setError(null);
        setReading(true);
        setPage(null);
        setForm({ edit: null, append: null });
        readRevision.current += 1;
        workspaceReadVersion.current = undefined;
        currentVersion.current = receipt.updated_at;
        setVersion(receipt.updated_at);
        setNotice(`已保存窗口，当前共 ${receipt.total_count} 段；尚未采用为完整文案。`);
        setReadAttempt(value => value + 1);
        try { await onSaved(receipt); }
        catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : "窗口已保存，但作品状态未能刷新。"); }
    }
    async function settleConfirmed(receipt: CreationTaskScriptConfirmed) {
        await recovery.clear(receipt);
        if (!active.current) return;
        setError(null);
        setReadError(null);
        setConflict(false);
        setForm({ edit: null, append: null });
        readRevision.current += 1;
        setReading(false);
        workspaceReadVersion.current = undefined;
        currentVersion.current = receipt.updated_at;
        setVersion(receipt.updated_at);
        setConfirmed(receipt);
        setNotice(`已核查并采用完整文案，共 ${receipt.total_count} 段。`);
        try { await onSaved(receipt); }
        catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : "文案已采用，但作品状态未能刷新。"); }
    }
    async function confirmSaved() {
        if (!currentVersion.current) return;
        const command: CreationTaskScriptConfirmSavedInput = { request_id: crypto.randomUUID(), expected_updated_at: currentVersion.current };
        setSaving(true);
        setError(null);
        setNotice(null);
        try {
            const receipt = await recovery.execute(command.request_id, "confirm", () => confirmSavedCreationTaskScript(taskId, script.id, command));
            if (!active.current) return;
            if (receipt.status === "confirmed") await settleConfirmed(receipt);
            else setNotice(receipt.message);
        } catch (cause) {
            if (!active.current) return;
            setError(cause instanceof Error ? cause.message : "完整采用结果尚待核对，原请求已保留。");
        } finally { if (active.current) setSaving(false); }
    }
    async function submit(command: CreationTaskScriptSectionsPatchInput) {
        setSaving(true);
        setError(null);
        setNotice(null);
        try {
            const receipt = await recovery.execute(command.request_id, "patch", () => patchCreationTaskScriptSections(taskId, script.id, command));
            if (!active.current) return;
            if (receipt.status === "saved") await settleSaved(receipt);
            else setNotice(receipt.message);
        } catch (cause) {
            if (!active.current) return;
            setError(cause instanceof Error ? cause.message : "保存结果尚待核对，原请求继续保留。");
            if (cause instanceof CreationPlanApiError && cause.status === 409) setConflict(true);
        } finally { if (active.current) setSaving(false); }
    }
    function updateSection(updated: CreationTaskScriptSection) {
        const next = sections.map(item => item.id === updated.id ? updated : item);
        setForm(current => ({ ...current, edit: JSON.stringify(next) === JSON.stringify(page?.items.map(item => item.section)) ? null
            : { version: current.edit?.version ?? currentVersion.current!, after, sections: next } }));
    }
    function save() {
        if (!page || !currentVersion.current) return;
        const changed = form.append?.sections ?? sections.filter(section => JSON.stringify(section) !== JSON.stringify(page.items.find(item => item.section.id === section.id)?.section));
        void submit({ request_id: crypto.randomUUID(), expected_updated_at: form.edit?.version ?? form.append?.version ?? currentVersion.current,
            sections: changed, ...(form.append ? { insert_after_section_id: form.append.anchor } : {}) });
    }
    function restart() {
        setForm({ edit: null, append: null });
        workspaceReadVersion.current = undefined;
        currentVersion.current = undefined;
        setVersion(undefined);
        setCursors([0]);
        setPage(null);
        setError(null);
        setConflict(false);
        setReadAttempt(value => value + 1);
    }
    return <section className={styles.workSection} aria-label="分窗编辑文案">
        <div className={styles.sectionHeading}><h2>{page?.title ?? script.title}</h2><span>共 {page?.total_count ?? script.sections_total_count} 段</span></div>
        {!script.draft_complete && !confirmed ? <p>当前为部分稿，已存 {page?.total_count ?? script.sections_total_count} 段不代表目标已完整；可分窗修改或追加，尚未核查并采用为完整文案。</p> : null}
        <p>每次只保存当前修改的段落，其他已存段保留。窗口保存不等于整稿核查或采用。</p>
        {auditionVoice && page?.items.length ? <SavedScriptAudition voice={auditionVoice} items={page.items}
            taskId={taskId} scriptId={script.id} updatedAt={page.updated_at} /> : null}
        {reading ? <p role="status">正在读取文案窗口…</p> : null}
        {notice ? <p role="status">{notice}</p> : null}
        {recovery.error ? <p role="alert">{recovery.error}</p> : null}
        <p>刷新仅保留原请求定位；尚未提交的修改不会随刷新恢复。原请求输入需从服务端回执读取。</p>
        {error ? <p role="alert">{error}</p> : null}
        {readError ? <p role="alert">{readError}</p> : null}
        {sourceError ? <p role="alert">{sourceError}</p> : null}
        {recovery.pending?.operation === "patch" ? <><p role="status">窗口保存结果尚待核对，原请求定位已保留；暂不能重复提交。</p><ScriptRequestLookup key={recovery.pending.requestId} taskId={taskId} requestId={recovery.pending.requestId} locator={{ operation: "patch", script_id: script.id }} disabled={saving || !recovery.ready} verify={recovery.verify} onResult={async result => {
            if (result.status !== "saved") throw new Error("原请求回执与窗口保存操作不符，原请求继续保留。");
            setSaving(true);
            try { await settleSaved(result); } finally { if (active.current) setSaving(false); }
        }}/></> : null}
        {recovery.pending?.operation === "confirm" ? <><p role="status">完整采用结果尚待核对，原请求已保留；暂不能重复提交。</p><ScriptRequestLookup key={recovery.pending.requestId} taskId={taskId} requestId={recovery.pending.requestId} locator={{ operation: "confirm", script_id: script.id }} disabled={saving || !recovery.ready} verify={recovery.verify} onResult={async result => {
            if (result.status !== "confirmed") throw new Error("原请求回执与完整采用操作不符，原请求继续保留。");
            setSaving(true);
            try { await settleConfirmed(result); } finally { if (active.current) setSaving(false); }
        }} onRejected={async result => {
            await recovery.clear(result);
            if (!active.current) return;
            setError(null);
            setNotice("核查未通过，已保存的草稿可以继续编辑。");
        }}/></> : null}
        {conflict && recovery.ready && !recovery.pending ? <Button variant="outline" onClick={restart}>放弃本页修改并读取最新版本</Button> : null}
        {(!page || readError) && !reading && !conflict ? <Button variant="outline" onClick={() => { setError(null); setReadAttempt(value => value + 1); }}>重试读取窗口</Button> : null}
        <div className={styles.scriptSections}>{sections.map((section, index) => <div key={section.id}>
            <SectionFields section={section} ordinal={page?.items[index]?.ordinal ?? after + index + 1} disabled={locked || reading || form.append !== null} sources={sources} onChange={updateSection}/>
            <Button variant="outline" disabled={!canNavigate} onClick={() => setForm(current => ({ ...current, append: { anchor: section.id, version: currentVersion.current!, after, sections: [newSection()] } }))}>在第 {page?.items[index]?.ordinal ?? index + 1} 段后插入</Button>
        </div>)}</div>
        {dirty && !recovery.pending ? <><p>本页有未保存修改，保存或撤回后可翻页。</p><Button disabled={reading || locked || !page || conflict || !validSections(sections)} onClick={save}>保存本页修改</Button><Button variant="outline" disabled={locked} onClick={() => setForm(current => ({ ...current, edit: null }))}>撤回本页未保存修改</Button></> : null}
        {form.append ? <section aria-label="新增文案窗口">
            <h3>{form.append.anchor ? "在所选段落后插入" : "追加到文案末尾"}</h3>
            <ScriptSubtitleImport disabled={locked} sources={sources} sections={form.append.sections}
                continuation={form.append.anchor === null && page ? {
                    taskId, scriptId: script.id, updatedAt: form.append.version, totalCount: page.total_count,
                } : undefined}
                onAppend={sections => setForm(current => current.append
                    ? ({ ...current, append: { ...current.append, sections } }) : current)}/>
            {form.append.sections.map((section, index) => <SectionFields key={section.id} section={section} ordinal={`新增 ${index + 1}`} disabled={locked} sources={sources} onChange={updated => setForm(current => ({ ...current, append: { ...current.append!, sections: current.append!.sections.map(item => item.id === updated.id ? updated : item) } }))}/>)}
            <Button variant="outline" disabled={locked || form.append.sections.length >= WINDOW_LIMIT} onClick={() => setForm(current => ({ ...current, append: { ...current.append!, sections: [...current.append!.sections, newSection()] } }))}>再加一段</Button>
            <p>本次最多保存 {WINDOW_LIMIT} 段，保存后可继续追加，不是整稿段数上限。</p>
            <Button disabled={locked || !page || conflict || !validSections(form.append.sections)} onClick={save}>保存新增段落</Button>
            <Button variant="outline" disabled={locked} onClick={() => setForm(current => ({ ...current, append: null }))}>撤回新增段落</Button>
        </section> : null}
        {!form.append ? <Button variant="outline" disabled={!canNavigate || !page} onClick={() => setForm(current => ({ ...current, append: { anchor: null, version: currentVersion.current!, after, sections: [newSection()] } }))}>追加文案</Button> : null}
        <div><Button variant="outline" disabled={!canNavigate || cursors.length === 1} onClick={() => setCursors(current => current.slice(0, -1))}>上一页文案</Button><Button variant="outline" disabled={!canNavigate || page?.next_after_ordinal == null} onClick={() => setCursors(current => [...current, page!.next_after_ordinal!])}>下一页文案</Button></div>
        {!confirmed ? <Button disabled={locked || reading || !page || dirty || form.append !== null || conflict || page.total_count === 0} onClick={() => void confirmSaved()}>核查并采用完整文案</Button> : null}
        {version ? <small>当前编辑版本：{version}</small> : null}
    </section>;
}

type WindowStartProps = {
    taskId: string;
    retainedScript?: Pick<CreationTaskScript, "id" | "title" | "updated_at">;
    onSaved: (receipt: CreationTaskScriptWindowSaved) => Promise<void>;
};
export function ScriptWindowStart(props: WindowStartProps) {
    const draftScope = props.retainedScript?.id ?? "first";
    return <WindowStartBody key={`${props.taskId}:${draftScope}`} {...props}/>;
}
function WindowStartBody({ taskId, retainedScript, onSaved }: WindowStartProps) {
    const formKey = retainedScript ? `script-window-start:${taskId}:${retainedScript.id}` : `script-window-start:${taskId}`;
    const [form, setForm] = useWorkspaceViewState<{ title: string; sections: CreationTaskScriptSection[] }>(
        formKey, () => ({ title: retainedScript?.title ?? "", sections: [newSection()] }));

    const [saving, setSaving] = useState(false);
    const recovery = useScriptWindowRequestRecovery(taskId, null);
    const [receipt, setReceipt] = useState<CreationTaskScriptWindowSaved | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [restored, setRestored] = useState(false);
    const active = useRef(true);
    const { sources, error: sourceError } = useSources(taskId);
    useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
    async function settleStart(value: CreationTaskScriptWindowSaved) {
        await recovery.clear(value);
        if (!active.current) return;
        setError(null);
        setReceipt(value);
        try { await onSaved(value); }
        catch (cause) { if (active.current) setError(cause instanceof Error ? cause.message : "首窗已保存，但作品状态未能刷新。"); }
    }
    async function save() {
        const command = {
            request_id: crypto.randomUUID(), title: form.title, sections: form.sections,
            ...(retainedScript ? { retained_script: {
                script_id: retainedScript.id, expected_updated_at: retainedScript.updated_at,
            } } : {}),
        };
        setSaving(true);
        setError(null);
        try {
            const value = await recovery.execute(command.request_id, "start", () => startCreationTaskScriptWindow(taskId, command));
            if (!active.current) return;
            if (value.status === "saved") await settleStart(value);
            else setError(value.message);
        } catch (cause) {
            if (!active.current) return;
            setError(cause instanceof Error ? cause.message : "首窗保存结果尚待核对，原请求继续保留。");
        } finally { if (active.current) setSaving(false); }
    }
    const locked = saving || !recovery.ready || recovery.pending !== null || receipt !== null;
    return <section className={styles.workSection} aria-label="手动编写文案">
        <h2>{retainedScript ? "基于新资料另存文案" : "手动编写文案"}</h2><p>先保存首个窗口，再在同一稿件继续追加；首窗不是完整文案，不会自动确认或启动制作。</p>
        {restored ? <p role="status">已恢复上次提交的正文和画面建议，请继续编辑或保存草稿。</p> : null}
        {recovery.error ? <p role="alert">{recovery.error}</p> : null}
        <p>刷新仅保留原请求定位；尚未提交的修改不会随刷新恢复。原请求输入需从服务端回执读取。</p>
        {error ? <p role="alert">{error}</p> : null}{sourceError ? <p role="alert">{sourceError}</p> : null}
        <label className={styles.field}><span>文案标题</span><input aria-label="新文案标题" value={form.title} disabled={locked} onChange={event => setForm(current => ({ ...current, title: event.target.value }))}/></label>
        <ScriptSubtitleImport disabled={locked} sources={sources} sections={form.sections}
            onAppend={sections => setForm(current => ({ ...current, sections }))}/>
        {form.sections.map((section, index) => <SectionFields key={section.id} section={section} ordinal={index + 1} disabled={locked} sources={sources} onChange={updated => setForm(current => ({ ...current, sections: current.sections.map(item => item.id === updated.id ? updated : item) }))}/>)}
        <Button variant="outline" disabled={locked || form.sections.length >= WINDOW_LIMIT} onClick={() => setForm(current => ({ ...current, sections: [...current.sections, newSection()] }))}>再加一段</Button>
        <p>本次最多保存 {WINDOW_LIMIT} 段，保存后可继续追加。</p>
        {recovery.pending ? <ScriptRequestLookup key={recovery.pending.requestId} taskId={taskId} requestId={recovery.pending.requestId} locator={{ operation: "start" }} disabled={saving || !recovery.ready} verify={recovery.verify} onResult={async result => {
            if (result.status !== "saved") throw new Error("原请求回执与首窗保存操作不符，原请求继续保留。");
            setSaving(true);
            try { await settleStart(result); } finally { if (active.current) setSaving(false); }
        }} onRejected={async result => {
            const original = result.submitted_input;
            if (!original || !("title" in original) || original.request_id !== result.request_id) {
                throw new Error("没有读回原首窗正文，原请求继续保留。");
            }
            await recovery.clear(result);
            if (!active.current) return;
            setForm({ title: original.title, sections: original.sections });
            setError(null);
            setRestored(true);
        }}/> : null}
        {receipt ? <><p role="status">首窗已保存，共 {receipt.total_count} 段；尚未核查并采用为完整文案。</p><Button variant="outline" onClick={() => void onSaved(receipt).catch(cause => setError(cause instanceof Error ? cause.message : "作品状态未能刷新。"))}>读取已保存草稿</Button></> : <Button disabled={locked || !form.title.trim() || !validSections(form.sections)} onClick={() => void save()}>{recovery.pending ? "首窗保存结果待核对" : saving ? "正在保存首窗…" : "保存首窗"}</Button>}
    </section>;
}
