import { describe, it, expect, vi, beforeEach } from "vitest";

// ESModule mock for ./types — api.ts imports types from ./types
vi.mock("../types", () => ({}));

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("apiUrl", () => {
  it("returns a relative path from API_BASE", async () => {
    const { apiUrl, API_BASE } = await import("../api");
    expect(apiUrl("/api/creation-tasks/task-1")).toBe(
      `${API_BASE}/api/creation-tasks/task-1`,
    );
  });

  it("keeps media URLs on the same-origin API proxy", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:8000");
    const { apiUrl } = await import("../api");
    expect(apiUrl("/api/knowledge/notes?q=test")).toBe(
      "/api/knowledge/notes?q=test",
    );
  });

  it("returns path unchanged when API_BASE is empty string", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "");
    const { apiUrl } = await import("../api");
    expect(apiUrl("/api/creation-tasks/task-1/topics")).toBe(
      "/api/creation-tasks/task-1/topics",
    );
  });
});

describe("API_BASE", () => {
  it("uses NEXT_PUBLIC_API_BASE when set", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://custom:3000");
    const { API_BASE } = await import("../api");
    expect(API_BASE).toBe("http://custom:3000");
  });

  it("defaults to empty string when not set", async () => {
    const { API_BASE } = await import("../api");
    expect(API_BASE).toBe("");
  });
});

describe("server authorization provider", () => {
  it("does not call the server provider in the browser bundle", async () => {
    const provider = vi.fn().mockResolvedValue("Bearer verified");
    const { setServerAuthorizationProvider, getSystemStatus } = await import("../api");
    setServerAuthorizationProvider(provider);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    }));

    await getSystemStatus();
    expect(provider).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledWith(
      "/api/system/status",
      expect.objectContaining({
        headers: expect.not.objectContaining({ Authorization: expect.anything() }),
      }),
    );
  });
});

describe("creation execution API", () => {
  it("sends an explicit start bound to the previewed script and requirement digest", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve("{}") });
    vi.stubGlobal("fetch", fetchMock);
    const { startCreationExecution } = await import("../api");
    await startCreationExecution("task 1", {
      task_id: "task 1", script_id: "script-1", script_title: "文案", script_sections: [],
      plan_id: "plan-1", input_digest: "a".repeat(64),
      requirements: { aspect_ratio: "9:16", duration_target_seconds: 30,
        presenter_share_target: 20, lipsync_quality: "high", selected_voice: "voice-1",
        template_settings: {} },
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/creation-tasks/task%201/execution/start", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ script_id: "script-1", input_digest: "a".repeat(64), requirements_confirmed: true }),
    }));
  });

  it("sends a single asset tag replacement to the scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    const { updateMaterialAssetTags } = await import("../api");
    await updateMaterialAssetTags("asset/1", ["访谈"]);
    expect(fetchMock).toHaveBeenCalledWith("/api/production/materials/asset%2F1/tags", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ user_tags: ["访谈"] }),
    }));
  });

  it("binds only the directory the user inspected", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    const { bindMaterialIntake } = await import("../api");
    await bindMaterialIntake("/Volumes/test/library");
    expect(fetchMock).toHaveBeenCalledWith("/api/production/materials/intake/bind", expect.objectContaining({
      method: "POST", body: JSON.stringify({ expected_directory: "/Volumes/test/library" }),
    }));
  });

  it("reads task-scoped assistant history", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: () => Promise.resolve(JSON.stringify({ items: [] })) });
    vi.stubGlobal("fetch", fetchMock);
    const { getCreationTaskAssistantHistory } = await import("../api");
    await getCreationTaskAssistantHistory("task/one");
    expect(fetchMock).toHaveBeenCalledWith("/api/creation-tasks/task%2Fone/assistant/history", expect.anything());
  });
});
