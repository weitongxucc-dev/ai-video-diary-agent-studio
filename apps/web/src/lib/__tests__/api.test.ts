import { describe, it, expect, vi, beforeEach } from "vitest";

// ESModule mock for ./types — api.ts imports types from ./types
vi.mock("../types", () => ({}));

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
});

describe("apiUrl", () => {
  it("returns a relative path from API_BASE", async () => {
    const { apiUrl, API_BASE } = await import("../api");
    expect(apiUrl("/api/episodes/today")).toBe(
      `${API_BASE}/api/episodes/today`,
    );
  });

  it("preserves query parameters in the path", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8000");
    const { apiUrl } = await import("../api");
    expect(apiUrl("/api/knowledge/notes?q=test")).toBe(
      "http://localhost:8000/api/knowledge/notes?q=test",
    );
  });

  it("returns path unchanged when API_BASE is empty string", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    const { apiUrl } = await import("../api");
    expect(apiUrl("/api/episodes/123/topic-evidence")).toBe(
      "/api/episodes/123/topic-evidence",
    );
  });
});

describe("API_BASE", () => {
  it("uses NEXT_PUBLIC_API_BASE when set", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://custom:3000");
    const { API_BASE } = await import("../api");
    expect(API_BASE).toBe("http://custom:3000");
  });

  it("defaults to http://localhost:8000 when not set", async () => {
    const { API_BASE } = await import("../api");
    expect(API_BASE).toBe("http://localhost:8000");
  });
});
