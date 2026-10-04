import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceApiError, listKnowledgeItems, listSourceItems, sourceErrorMessage, updateKnowledgeItemClassification } from "../source-api";

afterEach(() => vi.unstubAllGlobals());

describe("source API client", () => {
  it("keeps a connector filter in the real endpoint request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total_count: 0 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await listSourceItems("connector 1");
    expect(fetchMock).toHaveBeenCalledWith("/api/source-items?connector_id=connector%201", expect.objectContaining({ cache: "no-store" }));
  });

  it("keeps the real classification filters and the classification write in the endpoint", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ items: [], total_count: 0, category_counts: {}, topics: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await listKnowledgeItems({ use_category: "", topic: "曜核一体机" });
    expect(fetchMock).toHaveBeenCalledWith("/api/knowledge-items?use_category=&topic=%E6%9B%9C%E6%A0%B8%E4%B8%80%E4%BD%93%E6%9C%BA", expect.objectContaining({ cache: "no-store" }));
    await updateKnowledgeItemClassification("knowledge one", { use_category: "产品知识", topics: ["曜核一体机"] });
    expect(fetchMock).toHaveBeenLastCalledWith("/api/knowledge-items/knowledge%20one", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ use_category: "产品知识", topics: ["曜核一体机"] }) }));
  });

  it.each([
    [401, "authentication_required", "需要登录后才能访问此信息源。"],
    [403, "access_denied", "当前账号无权限访问此信息源。"],
    [429, "rate_limited", "信息源被限流，请稍后重试。"],
    [501, "capability_unavailable", "此平台尚未支持。"],
  ])("maps %s to a visible repair state", (status, code, expected) => {
    expect(sourceErrorMessage(new SourceApiError("backend detail", status, { code }))).toBe(expected);
  });
});
