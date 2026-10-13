import type { SourcesStatus, SourceItems, SourceSyncRetryResult } from '../types/sources';
import { request } from './transport';
export function getSourceItems(): Promise<SourceItems> {
    return request<SourceItems>("/api/sources/items");
}
export function getSourcesStatus(): Promise<SourcesStatus> {
    return request<SourcesStatus>("/api/sources/sync-status");
}
export function syncSourceItems(): Promise<SourceItems> {
    return request<SourceItems>("/api/sources/sync", { method: "POST" });
}
export function retrySourceSync(sourceKey: string): Promise<SourceSyncRetryResult> {
    return request<SourceSyncRetryResult>(`/api/sources/${encodeURIComponent(sourceKey)}/retry`, {
        method: "POST",
    });
}
export function ingestManualLink(url: string): Promise<SourceItems> {
    return request<SourceItems>("/api/sources/links", {
        method: "POST",
        body: JSON.stringify({ url }),
    });
}
