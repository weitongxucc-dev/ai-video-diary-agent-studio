"use client";

import {
  AlertCircle,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleMinus,
  Film,
  FolderPlus,
  FolderSearch,
  Grid2X2,
  List,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Volume2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { DesktopVoiceRecorder } from "@/components/DesktopVoiceRecorder";
import styles from "./MaterialLibraryControls.module.css";
import { MaterialBusinessObjects } from "@/components/MaterialBusinessObjects";
import {
  analyzeProductionMaterial,
  createMaterialBusinessObject,
  createMaterialCollection,
  getAssetBusinessObjects,
  getAssetSegmentUsageRecords,
  getMaterialBusinessObjects,
  getMaterialLibraryInsights,
  getMaterialIntakeStatus,
  getProductionMaterials,
  bindMaterialIntake,
  confirmMaterialCopyComplete,
  reviewProductionMaterial,
  retryMaterialIntake,
  saveAssetBusinessObjects,
  scanMaterialIntake,
  updateProductionMaterialSegment,
  updateMaterialUseState,
  updateMaterialNotes,
  transcribeMaterialVoiceNote,
} from "@/lib/api";
import { importLocalMaterials } from "@/lib/directoryPicker";
import {
  checkMediaLink,
  importMediaLink,
  mediaLinkErrorMessage,
  type MediaLinkCheck,
  type MediaLinkImport,
} from "@/lib/media-link-api";
import type {
  BusinessObjectKind,
  MaterialAsset,
  MaterialBusinessObject,
  MaterialCollections,
  MaterialLibrary,
  MaterialLibraryInsights,
  MaterialPendingCopy,
  MaterialSegment,
  MaterialUsageRecord,
} from "@/lib/types";

type Filter = "all" | "raw" | "approved" | "rejected";
type MediaTypeFilter = "all" | MaterialAsset["media_type"];
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
  | "notes"
  | null;

const directoryStateLabel: Record<IntakeStatus["directory_state"], string> = {
  ready: "目录可用",
  missing: "目录不存在",
  unmounted: "外接盘未连接",
  read_only: "目录不可写",
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

function clipRoleLabel(role: string | null) {
  if (role === "a_roll") return "人物主镜头";
  if (role === "b_roll") return "补充画面";
  return "未记录用途";
}

function mediaLabel(mediaType: MaterialAsset["media_type"]) {
  return { video: "视频", audio: "音频", image: "图片" }[mediaType];
}

const mediaTypeOptions: MediaTypeFilter[] = ["all", "video", "audio", "image"];
const mediaTypeLabels: Record<MediaTypeFilter, string> = {
  all: "全部",
  video: "视频",
  audio: "音频",
  image: "图片",
};

// 类型筛选和标签一样靠 URL 保留下来，刷新后从这里读回；读到的不是三个固定
// 类型之一就当没筛，免得手改地址栏把列表卡在一个服务端不认的值上。
function readMediaTypeFromUrl(): MediaTypeFilter {
  if (typeof window === "undefined") return "all";
  const value = new URLSearchParams(window.location.search).get("media_type");
  return mediaTypeOptions.includes(value as MediaTypeFilter)
    ? (value as MediaTypeFilter)
    : "all";
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
  if (message === "API server unreachable") return "暂时无法连接本地服务，请确认应用正常运行后重试";
  return /[\u3400-\u9fff]/.test(message) ? message : fallback;
}

// 关联还没读回来时传这个空数组，而不是每次渲染现造一个：同一个引用，
// 子组件就不会因为父页重渲染而重算一次“选择有没有变”。
const EMPTY_OBJECT_IDS: readonly string[] = [];

export function MaterialLibraryWorkspace({
  initialLibrary,
  initialInsights,
  initialCollections,
  initialTag,
}: {
  initialLibrary: MaterialLibrary;
  initialInsights: MaterialLibraryInsights;
  initialCollections: MaterialCollections;
  // 来自 /library?tag= 的精确标签筛选；URL 是唯一来源，清除后由页面重挂载恢复
  initialTag?: string;
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
  const [mediaType, setMediaType] = useState<MediaTypeFilter>("all");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState("");
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [intakeError, setIntakeError] = useState("");
  const [intakeBusy, setIntakeBusy] = useState<"bind" | "refresh" | "confirm" | null>("refresh");
  const [importBusy, setImportBusy] = useState(false);
  const [importResult, setImportResult] = useState<{
    copied: { sourceName: string; fileName: string }[];
    skipped: string[];
    failures: { sourceName: string; error: string }[];
    canceled: boolean;
    error?: string;
    warning?: string;
  } | null>(null);
  const [linkUrl, setLinkUrl] = useState("");
  const [linkBusy, setLinkBusy] = useState<"check" | "import" | null>(null);
  const [linkCheck, setLinkCheck] = useState<MediaLinkCheck | null>(null);
  const [linkImport, setLinkImport] = useState<MediaLinkImport | null>(null);
  const [linkError, setLinkError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [tagErrors, setTagErrors] = useState<Record<string, string>>({});
  const [tagDrafts, setTagDrafts] = useState<Record<string, string>>({});
  const [voiceNote, setVoiceNote] = useState<{ assetId: string; controller: AbortController } | null>(null);
  const voiceNoteRef = useRef<typeof voiceNote>(null);
  const [voiceNoteNotice, setVoiceNoteNotice] = useState("");
  const [noteDrafts, setNoteDrafts] = useState<Record<string, string>>({});
  const [noteErrors, setNoteErrors] = useState<Record<string, string>>({});
  const [noteSaved, setNoteSaved] = useState<Record<string, boolean>>({});
  const [noteBatchIds, setNoteBatchIds] = useState<string[]>([]);
  const [batchContext, setBatchContext] = useState("");
  const [batchNoteError, setBatchNoteError] = useState("");
  const [batchNoteSaved, setBatchNoteSaved] = useState(false);
  // 名称条目按素材分别保存：每条素材读回自己的关联，换素材不会串。
  const [businessObjects, setBusinessObjects] = useState<MaterialBusinessObject[]>([]);
  const [assetObjectIds, setAssetObjectIds] = useState<Record<string, string[]>>({});
  const [objectPending, setObjectPending] = useState<"create" | "save" | null>(null);
  const [objectError, setObjectError] = useState("");
  // 关联读成功之前 selectedIds 只是「还没读到」，不是「没有关联」，不能拿去保存。
  const [objectRead, setObjectRead] = useState<{ status: "loading" | "ready" | "error"; message: string }>({ status: "loading", message: "" });
  const [objectReadTick, setObjectReadTick] = useState(0);
  // 使用记录回查：按当前片段读取，读取本身不改任何计数。
  const [usageRecords, setUsageRecords] = useState<MaterialUsageRecord[]>([]);
  const [usageRead, setUsageRead] = useState<{ status: "loading" | "ready" | "error"; message: string }>({ status: "loading", message: "" });
  const usageLoadRevision = useRef(0);
  const [collectionQuery, setCollectionQuery] = useState("");
  const [collectionDraft, setCollectionDraft] = useState(
    initialLibrary.assets[0]?.segments[0]?.collection_ids[0] ?? "",
  );
  const [newCollectionName, setNewCollectionName] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState(initialLibrary.assets[0]?.segments[0]?.description ?? "");
  const [transcriptDraft, setTranscriptDraft] = useState(initialLibrary.assets[0]?.segments[0]?.transcript ?? "");
  const [usageRoleDraft, setUsageRoleDraft] = useState<MaterialSegment["usage_role"]>(initialLibrary.assets[0]?.segments[0]?.usage_role ?? "unassigned");
  const sourceVideo = useRef<HTMLVideoElement>(null);
  const sourceAudio = useRef<HTMLAudioElement>(null);
  const isComposingTag = useRef(false);
  const selectionRevision = useRef(0);
  const intakeRevision = useRef(0);
  // 服务端渲染的第一页没有类型条件，所以这里的 mediaType 只能是「全部」；
  // 挂载后从 URL 读回的类型会让下面的搜索 effect 重新取第一页。
  const lastSearch = useRef({ filter, query, tag: initialTag ?? "", mediaType: "all" as MediaTypeFilter });
  const assetWriteRevision = useRef(0);
  const objectLoadRevision = useRef(0);
  const objectReadRetry = useRef(0);

  // A background refresh can drop whatever is selected: the material was
  // removed or re-indexed, or the current search no longer returns it. Falling
  // back to assets[0] put one material's name above another material's
  // description draft, and 保存片段修改 then wrote that draft onto the wrong
  // item. Treat the loss as its own state instead. The typed draft stays in
  // component state so a later pick can restore context, the inspector closes
  // so nothing can be saved under the wrong name, and picking again re-seeds
  // from the newly selected item.
  const selectionLost =
    Boolean(selectedId) && !assets.some((asset) => asset.id === selectedId);
  const selected = selectionLost
    ? null
    : assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null;
  // 请求发出时记住目标素材，回来时用它判断这条结果还属不属于用户眼前的素材。
  const currentAssetIdRef = useRef<string | null>(null);
  currentAssetIdRef.current = selected?.id ?? null;
  // The same applies one level down: re-analysis can replace a segment's list,
  // and the drafts in the textarea still belong to the segment that left.
  const segmentLost =
    Boolean(selectedSegmentId) &&
    selected !== null &&
    !selected.segments.some((segment) => segment.id === selectedSegmentId);
  const selectedSegment = segmentLost
    ? null
    : selected?.segments.find((segment) => segment.id === selectedSegmentId) ??
      selected?.segments[0] ??
      null;
  const noteBatchAssets = assets.filter(asset => noteBatchIds.includes(asset.id));
  const noteDraft = selected ? noteDrafts[selected.id] ?? selected.user_description : "";
  const tagScope = selected && selectedSegment ? `${selected.id}:${selectedSegment.id}` : "";
  const tagDraft = tagDrafts[tagScope] ?? "";
  const tagError = tagErrors[tagScope] ?? "";
  const visible = assets;

  useEffect(() => {
    const context = voiceNoteRef.current;
    if (context && context.assetId !== selected?.id) {
      context.controller.abort();
      voiceNoteRef.current = null;
      setVoiceNote(null);
      setVoiceNoteNotice("");
    }
  }, [selected?.id]);

  useEffect(() => () => {
    voiceNoteRef.current?.controller.abort();
    voiceNoteRef.current = null;
  }, []);

  useEffect(() => {
    let active = true;
    let inFlight = false;
    async function refresh() {
      if (inFlight) return;
      inFlight = true;
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
        inFlight = false;
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
      const loadedCount = Math.max(initialLibrary.limit, assets.length);
      try {
        const libraries: MaterialLibrary[] = [];
        for (let offset = 0; offset < loadedCount; offset += 100) {
          libraries.push(await getProductionMaterials({
            limit: Math.min(100, loadedCount - offset), offset,
            query: query.trim(), reviewStatus: filter, tag: initialTag,
            mediaType,
          }));
        }
        if (active && revision === assetWriteRevision.current) {
          const refreshed = libraries.flatMap((page) => page.assets);
          setAssets(refreshed);
          setTotalCount(libraries[0]?.total_count ?? 0);
          setHasMore(refreshed.length < (libraries[0]?.total_count ?? 0));
        }
        try { const latestInsights = await getMaterialLibraryInsights(); if (active) setInsights(latestInsights); } catch { /* Keep the last usable summary. */ }
      } catch {
        // Keep the loaded pages until a later refresh succeeds.
      } finally {
        inFlight = false;
      }
    }, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [assets.length, busy, filter, initialLibrary.limit, initialTag, intake?.bound_to_current_workspace, mediaType, query, selectedId]);

  // 首屏是服务端按「全部」渲染的，URL 里的类型在挂载后才读回来，
  // 于是下面那次搜索会带上类型重新取第一页；URL 里没有类型时状态不变，不会多取一次。
  useEffect(() => {
    const restored = readMediaTypeFromUrl();
    if (restored !== "all") setMediaType(restored);
  }, []);

  useEffect(() => {
    if (lastSearch.current.filter === filter && lastSearch.current.query === query && lastSearch.current.tag === (initialTag ?? "") && lastSearch.current.mediaType === mediaType) return;
    lastSearch.current = { filter, query, tag: initialTag ?? "", mediaType };
    let active = true;
    const timer = window.setTimeout(() => {
      const revision = assetWriteRevision.current;
      setBusy("search");
      getProductionMaterials({
        limit: initialLibrary.limit,
        offset: 0,
        query: query.trim(),
        reviewStatus: filter,
        tag: initialTag,
        mediaType,
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
  }, [filter, initialLibrary.limit, initialTag, mediaType, query]);

  // 工作区的名称条目与素材无关，进页面读一次就够了；之后新增的直接并进同一份列表。
  useEffect(() => {
    let active = true;
    getMaterialBusinessObjects()
      .then(result => { if (active) setBusinessObjects(result.items); })
      .catch(cause => { if (active) setObjectError(visibleError(cause, "名称条目读取失败，请重试。")); });
    return () => { active = false; };
  }, []);

  // 每条素材读回自己的关联。单独一个 revision：selectionRevision 也会被切片段
  // 推进，用它会让切片段顺手作废这次读取，关联再也读不回来。
  useEffect(() => {
    const assetId = selected?.id ?? null;
    const revision = ++objectLoadRevision.current;
    // 换素材就清掉上一条素材的失败提示；之后迟到的失败不会再挂回来（见下面的目标判断）。
    setObjectError("");
    if (!assetId) {
      setObjectRead({ status: "ready", message: "" });
      return;
    }
    let active = true;
    setObjectRead({ status: "loading", message: "" });
    getAssetBusinessObjects(assetId)
      .then(result => {
        if (!active || revision !== objectLoadRevision.current) return;
        setAssetObjectIds(current => ({ ...current, [assetId]: result.items.map(item => item.id) }));
        setObjectRead({ status: "ready", message: "" });
      })
      .catch(cause => {
        if (!active || revision !== objectLoadRevision.current) return;
        // 读失败不等于「没有关联」。明确报出来并留重试，不让用户拿空选择去覆盖。
        setObjectRead({ status: "error", message: visibleError(cause, "名称关联读取失败，请重试。") });
      });
    return () => { active = false; };
  }, [selected?.id, objectReadTick]);

  // 按当前片段读使用记录。切片段就重读；读到的版本只是任务当前版本，
  // 界面必须写明这一点，不能让它看起来像当次使用的版本。
  useEffect(() => {
    const assetId = selected?.id ?? null;
    const segmentId = selectedSegment?.id ?? null;
    const revision = ++usageLoadRevision.current;
    if (!assetId || !segmentId) {
      setUsageRecords([]);
      setUsageRead({ status: "ready", message: "" });
      return;
    }
    let active = true;
    // 换片段就先清空上一片段的记录：加载中不能把旧记录的文案挂在当前片段上。
    setUsageRecords([]);
    setUsageRead({ status: "loading", message: "" });
    getAssetSegmentUsageRecords(assetId, segmentId)
      .then(result => {
        if (!active || revision !== usageLoadRevision.current) return;
        setUsageRecords(result.items);
        setUsageRead({ status: "ready", message: "" });
      })
      .catch(cause => {
        if (!active || revision !== usageLoadRevision.current) return;
        setUsageRecords([]);
        setUsageRead({ status: "error", message: visibleError(cause, "使用记录读取失败，请重试。") });
      });
    return () => { active = false; };
  }, [selected?.id, selectedSegment?.id]);

  function replaceAsset(updated: MaterialAsset) {
    assetWriteRevision.current += 1;
    setAssets((current) =>
      current.map((asset) => (asset.id === updated.id ? updated : asset)),
    );
  }

  function selectAsset(asset: MaterialAsset) {
    cancelVoiceNote();
    setVoiceNoteNotice("");
    selectionRevision.current += 1;
    const first = asset.segments[0];
    setDescriptionDraft(first?.description ?? "");
    setTranscriptDraft(first?.transcript ?? "");
    setUsageRoleDraft(first?.usage_role ?? "unassigned");
    setSelectedId(asset.id);
    setSelectedSegmentId(first?.id ?? "");
    setCollectionDraft(first?.collection_ids[0] ?? "");
    setCollectionQuery("");
    setDetailError("");
  }

  function seekSelectedSegment(media: HTMLMediaElement) {
    if (media !== sourceAudio.current && media !== sourceVideo.current) return;
    media.currentTime = selectedSegment?.start_seconds ?? 0;
  }

  function selectSegment(segment: MaterialSegment) {
    selectionRevision.current += 1;
    setSelectedSegmentId(segment.id);
    setDescriptionDraft(segment.description);
    setTranscriptDraft(segment.transcript);
    setUsageRoleDraft(segment.usage_role);
    const media = selected?.media_type === "audio" ? sourceAudio.current : sourceVideo.current;
    if (media && media.readyState >= HTMLMediaElement.HAVE_METADATA) {
      media.currentTime = segment.start_seconds;
    }
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

  async function confirmCopy(file: MaterialPendingCopy) {
    ++intakeRevision.current;
    setIntakeBusy("confirm");
    setIntakeError("");
    try {
      const result = await confirmMaterialCopyComplete({ ...file, confirmation_source: "manual" });
      ++intakeRevision.current;
      setIntake(result.intake);
    } catch (cause) {
      setIntakeError(visibleError(cause, "复制完成确认未保存，请重新检查文件后重试。"));
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

  async function runImport() {
    if (!intake?.bound_to_current_workspace || intake.directory_state !== "ready") return;
    setImportBusy(true);
    setImportResult(null);
    try {
      const result = await importLocalMaterials();
      if ("kind" in result) {
        setImportResult({
          copied: [], skipped: [], failures: [], canceled: false,
          error: "当前环境不支持从应用内导入素材，请使用桌面客户端。",
        });
        return;
      }
      setImportResult({
        copied: result.copied ?? [],
        skipped: result.skipped ?? [],
        failures: result.failures ?? [],
        canceled: result.canceled === true,
        error: result.error,
        warning: result.warning,
      });
    } finally {
      setImportBusy(false);
    }
  }

  async function runLinkCheck() {
    if (!linkUrl.trim()) return;
    setLinkBusy("check");
    setLinkError("");
    setLinkImport(null);
    try {
      setLinkCheck(await checkMediaLink(linkUrl.trim()));
    } catch (cause) {
      setLinkCheck(null);
      setLinkError(mediaLinkErrorMessage(cause));
    } finally {
      setLinkBusy(null);
    }
  }

  async function runLinkImport() {
    if (!linkCheck?.supported) return;
    setLinkBusy("import");
    setLinkError("");
    try {
      const result = await importMediaLink(linkUrl.trim());
      setLinkImport(result);
      await scan();
    } catch (cause) {
      setLinkImport(null);
      setLinkError(mediaLinkErrorMessage(cause));
    } finally {
      setLinkBusy(null);
    }
  }

  function selectMediaType(value: MediaTypeFilter) {
    // 只改这一个查询参数，tag 等其它已经生效的筛选原样留在地址栏里。
    const params = new URLSearchParams(window.location.search);
    if (value === "all") params.delete("media_type");
    else params.set("media_type", value);
    const search = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${search ? `?${search}` : ""}`,
    );
    setMediaType(value);
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
        tag: initialTag,
        mediaType,
      });
      assetWriteRevision.current += 1;
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
          usage_role: usageRoleDraft,
          user_tags: selectedSegment.user_tags,
          collection_ids: collectionDraft ? [collectionDraft] : [],
        },
      );
      replaceAsset(updated);
      if (revision === selectionRevision.current) {
        setUsageRoleDraft(updated.segments.find((segment) => segment.id === selectedSegment.id)!.usage_role);
      }
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

  function cancelVoiceNote() {
    voiceNoteRef.current?.controller.abort();
    voiceNoteRef.current = null;
    setVoiceNote(null);
  }

  function startVoiceNote(assetId: string) {
    cancelVoiceNote();
    const context = { assetId, controller: new AbortController() };
    voiceNoteRef.current = context;
    setVoiceNote(context);
    setVoiceNoteNotice("");
  }

  async function receiveVoiceNote(context: NonNullable<typeof voiceNote>, blob: Blob) {
    if (voiceNoteRef.current !== context) return;
    try {
      const transcript = await transcribeMaterialVoiceNote(blob, context.controller.signal);
      if (voiceNoteRef.current !== context) return;
      setNoteDrafts(current => ({ ...current, [context.assetId]: transcript }));
      setNoteSaved(current => ({ ...current, [context.assetId]: false }));
      setNoteErrors(current => ({ ...current, [context.assetId]: "" }));
      cancelVoiceNote();
      setVoiceNoteNotice("口述已转为可编辑草稿，尚未保存；请核对文字后保存本素材说明。");
    } catch (cause) {
      if (voiceNoteRef.current === context) {
        throw new Error(`${visibleError(cause, "转写失败")}。原文字保留，可重试或直接输入说明。`);
      }
    }
  }

  async function saveNotes(targets: MaterialAsset[], context?: string) {
    cancelVoiceNote();
    setVoiceNoteNotice("");
    const batch = context !== undefined;
    const assetId = targets[0].id;
    setBusy("notes");
    if (batch) { setBatchNoteError(""); setBatchNoteSaved(false); }
    else { setNoteErrors(current => ({ ...current, [assetId]: "" })); setNoteSaved(current => ({ ...current, [assetId]: false })); }
    try {
      const result = await updateMaterialNotes(targets.map(asset => ({
        asset_id: asset.id,
        user_description: batch ? asset.user_description : noteDrafts[asset.id] ?? asset.user_description,
        upload_context: batch ? context : asset.upload_context,
      })));
      result.assets.forEach(replaceAsset);
      if (batch) setBatchNoteSaved(true);
      else {
        setNoteDrafts(current => { const next = { ...current }; delete next[assetId]; return next; });
        setNoteSaved(current => ({ ...current, [assetId]: true }));
      }
    } catch (cause) {
      const message = `${visibleError(cause, "说明保存失败")}。草稿已保留，请重试保存。`;
      if (batch) setBatchNoteError(message);
      else setNoteErrors(current => ({ ...current, [assetId]: message }));
    } finally { setBusy(null); }
  }

  // 新增只写名称条目本身，不碰任何素材的关联；组件拿到新条目后自己选中，
  // 还要用户点「保存关联到当前素材」才真正写进这条素材。
  async function createBusinessObject(kind: BusinessObjectKind, name: string) {
    const targetAssetId = currentAssetIdRef.current;
    setObjectPending("create");
    setObjectError("");
    try {
      const created = await createMaterialBusinessObject({ kind, name });
      setBusinessObjects(current => [created, ...current.filter(item => item.id !== created.id)]);
      return created;
    } catch (cause) {
      const message = visibleError(cause, "名称条目新增失败，请重试");
      // 用户已经切到别的素材：这条迟到失败不挂到眼前那条身上。
      if (currentAssetIdRef.current === targetAssetId) setObjectError(message);
      throw new Error(message);
    } finally { setObjectPending(null); }
  }

  // 只写这一条素材的关联。它的说明、公共背景、片段描述和人工纠正都不在这里。
  async function saveBusinessObjects(assetId: string, businessObjectIds: string[]) {
    setObjectPending("save");
    setObjectError("");
    try {
      const result = await saveAssetBusinessObjects(assetId, businessObjectIds);
      setAssetObjectIds(current => ({ ...current, [assetId]: result.items.map(item => item.id) }));
      return result.items;
    } catch (cause) {
      const message = visibleError(cause, "名称关联保存失败，请重试");
      // 同上：目标素材已经不在眼前，就不把它的失败写成当前素材的问题。
      if (currentAssetIdRef.current === assetId) setObjectError(message);
      throw new Error(message);
    } finally { setObjectPending(null); }
  }

  const inspector = selected ? (
    <div className="library-inspector">
      <div className={`library-preview ${styles.preview}`}>
        {selected.media_type === "image" ? (
          <img
            alt={`原素材预览：${fileLabel(selected.file_name)}`}
            key={selected.id}
            src={`/api/production/materials/${selected.id}/media`}
          />
        ) : selected.media_type === "audio" ? (
          <audio
            ref={sourceAudio}
            onLoadedMetadata={event => seekSelectedSegment(event.currentTarget)}
            aria-label="原素材预览"
            controls
            key={selected.id}
            preload="metadata"
            src={`/api/production/materials/${selected.id}/media`}
          />
        ) : (
          <video
            ref={sourceVideo}
            aria-label="原素材预览"
            onLoadedMetadata={event => seekSelectedSegment(event.currentTarget)}
            controls
            key={selected.id}
            preload="metadata"
            src={`/api/production/materials/${selected.id}/media`}
          />
        )}
        {selected.media_type === "image" ? null : (
          <span>
            <Play size={14} /> {duration(selected.duration_seconds)}
          </span>
        )}
      </div>
      <div className="library-inspector__title">
        <strong title={fileLabel(selected.file_name)}>{fileLabel(selected.file_name)}</strong>
        <span>
          {mediaLabel(selected.media_type)} · {roleLabel(selected)} · {reviewLabel(selected)}
        </span>
      </div>
      {selected.source_url ? (
        <p className={styles.assetDiagnostic}>
          来源：<a href={selected.source_url} rel="noreferrer noopener" target="_blank">{selected.source_url}</a>
        </p>
      ) : null}
      {(selected.analysis_status === "blocked" || selected.analysis_status === "failed") ? (
        <p className={styles.analysisMessage} role="status">{analysisMessage(selected)}</p>
      ) : null}
      {detailError ? <div className="library-error" role="alert"><AlertCircle size={15} />{detailError}</div> : null}
      <section className={styles.notesEditor} aria-label="素材文字说明">
        <label>本素材说明（可选）<textarea disabled={busy === "notes"} value={noteDraft} onChange={event => {
          if (voiceNoteRef.current) {
            cancelVoiceNote();
            setVoiceNoteNotice("说明已编辑，口述已取消；保留当前文字，可继续编辑保存。");
          }
          setNoteDrafts(current => ({ ...current, [selected.id]: event.target.value }));
          setNoteSaved(current => ({ ...current, [selected.id]: false }));
        }} /></label>
        <p>只记录你的补充，不改片段描述或事实标签；留空不影响分析。</p>
        <p>已保存背景：{selected.upload_context || "未填写"}</p>
        <button disabled={busy !== null || voiceNote !== null} onClick={() => startVoiceNote(selected.id)} type="button">补口述说明</button>
        <p>仅补当前素材库条目的说明，不改变公共背景。</p>
        {voiceNote?.assetId === selected.id ? <>
          <DesktopVoiceRecorder purpose="material-notes" onRecorded={blob => receiveVoiceNote(voiceNote, blob)} onCancel={() => {
            cancelVoiceNote();
            setVoiceNoteNotice("口述已取消，原文字保留，可直接输入说明。");
          }} />
          <button onClick={() => { cancelVoiceNote(); setVoiceNoteNotice("口述已取消，原文字保留，可直接输入说明。"); }} type="button">取消口述说明</button>
        </> : null}
        {voiceNoteNotice ? <p role="status">{voiceNoteNotice}</p> : null}
        {noteErrors[selected.id] ? <p role="alert">{noteErrors[selected.id]}</p> : null}
        {noteSaved[selected.id] ? <p role="status">本素材说明已保存。</p> : null}
        <button disabled={busy !== null} onClick={() => void saveNotes([selected])} type="button">保存本素材说明</button>
      </section>
      <MaterialBusinessObjects
        assetId={selected.id}
        loaded={objectRead.status === "ready"}
        readError={objectRead.status === "error" ? objectRead.message : null}
        onRetryRead={() => {
          objectReadRetry.current += 1;
          setObjectReadTick(tick => tick + 1);
        }}
        objects={businessObjects}
        selectedIds={assetObjectIds[selected.id] ?? EMPTY_OBJECT_IDS}
        onCreate={createBusinessObject}
        onSave={saveBusinessObjects}
        pending={objectPending}
        error={objectError || null}
        onDismissError={() => setObjectError("")}
      />
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
          <section className="library-speech-evidence" aria-label="片段使用记录">
            <strong>使用记录</strong>
            <p>回查这个片段进过哪条成片。只列已实际写入的使用记录，读取不会增加使用次数。用途和版本都只反映该计划当前的取值，不是当次使用时的历史值。</p>
            {usageRead.status === "loading" ? <p>正在读取使用记录……</p> : null}
            {usageRead.status === "error" ? <p>{usageRead.message}</p> : null}
            {usageRead.status === "ready" && !usageRecords.length ? <p>这个片段还没有被任何成片使用过。</p> : null}
            {usageRead.status === "ready" ? usageRecords.map(record => <article key={`${record.plan_id}:${record.clip_id}`}>
              <p>
                计划 {record.plan_id} · 片段 {record.clip_id} · 当前计划用途：{clipRoleLabel(record.role)}
                {record.manually_selected === null ? "" : record.manually_selected ? " · 人工选定" : " · 自动匹配"}
              </p>
              <p>
                {new Date(record.used_at).toLocaleString("zh-CN")}
                {record.task
                  ? ` · 任务 ${record.task.stage ?? "阶段未记录"} / ${record.task.status ?? "状态未记录"}`
                  : " · 关联任务未记录"}
              </p>
              {record.task?.id ? <p><Link href={"/tasks/" + encodeURIComponent(record.task.id)}>打开任务{record.task.name ? `：${record.task.name}` : ""}</Link></p> : null}
              <p>
                {record.resolution === "current" && record.timeline_version_id
                  ? `时间线版本：当前版本 ${record.timeline_version_id}（使用记录本身未记录当次版本，这里只显示该任务的当前版本）`
                  : "时间线版本未记录"}
              </p>
            </article>) : null}
          </section>
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
          <label>
            <span>片段用途</span>
            <select
              disabled={busy !== null}
              value={usageRoleDraft}
              onChange={(event) => setUsageRoleDraft(event.target.value as MaterialSegment["usage_role"])}
            >
              {usageRoleDraft === "unassigned" ? <option value="unassigned" disabled>未指定（当前用途）</option> : null}
              {usageRoleDraft === "exclude" ? <option value="exclude" disabled>不使用（已排除）</option> : null}
              <option value="a_roll">人物主镜头</option>
              <option value="b_roll">补充画面</option>
            </select>
          </label>
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
                确认将 <code title={intake.configured_directory}>{intake.configured_directory}</code> 接入当前工作区。接入后每 {intake.scan_interval_seconds} 秒检查新文件，确认复制完成后才接收并分析，不会自动创建视频任务。
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
                  {intake.directory_state !== "ready" ? ` ${intake.directory_message} ${intake.repair_action ?? ""}` : " 外部文件须确认复制完成后才接收；应用内导入在复制结束后自动确认。"}
                </p>
                {intake.directory_state === "ready" ? (
                  <span className={styles.intakeRow}>
                    <button className={styles.intakeSecondary} disabled={importBusy || busy !== null || intakeBusy !== null} onClick={() => void runImport()} type="button">
                      {importBusy ? <LoaderCircle className="is-spinning" size={15} /> : <FolderPlus size={15} />}
                      {importBusy ? "正在导入…" : "导入本地素材"}
                    </button>
                    <button className={styles.intakeSecondary} disabled={busy !== null || intakeBusy !== null || importBusy} onClick={() => void scan()} type="button">
                      {busy === "scan" ? <LoaderCircle className="is-spinning" size={15} /> : <FolderSearch size={15} />}
                      {busy === "scan" ? "正在检查…" : "立即检查"}
                    </button>
                  </span>
                ) : null}
              </div>
              {intake.directory_state === "ready" && intake.pending_files.length > 0 ? (
                <section aria-label="待确认复制完成的文件">
                  <p className={styles.intakeDescription}>请确认这些文件已复制完成。此声明不代表文件已经通过解析或内容校验。</p>
                  {intake.pending_files.map((file) => (
                    <div className={styles.intakeRow} key={file.relative_path}>
                      <code>{file.relative_path}</code>
                      <button className={styles.intakePrimary} disabled={intakeBusy !== null || importBusy || busy !== null} onClick={() => void confirmCopy(file)} type="button">
                        复制完成，开始接收
                      </button>
                    </div>
                  ))}
                </section>
              ) : null}
              {importResult ? (
                <p
                  className={styles.intakeDescription}
                  role={importResult.error || importResult.failures.length > 0 ? "alert" : "status"}
                >
                  {importResult.canceled ? "已取消导入，源文件保持不变。" : null}
                  {!importResult.canceled && importResult.error ? importResult.error : null}
                  {!importResult.error && importResult.copied.length > 0
                    ? `已复制 ${importResult.copied.length} 个文件到素材目录：${importResult.copied.map((entry) => entry.fileName).join("、")}。复制完成确认已保存，等待接收并分析，源文件保持不变。`
                    : null}
                  {!importResult.error && importResult.copied.length === 0 && !importResult.canceled
                    && importResult.failures.length === 0 && importResult.skipped.length > 0
                    ? `没有可导入的文件：${importResult.skipped.join("、")}。仅支持 mp4、mov、m4v。`
                    : null}
                  {importResult.failures.length > 0
                    ? `导入失败 ${importResult.failures.length} 个：${importResult.failures.map((entry) => `${entry.sourceName}（${entry.error}）`).join("；")}`
                    : null}
                  {importResult.skipped.length > 0 && importResult.copied.length > 0
                    ? `跳过不支持的文件：${importResult.skipped.join("、")}。`
                    : null}
                  {importResult.warning ? ` ${importResult.warning}` : null}
                </p>
              ) : null}
              <div className={`${styles.intakeRow} ${styles.intakeLinkRow}`}>
                <input
                  aria-label="媒体文件直链"
                  className={styles.intakeLinkInput}
                  disabled={linkBusy !== null}
                  onChange={(event) => setLinkUrl(event.target.value)}
                  placeholder="粘贴公开的视频、音频或图片直链"
                  type="url"
                  value={linkUrl}
                />
                <span className={styles.intakeRow}>
                  <button
                    className={styles.intakeSecondary}
                    disabled={linkBusy !== null || !linkUrl.trim() || busy !== null}
                    onClick={() => void runLinkCheck()}
                    type="button"
                  >
                    {linkBusy === "check" ? <LoaderCircle className="is-spinning" size={15} /> : <ShieldCheck size={15} />}
                    {linkBusy === "check" ? "正在检查…" : "检查直链"}
                  </button>
                  <button
                    className={styles.intakeSecondary}
                    disabled={linkBusy !== null || !linkCheck?.supported || busy !== null}
                    onClick={() => void runLinkImport()}
                    type="button"
                  >
                    {linkBusy === "import" ? <LoaderCircle className="is-spinning" size={15} /> : <FolderPlus size={15} />}
                    {linkBusy === "import" ? "正在导入…" : "导入这条链接"}
                  </button>
                </span>
              </div>
              {linkCheck || linkImport || linkError ? (
                <p
                  className={styles.intakeDescription}
                  role={linkError ? "alert" : "status"}
                >
                  {linkError ? linkError : null}
                  {!linkError && linkCheck?.supported ? linkCheck.message : null}
                  {!linkError && linkImport
                    ? `已导入${linkImport.reused_existing_file ? "（复用已有素材）" : ""}：${linkImport.file_name}，${linkImport.size_bytes} 字节。${
                        linkImport.media_type === "video"
                          ? "已登记进素材库，等待文件稳定后分析。"
                          : linkImport.analysis_message
                      }来源：${linkImport.url}`
                    : null}
                </p>
              ) : null}
              <div className={styles.intakeCounts} aria-label="自动接入进度">
                <span>待确认复制 {intake.pending_files.length}</span>
                <span>分析中 {intake.file_counts.running}</span>
                <span>已分析 {intake.file_counts.ready}</span>
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
          {insights.recommended_gaps.length > 0 ? (
            <div className="library-coverage__gaps">
              <span>覆盖建议</span>
              <p>
                素材库暂时没有：{insights.recommended_gaps.map(tagLabel).join("、")}。按需补充即可，不影响使用。
              </p>
            </div>
          ) : null}
        </section>

        <details className={styles.notesEditor}>
          <summary>给选中素材保存公共背景（可选）</summary>
          <p>仅列出当前已加载素材。请明确勾选本批文件，不会默认应用到全库。</p>
          <div className={styles.notesSelection}>
            {assets.map(asset => <label key={asset.id}><input type="checkbox" checked={noteBatchIds.includes(asset.id)} disabled={busy === "notes"} onChange={event => {
              setNoteBatchIds(current => event.target.checked ? [...current, asset.id] : current.filter(id => id !== asset.id));
              setBatchNoteSaved(false);
            }} />{fileLabel(asset.file_name)}</label>)}
          </div>
          <label>公共背景（可选）<textarea disabled={busy === "notes"} value={batchContext} onChange={event => { setBatchContext(event.target.value); setBatchNoteSaved(false); }} /></label>
          <p>当前选中 {noteBatchAssets.length} 份；只更新这些素材的背景，各自说明保持不变。</p>
          {batchNoteError ? <p role="alert">{batchNoteError}</p> : null}
          {batchNoteSaved ? <p role="status">选中素材的公共背景已保存。</p> : null}
          <button disabled={busy !== null || !noteBatchAssets.length} onClick={() => void saveNotes(noteBatchAssets, batchContext)} type="button">保存选中素材背景</button>
        </details>

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
          <div
            className="library-filters"
            role="group"
            aria-label="媒体类型筛选"
          >
            {mediaTypeOptions.map((value) => (
              <button
                className={mediaType === value ? "is-active" : ""}
                key={value}
                onClick={() => selectMediaType(value)}
                type="button"
              >
                {mediaTypeLabels[value]}
              </button>
            ))}
          </div>
          {initialTag ? (
            <span className="library-tag">
              标签：{initialTag}
              <Link aria-label="清除标签筛选" href="/library">
                <X size={12} aria-hidden />
              </Link>
            </span>
          ) : null}
          <div className="library-view-toggle" role="group" aria-label="列表显示方式">
            <button aria-label="封面网格" aria-pressed={viewMode === "grid"} title="封面网格" onClick={() => setViewMode("grid")} type="button"><Grid2X2 size={16} /></button>
            <button aria-label="对象列表" aria-pressed={viewMode === "list"} title="对象列表" onClick={() => setViewMode("list")} type="button"><List size={16} /></button>
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

        {selectionLost || segmentLost ? (
          <div className="library-error" role="alert">
            <AlertCircle size={15} />
            {selectionLost
              ? "选中的素材已不在当前列表中。已停止编辑，未保存的说明不会写入其他素材；请重新选择一个素材后再保存。"
              : "选中的片段已不在该素材中。已停止编辑，未保存的说明不会写入其他片段；请重新选择一个片段后再保存。"}
          </div>
        ) : null}

        {visible.length ? (
          <div className={`library-grid${viewMode === "list" ? " is-list" : ""}`}>
          {visible.map((asset) => {
            const AssetStateIcon = asset.review_status === "approved" ? ShieldCheck
              : asset.review_status === "rejected" ? CircleDot
                : asset.analysis_status === "ready" ? CircleCheck
                  : asset.analysis_status === "blocked" ? CircleMinus
                    : asset.analysis_status === "failed" ? CircleAlert : CircleDashed;
            return (
              <button
                className={`library-card${selected?.id === asset.id ? " is-selected" : ""}`}
                key={asset.id}
                data-open-details onClick={() => selectAsset(asset)}
                type="button"
                >
                <span className="library-card__poster">
                  {asset.media_type === "image" ? (
                    <img
                      alt=""
                      aria-hidden="true"
                      src={`/api/production/materials/${asset.id}/media`}
                    />
                  ) : asset.media_type === "audio" ? (
                    <Volume2 className="library-card__fallback-icon" size={22} />
                  ) : (
                    <video
                      aria-hidden="true"
                      muted
                      playsInline
                      preload="metadata"
                      src={`/api/production/materials/${asset.id}/media`}
                    />
                  )}
                  {asset.media_type === "video" ? (
                    <Film className="library-card__fallback-icon" size={22} />
                  ) : null}
                  <small>
                    {asset.media_type === "image" ? mediaLabel(asset.media_type) : duration(asset.duration_seconds)}
                  </small>
                  <i
                    className={`library-card__state state-${asset.review_status === "approved" ? "approved" : asset.review_status === "rejected" ? "rejected" : asset.analysis_status}`}
                  >
                    <AssetStateIcon size={13} />
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
            );
          })}
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
                  ? "调整搜索条件、审核状态或媒体类型。"
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
