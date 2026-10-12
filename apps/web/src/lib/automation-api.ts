import { apiUrl } from "./api";
import { localMemberTokenHeader } from "./local-member-auth";
import type { CreationPlan, CreationPlanPreview } from "./types";

export type AutomationScope = { connector_ids:string[]; knowledge_ids:string[]; material_asset_ids:string[] };
export type AutomationTrigger = { cadence:"once"|"daily"; timezone:string; start_local:string; window_start:string|null; window_end:string|null };
export type AutomationInput = { name:string;goal:string;template_id:string;template_version:number;trigger:AutomationTrigger;source_scope:AutomationScope };
export type Automation = AutomationInput & { id:string;workspace_id:string;member_id:string;revision:number;paused:boolean;status:string;next_due_at:string|null;created_at:string;updated_at:string;confirmation_policy:Record<string,"required"> };
export type AutomationRun = { id:string;automation_id:string;automation_revision:number;due_at:string;due_key:string;definition:Automation;input_snapshot:Record<string,unknown>|null;state:string;stage:string;reason_code:string|null;reason:string|null;repair_action:string|null;plan_id:string|null;task_id:string|null;job_id:string|null;confirmation:Record<string,unknown>;execution:{job_ids?:string[];device_request_ids?:string[];output_refs?:string[]};revision:number;updated_at:string };
export type AutomationOptions = { templates:{id:string;version:number;name:string}[];connectors:{id:string;label:string;status:string}[];knowledge:{id:string;label:string;version:string}[];materials:{id:string;label:string;version:string}[];driver:{running:boolean;error:string|null} };

async function request<T>(path:string,init?:RequestInit):Promise<T> {
  let response:Response;
  try { response=await fetch(apiUrl(`/api/automations${path}`),{...init,headers:{"Content-Type":"application/json",...init?.headers,...localMemberTokenHeader()},cache:"no-store"}); }
  catch { throw new Error("本地服务未接通，请恢复服务后重试。"); }
  const payload=await response.json();
  if (!response.ok) {
    const detail=payload.detail;
    throw new Error(typeof detail==="string"?detail:[detail?.message,detail?.repair_action].filter(Boolean).join("；")||`请求失败（${response.status}）`);
  }
  return payload as T;
}
const body=(input:unknown)=>JSON.stringify(input);
export const getAutomationOptions=()=>request<AutomationOptions>("/options");
export const listAutomations=()=>request<{items:Automation[];total_count:number}>("");
export const createAutomation=(input:AutomationInput)=>request<Automation>("",{method:"POST",body:body(input)});
export const reviseAutomation=(id:string,input:AutomationInput,expected_revision:number)=>request<Automation>(`/${encodeURIComponent(id)}`,{method:"PUT",body:body({...input,expected_revision})});
export const pauseAutomation=(id:string,paused:boolean,expected_revision:number)=>request<Automation>(`/${encodeURIComponent(id)}/pause`,{method:"PATCH",body:body({paused,expected_revision})});
export const listAutomationRuns=(id:string)=>request<{items:AutomationRun[];total_count:number}>(`/${encodeURIComponent(id)}/runs`);
export const retryAutomationRun=(id:string)=>request<AutomationRun>(`/runs/${encodeURIComponent(id)}/retry`,{method:"POST"});
export const previewAutomationRun=(id:string)=>request<CreationPlanPreview>(`/runs/${encodeURIComponent(id)}/preview`,{method:"POST"});
export const confirmAutomationRun=(id:string,preflight_fingerprint:string)=>request<CreationPlan>(`/runs/${encodeURIComponent(id)}/confirm`,{method:"POST",body:body({preflight_fingerprint})});

export const syncAutomationRunSources=(id:string)=>request<AutomationRun>(`/runs/${encodeURIComponent(id)}/sync-sources`,{method:"POST"});
