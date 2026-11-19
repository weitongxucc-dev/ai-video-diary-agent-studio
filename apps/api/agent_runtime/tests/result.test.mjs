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
  const empty = createSessionResult("unknown");
  empty.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[],
    usage:{input:0, output:0, cacheRead:0, cacheWrite:0, totalTokens:0}}});
  const unknown = empty.value();
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
  result.observe({type:"compaction_end", timestamp:1600, result:{tokensBefore:8}});
  result.observe({type:"retry", timestamp:1700});
  const events = result.value().trace_events;
  assert.deepEqual(events.map(event=>event.type), ["turn_start", "tool_start", "tool_end", "nested_tool",
    "compaction_start", "compaction_end", "retry"]);
  assert.equal(events[2].parent_event_id, events[1].event_id);
  assert.equal(events[3].parent_event_id, events[2].event_id);
  assert.ok(events.every(event=>event.event_id));
  assert.equal(events.find(event=>event.type === "nested_tool").references.request_id, "request-1");
  assert.ok(!JSON.stringify(events).includes("prompt"));
});

test("pinned SDK round events have no timestamps, and reused sessions keep invocation events distinct", () => {
  const first = createSessionResult("same-session");
  const following = createSessionResult("same-session");
  for (const result of [first, following]) {
    for (const index of [1, 2]) {
      result.observe({type:"turn_start"});
      result.observe({type:"message_end", message:{role:"assistant", timestamp:1000,
        stopReason:"stop", usage:{input:4, output:index, totalTokens:4+index},
        content:[{type:"thinking", thinking:"PRIVATE SDK CONTENT"}, {type:"text", text:"公开答复"}]}});
      result.observe({type:"turn_end"});
    }
  }
  assert.deepEqual(first.value().trace_events.map(event => event.type),
    ["turn_start", "turn_end", "turn_start", "turn_end"]);
  assert.deepEqual(first.value().provider_usage, {input:8, output:3, totalTokens:11});
  assert.equal(first.value().answer, "公开答复");
  const events = [...first.value().trace_events, ...following.value().trace_events];
  assert.equal(new Set(events.map(event => event.event_id)).size, events.length);
  assert.ok(!JSON.stringify(first.value()).includes("PRIVATE SDK CONTENT"));
});
