import { describe, it, expect } from "vitest";
import { normalizeSetupPreflight } from "../preflight";
import type {
  SetupPreflightCheckItem,
  SetupPreflightResponse,
} from "../types";

function makeCheck(
  overrides: Partial<SetupPreflightCheckItem> = {},
): SetupPreflightCheckItem {
  return {
    id: "deepseek",
    label: "DeepSeek API",
    status: "ready",
    summary: "已配置（模型：deepseek-chat）",
    impact: "AI 选题、脚本生成、语音指令草案均依赖此服务。",
    repair_action: "无需处理。",
    settings_target: "deepseek_api_key",
    required: false,
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<SetupPreflightResponse> = {},
): SetupPreflightResponse {
  return {
    overall: "ready",
    generated_at: "2026-08-13T02:30:00.000Z",
    checks: [
      makeCheck(),
      makeCheck({ id: "obsidian", label: "Obsidian 知识库", settings_target: "obsidian_vault_path" }),
      makeCheck({ id: "ffmpeg", label: "ffmpeg", settings_target: null }),
      makeCheck({ id: "python", label: "Python 运行时", settings_target: null }),
      makeCheck({ id: "asr", label: "语音转写（ASR）", settings_target: "tencentcloud_secret_id" }),
    ],
    ...overrides,
  } as SetupPreflightResponse;
}

describe("normalizeSetupPreflight", () => {
  it("normalizes an all-ready payload into a ready state with full counts", () => {
    const snapshot = normalizeSetupPreflight(makeResponse());

    expect(snapshot.overall).toBe("ready");
    expect(snapshot.ready).toBe(true);
    expect(snapshot.counts.total).toBe(5);
    expect(snapshot.counts.ready).toBe(5);
    expect(snapshot.counts.attention).toBe(0);
    expect(snapshot.counts.blocked).toBe(0);
    expect(snapshot.counts.notApplicable).toBe(0);
    expect(snapshot.checks).toHaveLength(5);
    expect(snapshot.checks.every((check) => check.state === "ready")).toBe(true);
  });

  it("normalizes a blocked payload and keeps overall/blocked counts consistent", () => {
    const snapshot = normalizeSetupPreflight(
      makeResponse({
        overall: "blocked",
        checks: [
          makeCheck({ id: "deepseek", status: "missing" }),
          makeCheck({ id: "ffmpeg", label: "ffmpeg", status: "missing", settings_target: null, required: true }),
          makeCheck({ id: "python", label: "Python 运行时", status: "ready", settings_target: null }),
        ],
      }),
    );

    expect(snapshot.overall).toBe("blocked");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.counts.total).toBe(3);
    expect(snapshot.counts.ready).toBe(1);
    expect(snapshot.counts.attention).toBe(1);
    expect(snapshot.counts.blocked).toBe(1);
  });

  it("maps degraded/invalid/not_applicable statuses to the UI state vocabulary", () => {
    const snapshot = normalizeSetupPreflight(
      makeResponse({
        overall: "blocked",
        checks: [
          makeCheck({ id: "obsidian", status: "degraded" }),
          makeCheck({ id: "video_inbox", status: "invalid", required: true }),
          makeCheck({ id: "jianying", status: "not_applicable" }),
          makeCheck({ id: "python", label: "Python 运行时", status: "ready", settings_target: null }),
        ],
      }),
    );

    const byId = Object.fromEntries(snapshot.checks.map((check) => [check.id, check]));
    expect(byId.obsidian.state).toBe("attention");
    expect(byId.video_inbox.state).toBe("blocked");
    expect(byId.jianying.state).toBe("not_applicable");
    expect(byId.python.state).toBe("ready");
    expect(snapshot.counts.attention).toBe(1);
    expect(snapshot.counts.blocked).toBe(1);
    expect(snapshot.counts.notApplicable).toBe(1);
  });

  it("parses generated_at into both ISO and epoch millis", () => {
    const snapshot = normalizeSetupPreflight(
      makeResponse({ generated_at: "2026-08-13T02:30:00.000Z" }),
    );

    expect(snapshot.generatedAt).toBe("2026-08-13T02:30:00.000Z");
    expect(snapshot.generatedAtMs).toBe(Date.parse("2026-08-13T02:30:00.000Z"));
  });

  it("treats a missing or unparseable generated_at as null", () => {
    const missing = normalizeSetupPreflight(
      makeResponse({ generated_at: undefined }),
    );
    expect(missing.generatedAt).toBeNull();
    expect(missing.generatedAtMs).toBeNull();

    const malformed = normalizeSetupPreflight(
      makeResponse({ generated_at: "not-a-date" }),
    );
    expect(malformed.generatedAt).toBeNull();
    expect(malformed.generatedAtMs).toBeNull();
  });

  it("coerces settings_target null/empty to null and keeps real values", () => {
    const snapshot = normalizeSetupPreflight(
      makeResponse({
        checks: [
          makeCheck({ id: "a", settings_target: null }),
          makeCheck({ id: "b", settings_target: "" }),
          makeCheck({ id: "c", settings_target: "obsidian_vault_path" }),
        ],
      }),
    );

    const byId = Object.fromEntries(snapshot.checks.map((check) => [check.id, check]));
    expect(byId.a.settingsTarget).toBeNull();
    expect(byId.b.settingsTarget).toBeNull();
    expect(byId.c.settingsTarget).toBe("obsidian_vault_path");
  });

  it("returns a safe empty snapshot for null or undefined input", () => {
    for (const raw of [null, undefined]) {
      const snapshot = normalizeSetupPreflight(raw);
      expect(snapshot.overall).toBe("blocked");
      expect(snapshot.ready).toBe(false);
      expect(snapshot.generatedAt).toBeNull();
      expect(snapshot.generatedAtMs).toBeNull();
      expect(snapshot.counts).toEqual({
        total: 0,
        ready: 0,
        attention: 0,
        blocked: 0,
        notApplicable: 0,
      });
      expect(snapshot.checks).toEqual([]);
    }
  });

  it("defaults an unknown overall to blocked and treats a bad status as invalid", () => {
    const snapshot = normalizeSetupPreflight({
      overall: "weird",
      generated_at: "2026-08-13T02:30:00.000Z",
      checks: [{ id: "deepseek", status: "whatever" }],
    } as unknown as SetupPreflightResponse);

    expect(snapshot.overall).toBe("blocked");
    expect(snapshot.ready).toBe(false);
    expect(snapshot.checks[0].status).toBe("invalid");
    expect(snapshot.checks[0].state).toBe("blocked");
    expect(snapshot.counts.blocked).toBe(1);
  });

  it("drops a non-array checks field instead of throwing", () => {
    const snapshot = normalizeSetupPreflight({
      overall: "ready",
      generated_at: "2026-08-13T02:30:00.000Z",
      checks: "not-an-array",
    } as unknown as SetupPreflightResponse);

    expect(snapshot.checks).toEqual([]);
    expect(snapshot.counts.total).toBe(0);
    expect(snapshot.ready).toBe(true);
  });
});
