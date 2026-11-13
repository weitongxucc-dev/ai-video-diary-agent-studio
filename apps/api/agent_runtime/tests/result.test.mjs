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
