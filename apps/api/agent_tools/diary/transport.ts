import type {TaskVersion,ActionSource} from './contracts.ts';
import {randomUUID} from 'node:crypto';

export type ScriptCandidateLocation = {
  candidate_section_index: number;
  candidate_field: "title" | "content" | "visual_prompt";
  candidate_fragment_index: number;
  candidate_field_digest: string;
};

export type ScriptReviewIssue = ScriptCandidateLocation & {
  error: "script_fact_unsupported" | "script_goal_conflict";
  reason_code: "unsupported_fact" | "goal_conflict";
  section_index: number;
  review_explanation: string;
};

export type ScriptDraftFailure = Partial<ScriptCandidateLocation> & {
  error: "script_review_invalid" | "script_fact_unsupported" | "script_goal_conflict";
  reason?: string;
  repair_action?: string;
  reason_code: string;
  section_index: number | null;
  retryable: boolean;
  review_issues?: ScriptReviewIssue[];
};

export function safeScriptCandidateLocation(value: any): ScriptCandidateLocation | undefined {
  if (!["script_fact_unsupported", "script_goal_conflict"].includes(value?.error)) return undefined;
  const section = value.candidate_section_index, field = value.candidate_field;
  const fragment = value.candidate_fragment_index, digest = value.candidate_field_digest;
  if (!Number.isInteger(value.section_index) || value.section_index < 1 || value.section_index > 20
      || !Number.isInteger(section) || section < 0 || section > 20
      || !["title", "content", "visual_prompt"].includes(field)
      || (section === 0 ? field !== "title" : section !== value.section_index)
      || !Number.isInteger(fragment) || fragment < 0
      || fragment > (field === "title" ? 0 : field === "visual_prompt" ? 4 : 19)
      || typeof digest !== "string" || !/^[a-f0-9]{64}$/.test(digest)) return undefined;
  return {candidate_section_index: section, candidate_field: field,
    candidate_fragment_index: fragment, candidate_field_digest: digest};
}

export type BusinessFailure = Record<string, string | number | boolean | null | ScriptReviewIssue[]> & {
  error: string; reason: string; repair_action: string;
  stage: string; submission_effect: "not_submitted" | "rejected" | "unknown";
};

export function credentialSecrets(credentials: {authorization?: string; cookie?: string; localMemberToken?: string}): string[] {
  return Object.values(credentials).filter((value): value is string => !!value)
    .flatMap(value => [value, value.replace(/^Bearer\s+/i, ""), ...value.split(";").map(part => part.slice(part.indexOf("=") + 1).trim())]);
}

export function safeFailureText(value: unknown, secrets: string[] = []): string | undefined {
  if (typeof value !== "string") return undefined;
  let text = value;
  for (const secret of secrets.filter(Boolean)) text = text.replaceAll(secret, "[已隐藏]");
  text = text.replace(/\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/gi, "[已隐藏]")
    .replace(/\b(?:authorization|cookie|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+/gi, "[已隐藏]")
    .replace(/https?:\/\/[^\s）)；;]+|(?:[A-Za-z]:\\|\/(?!api\/)[A-Za-z0-9_.~-]+\/)[^\s）)；;]+/g, "[已隐藏]")
    .replace(/[\u0000-\u001f\u007f]/g, " ");
  return Array.from(text).slice(0, 500).join("");
}

export function safeScriptReviewIssues(value: any, secrets: string[] = []): ScriptReviewIssue[] | undefined {
  const issues = value?.review_issues;
  if (!["script_fact_unsupported", "script_goal_conflict"].includes(value?.error)
      || !Array.isArray(issues) || issues.length < 1 || issues.length > 12) return undefined;
  const safe: ScriptReviewIssue[] = [];
  for (const issue of issues) {
    const location = safeScriptCandidateLocation(issue);
    if (!location || issue.reason_code !== (issue.error === "script_fact_unsupported" ? "unsupported_fact" : "goal_conflict")
        || typeof issue.review_explanation !== "string" || !issue.review_explanation.trim()
        || Array.from(issue.review_explanation).length > 300) continue;
    const explanation = safeFailureText(issue.review_explanation, secrets)!;
    safe.push({error: issue.error, reason_code: issue.reason_code, section_index: issue.section_index,
      ...location, review_explanation: Array.from(explanation).slice(0, 300).join("")});
  }
  if (!safe.length) return undefined;
  // Keep already-clean receipts stable through the existing runtime decorator.
  if (safe.length === issues.length && safe.every((item, index) => Object.keys(item).length === Object.keys(issues[index]).length
      && Object.entries(item).every(([key, value]) => issues[index][key] === value))) return issues;
  return safe;
}

