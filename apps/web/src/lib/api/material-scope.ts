import { request } from "./transport";

export type MaterialScopeSelection = {
  asset_id: string;
  expected_asset_updated_at: string;
};

export type MaterialScopeRequest = {
  request_id: string;
  expected_plan_updated_at: string;
  assets: MaterialScopeSelection[];
};

export type MaterialScopeReceipt = {
  request_id: string;
  task_id: string;
  plan_id: string;
  plan_updated_at: string;
  selected_material_asset_ids: string[];
  status: "saved" | "unchanged";
};

function path(taskId: string) {
  return `/api/creation-tasks/${encodeURIComponent(taskId)}/material-scope`;
}

export function saveMaterialScope(taskId: string, input: MaterialScopeRequest) {
  return request<MaterialScopeReceipt>(path(taskId), {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function readMaterialScopeReceipt(taskId: string, requestId: string) {
  return request<MaterialScopeReceipt>(`${path(taskId)}/${encodeURIComponent(requestId)}`);
}
