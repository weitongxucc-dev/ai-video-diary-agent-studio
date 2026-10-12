import { afterEach, expect, it, vi } from "vitest";
import { confirmCreationRequestWindow, createCreationRequest, getCreationRequest, getCreationRequestItems, prepareCreationRequestWindow, updateCreationRequest } from "../creation-request-api";
import { clearLocalMemberToken, LOCAL_MEMBER_TOKEN_HEADER, storeLocalMemberToken } from "../local-member-auth";

afterEach(() => { clearLocalMemberToken(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("uses the existing authenticated transport for current reads, CAS updates and bounded window confirmation", async () => {
  storeLocalMemberToken("request-test-member");
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ id: "request/a" }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  await getCreationRequest("request/a");
  await updateCreationRequest("request/a", { expected_revision: 4, output_relation: "series", material_scope: "workspace_library" });
  await prepareCreationRequestWindow("request/a", { expected_revision: 5, goals: [{ goal: "第一集：开箱" }, { goal: "第二集：设置" }] });
  await confirmCreationRequestWindow("request/a", "window/a", { expected_revision: 6, input_fingerprint: "current-facts" });
  await getCreationRequestItems("request/a", 20);
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    "/api/creation-requests/request%2Fa", "/api/creation-requests/request%2Fa", "/api/creation-requests/request%2Fa/windows",
    "/api/creation-requests/request%2Fa/windows/window%2Fa/confirm", "/api/creation-requests/request%2Fa/items?after_ordinal=20&limit=20",
  ]);
  for (const call of fetch.mock.calls) expect(call[1]).toMatchObject({ cache: "no-store", headers: { [LOCAL_MEMBER_TOKEN_HEADER]: "request-test-member" } });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_revision: 4, output_relation: "series", material_scope: "workspace_library" });
  expect(JSON.parse(fetch.mock.calls[2][1].body).goals).toEqual([{ goal: "第一集：开箱" }, { goal: "第二集：设置" }]);
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({ expected_revision: 6, input_fingerprint: "current-facts" });
});

it("preserves a stale-version refusal without repeating confirmation with a new revision", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "制作要求已变化" }), { status: 409 }));
  vi.stubGlobal("fetch", fetch);
  await expect(confirmCreationRequestWindow("request-a", "window-a", { expected_revision: 4, input_fingerprint: "old-facts" })).rejects.toMatchObject({ status: 409 });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("preserves the full manual target and original references when creating a multi-video request", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "new-request" }), { status: 201 }));
  vi.stubGlobal("fetch", fetch);
  const input = { request_key: "stable-manual-request", goal: "制作10000条设备使用短片", target_count: 10000,
    language: "zh-CN" as const, aspect_ratio: "9:16" as const, duration_target_seconds: 120,
    output_relation: "different_topics" as const, material_scope: "today_received" as const,
    selected_knowledge: { knowledge_item_id: "knowledge-reference", version: "source-version" } };
  await expect(createCreationRequest(input)).resolves.toEqual({ id: "new-request" });
  expect(fetch.mock.calls[0][0]).toBe("/api/creation-requests");
  expect(fetch.mock.calls[0][1].method).toBe("POST");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(input);
});


it("saves this request's exact voice version with all requirements and uses the returned revision for new preparation", async () => {
  storeLocalMemberToken("voice-request-member");
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ id: "request/voice", revision: 12 }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: "request/voice", revision: 13 }), { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const requirements = { goal: "中文旁白介绍真实资料", target_count: 1, language: "zh-CN" as const,
    aspect_ratio: "9:16" as const, duration_target_seconds: 120, presenter_share_target: 0 as const,
    selected_voice: { profile_id: "existing-voice", version: "v2" },
    selected_knowledge: { knowledge_item_id: "owned-facts", version: "3" } };
  const saved = await updateCreationRequest("request/voice", { expected_revision: 11, output_relation: "single", requirements });
  await prepareCreationRequestWindow("request/voice", { expected_revision: saved.revision,
    goals: [{ goal: requirements.goal, name: "真实资料导览" }] });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch.mock.calls[0][0]).toBe("/api/creation-requests/request%2Fvoice");
  expect(fetch.mock.calls[0][1].method).toBe("PATCH");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ expected_revision: 11, output_relation: "single", requirements });
  expect(fetch.mock.calls[1][0]).toBe("/api/creation-requests/request%2Fvoice/windows");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ expected_revision: 12,
    goals: [{ goal: requirements.goal, name: "真实资料导览" }] });
  for (const call of fetch.mock.calls) expect(call[1].headers).toMatchObject({ [LOCAL_MEMBER_TOKEN_HEADER]: "voice-request-member" });
});
