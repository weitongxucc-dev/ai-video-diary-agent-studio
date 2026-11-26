"use client";

import { useEffect, useId, useRef, useState } from "react";
import { getCreationTaskWorkspace, confirmCreationTaskEdit, undoCreationTaskTimeline } from "@/lib/api/creation-tasks";
import { creationPlanRequest } from "@/lib/api/creation-transport";
import { importCreationTaskResourceAudio, readCreationTaskResourceAudioRequest, readCreationTaskResourceAudioRequestId, previewCreationTaskAudio, resolveCreationTaskMediaResponse, getCreationTaskMediaJob } from "@/lib/api/task-media";
import { readPreflightDraftScope, validDraftId, type DraftScope } from "@/lib/preflight-draft";
import type { CreationTaskWorkspace, CreationTaskEditPreviewResponse, TaskMediaResultMap, TaskMediaJob } from "@/lib/types";
import styles from "../EditorResourceLibraryPanel.module.css";

type ResourceAudioHistory = {
  completeness: "complete" | "partial";
  current_resource_present: boolean | null;
  timeline_history: {
    timeline_version_id: string;
    sequence: number;
    transition: "added" | "retained" | "removed";
    operation_type: string;
    operation_id: string | null;
  }[];
};

type ImportRequest = { id: string } & (
  { state: "unknown" | "not_submitted" } | { state: "recorded"; jobId: string }
);

function importRequestKey(scope: DraftScope, taskId: string, resourceId: string, version: number) {
  const parts = [scope.workspaceId, scope.memberId, taskId, resourceId, String(version)];
  return `ai-video-diary.resource-audio-request.v1:${parts.map(encodeURIComponent).join(":")}`;
}

function readResourceHistory(taskId: string, resourceId: string, version: number, signal: AbortSignal) {
  return creationPlanRequest<ResourceAudioHistory>(
    `/api/creation-tasks/${encodeURIComponent(taskId)}/audio/resources/${encodeURIComponent(resourceId)}/history?version=${version}`,
    { signal },
  );
}

