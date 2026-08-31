import { describe, expect, it } from "vitest";
import {
  isLocalAuthMode,
  isOnboardingSupportApiPath,
  isOnboardingSupportPath,
  isPublicAuthPath,
  isPublicApiPath,
  sanitizeNextPath,
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
});
