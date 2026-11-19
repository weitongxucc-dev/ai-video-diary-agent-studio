"use client";

import { ChevronUp, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getProductionMaterial, getProductionMaterials } from "@/lib/api/materials";
import {
  readMaterialScopeReceipt, saveMaterialScope,
  type MaterialScopeReceipt, type MaterialScopeRequest, type MaterialScopeSelection,
} from "@/lib/api/material-scope";
import { ApiRequestError } from "@/lib/api/transport";
import type { MaterialAsset, MaterialLibrary } from "@/lib/types";
import type { MaterialSelectionMode } from "@/lib/types/creation-plans";
import { HomeCardDialog } from "../HomeCardDialog";
import { visibleError } from "../material-library/material-errors";
import { duration } from "../material-library/material-labels";
import { Button } from "../ui/button";
import styles from "./WorkMaterialReview.module.css";

const PAGE_SIZE = 12;
const SELECTION_LIMIT = 64;

type Props = {
  taskId: string;
  planUpdatedAt: string;
  selectedAssetIds: string[];
  selectionMode?: MaterialSelectionMode;
  sourceStarts?: Record<string, number>;
  disabled?: boolean;
  onSaved: (receipt: MaterialScopeReceipt) => void;
};

function eligible(asset: MaterialAsset) {
  return asset.media_type === "video" && asset.source_available !== false &&
    asset.analysis_status === "ready" && asset.review_status !== "rejected" &&
    asset.usage_role !== "exclude" &&
    (asset.use_state === "available" || asset.use_state === "not_for_publish") &&
    asset.segments.some(segment => segment.review_status !== "rejected" && segment.usage_role !== "exclude");
}

