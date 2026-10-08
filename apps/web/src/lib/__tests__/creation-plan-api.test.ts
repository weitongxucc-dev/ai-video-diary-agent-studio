import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmCreationPlan, CreationPlanApiError, getCreationTaskQualityCheck, previewCreationPlan } from "../api";
import { LOCAL_MEMBER_TOKEN_HEADER, LOCAL_MEMBER_TOKEN_STORAGE_KEY } from "../local-member-auth";

const input = {
  goal: "制作产品观察视频",
  target_count: 1,
  language: "zh-CN" as const,
  aspect_ratio: "9:16" as const,
  duration_target_seconds: 60,
};

afterEach(() => vi.unstubAllGlobals());

describe("creation-plan API", () => {
  it("sends the frozen preview request without a write fingerprint", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ confirmable: false }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await previewCreationPlan(input);

    expect(fetch).toHaveBeenCalledWith("/api/creation-plans/preview", expect.objectContaining({
      method: "POST",
      body: JSON.stringify(input),
    }));
  });

  it("keeps the stale code, Chinese title, impact, and repair action from a real error payload", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        error: "creation_plan_preflight_stale",
        impact: "预检期间的资料、授权、素材或模型状态已变化，未创建任务。",
        repair_action: "重新执行内容预检并确认最新方案。",
      },
    }), { status: 409 }));
    vi.stubGlobal("fetch", fetch);

    const promise = confirmCreationPlan({ ...input, preflight_fingerprint: "f".repeat(64) });

    await expect(promise).rejects.toMatchObject({
      status: 409,
      code: "creation_plan_preflight_stale",
      message: "预检结果已变化",
      impact: "预检期间的资料、授权、素材或模型状态已变化，未创建任务。",
      repairAction: "重新执行内容预检并确认最新方案。",
    } satisfies Partial<CreationPlanApiError>);
  });
});

describe("quality result API identity", () => {
  afterEach(() => window.localStorage.clear());

  it("waits for first-browser member identity before reading quality without starting a check", async () => {
    window.localStorage.clear();
    let resolveIdentity!: (response: Response) => void;
    const identityResponse = new Promise<Response>(resolve => { resolveIdentity = resolve; });
    const result = { check_state: "none", result_status: null, findings: [], unavailable: [] };
    const fetch = vi.fn().mockImplementation((url: string, init: RequestInit) => {
      if (url === "/api/members/whoami") return identityResponse;
      const token = new Headers(init.headers).get(LOCAL_MEMBER_TOKEN_HEADER);
      return Promise.resolve(new Response(JSON.stringify(token === "fixture-member-token"
        ? result : { detail: "请使用可信成员身份检查成片" }), { status: token ? 200 : 401 }));
    });
    vi.stubGlobal("fetch", fetch);

    const reading = getCreationTaskQualityCheck("task/1").catch(error => error);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/members/whoami", expect.anything()));
    expect(fetch).toHaveBeenCalledTimes(1);
    resolveIdentity(new Response(JSON.stringify({ member_id: "fixture-member", role: "owner",
      name: "合成成员", created_at: "2026-10-08", member_token: "fixture-member-token" })));
    expect(await reading).toEqual(result);
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/creation-tasks/task%2F1/quality-check", expect.objectContaining({
      headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "fixture-member-token" }),
    }));
    expect(fetch.mock.calls.every(([, init]) => !init.method || init.method === "GET")).toBe(true);
  });

  it("keeps a rejected stored credential instead of retrying anonymously", async () => {
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "invalid-fixture-token");
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "成员凭据无效" }), { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    await expect(getCreationTaskQualityCheck("task-1")).rejects.toMatchObject({ status: 401, message: "成员凭据无效" });
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/creation-tasks/task-1/quality-check", expect.objectContaining({
      headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "invalid-fixture-token" }),
    }));
    expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe("invalid-fixture-token");
  });

  it("preserves identity initialization failure without sending an anonymous quality request", async () => {
    window.localStorage.clear();
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "身份服务不可用" }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);
    await expect(getCreationTaskQualityCheck("task-1")).rejects.toMatchObject({ status: 503, message: "身份服务不可用" });
    expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/members/whoami", expect.anything());
    expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBeNull();
  });
});
