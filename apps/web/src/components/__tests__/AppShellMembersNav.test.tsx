import { fireEvent, render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes, ImgHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ role: null as "owner" | "member" | null, ready: true }));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a href={href} {...props}>{children}</a>,
}));
vi.mock("next/image", () => ({
  default: ({ alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => <img alt={alt} {...props} />,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/components/HelpPanel", () => ({ HelpPanel: () => null }));
vi.mock("@/components/KeyboardShortcuts", () => ({ KeyboardShortcuts: () => null }));
vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: vi.fn() }));
vi.mock("@/lib/api", () => ({ listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }), getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }),
  getCurrentAssistantConversation: vi.fn(async (scope: import("@/lib/types").AssistantConversationScope) => ({
    id: `conversation-${scope.scope_kind}-${scope.scope_id}`, workspace_id: "workspace-1", ...scope,
    title: "新对话", draft_text: "", draft_version: 0, created_at: "2026-10-09T00:00:00Z", updated_at: "2026-10-09T00:00:00Z", turns: [],
  } satisfies import("@/lib/types").AssistantConversation)),
  listAssistantConversations: vi.fn().mockResolvedValue({ items: [] }),
  getAssistantCapabilities: vi.fn().mockResolvedValue({
    application_commands: ["/new", "/name <名称>", "/resume <会话ID>", "/help"],
    pi_tui_commands: { available: false, inventory_source: "静态命令清单", inventory_version: "1.1.0", runtime_version: null, runtime_status: "missing", runtime_reason: "未确认", runtime_check: "隔离检查", product_mode: "headless", reason: "未接通", commands: [] },
    extension_commands: { available: false, reason: "未接通" }, skills: { available: false, reason: "未接通" }, prompt_templates: { available: false, reason: "未接通" }, business_tools: {},
  } satisfies import("@/lib/types").AssistantCapabilities),
}));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({
  useLocalMemberIdentity: () => ({ token: "tok", role: identity.role, ready: identity.ready, error: null }),
}));

import { AppShell } from "@/components/AppShell";

function openPalette() {
  fireEvent.click(screen.getAllByRole("button", { name: "搜索" })[0]);
  return screen.getByRole("dialog", { name: "搜索页面和创作任务" });
}

describe("members nav visibility by role", () => {
  beforeEach(() => {
    identity.role = null;
    identity.ready = true;
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }),
    });
  });

  it("hides 成员与空间 from a member in BOTH the sidebar and the command palette", () => {
    identity.role = "member";
    render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);

    expect(screen.queryByRole("link", { name: "成员与空间" })).not.toBeInTheDocument();
    openPalette();
    expect(screen.queryByRole("option", { name: /成员与空间/ })).not.toBeInTheDocument();
  });

  it("shows 成员与空间 to an owner in the sidebar and palette", () => {
    identity.role = "owner";
    render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);

    expect(screen.getByRole("link", { name: "成员与空间" })).toBeInTheDocument();
    openPalette();
    expect(screen.getByRole("option", { name: /成员与空间/ })).toBeInTheDocument();
  });

  it("shows 成员与空间 while identity is still loading (don't hide from owner on first paint)", () => {
    identity.role = null;
    identity.ready = false;
    render(<AppShell creationTasks={[]}><p>页面内容</p></AppShell>);

    expect(screen.getByRole("link", { name: "成员与空间" })).toBeInTheDocument();
  });
});
