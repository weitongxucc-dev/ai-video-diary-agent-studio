"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { CreationPlanApiError } from "@/lib/api/creation-transport";
import { getDevicePreparation, getDeviceResourceStatus } from "@/lib/api/device-preparation";
import {
  cancelCreationTaskMediaJob, getCreationTaskMediaJob, readSourceClipCutOutput, requireSourceClipCutJob,
  requireSourceClipCutResult, resolveCreationTaskMediaResponse, retryCreationTaskMediaJob, submitSourceClipCut,
  taskMediaJobLabel, taskMediaResourceCheckLabel, TaskMediaJobError, type SourceClipCutInput,
} from "@/lib/api/task-media";
import { ApiRequestError } from "@/lib/api/transport";
import type { TaskMediaJob } from "@/lib/types";
import { Button } from "../ui/button";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { errorDetails } from "./request-errors";
import styles from "./SourceClipCut.module.css";

export type SourceClipCutTarget = {
  taskId: string;
  baseVersionId: string;
  baseSequence: number;
  assetId: string;
  clipNumber: number;
  sourceName: string;
  start: number;
  end: number;
};

type PublishedCut = { id: string; sequence: number };
type Attempt = {
  input: SourceClipCutInput;
  sourceName: string;
  clipNumber: number;
  baseSequence: number;
  jobId?: string;
  observedPublication?: PublishedCut;
};
const preparationId = /^[0-9a-f]{32}$/;
const requestId = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const validRange = (start: number, end: number) => Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start;
const storageKey = (scope: string, taskId: string) => `yaohe:source-clip-cut:v1:${scope}:${taskId}`;

function readAttempt(key: string): Attempt | null {
  const value = localStorage.getItem(key);
  if (!value) return null;
  const saved = JSON.parse(value) as Attempt;
  if (!saved?.input || !requestId.test(saved.input.request_id) || !preparationId.test(saved.input.preparation_request_id)
      || !saved.input.base_version_id || !saved.input.asset_id || !validRange(saved.input.start_seconds, saved.input.end_seconds)
      || typeof saved.sourceName !== "string" || !Number.isSafeInteger(saved.clipNumber) || !Number.isSafeInteger(saved.baseSequence)
      || (saved.jobId !== undefined && (typeof saved.jobId !== "string" || !saved.jobId))) {
    throw new Error("无法核对本机保存的原截取请求。");
  }
  const publication = saved.observedPublication;
  if (publication !== undefined && (!saved.jobId || typeof publication?.id !== "string" || !publication.id
      || publication.id === saved.input.base_version_id || !Number.isSafeInteger(publication.sequence)
      || publication.sequence <= saved.baseSequence)) {
    throw new Error("无法核对本机保存的截取发布记录。");
  }
  return saved;
}

function failureMessage(cause: unknown) {
  if (cause instanceof ApiRequestError) {
    try {
      const detail = JSON.parse(cause.message).detail;
      if (typeof detail === "string") return detail;
    } catch { /* Retain the original service message. */ }
  }
  const details = errorDetails(cause);
  return [details.message, details.repairAction].filter(Boolean).join(" ");
}

function cutStatus(job: TaskMediaJob) {
  if (job.status === "queued") return taskMediaJobLabel(job);
  if (job.status === "running") return "正在截取并保存 MP4…";
  if (job.status === "interrupted") return "截取已中断，等待原作业恢复。";
  if (job.status === "cancelled") return "原截取作业已取消。";
  if (job.status === "stale") return "原版本、素材或准备回执已失效。";
  if (job.status === "failed") return job.failure_reason === "result_invalid" ? "原截取文件已丢失或发生变化。" : "截取未完成。";
  return "截取作业已完成，正在核对登记版本。";
}

function CutOutput({ taskId, jobId, clipNumber }: { taskId: string; jobId: string; clipNumber: number }) {
  const [opened, setOpened] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    let output: string | undefined;
    void readSourceClipCutOutput(taskId, jobId, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      output = URL.createObjectURL(blob);
      setUrl(output);
    }).catch(cause => { if (!controller.signal.aborted) setFailure(failureMessage(cause)); });
    return () => { controller.abort(); if (output) URL.revokeObjectURL(output); };
  }, [opened, taskId, jobId]);
  return <div className={styles.form}>
    <Button variant="outline" type="button" aria-expanded={opened} onClick={() => {
      setUrl(null); setFailure(null); setOpened(value => !value);
    }}>{opened ? "收起截取文件" : "打开截取文件"}</Button>
    {opened && failure ? <p role="alert">{failure}</p> : opened && !url ? <p role="status">正在读取原 MP4…</p> : null}
    {opened && url ? <>
      <WorkspaceVideo aria-label="已保存的截取片段" controls playsInline preload="none" src={url}
        width={640} height={360} className={styles.video} onError={() => setFailure("截取文件无法播放。")} />
      <a href={url} download={`镜头-${clipNumber}-截取.mp4`}>下载 MP4</a>
    </> : null}
  </div>;
}

