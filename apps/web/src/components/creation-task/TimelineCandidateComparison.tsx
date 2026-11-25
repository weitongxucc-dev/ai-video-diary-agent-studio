"use client";

import { useContext, useEffect, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from "react";
import {
  cancelTimelineCandidatePreview, forkTimelineCandidate, listTimelineCandidates, readLatestTimelineCandidatePreview,
  readTimelineCandidate, readTimelineCandidatePreview, readTimelineCandidatePreviewVideo,
  saveTimelineCandidateCrop, submitTimelineCandidatePreview,
  type TimelineCandidateDetail, type TimelineCandidatePreview, type TimelineCandidateSummary,
} from "@/lib/api/timeline-candidates";
import { Button } from "../ui/button";
import { MediaVisibilityContext } from "../ui/media-visibility";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import styles from "./TimelineCandidateComparison.module.css";

export type CandidateComparisonTarget = {
  taskId: string;
  clipId: string;
  clipNumber: number;
  base: { id: string; sequence: number };
};

type Side = "A" | "B";

function message(error: unknown) {
  return error instanceof Error ? error.message : "候选操作失败，请重新读取后再试。";
}

function pending(preview: TimelineCandidatePreview | null) {
  return preview?.status === "queued" || preview?.status === "running" || preview?.status === "interrupted";
}

function previewStatus(preview: TimelineCandidatePreview) {
  if (preview.waiting_reason === "declared_resources_exceed_budget") return "此镜头超出当前设备预览档的资源上限。";
  if (preview.waiting_reason === "resource_unknown") return "暂时无法核实设备资源，预览仍在等待。";
  if (preview.waiting_reason === "resource_busy") return "当前设备资源尚未满足要求，预览仍在等待。";
  switch (preview.status) {
    case "queued": return "等待生成短预览。";
    case "interrupted": return "预览已中断，等待恢复。";
    case "running": return "正在生成镜头静音预览。";
    case "succeeded": return `作品 ${preview.timeline_start_seconds.toFixed(2)}–${preview.timeline_end_seconds.toFixed(2)} 秒 · 原片 ${preview.source_start_seconds.toFixed(2)}–${preview.source_end_seconds.toFixed(2)} 秒`;
    case "cancelled": return "预览请求已取消。";
    case "stale": return "此预览已过期，请读取候选当前版本。";
    case "failed": return preview.failure_reason === "result_invalid" ? "原预览文件已失效。" : "短预览生成失败。";
  }
}

function CandidateVideo({ side, preview, target }: {
  side: Side; preview: TimelineCandidatePreview; target: { taskId: string; candidateId: string };
}) {
  const visible = useContext(MediaVisibilityContext);
  const [source, setSource] = useState<{ jobId: string; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { taskId, candidateId } = target;
  useEffect(() => {
    setSource(null);
    setError(null);
    if (!visible) return;
    const controller = new AbortController();
    let url: string | undefined;
    void readTimelineCandidatePreviewVideo({ taskId, candidateId }, preview, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setSource({ jobId: preview.job_id, url });
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(message(cause));
    });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [taskId, candidateId, preview, visible]);
  if (error) return <p className={styles.error} role="alert">{error}</p>;
  if (!source || source.jobId !== preview.job_id) return <p role="status">正在读取预览画面…</p>;
  return <WorkspaceVideo className={styles.video} src={source.url} width={preview.width_px} height={preview.height_px}
    controls playsInline muted preload="none" aria-label={`候选 ${side} 的镜头静音预览`}
    onError={() => setError("无法播放此候选画面，请重新打开预览。")} />;
}

function CandidateEdit({ side, taskId, candidateId, clipId, openJobId, onVideo }: {
  side: Side; taskId: string; candidateId: string; clipId: string;
  openJobId: string | null; onVideo: Dispatch<SetStateAction<string | null>>;
}) {
  const visible = useContext(MediaVisibilityContext);
  const [detail, setDetail] = useState<TimelineCandidateDetail | null>(null);
  const [preview, setPreview] = useState<TimelineCandidatePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [readingPreview, setReadingPreview] = useState(true);
  const [action, setAction] = useState<"save" | "preview" | "cancel" | null>(null);
  const [saved, setSaved] = useState(false);
  const [dirtyVersion, setDirtyVersion] = useState<string | null>(null);
  const request = useRef<{ id: string; key: string } | null>(null);
  const target = { taskId, candidateId };

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    void readTimelineCandidate({ taskId, candidateId }, controller.signal).then((candidate) => {
      if (!controller.signal.aborted) setDetail(candidate);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(message(cause));
    });
    return () => controller.abort();
  }, [taskId, candidateId, reload, visible]);

  useEffect(() => {
    if (!visible || !detail) return;
    const controller = new AbortController();
    setPreview(null);
    setReadingPreview(true);
    void readLatestTimelineCandidatePreview({ taskId, candidateId }, detail.version, clipId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setPreview(result); })
      .catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); })
      .finally(() => { if (!controller.signal.aborted) setReadingPreview(false); });
    return () => controller.abort();
  }, [taskId, candidateId, detail, clipId, reload, visible]);

  useEffect(() => {
    if (!visible || !preview || !pending(preview) || action) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void readTimelineCandidatePreview({ taskId, candidateId }, preview, controller.signal)
        .then((result) => { if (!controller.signal.aborted) setPreview(result); })
        .catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    }, 1500);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [taskId, candidateId, preview, visible, action]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || action) return;
    const fields = new FormData(event.currentTarget);
    const cropX = Number(fields.get("crop-x")) / 100;
    const cropY = Number(fields.get("crop-y")) / 100;
    const fit = fields.get("fit") === "fit" ? "fit" : "fill";
    const key = `${detail.head_version_id}:${fit}:${cropX}:${cropY}`;
    const id = request.current?.key === key ? request.current.id : crypto.randomUUID();
    request.current = { key, id };
    setAction("save");
    setSaved(false);
    setError(null);
    try {
      const result = await saveTimelineCandidateCrop(target, detail, {
        operation_type: "ChangeCrop", clip_id: clipId, fit_mode: fit, crop_x: cropX, crop_y: cropY,
      }, id);
      const current = result.headVersionId === result.savedVersion.id
        ? { ...detail, head_version_id: result.headVersionId, version: result.savedVersion }
        : await readTimelineCandidate(target);
      onVideo((active) => active === preview?.job_id ? null : active);
      setPreview(null);
      setDetail(current);
      setSaved(current.head_version_id === result.savedVersion.id);
      if (current.head_version_id !== result.savedVersion.id) setError("候选已有更新，已读取最新版本。");
      request.current = null;
    } catch (cause) {
      setError(message(cause));
    } finally {
      setAction(null);
    }
  }

  async function startPreview() {
    if (!detail || action) return;
    const key = `preview:${detail.head_version_id}:${clipId}`;
    const id = request.current?.key === key ? request.current.id : crypto.randomUUID();
    request.current = { key, id };
    setAction("preview");
    setError(null);
    try {
      const result = await submitTimelineCandidatePreview(target, detail, clipId, id);
      onVideo((active) => active === preview?.job_id ? null : active);
      setPreview(result);
      request.current = null;
    } catch (cause) {
      setError(message(cause));
    } finally {
      setAction(null);
    }
  }

  async function cancelPreview() {
    if (!preview || action) return;
    setAction("cancel");
    setError(null);
    try {
      setPreview(await cancelTimelineCandidatePreview(target, preview));
    } catch (cause) {
      setError(message(cause));
    } finally {
      setAction(null);
    }
  }

  const clip = detail?.version.clips.find((item) => item.id === clipId);
  const dirty = dirtyVersion === detail?.version.id;
  return <>
    {error ? <div role="alert" className={styles.error}>{error}{" "}
      <Button variant="ghost" onClick={() => { setError(null); setReload((value) => value + 1); }}>重新读取</Button>
    </div> : null}
    {!detail && !error ? <p role="status">正在读取候选…</p> : null}
    {detail && !clip ? <p role="status">这个候选中没有所选镜头。</p> : null}
    {detail && clip ? <>
      <p className={styles.meta}>候选版本 #{detail.version.sequence}{saved ? " · 已保存" : ""}</p>
      <form key={detail.version.id} className={styles.form} onSubmit={(event) => void save(event)} onChange={(event) => {
        const values = new FormData(event.currentTarget);
        const changed = values.get("fit") !== (clip.fit_mode ?? "fill")
          || Number(values.get("crop-x")) !== (clip.crop_x ?? .5) * 100
          || Number(values.get("crop-y")) !== (clip.crop_y ?? .5) * 100;
        setDirtyVersion(changed ? detail.version.id : null);
      }}>
        <label>画面适配<select name="fit" defaultValue={clip.fit_mode ?? "fill"}>
          <option value="fill">填满</option><option value="fit">适应</option>
        </select></label>
        <div className={styles.fields}>
          <label>水平位置（%）<input name="crop-x" type="number" required min="0" max="100" step="any" defaultValue={(clip.crop_x ?? .5) * 100} /></label>
          <label>垂直位置（%）<input name="crop-y" type="number" required min="0" max="100" step="any" defaultValue={(clip.crop_y ?? .5) * 100} /></label>
        </div>
        <Button variant="outline" type="submit" disabled={action !== null || !dirty}>{action === "save" ? "正在保存…" : "保存候选裁切"}</Button>
      </form>
      <div className={styles.actions}>
        {preview?.status !== "succeeded" && !pending(preview)
          ? <Button type="button" disabled={action !== null || dirty || readingPreview} onClick={() => void startPreview()}>
            {action === "preview" ? "正在提交…" : "生成静音预览"}
          </Button> : null}
        {preview && pending(preview) ? <Button variant="outline" disabled={action !== null} onClick={() => void cancelPreview()}>取消预览请求</Button> : null}
        {preview?.status === "succeeded" ? <Button variant="outline" aria-expanded={openJobId === preview.job_id}
          onClick={() => onVideo(openJobId === preview.job_id ? null : preview.job_id)}>
          {openJobId === preview.job_id ? "收起画面" : "打开预览画面"}
        </Button> : null}
      </div>
      {preview ? <p className={styles.meta} role="status">{previewStatus(preview)}</p> : null}
      {preview?.status === "succeeded" && openJobId === preview.job_id
        ? <CandidateVideo key={preview.job_id} side={side} target={target} preview={preview} /> : null}
    </> : null}
  </>;
}

