import type {TaskVersion,ActionSource} from './contracts.ts';

export type ScriptDraftFailure = {
  error: "script_review_invalid" | "script_fact_unsupported" | "script_goal_conflict";
  reason_code: string;
  section_index: number | null;
  retryable: boolean;
};

const reviewReasons = new Set(["envelope", "length", "tool_finish", "tool_call_count", "tool_type", "tool_name",
  "tool_arguments", "tool_arguments_shape", "section_count", "section_shape", "index", "bool", "support_shape",
  "support_quote", "source_quote", "support_reference", "issue_shape", "issue_quote", "issue_reference", "issue_flags", "issue_count"]);

function scriptDraftFailure(detail: any): ScriptDraftFailure | undefined {
  const error = detail?.error;
  if (!["script_review_invalid", "script_fact_unsupported", "script_goal_conflict"].includes(error)) return undefined;
  const reason = detail.reason_code;
  const validReason = error === "script_review_invalid" ? reviewReasons.has(reason)
    : reason === (error === "script_fact_unsupported" ? "unsupported_fact" : "goal_conflict");
  return {error, reason_code: validReason ? reason : "unavailable",
    section_index: Number.isInteger(detail.section_index) && detail.section_index >= 1 && detail.section_index <= 20
      ? detail.section_index : null,
    retryable: detail.retryable === true};
}

export class BusinessRequestError extends Error {
  httpStatus: number;
  repairAction?: string;
  scriptFailure?: ScriptDraftFailure;
  constructor(message: string, status: number, repairAction?: string, scriptFailure?: ScriptDraftFailure) {
    super(message); this.httpStatus = status; this.repairAction = repairAction; this.scriptFailure = scriptFailure;
  }
}

export class RequestNotSubmitted extends Error {}

export function createBusinessRequest({apiBase, taskId, selectedTaskVersion, verifyTaskVersion, readCredentials, readActionSource, getSignal, fetchRequest}: {
  apiBase?: string; taskId?: string; selectedTaskVersion: TaskVersion | null;
  verifyTaskVersion: (workspace: any) => void;
  readCredentials: () => {authorization?: string; cookie?: string; localMemberToken?: string};
  readActionSource?:()=>ActionSource;
  getSignal: () => AbortSignal | undefined; fetchRequest: typeof fetch;
}) {
  async function request(path: string, init?: RequestInit) {
    if (!apiBase) throw new Error("本地业务服务不可用");
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    const {authorization, cookie, localMemberToken} = readCredentials();
    if (authorization) headers.set("Authorization", authorization);
    if (cookie) headers.set("Cookie", cookie);
    if (localMemberToken) headers.set("X-Local-Member-Token", localMemberToken);
    const inputId=readActionSource?.().inputId;
    if(inputId)headers.set("X-Assistant-Input-ID",inputId);
    if (taskId && selectedTaskVersion && init?.method && init.method !== "GET" && path.startsWith(`/api/creation-tasks/${encodeURIComponent(taskId)}/`)) {
      try {
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`);
        verifyTaskVersion(current);
      } catch (error) {
        throw new RequestNotSubmitted(error instanceof Error ? error.message : String(error));
      }
    }
    const activeSignal = getSignal();
    const timeout = AbortSignal.timeout(120000);
    const response = await fetchRequest(`${apiBase}${path}`, { ...init, headers,
      signal: AbortSignal.any([timeout, ...(activeSignal ? [activeSignal] : []), ...(init?.signal ? [init.signal] : [])]) });
    const body = await response.json();
    if (!response.ok) {
      const reason = typeof body.detail === "string" ? body.detail : body.detail?.reason;
      throw new BusinessRequestError(reason || `业务操作失败（${response.status}）`, response.status,
        body.detail?.repair_action, scriptDraftFailure(body.detail));
    }
    return body;
  }

  return request;
}
