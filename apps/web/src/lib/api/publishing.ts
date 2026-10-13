import type { PublishPlatformCapability, PlatformBrowserAccount, PlatformPublishJob, PublishPlatform } from '../types/publishing';
import { ensureLocalMemberIdentity, readLocalMemberToken } from "../local-member-auth";
import { isLocalAuthMode } from "../auth/routing";
import { request } from './transport';
export async function publishingRequest<T>(path: string, init?: RequestInit): Promise<T> {
    if (typeof window !== "undefined" && isLocalAuthMode() && !readLocalMemberToken()) {
        await ensureLocalMemberIdentity();
    }
    return request<T>(path, init);
}
export function listPublishingPlatforms(): Promise<{
    items: PublishPlatformCapability[];
}> {
    return publishingRequest("/api/publishing/platforms");
}
export function listPublishingAccounts(): Promise<{
    items: PlatformBrowserAccount[];
}> {
    return publishingRequest("/api/publishing/accounts");
}
export function connectPublishingAccount(platform: PublishPlatform): Promise<{
    platform: PublishPlatform;
    state: string;
    detail: string;
}> {
    return publishingRequest(`/api/publishing/accounts/${encodeURIComponent(platform)}/connect`, { method: "POST", body: JSON.stringify({}) });
}
export function verifyPublishingAccount(platform: PublishPlatform): Promise<{
    platform: PublishPlatform;
    connected: boolean;
    account_ref?: string;
    detail?: string;
}> {
    return publishingRequest(`/api/publishing/accounts/${encodeURIComponent(platform)}/verify`, { method: "POST", body: JSON.stringify({}) });
}
export function listPlatformPublishJobs(taskId: string): Promise<{
    items: PlatformPublishJob[];
}> {
    return publishingRequest(`/api/publishing/tasks/${encodeURIComponent(taskId)}/jobs`);
}
export function createPlatformPublishJob(taskId: string, input: {
    platform: PublishPlatform;
    account_ref: string;
    timeline_version_id: string;
    title: string;
    description: string;
    topics: string[];
    idempotency_key: string;
    confirmed: true;
}): Promise<PlatformPublishJob> {
    return publishingRequest(`/api/publishing/tasks/${encodeURIComponent(taskId)}/publish`, {
        method: "POST", body: JSON.stringify(input),
    });
}
