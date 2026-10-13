export type PublishPlatform = "douyin" | "wechat" | "xiaohongshu";
export type PublishPlatformCapability = {
    platform: PublishPlatform;
    label: string;
    browser_connect: boolean;
    browser_publish: boolean;
    verified: boolean;
    status: "candidate_unverified" | "not_connected";
    requirement: string;
};
export type PlatformBrowserAccount = {
    workspace_id: string;
    user_id: string;
    platform: PublishPlatform;
    account_ref: string;
    verified_at: string;
};
export type PlatformPublishJob = {
    id: string;
    task_id: string;
    status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
    progress: number;
    queue_state: "waiting" | "blocked" | "granted" | "done" | "failed" | "cancelled" | "not_submitted" | string;
    queue_wait_reason: string | null;
    failure_reason: string | null;
    impact: string | null;
    repair_action: string | null;
    events: Array<{
        type: string;
        payload: Record<string, unknown>;
        created_at: string;
    }>;
};
