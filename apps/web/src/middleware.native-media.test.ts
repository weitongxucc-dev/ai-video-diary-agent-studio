import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isLocalNativeMediaRequest, localMemberTokenCookieName } from "@/lib/native-media-auth";

const authMocks = vi.hoisted(() => ({ refreshSession: vi.fn() }));

vi.mock("@/lib/supabase/middleware", () => ({
  copyRefreshedSession: (response: Response) => response,
  redirectWithRefreshedCookies: (url: URL) => new Response(null, { status: 307, headers: { location: url.toString() } }),
  refreshSession: authMocks.refreshSession,
}));

import { middleware } from "./middleware";

const mediaPath = "/api/production/materials/private-asset/media";
const syntheticToken = "synthetic/member+token=";
const localCookie = `${localMemberTokenCookieName("http:", "3000")}=${encodeURIComponent(syntheticToken)}`;

describe("native local media identity forwarding", () => {
  const previousMode = process.env.NEXT_PUBLIC_AUTH_MODE;
  let fetchMock: ReturnType<typeof vi.fn>;
  let forwardedUrl = "";
  let forwardedHeaders = new Headers();
  let forwardedBody: BodyInit | null | undefined;
  let forwardedMethod = "";

  beforeEach(() => {
    process.env.NEXT_PUBLIC_AUTH_MODE = "local";
    forwardedUrl = "";
    forwardedHeaders = new Headers();
    forwardedBody = undefined;
    forwardedMethod = "";
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      forwardedUrl = String(input);
      forwardedHeaders = new Headers(init?.headers);
      forwardedBody = init?.body;
      forwardedMethod = init?.method ?? "";
      return new Response("range-bytes", {
        status: 206,
        headers: { "content-range": "bytes 20-30/100", "accept-ranges": "bytes" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    if (previousMode === undefined) delete process.env.NEXT_PUBLIC_AUTH_MODE;
    else process.env.NEXT_PUBLIC_AUTH_MODE = previousMode;
  });

  it("recognizes only current profile, preview, task, asset and montage media routes for GET/HEAD", () => {
    const routes = [
      "/api/media/person-profiles/p-1/reference",
      "/api/media/voice-profiles/v-1/reference",
      "/api/media/previews/voice/voice-preview-1/media",
      "/api/media/previews/avatar_lipsync/lipsync-preview-1/media",
      "/api/creation-tasks/task-1/person-renders/render-1/media",
      "/api/creation-tasks/task-1/output",
      "/api/creation-tasks/task-1/audio/artifacts/audio-1",
      "/api/creation-tasks/task-1/narration/segments/segment-1/audio",
      "/api/production/materials/asset-1/media",
      "/api/production/montage-plans/plan-1/output",
    ];
    for (const route of routes) {
      expect(isLocalNativeMediaRequest("GET", route), route).toBe(true);
      expect(isLocalNativeMediaRequest("HEAD", route), route).toBe(true);
      expect(isLocalNativeMediaRequest("POST", route), route).toBe(false);
    }
    expect(isLocalNativeMediaRequest("GET", "/api/production/materials")).toBe(false);
    expect(isLocalNativeMediaRequest("GET", "/api/creation-tasks/task-1/assistant/script-draft")).toBe(false);
  });

  it("forwards the existing cookie token only on the native GET and preserves Range/206 headers", async () => {
    const request = new NextRequest(`http://localhost:3000${mediaPath}`, {
      method: "GET",
      headers: { cookie: localCookie, range: "bytes=20-30" },
    });

    const response = await middleware(request);

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(forwardedMethod).toBe("GET");
    expect(forwardedHeaders.get("x-local-member-token")).toBe(syntheticToken);
    expect(forwardedHeaders.get("range")).toBe("bytes=20-30");
    expect(forwardedUrl).toBe(`http://127.0.0.1:8000${mediaPath}`);
    expect(forwardedUrl).not.toContain(syntheticToken);
    expect(forwardedBody).toBeUndefined();
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 20-30/100");
    expect(await response.text()).toBe("range-bytes");
  });

  it("forwards native HEAD with the port-matched cookie and keeps Range", async () => {
    fetchMock.mockImplementationOnce(async (input: RequestInfo | URL, init?: RequestInit) => {
      forwardedUrl = String(input);
      forwardedHeaders = new Headers(init?.headers);
      forwardedMethod = init?.method ?? "";
      return new Response(null, { status: 206, headers: { "content-range": "bytes 0-0/100" } });
    });
    const request = new NextRequest(`http://localhost:3000${mediaPath}`, {
      method: "HEAD",
      headers: { cookie: localCookie, range: "bytes=0-0" },
    });

    const response = await middleware(request);

    expect(forwardedMethod).toBe("HEAD");
    expect(forwardedHeaders.get("x-local-member-token")).toBe(syntheticToken);
    expect(forwardedHeaders.get("range")).toBe("bytes=0-0");
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-0/100");
  });

  it.each(["", "explicitly-invalid-token"]) (
    "does not replace an explicit token header (%s) with the cookie",
    async (explicitToken) => {
      const request = new NextRequest(`http://localhost:3000${mediaPath}`, {
        method: "GET",
        headers: { cookie: localCookie, "x-local-member-token": explicitToken },
      });
      await middleware(request);
      expect(forwardedHeaders.get("x-local-member-token")).toBe(explicitToken);
    },
  );

  it("does not inject a cookie from another port or into non-media POST requests", async () => {
    const wrongPortCookie = `${localMemberTokenCookieName("http:", "3001")}=other-port-token`;
    const wrongPort = new NextRequest(`http://localhost:3000${mediaPath}`, {
      headers: { cookie: wrongPortCookie },
    });
    await middleware(wrongPort);
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);

    const anonymous = new NextRequest(`http://localhost:3000${mediaPath}`);
    await middleware(anonymous);
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);

    const post = new NextRequest("http://localhost:3000/api/creation-tasks/task-1/assistant/script-draft", {
      method: "POST",
      headers: { cookie: localCookie, "content-type": "application/json" },
      body: JSON.stringify({ title: "ordinary payload" }),
    });
    await middleware(post);
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
    expect(new TextDecoder().decode(forwardedBody as ArrayBuffer)).toBe(
      JSON.stringify({ title: "ordinary payload" }),
    );
    expect(forwardedUrl).not.toContain(syntheticToken);
  });

  it("does not inherit local cookies in Supabase mode", async () => {
    process.env.NEXT_PUBLIC_AUTH_MODE = "supabase";
    authMocks.refreshSession.mockResolvedValueOnce({
      userId: "verified-user",
      onboardingComplete: true,
      accessToken: "verified-session-token",
      response: null,
    });
    const request = new NextRequest(`http://localhost:3000${mediaPath}`, {
      headers: { cookie: localCookie },
    });

    await middleware(request);

    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
    expect(forwardedHeaders.get("authorization")).toBe("Bearer verified-session-token");
  });
});
