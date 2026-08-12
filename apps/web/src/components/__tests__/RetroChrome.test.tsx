import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RetroChrome } from "@/components/RetroChrome";

const mocks = vi.hoisted(() => ({
  back: vi.fn(),
  toggleHelpPanel: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ back: mocks.back }),
}));

vi.mock("@/lib/helpPanel", () => ({ toggleHelpPanel: mocks.toggleHelpPanel }));

describe("RetroChrome", () => {
  it("shows a user-facing workspace location without development addresses", () => {
    render(<RetroChrome contextLabel="剪辑计划" todayLabel="2026/08/12周三" />);
    expect(screen.getByLabelText("当前位置")).toHaveTextContent("剪辑计划");
    expect(screen.getByRole("status", { name: "工作区状态" })).toHaveTextContent(
      "工作区状态未知",
    );
    expect(screen.queryByText("本地工作区已连接")).not.toBeInTheDocument();
    expect(screen.queryByText(/localhost|127\.0\.0\.1/)).not.toBeInTheDocument();
  });

  it("renders only the workspace status supplied by the owner", () => {
    render(
      <RetroChrome
        contextLabel="剪辑计划"
        todayLabel="2026/08/12周三"
        workspaceStatus={{ kind: "connected", label: "素材目录已连接" }}
      />,
    );
    expect(screen.getByRole("status", { name: "工作区状态" })).toHaveTextContent(
      "素材目录已连接",
    );
    expect(screen.getByRole("status", { name: "工作区状态" })).toHaveClass(
      "system-title-status--connected",
    );
  });

  it("contains only actionable menu and toolbar controls", () => {
    render(<RetroChrome contextLabel="今日工作台" todayLabel="2026/08/12周三" />);
    expect(document.querySelectorAll(":disabled")).toHaveLength(0);
    expect(screen.getByRole("navigation", { name: "应用菜单" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "帮助" }));
    expect(mocks.toggleHelpPanel).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(mocks.back).toHaveBeenCalledOnce();
  });
});
