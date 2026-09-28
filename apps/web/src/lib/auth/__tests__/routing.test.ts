import { describe, expect, it } from "vitest";
import {
  isDeviceInviteAcceptApiPath,
  isLocalAuthMode,
  isOnboardingSupportApiPath,
  isOnboardingSupportPath,
  isPublicAuthPath,
  isPublicApiPath,
  sanitizeNextPath,
  shouldBlockOnboarding,
  signInRedirectUrl,
  trustedApiHeaders,
} from "../routing";

describe("auth routing", () => {
  it("enables local auth only for an explicit internal build", () => {
    const previousMode = process.env.NEXT_PUBLIC_AUTH_MODE;
    try {
      process.env.NEXT_PUBLIC_AUTH_MODE = "local";
      expect(isLocalAuthMode()).toBe(true);
      process.env.NEXT_PUBLIC_AUTH_MODE = "supabase";
      expect(isLocalAuthMode()).toBe(false);
    } finally {
      if (previousMode === undefined) {
        delete process.env.NEXT_PUBLIC_AUTH_MODE;
      } else {
        process.env.NEXT_PUBLIC_AUTH_MODE = previousMode;
      }
    }
  });

  it("keeps sign-in, auth callbacks and legal pages public", () => {
    expect(isPublicAuthPath("/signin")).toBe(true);
    expect(isPublicAuthPath("/auth/callback")).toBe(true);
    expect(isPublicAuthPath("/auth/update-password")).toBe(true);
    expect(isPublicAuthPath("/auth/signout")).toBe(true);
    expect(isPublicAuthPath("/privacy")).toBe(true);
    expect(isPublicAuthPath("/tasks/task-1")).toBe(false);
  });

  it("keeps only readiness endpoints public", () => {
    expect(isPublicApiPath("/api/setup/preflight")).toBe(true);
    expect(isPublicApiPath("/api/runtime/health")).toBe(true);
    expect(isPublicApiPath("/api/creation-tasks/task-1")).toBe(false);
  });

  it("never trusts a browser supplied bearer token", () => {
    const incoming = new Headers({ authorization: "Bearer forged", accept: "application/json" });
    expect(trustedApiHeaders(incoming, null).has("authorization")).toBe(false);
    expect(trustedApiHeaders(incoming, "verified-token").get("authorization")).toBe(
      "Bearer verified-token",
    );
  });

  it("accepts only local non-auth return paths", () => {
    expect(sanitizeNextPath("/tasks/task-1?view=video")).toBe("/tasks/task-1?view=video");
    expect(sanitizeNextPath("https://evil.example")).toBeNull();
    expect(sanitizeNextPath("//evil.example")).toBeNull();
    expect(sanitizeNextPath("/\\evil.example")).toBeNull();
    expect(sanitizeNextPath("/tasks\\evil.example")).toBeNull();
    expect(sanitizeNextPath("C:\\evil.example")).toBeNull();
    expect(sanitizeNextPath("/signin?next=/tasks/task-1")).toBeNull();
    expect(sanitizeNextPath("/auth/callback")).toBeNull();
  });

  it("only exempts onboarding repair routes from the product gate", () => {
    expect(isOnboardingSupportPath("/onboarding")).toBe(true);
    expect(isOnboardingSupportPath("/settings")).toBe(true);
    expect(isOnboardingSupportPath("/tasks/task-1")).toBe(false);
    expect(isOnboardingSupportApiPath("/api/onboarding/complete")).toBe(true);
    expect(isOnboardingSupportApiPath("/api/settings/runtime")).toBe(true);
    expect(isOnboardingSupportApiPath("/api/models")).toBe(true);
    expect(
      isOnboardingSupportApiPath("/api/models/chinese-clip/install"),
    ).toBe(true);
    expect(isOnboardingSupportApiPath("/api/model-install-jobs/abc")).toBe(true);
    expect(isOnboardingSupportApiPath("/api/creation-tasks/task-1")).toBe(false);
  });

  it("preserves the protected route when redirecting to sign-in", () => {
    const target = signInRedirectUrl(
      new URL("https://app.example/tasks/task-1?view=video"),
    );
    expect(target.pathname).toBe("/signin");
    expect(target.searchParams.get("next")).toBe("/tasks/task-1?view=video");
  });

  it("passes through the device-invite accept path for a not-yet-onboarded identity", () => {
    expect(
      isDeviceInviteAcceptApiPath("POST", "/api/device-invites/accept"),
    ).toBe(true);
    expect(
      shouldBlockOnboarding("user-1", false, "POST", "/api/device-invites/accept"),
    ).toBe(false);
  });

  it("keeps the onboarding gate on every other path and method", () => {
    expect(
      shouldBlockOnboarding("user-1", false, "POST", "/api/creation-tasks/task-1"),
    ).toBe(true);
    expect(
      shouldBlockOnboarding("user-1", false, "GET", "/api/device-invites/accept"),
    ).toBe(true);
    expect(
      shouldBlockOnboarding("user-1", false, "POST", "/api/device-invites/accept/extra"),
    ).toBe(true);
    expect(
      shouldBlockOnboarding("user-1", false, "POST", "/api/device-invites"),
    ).toBe(true);
  });

  it("never treats the invite-accept path as public, so a JWT is still required", () => {
    expect(isPublicApiPath("/api/device-invites/accept")).toBe(false);
    expect(isDeviceInviteAcceptApiPath("GET", "/api/device-invites/accept")).toBe(
      false,
    );
    expect(isDeviceInviteAcceptApiPath("POST", "/api/device-invites/accept/extra")).toBe(
      false,
    );
  });

  it("does not block a JWT-less request at the web layer; the API re-verifies", () => {
    expect(
      shouldBlockOnboarding(null, false, "POST", "/api/device-invites/accept"),
    ).toBe(false);
  });
});
