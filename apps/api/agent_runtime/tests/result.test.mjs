import test from "node:test";
import assert from "node:assert/strict";
import { createSessionResult } from "../result.mjs";

test("SDK result preserves measured provider usage and unknown remains explicit", () => {
  const result = createSessionResult("usage-test", "minimax", "MiniMax-M3");
  result.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[],
    usage:{input:3, output:2, cacheRead:1, cacheWrite:0, totalTokens:6}}});
  result.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[]}});
  assert.deepEqual(result.value().provider_usage, {input:3, output:2, cacheRead:1, cacheWrite:0, totalTokens:6});
  assert.equal(result.value().usage_status, "known");
  assert.equal(result.value().provider, "minimax");
  assert.equal(result.value().model, "MiniMax-M3");
  const empty = createSessionResult("unknown");
  empty.observe({type:"message_end", message:{role:"assistant", stopReason:"stop", content:[],
    usage:{input:0, output:0, cacheRead:0, cacheWrite:0, totalTokens:0}}});
  const unknown = empty.value();
  assert.equal(unknown.provider_usage, null);
  assert.equal(unknown.usage_status, "unknown");
});

test("SDK result exposes token, observed latency and explicit estimated cost", () => {
  const result = createSessionResult("details-test", "minimax", "MiniMax-M3");
  result.observe({type:"turn_start", timestamp:500});
  result.observe({type:"message_start", timestamp:1000, message:{role:"assistant"}});
  result.observe({type:"message_end", timestamp:1450, message:{role:"assistant", stopReason:"stop", content:[],
    usage:{input:7, output:3, totalTokens:10, cost:{input:0.001, output:0.002, total:0.003}}}});
  result.observe({type:"turn_end", timestamp:1450});
  const details = result.value().usage_details;
  assert.deepEqual(details, {provider:"minimax", model:"MiniMax-M3", input_tokens:7,
    output_tokens:3, latency_ms:450,
    estimated_cost:{amount:0.003, currency:"USD", source:"sdk_catalog", status:"estimated"}});
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

test("Pi lifecycle and tool progress stay ordered without serializing partial payloads", () => {
  const session = createSessionResult("activity");
  for (const event of [
    {type:"agent_start", timestamp:1000},
    {type:"tool_execution_start", timestamp:1010, toolCallId:"a", toolName:"read"},
    {type:"tool_execution_start", timestamp:1012, toolCallId:"b", toolName:"write"},
    {type:"tool_execution_update", timestamp:1020, toolCallId:"b", toolName:"write",
      partialResult:{content:[{type:"text", text:"PRIVATE_PARTIAL_RESULT"}]}},
    {type:"tool_execution_end", timestamp:1030, durationMs:17.4, toolCallId:"b", toolName:"write", isError:true,
      result:{details:{failure:{submission_effect:"unknown", request_id:"original-request"}}}},
    {type:"tool_execution_end", timestamp:1040, toolCallId:"a", toolName:"read", isError:false},
    {type:"agent_end", timestamp:1050, willRetry:false},
  ]) session.observe(event);
  const events = session.value().trace_events;
  assert.deepEqual(events.map(event => event.type), [
    "agent_start", "tool_start", "tool_start", "tool_progress", "tool_end", "tool_end", "agent_end",
  ]);
  assert.equal(events[3].parent_event_id, events[2].event_id);
  assert.equal(events[4].parent_event_id, events[2].event_id);
  assert.equal(events[5].parent_event_id, events[1].event_id);
  assert.equal(events[4].status, "unknown");
  assert.equal(events[4].duration_ms, 17);
  assert.equal(events[4].references.request_id, "original-request");
  assert.doesNotMatch(JSON.stringify(events), /PRIVATE_PARTIAL_RESULT/);
});


test("failure, cancellation and retry preserve receipts without inventing missing cost", () => {
  for (const stopReason of ["error", "aborted"]) {
    const result = createSessionResult("terminal", "minimax", "M3");
    result.observe({type:"message_start", timestamp:1000, message:{role:"assistant"}});
    const message = {role:"assistant", stopReason, content:[], usage:{input:2, output:1, cost:{total:0}}};
    result.observe({type:"message_end", timestamp:1200, message});
    result.observe({type:"message_end", timestamp:1300, message});
    assert.equal(result.value().usage_details.latency_ms, 200);
    assert.equal(result.value().usage_details.input_tokens, 2);
    assert.equal(result.value().usage_details.estimated_cost, null);
    result.observe({type:"retry", timestamp:1500});
    result.observe({type:"message_start", timestamp:2000, message:{role:"assistant"}});
    result.observe({type:"message_end", timestamp:2100, message:{role:"assistant", stopReason:"stop",
      content:[], usage:{input:3, output:2, cost:{total:0.02}}}});
    assert.equal(result.value().usage_details.latency_ms, 300);
    assert.equal(result.value().usage_details.input_tokens, 5);
    assert.equal(result.value().usage_details.estimated_cost, null);
  }
  const aborted = createSessionResult("no-receipt");
  aborted.observe({type:"message_start", timestamp:1000, message:{role:"assistant"}});
  assert.deepEqual(aborted.value().usage_details, {provider:null, model:null, input_tokens:null,
    output_tokens:null, latency_ms:null, estimated_cost:null});
});
