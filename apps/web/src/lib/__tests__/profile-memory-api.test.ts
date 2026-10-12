import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreatorProfileApiError, exportCreatorPreferences, getCreatorProfile, updateCreatorProfile } from "../profile-api";
import { clearLocalMemberToken, LOCAL_MEMBER_TOKEN_HEADER, readLocalMemberToken, storeLocalMemberToken } from "../local-member-auth";

const profile = { id: "profile-a", revision: 7, common_expressions: [], banned_words: [], style_preferences: { "视频时长": "通常两分钟" }, created_at: "2026-10-11T00:00:00Z", updated_at: "2026-10-11T00:00:00Z" };
beforeEach(() => { vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "local"); clearLocalMemberToken(); });
afterEach(() => { clearLocalMemberToken(); window.history.replaceState({}, "", "/"); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("creator memory transport", () => {
  it("establishes the real local identity and injects its token rather than calling the profile anonymously", async () => {
    window.history.replaceState({}, "", "/api/creator-profile");
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ member_id: "member-a", name: "成员甲", role: "member", member_token: "test-member-token" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(profile), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getCreatorProfile()).resolves.toEqual(profile);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/members/whoami");
    expect(fetchMock.mock.calls[1]).toEqual(["/api/creator-profile", expect.objectContaining({ headers: expect.objectContaining({ [LOCAL_MEMBER_TOKEN_HEADER]: "test-member-token" }), credentials: "same-origin", cache: "no-store" })]);
    expect(readLocalMemberToken()).toBe("test-member-token");
    expect(document.cookie).toContain("test-member-token");
  });

  it("sends the read revision and an empty preference map to forget saved preferences", async () => {
    storeLocalMemberToken("test-member-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...profile, revision: 8, style_preferences: {} }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await updateCreatorProfile({ expected_revision: 7, style_preferences: {} });
    expect(fetchMock.mock.calls).toHaveLength(1);
    expect(fetchMock.mock.calls[0][1].headers[LOCAL_MEMBER_TOKEN_HEADER]).toBe("test-member-token");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ expected_revision: 7, style_preferences: {} });
  });

  it("keeps conflict status available to the editor and does not retry with another identity", async () => {
    storeLocalMemberToken("invalid-test-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "偏好已变化" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(updateCreatorProfile({ expected_revision: 7, style_preferences: {} })).rejects.toMatchObject({ status: 409, message: "偏好已变化" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(readLocalMemberToken()).toBe("invalid-test-token");
  });

  it("stops before the profile call when the identity cannot be retained", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("storage blocked"); });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ member_id: "member-a", name: "成员甲", role: "member", member_token: "test-member-token" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getCreatorProfile()).rejects.toThrow("无法确认当前创作者");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("propagates an invalid identity without anonymous fallback", async () => {
    storeLocalMemberToken("invalid-test-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "成员凭据无效" }), { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getCreatorProfile()).rejects.toBeInstanceOf(CreatorProfileApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].headers[LOCAL_MEMBER_TOKEN_HEADER]).toBe("invalid-test-token");
  });

  it("uses the normal authenticated cookie proxy without a stale local token", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "supabase"); storeLocalMemberToken("test-old-local-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(profile), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await getCreatorProfile();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/creator-profile");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "same-origin" });
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty(LOCAL_MEMBER_TOKEN_HEADER);
  });

  it("reads the saved Markdown projection with the actual member token", async () => {
    storeLocalMemberToken("test-member-token");
    const markdown = "# 我的创作偏好\n\n- 视频时长：通常两分钟\n";
    const fetchMock = vi.fn().mockResolvedValue(new Response(markdown, { status: 200, headers: { "Content-Type": "text/markdown; charset=utf-8", "Content-Disposition": "attachment; filename=user.md" } }));
    vi.stubGlobal("fetch", fetchMock);
    const blob = await exportCreatorPreferences();
    expect(await blob.text()).toBe(markdown);
    expect(blob.type).toContain("text/markdown");
    expect(fetchMock.mock.calls[0]).toEqual(["/api/assistant/memory/user.md", expect.objectContaining({ credentials: "same-origin", cache: "no-store", headers: expect.objectContaining({ Accept: "text/markdown", [LOCAL_MEMBER_TOKEN_HEADER]: "test-member-token" }) })]);
  });

  it("rejects a failed Markdown request rather than exporting its error body", async () => {
    storeLocalMemberToken("test-member-token");
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "暂时无法导出偏好" }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(exportCreatorPreferences()).rejects.toMatchObject({ status: 503, message: "暂时无法导出偏好" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
