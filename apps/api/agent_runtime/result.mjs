/** This invocation's results only; restoring SDK history never replays receipts. */
export function createSessionResult(sessionId) {
  const result = {type:"creation_session_result", protocol:1, owner:"pi-coding-agent-sdk",
    session_id:sessionId, status:"failed", answer:"", tool_results:[],
    provider_usage:null, usage_status:"unknown", trace_events:[]};
  const usageKeys = ["input", "output", "cacheRead", "cacheWrite", "totalTokens"];
  const usageSeen = new Set();
  const traceSeen = new Set();
  const eventIds = new Map();
  let traceStep = 0;
  function eventTime(event) {
    return Number.isFinite(event?.timestamp) ? event.timestamp : Date.now();
  }
  function traceEvent(event, type, fields = {}) {
    const toolId = event?.toolCallId ?? fields.tool_call_id ?? "";
    const key = `${type}|${toolId}|${event?.timestamp ?? ""}|${fields.nested_tool_id ?? ""}`;
    if (traceSeen.has(key)) return;
    traceSeen.add(key);
    const step = ++traceStep;
    const eventId = `${sessionId}:${step}`;
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
    if (!Object.keys(values).length) return;
    result.provider_usage ??= {};
    for (const [key, value] of Object.entries(values)) result.provider_usage[key] =
      (result.provider_usage[key] ?? 0) + value;
    result.usage_status = "known";
  }
  return {
    observe(event) {
      if (!event || typeof event !== "object") return;
      const eventType = event.type;
      if (eventType === "turn_start" || eventType === "turn_end" || eventType === "agent_start" ||
          eventType === "agent_end" || eventType === "agent_settled") traceEvent(event, eventType);
      if (eventType === "tool_execution_start") {
        traceEvent(event, "tool_start", {tool_call_id:event.toolCallId, tool_name:event.toolName});
      }
      if (eventType === "tool_execution_end") {
        const details = event.result?.details;
        const failure = details?.failure;
        const toolEventId = traceEvent(event, "tool_end", {tool_call_id:event.toolCallId, tool_name:event.toolName,
          status:event.isError ? "failed" : "succeeded",
          failure_class: event.isError ? (failure?.error ?? failure?.code ?? "tool_failed") : undefined,
          references:traceReferences(details)});
        for (const receipt of (Array.isArray(details?.business_receipts) ? details.business_receipts : [])) {
          traceEvent(event, "nested_tool", {nested_tool_id:receipt.tool_call_id ?? receipt.tool_name,
            tool_call_id:receipt.tool_call_id, tool_name:receipt.tool_name,
            status:receipt.is_error ? "failed" : "succeeded", parent_event_id:toolEventId,
            references:traceReferences(receipt)});
        }
      }
      if (eventType === "compaction_start") traceEvent(event, "compaction_start", {status:"started"});
      if (eventType === "compaction_end") traceEvent(event, "compaction_end", {
        status:event.aborted === true ? "cancelled" : event.errorMessage ? "failed" : "completed"});
      if (eventType === "retry" || event.retry === true || eventType?.includes?.("retry")) {
        traceEvent(event, "retry", {status:"started"});
      }
      if (event.type === "tool_execution_end") result.tool_results.push(event);
      if (event.type === "message_end" && event.message.role === "assistant") {
        const usageKey = event.message.id ?? event.message.timestamp ?? JSON.stringify(event.message.usage ?? null);
        if (!usageSeen.has(String(usageKey))) {
          usageSeen.add(String(usageKey));
          recordUsage(event.message.usage);
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
    value:()=>result,
  };
}
