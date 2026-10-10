import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ path: "/settings", search: "", scope: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.path, useSearchParams: () => new URLSearchParams(mocks.search), useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/preflight-draft", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: mocks.scope }));
import { PreflightDraftReturn } from "@/components/PreflightDraftReturn";
import { preflightReturnUrl, savePreflightDraft, savePreflightRepairContext, type PreflightDraftFields } from "@/lib/preflight-draft";
const scope = { memberId: "local:member", workspaceId: "space-a" };
const id = "12345678-1234-4123-8123-123456789abc", otherId = "12345678-1234-4123-8123-123456789abd";
const fields: PreflightDraftFields = { goal: "原目标", name: "", duration: "30", targetCount: "1", language: "zh-CN", aspectRatio: "9:16", presenterShare: 20, lipsyncQuality: "high", voicePin: null, personPin: null, voiceEdited: false, template: null };
beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); mocks.path = "/settings"; mocks.scope.mockResolvedValue(scope); savePreflightDraft(sessionStorage, scope, id, fields); savePreflightRepairContext(sessionStorage, scope, id, null); mocks.search = new URLSearchParams({ return_to: preflightReturnUrl(id, null) }).toString(); });
it("returns the same goal after settings' actual section URL drops return_to", async () => {
  const view = render(<PreflightDraftReturn />); await screen.findByRole("button", { name: "返回创作" });
  // settings/page.tsx's existing column Link: /settings?section=${item.id}.
  mocks.search = "section=connections"; view.rerender(<PreflightDraftReturn />);
  await screen.findByRole("button", { name: "返回创作" }); fireEvent.click(screen.getByRole("button", { name: "返回创作" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(preflightReturnUrl(id, null)));
});
it("survives sources' saved-receipt /knowledge navigation with no query", async () => {
  mocks.path = "/sources"; const view = render(<PreflightDraftReturn />); await screen.findByRole("button", { name: "返回创作" });
  // sources/page.tsx uses href=/knowledge after a real save receipt; we model only the navigation.
  mocks.path = "/knowledge"; mocks.search = ""; view.rerender(<PreflightDraftReturn />);
  await screen.findByRole("button", { name: "返回创作" }); fireEvent.click(screen.getByRole("button", { name: "返回创作" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(preflightReturnUrl(id, null)));
});
it("does not recover a marker after an explicitly invalid or empty return_to", async () => {
  mocks.search = "return_to="; render(<PreflightDraftReturn />);
  expect(await screen.findByRole("alert")).toHaveTextContent("没有回退其他草稿"); expect(screen.queryByRole("button")).toBeNull(); expect(mocks.push).not.toHaveBeenCalled();
});
it.each([{ memberId: "local:other", workspaceId: "space-a" }, { ...scope, workspaceId: "space-b" }])("does not discover another member or workspace's repair marker", async other => {
  mocks.scope.mockResolvedValue(other); mocks.search = ""; render(<PreflightDraftReturn />);
  await act(async () => {}); expect(screen.queryByRole("button")).toBeNull(); expect(mocks.push).not.toHaveBeenCalled();
});
it("refuses an adjacent UUID marker change between rendering and the actual return click", async () => {
  mocks.search = ""; render(<PreflightDraftReturn />); await screen.findByRole("button", { name: "返回创作" });
  savePreflightDraft(sessionStorage, scope, otherId, { ...fields, goal: "第二草稿" }); savePreflightRepairContext(sessionStorage, scope, otherId, null);
  fireEvent.click(screen.getByRole("button", { name: "返回创作" })); expect(await screen.findByRole("alert")).toHaveTextContent("定位已改变"); expect(mocks.push).not.toHaveBeenCalled();
});
it("does not navigate on storage refusal or a late member reply from a departed resource page", async () => {
  mocks.search = ""; const view = render(<PreflightDraftReturn />); await screen.findByRole("button", { name: "返回创作" });
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
  fireEvent.click(screen.getByRole("button", { name: "返回创作" })); expect(await screen.findByRole("alert")).toHaveTextContent("denied"); expect(mocks.push).not.toHaveBeenCalled(); get.mockRestore(); view.unmount();
  const next = render(<PreflightDraftReturn />); await screen.findByRole("button", { name: "返回创作" });
  let resolve!: (value: typeof scope) => void; mocks.scope.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button", { name: "返回创作" })); next.unmount(); await act(async () => resolve(scope)); expect(mocks.push).not.toHaveBeenCalled();
});
