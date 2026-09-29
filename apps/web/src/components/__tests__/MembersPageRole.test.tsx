import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LocalMemberRole } from "@/lib/local-member-auth";

const identity = vi.hoisted(() => ({
  role: null as LocalMemberRole | null,
  ready: true,
  error: null as string | null,
}));

const api = vi.hoisted(() => ({ listLocalMembers: vi.fn() }));

vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/lib/api", () => ({ ...api, membersErrorMessage: vi.fn((c: unknown) => (c instanceof Error ? c.message : "成员操作失败")) }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({
  useLocalMemberIdentity: () => ({ token: "tok", role: identity.role, ready: identity.ready, error: identity.error, refresh: vi.fn() }),
}));

import MembersPage from "@/app/members/page";

const owner = { member_id: "m-1", name: "本机创作者", role: "owner" as const, created_at: "2026-09-01T00:00:00Z" };
const member = { member_id: "m-2", name: "小王", role: "member" as const, created_at: "2026-09-02T00:00:00Z" };

/** owner 专属控件：加成员 / 改角色 / 移除 / 邀请码。member 一个都不该看见。 */
const OWNER_CONTROLS = [
  { name: "button", text: "添加成员" },
  { name: "button", text: "移除" },
  { name: "button", text: "设为所有者" },
  { name: "button", text: "设为成员" },
  { name: "button", text: "生成邀请码" },
];

beforeEach(() => {
  identity.role = null;
  identity.ready = true;
  identity.error = null;
  api.listLocalMembers.mockReset();
  api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
});

describe("members page renders by real identity", () => {
  it("a member sees NO owner-only controls (the controls that would 403)", async () => {
    identity.role = "member";
    render(<MembersPage />);
    await screen.findByText("小王");
    for (const control of OWNER_CONTROLS) {
      expect(screen.queryByRole(control.name as "button", { name: control.text })).not.toBeInTheDocument();
    }
    expect(screen.queryByLabelText("所有者操作")).not.toBeInTheDocument();
    // 成员列表本身照常可读
    expect(screen.getByText("本机创作者")).toBeInTheDocument();
  });

  it("an owner still sees the owner-only controls (wiring must not strip them)", async () => {
    identity.role = "owner";
    render(<MembersPage />);
    await screen.findByText("小王");
    expect(screen.getByRole("button", { name: "添加成员" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成邀请码" })).toBeInTheDocument();
    expect(screen.getByLabelText("所有者操作")).toBeInTheDocument();
    // per-member 控件：每位成员一行，所以是多个（移除/改角色）
    expect(screen.getAllByRole("button", { name: "移除" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "设为成员" }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByRole("button", { name: "设为所有者" }).length).toBeGreaterThanOrEqual(1);
  });

  it("renders no identity UI while identity is still loading (no owner flash on first paint)", async () => {
    identity.role = null;
    identity.ready = false;
    render(<MembersPage />);
    expect(screen.getByRole("status")).toHaveTextContent("正在确认身份");
    // 加载中：既不闪 owner 控件，也不渲染成员列表
    for (const control of OWNER_CONTROLS) {
      expect(screen.queryByRole(control.name as "button", { name: control.text })).not.toBeInTheDocument();
    }
    expect(screen.queryByText("小王")).not.toBeInTheDocument();
  });

  it("shows an honest error and withholds controls when identity lookup fails", async () => {
    identity.role = null;
    identity.ready = true;
    identity.error = "成员凭据无效";
    render(<MembersPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("成员凭据无效");
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    for (const control of OWNER_CONTROLS) {
      expect(screen.queryByRole(control.name as "button", { name: control.text })).not.toBeInTheDocument();
    }
  });

  it("keeps the join panel available to a member (joining is not an owner-only action)", async () => {
    identity.role = "member";
    render(<MembersPage />);
    await screen.findByText("小王");
    expect(screen.getByRole("button", { name: "加入" })).toBeInTheDocument();
  });

  it("falls back to the read-only view when the role is unknown but loading succeeded", async () => {
    identity.role = null;
    identity.ready = true;
    render(<MembersPage />);
    await waitFor(() => expect(screen.getByText("小王")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "加入" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加成员" })).not.toBeInTheDocument();
  });
});
