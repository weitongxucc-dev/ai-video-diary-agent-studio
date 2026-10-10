import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
const identity = vi.hoisted(() => ({ store: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/local-member-auth", () => ({ storeLocalMemberToken: identity.store }));
vi.mock("@/hooks/useLocalMemberIdentity", () => ({ requestLocalMemberIdentityRefresh: identity.refresh }));

import { MembersAndSpacesWorkspace } from "@/components/MembersAndSpacesWorkspace";

const owner = { member_id: "m-1", name: "本机创作者", role: "owner" as const, created_at: "2026-09-01T00:00:00Z" };
const member = { member_id: "m-2", name: "小王", role: "member" as const, created_at: "2026-09-02T00:00:00Z" };

afterEach(() => { vi.clearAllMocks(); });
beforeEach(() => {
  api.addLocalMember.mockResolvedValue(member);
  api.setLocalMemberRole.mockResolvedValue({ ...member, role: "owner" });
  api.joinLocalMember.mockResolvedValue({ ...member, member_token: "fixture-token" });
});

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
    expect(await screen.findByText("已添加成员 小王。")).toBeInTheDocument();
    expect(screen.getByLabelText("成员名称")).toHaveValue("");
    expect(api.listLocalMembers).toHaveBeenCalledTimes(2);
  });

  it("owner can remove a member", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("小王");
    fireEvent.click(within(screen.getByText("小王").closest("li")!).getByRole("button", { name: "管理成员 小王" }));
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(api.removeLocalMember).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认移除" }));
    await waitFor(() => expect(api.removeLocalMember).toHaveBeenCalledWith("m-2"));
  });

  it("owner can change a member's role", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace role="owner" />);
    await screen.findByText("小王");
    fireEvent.click(within(screen.getByText("小王").closest("li")!).getByRole("button", { name: "管理成员 小王" }));
    fireEvent.click(screen.getByRole("button", { name: "设为所有者" }));
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
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
    fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "加入" }));
    await waitFor(() => expect(api.joinLocalMember).toHaveBeenCalledWith({ code: "CODE-123", name: "小王" }));
    expect(await screen.findByText("小王 已加入空间。")).toBeInTheDocument();
    expect(identity.store).toHaveBeenCalledWith("fixture-token");
    expect(identity.refresh).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("邀请码")).toHaveValue("");
    expect(screen.getByLabelText("你的名称")).toHaveValue("");
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
    expect(screen.queryByRole("button", { name: /管理成员/ })).not.toBeInTheDocument();
  });

  it("marks whitespace-only member names required and validates beside the field", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    const input = screen.getByLabelText("成员名称");
    expect(input).toBeRequired();
    expect(screen.getByRole("button", { name: "添加成员" })).toBeDisabled();
    expect(input).toHaveAccessibleDescription("填写名称后可添加成员。");
    fireEvent.change(input, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "添加成员" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "添加成员" }));
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("请填写成员名称。");
    expect(input).toHaveFocus();
    expect(api.addLocalMember).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: " 小王 " } });
    expect(screen.queryByText("请填写成员名称。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
    await waitFor(() => expect(api.addLocalMember).toHaveBeenCalledWith({ name: "小王" }));
  });

  it("validates both join fields locally and does not submit incomplete input", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    expect(screen.getByRole("button", { name: "加入" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "加入空间" }));
    expect(screen.getByLabelText("邀请码")).toHaveAccessibleDescription("请填写邀请码。");
    expect(screen.getByLabelText("你的名称")).toHaveAccessibleDescription("请填写你的名称。");
    expect(api.joinLocalMember).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
    fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "加入" })).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: "加入空间" }));
    expect(screen.getByLabelText("你的名称")).toHaveFocus();
    expect(api.joinLocalMember).not.toHaveBeenCalled();
  });

  it("keeps member data and input when adding is rejected, then allows correction", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.addLocalMember.mockRejectedValueOnce(new Error("成员名已存在"));
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("成员名称"), { target: { value: "重复名字" } });
    fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
    expect(await within(screen.getByRole("form", { name: "添加成员" })).findByRole("alert")).toHaveTextContent("成员名已存在");
    expect(screen.getByLabelText("成员名称")).toHaveValue("重复名字");
    expect(screen.getByText("本机创作者")).toBeInTheDocument();
    expect(screen.queryByText("成员列表暂不可读取")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("成员名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
    expect(await screen.findByText("已添加成员 小王。")).toBeInTheDocument();
  });

  it.each(["邀请码无效或已使用", "使用名额已满"])("keeps join input and shows service rejection locally: %s", async message => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.joinLocalMember.mockRejectedValueOnce(new Error(message));
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
    fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "加入" }));
    expect(await within(screen.getByRole("form", { name: "加入空间" })).findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByLabelText("邀请码")).toHaveValue("CODE-123");
    expect(screen.getByLabelText("你的名称")).toHaveValue("小王");
    expect(identity.store).not.toHaveBeenCalled();
    expect(screen.getByText("本机创作者")).toBeInTheDocument();
  });

  it("does not claim identity adoption if the service omits the member token", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    api.joinLocalMember.mockResolvedValueOnce(member);
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
    fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "小王" } });
    fireEvent.click(screen.getByRole("button", { name: "加入" }));
    expect(await within(screen.getByRole("form", { name: "加入空间" })).findByRole("alert")).toHaveTextContent("没有返回成员令牌");
    expect(identity.store).not.toHaveBeenCalled();
    expect(identity.refresh).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps row actions compact and explains the last-owner constraint", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("小王");
    const row = screen.getByText("本机创作者").closest("li")!;
    expect(within(row).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(within(row).getByRole("button", { name: "管理成员 本机创作者" }));
    expect(screen.getByRole("button", { name: "设为成员" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "移除" })).toBeDisabled();
    expect(screen.getByText("至少保留一位所有者，暂不能降为成员或移除。")).toBeInTheDocument();
    expect(api.removeLocalMember).not.toHaveBeenCalled();
    expect(api.setLocalMemberRole).not.toHaveBeenCalled();
  });

  it("keeps both owners' actions available but honors a fresh service rejection", async () => {
    const secondOwner = { ...member, role: "owner" as const };
    api.listLocalMembers.mockResolvedValue({ members: [owner, secondOwner] });
    api.setLocalMemberRole.mockRejectedValueOnce(new Error("至少保留一个 owner"));
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("小王");
    fireEvent.click(screen.getByRole("button", { name: "管理成员 本机创作者" }));
    const action = screen.getByRole("button", { name: "设为成员" });
    expect(action).toBeEnabled();
    fireEvent.click(action);
    expect(await screen.findByRole("alert")).toHaveTextContent("至少保留一个 owner");
    expect(screen.getByText("本机创作者")).toBeInTheDocument();
    expect(screen.getByText("小王")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("成员列表暂不可读取")).not.toBeInTheDocument();
  });

  it("blocks duplicate form submission until the actual request settles", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner] });
    let finish!: (value: typeof member) => void;
    api.addLocalMember.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("本机创作者");
    fireEvent.change(screen.getByLabelText("成员名称"), { target: { value: "小王" } });
    const form = screen.getByRole("form", { name: "添加成员" });
    fireEvent.submit(form);
    expect(screen.getByRole("button", { name: "添加成员" })).toBeDisabled();
    fireEvent.submit(form);
    expect(api.addLocalMember).toHaveBeenCalledOnce();
    await act(async () => finish(member));
    expect(screen.getByLabelText("成员名称")).toHaveValue("");
    expect(screen.getByRole("button", { name: "添加成员" })).toBeDisabled();
  });

  it("cancels member removal without an API call and restores the compact action", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("小王");
    fireEvent.click(screen.getByRole("button", { name: "管理成员 小王" }));
    const trigger = screen.getByRole("button", { name: "移除" });
    trigger.focus(); fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "移除成员" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "移除成员" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(api.removeLocalMember).not.toHaveBeenCalled();
  });

  it("retains the removal target and truthful error after service rejection", async () => {
    api.listLocalMembers.mockResolvedValue({ members: [owner, member] });
    api.removeLocalMember.mockRejectedValueOnce(new Error("成员操作被拒绝"));
    render(<MembersAndSpacesWorkspace />);
    await screen.findByText("小王");
    fireEvent.click(screen.getByRole("button", { name: "管理成员 小王" }));
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认移除" }));
    const confirmation = screen.getByRole("dialog", { name: "移除成员" });
    expect(await within(confirmation).findByRole("alert")).toHaveTextContent("成员操作被拒绝");
    expect(confirmation).toHaveTextContent("小王");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog", { name: "移除成员" })).not.toBeInTheDocument();
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