// Only business locators and finite diagnostics cross into model input/events.
export function safeBusinessFailure(value: any, secrets: string[] = []): Partial<BusinessFailure> {
  const safe: Record<string, string | number | boolean | null | ScriptReviewIssue[]> = {};
  for (const key of ["reason", "impact", "repair_action"]) {
    const text = safeFailureText(value?.[key], secrets);
    if (text) safe[key] = text;
  }
  for (const key of ["error", "code", "stage", "object_type", "query_tool", "reason_code", "failure_category"])
    if (typeof value?.[key] === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(value[key])) safe[key] = value[key];
  for (const key of ["request_id", "window_id", "current_window_id", "plan_id", "task_id", "object_id", "job_id"])
    if (typeof value?.[key] === "string" && /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value[key])
        && !secrets.some(secret => secret && value[key].includes(secret))) safe[key] = value[key];
  if (typeof value?.request_key === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.request_key))
    safe.request_key = value.request_key;
  for (const key of ["expected_revision", "current_revision"])
    if (Number.isSafeInteger(value?.[key]) && value[key] >= 0) safe[key] = value[key];
  for (const key of ["expected_input_fingerprint", "current_input_fingerprint"])
    if (typeof value?.[key] === "string" && /^[a-f0-9]{64}$/.test(value[key])) safe[key] = value[key];
  if (["not_submitted", "rejected", "unknown"].includes(value?.submission_effect)) safe.submission_effect = value.submission_effect;
  if (typeof value?.query_url === "string" && /^\/api\/(?:creation-requests|creation-tasks)\/[A-Za-z0-9_/-]{1,350}(?:\?phase=(?:apply|preview))?$/.test(value.query_url))
    safe.query_url = value.query_url;
  // Existing script-review consumers use these exact fields.
  if (typeof value?.retryable === "boolean") safe.retryable = value.retryable;
  if (value?.section_index === null || (Number.isInteger(value?.section_index) && value.section_index >= 1 && value.section_index <= 20))
    safe.section_index = value.section_index;
  Object.assign(safe, safeScriptCandidateLocation(value));
  const issues = safeScriptReviewIssues(value, secrets);
  if (issues) safe.review_issues = issues;
  for (const [key, text] of Object.entries(safe))
    if (typeof text === "string" && secrets.some(secret => secret && text.includes(secret))) delete safe[key];
  return safe;
}

const reviewReasons = new Set(["envelope", "length", "tool_finish", "tool_call_count", "tool_type", "tool_name",
  "tool_arguments", "tool_arguments_shape", "section_count", "section_shape", "index", "bool", "support_shape",
  "support_quote", "source_quote", "support_reference", "issue_shape", "issue_quote", "issue_reference", "issue_flags", "issue_count"]);

function scriptDraftFailure(detail: any, secrets: string[]): ScriptDraftFailure | undefined {
  const error = detail?.error;
  if (!["script_review_invalid", "script_fact_unsupported", "script_goal_conflict"].includes(error)) return undefined;
  const reason = detail.reason_code;
  const validReason = error === "script_review_invalid" ? reviewReasons.has(reason)
    : reason === (error === "script_fact_unsupported" ? "unsupported_fact" : "goal_conflict");
  const issues = safeScriptReviewIssues(detail, secrets);
  return {error, reason_code: validReason ? reason : "unavailable",
    ...(safeFailureText(detail.reason, secrets) ? {reason: safeFailureText(detail.reason, secrets)} : {}),
    ...(safeFailureText(detail.repair_action, secrets) ? {repair_action: safeFailureText(detail.repair_action, secrets)} : {}),
    section_index: Number.isInteger(detail.section_index) && detail.section_index >= 1 && detail.section_index <= 20
      ? detail.section_index : null,
    retryable: detail.retryable === true, ...safeScriptCandidateLocation(detail), ...(issues ? {review_issues: issues} : {})};
}

export class BusinessRequestError extends Error {
  httpStatus: number;
  repairAction?: string;
  scriptFailure?: ScriptDraftFailure;
  businessFailure?: BusinessFailure;
  constructor(message: string, status: number, repairAction?: string, scriptFailure?: ScriptDraftFailure, businessFailure?: BusinessFailure) {
    super(message); this.httpStatus = status; this.repairAction = repairAction; this.scriptFailure = scriptFailure;
    this.businessFailure = businessFailure;
  }
}

export class RequestNotSubmitted extends Error {
  businessFailure?: BusinessFailure;
  constructor(message: string, failure?: BusinessFailure) {super(message); this.businessFailure = failure;}
}

