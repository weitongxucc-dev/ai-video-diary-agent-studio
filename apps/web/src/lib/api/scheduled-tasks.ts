import type { ScheduledTask, ScheduledTasksResponse, ScheduledTaskCreateInput } from '../types/scheduled-tasks';
export type { ScheduledTaskType, ScheduledTaskTriggerMode, ScheduledTask, ScheduledTasksResponse, ScheduledTaskCreateInput } from '../types/scheduled-tasks';
import { creationPlanRequest } from './creation-transport';
export function listScheduledTasks(): Promise<ScheduledTasksResponse> {
    return creationPlanRequest("/api/scheduled-tasks");
}
export function createScheduledTask(input: ScheduledTaskCreateInput): Promise<ScheduledTask> {
    return creationPlanRequest("/api/scheduled-tasks", { method: "POST", body: JSON.stringify(input) });
}
export function setScheduledTaskEnabled(taskId: string, enabled: boolean): Promise<ScheduledTask> {
    return creationPlanRequest(`/api/scheduled-tasks/${encodeURIComponent(taskId)}/enabled`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
    });
}
export async function deleteScheduledTask(taskId: string): Promise<void> {
    await creationPlanRequest<ScheduledTask | null>(`/api/scheduled-tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}
