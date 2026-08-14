import { afterEach, describe, expect, it, vi } from "vitest";
import { readableAuthError } from "../errors";
import {
  getSupabasePublicConfig,
  SUPABASE_CONFIG_ERROR,
} from "../../supabase/config";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("auth configuration and errors", () => {
  it("reports missing public configuration without creating a client", () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "");
    expect(getSupabasePublicConfig()).toEqual({
      ok: false,
      message: SUPABASE_CONFIG_ERROR,
    });
  });

  it("accepts a valid public configuration", () => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
    expect(getSupabasePublicConfig()).toEqual({
      ok: true,
      config: {
        url: "https://project.supabase.co",
        publishableKey: "sb_publishable_test",
      },
    });
  });

  it("translates known auth failures and hides raw unknown errors", () => {
    expect(readableAuthError(new Error("Invalid login credentials"))).toContain(
      "邮箱或密码不正确",
    );
    expect(readableAuthError(new Error("postgres secret detail"))).not.toContain(
      "postgres secret detail",
    );
  });
});
