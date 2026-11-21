import { randomUUID } from "node:crypto";

/** This invocation's results only; restoring SDK history never replays receipts. */
export function createSessionResult(sessionId, provider = null, model = null) {
  const result = {type:"creation_session_result", protocol:1, owner:"pi-coding-agent-sdk",
    session_id:sessionId, provider, model, status:"failed", answer:"", tool_results:[],
    provider_usage:null, usage_status:"unknown", trace_events:[]};
  const usageKeys = ["input", "output", "cacheRead", "cacheWrite", "totalTokens"];
  const usageSeen = new WeakSet();
  const invocationId = randomUUID();
  const eventIds = new Map();
  let traceStep = 0;
  let messageStarted = null;
  let latency = 0, messageCount = 0, timedMessages = 0, estimatedMessages = 0, estimatedAmount = 0;
  function eventTime(event) {
    return Number.isFinite(event?.timestamp) ? event.timestamp : Date.now();
  }
  function traceEvent(event, type, fields = {}) {
    const toolId = event?.toolCallId ?? fields.tool_call_id ?? "";
    const step = ++traceStep;
    const eventId = `${invocationId}:${step}`;
    const parentEventId = fields.parent_event_id ?? (toolId ? eventIds.get(`tool:${toolId}`) : undefined);
    result.trace_events.push({step, event_id:eventId, ...(parentEventId ? {parent_event_id:parentEventId} : {}), type,
      at_ms:eventTime(event), ...fields});
    if (toolId && (type === "tool_start" || type === "nested_tool")) eventIds.set(`tool:${toolId}`, eventId);
    if (toolId && type === "tool_end") eventIds.delete(`tool:${toolId}`);
    return eventId;
  }
  function traceReferences(value, depth = 0) {
    if (!value || typeof value !== "object" || depth > 4) return {};
    const allowed = new Set(["request_id", "failed_request_id", "job_id", "execution_job_id",
      "output_id", "output_reference", "artifact_id", "operation_id", "task_id", "input_id",
      "input_turn_id", "assistant_turn_id"]);
    const refs = {};
    for (const [key, item] of Object.entries(value)) {
      if (allowed.has(key) && typeof item === "string" && item.length <= 256) refs[key] = item;
      else if (item && typeof item === "object") Object.assign(refs, traceReferences(item, depth + 1));
    }
    return refs;
  }
  function recordUsage(usage) {
    if (!usage || typeof usage !== "object") return;
    const values = Object.fromEntries(usageKeys
      .filter(key => Number.isInteger(usage[key]) && usage[key] >= 0)
      .map(key => [key, usage[key]]));
    // Pi initializes usage to zero before receipt of provider accounting.
    if (!Object.values(values).some(value => value > 0)) return;
    result.provider_usage ??= {};
    for (const [key, value] of Object.entries(values)) result.provider_usage[key] =
      (result.provider_usage[key] ?? 0) + value;
    result.usage_status = "known";
  }
  function usageDetails() {
    const usage = result.provider_usage;
    const details = {provider: result.provider || null, model: result.model || null,
      input_tokens: Number.isInteger(usage?.input) ? usage.input : null,
      output_tokens: Number.isInteger(usage?.output) ? usage.output : null,
      latency_ms: null, estimated_cost: null};
    // SDK message boundaries exclude tools, compaction and retry backoff.
    if (messageStarted === null && messageCount && timedMessages === messageCount) details.latency_ms = latency;
    if (messageCount && estimatedMessages === messageCount) details.estimated_cost = {
      amount: estimatedAmount, currency: "USD", source: "sdk_catalog", status: "estimated"};
    return details;
  }
  return {
    observe(event) {
      if (!event || typeof event !== "object") return;
      const eventType = event.type;
      if (eventType === "message_start" && event.message?.role === "assistant") messageStarted = eventTime(event);
      if (eventType === "turn_start" || eventType === "turn_end" || eventType === "agent_settled") {
        traceEvent(event, eventType);
      }
      if (eventType === "agent_start") traceEvent(event, "agent_start", {status:"running"});
      if (eventType === "agent_end") traceEvent(event, "agent_end", {
        ...(event.willRetry ? {status:"retrying"} : {}),
      });
      if (eventType === "tool_execution_start") {
        traceEvent(event, "tool_start", {tool_call_id:event.toolCallId, tool_name:event.toolName});
      }
      if (eventType === "tool_execution_update") {
        traceEvent(event, "tool_progress", {tool_call_id:event.toolCallId, tool_name:event.toolName});
      }
      if (eventType === "tool_execution_end") {
        const details = event.result?.details;
        const failure = details?.failure;
        const toolEventId = traceEvent(event, "tool_end", {tool_call_id:event.toolCallId, tool_name:event.toolName,
          status:failure?.submission_effect === "unknown" ? "unknown" : event.isError ? "failed" : "succeeded",
          ...(Number.isFinite(event.durationMs) && event.durationMs >= 0 ? {duration_ms:Math.round(event.durationMs)} : {}),
          failure_class: event.isError ? (failure?.error ?? failure?.code ?? "tool_failed") : undefined,
          references:traceReferences(details)});
        for (const receipt of (Array.isArray(details?.business_receipts) ? details.business_receipts : [])) {
          traceEvent(event, "nested_tool", {nested_tool_id:receipt.tool_call_id ?? receipt.tool_name,
            tool_call_id:receipt.tool_call_id, tool_name:receipt.tool_name,
            status:receipt.result?.failure?.submission_effect === "unknown" ? "unknown" : receipt.is_error ? "failed" : "succeeded",
            parent_event_id:toolEventId,
            references:traceReferences(receipt)});
        }
      }
      if (eventType === "compaction_start") traceEvent(event, "compaction_start", {status:"started"});
      if (eventType === "compaction_end") traceEvent(event, "compaction_end", {
        status:event.aborted === true ? "cancelled" : event.errorMessage || !event.result ? "failed" : "completed"});
      if (eventType === "retry" || event.retry === true || eventType?.includes?.("retry")) {
        traceEvent(event, "retry", {status:"started"});
      }
      if (event.type === "tool_execution_end") result.tool_results.push(event);
      if (event.type === "message_end" && event.message.role === "assistant") {
        if (!usageSeen.has(event.message)) {
          usageSeen.add(event.message);
          recordUsage(event.message.usage);
          messageCount += 1;
          if (messageStarted !== null) {
            latency += Math.max(0, Math.round(eventTime(event) - messageStarted));
            timedMessages += 1;
            messageStarted = null;
          }
          const cost = event.message.usage?.cost;
          // Pi's zero defaults are not proof of free usage; provider-billed
          // totals are deliberately not relabelled as catalog estimates.
          if (Number.isFinite(cost?.total) && cost.total > 0 && cost.totalFromProvider !== true) {
            estimatedAmount += cost.total;
            estimatedMessages += 1;
          }
        }
        if (event.message.stopReason === "stop") result.answer = event.message.content
          .filter(item=>item.type === "text").map(item=>item.text).join("").trim();
        else if (["error","aborted"].includes(event.message.stopReason)) {
          result.failure = event.message.errorMessage;
          result.answer = "";
        }
      }
      if (event.type === "agent_settled") result.status = "settled";
    },
    value:()=>{ const {_cost, ...publicResult} = result; return {...publicResult, usage_details:usageDetails()}; },
  };
}
