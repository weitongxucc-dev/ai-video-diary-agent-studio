/** This invocation's results only; restoring SDK history never replays receipts. */
export function createSessionResult(sessionId) {
  const result = {type:"creation_session_result", protocol:1, owner:"pi-coding-agent-sdk",
    session_id:sessionId, status:"failed", answer:"", tool_results:[],
    provider_usage:null, usage_status:"unknown"};
  const usageKeys = ["input", "output", "cacheRead", "cacheWrite", "totalTokens"];
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
      if (event.type === "tool_execution_end") result.tool_results.push(event);
      if (event.type === "message_end" && event.message.role === "assistant") {
        recordUsage(event.message.usage);
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
