import { render, screen } from "@testing-library/react";
import type { ReactNode, AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ getRuntimeHealth: vi.fn() }));
vi.mock("@/lib/api.server", () => api);
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props}>{children}</a> }));
import HealthPage from "../page";
beforeEach(() => api.getRuntimeHealth.mockReset());
describe("health status evidence", () => {
  it("does not treat a configuration check as a successful call, and links to its settings", async () => {
    api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "deepseek", label: "AI 文案模型", status: "ready", reason: "已配置；实际生成尚未验证。", impact: "用于写稿。", repair_action: "真实调用时验证。", last_checked_at: "2026-10-09T04:00:00Z" }] });
    const { container } = render(await HealthPage());
    expect(screen.getByText("已配置·待验证")).toBeInTheDocument();
    expect(screen.queryByText("可使用")).not.toBeInTheDocument();
    expect(screen.queryByText("最近调用成功")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "调整相关设置" })).toHaveAttribute("href", "/settings?section=model");
    expect(container.querySelector("time")).toHaveAttribute("datetime", "2026-10-09T04:00:00Z");
  });
  it("retains the API diagnostic rather than masking a missing configuration", async () => {
    api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "deepseek", label: "对话与文字模型", status: "degraded", reason: "未配置外部文字模型", impact: "暂时不能写稿", repair_action: "检查设置", last_checked_at: "2026-10-09T04:00:00Z" }] });
    render(await HealthPage());
    expect(screen.getByText("未配置外部文字模型")).toBeInTheDocument();
    expect(screen.getByText("受限制")).toBeInTheDocument();
  });
});
