"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "../ui/button";
import { InputGroup, InputGroupInput } from "../ui/input-group";
import { AppSelect, AppSelectOption } from "../AppSelect";
import { useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";
import { ensureLocalMemberIdentity } from "@/lib/local-member-auth";
import { isLocalAuthMode } from "@/lib/auth/routing";
import { createClient } from "@/lib/supabase/client";
import { createSourceTask, getSourceTaskRequest } from "@/lib/api/creation-tasks";
import { CreationPlanApiError } from "@/lib/api/creation-transport";
import type { MaterialAsset } from "@/lib/types";
import type { SourceTaskCreateInput, SourceTaskReceipt } from "@/lib/types/creation-tasks";
import styles from "../MaterialLibraryControls.module.css";

function isOriginalInput(value: unknown, assetId: string): value is SourceTaskCreateInput {
  if (!value || typeof value !== "object") return false;
  const input = value as Record<string, unknown>;
  return typeof input.request_id === "string" && input.request_id.length > 0 && input.asset_id === assetId
    && typeof input.expected_asset_updated_at === "string" && typeof input.name === "string"
    && typeof input.source_start_seconds === "number" && Number.isFinite(input.source_start_seconds)
    && typeof input.source_end_seconds === "number" && Number.isFinite(input.source_end_seconds)
    && input.source_start_seconds >= 0 && input.source_end_seconds > input.source_start_seconds
    && ["9:16", "16:9", "1:1"].includes(String(input.aspect_ratio)) && typeof input.preserve_audio === "boolean";
}

export function SourceTaskForm({ asset, onCreated }: { asset: MaterialAsset; onCreated: (receipt: SourceTaskReceipt) => void }) {
  const identity = useLocalMemberIdentity();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(asset.file_name);
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState(asset.duration_seconds?.toString() ?? "");
  const [ratio, setRatio] = useState<SourceTaskCreateInput["aspect_ratio"]>("16:9");
  const [preserveAudio, setPreserveAudio] = useState(true);
  const [pending, setPending] = useState<SourceTaskCreateInput | null>(null);
  const [storageKey, setStorageKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
  const scope = useRef<string | null>(null);
  const operation = useRef<object | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true, revision = 0;
    async function restore() {
      const current = ++revision;
      scope.current = null; operation.current = null;
      setStorageKey(null); setPending(null); setBusy(false); setError(""); setNotFound(false);
      try {
        let memberId: string;
        if (isLocalAuthMode()) memberId = (await ensureLocalMemberIdentity()).member_id;
        else {
          const { data, error } = await createClient().auth.getSession();
          if (error || !data.session) throw new Error("请先登录原片创作所属的账号。");
          memberId = data.session.user.id;
        }
        if (!active || current !== revision) return;
        const key = `source-task:${asset.workspace_id}:${asset.id}:${memberId}`;
        const saved = localStorage.getItem(key);
        const input: unknown = saved ? JSON.parse(saved) : null;
        if (input !== null && !isOriginalInput(input, asset.id)) throw new Error("上次创建请求无法读取，请保留现场核对原结果。");
        scope.current = key; setStorageKey(key); setPending(input);
      } catch (cause) {
        if (active && current === revision) setError(cause instanceof Error ? cause.message : "无法读取原创建请求。");
      }
    }
    void restore();
    const subscription = !isLocalAuthMode()
      ? createClient().auth.onAuthStateChange(() => { void restore(); }).data.subscription : null;
    return () => { active = false; scope.current = null; operation.current = null; subscription?.unsubscribe(); };
  }, [open, asset.workspace_id, asset.id, identity.token]);

  async function run(input: SourceTaskCreateInput, query: boolean, firstAttempt = false) {
    const key = storageKey;
    if (!key || operation.current) return;
    const token = {};
    operation.current = token; setBusy(true); setError(""); setNotFound(false);
    try {
      const receipt = query ? await getSourceTaskRequest(input.request_id) : await createSourceTask(input);
      if (scope.current !== key || operation.current !== token) return;
      const source = receipt.source_media;
      if (receipt.request_id !== input.request_id || source.asset_id !== input.asset_id
        || source.source_start_seconds !== input.source_start_seconds || source.source_end_seconds !== input.source_end_seconds
        || source.aspect_ratio !== input.aspect_ratio || source.preserve_audio !== input.preserve_audio) {
        throw new Error("返回结果与原片范围或制作要求不一致，请继续核对原创建结果。");
      }
      localStorage.removeItem(key);
      setPending(null);
      onCreated(receipt);
    } catch (cause) {
      if (scope.current !== key || operation.current !== token) return;
      if (query && cause instanceof CreationPlanApiError && cause.status === 404) {
        setNotFound(true);
        setError("尚未查到原创建结果，可以继续提交同一次原片创作请求。");
      } else {
        const rejected = firstAttempt && cause instanceof CreationPlanApiError && [400, 401, 403, 422].includes(cause.status);
        if (rejected) { localStorage.removeItem(key); setPending(null); }
        setError(cause instanceof Error ? cause.message : "创建结果尚未核实，请查询原请求。");
      }
    } finally {
      if (operation.current === token) { operation.current = null; setBusy(false); }
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!storageKey || pending || operation.current) return;
    const from = Number(start), to = Number(end);
    if (!name.trim() || !Number.isFinite(from) || !Number.isFinite(to) || from < 0 || to <= from
      || asset.duration_seconds === null || to > asset.duration_seconds) {
      setError("请填写名称，并选择原视频真实时长内的有效起止时间。"); return;
    }
    const input: SourceTaskCreateInput = { request_id: crypto.randomUUID(), asset_id: asset.id,
      expected_asset_updated_at: asset.updated_at, source_start_seconds: from, source_end_seconds: to,
      name: name.trim(), aspect_ratio: ratio, preserve_audio: preserveAudio };
    try {
      const serialized = JSON.stringify(input);
      localStorage.setItem(storageKey, serialized);
      if (localStorage.getItem(storageKey) !== serialized) throw new Error("无法保留原创建请求，本次未提交。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法保留原请求，本次未提交。"); return; }
    setPending(input);
    void run(input, false, true);
  }

  return <section className={styles.notesEditor} aria-label="原片创作">
    <Button variant="outline" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? "收起原片创作" : "以原片创作"}
    </Button>
    {open ? <form onSubmit={submit} className={styles.notesEditor} aria-label="以原片创作">
      <p>从所选原片开始剪辑，保留原文件。</p>
      <label>作品名称<InputGroup><InputGroupInput value={pending?.name ?? name} disabled={Boolean(pending) || busy} onChange={event => setName(event.target.value)} required /></InputGroup></label>
      <label>起点（秒）<InputGroup><InputGroupInput type="number" min="0" step="0.001" value={pending?.source_start_seconds ?? start} disabled={Boolean(pending) || busy} onChange={event => setStart(event.target.value)} required /></InputGroup></label>
      <label>终点（秒）<InputGroup><InputGroupInput type="number" min="0" step="0.001" value={pending?.source_end_seconds ?? end} disabled={Boolean(pending) || busy} onChange={event => setEnd(event.target.value)} required /></InputGroup></label>
      <label>画幅<AppSelect value={pending?.aspect_ratio ?? ratio} disabled={Boolean(pending) || busy} onValueChange={value => setRatio(value as typeof ratio)} aria-label="原片创作画幅">
        <AppSelectOption value="16:9">横屏 16:9</AppSelectOption><AppSelectOption value="9:16">竖屏 9:16</AppSelectOption><AppSelectOption value="1:1">方形 1:1</AppSelectOption>
      </AppSelect></label>
      <label><input type="checkbox" checked={pending?.preserve_audio ?? preserveAudio} disabled={Boolean(pending) || busy} onChange={event => setPreserveAudio(event.target.checked)} />保留原声</label>
      {asset.duration_seconds === null ? <p role="status">原视频时长尚未就绪，请先检查素材状态。</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {pending ? <>
        <p role="status">上次创建结果等待核对，已保留原片范围和要求。</p>
        <Button variant="outline" disabled={busy || !storageKey} onClick={() => void run(pending, true)}>查询原创建结果</Button>
        {notFound ? <Button disabled={busy} onClick={() => void run(pending, false)}>继续原片创作请求</Button> : null}
      </> : <Button type="submit" disabled={busy || !storageKey || asset.duration_seconds === null}>创建剪辑任务</Button>}
      {busy ? <p role="status">正在核对原片创作结果…</p> : null}
    </form> : null}
  </section>;
}
