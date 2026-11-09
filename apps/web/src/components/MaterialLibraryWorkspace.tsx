"use client";
import { Button } from "./ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "./ui/input-group";
import { useWorkspaceViewState, useWorkspacePage } from "./workbench/context";
import { MaterialNotesEditor } from "./material-library/MaterialNotesEditor";
import { MaterialSegmentTags } from "./material-library/MaterialSegmentTags";
import { MaterialSegmentEditor } from "./material-library/MaterialSegmentEditor";
import { useMaterialNotes } from "./material-library/useMaterialNotes";
import { MaterialAssetPreview } from "./material-library/MaterialAssetPreview";
import { MaterialUsageEvidence } from "./material-library/MaterialUsageEvidence";
import { MaterialAssetTags } from "./material-library/MaterialAssetTags";
import { SourceTaskForm } from "./material-library/SourceTaskForm";
import { MaterialCollectionPicker } from "./material-library/MaterialCollectionPicker";
import { MaterialResults } from "./material-library/MaterialResults";
import { MaterialDiscoveryPanel } from "./material-library/MaterialDiscoveryPanel";
import { MaterialIntakePanel } from "./material-library/MaterialIntakePanel";
import { MediaLinkImportPanel } from "./material-library/MediaLinkImportPanel";
import { useMediaLinkImport } from "./material-library/useMediaLinkImport";
import { useLocalMaterialImport } from "./material-library/useLocalMaterialImport";
import { useMaterialIntake } from "./material-library/useMaterialIntake";
import { useMaterialUsageRecords } from "./material-library/useMaterialUsageRecords";
import { useMaterialBusinessObjects } from "./material-library/useMaterialBusinessObjects";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { AlertCircle, Check, Grid2X2, Plus, Info, List, LoaderCircle, RefreshCw, Search, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { useAssistantMaterialReference, useAssistantSelection } from "./AssistantConversationProvider";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import styles from "./MaterialLibraryControls.module.css";
import { getProductionMaterial } from "@/lib/api/materials";
import { ASSISTANT_LOCATE_REFERENCE_EVENT, readAssistantLocateReference } from "@/lib/assistant-reference";
import { useWorkspaceSearchParams as useSearchParams } from "./workbench/context";
import type { AssistantReferenceItem } from "@/lib/types/assistant";
import { MaterialBusinessObjects } from "@/components/MaterialBusinessObjects";
import { analyzeProductionMaterial, createMaterialCollection, getMaterialLibraryInsights, getProductionMaterials, reviewProductionMaterial, retryMaterialIntake, scanMaterialIntake, updateProductionMaterialSegment, updateMaterialUseState, updateMaterialAssetTags } from "@/lib/api";

import type { MaterialAsset, MaterialCollections, MaterialLibrary, MaterialLibraryInsights, MaterialSegment, MaterialAssistantResult, MaterialAssistantSelection } from "@/lib/types";
import type { Filter, MediaTypeFilter, SegmentEditAnchor, Busy } from './material-library/material-state';
import { useStateOptions, EMPTY_OBJECT_IDS } from './material-library/material-state';
import { duration, roleLabel, analysisMessage, tagLabel, fileLabel } from './material-library/material-labels';
import { mediaTypeOptions, mediaTypeLabels, readMediaTypeFromUrl } from './material-library/material-filters';
import { visibleError } from './material-library/material-errors';

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
  const router = useRouter();
  const viewActive = useWorkspacePage()?.active ?? true;
  const searchParams = useSearchParams();
  const locateQuery = searchParams?.get("locate_ref") ?? "";
  const [assets, setAssets] = useState(initialLibrary.assets);
  const [locatedAsset, setLocatedAsset] = useState<MaterialAsset | null>(null);
  const locatedAssetRef = useRef(locatedAsset);
  locatedAssetRef.current = locatedAsset;
  const assetsRef = useRef(assets);
  assetsRef.current = assets;
  const [totalCount, setTotalCount] = useState(initialLibrary.total_count);
  const [hasMore, setHasMore] = useState(initialLibrary.has_more);
  const [insights, setInsights] = useState(initialInsights);
  const [collections, setCollections] = useState(
    initialCollections.collections,
  );
  const [selectedId, setSelectedId] = useWorkspaceViewState("library.selectedId",
    initialLibrary.assets[0]?.id ?? "",
  );
  const [selectedSegmentId, setSelectedSegmentId] = useWorkspaceViewState("library.selectedSegmentId",
    initialLibrary.assets[0]?.segments[0]?.id ?? "",
  );
  const [query, setQuery] = useWorkspaceViewState("library.query", "");
  const [importOpen, setImportOpen] = useState(false);
  const [statisticsOpen, setStatisticsOpen] = useState(false);
  const [previewErrorId, setPreviewErrorId] = useState<string | null>(null);
  const [previewReadTick, setPreviewReadTick] = useState(0);
  const [failedThumbnails, setFailedThumbnails] = useState<Record<string, boolean>>({});
  const searchInput = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useWorkspaceViewState<Filter>("library.filter", "all");
  const [mediaType, setMediaType] = useWorkspaceViewState<MediaTypeFilter>("library.mediaType", "all");
  const [viewMode, setViewMode] = useWorkspaceViewState<"grid" | "list">("library.viewMode", "grid");
  const [actionBusy, setBusy] = useState<Busy>(null);
  const [listBusy, setListBusy] = useState<"search" | "load" | null>(null);
  const [needsVisibleRead, setNeedsVisibleRead] = useState(false);
  const [scanReadRevision, setScanReadRevision] = useState(0);
  const [error, setError] = useState("");
  const { intake, intakeError, intakeBusy, refreshIntakeStatus, bindIntake, confirmCopy, readIntakeAfterScan } = useMaterialIntake();
  const { importBusy, importResult, runImport } = useLocalMaterialImport(Boolean(intake?.bound_to_current_workspace && intake.directory_state === "ready"));
  const { linkUrl, changeLinkUrl, linkBusy, linkCheck, linkImport, linkError, runLinkCheck, runLinkImport } = useMediaLinkImport(scan);
  const [detailError, setDetailError] = useState("");
  const persistentReference = useAssistantMaterialReference();
  const referenceSelection = useAssistantSelection();
  const setMaterialReference = persistentReference?.setReference;
  const lastPublishedSelection = useRef<MaterialAssistantSelection | null>(null);
  const [assistantSelection, setAssistantSelection] = useState<MaterialAssistantSelection | null>(null);
  const [assetTagDrafts, setAssetTagDrafts] = useWorkspaceViewState<Record<string, string>>("library.assetTagDrafts", {});
  const [assetTagErrors, setAssetTagErrors] = useState<Record<string, string>>({});
  const [assetTagsPendingId, setAssetTagsPendingId] = useState<string | null>(null);
  const [tagErrors, setTagErrors] = useState<Record<string, string>>({});
  const [tagDrafts, setTagDrafts] = useWorkspaceViewState<Record<string, string>>("library.tagDrafts", {});
  const [collectionQuery, setCollectionQuery] = useWorkspaceViewState("library.collectionQuery", "");
  const [collectionDraft, setCollectionDraft] = useWorkspaceViewState("library.collectionDraft",
    initialLibrary.assets[0]?.segments[0]?.collection_ids[0] ?? "",
  );
  const [newCollectionName, setNewCollectionName] = useWorkspaceViewState("library.newCollectionName", "");
  const [descriptionDraft, setDescriptionDraft] = useWorkspaceViewState("library.descriptionDraft", initialLibrary.assets[0]?.segments[0]?.description ?? "");
  const [transcriptDraft, setTranscriptDraft] = useWorkspaceViewState("library.transcriptDraft", initialLibrary.assets[0]?.segments[0]?.transcript ?? "");
  const [usageRoleDraft, setUsageRoleDraft] = useWorkspaceViewState<MaterialSegment["usage_role"]>("library.usageRoleDraft", initialLibrary.assets[0]?.segments[0]?.usage_role ?? "unassigned");
  const [editAnchor, setEditAnchor] = useWorkspaceViewState<SegmentEditAnchor | null>("library.editAnchor", null);
  const editAnchorRef = useRef<SegmentEditAnchor | null>(editAnchor);
  const editDraftRevision = useRef(0);
  const sourceVideo = useRef<HTMLVideoElement>(null);
  const sourceAudio = useRef<HTMLAudioElement>(null);
  const selectionRevision = useRef(0);
  // 服务端渲染的第一页没有类型条件，所以这里的 mediaType 只能是「全部」；
  // 挂载后从 URL 读回的类型会让下面的搜索 effect 重新取第一页。
  const searchScope = useMemo(() => ({ filter, query, tag: initialTag ?? "", mediaType, limit: initialLibrary.limit }),
    [filter, query, initialTag, mediaType, initialLibrary.limit]);
  const lastSearch = useRef(searchScope);
  lastSearch.current = searchScope;
  const initialSearch = useRef(query || filter !== "all" || mediaType !== "all" ? null : searchScope);
  const [acceptedSearch, setAcceptedSearch] = useState(searchScope);
  const listReadSequence = useRef(0);
  const assetWriteRevision = useRef(0);
  useEffect(() => () => { listReadSequence.current += 1; }, []);
  const ownsListRead = useCallback((scope: typeof searchScope, request: number) => {
    return scope === lastSearch.current && request === listReadSequence.current;
  }, []);

  // Keep explicit assistant targets and edited segment drafts tied to their
  // original asset. If a filter drops that object, close the inspector and
  // retain the draft instead of pairing it with the first row in the result.
  const explicitlySelectedAssetId = assistantSelection?.asset_id;
  const assistantSelectionLost =
    Boolean(explicitlySelectedAssetId) &&
    !assets.some((asset) => asset.id === explicitlySelectedAssetId) && locatedAsset?.id !== explicitlySelectedAssetId;
  const editAnchorLost = Boolean(editAnchor) && !assets.some((asset) => asset.id === editAnchor?.assetId) && locatedAsset?.id !== editAnchor?.assetId;
  const selectionLost = assistantSelectionLost || editAnchorLost;
  const selected = selectionLost
    ? null
    : assets.find((asset) => asset.id === selectedId) ?? (locatedAsset?.id === selectedId ? locatedAsset : null) ?? assets[0] ?? null;
  // 请求发出时记住目标素材，回来时用它判断这条结果还属不属于用户眼前的素材。
  const currentAssetIdRef = useRef<string | null>(null);
  currentAssetIdRef.current = selected?.id ?? null;
  // A segment edit is also tied to its original id and time range; re-analysis
  // must not silently move its draft onto a different segment.
  const editAnchorRangeLost = Boolean(
    editAnchor && selected?.id === editAnchor.assetId &&
    selected.segments.some((segment) => segment.id === editAnchor.segmentId &&
      (segment.start_seconds !== editAnchor.startSeconds || segment.end_seconds !== editAnchor.endSeconds)),
  );
  const segmentLost =
    Boolean(selectedSegmentId) &&
    selected !== null &&
    (!selected.segments.some((segment) => segment.id === selectedSegmentId) || editAnchorRangeLost);
  const selectedSegment = segmentLost
    ? null
    : selected?.segments.find((segment) => segment.id === selectedSegmentId) ??
      selected?.segments[0] ??
      null;
  const { businessObjects, assetObjectIds, objectPending, objectError, objectRead, createBusinessObject, saveBusinessObjects, retryObjectRead, dismissObjectError } = useMaterialBusinessObjects(selected?.id ?? null);
  const { usageRecords, usageRead, retryUsageRead } = useMaterialUsageRecords(selected?.id ?? null, selectedSegment?.id ?? null);
  const { pending: notesPending, voiceNote, voiceNoteNotice, noteDrafts, noteErrors, noteSaved, noteBatchIds, batchContext, batchNoteError, batchNoteSaved,
    resetVoiceNote, changeAssetNote, cancelSpokenNote, startVoiceNote, receiveVoiceNote, saveNotes, chooseBatchAsset, changeBatchContext } = useMaterialNotes(selected?.id ?? null, replaceAsset);
  const busy: Busy = notesPending ? "notes" : actionBusy ?? listBusy;
  const selectionBlocked = needsVisibleRead || listBusy === "search";
  const noteBatchAssets = assets.filter(asset => noteBatchIds.includes(asset.id));
  const noteDraft = selected ? noteDrafts[selected.id] ?? selected.user_description : "";
  const tagScope = selected && selectedSegment ? `${selected.id}:${selectedSegment.id}` : "";
  const tagDraft = tagDrafts[tagScope] ?? "";
  const tagError = tagErrors[tagScope] ?? "";
  const visible = assets;
  const materialAssistantSelection = assistantSelection && selected?.id === assistantSelection.asset_id
    && (assistantSelection.scope === "asset"
      || selected.segments.some((segment) => segment.id === assistantSelection.segment_id))
    ? assistantSelection
    : null;
  const materialAssistantLabel = materialAssistantSelection && selected
    ? materialAssistantSelection.scope === "asset"
      ? `整条素材 · ${fileLabel(selected.file_name)}`
      : `${fileLabel(selected.file_name)} · 片段 ${duration(selected.segments.find((segment) => segment.id === materialAssistantSelection.segment_id)!.start_seconds)}–${duration(selected.segments.find((segment) => segment.id === materialAssistantSelection.segment_id)!.end_seconds)}`
    : null;

  useEffect(() => {
    if (!materialAssistantSelection || !materialAssistantLabel || !selected) return;
    // Freeze the actual version at this explicit selection; refresh must not rebind it.
    if (lastPublishedSelection.current === materialAssistantSelection) return;
    lastPublishedSelection.current = materialAssistantSelection;
    const segment = materialAssistantSelection.scope === "segment" ? selected.segments.find(item => item.id === materialAssistantSelection.segment_id) : null;
    setMaterialReference?.({label: materialAssistantLabel, selection: { ...materialAssistantSelection, expected_updated_at: selected.updated_at, ...(segment ? { expected_segment_updated_at: segment.updated_at } : {}) }});
  }, [materialAssistantSelection, materialAssistantLabel, selected, setMaterialReference]);

  useEffect(() => {
    let active = true;
    let locateRevision = 0;
    const locate = async (event: Event) => {
      const reference = (event as CustomEvent<AssistantReferenceItem>).detail;
      if (reference?.kind !== "material") return;
      const request = ++locateRevision;
      const selectionAtRequest = ++selectionRevision.current;
      const target = reference.selection;
      try {
        const asset = await getProductionMaterial(target.asset_id);
        if (!active || request !== locateRevision || selectionAtRequest !== selectionRevision.current) return;
        const segment = target.scope === "segment" ? asset.segments.find(item => item.id === target.segment_id) : asset.segments[0];
        if (target.scope === "segment" && !segment) throw new Error("引用的片段已不存在，请重新选择。");
        // Locating an existing capsule must not silently freeze a newer version.
        lastPublishedSelection.current = target;
        setLocatedAsset(asset);
        setAssets(current => current.map(item => item.id === asset.id ? asset : item));
        setSelectedId(asset.id); setSelectedSegmentId(segment?.id ?? "");
        setAssistantSelection(target); setPreviewErrorId(null);
        setDescriptionDraft(segment?.description ?? ""); setTranscriptDraft(segment?.transcript ?? "");
        setUsageRoleDraft(segment?.usage_role ?? "unassigned");
        setCollectionDraft(segment?.collection_ids[0] ?? "");
        editAnchorRef.current = null; setEditAnchor(null);
        setDetailError((target.expected_updated_at && target.expected_updated_at !== asset.updated_at) || (target.expected_segment_updated_at && target.expected_segment_updated_at !== segment?.updated_at)
          ? "已定位原对象；对象已更新，胶囊仍保留原版本，请明确重新选择后再编辑。" : "");
        const media = asset.media_type === "audio" ? sourceAudio.current : sourceVideo.current;
        if (media && media.readyState >= HTMLMediaElement.HAVE_METADATA && currentAssetIdRef.current === asset.id) media.currentTime = target.scope === "segment" ? segment?.start_seconds ?? 0 : 0;
      } catch (cause) {
        if (active && request === locateRevision && selectionAtRequest === selectionRevision.current) setError(visibleError(cause, "无法定位这条素材引用，请检查权限或重新选择。"));
      }
    };
    window.addEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate);
    try {
      const fromUrl = readAssistantLocateReference(new URLSearchParams(locateQuery ? { locate_ref: locateQuery } : {}));
      if (fromUrl?.kind === "material") void locate(new CustomEvent(ASSISTANT_LOCATE_REFERENCE_EVENT, { detail: fromUrl }));
    } catch (cause) {
      setError(visibleError(cause, "引用定位格式不可用。"));
    }
    return () => { active = false; window.removeEventListener(ASSISTANT_LOCATE_REFERENCE_EVENT, locate); };
  }, [locateQuery]);

  useEffect(() => {
    if (!viewActive || !intake?.bound_to_current_workspace || busy !== null) return;
    let active = true;
    let inFlight = false;
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== "visible" || inFlight) return;
      inFlight = true;
      const scope = lastSearch.current;
      const request = ++listReadSequence.current;
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
        if (active && ownsListRead(scope, request) && revision === assetWriteRevision.current) {
          const refreshed = libraries.flatMap((page) => page.assets);
          setAcceptedSearch(scope);
          setAssets(refreshed);
          setNeedsVisibleRead(false);
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
  }, [viewActive, assets.length, busy, filter, initialLibrary.limit, initialTag, intake?.bound_to_current_workspace, mediaType, query, selectedId, ownsListRead]);

  // 首屏是服务端按「全部」渲染的，URL 里的类型在挂载后才读回来，
  // 于是下面那次搜索会带上类型重新取第一页；URL 里没有类型时状态不变，不会多取一次。
  useEffect(() => {
    const restored = readMediaTypeFromUrl();
    if (restored !== "all") setMediaType(restored);
  }, []);

  useEffect(() => {
    const scope = searchScope;
    if (scope === initialSearch.current && scanReadRevision === 0) return;
    const request = ++listReadSequence.current;
    const selectionAtRequest = selectionRevision.current;
    let active = true;
    setError("");
    setListBusy("search");
    const timer = window.setTimeout(() => {
      const revision = assetWriteRevision.current;
      getProductionMaterials({
        limit: initialLibrary.limit,
        offset: 0,
        query: query.trim(),
        reviewStatus: filter,
        tag: initialTag,
        mediaType,
      })
        .then((result) => {
          if (!active || !ownsListRead(scope, request) || revision !== assetWriteRevision.current) return;
          setAcceptedSearch(scope);
          setAssets(result.assets);
          setNeedsVisibleRead(false);
          setTotalCount(result.total_count);
          setHasMore(result.has_more);
          if (
            !assistantSelection &&
            !editAnchorRef.current &&
            selectionAtRequest === selectionRevision.current &&
            result.assets[0] &&
            !result.assets.some((asset) => asset.id === selectedId)
          ) {
            selectAsset(result.assets[0], false);
          }
        })
        .catch((cause) => {
          if (active && ownsListRead(scope, request))
            setError(visibleError(cause, "搜索素材失败，请重试。"));
        })
        .finally(() => {
          if (active && ownsListRead(scope, request)) setListBusy(null);
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
    // Selection changes do not re-run search; a revision check protects user choices made in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchScope, scanReadRevision, ownsListRead]);

  function replaceAsset(updated: MaterialAsset) {
    assetWriteRevision.current += 1;
    setAssets((current) =>
      current.map((asset) => (asset.id === updated.id ? updated : asset)),
    );
    setLocatedAsset(current => current?.id === updated.id ? updated : current);
  }

  function applyMaterialAssistantResult(selection: MaterialAssistantSelection, result: MaterialAssistantResult) {
    if (result.scope !== selection.scope || result.asset_ref !== `asset:${selection.asset_id}`
      || result.segment_ref !== (selection.scope === "segment" ? `segment:${selection.segment_id}` : null)) return;
    const current = assetsRef.current.find((asset) => asset.id === selection.asset_id) ?? (locatedAssetRef.current?.id === selection.asset_id ? locatedAssetRef.current : null);
    if (!current) return;
    assetWriteRevision.current += 1;
    const apply = (asset: MaterialAsset): MaterialAsset => {
      if (asset.id !== selection.asset_id) return asset;
      if (selection.scope === "asset") return { ...asset, user_tags: result.user_tags };
      return { ...asset, segments: asset.segments.map((segment) => segment.id === selection.segment_id
        ? { ...segment, user_tags: result.user_tags, ...(result.system_tags ? { system_tags: result.system_tags } : {}) }
        : segment) };
    };
    setAssets((latest) => latest.map(apply));
    setLocatedAsset(current => current ? apply(current) : current);
    if (result.action && result.changed) void refreshInsights();
  }

  async function saveAssetTags(asset: MaterialAsset, nextTags: string[]) {
    if (assetTagsPendingId !== null) return;
    setAssetTagsPendingId(asset.id);
    setAssetTagErrors((current) => ({ ...current, [asset.id]: "" }));
    try {
      const updated = await updateMaterialAssetTags(asset.id, nextTags);
      replaceAsset(updated);
      setAssetTagDrafts((current) => ({ ...current, [asset.id]: "" }));
      await refreshInsights();
    } catch (cause) {
      setAssetTagErrors((current) => ({
        ...current,
        [asset.id]: visibleError(cause, "素材标签保存失败，请重试。"),
      }));
    } finally {
      setAssetTagsPendingId(null);
    }
  }

  async function addAssetTag(asset: MaterialAsset) {
    const tag = (assetTagDrafts[asset.id] ?? "").trim();
    if (!tag || assetTagsPendingId !== null || asset.user_tags.includes(tag)) return;
    await saveAssetTags(asset, [...asset.user_tags, tag]);
  }

  function markSegmentDraftChanged() {
    if (!selected || !selectedSegment) return;
    const anchor = {
      assetId: selected.id,
      segmentId: selectedSegment.id,
      startSeconds: selectedSegment.start_seconds,
      endSeconds: selectedSegment.end_seconds,
    };
    editAnchorRef.current = anchor;
    setEditAnchor(anchor);
    editDraftRevision.current += 1;
  }

  function clearSegmentEditAnchor() {
    editAnchorRef.current = null;
    setEditAnchor(null);
  }

  function selectAsset(asset: MaterialAsset, explicit = true) {
    if (explicit && selectionBlocked) return;
    setLocatedAsset(null);
    resetVoiceNote();
    selectionRevision.current += 1;
    const first = asset.segments[0];
    setDescriptionDraft(first?.description ?? "");
    setTranscriptDraft(first?.transcript ?? "");
    setUsageRoleDraft(first?.usage_role ?? "unassigned");
    clearSegmentEditAnchor();
    setSelectedId(asset.id);
    setPreviewErrorId(null);
    if (selected?.id === asset.id) {
      const media = asset.media_type === "audio" ? sourceAudio.current : sourceVideo.current;
      if (media && media.readyState >= HTMLMediaElement.HAVE_METADATA) media.currentTime = 0;
    }
    const target = explicit ? { scope: "asset" as const, asset_id: asset.id } : null;
    setAssistantSelection(target);
    setSelectedSegmentId(first?.id ?? "");
    setCollectionDraft(first?.collection_ids[0] ?? "");
    setCollectionQuery("");
    setDetailError("");
  }

  function appendMaterialReference(asset: MaterialAsset, segment?: MaterialSegment) {
    if (selectionBlocked) return;
    referenceSelection?.select([{
      kind: "material",
      label: segment
        ? `${fileLabel(asset.file_name)} · 片段 ${duration(segment.start_seconds)}–${duration(segment.end_seconds)}`
        : `整条素材 · ${fileLabel(asset.file_name)}`,
      selection: {
        ...(segment ? { scope: "segment" as const, asset_id: asset.id, segment_id: segment.id }
          : { scope: "asset" as const, asset_id: asset.id }),
        expected_updated_at: asset.updated_at,
        ...(segment ? { expected_segment_updated_at: segment.updated_at } : {}),
      },
    }], true);
  }

  function seekSelectedSegment(media: HTMLMediaElement) {
    if (media !== sourceAudio.current && media !== sourceVideo.current) return;
    media.currentTime = assistantSelection?.scope === "segment" ? selectedSegment?.start_seconds ?? 0 : 0;
  }

  function selectSegment(segment: MaterialSegment, explicit = true) {
    if (explicit && selectionBlocked) return;
    selectionRevision.current += 1;
    setSelectedSegmentId(segment.id);
    clearSegmentEditAnchor();
    if (explicit && selected) {
      const target = { scope: "segment" as const, asset_id: selected.id, segment_id: segment.id };
      setAssistantSelection(target);
    } else if (!explicit && assistantSelection?.scope === "segment"
      && assistantSelection.segment_id !== segment.id) {
      setAssistantSelection(null);
    }
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

  async function scan() {
    if (!intake?.bound_to_current_workspace || intake.directory_state !== "ready") return;
    const scope = lastSearch.current;
    const request = ++listReadSequence.current;
    setBusy("scan");
    setError("");
    try {
      await scanMaterialIntake();
      if (ownsListRead(scope, request)) {
        setQuery("");
        setFilter("all");
        setScanReadRevision(current => current + 1);
      }
      await refreshInsights();
      try { await readIntakeAfterScan(); } catch { /* Keep the last directory status. */ }
    } catch (cause) {
      if (ownsListRead(scope, request)) setError(visibleError(cause, "检查素材目录失败，请重试。"));
    } finally {
      setBusy(null);
    }
  }

  const refreshLibrary = useCallback(async () => {
    const request = ++listReadSequence.current;
    const revision = assetWriteRevision.current;
    const searchAtRequest = lastSearch.current;
    const loadedCount = Math.max(initialLibrary.limit, assets.length);
    setListBusy("search"); setError("");
    try {
      const pages: MaterialLibrary[] = [];
      for (let offset = 0; offset < loadedCount; offset += 100) {
        pages.push(await getProductionMaterials({ limit: Math.min(100, loadedCount - offset), offset, query: query.trim(), reviewStatus: filter, tag: initialTag, mediaType }));
        if (!ownsListRead(searchAtRequest, request) || revision !== assetWriteRevision.current) return;
      }
      const refreshed = pages.flatMap(page => page.assets);
      setAcceptedSearch(searchAtRequest);
      setAssets(refreshed); setTotalCount(pages[0].total_count); setHasMore(refreshed.length < pages[0].total_count);
      setNeedsVisibleRead(false);
      setFailedThumbnails({});
    } catch (cause) {
      if (ownsListRead(searchAtRequest, request)) setError(visibleError(cause, "素材列表刷新失败，请重试。"));
    } finally { if (ownsListRead(searchAtRequest, request)) setListBusy(null); }
  }, [assets.length, initialLibrary.limit, query, filter, initialTag, mediaType, ownsListRead]);

  const wasViewActive = useRef(viewActive);
  useLayoutEffect(() => {
    const resumed = viewActive && !wasViewActive.current;
    wasViewActive.current = viewActive;
    if (!viewActive) {
      listReadSequence.current += 1;
      setListBusy(null);
      setNeedsVisibleRead(true);
    } else if (resumed) {
      void refreshLibrary();
    }
  }, [viewActive, refreshLibrary]);

  function constrainSegmentPlayback(media: HTMLMediaElement) {
    if (assistantSelection?.scope !== "segment" || !selectedSegment) return;
    if (media.currentTime >= selectedSegment.end_seconds) media.pause();
  }
  function startSegmentPlayback(media: HTMLMediaElement) {
    if (assistantSelection?.scope !== "segment" || !selectedSegment) return;
    if (media.currentTime < selectedSegment.start_seconds || media.currentTime >= selectedSegment.end_seconds) media.currentTime = selectedSegment.start_seconds;
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

  function resetAllFilters() {
    window.history.replaceState(null, "", window.location.pathname);
    setQuery("");
    setFilter("all");
    setMediaType("all");
    searchInput.current?.focus();
  }

  async function loadMore() {
    const scope = lastSearch.current;
    if (scope !== acceptedSearch || busy !== null) return;
    const request = ++listReadSequence.current;
    const revision = assetWriteRevision.current;
    setListBusy("load");
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
      if (!ownsListRead(scope, request) || revision !== assetWriteRevision.current) return;
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
      if (ownsListRead(scope, request)) setError(cause instanceof Error ? cause.message : "加载更多素材失败");
    } finally {
      if (ownsListRead(scope, request)) setListBusy(null);
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
      if (revision === selectionRevision.current && updated.segments[0]) selectSegment(updated.segments[0], false);
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
      try { await readIntakeAfterScan(); } catch { /* Keep the last directory status. */ }
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
    const draftRevision = editDraftRevision.current;
    const target = { assetId: selected.id, segmentId: selectedSegment.id };
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
        if (draftRevision === editDraftRevision.current) {
          setUsageRoleDraft(updated.segments.find((segment) => segment.id === selectedSegment.id)!.usage_role);
          if (editAnchorRef.current?.assetId === target.assetId && editAnchorRef.current.segmentId === target.segmentId) {
            clearSegmentEditAnchor();
          }
        }
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
      const saved = updated.segments.find(segment => segment.id === selectedSegment.id);
      if (updated.id !== selected.id || !saved || JSON.stringify(saved.user_tags) !== JSON.stringify(nextTags)) {
        throw new Error("标签请求已提交，但保存结果与当前片段不一致，请重新查看素材。");
      }
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
    <div className={`library-inspector ${styles.inspector}`}>
      <MaterialAssetPreview asset={selected} segment={selectedSegment} segmentScope={assistantSelection?.scope === "segment"} selectedSegmentId={assistantSelection?.scope === "segment" ? assistantSelection.segment_id : null} disabled={busy !== null || selectionBlocked} previewFailed={previewErrorId === selected.id} previewReadTick={previewReadTick} sourceAudio={sourceAudio} sourceVideo={sourceVideo} onPreviewError={() => setPreviewErrorId(selected.id)} onRetryPreview={() => { setPreviewErrorId(null); setPreviewReadTick(value => value + 1); }} seekSelectedSegment={seekSelectedSegment} constrainSegmentPlayback={constrainSegmentPlayback} startSegmentPlayback={startSegmentPlayback} onSelectAsset={() => selectAsset(selected)} onSelectSegment={selectSegment} onReferenceSegment={referenceSelection ? segment => appendMaterialReference(selected, segment) : undefined} />
      {selected.media_type === "video" ? <SourceTaskForm key={`${selected.workspace_id}:${selected.id}`} asset={selected} onCreated={receipt => router.push(`/tasks/${encodeURIComponent(receipt.task_id)}`)} /> : null}
      <details className={styles.detailSection}><summary>原文件与来源</summary><p>文件名：{selected.file_name}</p><p>原文件：{selected.path}</p>{selected.source_url ? <p>来源：<a href={selected.source_url} rel="noreferrer noopener" target="_blank">{selected.source_url}</a></p> : null}</details>
      {(selected.analysis_status === "blocked" || selected.analysis_status === "failed") ? (
        <p className={styles.analysisMessage} role="status">{analysisMessage(selected)}</p>
      ) : null}
      {detailError ? <div className="library-error" role="alert"><AlertCircle size={15} />{detailError}</div> : null}
      <MaterialAssetTags tags={selected.user_tags} error={assetTagErrors[selected.id]} draft={assetTagDrafts[selected.id] ?? ""} pending={assetTagsPendingId !== null} saving={assetTagsPendingId === selected.id} onRemove={tag => void saveAssetTags(selected, selected.user_tags.filter(entry => entry !== tag))} onDraftChange={value => setAssetTagDrafts(current => ({ ...current, [selected.id]: value }))} onAdd={() => void addAssetTag(selected)} />
      <MaterialNotesEditor draft={noteDraft} uploadContext={selected.upload_context} error={noteErrors[selected.id]} saved={noteSaved[selected.id]} saving={notesPending} disabled={busy !== null} voiceNoteActive={voiceNote?.assetId === selected.id} voiceNoteNotice={voiceNoteNotice} onDraftChange={value => changeAssetNote(selected.id, value)} onStartVoiceNote={() => startVoiceNote(selected.id)} onRecorded={async blob => { if (voiceNote) await receiveVoiceNote(voiceNote, blob); }} onCancelVoiceNote={cancelSpokenNote} onSave={() => void saveNotes([selected])} />
      <MaterialBusinessObjects
        assetId={selected.id}
        loaded={objectRead.status === "ready"}
        readError={objectRead.status === "error" ? objectRead.message : null}
        onRetryRead={retryObjectRead}
        objects={businessObjects}
        selectedIds={assetObjectIds[selected.id] ?? EMPTY_OBJECT_IDS}
        onCreate={createBusinessObject}
        onSave={saveBusinessObjects}
        pending={objectPending}
        error={objectError || null}
        onDismissError={dismissObjectError}
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
                <Button variant="ghost"
                  aria-pressed={selected.use_state === option.value}
                  className={selected.use_state === option.value ? styles.isSelected : ""}
                  disabled={busy !== null}
                  key={option.value}
                  onClick={() => void changeUseState(option.value)}
                  type="button"
                >{option.label}</Button>
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
          <Button variant="ghost" disabled={busy !== null} onClick={() => void retryFailedAsset()} type="button">
            <RefreshCw size={14} />
            {busy === "retry" ? "正在重试…" : "重试分析"}
          </Button>
        ) : selected.analysis_status === "ready" ? (
          <Button variant="ghost" disabled={busy !== null} onClick={() => void analyze()} type="button">
            <RefreshCw size={14} />
            {busy === "analyze" ? "分析中…" : "重新分析"}
          </Button>
        ) : null}
        <Button variant="ghost"
          className="is-primary"
          disabled={busy !== null}
          onClick={() => void review("approved", "b_roll")}
          type="button"
        >
          <Check size={14} />
          用作配图
        </Button>
      </div>
      {selectedSegment ? (
        <MaterialSegmentEditor segment={selectedSegment} speechAnalysis={selected.speech_analysis} descriptionDraft={descriptionDraft} transcriptDraft={transcriptDraft} usageRoleDraft={usageRoleDraft} disabled={busy !== null} saving={busy === "segment"}
          onDescriptionChange={value => { markSegmentDraftChanged(); setDescriptionDraft(value); }} onTranscriptChange={value => { markSegmentDraftChanged(); setTranscriptDraft(value); }} onUsageRoleChange={value => { markSegmentDraftChanged(); setUsageRoleDraft(value); }} onSeekSpeech={seconds => { if (sourceVideo.current) sourceVideo.current.currentTime = seconds; }} onSave={() => void saveSegmentOrganization()}
          usageEvidence={<MaterialUsageEvidence usageRecords={usageRecords} usageRead={usageRead} retryUsageRead={retryUsageRead} />}
          tagsEditor={<MaterialSegmentTags segment={selectedSegment} commonTags={insights.tags} tagDraft={tagDraft} tagError={tagError} disabled={busy !== null} onDraftChange={value => setTagDrafts(current => ({ ...current, [tagScope]: value }))} onAdd={addTag} onRemove={tag => void saveTags(selectedSegment.user_tags.filter(entry => entry !== tag))} />}
          collectionPicker={<MaterialCollectionPicker query={collectionQuery} onQueryChange={setCollectionQuery} collections={collections} collectionDraft={collectionDraft} newCollectionName={newCollectionName} disabled={busy !== null} onChoose={id => { markSegmentDraftChanged(); setCollectionDraft(id); }} onNameChange={setNewCollectionName} onCreate={() => void addCollection()} />} />
      ) : null}
    </div>
  ) : null;

  return (
    <AppShell
      inspector={inspector}
      inspectorTitle="素材详情"
      assistantContext={{ materialContext: { selection: materialAssistantSelection, label: materialAssistantLabel, onResult: applyMaterialAssistantResult } }}
      conversation={<HomeCommandComposer materialContext={{
        selection: materialAssistantSelection,
        label: materialAssistantLabel,
        onResult: applyMaterialAssistantResult,
      }} />}
    >
      <div className={`modern-library ${styles.workspace}`}>
        <div className={`library-toolbar ${styles.toolbar}`}>
          <InputGroup data-input-group aria-label="素材检索" className={`library-search ${styles.search}`}>
            <InputGroupAddon><Search size={16} /></InputGroupAddon>
            <InputGroupInput
              aria-label="搜索素材"
              ref={searchInput}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索文件、画面、标签或标签夹…"
              value={query}
            />
            {query ? <Button variant="ghost" aria-label="清除搜索" className={styles.searchClear} onClick={() => { setQuery(""); searchInput.current?.focus(); }} type="button"><X size={14} /></Button> : null}
          </InputGroup>
          <AppSelect className={styles.filterSelect} aria-label="媒体类型筛选" value={mediaType} onValueChange={newValue => selectMediaType(newValue as MediaTypeFilter)}>{mediaTypeOptions.map(value => <AppSelectOption key={value} value={value}>{value === "all" ? "全部类型" : mediaTypeLabels[value]}</AppSelectOption>)}</AppSelect>
          <AppSelect className={styles.filterSelect} aria-label="审核状态筛选" value={filter} onValueChange={newValue => setFilter(newValue as Filter)}><AppSelectOption value="all">全部确认状态</AppSelectOption><AppSelectOption value="raw">未人工确认</AppSelectOption><AppSelectOption value="approved">已人工确认</AppSelectOption><AppSelectOption value="rejected">已标记排除</AppSelectOption></AppSelect>
          {initialTag ? (
            <span className="library-tag">
              标签：{initialTag}
              <Link aria-label="清除标签筛选" href="/library">
                <X size={12} aria-hidden />
              </Link>
            </span>
          ) : null}
          {query || filter !== "all" || mediaType !== "all" || initialTag ? initialTag ? (
            <Link className={styles.resetFilters} href="/library" onClick={() => { setQuery(""); setFilter("all"); setMediaType("all"); }}>重置全部</Link>
          ) : (
            <Button variant="ghost" className={styles.resetFilters} onClick={resetAllFilters} type="button">重置全部</Button>
          ) : null}
          <div className="library-view-toggle" role="group" aria-label="列表显示方式">
            <Button variant="ghost" aria-label="封面网格" aria-pressed={viewMode === "grid"} title="封面网格" onClick={() => setViewMode("grid")} type="button"><Grid2X2 size={16} /></Button>
            <Button variant="ghost" aria-label="对象列表" aria-pressed={viewMode === "list"} title="对象列表" onClick={() => setViewMode("list")} type="button"><List size={16} /></Button>
          </div>
          <Button variant="ghost" className={styles.iconButton} aria-label="刷新素材列表" title="刷新素材列表" disabled={busy !== null} onClick={() => void refreshLibrary()} type="button"><RefreshCw size={16} /></Button>
          <Button variant="ghost" className={styles.importButton} onClick={() => setImportOpen(true)} type="button"><Plus size={16} />导入素材</Button>

        </div>
        <div className={styles.summaryLine}><span>已载入 {assets.length} / {totalCount} · 最近更新在前</span><Button variant="ghost" className={styles.iconButton} aria-label="查看素材统计" title="查看素材统计" onClick={() => setStatisticsOpen(true)} type="button"><Info size={16} /></Button></div>
        {intakeError || intake?.pending_files.length || (intake && intake.bound_to_current_workspace && intake.directory_state !== "ready") ? <div className={styles.intakeNotice} role={intakeError ? "alert" : "status"}><span>{intakeError ? "素材接入暂不可用" : intake?.pending_files.length ? `${intake.pending_files.length} 个外部文件等待确认复制完成` : "素材目录需要检查"}</span><Button variant="ghost" onClick={() => setImportOpen(true)} type="button">查看接入</Button></div> : null}

        {error ? (
          <div className="library-error" role="alert">
            <AlertCircle size={15} />
            {error}
          </div>
        ) : null}
        {listBusy === "search" ? <p className={styles.searchStatus} role="status">正在更新素材列表…</p> : needsVisibleRead ? <p className={styles.searchStatus} role="status">请刷新素材列表后再选择或引用。</p> : null}

        {selectionLost || segmentLost ? (
          <div className="library-error" role="alert">
            <AlertCircle size={15} />
            {assistantSelectionLost
              ? "选中的素材已不在当前列表中。已停止编辑，未保存的说明不会写入其他素材；请重新选择一个素材后再保存。"
              : editAnchorRangeLost
                ? "正在编辑的片段时间范围已变化。草稿已保留；不会保存到新的时间范围。"
                : editAnchorLost
                ? "正在编辑的素材已不在当前结果中。草稿已保留；找回该素材后可继续保存，不会写入其他素材。"
                : "选中的片段已不在该素材中。已停止编辑，未保存的说明不会写入其他片段；请重新选择一个片段后再保存。"}
          </div>
        ) : null}

        <MaterialResults assets={visible} selectedId={selected?.id ?? null} selectionDisabled={selectionBlocked} viewMode={viewMode} hasFilters={Boolean(query || filter !== "all" || mediaType !== "all" || initialTag)} hasTagFilter={Boolean(initialTag)} intakeBound={Boolean(intake?.bound_to_current_workspace)} failedThumbnails={failedThumbnails} onThumbnailFailure={id => setFailedThumbnails(current => ({ ...current, [id]: true }))} onSelect={selectAsset} onReference={referenceSelection ? appendMaterialReference : undefined} onResetFilters={resetAllFilters} onImport={() => setImportOpen(true)} />
        {hasMore ? (
          <div className="library-load-more">
            <Button variant="ghost"
              disabled={busy !== null || acceptedSearch !== searchScope}
              onClick={() => void loadMore()}
              type="button"
            >
              {busy === "load" ? (
                <LoaderCircle className="is-spinning" size={15} />
              ) : null}
              {busy === "load"
                ? "正在载入…"
                : `载入更多（剩余 ${Math.max(0, totalCount - assets.length)}）`}
            </Button>
          </div>
        ) : null}
        <details className={styles.notesEditor}>
          <summary>给选中素材保存公共背景（可选）</summary>
          <p>仅列出当前已加载素材。请明确勾选本批文件，不会默认应用到全库。</p>
          <div className={styles.notesSelection}>
            {assets.map(asset => <label key={asset.id}><input type="checkbox" checked={noteBatchIds.includes(asset.id)} disabled={busy === "notes"} onChange={event => chooseBatchAsset(asset.id, event.target.checked)} />{fileLabel(asset.file_name)}</label>)}
          </div>
          <label>公共背景（可选）<textarea disabled={busy === "notes"} value={batchContext} onChange={event => changeBatchContext(event.target.value)} /></label>
          <p>当前选中 {noteBatchAssets.length} 份；只更新这些素材的背景，各自说明保持不变。</p>
          {batchNoteError ? <p role="alert">{batchNoteError}</p> : null}
          {batchNoteSaved ? <p role="status">选中素材的公共背景已保存。</p> : null}
          <Button variant="ghost" disabled={busy !== null || !noteBatchAssets.length} onClick={() => void saveNotes(noteBatchAssets, batchContext)} type="button">保存选中素材背景</Button>
        </details>

        {importOpen ? <HomeCardDialog kind="config" title="导入素材" onClose={() => setImportOpen(false)}>
        <MaterialDiscoveryPanel onReceived={() => { void refreshLibrary(); void refreshInsights(); }} />
        <details><summary>从接收目录或链接导入</summary>
        <MaterialIntakePanel intake={intake} intakeError={intakeError} intakeBusy={intakeBusy} busy={busy} importBusy={importBusy} importResult={importResult} onRefresh={() => void refreshIntakeStatus()} onBind={() => void bindIntake()} onLocalImport={() => void runImport()} onScan={() => void scan()} onConfirmCopy={file => void confirmCopy(file)} mediaLinkImport={<MediaLinkImportPanel linkUrl={linkUrl} linkBusy={linkBusy} linkCheck={linkCheck} linkImport={linkImport} linkError={linkError} disabled={busy !== null} onUrlChange={changeLinkUrl} onCheck={() => void runLinkCheck()} onImport={() => void runLinkImport()} />} />
        </details>
        </HomeCardDialog> : null}
        {statisticsOpen ? <HomeCardDialog kind="detail" title="素材统计" onClose={() => setStatisticsOpen(false)}>
        <section className={styles.statistics} aria-label="素材覆盖"><p>{insights.asset_count} 个可见文件 · {insights.segment_count} 个片段</p><div className={styles.statTags}>{insights.tags.filter(entry => entry.asset_count > 0).map(entry => <Link key={entry.tag} href={"/library?tag=" + encodeURIComponent(entry.tag)}>{tagLabel(entry.tag)}<small>{entry.asset_count} 份素材</small></Link>)}</div><p>此处统计已有内容；具体缺口会在制作任务检查素材时显示。</p></section>        </HomeCardDialog> : null}
      </div>
    </AppShell>
  );
}
