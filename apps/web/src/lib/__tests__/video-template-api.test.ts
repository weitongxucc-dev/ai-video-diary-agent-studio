import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({ apiUrl: (path: string) => `http://local.test${path}` }));
vi.mock("@/lib/local-member-auth", () => ({ localMemberTokenHeader: () => ({ "X-Local-Member-Token": "fixture-token" }) }));
import { createVideoTemplateVersion } from "@/lib/video-template-api";
afterEach(() => vi.unstubAllGlobals());
it("uses the authenticated local version endpoint and exact predecessor body", async () => {
  const saved = { id: "template/a", version: 4, name: "新名称", can_edit: true };
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(saved)));
  vi.stubGlobal("fetch", fetch);
  const input = { expected_version: 3, name: "新名称", style_instruction: "" };
  await expect(createVideoTemplateVersion("template/a", input)).resolves.toEqual(saved);
  expect(fetch).toHaveBeenCalledWith("http://local.test/api/video-templates/template%2Fa/versions", expect.objectContaining({ method: "POST", headers: { "Content-Type": "application/json", "X-Local-Member-Token": "fixture-token" }, body: JSON.stringify(input) }));
});
it("surfaces server conflict and local connection failure without a fake saved version", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ detail: "模板版本已变化" }), { status: 409 })).mockRejectedValueOnce(new TypeError("offline"));
  vi.stubGlobal("fetch", fetch);
  const input = { expected_version: 3, name: "名称", style_instruction: "" };
  await expect(createVideoTemplateVersion("template", input)).rejects.toThrow("模板版本已变化");
  await expect(createVideoTemplateVersion("template", input)).rejects.toThrow("本地服务未接通");
});
