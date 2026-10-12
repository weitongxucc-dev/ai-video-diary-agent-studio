import { request } from "./api";
import type { CreationPlanInput, CreationRequest, CreationRequestOutputRelation } from "./types";

export type CreationRequestEntry = NonNullable<CreationRequest["current_window"]>["entries"][number];
export type CreationRequestItems = { items: CreationRequestEntry[]; next_after_ordinal: number | null; has_more: boolean };

export function createCreationRequest(input: CreationPlanInput & {
  request_key: string;
  output_relation?: CreationRequestOutputRelation;
  material_scope?: "workspace_library" | "today_received";
}): Promise<CreationRequest> {
  return request("/api/creation-requests", { method: "POST", body: JSON.stringify(input) });
}

export function getCreationRequest(id: string): Promise<CreationRequest> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}`);
}

export function updateCreationRequest(id: string, input: {
  expected_revision: number;
  output_relation: CreationRequest["output_relation"];
  requirements?: CreationRequest["requirements"];
  material_scope?: "workspace_library";
}): Promise<CreationRequest> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function prepareCreationRequestWindow(id: string, input: {
  expected_revision: number; goals: { goal: string; name?: string }[];
}): Promise<CreationRequest> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}/windows`, { method: "POST", body: JSON.stringify(input) });
}

export function confirmCreationRequestWindow(id: string, windowId: string, input: {
  expected_revision: number; input_fingerprint: string;
}): Promise<CreationRequest> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}/windows/${encodeURIComponent(windowId)}/confirm`, { method: "POST", body: JSON.stringify(input) });
}

export function replanCreationRequestWindow(id: string, windowId: string, expectedRevision: number): Promise<CreationRequest> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}/windows/${encodeURIComponent(windowId)}/replan`,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}

export function getCreationRequestItems(id: string, afterOrdinal = 0): Promise<CreationRequestItems> {
  return request(`/api/creation-requests/${encodeURIComponent(id)}/items?after_ordinal=${afterOrdinal}&limit=20`);
}
