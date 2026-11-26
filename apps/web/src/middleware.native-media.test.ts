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
const confirmationPath = "/api/creation-tasks/task_confirm/edits/proposal_confirm/section-candidate-confirmation";
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
      "/api/creation-tasks/task-1/covers/cover-1/image",
      "/api/creation-tasks/task-1/output/selected-subtitles",
      "/api/creation-tasks/task-1/audio/artifacts/audio-1",
      "/api/creation-tasks/task-1/narration/segments/segment-1/audio",
      "/api/production/materials/asset-1/media",
      "/api/production/materials/discovery/session-1/candidates/candidate-1/preview",
      "/api/production/montage-plans/plan-1/output",
    ];
    for (const route of routes) {
      expect(isLocalNativeMediaRequest("GET", route), route).toBe(true);
      expect(isLocalNativeMediaRequest("HEAD", route), route).toBe(true);
      expect(isLocalNativeMediaRequest("POST", route), route).toBe(false);
    }
    expect(isLocalNativeMediaRequest("GET", "/api/production/materials")).toBe(false);
    expect(isLocalNativeMediaRequest("POST", "/api/production/materials/discovery/session-1/receive")).toBe(false);
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

  it("forwards the explicit local session only to the exact confirmation GET and rejects owner fallback", async () => {
    const url = `http://localhost:3000${confirmationPath}`;
    await middleware(new NextRequest(`${url}?request_id=original`, {
      headers: { cookie: localCookie, "x-yaohe-desktop-control": "renderer-forgery" },
    }));
    expect(forwardedHeaders.get("x-local-member-token")).toBe(syntheticToken);
    expect(forwardedHeaders.has("x-yaohe-desktop-control")).toBe(false);
    expect(forwardedUrl).toBe(`http://127.0.0.1:8000${confirmationPath}?request_id=original`);
    expect(fetchMock.mock.calls[0][1]?.cache).toBe("no-store");

    fetchMock.mockClear();
    const noSession: HeadersInit[] = [{}, { cookie: localCookie, "x-local-member-token": "" },
      { cookie: `${localMemberTokenCookieName("http:", "3001")}=wrong-port` }];
    for (const headers of noSession) {
      const response = await middleware(new NextRequest(url, { headers }));
      expect(response.status).toBe(401);
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
    expect(fetchMock).not.toHaveBeenCalled();

    for (const [method, target] of [["HEAD", url], ["POST", url], ["GET", `${url}/other`],
      ["GET", url.replace("proposal_confirm", "proposal_%2Fconfirm")],
      ["POST", url.replace(/section-candidate-confirmation$/, "confirm")]]) {
      await middleware(new NextRequest(target, { method, headers: {
        cookie: localCookie, "x-yaohe-desktop-control": "renderer-forgery",
      } }));
      expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
      expect(forwardedHeaders.has("x-yaohe-desktop-control")).toBe(false);
    }
  });

  it("requires an authenticated account for the confirmation GET and forwards its verified access token", async () => {
    process.env.NEXT_PUBLIC_AUTH_MODE = "supabase";
    const url = `http://localhost:3000${confirmationPath}`;
    const headers = { cookie: localCookie, authorization: "Bearer renderer-forgery" };
    authMocks.refreshSession.mockResolvedValueOnce({
      userId: null, onboardingComplete: false, accessToken: null, response: null,
    }).mockResolvedValueOnce({
      userId: "verified-user", onboardingComplete: true, accessToken: "verified-session-token", response: null,
    });
    expect((await middleware(new NextRequest(url, { headers }))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    await middleware(new NextRequest(url, { headers }));
    expect(forwardedHeaders.get("authorization")).toBe("Bearer verified-session-token");
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
  });

  it("forwards local identity and Range only for the exact history video path", async () => {
    const path = "/api/creation-tasks/task-1/timeline/versions/version-1/artifacts/video";
    const request = new NextRequest(`http://localhost:3000${path}?preview=true`, {
      headers: { cookie: localCookie, range: "bytes=20-30" },
    });
    const response = await middleware(request);
    expect(forwardedUrl).toBe(`http://127.0.0.1:8000${path}?preview=true`);
    expect(forwardedHeaders.get("x-local-member-token")).toBe(syntheticToken);
    expect(forwardedHeaders.get("range")).toBe("bytes=20-30");
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 20-30/100");

    expect(isLocalNativeMediaRequest("HEAD", path)).toBe(true);
    for (const [method, url] of [
      ["GET", path.replace(/video$/, "subtitles")],
      ["GET", path.replace(/video$/, "cover")],
      ["GET", path.replace("versions/version-1", "versions/version-1/extra")],
      ["POST", path],
    ]) {
      await middleware(new NextRequest(`http://localhost:3000${url}`, { method, headers: { cookie: localCookie } }));
      expect(forwardedHeaders.has("x-local-member-token"), `${method} ${url}`).toBe(false);
    }
  });

  it.each([
    "/api/creation-tasks/task-1/covers/cover-1/image",
    "/api/creation-tasks/task-1/output/selected-subtitles?expected_version_id=version-1&mode=zh&zh_track_id=track-1",
  ])("uses only the current cookie/explicit header for the save path %s", async (savePath) => {
    for (const method of ["GET", "HEAD"]) {
      await middleware(new NextRequest(`http://localhost:3000${savePath}`, { method, headers: { cookie: localCookie } }));
      expect(forwardedHeaders.get("x-local-member-token")).toBe(syntheticToken);
      expect(forwardedUrl).toBe(`http://127.0.0.1:8000${savePath}`);
    }
    for (const explicit of ["", "explicitly-invalid-token"]) {
      await middleware(new NextRequest(`http://localhost:3000${savePath}`, {
        headers: { cookie: localCookie, "x-local-member-token": explicit },
      }));
      expect(forwardedHeaders.get("x-local-member-token")).toBe(explicit);
    }
    for (const cookie of ["", `${localMemberTokenCookieName("http:", "3001")}=wrong-port`]) {
      await middleware(new NextRequest(`http://localhost:3000${savePath}`, { headers: { cookie } }));
      expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
    }
    await middleware(new NextRequest(`http://localhost:3000${savePath}`, { method: "POST", headers: { cookie: localCookie } }));
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
  });

  it.each([
    "/api/creation-tasks/task-1/covers/cover-1/image",
    "/api/creation-tasks/task-1/output/selected-subtitles",
  ])("keeps verified Supabase identity on the save path %s", async (savePath) => {
    process.env.NEXT_PUBLIC_AUTH_MODE = "supabase";
    authMocks.refreshSession.mockResolvedValueOnce({
      userId: "verified-user", onboardingComplete: true, accessToken: "verified-session-token", response: null,
    });
    await middleware(new NextRequest(`http://localhost:3000${savePath}`, {
      headers: { cookie: localCookie, authorization: "Bearer renderer-forgery" },
    }));
    expect(forwardedHeaders.has("x-local-member-token")).toBe(false);
    expect(forwardedHeaders.get("authorization")).toBe("Bearer verified-session-token");
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
