import { afterEach, describe, expect, it, vi } from "vitest";
import {
  confirmCreationTaskTopic,
  CreationPlanApiError,
  generateCreationTaskTopics,
  getCreationTaskTopics,
} from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("creation-task topic API", () => {
  it("uses the frozen GET and empty generation body", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ task: {} }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ task: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await getCreationTaskTopics("task/1");
    await generateCreationTaskTopics("task/1");

    expect(fetch).toHaveBeenNthCalledWith(1, "/api/creation-tasks/task%2F1/topics", expect.objectContaining({ cache: "no-store" }));
    expect(fetch).toHaveBeenNthCalledWith(2, "/api/creation-tasks/task%2F1/topics/generate", expect.objectContaining({
      method: "POST",
      body: "{}",
    }));
  });

  it("uses the persisted topic identity and an empty confirmation body", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);

    await confirmCreationTaskTopic("task-1", "topic-1");

    expect(fetch).toHaveBeenCalledWith("/api/creation-tasks/task-1/topics/topic-1/confirm", expect.objectContaining({
      method: "POST",
      body: "{}",
    }));
  });

  it("keeps the structured failure fields from the topic service", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        error: "capability_unavailable",
        reason: "capability_unavailable",
        impact: "没有调用文字模型，也没有生成候选。",
        repair_action: "配置文字模型后重新开始选题。",
        retryable: true,
      },
    }), { status: 503 }));
    vi.stubGlobal("fetch", fetch);

    await expect(generateCreationTaskTopics("task-1")).rejects.toMatchObject({
      status: 503,
      code: "capability_unavailable",
      message: "当前文字模型不可用",
      reason: "capability_unavailable",
      impact: "没有调用文字模型，也没有生成候选。",
      repairAction: "配置文字模型后重新开始选题。",
    } satisfies Partial<CreationPlanApiError>);
  });
});
