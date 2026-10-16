import type {TaskVersion,ActionSource} from './contracts.ts';

export class BusinessRequestError extends Error {
  httpStatus: number;
  repairAction?: string;
  constructor(message: string, status: number, repairAction?: string) { super(message); this.httpStatus = status; this.repairAction = repairAction; }
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
      throw new BusinessRequestError(reason || `业务操作失败（${response.status}）`, response.status, body.detail?.repair_action);
    }
    return body;
  }

  return request;
}
