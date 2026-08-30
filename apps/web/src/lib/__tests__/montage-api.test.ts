import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe("montage API payloads", () => {
  it("creates a video-editing plan with only the selected content", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "plan-1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { createMontagePlan } = await import("../api");

    await createMontagePlan("task-1", { episode_id: "content-1" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/production/batches/task-1/montage-plans",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ episode_id: "content-1" }),
      }),
    );
  });

  it("renders the persisted plan without a client media reference", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "plan-1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { renderMontagePlan } = await import("../api");

    await renderMontagePlan("plan-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/production/montage-plans/plan-1/render",
      expect.objectContaining({ method: "POST", body: "{}" }),
    );
  });
});
