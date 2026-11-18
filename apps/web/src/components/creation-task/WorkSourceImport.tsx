"use client";

import { useState } from "react";
import { sourceErrorMessage, type SourceItem } from "@/lib/source-api";
import { saveVerifiedSourceToKnowledge } from "@/lib/source-knowledge";
import { HomeCardDialog } from "../HomeCardDialog";
import { ManualSourceForm } from "../sources/ManualSourceForm";
import { Button } from "../ui/button";

export function WorkSourceImport() {
  const [open, setOpen] = useState(false);
  const [item, setItem] = useState<SourceItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function save() {
    if (!item || busy) return;
    setBusy(true);
    setError("");
    try {
      const saved = await saveVerifiedSourceToKnowledge(item.id);
      setNotice(saved.use_state === "available"
        ? `《${item.title}》已存入知识库，可重新核对制作方案。`
        : `《${item.title}》已按所选使用范围存入知识库。`);
      setItem(null);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : sourceErrorMessage(cause));
    } finally { setBusy(false); }
  }

  return <>
    <Button type="button" variant="outline" onClick={() => setOpen(true)}>补充文字资料</Button>
    {notice && <p role="status">{notice}</p>}
    {open && <HomeCardDialog title="补充文字资料" onClose={() => { if (!busy) setOpen(false); }}>
      {item ? <section>
        <h3>{item.title}</h3>
        <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.body || item.source_url || item.file_reference}</p>
        <Button type="button" disabled={busy} onClick={() => void save()}>保存到知识库</Button>
      </section> : <ManualSourceForm busy={busy} onStart={() => { setBusy(true); setError(""); setNotice(""); }}
        onCreated={setItem} onError={cause => setError(sourceErrorMessage(cause))} onEnd={() => setBusy(false)} />}
      {error && <p role="alert">{error}</p>}
    </HomeCardDialog>}
  </>;
}
