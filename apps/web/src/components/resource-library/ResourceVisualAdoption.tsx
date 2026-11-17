"use client";

import { useEffect, useRef, useState } from "react";
import { getCreationTaskWorkspace, previewCreationTaskExactEdit, confirmCreationTaskEdit, undoCreationTaskTimeline } from "@/lib/api/creation-tasks";
import { resolveCreationTaskMediaResponse } from "@/lib/api/task-media";
import { readVideoTemplateVersion } from "@/lib/video-template-api";
import type { CreationTaskWorkspace, CreationTaskEditPreviewResponse, CreationTaskEditOperation, VideoTemplate } from "@/lib/types";
import type { CatalogResource } from "../EditorResourceLibraryPanel";
import { VisualTemplatePreview } from "../VisualTemplatePreview";
import styles from "../EditorResourceLibraryPanel.module.css";

async function readResourceTemplate(resource: CatalogResource) {
  const { template_id, template_version } = resource.runtime_requirements ?? {};
  if (typeof template_id !== "string" || typeof template_version !== "number") throw new Error("资源缺少固定模板版本");
  return readVideoTemplateVersion(template_id, template_version);
}

export function ResourceVisualPreview({ resource }: { resource: CatalogResource }) {
  const [template, setTemplate] = useState<VideoTemplate | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void readResourceTemplate(resource).then(value => { if (active) setTemplate(value); })
      .catch(cause => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [resource]);
  return template?.settings.visual ? <VisualTemplatePreview definition={template.settings.visual} />
    : <p role={error ? "alert" : "status"}>{error ?? "正在读取动画源码…"}</p>;
}