function ScopeDialog({ taskId, planUpdatedAt, selectedAssetIds, selectionMode, sourceStarts, disabled, onSaved, onClose }: Props & {
  onClose: () => void;
}) {
  const [selection, setSelection] = useState(() => new Map<string, MaterialAsset | null>(
    selectedAssetIds.map(id => [id, null]),
  ));
  const [startInputs, setStartInputs] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(sourceStarts ?? {}).map(([id, seconds]) => [id, String(seconds)])),
  );
  const [mode, setMode] = useState<MaterialSelectionMode>(selectionMode ?? "automatic");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [readCount, setReadCount] = useState(0);
  const [page, setPage] = useState<MaterialLibrary>();
  const [listError, setListError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [checkingSelection, setCheckingSelection] = useState(selectedAssetIds.length > 0);
  const [saving, setSaving] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const pending = useRef<MaterialScopeRequest | null>(null);
  const active = useRef(false);
  const originalRevision = useRef(planUpdatedAt);
  const originalSelection = useRef(selectedAssetIds);
  const planChanged = planUpdatedAt !== originalRevision.current;
  const blocked = [...selection.values()].some(asset => asset === null || !eligible(asset));
  const invalidStart = mode === "source_order" && [...selection].some(([id, asset]) => {
    const seconds = Number(startInputs[id] || 0);
    return !Number.isFinite(seconds) || seconds < 0 ||
      (asset?.duration_seconds != null && seconds >= asset.duration_seconds);
  });
  const editable = !disabled && !saving && !uncertain && !checkingSelection && !planChanged;

  useEffect(() => {
    let current = true;
    const ids = originalSelection.current;
    if (ids.length) {
      void Promise.allSettled(ids.map(getProductionMaterial)).then(results => {
        if (!current) return;
        setSelection(new Map(ids.map((id, index) => {
          const result = results[index];
          return [id, result.status === "fulfilled" ? result.value : null];
        })));
        setCheckingSelection(false);
      });
    }
    return () => { current = false; };
  }, []);

  useEffect(() => {
    let current = true;
    void getProductionMaterials({ limit: PAGE_SIZE, offset, query, mediaType: "video" })
      .then(result => { if (current) setPage(result); })
      .catch(cause => { if (current) setListError(visibleError(cause, "素材列表读取失败。")); });
    return () => { current = false; };
  }, [offset, query, readCount]);

  function load(nextOffset: number, nextQuery = query) {
    setPage(undefined);
    setListError("");
    setOffset(nextOffset);
    setQuery(nextQuery);
    setReadCount(value => value + 1);
  }

  function toggle(asset: MaterialAsset) {
    setSaveError("");
    setSelection(prior => {
      const next = new Map(prior);
      if (next.has(asset.id)) next.delete(asset.id);
      else if (next.size < SELECTION_LIMIT) next.set(asset.id, asset);
      return next;
    });
  }

  function moveAsset(index: number, delta: number) {
    const items = Array.from(selection);
    const next = index + delta;
    if (next < 0 || next >= items.length) return;
    [items[index], items[next]] = [items[next], items[index]];
    setSelection(new Map(items));
    setSaveError("");
  }

  async function save() {
    if (active.current || disabled || (!uncertain && (!editable || blocked || invalidStart))) return;
    active.current = true;
    setSaving(true);
    setSaveError("");
    if (!pending.current) {
      const assets: MaterialScopeSelection[] = Array.from(selection).flatMap(([asset_id, asset]) =>
        asset ? [{
          asset_id, expected_asset_updated_at: asset.updated_at,
          ...(mode === "source_order" && { source_start_seconds: Number(startInputs[asset_id] || 0) }),
        }] : [],
      );
      pending.current = {
        request_id: crypto.randomUUID(),
        expected_plan_updated_at: originalRevision.current,
        assets,
        ...(selectionMode && { selection_mode: assets.length ? mode : "automatic" }),
      };
    }
    const input = pending.current;
    let receipt: MaterialScopeReceipt;
    try {
      if (uncertain) {
        try {
          receipt = await readMaterialScopeReceipt(taskId, input.request_id);
        } catch (cause) {
          if (!(cause instanceof ApiRequestError) || cause.status !== 404) throw cause;
          receipt = await saveMaterialScope(taskId, input);
        }
      } else {
        receipt = await saveMaterialScope(taskId, input);
      }
    } catch (cause) {
      const definitive = cause instanceof ApiRequestError && cause.status >= 400 && cause.status < 500;
      setUncertain(!definitive);
      if (definitive) pending.current = null;
      setSaveError(visibleError(cause, definitive ? "无法保存，请核对作品和素材。" : "保存结果暂未确认，请查询原请求。"));
      return;
    } finally {
      setSaving(false);
      active.current = false;
    }
    pending.current = null;
    onSaved(receipt);
    onClose();
  }

  return <HomeCardDialog title="本作品的选材范围" onClose={() => { if (!active.current && !uncertain) onClose(); }}
    footer={<>
      <Button variant="ghost" disabled={saving || uncertain} onClick={onClose}>取消</Button>
      <Button disabled={saving || disabled || (!uncertain && (!editable || blocked || invalidStart))} onClick={() => void save()}>
        {saving ? "正在核对保存" : uncertain ? "查询原请求" : "保存范围"}
      </Button>
    </>}>
    <div className={styles.review}>
      <p>{selection.size ? `本作品已选 ${selection.size} 项，自动选材将限于这些视频。` : "未指定视频，作品将使用原有选材策略。"}</p>
      {selection.size > 0 && selectionMode && <label className={styles.field}>镜头顺序
        <select value={mode} disabled={!editable} onChange={event => {
          setMode(event.target.value === "source_order" ? "source_order" : "automatic");
          setSaveError("");
        }}>
          <option value="automatic">按文案匹配镜头</option>
          <option value="source_order">按原片顺序</option>
        </select>
      </label>}
      {selection.size > 0 && <div>
        <ol className={styles.selectedAssets} aria-label="已选视频">
          {[...selection].map(([id, asset], index) => <li key={id}>
            <div className={styles.assetRow}>
              <span>{asset?.file_name ?? (checkingSelection ? "正在读取已选素材" : "素材不可读取")}</span>
              <div className={styles.assetActions}>
                {mode === "source_order" && selection.size > 1 && <>
                  <Button variant="ghost" aria-label={`上移 ${asset?.file_name ?? id}`} disabled={!editable || index === 0}
                    onClick={() => moveAsset(index, -1)}><ChevronUp size={15} aria-hidden /></Button>
                  <Button variant="ghost" aria-label={`下移 ${asset?.file_name ?? id}`} disabled={!editable || index === selection.size - 1}
                    onClick={() => moveAsset(index, 1)}><ChevronDown size={15} aria-hidden /></Button>
                </>}
                <Button variant="ghost" disabled={!editable} onClick={() => {
                  setSelection(new Map([...selection].filter(([key]) => key !== id)));
                  setSaveError("");
                }}>移除</Button>
              </div>
            </div>
            {mode === "source_order" && asset && <label className={styles.sourceIn}>
              从原片第
              <input type="number" min="0" step="any" disabled={!editable}
                aria-label={`${asset.file_name} 起点（秒）`}
                value={startInputs[id] ?? "0"} onChange={event => {
                  setStartInputs({ ...startInputs, [id]: event.target.value });
                  setSaveError("");
                }} />
              秒开始
            </label>}
          </li>)}
        </ol>
        <Button variant="ghost" disabled={!editable} onClick={() => { setSelection(new Map()); setSaveError(""); }}>
          清空选择
        </Button>
      </div>}
      <form className={styles.search} onSubmit={event => { event.preventDefault(); load(0, search.trim()); }}>
        <label className={styles.field}>搜索视频素材
          <input type="search" value={search} disabled={saving || uncertain} onChange={event => setSearch(event.target.value)} />
        </label>
        <Button type="submit" variant="outline" disabled={saving || uncertain}>搜索</Button>
      </form>
      {listError ? <p role="alert" className={styles.error}>{listError}{" "}
        <Button variant="ghost" onClick={() => load(offset)}>重试</Button>
      </p> : !page ? <p role="status">正在读取素材…</p> : page.assets.length === 0 ?
        <p role="status">没有找到视频素材。</p> : <>
          <fieldset className={styles.roles} disabled={!editable}>
            <legend>可选视频</legend>
            <ul className={styles.results}>
              {page.assets.map(asset => {
                const checked = selection.has(asset.id);
                const available = eligible(asset);
                return <li key={asset.id}><label>
                  <input type="checkbox" checked={checked} onChange={() => toggle(asset)}
                    disabled={!checked && (!available || selection.size >= SELECTION_LIMIT)} />
                  <strong>{asset.file_name}</strong>
                  <span>{available ? duration(asset.duration_seconds) : "当前无法用于自动选材"}</span>
                </label></li>;
              })}
            </ul>
          </fieldset>
          <div className={styles.pages}>
            <Button variant="outline" disabled={offset === 0} onClick={() => load(offset - PAGE_SIZE)}>上一页</Button>
            <Button variant="outline" disabled={!page.has_more} onClick={() => load(offset + PAGE_SIZE)}>下一页</Button>
          </div>
        </>}
      {planChanged && !uncertain && <p role="alert" className={styles.error}>制作方案已更新，请重新打开后核对素材。</p>}
      {invalidStart && <p role="alert" className={styles.error}>起点须在原片时长以内，且大于或等于 0 秒。</p>}
      {blocked && !checkingSelection && <p role="status">部分已选素材已不可用，请移除后保存。</p>}
      {saveError && <p role="alert" className={styles.error}>{saveError}</p>}
    </div>
  </HomeCardDialog>;
}

export function MaterialScopePicker(props: Props) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState("");
  const count = props.selectedAssetIds.length;
  return <>
    <Button variant="outline" disabled={props.disabled} onClick={() => { setSaved(""); setOpen(true); }}>
      {count ? `选材范围（${count} 项）${props.selectionMode === "source_order" ? " · 按原片顺序" : ""}` : "选择本作品素材"}
    </Button>
    {saved && <span role="status">{saved}</span>}
    {open && <ScopeDialog {...props} onClose={() => setOpen(false)} onSaved={receipt => {
      setSaved(receipt.selected_material_asset_ids.length ? "已保存本作品的选材范围。" : "已恢复作品原有选材策略。");
      props.onSaved(receipt);
    }} />}
  </>;
}
