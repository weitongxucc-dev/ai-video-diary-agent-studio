export type ScheduledTaskType = "material_analysis" | "creation_render";
export type ScheduledTaskTriggerMode = "idle" | "scheduled";
export type ScheduledTask = {
    id: string;
    workspace_id: string;
    task_type: string;
    trigger_mode: string;
    target_ref: string;
    not_before_utc: string | null;
    window: Record<string, unknown> | null;
    enabled: boolean;
    created_at: string;
    updated_at: string;
};
export type ScheduledTasksResponse = {
    tasks: ScheduledTask[];
    total_count: number;
};
export type ScheduledTaskCreateInput = {
    task_type: ScheduledTaskType;
    trigger_mode: ScheduledTaskTriggerMode;
    target_ref: string;
    not_before_utc?: string | null;
    window?: Record<string, unknown> | null;
};
