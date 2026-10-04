/**
 * 首页真实数据容器的身份与请求时序：
 * 切成员后旧身份的响应不能落到页面上，重试必须用当前选择的天数。
 * 全部使用受控的假接口，不连真实服务、不读真实数据。
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HomeAggregation } from "@/lib/types";

const api = vi.hoisted(() => ({
  getHomeDashboard: vi.fn(),
  listCreationTasks: vi.fn(),
  getProductionFoundation: vi.fn(),
  getMaterialLibraryInsights: vi.fn(),
}));
const identity = vi.hoisted(() => ({ state: { token: "tok-a" as string | null, ready: true } }));
const rerenderRef = vi.hoisted(() => ({ current: null as null | (() => void) }));
vi.mock("@/lib/api", () => api);

// 模拟既有身份钩子：令牌状态由用例控制，refresh 重新收敛一次
// （真实实现会重新读 localStorage），页面只消费 token/ready/refresh。
vi.mock("@/hooks/useLocalMemberIdentity", () => ({
  useLocalMemberIdentity: () => ({
    token: identity.state.token ?? window.localStorage.getItem("ai-video-diary-local-member-token"),
    ready: identity.state.ready,
    role: null,
    error: null,
    refresh: () => {
      identity.state = {
        token: window.localStorage.getItem("ai-video-diary-local-member-token") ?? identity.state.token,
        ready: true,
      };
      rerenderRef.current?.();
    },
  }),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import { HomeDashboardLive } from "@/components/HomeWorkspace";

const meta = { scope: "caller_visible" as const, source: "creation_task", as_of: "2026-10-04T00:00:00Z" };

function payload(days: 7 | 30, title: string): HomeAggregation {
  return {
    days,
    counts: [{ key: "visible_assets", label: "可见素材", value: days === 7 ? 7 : 30, meta }],
    trend: { days, points: [], meta },
    device_queue: { counts: { "job.ready": 2 }, records_total: 2, running: null, queued: null, meta },
    storage: { directory_state: "ready", bound_to_current_workspace: true, bound_to_another_workspace: false, configured_path_changed: false, capacity: null, meta },
    entries: {
      queue: [{ task_id: "task-a", title, stage: "montage", status: "running", updated_at: "2026-10-04T00:00:00Z" }],
      todos: [], works: [], meta,
    },
    unknown: [],
  };
}

beforeEach(() => {
  api.getHomeDashboard.mockReset();
  api.listCreationTasks.mockResolvedValue({ items: [], total_count: 0 });
  api.getProductionFoundation.mockResolvedValue(null);
  api.getMaterialLibraryInsights.mockResolvedValue(null);
  identity.state = { token: "tok-a", ready: true };
  window.localStorage.clear();
  // 页面会读真实的 localStorage 令牌与钩子身份比对，令牌保持一致。
  window.localStorage.setItem("ai-video-diary-local-member-token", identity.state.token ?? "tok-a");
});

describe("首页真实数据容器", () => {
  it("does not send any request before the identity has converged", async () => {
    identity.state = { token: null, ready: false };
    render(<HomeDashboardLive />);
    await act(async () => { await Promise.resolve(); });
    expect(api.getHomeDashboard).not.toHaveBeenCalled();
    expect(api.listCreationTasks).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("正在读取整机概览…");
  });

  it("clears the old identity's content when another tab stores a new token", async () => {
    api.getHomeDashboard.mockImplementation(async (days: 7 | 30) =>
      payload(days, identity.state.token === "tok-b" ? "令牌 B 的任务" : "令牌 A 的任务"));
    const { rerender } = render(<HomeDashboardLive />);
    rerenderRef.current = () => rerender(<HomeDashboardLive />);
    expect(await screen.findByRole("button", { name: /令牌 A 的任务/ })).toBeInTheDocument();
    // 另一个标签页写入新令牌：既有钩子收敛后，旧身份的私有内容必须清掉并按新身份重读。
    window.localStorage.setItem("ai-video-diary-local-member-token", "tok-b");
    window.dispatchEvent(new StorageEvent("storage", { key: "ai-video-diary-local-member-token" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /令牌 B 的任务/ })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /令牌 A 的任务/ })).not.toBeInTheDocument();
  });

  it("keeps the loaded page when the window regains focus without a member change", async () => {
    api.getHomeDashboard.mockImplementation(async (days: 7 | 30) => payload(days, "同身份任务"));
    render(<HomeDashboardLive />);
    expect(await screen.findByRole("button", { name: /同身份任务/ })).toBeInTheDocument();
    // 令牌没变：切回窗口不能把首页清空后停住，也不能重复读取。
    window.dispatchEvent(new Event("focus"));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("button", { name: /同身份任务/ })).toBeInTheDocument();
    expect(api.getHomeDashboard).toHaveBeenCalledTimes(1);
  });

  it("drops a late response that belongs to the previous identity", async () => {
    let releaseFirst: ((value: HomeAggregation) => void) | undefined;
    api.getHomeDashboard
      .mockImplementationOnce(() => new Promise<HomeAggregation>((resolve) => { releaseFirst = resolve; }))
      .mockImplementation(async (days: 7 | 30) => payload(days, "新身份任务"));
    const { rerender } = render(<HomeDashboardLive />);
    // 切成员：旧身份的请求仍在途。
    identity.state = { token: "tok-b", ready: true };
    rerenderRef.current = () => rerender(<HomeDashboardLive />);
    rerender(<HomeDashboardLive />);
    await waitFor(() => expect(screen.getByRole("button", { name: /新身份任务/ })).toBeInTheDocument());
    // 旧身份迟到响应不得把旧标题提交上来。
    releaseFirst?.(payload(7, "旧身份任务"));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.queryByText("旧身份任务")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新身份任务/ })).toBeInTheDocument();
  });

  it("retries with the currently selected range instead of the first one", async () => {
    api.getHomeDashboard.mockImplementation(async (days: 7 | 30) => payload(days, `${days} 天任务`));
    render(<HomeDashboardLive />);
    await waitFor(() => expect(screen.getByRole("button", { name: /7 天任务/ })).toBeInTheDocument());
    // 切到 30 天后读取失败：重试必须用 30 天，不能退回首次的 7 天。
    api.getHomeDashboard.mockRejectedValueOnce(new Error("读取失败：503"));
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("读取失败：503");
    api.getHomeDashboard.mockImplementation(async (days: 7 | 30) => payload(days, `${days} 天任务`));
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(api.getHomeDashboard).toHaveBeenLastCalledWith(30));
    expect(await screen.findByRole("button", { name: /30 天任务/ })).toBeInTheDocument();
  });
});
