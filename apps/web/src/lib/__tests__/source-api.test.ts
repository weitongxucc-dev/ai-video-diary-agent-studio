import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceApiError, listSourceItems, sourceErrorMessage } from "../source-api";

afterEach(() => vi.unstubAllGlobals());

describe("source API client", () => {
  it("keeps a connector filter in the real endpoint request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total_count: 0 }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await listSourceItems("connector 1");
    expect(fetchMock).toHaveBeenCalledWith("/api/source-items?connector_id=connector%201", expect.objectContaining({ cache: "no-store" }));
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