/** Packaged visual resources use ordinary versioned template layers and edits. */
export function ResourceVisualAdoption({ taskId, resource, playheadMs, onTaskUpdated }: {
  taskId: string; resource: CatalogResource; playheadMs: number; onTaskUpdated?: () => void;
}) {
  const isSticker = resource.resource_type === "svg_sticker";
  const defaults = resource.runtime_requirements ?? {};
  const [workspace, setWorkspace] = useState<CreationTaskWorkspace | null>(null);
  const [template, setTemplate] = useState<VideoTemplate | null>(null);
  const [start, setStart] = useState(Math.max(0, playheadMs / 1000));
  const [stickerDuration, setStickerDuration] = useState(resource.preview.duration_seconds ?? 3);
  const [width, setWidth] = useState(typeof defaults.width === "number" ? defaults.width : 1);
  const [x, setX] = useState(typeof defaults.x === "number" ? defaults.x : .5);
  const [y, setY] = useState(typeof defaults.y === "number" ? defaults.y : .5);
  const [preview, setPreview] = useState<CreationTaskEditPreviewResponse | null>(null);
  const [applied, setApplied] = useState<CreationTaskEditOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller;
    void Promise.all([getCreationTaskWorkspace(taskId), readResourceTemplate(resource)])
      .then(([work, value]) => { if (!controller.signal.aborted) { setWorkspace(work); setTemplate(value); } })
      .catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [taskId, resource]);
  const definition = template?.settings.visual;
  const duration = workspace?.montage?.duration_seconds ?? 0;
  const span = isSticker ? stickerDuration : definition?.duration_seconds ?? 0;
  const valid = !!definition && Number.isFinite(start) && start >= 0 && span >= .04 && start + span <= duration
    && width >= .05 && width <= 1 && x >= 0 && x <= 1 && y >= 0 && y <= 1;

  async function prepare() {
    if (!workspace || !template || !definition || !valid || busy) return;
    const signal = lifetime.current!.signal;
    setBusy(true); setError(null); setPreview(null); setNotice("");
    try {
      const latest = await getCreationTaskWorkspace(taskId);
      if (signal.aborted) return;
      if (latest.task.current_timeline_version_id !== workspace.task.current_timeline_version_id) {
        setWorkspace(latest);
        throw new Error("作品已更新，请核对位置后重新准备。");
      }
      const [w, h] = (workspace.montage?.aspect_ratio ?? workspace.plan.aspect_ratio).split(":").map(Number);
      const response = await previewCreationTaskExactEdit(taskId, {
        operation_type: "EditGraphicOverlay", overlay_action: "add", graphic_overlay: {
          kind: "template", id: crypto.randomUUID(), name: `${resource.name} · v${resource.version}`,
          template_id: template.id, template_version: template.version,
          definition: isSticker ? definition : { ...definition, width: 384, height: Math.round(384 * h / w) },
          start_seconds: start, end_seconds: start + span, x, y, width,
          opacity: 1, keyframes: [], z_index: 1000, animation_offset_seconds: 0,
        },
      }, { draftOnly: true });
      const result = await resolveCreationTaskMediaResponse(taskId, response, "edit_preview", {
        baseVersionId: workspace.task.current_timeline_version_id,
        signal, onUpdate: job => { if (!signal.aborted) setNotice(`编辑准备：${job.status}`); },
      });
      if (!signal.aborted) setPreview(result);
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "动画采用尚未准备好"); }
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
      if ("operation" in result && result.operation.status === "applied") {
        setApplied(result.operation); setNotice(`已加入${isSticker ? "贴纸" : "动画"}图层。重新渲染后会进入成片，可从图文与动画中调整或撤销。`);
      } else setNotice("采用请求已提交，请在作品运行记录中回查实际结果。");
      onTaskUpdated?.();
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "采用结果未知，请回查原记录"); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  async function undo() {
    if (!applied || busy) return;
    const signal = lifetime.current!.signal;
    setBusy(true); setError(null);
    try {
      const latest = await getCreationTaskWorkspace(taskId);
      if (signal.aborted) return;
      await undoCreationTaskTimeline(taskId, { request_id: crypto.randomUUID(), target_operation_id: applied.id,
        expected_timeline_version_id: latest.task.current_timeline_version_id,
        expected_timeline_sequence: latest.task.timeline_sequence });
      if (signal.aborted) return;
      const restored = await getCreationTaskWorkspace(taskId);
      if (signal.aborted) return;
      setApplied(null); setWorkspace(restored);
      setNotice(`已撤销本次${isSticker ? "贴纸" : "动画"}采用，历史版本仍保留。`); onTaskUpdated?.();
    } catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "撤销未完成"); }
    finally { if (!signal.aborted) setBusy(false); }
  }
  return <section className={styles.catalogPreview} aria-label={`采用${resource.name}`}>
    {!applied ? <fieldset className={styles.adoptionFields} disabled={busy || !definition || !workspace} onChange={() => setPreview(null)}>
      <legend>添加到当前作品</legend>
      <label>开始位置（秒）<input type="number" min={0} max={Math.max(0, duration - span)} step="0.01" value={start}
        onChange={event => { setStart(event.target.valueAsNumber); setPreview(null); }} /></label>
      {isSticker ? <>
        <label>显示时长（秒）<input type="number" min={.1} max={Math.max(0, duration - start)} step="0.1" value={stickerDuration} onChange={event => setStickerDuration(event.target.valueAsNumber)} /></label>
        <label>宽度（画面 %）<input type="number" min={5} max={100} value={Number((width * 100).toFixed(2))} onChange={event => setWidth(event.target.valueAsNumber / 100)} /></label>
        <label>水平位置（%）<input type="number" min={0} max={100} value={Number((x * 100).toFixed(2))} onChange={event => setX(event.target.valueAsNumber / 100)} /></label>
        <label>垂直位置（%）<input type="number" min={0} max={100} value={Number((y * 100).toFixed(2))} onChange={event => setY(event.target.valueAsNumber / 100)} /></label>
        <p>贴纸保持原比例。位置表示贴纸中心，从画面左侧、顶部开始计算；采用后仍可在图文与动画中调整。</p>
      </> : <p>原始信号故障动画持续 {span || "0.6"} 秒，覆盖画面；放在切点附近，原镜头与声音保留。</p>}
      {!valid && definition ? <p>{isSticker ? "请检查贴纸时长、位置和宽度，显示区间不能超出作品。" : `请选取能完整放下 ${span} 秒动画的位置。`}</p> : null}
      <button type="button" disabled={!valid} onClick={() => void prepare()}>{busy ? "正在准备…" : "准备采用"}</button>
    </fieldset> : null}
    {preview ? <div><p>{preview.proposal.summary}</p><button type="button" disabled={busy} onClick={() => void confirm()}>确认加入{isSticker ? "贴纸" : "动画"}</button></div> : null}
    {applied ? <button type="button" disabled={busy} onClick={() => void undo()}>撤销本次采用</button> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
