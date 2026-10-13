import type { MaterialAssetNote } from '../types/materials';
export type { MaterialAssetNote } from '../types/materials';
import type { BusinessObjectKind, MaterialBusinessObject, MaterialBusinessObjects, MaterialUsageRecords, MaterialIntakeStatus, MaterialCopyCompleteInput, MaterialAsset, MaterialCollection, MaterialCollections, MaterialLibrary, MaterialLibraryInsights } from '../types/materials';
import type { ContentVisibility } from '../types/common';
import { apiUrl, request, authorizedFetch } from './transport';
export function getProductionMaterials(input?: {
    limit?: number;
    offset?: number;
    query?: string;
    reviewStatus?: string;
    // 精确标签筛选，由服务端在分页前生效
    tag?: string;
    // 媒体类型筛选，省略或 all 与不带这个参数等价，表示不限类型
    mediaType?: string;
}): Promise<MaterialLibrary> {
    const query = new URLSearchParams();
    if (input?.limit)
        query.set("limit", String(input.limit));
    if (input?.offset)
        query.set("offset", String(input.offset));
    if (input?.query)
        query.set("q", input.query);
    if (input?.reviewStatus && input.reviewStatus !== "all")
        query.set("review_status", input.reviewStatus);
    if (input?.tag)
        query.set("tag", input.tag);
    if (input?.mediaType && input.mediaType !== "all")
        query.set("media_type", input.mediaType);
    const suffix = query.size ? `?${query.toString()}` : "";
    return request<MaterialLibrary>(`/api/production/materials${suffix}`);
}
export function getMaterialLibraryInsights(): Promise<MaterialLibraryInsights> {
    return request<MaterialLibraryInsights>("/api/production/materials/insights");
}
export function getMaterialCollections(): Promise<MaterialCollections> {
    return request<MaterialCollections>("/api/production/material-collections");
}
export function createMaterialCollection(input: {
    name: string;
    parent_id?: string | null;
}): Promise<MaterialCollection> {
    return request<MaterialCollection>("/api/production/material-collections", {
        method: "POST",
        body: JSON.stringify(input),
    });
}
export function scanProductionMaterials(): Promise<MaterialLibrary> {
    return request<MaterialLibrary>("/api/production/materials/scan", {
        method: "POST",
    });
}
export function getMaterialIntakeStatus(): Promise<MaterialIntakeStatus> {
    return request("/api/production/materials/intake");
}
export function confirmMaterialCopyComplete(input: MaterialCopyCompleteInput): Promise<{
    confirmed: true;
    intake: MaterialIntakeStatus;
}> {
    return request("/api/production/materials/intake/copy-complete", {
        method: "POST",
        body: JSON.stringify(input),
    });
}
export function bindMaterialIntake(expectedDirectory: string): Promise<MaterialIntakeStatus> {
    return request("/api/production/materials/intake/bind", {
        method: "POST", body: JSON.stringify({ expected_directory: expectedDirectory }),
    });
}
export function scanMaterialIntake(): Promise<MaterialLibrary> {
    return request("/api/production/materials/intake/scan", { method: "POST" });
}
export function retryMaterialIntake(assetId: string): Promise<MaterialAsset> {
    return request(`/api/production/materials/${encodeURIComponent(assetId)}/intake/retry`, { method: "POST" });
}
export function updateMaterialAssetTags(assetId: string, userTags: string[]): Promise<MaterialAsset> {
    return request(`/api/production/materials/${encodeURIComponent(assetId)}/tags`, {
        method: "PUT", body: JSON.stringify({ user_tags: userTags }),
    });
}
export function updateMaterialAssetTagsBatch(assetIds: string[], add: string[], remove: string[]): Promise<{
    assets: MaterialAsset[];
}> {
    return request("/api/production/materials/tags/batch", {
        method: "POST", body: JSON.stringify({ asset_ids: assetIds, add, remove }),
    });
}
export function updateMaterialNotes(items: MaterialAssetNote[]): Promise<{
    assets: MaterialAsset[];
}> {
    return request("/api/production/materials/notes", {
        method: "PUT", body: JSON.stringify({ items }),
    });
}
export async function transcribeMaterialVoiceNote(audio: Blob, signal?: AbortSignal): Promise<string> {
    if (!audio.size)
        throw new Error("没有录到声音，请重录或直接输入说明。");
    const mediaType = audio.type.split(";")[0].trim().toLowerCase();
    const suffix = ({ "audio/webm": "webm", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
        "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav" } as Record<string, string>)[mediaType];
    if (!suffix)
        throw new Error("当前录音格式无法转写，请直接输入说明或换支持录音的浏览器。");
    const body = new FormData();
    body.append("audio", audio, `素材口述录音.${suffix}`);
    body.append("local_only", "true");
    const response = await authorizedFetch(apiUrl("/api/voice/transcribe"), {
        method: "POST", body, signal, cache: "no-store",
    });
    const payload = await response.json();
    if (!response.ok)
        throw new Error(typeof payload.detail === "string" ? payload.detail : "转写失败，请重试或直接输入说明。");
    if (typeof payload.transcript !== "string" || !payload.transcript.trim()) {
        throw new Error("录音未转成可用文字，请重试或直接输入说明。");
    }
    return payload.transcript.trim();
}
/** 当前工作区已有的产品/菜品/人物名称条目，供用户选择关联。 */
export function getMaterialBusinessObjects(): Promise<MaterialBusinessObjects> {
    return request<MaterialBusinessObjects>("/api/production/business-objects");
}
/** 新增一条名称条目。不会自动关联到任何素材，仍需用户明确选择保存。 */
export function createMaterialBusinessObject(input: {
    kind: BusinessObjectKind;
    name: string;
}): Promise<MaterialBusinessObject> {
    return request<MaterialBusinessObject>("/api/production/business-objects", {
        method: "POST", body: JSON.stringify(input),
    });
}
/** 这条素材当前已保存的名称条目。 */
export function getAssetBusinessObjects(assetId: string): Promise<MaterialBusinessObjects> {
    return request<MaterialBusinessObjects>(`/api/production/materials/${encodeURIComponent(assetId)}/business-objects`);
}
/**
 * 把这条素材的名称关联换成用户选中的那几条。只写这一个素材的关联，
 * 不改它的说明、公共背景、片段描述或人工纠正；空数组清空关联。
 */
