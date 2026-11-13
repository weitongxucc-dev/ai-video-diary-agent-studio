import test from "node:test";
import assert from "node:assert/strict";
import { createSessionResult } from "../result.mjs";

test("SDK result preserves measured provider usage and unknown remains explicit", () => {
  const result = createSessionResult("usage-test");
  result.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[],
    usage:{input:3, output:2, cacheRead:1, cacheWrite:0, totalTokens:6}}});
  result.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[]}});
  assert.deepEqual(result.value().provider_usage, {input:3, output:2, cacheRead:1, cacheWrite:0, totalTokens:6});
  assert.equal(result.value().usage_status, "known");
  const unknown = createSessionResult("unknown").value();
  assert.equal(unknown.provider_usage, null);
  assert.equal(unknown.usage_status, "unknown");
});

test("SDK result records bounded runtime events without prompt or argument payloads", () => {
  const result = createSessionResult("trace-test");
  result.observe({type:"turn_start", timestamp:1000});
  result.observe({type:"tool_execution_start", timestamp:1100, toolCallId:"call-1", toolName:"read"});
  result.observe({type:"tool_execution_end", timestamp:1300, toolCallId:"call-1", toolName:"read", isError:true,
    result:{details:{failure:{error:"business_tool_failed"}, business_receipts:[
      {tool_call_id:"nested-1", tool_name:"read_job", is_error:false, request_id:"request-1"}]}}});
  result.observe({type:"compaction_start", timestamp:1400});
  result.observe({type:"compaction_end", timestamp:1600});
  result.observe({type:"retry", timestamp:1700});
  const events = result.value().trace_events;
  assert.deepEqual(events.map(event=>event.type), ["turn_start", "tool_start", "tool_end", "nested_tool",
    "compaction_start", "compaction_end", "retry"]);
  assert.equal(events[1].event_id, "trace-test:2");
  assert.equal(events[2].parent_event_id, "trace-test:2");
  assert.equal(events[3].parent_event_id, "trace-test:3");
  assert.ok(events.every(event=>event.event_id));
  assert.equal(events.find(event=>event.type === "nested_tool").references.request_id, "request-1");
  assert.ok(!JSON.stringify(events).includes("prompt"));
});
