import { request } from "./transport";

export type NarrationDurationStatus = {
  task_id: string;
  completed_sections: number;
  total_sections: number;
  measured_seconds: number;
  target_seconds: number;
  minimum_delivery_seconds: number;
  exceeds_target: boolean;
  review_id: string | null;
};

export type NarrationDurationRequest = { request_id: string; review_id: string };

type NarrationDurationReceipt = {
  request_id: string;
  task_id: string;
  job_id: string;
  plan_updated_at: string;
  duration_target_seconds: number;
  measured_seconds: number;
  completed_sections: number;
};

function path(taskId: string) {
  return `/api/creation-tasks/${encodeURIComponent(taskId)}/narration/duration`;
}

export function readNarrationDuration(taskId: string) {
  return request<NarrationDurationStatus>(path(taskId));
}

export function adoptNarrationDuration(taskId: string, input: NarrationDurationRequest) {
  return request<NarrationDurationReceipt>(path(taskId), { method: "PUT", body: JSON.stringify(input) });
}
