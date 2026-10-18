"use client";
import { WorkspaceVideo, WorkspaceAudio } from "../workbench/WorkspaceMedia";
import Link from "next/link";
import NextImage from "next/image";
import { AlertCircle, CheckCircle2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { apiUrl, getProductionMaterials } from "@/lib/api";
import type { MaterialAsset, MaterialSegmentMatch } from "@/lib/types";
import styles from "../CreationTaskWorkspace.module.css";
export function materialAnalysisLabel(status: MaterialAsset["analysis_status"]) {
    if (status === "ready")
        return "已解析";
    if (status === "pending")
        return "待解析";
    if (status === "blocked")
        return "未解析";
    return "解析失败";
}
export function materialUseLabel(status: MaterialAsset["use_state"]) {
    return { available: "可用于创作", reference_only: "仅参考", excluded: "不使用",
        not_for_automation: "不用于自动任务", not_for_publish: "不用于发布" }[status];
}
export const PREPARE_MATERIALS_PAGE_SIZE = 100;
export function PrepareMaterialsView({ matches, approvedAssetCount, approvedClipCount }: {
    matches: MaterialSegmentMatch[];
    approvedAssetCount: number;
    approvedClipCount: number;
}) {
    const [materials, setMaterials] = useState<MaterialAsset[] | null>(null);
    const [previewAsset, setPreviewAsset] = useState<MaterialAsset | null>(null);
    const [totalCount, setTotalCount] = useState<number | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [nextOffset, setNextOffset] = useState(0);
    const [loading, setLoading] = useState(false);
    const activeRef = useRef(true);
    const loadingRef = useRef(false);
    const loadPage = useCallback(async (offset: number, replace: boolean) => {
        if (loadingRef.current)
            return;
        loadingRef.current = true;
        setLoading(true);
        setLoadError(null);
        try {
            const library = await getProductionMaterials({ limit: PREPARE_MATERIALS_PAGE_SIZE, offset });
            if (!activeRef.current)
                return;
            setMaterials((current) => {
                const assets = replace ? [] : current ?? [];
                const byId = new Map(assets.map((asset) => [asset.id, asset]));
                for (const asset of library.assets)
                    byId.set(asset.id, asset);
                return [...byId.values()];
            });
            setHasMore(library.has_more);
            setTotalCount(library.total_count);
            setNextOffset(library.offset + library.limit);
        }
        catch (cause: unknown) {
            if (!activeRef.current)
                return;
            setLoadError(cause instanceof Error ? cause.message : "素材读取失败");
        }
        finally {
            loadingRef.current = false;
            if (activeRef.current)
                setLoading(false);
        }
    }, []);
    useEffect(() => {
        activeRef.current = true;
        void loadPage(0, true);
        return () => { activeRef.current = false; };
    }, [loadPage]);
    const matchesByAsset = useMemo(() => {
        const map = new Map<string, MaterialSegmentMatch[]>();
        for (const match of matches) {
            const id = match.asset_ref.startsWith("asset:") ? match.asset_ref.slice("asset:".length) : match.asset_ref;
            map.set(id, [...(map.get(id) ?? []), match]);
        }
        return map;
    }, [matches]);
    const matchedAssets = useMemo(() => (materials ?? []).filter((asset) => matchesByAsset.has(asset.id)), [materials, matchesByAsset]);
    return (<section className={styles.workSection}>
      <div className={styles.sectionHeading}>
        <div>
          <h2>本次任务素材</h2>
          <p>回看方案检索候选与当前使用范围；候选不代表已采用镜头。补充或调整素材后需重新检查真实条件。</p>
        </div>
        <span className={styles.artifactStatus}><CheckCircle2 size={15}/>{matches.length ? `方案已记录 ${matches.length} 个检索候选` : "方案尚无匹配片段"}</span>
      </div>
      <div className={styles.artifactGrid}>
        <section><strong>检索候选</strong><p>{matches.length} 个片段，需核对画面内容及本次用途。</p></section>
        <section><strong>方案可用内容</strong><p>{approvedAssetCount} 个素材 · {approvedClipCount} 个补充镜头</p><small>这是创建方案时的可用数量；当前限制见下方素材状态。</small></section>
      </div>
      {loadError ? <div className={styles.blocker} role="alert"><AlertCircle size={17}/><span>素材列表读取失败：{loadError}；这不改变方案匹配或资料就绪状态。</span></div> : null}
      <div className={styles.evidenceList}>
        <strong>本次素材列表</strong>
        {loading && materials === null ? <p>正在读取素材…</p> : null}
        {materials !== null && matchedAssets.length === 0 && hasMore ? <p>当前已读 {materials.length} 条，尚未找到本方案匹配素材；可继续读取下一页。</p> : null}
        {materials !== null && matchedAssets.length === 0 && !hasMore ? <p>已读完素材列表；本方案暂无匹配素材，或素材解析状态缺失。</p> : null}
        {matchedAssets.map((asset) => {
            const matches = matchesByAsset.get(asset.id) ?? [];
            const tags = asset.user_tags ?? [];
            return (<article key={asset.id}>
              <span>{materialUseLabel(asset.use_state)}</span>
              <div>
                <strong>{asset.file_name}</strong>
                <small>{materialAnalysisLabel(asset.analysis_status)}{asset.duration_seconds !== null ? ` · ${asset.duration_seconds.toFixed(1)} 秒` : " · 时长未解析"}</small>
                <small>{tags.length ? tags.join(" · ") : "暂无标签"}{asset.analysis_message ? ` · ${asset.analysis_message}` : ""}</small>
                {matches.map(match => <p key={match.segment_ref}>{match.reasons.join("；") || "结构候选，需查看内容后判断"}</p>)}
                <button className={styles.secondaryButton} type="button" onClick={() => setPreviewAsset(asset)}>查看候选素材</button>
                <details><summary>候选诊断</summary>{matches.map(match => <p key={match.segment_ref}>{match.segment_ref} · 启发分值 {match.score}，用于排序，不是适配概率</p>)}</details>
              </div>
            </article>);
        })}
        {hasMore || loadError ? (<button type="button" disabled={loading} onClick={() => void loadPage(nextOffset, false)}>
            {loading ? "正在读取…" : loadError && nextOffset > 0 ? "重试读取下一页" : loadError ? "重试读取素材" : `读取更多素材（已读 ${materials?.length ?? 0}${totalCount === null ? "" : ` / ${totalCount}`}）`}
          </button>) : null}
      </div>
      {previewAsset ? <HomeCardDialog kind="detail" title={previewAsset.file_name} onClose={() => setPreviewAsset(null)}>
        <p>{materialUseLabel(previewAsset.use_state)} · {materialAnalysisLabel(previewAsset.analysis_status)}</p>
        <p>原始素材预览；是否可采用仍由当前任务的真实媒体和使用条件检查决定。</p>
        {previewAsset.media_type === "image" ? <NextImage alt={previewAsset.file_name} src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)} width={720} height={1280} unoptimized className={styles.materialPreview}/> : previewAsset.media_type === "audio" ? <WorkspaceAudio aria-label="候选素材预览" controls src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)}/> : <WorkspaceVideo aria-label="候选素材预览" className={styles.materialPreview} controls preload="metadata" src={apiUrl(`/api/production/materials/${encodeURIComponent(previewAsset.id)}/media`)}/>}
        <Link href="/library">在素材库检查来源与使用范围</Link>
      </HomeCardDialog> : null}
    </section>);
}
