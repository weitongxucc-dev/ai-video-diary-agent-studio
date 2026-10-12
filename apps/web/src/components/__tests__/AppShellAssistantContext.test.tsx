import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CreationTask } from "@/lib/types";

import { AppShell } from "../AppShell";

const fixtures = vi.hoisted(() => ({ pathname: "/settings", listTasks: vi.fn(), composer: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => fixtures.pathname, useRouter: () => ({ push: vi.fn(), back: vi.fn() }), useSearchParams: () => new URLSearchParams() }));
vi.mock("next/link", () => ({ default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a> }));
vi.mock("next/image", () => ({ default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} /> }));
vi.mock("@/components/HomeCommandComposer", () => ({ HomeCommandComposer: (props: unknown) => { fixtures.composer(props); return <p>controlled assistant</p>; } }));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/api", () => ({ listLocalMembers: vi.fn().mockResolvedValue({members:[{member_id:"member-a",name:"测试创作者",role:"owner",created_at:"2026-10-10"}]}),
  listCreationTasks: fixtures.listTasks }));
beforeEach(() => {
  vi.clearAllMocks(); fixtures.pathname = "/settings";
  fixtures.listTasks.mockResolvedValue({ items: [], total_count: 0 });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }) });
});
afterEach(cleanup);
describe("shell default assistant context plumbing", () => {
  it("passes the actual pathname across navigation without selecting an unrelated task", async () => {
    const view = render(<AppShell><p>page</p></AppShell>);
    await waitFor(() => expect(fixtures.composer).toHaveBeenLastCalledWith(expect.objectContaining({ pageContext: { pathname: "/settings" }, task: null })));
    fixtures.pathname = "/members"; view.rerender(<AppShell><p>page</p></AppShell>);
    expect(fixtures.composer).toHaveBeenLastCalledWith(expect.objectContaining({ pageContext: { pathname: "/members" }, task: null }));
  });
  it("passes the matching persisted task and its stage rather than fabricating a task", async () => {
    const task = { id: "task-fixture", name: "已保存任务", stage: "topic", stage_status: "not_started", status: "created", progress: 0 } as CreationTask;
    fixtures.pathname = "/tasks/task-fixture";
    fixtures.listTasks.mockResolvedValue({ items: [task], total_count: 1 });
    render(<AppShell><p>page</p></AppShell>);
    await waitFor(() => expect(fixtures.composer).toHaveBeenLastCalledWith(expect.objectContaining({ task, pageContext: { pathname: fixtures.pathname } })));
  });
  it("keeps an existing page-owned conversation consumer", () => {
    render(<AppShell conversation={<p>existing conversation consumer</p>}><p>page</p></AppShell>);
    expect(screen.getByText("existing conversation consumer")).toBeInTheDocument();
    expect(fixtures.composer).not.toHaveBeenCalled();
  });
});
