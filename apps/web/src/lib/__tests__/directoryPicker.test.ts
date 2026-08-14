import { describe, it, expect, vi, afterEach } from "vitest";
import { hasDirectoryPicker, pickDirectory } from "../directoryPicker";

describe("directoryPicker", () => {
  afterEach(() => {
    delete window.electronAPI;
    vi.restoreAllMocks();
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
