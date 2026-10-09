import { beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ local: vi.fn(), identity: vi.fn(), foundation: vi.fn(), user: vi.fn() }));
vi.mock("@/lib/auth/routing", () => ({ isLocalAuthMode: mocks.local }));
vi.mock("@/lib/local-member-auth", () => ({ fetchLocalMemberIdentity: mocks.identity }));
vi.mock("@/lib/api", () => ({ getProductionFoundation: mocks.foundation }));
vi.mock("@/lib/supabase/client", () => ({ createClient: () => ({ auth: { getUser: mocks.user } }) }));
import { boundedPreflightReturn, clearPreflightDraft, loadPreflightDraft, preflightReturnUrl, readPreflightDraftScope, savePreflightDraft, type PreflightDraftFields } from "@/lib/preflight-draft";
const id = "12345678-1234-4123-8123-123456789abc";
const id2 = "12345678-1234-4123-8123-123456789abd";
const owner = { memberId: "local:owner", workspaceId: "space-a" };
const member = { memberId: "local:member", workspaceId: "space-a" };
const draft: PreflightDraftFields = { goal: "原目标", name: "原任务", targetCount: "2", duration: "30", language: "zh-CN", aspectRatio: "16:9", presenterShare: 40, lipsyncQuality: "fast", voicePin: { profile_id: "voice-a", version: "v2" }, personPin: { profile_id: "person-b", version: "p3" }, voiceEdited: true, template: { id: "fixed-template", version: 4 } };
beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); mocks.local.mockReturnValue(true); mocks.identity.mockResolvedValue({ member_id: "owner", role: "owner" }); mocks.foundation.mockResolvedValue({ profile: { workspace_id: "space-a" } }); });
describe("bounded member/workspace session drafts", () => {
  it("uses verified local roles or Supabase user and actual foundation workspace, never a body identity", async () => {
    expect(await readPreflightDraftScope()).toEqual(owner);
    mocks.identity.mockResolvedValue({ member_id: "member", role: "member" }); mocks.foundation.mockResolvedValue({ profile: { workspace_id: "space-b" } });
    expect(await readPreflightDraftScope()).toEqual({ memberId: "local:member", workspaceId: "space-b" });
    mocks.local.mockReturnValue(false); mocks.user.mockResolvedValue({ data: { user: { id: "verified-user" } }, error: null });
    expect(await readPreflightDraftScope()).toEqual({ memberId: "supabase:verified-user", workspaceId: "space-b" });
  });
  it("rejects unavailable identity/workspace and a member switch during the workspace read", async () => {
    mocks.identity.mockResolvedValueOnce({ member_id: "owner" }).mockResolvedValueOnce({ member_id: "member" });
    await expect(readPreflightDraftScope()).rejects.toThrow("读取期间变化");
    mocks.foundation.mockResolvedValue({ profile: { workspace_id: "" } });
    await expect(readPreflightDraftScope()).rejects.toThrow("工作区未确认");
    mocks.identity.mockResolvedValue({ member_id: "" });
    await expect(readPreflightDraftScope()).rejects.toThrow("成员身份未确认");
    mocks.local.mockReturnValue(false); mocks.user.mockResolvedValue({ data: { user: null }, error: new Error("expired") });
    await expect(readPreflightDraftScope()).rejects.toThrow("登录身份未确认");
  });
  it("isolates two roles, two spaces and adjacent drafts; clears only the successful UUID", () => {
    savePreflightDraft(sessionStorage, owner, id, draft); savePreflightDraft(sessionStorage, owner, id2, { ...draft, goal: "第二草稿" });
    expect(loadPreflightDraft(sessionStorage, member, id)).toBeNull();
    expect(loadPreflightDraft(sessionStorage, { ...owner, workspaceId: "space-b" }, id)).toBeNull();
    savePreflightDraft(sessionStorage, member, id, { ...draft, goal: "成员草稿" });
    clearPreflightDraft(sessionStorage, owner, id);
    expect(loadPreflightDraft(sessionStorage, owner, id)).toBeNull();
    expect(loadPreflightDraft(sessionStorage, owner, id2)?.goal).toBe("第二草稿");
    expect(loadPreflightDraft(sessionStorage, member, id)?.goal).toBe("成员草稿");
  });
  it("stores only bounded inputs; rejects malformed IDs, envelope and credential fields", () => {
    expect(() => savePreflightDraft(sessionStorage, owner, "../../other", draft)).toThrow("编号无效");
    expect(() => savePreflightDraft(sessionStorage, owner, id, { ...draft, token: "secret" } as PreflightDraftFields)).toThrow("输入超出");
    savePreflightDraft(sessionStorage, owner, id, draft);
    const key = sessionStorage.key(0)!;
    const body = JSON.parse(sessionStorage.getItem(key)!);
    expect(Object.keys(body.fields).sort()).toEqual(Object.keys(draft).sort());
    body.memberId = member.memberId; sessionStorage.setItem(key, JSON.stringify(body));
    expect(() => loadPreflightDraft(sessionStorage, owner, id)).toThrow("内容无效");
    sessionStorage.setItem(key, "{"); expect(() => loadPreflightDraft(sessionStorage, owner, id)).toThrow();
    sessionStorage.setItem(key, "x".repeat(10001)); expect(() => loadPreflightDraft(sessionStorage, owner, id)).toThrow("读取范围");
  });
  it("bounds return_to to the original creation URL and fixed template, with no form body", () => {
    const href = preflightReturnUrl(id, draft.template);
    expect(boundedPreflightReturn(href, "https://example.test")).toEqual({ href, draftId: id, template: draft.template });
    expect(href).not.toContain("原目标"); expect(href).not.toContain("voice");
    for (const raw of ["https://evil.test" + href, "https://u:p@example.test" + href, "/knowledge?new=1&draft_id=" + id, href + "#fragment", href + "&goal=leak", href + "&draft_id=" + id2, "/production?new=1&draft_id=not-a-uuid", href.replace("template_version=4", "template_version=-1"), href.replace("template_version=4", "template_version=9007199254740992"), href.replace("template_version=4", "unknown=4"), "\\\\evil.test" + href]) expect(boundedPreflightReturn(raw, "https://example.test"), raw).toBeNull();
  });
  it("propagates storage denied/quota errors instead of claiming a save or fallback", () => {
    const storage = { setItem() { throw new Error("quota"); }, getItem() { throw new Error("denied"); } } as unknown as Storage;
    expect(() => savePreflightDraft(storage, owner, id, draft)).toThrow("quota");
    expect(() => loadPreflightDraft(storage, owner, id)).toThrow("denied");
  });
});
