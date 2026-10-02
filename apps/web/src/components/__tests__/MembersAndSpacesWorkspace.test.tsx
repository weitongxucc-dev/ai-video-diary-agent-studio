import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listLocalMembers: vi.fn(),
  addLocalMember: vi.fn(),
  removeLocalMember: vi.fn(),
  setLocalMemberRole: vi.fn(),
  createLocalInviteCode: vi.fn(),
  joinLocalMember: vi.fn(),
  membersErrorMessage: vi.fn((cause: unknown) => (cause instanceof Error ? cause.message : "成员操作失败")),
}));

vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/lib/api", () => api);

import { MembersAndSpacesWorkspace } from "@/components/MembersAndSpacesWorkspace";

const owner = { member_id: "m-1", name: "本机创作者", role: "owner" as const, created_at: "2026-09-01T00:00:00Z" };
const member = { member_id: "m-2", name: "小王", role: "member" as const, created_at: "2026-09-02T00:00:00Z" };

afterEach(() => { vi.clearAllMocks(); });

describe("MembersAndSpacesWorkspace", () => {
  it.each(["owner", "member"] as const)("leaves the page title to AppShell for %s and keeps refresh available", async (role) => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role={role} />);
    await screen.findByText("小王");
    expect(screen.queryByText("成员与空间")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(api.listLocalMembers).toHaveBeenCalledTimes(2));
  });
  it("lists members with name and role badges", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace />);
    expect(await screen.findByText("本机创作者")).toBeInTheDocument();
    expect(screen.getByText("小王")).toBeInTheDocument();
    expect(screen.getByText("所有者")).toBeInTheDocument();
    expect(screen.getByText("成员")).toBeInTheDocument();
  });

  it("owner can add a member by name", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.addLocalMember.mockResolvedValue(member);
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("成员名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
    await waitFor(() => expect(api.addLocalMember).toHaveBeenCalledWith({ name: "小王" }));
  });

  it("owner can remove a member", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("小王");
    fireEvent.click(within(screen.getByText("小王").closest("li")!).getByRole("button", { name: "移除" }));
    await waitFor(() => expect(api.removeLocalMember).toHaveBeenCalledWith("m-2"));
  });

  it("owner can change a member's role", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("小王");
    fireEvent.click(within(screen.getByText("小王").closest("li")!).getByRole("button", { name: "设为所有者" }));
    await waitFor(() => expect(api.setLocalMemberRole).toHaveBeenCalledWith("m-2", "owner"));
  });

  it("owner can generate a local invite code", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.createLocalInviteCode.mockResolvedValue({ invite_code: "CODE-123" });
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("本机创作者");
    fireEvent.click(screen.getByRole("button", { name: "生成邀请码" }));
    expect(await screen.findByText("CODE-123")).toBeInTheDocument();
  });

  it("joins the space with an invite code and name", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.joinLocalMember.mockResolvedValue(member);
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
    fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "加入" }));
    await waitFor(() => expect(api.joinLocalMember).toHaveBeenCalledWith({ code: "CODE-123", name: "小王" }));
  });

  it("member role is read-only: no add, remove, or role controls", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role="member" />);
    await screen.findByText("小王");
    expect(screen.getByText("所有者")).toBeInTheDocument();
    expect(screen.getByText("成员")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加成员" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "移除" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "设为所有者" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成邀请码" })).not.toBeInTheDocument();
  });

  it("shows an honest empty state when there are no members", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [] });
    render(<MembersAndSpacesWorkspace />);
    expect(await screen.findByText("还没有成员")).toBeInTheDocument();
  });

  it("shows an honest error when members cannot be read", async () => {
    api.listLocalMembers.mockRejectedValue(new Error("本地服务未接通"));
    render(<MembersAndSpacesWorkspace />);
    expect(await screen.findByText("本地服务未接通")).toBeInTheDocument();
  });
});
