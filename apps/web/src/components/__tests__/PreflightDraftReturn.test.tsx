import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ path: "/library", raw: "", scope: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.path, useSearchParams: () => new URLSearchParams({ return_to: mocks.raw }), useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/preflight-draft", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/preflight-draft")>(), readPreflightDraftScope: mocks.scope }));
import { PreflightDraftReturn } from "@/components/PreflightDraftReturn";
import { preflightReturnUrl, savePreflightDraft, type PreflightDraftFields } from "@/lib/preflight-draft";
const id = "12345678-1234-4123-8123-123456789abc";
const scope = { memberId: "local:member", workspaceId: "space-a" };
const fields: PreflightDraftFields = { goal: "制作目标", name: "", duration: "30", targetCount: "1", language: "zh-CN", aspectRatio: "9:16", presenterShare: 20, lipsyncQuality: "high", voicePin: null, personPin: null, voiceEdited: false, template: null };
beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); mocks.path = "/library"; mocks.raw = preflightReturnUrl(id, null); mocks.scope.mockResolvedValue(scope); savePreflightDraft(sessionStorage, scope, id, fields); });
it("exposes the same bounded return on each actual repair resource and pushes only on a click", async () => {
  for (const path of ["/library", "/knowledge", "/sources", "/profiles", "/health", "/settings", "/templates"]) {
    mocks.path = path;
    const view = render(<PreflightDraftReturn />);
    await waitFor(() => expect(screen.getByRole("button", { name: "返回创作" })).toBeEnabled());
    expect(mocks.push).not.toHaveBeenCalled(); view.unmount();
  }
  render(<PreflightDraftReturn />);
  await waitFor(() => expect(screen.getByRole("button", { name: "返回创作" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "返回创作" }));
  await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(mocks.raw));
});
it("does not expose a free return on unrelated pages or accept an external URL", async () => {
  mocks.path = "/tasks/task-a"; const view = render(<PreflightDraftReturn />); expect(screen.queryByRole("button")).toBeNull();
  mocks.path = "/library"; mocks.raw = "https://evil.test" + mocks.raw; view.rerender(<PreflightDraftReturn />);
  expect(await screen.findByRole("alert")).toHaveTextContent("地址无效"); expect(mocks.scope).not.toHaveBeenCalled();
});
it("rechecks member/workspace on the click and refuses another scope's draft", async () => {
  render(<PreflightDraftReturn />); await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
  mocks.scope.mockResolvedValue({ ...scope, workspaceId: "space-b" });
  fireEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("alert")).toHaveTextContent("成员或工作区已改变"); expect(mocks.push).not.toHaveBeenCalled();
});
it("reports unavailable storage or a mismatched fixed template", async () => {
  mocks.raw = preflightReturnUrl(id, { id: "template-a", version: 2 });
  render(<PreflightDraftReturn />); expect(await screen.findByRole("alert")).toHaveTextContent("没有这份创作草稿"); expect(mocks.push).not.toHaveBeenCalled();
});
it("ignores a late return identity reply after the resource page is left", async () => {
  const view = render(<PreflightDraftReturn />); await waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
  let resolve!: (value: typeof scope) => void;
  mocks.scope.mockImplementationOnce(() => new Promise<typeof scope>(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button")); view.unmount();
  await act(async () => resolve(scope)); expect(mocks.push).not.toHaveBeenCalled();
});