export function saveAssetBusinessObjects(assetId: string, businessObjectIds: string[]): Promise<MaterialBusinessObjects> {
    return request<MaterialBusinessObjects>(`/api/production/materials/${encodeURIComponent(assetId)}/business-objects`, { method: "PUT", body: JSON.stringify({ business_object_ids: businessObjectIds }) });
}
/**
 * 这一个片段被哪条成片用过。只读既有渲染写入的记录，不改任何计数。
 * 返回的 timeline_version_id 是任务当前版本，不代表当次使用时的版本。
 */
export function getAssetSegmentUsageRecords(assetId: string, segmentId: string): Promise<MaterialUsageRecords> {
    return request<MaterialUsageRecords>(`/api/production/materials/${encodeURIComponent(assetId)}/usage-records` +
        `?segment_id=${encodeURIComponent(segmentId)}`);
}
export function analyzeProductionMaterial(assetId: string): Promise<MaterialAsset> {
    return request<MaterialAsset>(`/api/production/materials/${assetId}/analyze`, {
        method: "POST",
    });
}
export function reviewProductionMaterial(assetId: string, input: {
    review_status: "raw" | "approved" | "rejected";
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
}): Promise<MaterialAsset> {
    return request<MaterialAsset>(`/api/production/materials/${assetId}/review`, {
        method: "PUT",
        body: JSON.stringify(input),
    });
}
export function setMaterialVisibility(assetId: string, visibility: ContentVisibility): Promise<MaterialAsset> {
    return request<MaterialAsset>(`/api/production/materials/${encodeURIComponent(assetId)}/visibility`, { method: "PUT", body: JSON.stringify({ visibility }) });
}
export function updateMaterialUseState(assetId: string, useState: MaterialAsset["use_state"]): Promise<MaterialAsset> {
    return request<MaterialAsset>(`/api/production/materials/${assetId}/use-state`, {
        method: "PUT", body: JSON.stringify({ use_state: useState }),
    });
}
export function updateProductionMaterialSegment(assetId: string, segmentId: string, input: {
    description: string;
    transcript: string;
    review_status: "raw" | "approved" | "rejected";
    usage_role: "unassigned" | "a_roll" | "b_roll" | "exclude";
    user_tags?: string[];
    collection_ids?: string[];
}): Promise<MaterialAsset> {
    return request<MaterialAsset>(`/api/production/materials/${assetId}/segments/${segmentId}`, {
        method: "PUT",
        body: JSON.stringify(input),
    });
}
