/**
 * 首页整机聚合（GET /api/production/home?days=7|30）。
 *
 * 口径与 98 的响应一致：每块都带 meta（scope/source/as_of），回答「这是谁的数、
 * 按什么来源、什么时候的」。device_queue 只有匿名计数；entries 里每条都带真实
 * task_id，可直接跳转。没有真实载体的项进 unknown，页面不猜也不填 0。
 */
export type HomeFactMeta = {
    scope: "caller_visible" | "local_diagnostic" | "workspace" | "device_anonymous" | "host";
    source: string;
    as_of: string;
};
export type HomeCountFact = {
    key: string;
    label: string;
    value: number;
    meta: HomeFactMeta;
};
export type HomeTrendFact = {
    days: number;
    points: {
        date: string;
        count: number;
    }[];
    meta: HomeFactMeta;
};
export type HomeDeviceQueueFact = {
    /** 状态键形如 "job.running" / "intake.observing"；读不到时为空对象。 */
    counts: Record<string, number>;
    /** 读不到表时为 null：页面显示未知，不显示 0。 */
    records_total: number | null;
    running: number | null;
    queued: number | null;
    meta: HomeFactMeta;
};
export type HomeStorageFact = {
    directory_state: string;
    bound_to_current_workspace: boolean;
    bound_to_another_workspace: boolean;
    configured_path_changed: boolean;
    capacity: {
        total_bytes: number;
        used_bytes: number;
        free_bytes: number;
    } | null;
    meta: HomeFactMeta;
};
export type HomeQueueEntry = {
    task_id: string;
    title: string;
    stage: string;
    status: string;
    updated_at: string;
};
export type HomeTodoEntry = {
    task_id: string;
    title: string;
    stage: string;
    reason: string;
    repair_action: string | null;
};
export type HomeWorkEntry = {
    task_id: string;
    title: string;
    updated_at: string;
};
export type HomeEntriesFact = {
    queue: HomeQueueEntry[];
    todos: HomeTodoEntry[];
    works: HomeWorkEntry[];
    meta: HomeFactMeta;
};
export type HomeUnknownFact = {
    key: string;
    reason: string;
};
export type HomeAggregation = {
    days: number;
    counts: HomeCountFact[];
    trend: HomeTrendFact;
    device_queue: HomeDeviceQueueFact;
    storage: HomeStorageFact;
    entries: HomeEntriesFact;
    unknown: HomeUnknownFact[];
};
