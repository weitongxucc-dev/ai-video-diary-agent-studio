import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode, AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ getRuntimeHealth: vi.fn() }));
vi.mock("@/lib/api.server", () => api);
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => <a {...props}>{children}</a> }));
import HealthPage from "../page";
beforeEach(() => { api.getRuntimeHealth.mockReset(); });
describe("health status evidence", () => {
  it("shows the actual recent service observation, and links to its settings", async () => {
    api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "deepseek", label: "AI 文案模型", status: "ready", reason: "最近一次文字服务调用成功。", impact: "用于写稿。", repair_action: "真实调用时验证。", last_checked_at: "2026-10-09T04:00:00Z" }] });
    const { container } = render(await HealthPage());
    expect(screen.getByText("最近调用成功")).toBeInTheDocument();
    expect(screen.queryByText("可使用")).not.toBeInTheDocument();

    expect(screen.getByRole("link", { name: "调整对话与文字服务设置" })).toHaveAttribute("href", "/settings?section=model");
    expect(container.querySelector("time")).toHaveAttribute("datetime", "2026-10-09T04:00:00Z");
  });
  it("retains the API diagnostic rather than masking a missing configuration", async () => {
    api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "deepseek", label: "对话与文字模型", status: "degraded", reason: "未配置外部文字模型", impact: "暂时不能写稿", repair_action: "检查设置", last_checked_at: "2026-10-09T04:00:00Z" }] });
    render(await HealthPage());
    expect(screen.getByText("未配置外部文字模型")).toBeInTheDocument();
    expect(screen.getByText("受限制")).toBeInTheDocument();
  });
});

it("shows compact check rows without claiming device resources or video generation are ready", async () => {
  api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "ffmpeg", label: "ffmpeg", status: "ready", reason: "命令可用。", impact: "影响视频编码。", repair_action: "无需处理。", last_checked_at: "2026-10-09T04:00:00Z" }] });
  const { container } = render(await HealthPage());
  expect(screen.getByText("视频编码工具")).toBeVisible();
  expect(screen.getByText("程序路径检查")).toBeVisible();
  expect(screen.getByText("检查通过")).toBeVisible();
  expect(screen.getByText(/生成仍需通过模型和设备资源检查/)).toBeVisible();
  expect(screen.queryByText("视频制作")).toBeNull();
  expect(screen.queryByText("生成已备妥")).toBeNull();
  const item = screen.getByRole("list", { name: "本机检查项" }).querySelector("details")!;
  expect(item).not.toHaveAttribute("open");
  expect(container.querySelectorAll('li')).toHaveLength(1);
  expect(screen.queryByRole("link", { name: "调整视频编码工具设置" })).toBeNull();
});
it("keeps a technical failure and its exact repair record behind the affected row", async () => {
  api.getRuntimeHealth.mockResolvedValue({ items: [{ id: "pi_node", label: "对话运行环境 Node.js", status: "missing", reason: "spawn node ENOENT", impact: "对话助手暂时无法运行。", repair_action: "安装 Node.js 22.19 或更高版本，然后 node --version。", last_checked_at: "2026-10-09T04:00:00Z" }] });
  render(await HealthPage());
  expect(screen.getByText("未备好")).toBeVisible();
  expect(screen.getByText("spawn node ENOENT")).not.toBeVisible();
  const item = screen.getByRole("list", { name: "本机检查项" }).querySelector("details")!;
  fireEvent.click(item.querySelector("summary")!);
  expect(screen.getByText("更新对话运行环境，重新打开应用后检查。")).toBeVisible();
  expect(screen.getByText("spawn node ENOENT")).not.toBeVisible();
  fireEvent.click(within(item).getByText("原始检查详情"));
  expect(screen.getByText("spawn node ENOENT")).toBeVisible();
  expect(screen.getByText(/node --version/)).toBeVisible();
  expect(item.querySelector("time")).toHaveAttribute("datetime", "2026-10-09T04:00:00Z");
});
it("reports an unavailable service without leftover successful rows", async () => {
  api.getRuntimeHealth.mockRejectedValue(new Error("fixture offline"));
  render(await HealthPage());
  expect(screen.getByRole("alert")).toHaveTextContent("暂时连不上本机服务");
  expect(screen.queryByRole("list", { name: "本机检查项" })).toBeNull();
  expect(screen.getByRole("link", { name: "返回运行与诊断" })).toHaveAttribute("href", "/settings?section=runtime");
});
