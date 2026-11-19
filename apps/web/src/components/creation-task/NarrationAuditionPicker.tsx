"use client";

import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import { getCreationTaskScriptSections } from "@/lib/api/creation-tasks";
import { readSectionAudition, saveSectionAudition, type SectionAuditionStatus } from "@/lib/api/section-audition";
import type { CreationTaskScript, CreationTaskScriptSectionWindow } from "@/lib/types";
import { visibleError } from "../material-library/material-errors";
import { WorkspaceAudio } from "../workbench/WorkspaceMedia";
import { Button } from "../ui/button";
import styles from "../CreationTaskWorkspace.module.css";

function AuditionChoice({ script, sectionId, disabled }: {
  script: CreationTaskScript; sectionId: string; disabled: boolean;
}) {
  const [status, setStatus] = useState<SectionAuditionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setStatus(null);
    setError("");
    void readSectionAudition(script.task_id, sectionId)
      .then(value => { if (active) setStatus(value); })
      .catch(cause => { if (active) setError(visibleError(cause, "这段的试听记录未能读取。")); });
    return () => { active = false; };
  }, [script.task_id, sectionId, revision]);

  async function choose(id: string | null) {
    if (!status?.available || busy || disabled) return;
    setBusy(true);
    try {
      await saveSectionAudition(script.task_id, sectionId, {
        request_id: crypto.randomUUID(), preview_run_id: id,
        expected_preview_run_id: status.selection?.preview_run_id ?? null,
        expected_script_id: script.id, expected_script_updated_at: script.updated_at,
      });
      setRevision(n => n + 1);
    } catch (cause) {
      setStatus(null);
      setError(visibleError(cause, "配音选择还未确认，请重新读取当前段落。"));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p role="alert">{error} <Button variant="outline" onClick={() => setRevision(n => n + 1)}>重新读取</Button></p>;
  if (!status) return <p role="status">正在读取段落试听…</p>;
  return <>
    {!status.available && status.reason && <p role="status">{status.reason}</p>}
    {status.candidates.length === 0 && <p>这段还没有可采用的试听录音。</p>}
    {status.candidates.map((item, index) => {
      const selected = item.preview_run_id === status.selection?.preview_run_id;
      return <div key={item.preview_run_id}>
        <span>试听 {index + 1} · {item.duration_seconds.toFixed(2)} 秒</span>
        <WorkspaceAudio controls preload="metadata" aria-label={`试听录音 ${index + 1}`} src={apiUrl(item.output_url)} />
        {selected ? <span role="status">已采用为本段配音</span>
          : <Button variant="outline" disabled={busy || disabled || !status.available}
              onClick={() => void choose(item.preview_run_id)}>采用这次试听</Button>}
      </div>;
    })}
    {status.selection && <Button variant="ghost" disabled={busy || disabled || !status.available}
      onClick={() => void choose(null)}>取消本段采用</Button>}
  </>;
}

export function NarrationAuditionPicker({ script, disabled }: { script: CreationTaskScript; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [cursors, setCursors] = useState([0]);
  const [page, setPage] = useState<CreationTaskScriptSectionWindow | null>(null);
  const [sectionId, setSectionId] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const cursor = cursors[cursors.length - 1];
  useEffect(() => {
    if (!open) return;
    let active = true;
    setPage(null);
    setError("");
    setSectionId("");
    void getCreationTaskScriptSections(script.task_id, script.id, {
      after_ordinal: cursor, expected_updated_at: script.updated_at,
    }).then(value => {
      if (active) setPage(value);
    }).catch(cause => { if (active) setError(visibleError(cause, "已确认文案未能读取。")); });
    return () => { active = false; };
  }, [open, cursor, retry, script.task_id, script.id, script.updated_at]);
  const selected = page?.items.find(item => item.section.id === sectionId);
  return <details className={styles.requirementDetails} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>采用段落试听</summary>
    {open && (error ? <p role="alert">{error} <Button variant="outline" onClick={() => setRetry(n => n + 1)}>重新读取文案</Button></p>
      : !page ? <p role="status">正在读取文案…</p> : <>
        <label className={styles.field}><span>文案段落</span>
          <select value={sectionId} onChange={event => setSectionId(event.target.value)}>
            <option value="">选择已确认的段落</option>
            {page.items.map(({ section, ordinal }) => <option key={section.id} value={section.id}>第 {ordinal} 段 · {section.title}</option>)}
          </select>
        </label>
        {page.total_count > page.items.length && <div>
          <Button variant="ghost" disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>上一页段落</Button>
          <Button variant="ghost" disabled={page.next_after_ordinal === null} onClick={() => {
            if (page.next_after_ordinal !== null) setCursors([...cursors, page.next_after_ordinal]);
          }}>下一页段落</Button>
        </div>}
        {selected && <><p>{selected.section.content}</p><AuditionChoice key={sectionId} script={script} sectionId={sectionId} disabled={disabled} /></>}
      </>)}
  </details>;
}
