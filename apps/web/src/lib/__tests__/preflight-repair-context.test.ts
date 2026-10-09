import { beforeEach, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({ getProductionFoundation: vi.fn() }));
import { clearPreflightDraft, loadPreflightDraft, preflightReturnUrl, replacePreflightDraftTemplate, resolvePreflightRepairContext, savePreflightDraft, savePreflightRepairContext, type PreflightDraftFields } from "@/lib/preflight-draft";
const scope = { memberId: "local:owner", workspaceId: "a" }, other = { memberId: "local:member", workspaceId: "b" };
const id = "12345678-1234-4123-8123-123456789abc", next = "12345678-1234-4123-8123-123456789abd";
const origin = "http://localhost:4321";
const fields: PreflightDraftFields = { goal: "原目标", name: "原名称", targetCount: "2", duration: "30", language: "zh-CN", aspectRatio: "16:9", presenterShare: 40, lipsyncQuality: "fast", voicePin: { profile_id: "voice", version: "v3" }, personPin: { profile_id: "person", version: "p2" }, voiceEdited: true, template: { id: "old", version: 1 } };
beforeEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); savePreflightDraft(sessionStorage, scope, id, fields); savePreflightRepairContext(sessionStorage, scope, id, fields.template); });
it("keeps only scoped UUID and bounded URL in the marker; absent query returns the original draft", () => {
  const context = resolvePreflightRepairContext(sessionStorage, scope, null, origin)!;
  expect(context.target.href).toBe(preflightReturnUrl(id, fields.template)); expect(context.draft).toEqual(fields);
  const markerKey = Object.keys(sessionStorage).find(key => key.includes("preflight-repair"))!;
  const marker = JSON.parse(sessionStorage.getItem(markerKey)!);
  expect(Object.keys(marker).sort()).toEqual(["draftId", "memberId", "returnTo", "version", "workspaceId"]); expect(JSON.stringify(marker)).not.toContain("原目标"); expect(JSON.stringify(marker)).not.toContain("voice");
  expect(resolvePreflightRepairContext(sessionStorage, other, null, origin)).toBeNull();
  expect(resolvePreflightRepairContext(sessionStorage, { ...scope, workspaceId: "b" }, null, origin)).toBeNull();
});
it("explicit malformed, external, credential or lost draft URLs never fall back to the current marker", () => {
  for (const raw of ["", "https://evil.test" + preflightReturnUrl(id, null), "http://u:p@localhost:4321" + preflightReturnUrl(id, null), "/library", preflightReturnUrl(id, fields.template) + "&goal=body"]) expect(() => resolvePreflightRepairContext(sessionStorage, scope, raw, origin)).toThrow("地址无效");
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, preflightReturnUrl(next, fields.template), origin)).toThrow("失联");
});
it("requires the actual existing template version and rejects corrupt or orphaned markers", () => {
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, preflightReturnUrl(id, { id: "old", version: 2 }), origin)).toThrow("失联");
  const markerKey = Object.keys(sessionStorage).find(key => key.includes("preflight-repair"))!;
  const value = JSON.parse(sessionStorage.getItem(markerKey)!); value.memberId = other.memberId; sessionStorage.setItem(markerKey, JSON.stringify(value));
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, null, origin)).toThrow("定位无效");
  savePreflightRepairContext(sessionStorage, scope, id, fields.template);
  const draftKey = Object.keys(sessionStorage).find(key => key.includes("preflight-draft"))!; sessionStorage.removeItem(draftKey);
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, null, origin)).toThrow("失联");
});
it("updates only the explicitly selected fixed template, preserving all user inputs and its UUID", () => {
  const context = resolvePreflightRepairContext(sessionStorage, scope, null, origin)!;
  const template = { id: "selected", version: 7 };
  expect(replacePreflightDraftTemplate(sessionStorage, context, template, origin)).toBe(preflightReturnUrl(id, template));
  expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual({ ...fields, template });
  expect(resolvePreflightRepairContext(sessionStorage, scope, null, origin)?.target.template).toEqual(template);
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, preflightReturnUrl(id, fields.template), origin)).toThrow("失联");
});
it("does not overwrite a newer parallel repair marker and clears only its own UUID/scope", () => {
  const old = resolvePreflightRepairContext(sessionStorage, scope, null, origin)!;
  savePreflightDraft(sessionStorage, scope, next, { ...fields, goal: "下一草稿" }); savePreflightRepairContext(sessionStorage, scope, next, fields.template);
  savePreflightDraft(sessionStorage, other, id, fields); savePreflightRepairContext(sessionStorage, other, id, fields.template);
  expect(() => replacePreflightDraftTemplate(sessionStorage, old, { id: "new", version: 2 }, origin)).toThrow("定位已变化");
  clearPreflightDraft(sessionStorage, scope, id);
  expect(resolvePreflightRepairContext(sessionStorage, scope, null, origin)?.target.draftId).toBe(next);
  expect(resolvePreflightRepairContext(sessionStorage, other, null, origin)?.target.draftId).toBe(id);
  clearPreflightDraft(sessionStorage, scope, next); expect(resolvePreflightRepairContext(sessionStorage, scope, null, origin)).toBeNull();
});
it("rolls back a template change when marker quota fails, keeping both the original fields and marker", () => {
  const context = resolvePreflightRepairContext(sessionStorage, scope, null, origin)!;
  const originalSet = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key, value) { if (key.includes("preflight-repair")) throw new Error("quota"); originalSet.call(this, key, value); });
  expect(() => replacePreflightDraftTemplate(sessionStorage, context, { id: "new", version: 2 }, origin)).toThrow("quota");
  expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(resolvePreflightRepairContext(sessionStorage, scope, null, origin)?.target.href).toBe(context.target.href);
});
it("preserves draft and marker on removal denial, including a failed draft removal after marker removal", () => {
  const originalRemove = Storage.prototype.removeItem;
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function(this: Storage, key) { if (key.includes("preflight-draft")) throw new Error("denied"); originalRemove.call(this, key); });
  expect(() => clearPreflightDraft(sessionStorage, scope, id)).toThrow("denied"); expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields); expect(resolvePreflightRepairContext(sessionStorage, scope, null, origin)?.target.draftId).toBe(id);
});
it("reports storage read refusal instead of inventing a normal no-context template creation", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("storage denied"); });
  expect(() => resolvePreflightRepairContext(sessionStorage, scope, null, origin)).toThrow("storage denied");
});

it.each(["clear", "template"])("reports an interrupted %s rollback without pretending the repair context remains valid", kind => {
  const context = resolvePreflightRepairContext(sessionStorage, scope, null, origin)!;
  if (kind === "clear") {
    const original = Storage.prototype.removeItem;
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function(this: Storage, key) { if (key.includes("preflight-draft")) throw new Error("denied"); original.call(this, key); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("restore denied"); });
    expect(() => clearPreflightDraft(sessionStorage, scope, id)).toThrow("修复定位恢复也失败");
    expect(loadPreflightDraft(sessionStorage, scope, id)).toEqual(fields);
  } else {
    const original = Storage.prototype.setItem; let writes = 0;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key, value) { if (++writes > 1) throw new Error("denied"); original.call(this, key, value); });
    expect(() => replacePreflightDraftTemplate(sessionStorage, context, { id: "new", version: 2 }, origin)).toThrow("原草稿恢复也失败");
    expect(() => resolvePreflightRepairContext(sessionStorage, scope, null, origin)).toThrow("失联");
  }
});
