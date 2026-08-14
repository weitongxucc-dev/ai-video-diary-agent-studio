import { describe, expect, it } from "vitest";
import {
  isOnboardingSupportApiPath,
  isOnboardingSupportPath,
  isPublicAuthPath,
  isPublicApiPath,
  sanitizeNextPath,
  signInRedirectUrl,
  trustedApiHeaders,
} from "../routing";

describe("auth routing", () => {
  it("keeps sign-in, auth callbacks and legal pages public", () => {
    expect(isPublicAuthPath("/signin")).toBe(true);
    expect(isPublicAuthPath("/auth/callback")).toBe(true);
    expect(isPublicAuthPath("/auth/signout")).toBe(true);
    expect(isPublicAuthPath("/privacy")).toBe(true);
    expect(isPublicAuthPath("/topics")).toBe(false);
  });

  it("keeps only readiness endpoints public", () => {
    expect(isPublicApiPath("/api/setup/preflight")).toBe(true);
    expect(isPublicApiPath("/api/runtime/health")).toBe(true);
    expect(isPublicApiPath("/api/episodes/today")).toBe(false);
  });

  it("never trusts a browser supplied bearer token", () => {
    const incoming = new Headers({ authorization: "Bearer forged", accept: "application/json" });
    expect(trustedApiHeaders(incoming, null).has("authorization")).toBe(false);
    expect(trustedApiHeaders(incoming, "verified-token").get("authorization")).toBe(
      "Bearer verified-token",
    );
  });

  it("accepts only local non-auth return paths", () => {
    expect(sanitizeNextPath("/topics?q=video")).toBe("/topics?q=video");
    expect(sanitizeNextPath("https://evil.example")).toBeNull();
    expect(sanitizeNextPath("//evil.example")).toBeNull();
    expect(sanitizeNextPath("/\\evil.example")).toBeNull();
    expect(sanitizeNextPath("/topics\\evil.example")).toBeNull();
    expect(sanitizeNextPath("C:\\evil.example")).toBeNull();
    expect(sanitizeNextPath("/signin?next=/topics")).toBeNull();
    expect(sanitizeNextPath("/auth/callback")).toBeNull();
  });

  it("only exempts onboarding repair routes from the product gate", () => {
    expect(isOnboardingSupportPath("/onboarding")).toBe(true);
    expect(isOnboardingSupportPath("/settings")).toBe(true);
    expect(isOnboardingSupportPath("/topics")).toBe(false);
    expect(isOnboardingSupportApiPath("/api/onboarding/complete")).toBe(true);
    expect(isOnboardingSupportApiPath("/api/settings/runtime")).toBe(true);
    expect(isOnboardingSupportApiPath("/api/episodes/today")).toBe(false);
  });

  it("preserves the protected route when redirecting to sign-in", () => {
    const target = signInRedirectUrl(
      new URL("https://app.example/topics?q=video"),
    );
    expect(target.pathname).toBe("/signin");
    expect(target.searchParams.get("next")).toBe("/topics?q=video");
  });
});
