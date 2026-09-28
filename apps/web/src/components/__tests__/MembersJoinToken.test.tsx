import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LOCAL_MEMBER_TOKEN_STORAGE_KEY, readLocalMemberToken } from "@/lib/local-member-auth";
import { requestLocalMemberIdentityRefresh, useLocalMemberIdentity } from "@/hooks/useLocalMemberIdentity";

const api = vi.hoisted(() => ({
  listLocalMembers: vi.fn(),
  joinLocalMember: vi.fn(),
  membersErrorMessage: vi.fn((cause: unknown) => (cause instanceof Error ? cause.message : "成员操作失败")),
}));

vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("@/lib/api", () => api);

import { MembersAndSpacesWorkspace } from "@/components/MembersAndSpacesWorkspace";

const owner = { member_id: "m-1", name: "本机创作者", role: "owner" as const, created_at: "2026-09-01T00:00:00Z" };

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
  api.listLocalMembers.mockResolvedValue({ members: [owner] });
});

async function submitJoin() {
  render(<MembersAndSpacesWorkspace />);
  await screen.findByText("本机创作者");
  fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: "CODE-123" } });
  fireEvent.change(screen.getByLabelText("你的名称"), { target: { value: "小王" } });
  fireEvent.click(screen.getByRole("button", { name: "加入" }));
}

describe("join persists the member token", () => {
  it("stores member_token into localStorage after a successful join", async () => {
    api.joinLocalMember.mockResolvedValue({ ...owner, member_id: "m-2", name: "小王", role: "member", member_token: "member-tok-xyz" });
    await submitJoin();
    await waitFor(() => expect(readLocalMemberToken()).toBe("member-tok-xyz"));
    expect(window.localStorage.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY)).toBe("member-tok-xyz");
  });

  it("reports honestly (does NOT fake success) when the backend returns no member_token", async () => {
    // 后端已建成员但没回 token：必须显式报错，不能静默当作加入成功。
    api.joinLocalMember.mockResolvedValue({ member_id: "m-2", name: "小王", role: "member" });
    await submitJoin();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("没有返回成员令牌");
    // 关键：没有令牌就不许写 storage、也不许出现成功提示（role=status）
    expect(readLocalMemberToken()).toBeNull();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText(/已加入空间。/)).not.toBeInTheDocument();
  });
});

describe("useLocalMemberIdentity refresh", () => {
  it("converges role from owner to member after a refresh (token now on disk)", async () => {
    // 首屏：storage 已有 owner 令牌 → whoami 回 owner
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "owner-tok");
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ member_id: "m-1", name: "创作者", role: "owner", created_at: "t", member_token: "owner-tok" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useLocalMemberIdentity());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.role).toBe("owner");

    // 模拟 join 后：storage 换成 member 令牌，whoami 改为回 member
    window.localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, "member-tok");
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ member_id: "m-2", name: "小王", role: "member", created_at: "t", member_token: "member-tok" }), { status: 200 }),
    );

    act(() => { result.current.refresh(); });
    await waitFor(() => expect(result.current.role).toBe("member"));
    expect(result.current.token).toBe("member-tok");
    vi.unstubAllGlobals();
  });

  it("re-reads identity for every mounted hook when a refresh is requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ member_id: "m-1", name: "创作者", role: "owner", created_at: "t", member_token: "tok" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useLocalMemberIdentity());
    await waitFor(() => expect(result.current.ready).toBe(true));
    const before = fetchMock.mock.calls.length;
    act(() => { requestLocalMemberIdentityRefresh(); });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
    vi.unstubAllGlobals();
  });
});
