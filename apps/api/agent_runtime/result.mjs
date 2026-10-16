/** This invocation's results only; restoring SDK history never replays receipts. */
export function createSessionResult(sessionId) {
  const result = {type:"creation_session_result", protocol:1, owner:"pi-coding-agent-sdk",
    session_id:sessionId, status:"failed", answer:"", tool_results:[]};
  return {
    observe(event) {
      if (event.type === "tool_execution_end") result.tool_results.push(event);
      if (event.type === "message_end" && event.message.role === "assistant") {
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