export function SourceClipCut({ target, currentVersionId, onJob, onPublished }: {
  target: SourceClipCutTarget;
  currentVersionId: string | null;
  onJob: (job: TaskMediaJob) => void;
  onPublished: (versionId: string, signal: AbortSignal) => Promise<number | null>;
}) {
  const { taskId, baseVersionId } = target;
  const [scope, setScope] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [job, setJob] = useState<TaskMediaJob | null>(null);
  const [savedVersion, setSavedVersion] = useState<PublishedCut | null>(null);
  const [readRevision, setReadRevision] = useState(0);
  const [scopeRevision, setScopeRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [start, setStart] = useState(String(target.start));
  const [end, setEnd] = useState(String(target.end));
  const [receiptInput, setReceiptInput] = useState("");
  const [verifiedId, setVerifiedId] = useState<string | null>(null);
  const warningId = useId();
  const interaction = useRef(false);
  const lifetime = useRef<AbortSignal | null>(null);
  const callbacks = useRef({ onJob, onPublished });
  callbacks.current = { onJob, onPublished };

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller.signal;
    void getDeviceResourceStatus(controller.signal).then(current => {
      if (controller.signal.aborted) return;
      setAttempt(readAttempt(storageKey(current.actor_scope, taskId)));
      setScope(current.actor_scope);
    }).catch(cause => { if (!controller.signal.aborted) setFailure(failureMessage(cause)); });
    return () => controller.abort();
  }, [taskId, scopeRevision]);

  const jobId = attempt?.jobId;
  const originalInput = attempt?.input;
  const originalSequence = attempt?.baseSequence;
  useEffect(() => {
    if (!jobId || !scope || !originalInput || originalSequence === undefined) return;
    const controller = new AbortController();
    const signal = controller.signal;
    setSavedVersion(null);
    const read = async () => {
      try {
        const response = await getCreationTaskMediaJob(taskId, jobId, signal);
        const result = await resolveCreationTaskMediaResponse(taskId, response, "media_output", {
          signal, baseVersionId: originalInput.base_version_id,
          onUpdate: value => {
            requireSourceClipCutJob(taskId, value, originalInput.base_version_id, jobId);
            setJob(value);
            callbacks.current.onJob(value);
          },
        });
        const output = requireSourceClipCutResult(taskId, jobId, originalInput, result);
        const current = await getDeviceResourceStatus(signal);
        if (signal.aborted) return;
        if (current.actor_scope !== scope) throw new Error("当前设备或成员已变化，请关闭后重新打开截取。");
        const sequence = await callbacks.current.onPublished(output.timeline_version_id, signal);
        if (signal.aborted) return;
        if (sequence === null || sequence <= originalSequence) throw new Error("截取回执已返回，当前作品版本与登记结果不一致。");
        const observedPublication = { id: output.timeline_version_id, sequence };
        setSavedVersion(observedPublication);
        // Retain the observed terminal result for dismissing an old receipt.
        // Opening bytes still requires today's authorized result and version.
        const key = storageKey(scope, taskId);
        const persisted = readAttempt(key);
        if (persisted?.jobId === jobId && persisted.input.request_id === originalInput.request_id) {
          localStorage.setItem(key, JSON.stringify({ ...persisted, observedPublication }));
          setAttempt(current => current?.jobId === jobId ? { ...current, observedPublication } : current);
        }
      } catch (cause) {
        if (!signal.aborted && !(cause instanceof TaskMediaJobError)) setFailure(failureMessage(cause));
      }
    };
    void read();
    return () => controller.abort();
  }, [originalInput, originalSequence, jobId, taskId, scope, readRevision]);

  async function run(action: (scope: string, signal: AbortSignal) => Promise<void>) {
    const signal = lifetime.current;
    if (!scope || !signal || signal.aborted || interaction.current) return;
    interaction.current = true;
    setBusy(true);
    setFailure(null);
    try {
      const current = await getDeviceResourceStatus(signal);
      signal.throwIfAborted();
      if (current.actor_scope !== scope) throw new Error("当前设备或成员已变化，请关闭后重新打开截取。");
      await action(scope, signal);
    } catch (cause) {
      if (!signal.aborted) setFailure(failureMessage(cause));
    } finally {
      interaction.current = false;
      if (!signal.aborted) setBusy(false);
    }
  }

  function checkPreparation() {
    const id = receiptInput.trim();
    setVerifiedId(null);
    void run(async (actorScope, signal) => {
      if (!preparationId.test(id)) throw new Error("请粘贴设置中复制的 32 位截取准备回执编号。");
      const receipt = await getDevicePreparation(id, actorScope, signal);
      signal.throwIfAborted();
      if (receipt.state !== "done") throw new Error("这次准备尚未完成，请在设置中核对原回执。");
      const measurement = "mp4_cut_measurement" in receipt ? receipt.mp4_cut_measurement : undefined;
      if (measurement !== "recorded") {
        throw new Error(measurement === "not_requested" ? "这次准备未选择 MP4 截取测量。" : "这份回执的截取测量状态未知，请在设置核对。");
      }
      setVerifiedId(receipt.id);
    });
  }

  async function submit(original: Attempt, actorScope: string, signal: AbortSignal) {
    const key = storageKey(actorScope, taskId);
    const existing = readAttempt(key);
    if (existing?.jobId || existing && existing.input.request_id !== original.input.request_id) {
      setAttempt(existing);
      return;
    }
    // Save before any write so a lost response or a reopened workbench can use the exact same input.
    localStorage.setItem(key, JSON.stringify(original));
    setAttempt(original);
    try {
      const value = await submitSourceClipCut(taskId, original.input);
      const received = { ...original, jobId: value.job_id };
      // The request can finish after this dialog has closed.
      if (readAttempt(key)?.input.request_id === original.input.request_id) {
        localStorage.setItem(key, JSON.stringify(received));
      }
      if (!signal.aborted) {
        setJob(value);
        setAttempt(received);
        callbacks.current.onJob(value);
      }
    } catch (cause) {
      // First-send admission rejections precede submission. An earlier unobserved
      // response may already have committed, so keep its original request.
      if (!existing && cause instanceof CreationPlanApiError
          && ([403, 409, 422].includes(cause.status) || cause.submissionEffect === "not_submitted")) {
        const pending = readAttempt(key);
        if (pending?.input.request_id === original.input.request_id && !pending.jobId) {
          localStorage.removeItem(key);
          if (!signal.aborted) setAttempt(null);
        }
      }
      throw cause;
    }
  }

  const boundReceipt = verifiedId !== null && verifiedId === receiptInput.trim();
  const startValue = Number(start);
  const endValue = Number(end);
  const inRange = !!start && !!end && validRange(startValue, endValue) && startValue >= target.start && endValue <= target.end;
  const current = currentVersionId === baseVersionId;
  function startCut(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!inRange || !current || !boundReceipt || attempt || !verifiedId) return;
    const original: Attempt = { sourceName: target.sourceName, clipNumber: target.clipNumber, baseSequence: target.baseSequence,
      input: { request_id: crypto.randomUUID(), base_version_id: baseVersionId, asset_id: target.assetId,
        start_seconds: startValue, end_seconds: endValue, preparation_request_id: verifiedId } };
    void run((actorScope, signal) => submit(original, actorScope, signal));
  }

  const taskBusy = job && ["queued", "running", "interrupted"].includes(job.status);
  const originalRetry = job && ["failed", "cancelled"].includes(job.status) && job.failure_reason !== "result_invalid"
    && currentVersionId === job.base_version_id;
  const savedCurrent = savedVersion?.id === currentVersionId;
  const observedPublication = attempt?.observedPublication;
  const previousPublication = current && observedPublication && target.baseSequence > observedPublication.sequence
    ? observedPublication : undefined;
  const canStartAnother = current && (savedVersion || previousPublication
    || job?.status === "failed" || job?.status === "cancelled");
  const selected = attempt ?? target;
  return <div className={styles.form}>
    <p>镜头 {selected.clipNumber} · {selected.sourceName} · 作品第 {selected.baseSequence} 版</p>
    {!scope && !failure ? <p role="status">正在核对设备和成员…</p> : null}
    {!scope && failure ? <Button type="button" variant="outline" onClick={() => {
      setFailure(null); setScopeRevision(value => value + 1);
    }}>重新读取截取记录</Button> : null}
    {attempt ? <>
      <p>原片 {attempt.input.start_seconds}–{attempt.input.end_seconds} 秒</p>
      <p role="status">{savedVersion ? `MP4 已保存到作品，登记于第 ${savedVersion.sequence} 版。`
        : previousPublication ? `原截取已登记于第 ${previousPublication.sequence} 版，当前作品版本已变化。`
        : job ? cutStatus(job) : attempt.jobId ? "正在读取原截取作业…" : "原请求尚未取得队列回执。"}</p>
      {job?.status === "failed" && job.failure_reason && job.failure_reason !== "result_invalid" ? <p>原因：{job.failure_reason}</p> : null}
      {job?.repair_action ? <p>{job.repair_action}</p> : null}
      {job?.resource_wait ? <details><summary>设备等待条件</summary><p>{job.resource_wait.summary}</p>
        <ul>{job.resource_wait.checks.map(check => <li key={check.metric}>{taskMediaResourceCheckLabel(check)}</li>)}</ul>
      </details> : null}
      {savedVersion && !savedCurrent ? <p>作品当前版本已变化，请从历史核对原登记版本。</p> : null}
      {savedCurrent && attempt.jobId ? <CutOutput taskId={taskId} jobId={attempt.jobId} clipNumber={attempt.clipNumber} /> : null}
      <div className={styles.actions}>
        {!attempt.jobId ? <Button variant="outline" disabled={busy} onClick={() => void run((actorScope, signal) => submit(attempt, actorScope, signal))}>核对原提交</Button> : null}
        {attempt.jobId && failure ? <Button variant="outline" disabled={busy} onClick={() => {
          setFailure(null); setReadRevision(value => value + 1);
        }}>重读原作业</Button> : null}
        {taskBusy ? <Button variant="outline" disabled={busy} onClick={() => void run(async (_, signal) => {
          await cancelCreationTaskMediaJob(taskId, job.job_id);
          if (!signal.aborted) setReadRevision(value => value + 1);
        })}>取消截取</Button> : null}
        {originalRetry ? <Button variant="outline" disabled={busy} onClick={() => void run(async (_, signal) => {
          await retryCreationTaskMediaJob(taskId, job.job_id);
          if (!signal.aborted) setReadRevision(value => value + 1);
        })}>重试原作业</Button> : null}
        {canStartAnother ? <Button variant="ghost" disabled={busy} onClick={() => void run(async actorScope => {
          const key = storageKey(actorScope, taskId);
          if (readAttempt(key)?.input.request_id !== attempt.input.request_id) {
            throw new Error("另一窗口已更新原截取记录，请关闭后重新打开。");
          }
          localStorage.removeItem(key);
          setAttempt(null); setJob(null); setSavedVersion(null); setVerifiedId(null);
        })}>截取另一段</Button> : null}
      </div>
    </> : <form className={styles.form} onSubmit={startCut}>
      <p>从镜头绑定的原素材截出一段有声 MP4，保存到当前作品。</p>
      <div className={styles.fields}>
        <label>原片起点（秒）<input type="number" required min={target.start} max={target.end} step="any" value={start}
          disabled={busy} onChange={event => setStart(event.target.value)} /></label>
        <label>原片终点（秒）<input type="number" required min={target.start} max={target.end} step="any" value={end}
          disabled={busy} onChange={event => setEnd(event.target.value)} /></label>
      </div>
      <label>截取准备回执编号<input value={receiptInput} required autoComplete="off" spellCheck={false} disabled={busy}
        aria-describedby={warningId} onChange={event => { setReceiptInput(event.target.value); setVerifiedId(null); }} /></label>
      <p id={warningId}>从<Link href="/settings?section=runtime">设置 → 高级诊断 → 基础媒体准备</Link>复制编号。</p>
      {boundReceipt ? <p role="status">已核对这份准备回执。提交时会复查素材、工具和额度。</p> : null}
      {!current ? <p role="status">作品版本已变化，请关闭后重新选择镜头。</p> : null}
      <div className={styles.actions}>
        <Button type="button" variant="outline" disabled={busy || !scope || !current} onClick={checkPreparation}>核对回执</Button>
        <Button type="submit" disabled={!scope || busy || !current || !boundReceipt || !inRange}>{busy && boundReceipt ? "正在提交…" : "截取并保存"}</Button>
      </div>
    </form>}
    {failure ? <p role="alert" className={styles.error}>{failure}</p> : null}
  </div>;
}
