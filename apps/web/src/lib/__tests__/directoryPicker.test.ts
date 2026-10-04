import { describe, it, expect, vi, afterEach } from "vitest";
import { hasDirectoryPicker, pickDirectory, importLocalMaterials } from "../directoryPicker";
import { LOCAL_MEMBER_TOKEN_STORAGE_KEY } from "../local-member-auth";

const session = vi.hoisted(() => vi.fn());
vi.mock("../supabase/client", () => ({ createClient: () => ({ auth: { getSession: session } }) }));

describe("directoryPicker", () => {
  afterEach(() => {
    delete window.electronAPI;
    window.localStorage.clear();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("passes the current member credential through the native material import bridge", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "local");
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "member-b-token");
    const importer = vi.fn().mockResolvedValue({ copied: [{ sourceName: "clip.mp4", fileName: "clip.mp4" }] });
    window.electronAPI = { importMaterials: importer };
    await importLocalMaterials();
    expect(importer).toHaveBeenCalledWith({ localMemberToken: "member-b-token" });
  });

  it("passes the existing authenticated session and does not import without it", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_MODE", "supabase");
    const importer = vi.fn().mockResolvedValue({ canceled: true });
    window.electronAPI = { importMaterials: importer };
    session.mockResolvedValue({ data: { session: { access_token: "isolated-access-token" } }, error: null });
    await importLocalMaterials();
    expect(importer).toHaveBeenCalledWith({ authorization: "Bearer isolated-access-token" });
    importer.mockClear();
    session.mockResolvedValue({ data: { session: null }, error: null });
    await expect(importLocalMaterials()).resolves.toEqual({ error: "请先登录，再导入素材。" });
    expect(importer).not.toHaveBeenCalled();
  });

  it("reports unavailable when electronAPI is absent", () => {
    delete window.electronAPI;
    expect(hasDirectoryPicker()).toBe(false);
  });

  it("reports unavailable when openDirectory is not a function", () => {
    window.electronAPI = {};
    expect(hasDirectoryPicker()).toBe(false);
  });

  it("returns unavailable from pickDirectory without electronAPI", async () => {
    delete window.electronAPI;
    await expect(pickDirectory()).resolves.toEqual({ kind: "unavailable" });
  });

  it("returns picked with the absolute path from the native dialog", async () => {
    window.electronAPI = {
      openDirectory: vi.fn().mockResolvedValue("/Users/me/ObsidianVault"),
    };
    expect(hasDirectoryPicker()).toBe(true);
    await expect(pickDirectory()).resolves.toEqual({
      kind: "picked",
      path: "/Users/me/ObsidianVault",
    });
    expect(window.electronAPI.openDirectory).toHaveBeenCalledTimes(1);
  });

  it("returns canceled when the dialog resolves null", async () => {
    window.electronAPI = { openDirectory: vi.fn().mockResolvedValue(null) };
    await expect(pickDirectory()).resolves.toEqual({ kind: "canceled" });
  });

  it("returns canceled when the dialog resolves an empty string", async () => {
    window.electronAPI = { openDirectory: vi.fn().mockResolvedValue("") };
    await expect(pickDirectory()).resolves.toEqual({ kind: "canceled" });
  });

  it("returns canceled when the bridge rejects instead of throwing", async () => {
    window.electronAPI = {
      openDirectory: vi.fn().mockRejectedValue(new Error("ipc down")),
    };
    await expect(pickDirectory()).resolves.toEqual({ kind: "canceled" });
  });
});
