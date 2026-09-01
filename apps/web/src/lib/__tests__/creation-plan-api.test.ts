import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmCreationPlan, CreationPlanApiError, previewCreationPlan } from "../api";

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
