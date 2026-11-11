"use client";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import Link from "next/link";
import { CircleAlert, CircleCheck, CircleDashed, CircleDot, CircleMinus, Film, MessageSquarePlus, Volume2 } from "lucide-react";
import type { MaterialAsset } from "@/lib/types";
import { assetDuration, fileLabel, mediaLabel, roleLabel, reviewLabel } from './material-labels';
import { useStateOptions } from './material-state';
import styles from "./MaterialResults.module.css";
import { IconButton } from "../ui/icon-button";
type Props = {
    assets: MaterialAsset[];
    selectedId: string | null;
    selectionDisabled: boolean;
    viewMode: "grid" | "list";
    hasFilters: boolean;
    hasTagFilter: boolean;
    intakeBound: boolean;
    failedThumbnails: Readonly<Record<string, boolean>>;
    onThumbnailFailure: (assetId: string) => void;
    onSelect: (asset: MaterialAsset) => void;
    onReference?: (asset: MaterialAsset) => void;
    onResetFilters: () => void;
    onImport: () => void;
};
export function MaterialResults({ assets, selectedId, selectionDisabled, viewMode, hasFilters, hasTagFilter, intakeBound, failedThumbnails, onThumbnailFailure, onSelect, onReference, onResetFilters, onImport }: Props) {
    return <>
        {assets.length ? (<div className={`library-grid ${styles.results}${viewMode === "list" ? ` is-list ${styles.objectList}` : ""}`}>
          {assets.map((asset) => {
                const AssetStateIcon = asset.analysis_status === "failed" ? CircleAlert
                    : asset.analysis_status === "blocked" ? CircleMinus : asset.use_state === "excluded" ? CircleDot
                        : asset.analysis_status === "ready" ? CircleCheck : CircleDashed;
                return (<div className={styles.referenceEntry} key={asset.id}><button className={`library-card ${styles.materialCard}${selectedId === asset.id ? " is-selected" : ""}`} data-open-details disabled={selectionDisabled} onClick={() => onSelect(asset)} type="button">
                <span className="library-card__poster">
                  {failedThumbnails[asset.id] ? <Film className="library-card__fallback-icon" size={22}/> : asset.media_type === "image" ? (<img alt="" aria-hidden="true" loading="lazy" src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} onError={() => onThumbnailFailure(asset.id)}/>) : asset.media_type === "audio" ? (<Volume2 className="library-card__fallback-icon" size={22}/>) : (<WorkspaceVideo aria-hidden="true" muted playsInline preload="metadata" src={`/api/production/materials/${encodeURIComponent(asset.id)}/media`} onError={() => onThumbnailFailure(asset.id)}/>)}
                  {asset.media_type === "video" ? (<Film className="library-card__fallback-icon" size={22}/>) : null}
                  <small>
                    {asset.media_type === "image" ? mediaLabel(asset.media_type) : assetDuration(asset.duration_seconds)}
                  </small>
                  <i className={`library-card__state state-${asset.analysis_status}`} title={asset.analysis_status === "ready" ? "分析已完成" : asset.analysis_status === "failed" ? "分析失败" : asset.analysis_status === "blocked" ? "分析受阻" : "等待分析"}>
                    <AssetStateIcon size={13}/>
                  </i>
                </span>
                <span className="library-card__copy">
                  <strong title={asset.file_name}>{fileLabel(asset.file_name)}</strong>
                  <small>
                    {mediaLabel(asset.media_type)}{asset.media_type !== "image" && viewMode === "list" ? ` · ${assetDuration(asset.duration_seconds)}` : ''} · {reviewLabel(asset)}
                    {asset.analysis_status === "pending" ? " · 等待分析" : ""}
                  </small>
                  <small>{asset.user_tags.slice(0, 2).join(" · ") || roleLabel(asset)}{asset.use_state !== "available" ? ` · ${useStateOptions.find(option => option.value === asset.use_state)?.label}` : ""}</small>
                </span>
              </button>{onReference ? <span className={styles.referenceAction}><IconButton className={styles.referenceButton} disabled={selectionDisabled} label={`引用到对话：${fileLabel(asset.file_name)}`} onClick={() => onReference(asset)}><MessageSquarePlus size={16} /></IconButton></span> : null}</div>);
            })}
          </div>) : (<div className="library-empty">
            <Film size={23}/>
            <div>
              <strong>{hasFilters ? "没有匹配的素材" : "素材库还是空的"}</strong>
              <p>
                {hasFilters
                ? "试试清除搜索词，或重置全部筛选条件。"
                : intakeBound
                    ? "导入本地文件即可开始；外部复制的文件需要先确认复制完成。"
                    : "点击导入素材，检查接入目录后选择本地文件或媒体直链。"}
              </p>
              {hasFilters ? hasTagFilter ? <Link href="/library" className={styles.resetFilters}>重置全部</Link> : <button className={styles.resetFilters} onClick={onResetFilters} type="button">重置全部</button> : <button className={styles.importButton} onClick={onImport} type="button">导入素材</button>}
            </div>
          </div>)}
  </>;
}
