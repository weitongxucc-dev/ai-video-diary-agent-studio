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
vi.mock("@/lib/api", () => ({ listCreationTasks: vi.fn().mockResolvedValue({ items: [], total_count: 0 }), getRuntimeSettings: vi.fn().mockResolvedValue({ status: { llm: { configured: false } } }) }));
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
