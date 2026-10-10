import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  LOCAL_MEMBER_TOKEN_HEADER,
  LOCAL_MEMBER_TOKEN_STORAGE_KEY,
  clearLocalMemberToken,
  ensureLocalMemberIdentity,
  fetchLocalMemberIdentity,
  localMemberTokenHeader,
  readLocalMemberToken,
  storeLocalMemberToken,
} from "../local-member-auth";
import {
  localMemberTokenCookieAssignment,
  localMemberTokenCookieName,
} from "../native-media-auth";

describe("local member token storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("reads back a stored token", () => {
    storeLocalMemberToken("tok-123");
    expect(readLocalMemberToken()).toBe("tok-123");
  });

  it("syncs the stored token to a port-scoped /api session cookie and clears it", () => {
    window.history.replaceState({}, "", "/api/native-media-test");
    const name = localMemberTokenCookieName(window.location.protocol, window.location.port);
    storeLocalMemberToken("synthetic-token");
    expect(document.cookie).toContain(`${name}=synthetic-token`);
    clearLocalMemberToken();
    expect(document.cookie).not.toContain(`${name}=`);
    expect(readLocalMemberToken()).toBeNull();
  });

  it("uses distinct cookie names per origin port and strict scoped attributes", () => {
    expect(localMemberTokenCookieName("http:", "3000")).not.toBe(
      localMemberTokenCookieName("http:", "3001"),
    );
    expect(localMemberTokenCookieName("http:", "")).toContain("port-80");
    expect(localMemberTokenCookieAssignment("token", "http:", "3000")).toBe(
      `${localMemberTokenCookieName("http:", "3000")}=token; Path=/api; SameSite=Strict`,
    );
    expect(localMemberTokenCookieAssignment("token", "https:", "3000")).toContain("; Secure");
    expect(localMemberTokenCookieAssignment(null, "https:", "3000")).toContain("Max-Age=0; Secure");
  });

  it("treats blank / whitespace tokens as absent and ignores blank writes", () => {
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "   ");
    expect(readLocalMemberToken()).toBeNull();
    storeLocalMemberToken("   ");
    expect(readLocalMemberToken()).toBeNull();
  });

  it("survives a localStorage that throws (private mode / SSR guard)", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => readLocalMemberToken()).not.toThrow();
    expect(readLocalMemberToken()).toBeNull();
    expect(() => storeLocalMemberToken("tok")).not.toThrow();
    expect(() => clearLocalMemberToken()).not.toThrow();
    getItem.mockRestore();
    setItem.mockRestore();
  });
});

describe("request header injection", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("adds the token header when a token is stored", () => {
    storeLocalMemberToken("tok-abc");
    expect(localMemberTokenHeader()).toEqual({ [LOCAL_MEMBER_TOKEN_HEADER]: "tok-abc" });
  });

  it("adds NOTHING when no token is stored (absent header = backend anonymous allow)", () => {
    expect(readLocalMemberToken()).toBeNull();
    const header = localMemberTokenHeader();
    expect(header).toEqual({});
    expect(Object.keys(header)).toHaveLength(0);
    expect(LOCAL_MEMBER_TOKEN_HEADER in header).toBe(false);
  });
});

describe("whoami", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends the stored token and stores the returned member_token", async () => {
    storeLocalMemberToken("tok-existing");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ member_id: "m1", name: "A", role: "member", created_at: "t", member_token: "tok-returned" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const identity = await ensureLocalMemberIdentity();
    expect(identity.role).toBe("member");
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>)[LOCAL_MEMBER_TOKEN_HEADER]).toBe("tok-existing");
    // the refreshed token is persisted
    expect(readLocalMemberToken()).toBe("tok-returned");
  });

  it("sends NO token header when anonymous (must stay absent, not blank)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ member_id: "owner1", name: "Owner", role: "owner", created_at: "t", member_token: "owner-tok" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await ensureLocalMemberIdentity();
    const [, init] = fetchMock.mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(LOCAL_MEMBER_TOKEN_HEADER in headers).toBe(false);
  });

  it("surfaces a 401 invalid-credential error instead of silently acting as owner", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "成员凭据无效" }), { status: 401, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchLocalMemberIdentity("stale-token")).rejects.toThrow("成员凭据无效");
  });
});
