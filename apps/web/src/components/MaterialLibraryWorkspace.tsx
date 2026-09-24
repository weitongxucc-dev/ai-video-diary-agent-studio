"use client";

import {
  AlertCircle,
  Check,
  ChevronRight,
  Clock3,
  Film,
  FolderPlus,
  FolderSearch,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import styles from "./MaterialLibraryControls.module.css";
import {
  analyzeProductionMaterial,
  createMaterialCollection,
  getMaterialLibraryInsights,
  getMaterialIntakeStatus,
  getProductionMaterials,
  bindMaterialIntake,
  reviewProductionMaterial,
  retryMaterialIntake,
  scanMaterialIntake,
  updateProductionMaterialSegment,
  updateMaterialUseState,
} from "@/lib/api";
import type {
  MaterialAsset,
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
  MaterialSegment,
} from "@/lib/types";

type Filter = "all" | "raw" | "approved" | "rejected";
type IntakeStatus = Awaited<ReturnType<typeof getMaterialIntakeStatus>>;
type Busy =
  | "scan"
  | "search"
  | "load"
  | "analyze"
  | "review"
  | "segment"
  | "collection"
  | "retry"
  | null;

const directoryStateLabel: Record<IntakeStatus["directory_state"], string> = {
  ready: "目录可用",
  missing: "目录不存在",
  unmounted: "外接盘未连接",
  invalid: "目录不可用",
  changed: "目录已变更",
  unknown: "等待确认",
};

const useStateOptions: { value: MaterialAsset["use_state"]; label: string; impact: string }[] = [
  { value: "available", label: "可用于创作", impact: "可用于选片和发布。" },
  { value: "reference_only", label: "仅参考", impact: "只供查看，不用于成片。" },
  { value: "excluded", label: "不使用", impact: "不用于创作或发布。" },
  { value: "not_for_automation", label: "不用于自动任务", impact: "手动创作可用，自动任务不使用。" },
  { value: "not_for_publish", label: "不用于发布", impact: "可用于制作预览，发布时排除。" },
];

function duration(value: number | null) {
  if (value === null) return "--:--";
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function roleLabel(asset: MaterialAsset) {
  const role =
    asset.usage_role === "unassigned"
      ? asset.suggested_usage_role
      : asset.usage_role;
  if (role === "a_roll") return "人物主镜头";
  if (role === "b_roll") return "补充画面";
  if (asset.usage_role === "exclude") return "不使用";
  return "待判断";
}

function reviewLabel(asset: MaterialAsset) {
  if (asset.use_state === "excluded") return "不使用";
  if (asset.use_state === "reference_only") return "仅参考";
  if (asset.review_status === "approved") return "已审核";
  if (asset.review_status === "rejected") return "用途待确认";
  if (asset.analysis_status === "ready") return "已分析";
  if (asset.analysis_status === "failed" || asset.analysis_status === "blocked")
    return "分析受阻";
  return "等待分析";
}

function analysisMessage(asset: MaterialAsset) {
  const message = asset.analysis_message.trim();
  if (!message || /[{}\n\r]|Traceback|Error:|\/Users\/|\/Volumes\/|[A-Za-z]:\\/.test(message)) {
    return asset.analysis_status === "blocked"
      ? "素材目录暂不可用，请连接原盘后重新检查。"
      : "素材分析失败，请检查文件或本地模型后重试。";
  }
  return message;
}

function tagLabel(value: string) {
  return value
    .replaceAll("a_roll", "人物主镜头")
    .replaceAll("b_roll", "补充画面");
}

function fileLabel(value: string) {
  return value
    .replace(/batch_(\d+)/gi, "第 $1 组")
    .replace(/episode/gi, "创作任务");
}

function visibleError(cause: unknown, fallback: string) {
  if (!(cause instanceof Error)) return fallback;
  let message = cause.message;
  if (message.startsWith("{")) {
    try {
      const payload = JSON.parse(message) as { detail?: unknown };
      message = typeof payload.detail === "string" ? payload.detail : "";
    } catch {
      return fallback;
    }
  }
  return /[\u3400-\u9fff]/.test(message) ? message : fallback;
}

export function MaterialLibraryWorkspace({
  initialLibrary,
  initialInsights,
  initialCollections,
}: {
  initialLibrary: MaterialLibrary;
  initialInsights: MaterialLibraryInsights;
  initialCollections: MaterialCollections;
}) {
  const [assets, setAssets] = useState(initialLibrary.assets);
  const [totalCount, setTotalCount] = useState(initialLibrary.total_count);
  const [hasMore, setHasMore] = useState(initialLibrary.has_more);
  const [insights, setInsights] = useState(initialInsights);
  const [collections, setCollections] = useState(
    initialCollections.collections,
  );
  const [selectedId, setSelectedId] = useState(
    initialLibrary.assets[0]?.id ?? "",
  );
  const [selectedSegmentId, setSelectedSegmentId] = useState(
    initialLibrary.assets[0]?.segments[0]?.id ?? "",
  );
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState("");
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [intakeError, setIntakeError] = useState("");
  const [intakeBusy, setIntakeBusy] = useState<"bind" | "refresh" | null>("refresh");
  const [detailError, setDetailError] = useState("");
  const [tagErrors, setTagErrors] = useState<Record<string, string>>({});
  const [tagDrafts, setTagDrafts] = useState<Record<string, string>>({});
  const [collectionQuery, setCollectionQuery] = useState("");
  const [collectionDraft, setCollectionDraft] = useState(
    initialLibrary.assets[0]?.segments[0]?.collection_ids[0] ?? "",
  );
  const [newCollectionName, setNewCollectionName] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState(initialLibrary.assets[0]?.segments[0]?.description ?? "");
  const [transcriptDraft, setTranscriptDraft] = useState(initialLibrary.assets[0]?.segments[0]?.transcript ?? "");
  const sourceVideo = useRef<HTMLVideoElement>(null);
  const isComposingTag = useRef(false);
  const selectionRevision = useRef(0);
  const intakeRevision = useRef(0);
  const lastSearch = useRef({ filter, query });
  const assetWriteRevision = useRef(0);

  const selected =
    assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null;
  const selectedSegment =
    selected?.segments.find((segment) => segment.id === selectedSegmentId) ??
    selected?.segments[0] ??
    null;
  const tagScope = selected && selectedSegment ? `${selected.id}:${selectedSegment.id}` : "";
  const tagDraft = tagDrafts[tagScope] ?? "";
  const tagError = tagErrors[tagScope] ?? "";
  const visible = assets;

  useEffect(() => {
    let active = true;
    async function refresh() {
      const revision = intakeRevision.current;
      try {
        const status = await getMaterialIntakeStatus();
        if (active && revision === intakeRevision.current) {
          setIntake(status);
          setIntakeError("");
        }
      } catch (cause) {
        if (active && revision === intakeRevision.current) setIntakeError(visibleError(cause, "无法查看素材目录状态，请重试。"));
      } finally {
        if (active) setIntakeBusy((current) => current === "refresh" ? null : current);
      }
    }
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (!intake?.bound_to_current_workspace || busy !== null) return;
    let active = true;
    let inFlight = false;
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      const revision = assetWriteRevision.current;
      try {
        const [libraryResult, insightsResult] = await Promise.allSettled([
          getProductionMaterials({ limit: initialLibrary.limit, offset: 0, query: query.trim(), reviewStatus: filter }),
          getMaterialLibraryInsights(),
        ]);
        if (active && revision === assetWriteRevision.current) {
          if (libraryResult.status === "fulfilled") {
            setAssets(libraryResult.value.assets);
            setTotalCount(libraryResult.value.total_count);
            setHasMore(libraryResult.value.has_more);
          }
          if (insightsResult.status === "fulfilled") setInsights(insightsResult.value);
        }
      } finally {
        inFlight = false;
      }
    }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [busy, filter, initialLibrary.limit, intake?.bound_to_current_workspace, query]);

  useEffect(() => {
    if (lastSearch.current.filter === filter && lastSearch.current.query === query) return;
    lastSearch.current = { filter, query };
    let active = true;
    const timer = window.setTimeout(() => {
      const revision = assetWriteRevision.current;
      setBusy("search");
      getProductionMaterials({
        limit: initialLibrary.limit,
        offset: 0,
        query: query.trim(),
        reviewStatus: filter,
      })
        .then((result) => {
          if (!active || revision !== assetWriteRevision.current) return;
          setAssets(result.assets);
          setTotalCount(result.total_count);
          setHasMore(result.has_more);
          if (
            result.assets[0] &&
            !result.assets.some((asset) => asset.id === selectedId)
          ) {
            selectAsset(result.assets[0]);
          }
        })
        .catch((cause) => {
          if (active)
            setError(cause instanceof Error ? cause.message : "搜索素材失败");
        })
        .finally(() => {
          if (active) setBusy(null);
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
    // selectedId and selectAsset are intentionally excluded: changing selection must not re-run search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, initialLibrary.limit, query]);

  function replaceAsset(updated: MaterialAsset) {
    assetWriteRevision.current += 1;
    setAssets((current) =>
      current.map((asset) => (asset.id === updated.id ? updated : asset)),
    );
  }

  function selectAsset(asset: MaterialAsset) {
    selectionRevision.current += 1;
    const first = asset.segments[0];
    setDescriptionDraft(first?.description ?? "");
    setTranscriptDraft(first?.transcript ?? "");
    setSelectedId(asset.id);
    setSelectedSegmentId(first?.id ?? "");
    setCollectionDraft(first?.collection_ids[0] ?? "");
    setCollectionQuery("");
    setDetailError("");
  }

  function selectSegment(segment: MaterialSegment) {
    selectionRevision.current += 1;
    setSelectedSegmentId(segment.id);
    setDescriptionDraft(segment.description);
    setTranscriptDraft(segment.transcript);
    if (sourceVideo.current) sourceVideo.current.currentTime = segment.start_seconds;
    setCollectionDraft(segment.collection_ids[0] ?? "");
    setCollectionQuery("");
    setDetailError("");
  }

  async function refreshInsights() {
    try {
      setInsights(await getMaterialLibraryInsights());
    } catch {
      /* Keep last usable snapshot. */
    }
  }

  async function refreshIntakeStatus() {
    const revision = ++intakeRevision.current;
    setIntakeBusy("refresh");
    try {
      const status = await getMaterialIntakeStatus();
      if (revision === intakeRevision.current) { setIntake(status); setIntakeError(""); }
    } catch (cause) {
      if (revision === intakeRevision.current) setIntakeError(visibleError(cause, "无法查看素材目录状态，请重试。"));
    } finally {
      setIntakeBusy(null);
    }
  }

  async function bindIntake() {
    const directory = intake?.configured_directory;
    if (!directory || !intake.binding_available || intake.directory_state !== "ready" || intake.bound_to_current_workspace) return;
    ++intakeRevision.current;
    setIntakeBusy("bind");
    setIntakeError("");
    try {
      setIntake(await bindMaterialIntake(directory));
    } catch (cause) {
      setIntakeError(visibleError(cause, "接入失败。请核对目录后重新确认。"));
      try { setIntake(await getMaterialIntakeStatus()); } catch { /* Preserve the reviewed directory and error. */ }
    } finally {
      setIntakeBusy(null);
    }
  }

  async function scan() {
    if (!intake?.bound_to_current_workspace || intake.directory_state !== "ready") return;
    setBusy("scan");
    setError("");
    try {
      const result = await scanMaterialIntake();
      assetWriteRevision.current += 1;
      setAssets(result.assets);
      setTotalCount(result.total_count);
      setHasMore(result.has_more);
      setQuery("");
      setFilter("all");
      if (
        result.assets[0] &&
        !result.assets.some((asset) => asset.id === selectedId)
      ) {
        selectAsset(result.assets[0]);
      }
      await refreshInsights();
      try { setIntake(await getMaterialIntakeStatus()); } catch { /* Keep the last directory status. */ }
    } catch (cause) {
      setError(visibleError(cause, "检查素材目录失败，请重试。"));
    } finally {
      setBusy(null);
    }
  }

  async function loadMore() {
    setBusy("load");
    setError("");
    try {
      const result = await getProductionMaterials({
        limit: initialLibrary.limit,
        offset: assets.length,
        query: query.trim(),
        reviewStatus: filter,
      });
      setAssets((current) => {
        const known = new Set(current.map((asset) => asset.id));
        return [
          ...current,
          ...result.assets.filter((asset) => !known.has(asset.id)),
        ];
      });
      setTotalCount(result.total_count);
      setHasMore(result.has_more);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载更多素材失败");
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selected) return;
    const revision = selectionRevision.current;
    setBusy("analyze");
    setDetailError("");
    try {
      const updated = await analyzeProductionMaterial(selected.id);
      replaceAsset(updated);
      if (revision === selectionRevision.current && updated.segments[0]) selectSegment(updated.segments[0]);
      await refreshInsights();
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "素材分析失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function retryFailedAsset() {
    if (!selected || selected.analysis_status !== "failed") return;
    const revision = selectionRevision.current;
    setBusy("retry");
    setDetailError("");
    try {
      replaceAsset(await retryMaterialIntake(selected.id));
      try { setIntake(await getMaterialIntakeStatus()); } catch { /* Keep the last directory status. */ }
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "重试失败，请检查素材目录后再试。"));
    } finally {
      setBusy(null);
    }
  }

  async function changeUseState(useState: MaterialAsset["use_state"]) {
    if (!selected) return;
    const revision = selectionRevision.current;
    setBusy("review");
    setDetailError("");
    try {
      replaceAsset(await updateMaterialUseState(selected.id, useState));
      await refreshInsights();
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "使用状态保存失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function review(
    status: "approved" | "rejected",
    role: "b_roll" | "exclude",
  ) {
    if (!selected) return;
    const revision = selectionRevision.current;
    setBusy("review");
    setDetailError("");
    try {
      replaceAsset(
        await reviewProductionMaterial(selected.id, {
          review_status: status,
          usage_role: role,
        }),
      );
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "素材审核失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function saveSegmentOrganization() {
    if (!selected || !selectedSegment) return;
    const revision = selectionRevision.current;
    setBusy("segment");
    setDetailError("");
    try {
      const updated = await updateProductionMaterialSegment(
        selected.id,
        selectedSegment.id,
        {
          description: descriptionDraft,
          transcript: transcriptDraft,
          review_status: selectedSegment.review_status,
          usage_role: selectedSegment.usage_role,
          user_tags: selectedSegment.user_tags,
          collection_ids: collectionDraft ? [collectionDraft] : [],
        },
      );
      replaceAsset(updated);
      await refreshInsights();
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "片段修改保存失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  async function saveTags(nextTags: string[], clearInput = false) {
    if (!selected || !selectedSegment || busy !== null) return;
    const scope = `${selected.id}:${selectedSegment.id}`;
    setBusy("segment");
    setTagErrors((current) => ({ ...current, [scope]: "" }));
    try {
      const updated = await updateProductionMaterialSegment(selected.id, selectedSegment.id, {
        description: selectedSegment.description,
        transcript: selectedSegment.transcript,
        review_status: selectedSegment.review_status,
        usage_role: selectedSegment.usage_role,
        user_tags: nextTags,
        collection_ids: selectedSegment.collection_ids,
      });
      replaceAsset(updated);
      if (clearInput) setTagDrafts((current) => ({ ...current, [scope]: "" }));
      await refreshInsights();
    } catch (cause) {
      setTagErrors((current) => ({ ...current, [scope]: visibleError(cause, "标签保存失败，请重试") }));
    } finally {
      setBusy(null);
    }
  }

  function addTag(value: string) {
    if (!selectedSegment || busy !== null) return;
    const tag = value.trim();
    if (!tag) return;
    if (selectedSegment.user_tags.includes(tag)) {
      if (value === tagDraft) setTagDrafts((current) => ({ ...current, [tagScope]: "" }));
      return;
    }
    void saveTags([...selectedSegment.user_tags, tag], value === tagDraft);
  }

  async function addCollection() {
    const name = newCollectionName.trim();
    if (!name) return;
    const revision = selectionRevision.current;
    setBusy("collection");
    setDetailError("");
    try {
      const created = await createMaterialCollection({ name });
      setCollections((current) => [...current, created]);
      if (revision === selectionRevision.current) {
        setCollectionDraft(created.id);
        setNewCollectionName("");
      }
    } catch (cause) {
      if (revision === selectionRevision.current) setDetailError(visibleError(cause, "新建标签夹失败，请重试"));
    } finally {
      setBusy(null);
    }
  }

  const inspector = selected ? (
    <div className="library-inspector">
      <div className="library-preview">
        <video
          ref={sourceVideo}
          aria-label="原素材预览"
          onLoadedMetadata={event => { event.currentTarget.currentTime = selectedSegment?.start_seconds ?? 0; }}
          controls
          key={selected.id}
          preload="metadata"
          src={`/api/production/materials/${selected.id}/media`}
        />
        <span>
          <Play size={14} /> {duration(selected.duration_seconds)}
        </span>
      </div>
      <div className="library-inspector__title">
        <strong title={fileLabel(selected.file_name)}>{fileLabel(selected.file_name)}</strong>
        <span>
          {roleLabel(selected)} · {reviewLabel(selected)}
        </span>
      </div>
      {(selected.analysis_status === "blocked" || selected.analysis_status === "failed") ? (
        <p className={styles.analysisMessage} role="status">{analysisMessage(selected)}</p>
      ) : null}
      {detailError ? <div className="library-error" role="alert"><AlertCircle size={15} />{detailError}</div> : null}
      <dl className="library-inspector__meta">
        <div>
          <dt>画面建议</dt>
          <dd>
            {roleLabel(selected)}{" "}
            {selected.usage_role_confidence
              ? `${Math.round(selected.usage_role_confidence * 100)}%`
              : ""}
          </dd>
        </div>
        <div>
          <dt>使用状态</dt>
          <dd>
            <div className={styles.useStateOptions} role="group" aria-label="素材使用状态">
              {useStateOptions.map((option) => (
                <button
                  aria-pressed={selected.use_state === option.value}
                  className={selected.use_state === option.value ? styles.isSelected : ""}
                  disabled={busy !== null}
                  key={option.value}
                  onClick={() => void changeUseState(option.value)}
                  type="button"
                >{option.label}</button>
              ))}
            </div>
            <small className={styles.useStateImpact}>{useStateOptions.find((option) => option.value === selected.use_state)?.impact}</small>
          </dd>
        </div>
        <div>
          <dt>识别片段</dt>
          <dd>{selected.segments.length}</dd>
        </div>
      </dl>
      {selected.usage_role_reason ? (
        <div className="library-ai-reason">
          <Sparkles size={14} />
          <p>{selected.usage_role_reason}</p>
        </div>
      ) : null}
      <div className="library-inspector__actions">
        {selected.analysis_status === "failed" ? (
          <button disabled={busy !== null} onClick={() => void retryFailedAsset()} type="button">
            <RefreshCw size={14} />
            {busy === "retry" ? "正在重试…" : "重试分析"}
          </button>
        ) : selected.analysis_status === "ready" ? (
          <button disabled={busy !== null} onClick={() => void analyze()} type="button">
            <RefreshCw size={14} />
            {busy === "analyze" ? "分析中…" : "重新分析"}
          </button>
        ) : null}
        <button
          className="is-primary"
          disabled={busy !== null}
          onClick={() => void review("approved", "b_roll")}
          type="button"
        >
          <Check size={14} />
          用作配图
        </button>
      </div>
      {selected.segments.length ? (
        <div className="library-segments">
          <h3>识别片段</h3>
          {selected.segments.map((segment) => (
            <button
              className={
                selectedSegment?.id === segment.id ? "is-selected" : ""
              }
              disabled={busy !== null}
              key={segment.id}
              onClick={() => selectSegment(segment)}
              type="button"
            >
              <span>
                {duration(segment.start_seconds)}–
                {duration(segment.end_seconds)}
              </span>
              <p>
                {segment.description ||
                  segment.understanding?.summary ||
                  segment.transcript ||
                  "等待补充片段描述"}
              </p>
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
      ) : null}
      {selectedSegment ? (
        <div className="library-segment-organizer">
          <div className="library-segment-heading">
            <strong>片段分类</strong>
            <span>
              已用 {selectedSegment.usage_count} 次
              {selectedSegment.last_used_at
                ? ` · 最近 ${new Date(selectedSegment.last_used_at).toLocaleDateString("zh-CN")}`
                : ""}
            </span>
          </div>
          <p className={styles.segmentRange}>当前片段：{duration(selectedSegment.start_seconds)}–{duration(selectedSegment.end_seconds)}。下面的标签只用于这段画面。</p>
          {selectedSegment.understanding ? <section className="library-speech-evidence" aria-label="画面观察证据">
            <strong>画面观察</strong>
            {selectedSegment.understanding.status === "ready" ? <>
              <p>{selectedSegment.understanding.summary}</p>
              <div className="library-evidence-times" aria-label="采样帧">{selectedSegment.understanding.sample_times.map(time =>
                <button key={time} type="button" aria-label={`查看采样帧 ${time.toFixed(2)} 秒`} onClick={() => { if (sourceVideo.current) sourceVideo.current.currentTime = time; }}><time>{duration(time)}</time></button>)}</div>
              {selectedSegment.understanding.facts.length ? <details><summary>查看 {selectedSegment.understanding.facts.length} 条观察依据</summary>
                {selectedSegment.understanding.facts.map((fact, index) => <button key={index} type="button" aria-label={`核对画面：${fact.text}`} onClick={() => { if (sourceVideo.current) sourceVideo.current.currentTime = fact.sample_times[0]; }}>
                  <time>{duration(fact.sample_times[0])}</time><span>{fact.text}</span>
                </button>)}
              </details> : null}
              {selectedSegment.understanding.unknowns.length ? <p>待核对：{selectedSegment.understanding.unknowns.join("；")}</p> : null}
            </> : <p>{selectedSegment.understanding.message}</p>}
            <strong>画面文字</strong>
            {selectedSegment.understanding.ocr_status === "ready" ? selectedSegment.understanding.ocr_frames.some(frame => frame.observations.length) ?
              Array.from(new Set(selectedSegment.understanding.ocr_frames.flatMap(frame => frame.observations.map(item => item.text)))).map(text => {
                const frame = selectedSegment.understanding!.ocr_frames.find(frame => frame.observations.some(item => item.text === text))!;
                const eligible = selectedSegment.understanding!.ocr_frames.some(item => item.observations.some(observation => observation.text === text && observation.retrieval_eligible));
                return <button key={text} type="button" onClick={() => { if (sourceVideo.current) sourceVideo.current.currentTime = frame.timestamp_seconds; }}><time>{duration(frame.timestamp_seconds)}</time><span>{text}{!eligible ? " · 低置信度，未用于检索" : ""}</span></button>;
              }) : <p>采样帧中未识别到文字。</p>
              : <p>{selectedSegment.understanding.ocr_status === "failed" ? "本地文字识别失败，可重新分析。" : "本地文字识别尚未配置。"}</p>}
          </section> : null}
          <label><span>片段描述</span><textarea value={descriptionDraft} onChange={event => setDescriptionDraft(event.target.value)} /></label>
          <label><span>转写纠正（留空使用识别结果）</span><textarea value={transcriptDraft} onChange={event => setTranscriptDraft(event.target.value)} /></label>
          {selected.speech_analysis ? <section className="library-speech-evidence" aria-label="原片语音证据">
            <strong>原片语音</strong>
            {selected.speech_analysis.status !== "ready" || !selected.speech_analysis.observations.length ? <p>{selected.speech_analysis.message}</p> : null}
            {selected.speech_analysis.observations.filter(item => item.end_ms > selectedSegment.start_seconds * 1000 && item.start_ms < selectedSegment.end_seconds * 1000).map((item, index) =>
              <button key={index} type="button" onClick={() => { if (sourceVideo.current) sourceVideo.current.currentTime = item.start_ms / 1000; }}>
                <time>{duration(item.start_ms / 1000)}</time><span>{item.text}</span>
              </button>)}
          </section> : null}
          <div className="library-tag-list" aria-label="系统标签">
            {selectedSegment.system_tags.map((tag) => (
              <span key={tag}>{tagLabel(tag)}</span>
            ))}
            {!selectedSegment.system_tags.length ? (
              <small>等待画面理解生成系统标签</small>
            ) : null}
          </div>
          <div className={styles.tagEditor}>
            <strong>我的标签</strong>
            <div className={styles.tagChips} aria-label="已保存的我的标签">
              {selectedSegment.user_tags.length ? selectedSegment.user_tags.map((tag) => (
                <span className={styles.tagChip} key={tag}>{tag}
                  <button aria-label={`移除标签 ${tag}`} disabled={busy !== null} onClick={() => void saveTags(selectedSegment.user_tags.filter((entry) => entry !== tag))} type="button"><X size={13} /></button>
                </span>
              )) : <small>这段画面还没有我的标签。</small>}
            </div>
            <label htmlFor="material-tag-input">添加标签</label>
            {tagError ? <p className={styles.tagError} role="alert">{tagError}</p> : null}
            <input
              autoComplete="off"
              disabled={busy !== null}
              id="material-tag-input"
              onChange={(event) => setTagDrafts((current) => ({ ...current, [tagScope]: event.target.value }))}
              onCompositionStart={() => { isComposingTag.current = true; }}
              onCompositionEnd={() => { isComposingTag.current = false; }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !isComposingTag.current && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  addTag(tagDraft);
                }
              }}
              placeholder="输入标签后按回车，如产品演示"
              value={tagDraft}
            />
            {insights.tags.some((entry) => !selectedSegment.user_tags.includes(entry.tag)) ? (
              <div className={styles.commonTags} aria-label="常用标签">
                <span>常用标签</span>
                {insights.tags.filter((entry) => !selectedSegment.user_tags.includes(entry.tag)).slice(0, 6).map((entry) => (
                  <button disabled={busy !== null} key={entry.tag} onClick={() => addTag(entry.tag)} type="button">{tagLabel(entry.tag)}</button>
                ))}
              </div>
            ) : null}
          </div>
          <div className={styles.collectionPicker}>
            <label htmlFor="material-collection-search">标签夹</label>
            <input
              id="material-collection-search"
              onChange={(event) => setCollectionQuery(event.target.value)}
              placeholder="搜索标签夹"
              type="search"
              value={collectionQuery}
            />
            <div className={styles.collectionOptions} role="group" aria-label="选择标签夹">
              {[{ id: "", name: "未归类" }, ...collections]
                .filter((collection) => collection.name.includes(collectionQuery.trim()))
                .map((collection) => (
                  <button aria-pressed={collectionDraft === collection.id} className={collectionDraft === collection.id ? styles.isSelected : ""} key={collection.id || "none"} onClick={() => setCollectionDraft(collection.id)} type="button">{collection.name}</button>
                ))}
              {!collections.some((collection) => collection.name.includes(collectionQuery.trim())) && !"未归类".includes(collectionQuery.trim()) ? <small>没有匹配的标签夹</small> : null}
            </div>
          </div>
          <div className="library-collection-create">
            <input
              aria-label="新标签夹名称"
              onChange={(event) => setNewCollectionName(event.target.value)}
              placeholder="新建标签夹"
              value={newCollectionName}
            />
            <button
              disabled={busy !== null || !newCollectionName.trim()}
              onClick={() => void addCollection()}
              type="button"
            >
              <FolderPlus size={14} />
              新建
            </button>
          </div>
          <button
            className="library-segment-save"
            disabled={busy !== null}
            onClick={() => void saveSegmentOrganization()}
            type="button"
          >
            {busy === "segment" ? "保存中…" : "保存片段修改"}
          </button>
        </div>
      ) : null}
    </div>
  ) : null;

  return (
    <AppShell inspector={inspector} inspectorTitle="素材详情">
      <div className="modern-library">
        <section className={styles.intakePanel} aria-label="素材自动接入">
          <div className={styles.intakeHeading}>
            <strong>素材自动接入</strong>
            <span>{intake ? directoryStateLabel[intake.directory_state] : "正在核对目录"}</span>
          </div>
          {intakeError ? (
            <p className={styles.intakeError} role="alert">{intakeError}
              <button disabled={intakeBusy !== null} onClick={() => void refreshIntakeStatus()} type="button">重新检查</button>
            </p>
          ) : null}
          {!intake ? (
            <p className={styles.intakeDescription}>{intakeError ? "暂时无法确认目录状态，请重新检查。" : "正在确认素材目录与当前工作区的关系…"}</p>
          ) : !intake.binding_available ? (
            <p className={styles.intakeDescription}>该目录已接入其他工作区。请切换到所属工作区处理，当前工作区不能直接接入。</p>
          ) : intake.directory_state === "changed" ? (
            <p className={styles.intakeDescription}>{intake.directory_message} {intake.repair_action} 请在<Link href="/settings">设置</Link>中核对目录；不会自动更改工作区归属。</p>
          ) : !intake.configured_directory ? (
            <p className={styles.intakeDescription}>尚未选择素材目录。请先到<Link href="/settings">设置</Link>选择目录。</p>
          ) : !intake.bound_to_current_workspace ? (
            <div className={styles.intakeRow}>
              <p className={styles.intakeDescription}>
                确认将 <code title={intake.configured_directory}>{intake.configured_directory}</code> 接入当前工作区。接入后每 {intake.scan_interval_seconds} 秒检查新文件并分析，不会自动创建视频任务。
                {intake.directory_state !== "ready" ? ` ${intake.directory_message} ${intake.repair_action ?? ""}` : ""}
              </p>
              <button className={styles.intakePrimary} disabled={intakeBusy !== null || intake.directory_state !== "ready"} onClick={() => void bindIntake()} type="button">
                {intakeBusy === "bind" ? "正在接入…" : "确认接入"}
              </button>
            </div>
          ) : (
            <>
              <div className={styles.intakeRow}>
                <p className={styles.intakeDescription}>
                  已接入 <code title={intake.bound_directory ?? ""}>{intake.bound_directory}</code>，每 {intake.scan_interval_seconds} 秒自动检查。
                  {intake.directory_state !== "ready" ? ` ${intake.directory_message} ${intake.repair_action ?? ""}` : " 新文件稳定后自动分析。"}
                </p>
                {intake.directory_state === "ready" ? (
                  <button className={styles.intakeSecondary} disabled={busy !== null || intakeBusy !== null} onClick={() => void scan()} type="button">
                    {busy === "scan" ? <LoaderCircle className="is-spinning" size={15} /> : <FolderSearch size={15} />}
                    {busy === "scan" ? "正在检查…" : "立即检查"}
                  </button>
                ) : null}
              </div>
              <div className={styles.intakeCounts} aria-label="自动接入进度">
                <span>等待稳定 {intake.file_counts.observing}</span>
                <span>分析中 {intake.file_counts.running}</span>
                <span>已就绪 {intake.file_counts.ready}</span>
                <span>失败 {intake.file_counts.failed}</span>
                <span>等待磁盘 {intake.file_counts.blocked_disk}</span>
              </div>
            </>
          )}
        </section>
        <section
          className="library-coverage"
          aria-label="素材覆盖"
        >
          <div className="library-coverage__summary">
            <strong>{insights.segment_count} 个片段</strong>
            <small>{insights.asset_count} 个文件</small>
          </div>
          <div className="library-coverage__tags">
            {insights.tags.slice(0, 3).map((entry) => (
              <span key={entry.tag}>
                <strong>{tagLabel(entry.tag)}</strong>
                <small>
                  {entry.segment_count} 段
                </small>
              </span>
            ))}
          </div>

        </section>

        <div className="library-toolbar">
          <label className="library-search">
            <Search size={15} />
            <input
              aria-label="搜索素材"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件、画面、标签或标签夹…"
              value={query}
            />
          </label>
          <div
            className="library-filters"
            role="group"
            aria-label="审核状态筛选"
          >
            {(["all", "raw", "approved", "rejected"] as Filter[]).map(
              (value) => (
                <button
                  className={filter === value ? "is-active" : ""}
                  key={value}
                  onClick={() => setFilter(value)}
                  type="button"
                >
                  {
                    {
                      all: "全部",
                      raw: "待审核",
                      approved: "已批准",
                      rejected: "已排除",
                    }[value]
                  }
                </button>
              ),
            )}
          </div>
          <span className="library-count">
            已载入 {assets.length} / {totalCount}
          </span>
        </div>

        {error ? (
          <div className="library-error" role="alert">
            <AlertCircle size={15} />
            {error}
          </div>
        ) : null}

        {visible.length ? (
          <div className="library-grid">
            {visible.map((asset) => (
              <button
                className={`library-card${selected?.id === asset.id ? " is-selected" : ""}`}
                key={asset.id}
                data-open-details onClick={() => selectAsset(asset)}
                type="button"
                >
                <span className="library-card__poster">
                  <video
                    aria-hidden="true"
                    muted
                    playsInline
                    preload="metadata"
                    src={`/api/production/materials/${asset.id}/media`}
                  />
                  <Film className="library-card__fallback-icon" size={22} />
                  <small>{duration(asset.duration_seconds)}</small>
                  <i
                    className={`library-card__state state-${asset.review_status === "approved" ? "approved" : asset.review_status === "rejected" ? "rejected" : asset.analysis_status}`}
                  >
                    {asset.review_status === "approved" ? (
                      <ShieldCheck size={13} />
                    ) : asset.analysis_status === "ready" ? (
                      <Sparkles size={13} />
                    ) : (
                      <Clock3 size={13} />
                    )}
                  </i>
                </span>
                <span className="library-card__copy">
                  <strong title={fileLabel(asset.file_name)}>{fileLabel(asset.file_name)}</strong>
                  <small>
                    {roleLabel(asset)} · {reviewLabel(asset)}
                  </small>
                  {(asset.analysis_status === "blocked" || asset.analysis_status === "failed") ? (
                    <small className={styles.assetDiagnostic}>{analysisMessage(asset)}</small>
                  ) : null}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="library-empty">
            <Film size={23} />
            <div>
              <strong>
                {assets.length ? "没有匹配的素材" : "素材库还是空的"}
              </strong>
              <p>
                {assets.length
                  ? "调整搜索条件或审核状态。"
                  : intake?.bound_to_current_workspace
                    ? "系统会自动检查新素材。新文件稳定并完成分析后，将显示在这里。"
                    : "先在上方确认素材目录，系统随后会自动检查新文件。"}
              </p>
            </div>
          </div>
        )}
        {hasMore ? (
          <div className="library-load-more">
            <button
              disabled={busy !== null}
              onClick={() => void loadMore()}
              type="button"
            >
              {busy === "load" ? (
                <LoaderCircle className="is-spinning" size={15} />
              ) : null}
              {busy === "load"
                ? "正在载入…"
                : `载入更多（剩余 ${Math.max(0, totalCount - assets.length)}）`}
            </button>
          </div>
        ) : null}
      </div>
    </AppShell>
  );
}