export class BusinessResultUnknown extends Error {
  businessFailure: BusinessFailure;
  constructor(failure: BusinessFailure) {super(failure.reason); this.businessFailure = failure;}
}

export type HostBusinessCall = (operation: string, arguments_: Record<string, any>, options: {
  callId: string; toolCallId?: string; signal: AbortSignal;
}) => Promise<{ok: boolean; value?: any; failure?: any}>;

// Only these existing task consumers can cross the background host boundary.
// Paths remain an internal tool implementation detail, never a host command.
function hostOperation(path: string, method: string, payload: any): {operation: string; arguments: Record<string, any>} | null {
  const [pathname, search = '', extra] = path.split('?');
  if (extra !== undefined) return null;
  const query = new URLSearchParams(search);
  const only = (...keys: string[]) => [...query.keys()].every(key => keys.includes(key) && query.getAll(key).length === 1);
  const result = (operation: string, args: Record<string, any> = {}) => ({operation, arguments: args});
  if (method === 'GET') {
    if (pathname === '/api/assistant/memory' && !search) return result('read_creator_memory');
    if (pathname === '/api/media-script-tools/capabilities' && !search) return result('read_media_script_tool_capabilities');
    if (pathname === '/api/production/materials/search' && only('q', 'limit', 'purpose', 'include_eligibility'))
      return result('search_task_materials', {query: query.get('q') || '', limit: Number(query.get('limit') || 8),
        purpose: query.get('purpose') || 'manual', include_eligibility: query.get('include_eligibility') === 'true'});
    const material = pathname.match(/^\/api\/production\/materials\/([^/]+)$/);
    if (material && !search) return result('read_task_material', {asset_id: decodeURIComponent(material[1])});
  }
  const task = pathname.match(/^\/api\/creation-tasks\/([^/]+)(.*)$/);
  if (!task) return null;
  const args = {task_id: decodeURIComponent(task[1])};
  const suffix = task[2];
  if (method === 'GET') {
    if (!suffix && !search) return result('read_task_workspace', args);
    if (suffix === '/execution' && only('job_id')) return result('read_creation_execution',
      {...args, ...(query.has('job_id') ? {job_id: query.get('job_id')} : {})});
    const reads: Record<string, string> = {
      '/execution/preview': 'read_creation_execution_preview',
      '/assistant/script-context': 'read_assistant_script_context',
      '/assistant/edit-capabilities': 'read_task_edit_capabilities',
      '/assistant/media-script-capabilities': 'read_media_script_capabilities',
      '/observations': 'read_task_observations',
    };
    if (reads[suffix] && !search) return result(reads[suffix], args);
    const segmentContext=suffix.match(/^\/segments\/([^/]+)\/context$/);
    if (segmentContext && !search) return result('read_narration_segment_context',{...args,segment_id:decodeURIComponent(segmentContext[1])});
    const segmentRequest=suffix.match(/^\/segment-requests\/([^/]+)$/);
    if (segmentRequest && !search) return result('read_narration_revision',{...args,request_id:decodeURIComponent(segmentRequest[1])});
    const observationRequest = suffix.match(/^\/assistant\/edit-requests\/([^/]+)$/);
    if (observationRequest && only('phase') && query.get('phase') === 'observation')
      return result('read_task_observation_request', {...args, request_id: decodeURIComponent(observationRequest[1])});
    const mediaJob = suffix.match(/^\/media-jobs\/([^/]+)$/);
    if (mediaJob && !search) return result('read_task_media_job', {...args, job_id: decodeURIComponent(mediaJob[1])});
  }
  if (method !== 'POST' || !payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  if (['/execution/start', '/execution/resume'].includes(suffix) && only('assistant_turn_id'))
    return result(suffix.endsWith('/start') ? 'start_creation_execution' : 'resume_creation_execution',
      {...args, assistant_turn_id: query.get('assistant_turn_id'), payload});
  if (search) return null;
  const writes: Record<string, string> = {
    '/assistant/input-understanding':'record_input_understanding',
    '/assistant/narration-semantic-review':'record_narration_semantic_review',
    '/assistant/script-draft': 'save_assistant_script_draft',
    '/execution/cancel': 'cancel_creation_execution',
    '/topics/generate': 'generate_creation_task_topics',
    '/observations': 'observe_task_output',
  };
  if (writes[suffix]) return result(writes[suffix], {...args, payload});
  const segmentRevision=suffix.match(/^\/segments\/([^/]+)\/revision$/);
  if (segmentRevision) return result('revise_narration_segment',{...args,segment_id:decodeURIComponent(segmentRevision[1]),payload});
  const script = suffix.match(/^\/scripts\/([^/]+)\/confirm$/);
  if (script) return result('confirm_creation_task_script', {...args, script_id: decodeURIComponent(script[1]), payload});
  const topic = suffix.match(/^\/topics\/([^/]+)\/confirm$/);
  return topic ? result('confirm_creation_task_topic', {...args, candidate_id: decodeURIComponent(topic[1]), payload}) : null;
}

export function createBusinessRequest({apiBase, taskId, selectedTaskVersion, verifyTaskVersion, readCredentials, readActionSource, getSignal, getToolCallId, fetchRequest, businessCall, onFailure}: {
  apiBase?: string; taskId?: string; selectedTaskVersion: TaskVersion | null;
  verifyTaskVersion: (workspace: any) => void;
  readCredentials: () => {authorization?: string; cookie?: string; localMemberToken?: string};
  readActionSource?:()=>ActionSource;
  getSignal: () => AbortSignal | undefined; fetchRequest: typeof fetch;
  businessCall?: HostBusinessCall;
  getToolCallId?: () => string | undefined;
  onFailure?: (failure: BusinessFailure) => void;
}) {
  async function request(path: string, init?: RequestInit, reportFailure = true) {
    const credentials = businessCall ? {} : readCredentials();
    const secrets = credentialSecrets(credentials);
    const write = !["GET", "HEAD"].includes((init?.method || "GET").toUpperCase());
    const segments = path.split("?")[0].split("/");
    let requestBody: any;
    if (typeof init?.body === "string") {
      try { requestBody = JSON.parse(init.body); } catch { /* A malformed body is not a source of trusted locators. */ }
    }
    const locators = safeBusinessFailure({request_id: requestBody?.request_id, request_key: requestBody?.request_key,
      expected_revision: requestBody?.expected_revision, expected_input_fingerprint: requestBody?.input_fingerprint,
      ...(segments[2] === "creation-requests" && segments[3] ? {request_id: segments[3], query_url: `/api/creation-requests/${segments[3]}`,
        ...(segments[4] === "windows" ? {window_id: segments[5]} : {})} : {}),
      ...(segments[2] === "creation-tasks" ? {task_id: segments[3], query_url: `/api/creation-tasks/${segments[3]}`} : {})}, secrets);
    if (locators.request_key && path === "/api/creation-requests" && write) locators.query_tool = "read_creation_request";
    if (locators.task_id && locators.request_id) {
      const root = `/api/creation-tasks/${locators.task_id}`;
      if (path.split("?")[0] === `${root}/edits/exact` || path.split("?")[0] === `${root}/edits/exact/preview`)
        locators.query_url = `${root}/assistant/edit-requests/${locators.request_id}?phase=${path.endsWith("/preview") ? "preview" : "apply"}`;
      if (path === `${root}/montage/plan` || path === `${root}/narration/voice`)
        locators.query_url = `${path}/requests/${locators.request_id}`;
    }
    const reject = (error: RequestNotSubmitted | BusinessRequestError | BusinessResultUnknown): never => {
      if (reportFailure && error.businessFailure) onFailure?.(error.businessFailure);
      throw error;
    };
    const notSubmitted = (error: string, reason: string, stage: string) => reject(new RequestNotSubmitted(reason,
      {...locators, error, reason, repair_action: "重新读取当前对象和原请求，处理阻断后再提交。", stage, submission_effect: "not_submitted"}));
    const operation = businessCall ? hostOperation(path, (init?.method || 'GET').toUpperCase(), requestBody) : null;
    if (businessCall && !operation) return notSubmitted('business_operation_not_supported', '该操作未接入原目标后台执行，请求未提交。', 'capability');
    if (!businessCall && !apiBase) return notSubmitted("business_service_unavailable", "本地业务服务不可用，请求未提交。", "connection");
    if (taskId && selectedTaskVersion && init?.method && init.method !== "GET" && path.startsWith(`/api/creation-tasks/${encodeURIComponent(taskId)}/`)) {
      try {
        const current = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}`, undefined, false);
        verifyTaskVersion(current);
      } catch (error) {
        const original = error instanceof BusinessRequestError || error instanceof BusinessResultUnknown || error instanceof RequestNotSubmitted
          ? error.businessFailure : undefined;
        if (original) {
          const failure: BusinessFailure = {...original, ...locators, stage: "task_version_preflight", submission_effect: "not_submitted"};
          return reject(new RequestNotSubmitted(failure.reason, failure));
        }
        return notSubmitted("task_version_preflight_failed", "当前任务版本核验未通过，写入请求未提交。", "task_version_preflight");
      }
    }
    const activeSignal = getSignal();
    const timeoutMs = 120000;
    const timeout = AbortSignal.timeout(timeoutMs);
    const signal = AbortSignal.any([timeout, ...(activeSignal ? [activeSignal] : []), ...(init?.signal ? [init.signal] : [])]);
    if (signal.aborted) return notSubmitted("request_aborted_before_submit", "调用已停止，请求未提交。", "connection");
    const unknown = (error: string, stage: string) => reject(new BusinessResultUnknown({...locators, error, stage,
      reason: write ? "业务请求响应中断，写入结果未知。" : "业务读取未取得有效响应。", submission_effect: write ? "unknown" : "not_submitted",
      repair_action: write ? "先查询原请求及当前对象，确定未写入后才能修订并再次提交；不要盲重试。" : "重新读取原请求及当前对象状态。"}));
    const toolCallId = getToolCallId?.();
    const requestId = randomUUID();
    const phase = (value: "start" | "end") => {
      if (toolCallId) process.stdout.write(JSON.stringify({type: "diary_business_request", phase: value,
        tool_call_id: toolCallId, request_id: requestId, write, timeout_ms: timeoutMs}) + "\n");
    };
    phase("start");
    try {
      let status: number, detail: any;
      if (businessCall) {
        let receipt;
        try { receipt = await businessCall(operation!.operation, operation!.arguments, {callId: requestId, toolCallId, signal}); }
        catch { return unknown('business_host_response_lost', 'connection'); }
        if (receipt.ok === true) return receipt.value;
        if (receipt.ok !== false || !receipt.failure || !Number.isInteger(receipt.failure.status_code))
          return unknown('business_host_response_unreadable', 'response_decode');
        status = receipt.failure.status_code;
        detail = receipt.failure;
      } else {
        const headers = new Headers(init?.headers);
        headers.set('Content-Type', 'application/json');
        const {authorization, cookie, localMemberToken} = credentials;
        if (authorization) headers.set('Authorization', authorization);
        if (cookie) headers.set('Cookie', cookie);
        if (localMemberToken) headers.set('X-Local-Member-Token', localMemberToken);
        const inputId = readActionSource?.().inputId;
        if (inputId) headers.set('X-Assistant-Input-ID', inputId);
        let response: Response;
        try { response = await fetchRequest(`${apiBase}${path}`, {...init, headers, signal}); }
        catch { return unknown('business_connection_lost', 'connection'); }
        let body: any;
        try { body = await response.json(); }
        catch { return unknown('business_response_unreadable', 'response_decode'); }
        if (response.ok) return body;
        status = response.status;
        detail = typeof body?.detail === 'string' ? {reason: body.detail} : body?.detail;
      }
      {
        const scriptFailure = scriptDraftFailure(detail, secrets);
        const safe = safeBusinessFailure(detail, secrets);
        const effect = safe.submission_effect ?? (status < 500 || scriptFailure ? "rejected" : "unknown");
        const failure: BusinessFailure = {...locators, ...safe, error: safe.error || safe.code || `http_${status}`,
          stage: safe.stage || "business_request", submission_effect: effect,
          ...(scriptFailure ? {failure_category: scriptFailure.error === "script_review_invalid" ? "protocol_invalid" : "content_rejected"} : {}),
          reason: safe.reason || (scriptFailure?.error === "script_review_invalid" ? "核查报告无效，尚不能判定正文是否通过；本次送审稿未保存。"
            : scriptFailure ? "本次送审稿未通过内容核查，未保存。" : `业务操作失败（${status}）。`),
          repair_action: effect === "unknown" ? ("先查询原请求及当前对象，确定未写入后才能修订并再次提交；不要盲重试。"
            + (safe.repair_action ? ` 确定未写入后再处理：${safe.repair_action}` : "")).slice(0, 500)
            : safe.repair_action || "读取当前对象和原请求，按拒绝原因修订后再提交。"};
        if (effect === "unknown") return reject(Object.assign(new BusinessResultUnknown(failure), {httpStatus: status}));
        if (effect === "not_submitted") return reject(Object.assign(new RequestNotSubmitted(failure.reason, failure), {httpStatus: status}));
        return reject(new BusinessRequestError(failure.reason, status, failure.repair_action, scriptFailure, failure));
      }
    } finally {
      phase("end");
    }
  }

  return (path: string, init?: RequestInit) => request(path, init);
}
