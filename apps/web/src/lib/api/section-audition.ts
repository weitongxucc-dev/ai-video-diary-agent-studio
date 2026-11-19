import { request } from "./transport";

export type SectionAudition = {
  preview_run_id: string;
  output_url: string;
  duration_seconds: number;
};

export type SectionAuditionStatus = {
  available: boolean;
  reason: string | null;
  candidates: SectionAudition[];
  selection: Pick<SectionAudition, "preview_run_id" | "duration_seconds"> | null;
};

export type SectionAuditionInput = {
  request_id: string;
  preview_run_id: string | null;
  expected_preview_run_id: string | null;
  expected_script_id: string;
  expected_script_updated_at: string;
};

function path(taskId: string, sectionId: string) {
  return `/api/creation-tasks/${encodeURIComponent(taskId)}/narration/sections/${encodeURIComponent(sectionId)}/audition`;
}

export function readSectionAudition(taskId: string, sectionId: string) {
  return request<SectionAuditionStatus>(path(taskId, sectionId));
}

export function saveSectionAudition(taskId: string, sectionId: string, input: SectionAuditionInput) {
  return request<SectionAuditionStatus>(path(taskId, sectionId), { method: "PUT", body: JSON.stringify(input) });
}
