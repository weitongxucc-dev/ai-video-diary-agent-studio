"use client";

import { useContext, useEffect, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from "react";
import {
  cancelTimelineCandidatePreview, confirmTimelineCandidateAdoption, forkTimelineCandidate, listTimelineCandidates,
  prepareTimelineCandidateAdoption, readLatestTimelineCandidatePreview,
  readTimelineCandidate, readTimelineCandidatePreview, readTimelineCandidatePreviewVideo,
  saveTimelineCandidateCrop, submitTimelineCandidatePreview,
  type TimelineCandidateAdoptionResult, type TimelineCandidateAdoptionSelection,
  type TimelineCandidateCropChange, type TimelineCandidateDetail, type TimelineCandidatePreview, type TimelineCandidateSummary,
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
    case "running": return "正在生成镜头预览。";
    case "succeeded": return `作品 ${preview.timeline_start_seconds.toFixed(2)}–${preview.timeline_end_seconds.toFixed(2)} 秒 · 原片 ${preview.source_start_seconds.toFixed(2)}–${preview.source_end_seconds.toFixed(2)} 秒 · ${preview.silent ? "静音" : "含当前音轨"}`;
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
    controls playsInline muted={preview.silent} preload="none"
    aria-label={`候选 ${side} 的镜头${preview.silent ? "静音" : "有声"}预览`}
    onError={() => setError("无法播放此候选画面，请重新打开预览。")} />;
}

function cropSummary(crop: TimelineCandidateCropChange["before"]) {
  const percent = (value: number | undefined) => Number(((value ?? .5) * 100).toPrecision(12));
  return `${crop.fit_mode === "fit" ? "适应" : "填满"} · 水平 ${percent(crop.crop_x)}% · 垂直 ${percent(crop.crop_y)}%`;
}

function CandidateEdit({ side, taskId, candidateId, initialClipId, openJobId, onVideo, base, currentFormalId,
  adoptionSide, adopting, onSelectAdoption, onAdopting, onAdopted }: {
  side: Side; taskId: string; candidateId: string; initialClipId: string;
  openJobId: string | null; onVideo: Dispatch<SetStateAction<string | null>>;
  base: CandidateComparisonTarget["base"]; currentFormalId: string | null;
  adoptionSide: Side | null; adopting: boolean; onSelectAdoption: (side: Side | null) => void;
  onAdopting: (adopting: boolean) => void; onAdopted: (result: TimelineCandidateAdoptionResult) => Promise<void>;
}) {
  const visible = useContext(MediaVisibilityContext);
  const [clipId, setClipId] = useState(initialClipId);
  const [detail, setDetail] = useState<TimelineCandidateDetail | null>(null);
  const [preview, setPreview] = useState<TimelineCandidatePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [readingPreview, setReadingPreview] = useState(true);
  const [action, setAction] = useState<"save" | "preview" | "cancel" | "prepare-adoption" | "confirm-adoption" | null>(null);
  const [saved, setSaved] = useState(false);
  const [dirtyVersion, setDirtyVersion] = useState<string | null>(null);
  const [prepared, setPrepared] = useState<{ selection: TimelineCandidateAdoptionSelection; requestId: string } | null>(null);
  const confirmationHeading = useRef<HTMLParagraphElement>(null);
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
    if (!detail || action || adopting) return;
    const fields = new FormData(event.currentTarget);
    const cropX = Number(fields.get("crop-x")) / 100;
    const cropY = Number(fields.get("crop-y")) / 100;
    const fit = fields.get("fit") === "fit" ? "fit" : "fill";
    const key = `crop:${detail.head_version_id}:${clipId}:${fit}:${cropX}:${cropY}`;
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

  async function startPreview(withAudio: boolean) {
    if (!detail || action || adopting) return;
    const key = `preview:${detail.head_version_id}:${clipId}:${withAudio}`;
    const id = request.current?.key === key ? request.current.id : crypto.randomUUID();
    request.current = { key, id };
    setAction("preview");
    setError(null);
    try {
      const result = await submitTimelineCandidatePreview(target, detail, clipId, id, withAudio);
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
    if (!preview || action || adopting) return;
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

  async function prepareAdoption() {
    if (!detail || action || adopting) return;
    onSelectAdoption(side);
    setPrepared(null);
    setError(null);
    setAction("prepare-adoption");
    try {
      const selection = await prepareTimelineCandidateAdoption(target, detail, base);
      setPrepared({ selection, requestId: crypto.randomUUID() });
    } catch (cause) {
      setError(message(cause));
    } finally {
      setAction(null);
    }
  }

  async function confirmAdoption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmation || action || adopting) return;
    setAction("confirm-adoption");
    onAdopting(true);
    onVideo(null);
    setError(null);
    try {
      const result = await confirmTimelineCandidateAdoption(confirmation.selection, confirmation.requestId);
      setDetail((current) => current ? { ...current, adoption: result } : current);
      setPrepared(null);
      onSelectAdoption(null);
      await onAdopted(result);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setAction(null);
      onAdopting(false);
    }
  }

  const clip = detail?.version.clips.find((item) => item.id === clipId);
  const dirty = dirtyVersion === detail?.version.id;
  const currentBase = detail?.base_version_id === currentFormalId && currentFormalId === base.id;
  const canAdopt = detail && !detail.adoption && currentBase && detail.version.id !== detail.base_version_id && !dirty;
  const confirmation = canAdopt && adoptionSide === side && prepared?.selection.candidate_version_id === detail.version.id
    ? prepared : null;
  const changes = confirmation
    ? (confirmation.selection.all_crops ? confirmation.selection.changes : [confirmation.selection])
    : [];
  useEffect(() => {
    if (confirmation) confirmationHeading.current?.focus();
  }, [confirmation]);

  async function reloadCandidate() {
    setError(null);
    setPrepared(null);
    setReload((value) => value + 1);
    if (detail?.adoption && currentFormalId === detail.adoption.base_version_id) {
      try {
        await onAdopted(detail.adoption);
      } catch (cause) {
        setError(message(cause));
      }
    }
  }

  return <>
    {error ? <div role="alert" className={styles.error}>{error}{" "}
      <Button variant="ghost" disabled={action !== null || adopting} onClick={() => void reloadCandidate()}>重新读取</Button>
    </div> : null}
    {!detail && !error ? <p role="status">正在读取候选…</p> : null}
    {detail && !clip ? <p role="status">这个候选中没有所选镜头。</p> : null}
    {detail && detail.version.clips.length > 1 ? <label>编辑镜头
      <select value={clipId} disabled={dirty || action !== null || adopting} onChange={(event) => {
        onVideo((active) => active === preview?.job_id ? null : active);
        setClipId(event.target.value);
        setPreview(null);
        setReadingPreview(true);
        setError(null);
        setSaved(false);
      }}>
        {!clip ? <option value={clipId} disabled>原镜头不可用</option> : null}
        {detail.version.clips.map((item, index) => <option key={item.id} value={item.id}>镜头 {index + 1}</option>)}
      </select>
    </label> : null}
    {detail && clip ? <>
      <p className={styles.meta} aria-live="polite">候选版本 #{detail.version.sequence}{saved ? " · 已保存" : ""}</p>
      <form key={`${detail.version.id}:${clipId}`} className={styles.form}
        onSubmit={(event) => void save(event)} onReset={() => setDirtyVersion(null)} onChange={(event) => {
        const values = new FormData(event.currentTarget);
        const changed = values.get("fit") !== (clip.fit_mode ?? "fill")
          || Number(values.get("crop-x")) !== (clip.crop_x ?? .5) * 100
          || Number(values.get("crop-y")) !== (clip.crop_y ?? .5) * 100;
        setPrepared(null);
        setDirtyVersion(changed ? detail.version.id : null);
      }}>
        <label>画面适配<select name="fit" defaultValue={clip.fit_mode ?? "fill"}>
          <option value="fill">填满</option><option value="fit">适应</option>
        </select></label>
        <div className={styles.fields}>
          <label>水平位置（%）<input name="crop-x" type="number" required min="0" max="100" step="any" defaultValue={(clip.crop_x ?? .5) * 100} /></label>
          <label>垂直位置（%）<input name="crop-y" type="number" required min="0" max="100" step="any" defaultValue={(clip.crop_y ?? .5) * 100} /></label>
        </div>
        <div className={styles.actions}>
          <Button variant="outline" type="submit" disabled={action !== null || adopting || !dirty}>{action === "save" ? "正在保存…" : "保存候选裁切"}</Button>
          {dirty ? <Button variant="ghost" type="reset" disabled={action !== null || adopting}>放弃未保存裁切</Button> : null}
        </div>
      </form>
      <div className={styles.actions}>
        {(preview?.status !== "succeeded" || preview.silent) && !pending(preview)
          ? <Button type="button" disabled={action !== null || adopting || dirty || readingPreview} onClick={() => void startPreview(true)}>
            {action === "preview" ? "正在提交…" : "生成有声预览"}
          </Button> : null}
        {preview?.status !== "succeeded" && !pending(preview)
          ? <Button variant="outline" type="button" disabled={action !== null || adopting || dirty || readingPreview}
            onClick={() => void startPreview(false)}>生成静音预览</Button> : null}
        {preview && pending(preview) ? <Button variant="outline" disabled={action !== null || adopting} onClick={() => void cancelPreview()}>取消预览请求</Button> : null}
        {preview?.status === "succeeded" ? <Button variant="outline" disabled={adopting} aria-expanded={openJobId === preview.job_id}
          onClick={() => onVideo(openJobId === preview.job_id ? null : preview.job_id)}>
          {openJobId === preview.job_id ? "收起画面" : "打开预览画面"}
        </Button> : null}
      </div>
      {preview ? <p className={styles.meta} role="status">{previewStatus(preview)}</p> : null}
      {preview?.status === "succeeded" && openJobId === preview.job_id
        ? <CandidateVideo key={preview.job_id} side={side} target={target} preview={preview} /> : null}
      {detail.adoption ? <p role="status" className={styles.meta}>
        候选第 {detail.adoption.candidate_sequence} 版已采用为正式第 {detail.adoption.result_sequence} 版。采用记录可在历史版本中查看。
      </p> : !currentBase ? <p className={styles.meta}>此候选基于较早的正式版本，可继续比较画面。</p> : null}
      {canAdopt && !confirmation ? <div className={styles.actions}>
        <Button type="button" variant="outline" disabled={action !== null || adopting} onClick={() => void prepareAdoption()}>
          {action === "prepare-adoption" ? "正在核对…" : `采用候选 ${side}`}
        </Button>
      </div> : null}
      {confirmation ? <form className={styles.adoption} aria-label={`确认采用候选 ${side}`} onSubmit={(event) => void confirmAdoption(event)}>
        <p ref={confirmationHeading} tabIndex={-1}><strong>采用候选 {side} 第 {confirmation.selection.candidate_sequence} 版</strong></p>
        <p className={styles.meta}>正式第 {confirmation.selection.base_sequence} 版 · {changes.length} 处镜头裁切</p>
        {changes.map((change) => <div className={styles.form} key={change.clip_id}>
          <p><strong>镜头 {change.clip_number}</strong></p>
          <p>当前 {cropSummary(change.before)}</p>
          <p>采用后 {cropSummary(change.after)}</p>
        </div>)}
        <p className={styles.meta}>全部裁切保存到同一个正式版本，可从历史一次撤销。成片需另行生成。</p>
        <div className={styles.actions}>
          <Button type="submit" disabled={action !== null || adopting}>
            {action === "confirm-adoption" ? "正在采用…" : `确认采用候选 ${side}`}
          </Button>
          <Button type="button" variant="ghost" disabled={adopting} onClick={() => { setPrepared(null); onSelectAdoption(null); }}>
            取消
          </Button>
        </div>
      </form> : null}
    </> : null}
  </>;
}

export function TimelineCandidateComparison({ taskId, clipId, clipNumber, base, currentFormalId, onAdopted }:
  CandidateComparisonTarget & { currentFormalId: string | null; onAdopted: (result: TimelineCandidateAdoptionResult) => Promise<void> }) {
  const visible = useContext(MediaVisibilityContext);
  const [items, setItems] = useState<TimelineCandidateSummary[] | null>(null);
  const [selected, setSelected] = useState<Record<Side, string>>({ A: "", B: "" });
  const [error, setError] = useState<string | null>(null);
  const [forking, setForking] = useState<Side | null>(null);
  const [openJobId, setOpenJobId] = useState<string | null>(null);
  const [adoptionSide, setAdoptionSide] = useState<Side | null>(null);
  const [adopting, setAdopting] = useState(false);
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
    <p className={styles.meta}>默认镜头 {clipNumber} · 最多预览 5 秒，仅比较镜头画面与该区间音轨。</p>
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
            <select value={selected[side]} disabled={adopting} onChange={(event) => {
              setOpenJobId(null);
              setAdoptionSide(null);
              setSelected((current) => ({ ...current, [side]: event.target.value }));
            }}>
              <option value="">选择候选</option>
              {choices.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.id.slice(0, 8)}</option>)}
            </select>
          </label>
          {!selected[side] && (!other || other.base_version_id === base.id)
            ? <Button variant="outline" disabled={forking !== null || adopting || currentFormalId !== base.id} onClick={() => void fork(side)}>
              {forking === side ? "正在创建…" : `从第 ${base.sequence} 版创建候选`}
            </Button> : null}
          {!selected[side] && other && !choices.length && other.base_version_id !== base.id
            ? <p className={styles.meta}>这个基线只有一个候选。可以清除另一侧选择，切回当前版。</p> : null}
          {selected[side] ? <CandidateEdit key={`${taskId}:${clipId}:${selected[side]}`} side={side}
            taskId={taskId} candidateId={selected[side]} initialClipId={clipId} openJobId={openJobId} onVideo={setOpenJobId}
            base={base} currentFormalId={currentFormalId} adoptionSide={adoptionSide} adopting={adopting}
            onSelectAdoption={setAdoptionSide} onAdopting={setAdopting} onAdopted={onAdopted} /> : null}
        </section>;
      })}
    </div> : null}
  </div>;
}