export function TimelineCandidateComparison({ taskId, clipId, clipNumber, base }: CandidateComparisonTarget) {
  const visible = useContext(MediaVisibilityContext);
  const [items, setItems] = useState<TimelineCandidateSummary[] | null>(null);
  const [selected, setSelected] = useState<Record<Side, string>>({ A: "", B: "" });
  const [error, setError] = useState<string | null>(null);
  const [forking, setForking] = useState<Side | null>(null);
  const [openJobId, setOpenJobId] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const ids = useRef<Partial<Record<Side, string>>>({});

  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    void listTimelineCandidates(taskId, controller.signal).then((candidates) => {
      if (controller.signal.aborted) return;
      setItems(candidates);
      setSelected((prior) => {
        if (prior.A || prior.B) return prior;
        const first = candidates.find((item) => item.base_version_id === base.id) ?? candidates[0];
        const second = first && candidates.find((item) => item.id !== first.id && item.base_version_id === first.base_version_id);
        return { A: first?.id ?? "", B: second?.id ?? "" };
      });
    }).catch((cause: unknown) => { if (!controller.signal.aborted) setError(message(cause)); });
    return () => controller.abort();
  }, [taskId, base.id, reload, visible]);

  async function fork(side: Side) {
    if (forking) return;
    const id = ids.current[side] ?? crypto.randomUUID();
    ids.current[side] = id;
    setForking(side);
    setError(null);
    try {
      const candidate = await forkTimelineCandidate({ taskId, candidateId: id }, base);
      setItems((current) => [...(current ?? []).filter((item) => item.id !== id), candidate]);
      setSelected((current) => ({ ...current, [side]: id }));
      setOpenJobId(null);
      delete ids.current[side];
    } catch (cause) {
      setError(message(cause));
    } finally {
      setForking(null);
    }
  }

  return <div className={styles.comparison}>
    <p className={styles.meta}>镜头 {clipNumber} · 静音短预览，最多 5 秒</p>
    {error ? <div role="alert" className={styles.error}>{error}{" "}
      <Button variant="ghost" onClick={() => { setError(null); setReload((value) => value + 1); }}>重新读取</Button>
    </div> : null}
    {!items && !error ? <p role="status">正在读取作品候选…</p> : null}
    {items ? <div className={styles.sides}>
      {(["A", "B"] as const).map((side) => {
        const other = items.find((item) => item.id === selected[side === "A" ? "B" : "A"]);
        const choices = items.filter((item) => item.id !== other?.id && (!other || item.base_version_id === other.base_version_id));
        return <section key={side} aria-label={`候选 ${side}`} className={styles.side}>
          <label>候选 {side}
            <select value={selected[side]} onChange={(event) => {
              setOpenJobId(null);
              setSelected((current) => ({ ...current, [side]: event.target.value }));
            }}>
              <option value="">选择候选</option>
              {choices.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.id.slice(0, 8)}</option>)}
            </select>
          </label>
          {!selected[side] && (!other || other.base_version_id === base.id)
            ? <Button variant="outline" disabled={forking !== null} onClick={() => void fork(side)}>
              {forking === side ? "正在创建…" : `从第 ${base.sequence} 版创建候选`}
            </Button> : null}
          {!selected[side] && other && !choices.length && other.base_version_id !== base.id
            ? <p className={styles.meta}>这个基线只有一个候选。可以清除另一侧选择，切回当前版。</p> : null}
          {selected[side] ? <CandidateEdit key={`${taskId}:${clipId}:${selected[side]}`} side={side}
            taskId={taskId} candidateId={selected[side]} clipId={clipId} openJobId={openJobId} onVideo={setOpenJobId} /> : null}
        </section>;
      })}
    </div> : null}
  </div>;
}