/** Uses the same task import, edit proposal and undo APIs as the audio editor. */
export function ResourceAudioAdoption({ taskId, resource, playheadMs, onTaskUpdated }: {
  taskId: string; resource: { id: string; version: number; name: string; category_id: string };
  playheadMs: number; onTaskUpdated?: () => void;
}) {
  const [workspace, setWorkspace] = useState<CreationTaskWorkspace | null>(null);
  const [trackId, setTrackId] = useState("");
  const [start, setStart] = useState(Math.max(0, playheadMs / 1000));
  const [gain, setGain] = useState(resource.category_id === "music" ? -18 : 0);
  const gainSliderId = useId();
  const [source, setSource] = useState<TaskMediaResultMap["audio_source"] | null>(null);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const [importRequest, setImportRequest] = useState<ImportRequest | null>(null);
  const [preview, setPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [applied, setApplied] = useState<{ id: string; seen?: { versionId: string; sequence: number } } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const lifetime = useRef<AbortController | null>(null);
  const undoRequest = useRef<Parameters<typeof undoCreationTaskTimeline>[1] | null>(null);
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller;
    void readPreflightDraftScope().then(scope => {
      if (controller.signal.aborted) return;
      const key = importRequestKey(scope, taskId, resource.id, resource.version);
      const id = sessionStorage.getItem(key);
      if (id !== null && !validDraftId(id)) throw new Error("原导入请求编号无法读取。");
      setImportRequest(id === null ? null : { id, state: "unknown" });
      setRequestKey(key);
    }).catch(() => {
      if (!controller.signal.aborted) setError("无法恢复当前成员的导入记录，请重新打开资源后核对。");
    });
    void getCreationTaskWorkspace(taskId).then(async value => {
      if (controller.signal.aborted) return;
      if (value.task.can_undo && value.task.current_timeline_version_id) {
        try {
          const history = await readResourceHistory(taskId, resource.id, resource.version, controller.signal);
          if (controller.signal.aborted) return;
          // A retained state cannot disambiguate repeated uses of the same catalog version.
          const adoption = history.completeness === "complete" && history.current_resource_present
            ? history.timeline_history.find(entry => entry.timeline_version_id === value.task.current_timeline_version_id
                && entry.sequence === value.task.timeline_sequence && entry.transition === "added"
                && entry.operation_type === "EditAudioTimeline")
            : undefined;
          if (adoption?.operation_id) {
            setApplied({ id: adoption.operation_id, seen: { versionId: adoption.timeline_version_id, sequence: adoption.sequence } });
            setNotice("已加入当前作品音轨。");
          }
        } catch {
          if (!controller.signal.aborted) setNotice("暂时无法读取此资源的采用记录。可在作品版本历史核对。");
        }
      }
      if (controller.signal.aborted) return;
      setWorkspace(value);
      const role = resource.category_id === "music" ? "music" : "effect";
      setTrackId(value.montage?.audio_timeline?.tracks.find(track => track.role === role && !track.locked)?.id ?? "");
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法读取当前作品"); });
    return () => controller.abort();
  }, [taskId, resource.category_id, resource.id, resource.version]);
  const tracks = workspace?.montage?.audio_timeline?.tracks.filter(track => !track.locked && ["music", "effect"].includes(track.role)) ?? [];
  const duration = workspace?.montage?.duration_seconds ?? 0;

  async function checkImportRequest(id: string, signal: AbortSignal) {
    let receipt: Awaited<ReturnType<typeof readCreationTaskResourceAudioRequest>> | undefined;
    try {
      receipt = await readCreationTaskResourceAudioRequest(taskId, id, signal);
    } catch {
      // A missing or unreadable receipt is still an unknown outcome.
    }
    if (signal.aborted) return;
    if (receipt) {
      if (receipt.request_id !== id || receipt.task_id !== taskId
          || receipt.submitted_input?.resource_id !== resource.id || receipt.submitted_input.version !== resource.version) {
        setError("原导入回执与当前资源或版本不一致，请核对原请求。");
        return;
      }
      if (receipt.status === "recorded" && receipt.job_id) {
        setImportRequest({ id, state: "recorded", jobId: receipt.job_id });
        setError(null);
        setNotice("原导入已登记，可读取原作业结果。");
        return;
      }
      if (receipt.status === "rejected" && receipt.submission_effect === "not_submitted") {
        setImportRequest({ id, state: "not_submitted" });
        setNotice("原导入未提交，可以重新尝试。");
        return;
      }
    }
    setNotice("原导入结果尚未确认。请继续查询原请求。");
  }

  async function verifyImportScope(key: string) {
    const scope = await readPreflightDraftScope();
    if (importRequestKey(scope, taskId, resource.id, resource.version) !== key) {
      throw new Error("成员或工作区已变化，请重新打开资源。");
    }
  }

  async function prepare() {
    if (!workspace || !requestKey || busy) return;
    const signal = lifetime.current!.signal;
    setBusy(true); setError(null); setNotice(""); setPreview(null);
    try {
      if (importRequest?.state !== "unknown") {
        const latest = await getCreationTaskWorkspace(taskId);
        if (signal.aborted) return;
        if (latest.task.current_timeline_version_id !== workspace.task.current_timeline_version_id) {
          setWorkspace(latest);
          throw new Error("作品已更新，请核对音轨和位置后重新准备。");
        }
      }
      await verifyImportScope(requestKey);
      if (signal.aborted) return;
      const savedId = sessionStorage.getItem(requestKey);
      if (savedId !== (importRequest?.id ?? null)) {
        if (savedId !== null && !validDraftId(savedId)) throw new Error("原导入请求编号无法读取。");
        if (savedId !== null) setImportRequest({ id: savedId, state: "unknown" });
        throw new Error("导入记录已变化，请重新核对。");
      }
      if (importRequest?.state === "unknown") {
        await checkImportRequest(importRequest.id, signal);
        return;
      }
      let audio = source;
      if (!audio) {
        let response: TaskMediaJob;
        if (importRequest?.state === "recorded") {
          response = await getCreationTaskMediaJob(taskId, importRequest.jobId, signal);
        } else {
          const retryOf = importRequest?.state === "not_submitted" ? importRequest.id : undefined;
          const attempt = await readCreationTaskResourceAudioRequestId(taskId, resource.id, resource.version, retryOf, signal);
          if (signal.aborted) return;
          await verifyImportScope(requestKey);
          if (signal.aborted) return;
          if (sessionStorage.getItem(requestKey) !== savedId) throw new Error("导入记录已变化，请重新核对。");
          const originalId = attempt.original_request_id;
          const id = originalId === null ? attempt.request_id : originalId;
          if (!validDraftId(id)) throw new Error("当前资源导入编号无法读取。");
          sessionStorage.setItem(requestKey, id);
          setImportRequest({ id, state: "unknown" });
          if (originalId !== null) {
            await checkImportRequest(id, signal);
            return;
          }
          try {
            response = await importCreationTaskResourceAudio(taskId, resource.id, resource.version, id, retryOf);
          } catch (cause) {
            if (signal.aborted) return;
            if (cause instanceof Error) setError(cause.message);
            await checkImportRequest(id, signal);
            return;
          }
          if (signal.aborted) return;
          setImportRequest({ id, state: "recorded", jobId: response.job_id });
        }
        audio = await resolveCreationTaskMediaResponse(taskId, response, "audio_source", {
          signal, baseVersionId: null,
          onUpdate: value => {
            if (!signal.aborted && (value.status === "queued" || value.status === "running")) setNotice("正在导入资源音频…");
          },
        });
        if (signal.aborted) return;
        setNotice("");
        setSource(audio);
      }
      const response = await previewCreationTaskAudio(taskId, {
        base_version_id: workspace.task.current_timeline_version_id, action: "add", target_id: trackId,
        raw_command: `添加${resource.name} v${resource.version}`,
        values: { source_ref: audio.source_ref, name: resource.name, source_start_ms: 0,
          source_end_ms: Math.min(audio.duration_ms, Math.round((duration - start) * 1000)),
          timeline_start_ms: Math.round(start * 1000), gain_db: gain },
      });
      const result = await resolveCreationTaskMediaResponse(taskId, response, "edit_preview", {
        signal, baseVersionId: workspace.task.current_timeline_version_id,
        onUpdate: value => { if (!signal.aborted) setNotice(`编辑准备：${value.status}`); },
      });
      if (!signal.aborted) setPreview(result);
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "资源尚未准备好"); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  async function confirm() {
    if (!preview || busy) return;
    const signal = lifetime.current!.signal;
    setBusy(true); setError(null);
    try {
      const result = await confirmCreationTaskEdit(taskId, preview.proposal.id);
      if (signal.aborted) return;
      setPreview(null);
      if (!("operation" in result)) {
        setNotice("编辑已进入任务队列，请在作品的运行记录中查看结果。");
      } else if (result.operation.status === "applied") {
        setApplied({ id: result.operation.id }); setNotice("已加入当前作品音轨。重新渲染后会进入成片。");
      } else {
        setNotice("编辑尚未采用，请在作品中检查运行结果。");
      }
      onTaskUpdated?.();
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "采用结果尚未确认，请核对作品版本"); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  async function undo() {
    if (!applied || busy) return;
    const signal = lifetime.current!.signal;
    setBusy(true); setError(null);
    try {
      let input = undoRequest.current;
      if (input?.target_operation_id !== applied.id) {
        const latest = await getCreationTaskWorkspace(taskId);
        if (signal.aborted) return;
        const { current_timeline_version_id: versionId, timeline_sequence: sequence } = latest.task;
        if (applied.seen && (versionId !== applied.seen.versionId || sequence !== applied.seen.sequence)) {
          setWorkspace(latest); setApplied(null); setPreview(null); setNotice("");
          throw new Error("作品已更新，请从作品版本历史核对后再撤销。");
        }
        input = { request_id: crypto.randomUUID(), target_operation_id: applied.id,
          expected_timeline_version_id: versionId, expected_timeline_sequence: sequence };
        undoRequest.current = input;
      }
      await undoCreationTaskTimeline(taskId, input);
      if (signal.aborted) return;
      undoRequest.current = null;
      setApplied(null); setPreview(null); setNotice("已撤销本次音轨采用，导入资源和历史版本仍保留。");
      setWorkspace(await getCreationTaskWorkspace(taskId)); onTaskUpdated?.();
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "撤销未完成"); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  const checkingOriginal = importRequest?.state === "unknown";
  const prepareLabel = importRequest?.state === "not_submitted" ? "重新尝试导入"
    : importRequest?.state === "recorded" && !source ? "读取导入结果" : "准备采用";
  return <section className={styles.catalogPreview} aria-label={`采用${resource.name}`}>
    <h4>添加到当前作品</h4>
    {workspace ? <p>{workspace.task.name}</p> : <p>正在读取作品…</p>}
    {workspace && !tracks.length ? <p>请先在当前作品建立可编辑的音乐或音效轨。</p> : null}
    {!applied || applied.seen ? <fieldset disabled={busy || !tracks.length} className={styles.adoptionFields} onChange={() => setPreview(null)}>
      <label>音轨<select value={trackId} onChange={event => setTrackId(event.target.value)}><option value="">选择音轨</option>{tracks.map(track => <option key={track.id} value={track.id}>{track.name}</option>)}</select></label>
      <label>起始位置（秒）<input type="number" min={0} max={duration} step="0.1" value={start} onChange={event => setStart(event.target.valueAsNumber)} /></label>
      <label htmlFor={gainSliderId}>音量调节（dB）<input id={gainSliderId} type="range" min={-60} max={12} step={1} value={Number.isFinite(gain) ? gain : 0} aria-valuetext={Number.isFinite(gain) ? `${gain} dB` : undefined} onChange={event => setGain(event.target.valueAsNumber)} /></label>
      <label>音量（dB）<input type="number" min={-60} max={12} value={gain} onChange={event => setGain(event.target.valueAsNumber)} /></label>
      <p>从资源开头加入，超过作品结尾的部分会裁掉。准备后确认采用，原旁白保留。</p>
      {!checkingOriginal ? <button type="button" disabled={!requestKey || !trackId || !Number.isFinite(start) || start < 0 || start >= duration || !Number.isFinite(gain) || gain < -60 || gain > 12} onClick={() => void prepare()}>{busy ? "正在准备…" : prepareLabel}</button> : null}
    </fieldset> : null}
    {checkingOriginal ? <button type="button" disabled={!workspace || busy} onClick={() => void prepare()}>{busy ? "正在处理…" : "查询原导入请求"}</button> : null}
    {preview ? <div><p>{preview.proposal.summary}</p><button type="button" disabled={busy} onClick={() => void confirm()}>确认加入音轨</button></div> : null}
    {applied ? <button type="button" disabled={busy} onClick={() => void undo()}>撤销本次采用</button> : null}
    <span role="status">{notice || null}</span>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
