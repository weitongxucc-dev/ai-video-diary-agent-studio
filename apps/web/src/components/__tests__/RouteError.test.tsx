import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { RouteError, sanitizeMessage } from "../../components/RouteError";

// Mock next/link
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode;
    href: string;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

// Mock PixelIcon
vi.mock("@/components/PixelIcon", () => ({
  PixelIcon: ({ name }: { name: string }) => (
    <span data-testid={`icon-${name}`} />
  ),
}));

describe("sanitizeMessage", () => {
  it("returns null for undefined or empty string", () => {
    expect(sanitizeMessage(undefined)).toBeNull();
    expect(sanitizeMessage("")).toBeNull();
    expect(sanitizeMessage("   ")).toBeNull();
  });

  it("truncates messages longer than 240 characters", () => {
    const long = "x".repeat(241);
    const result = sanitizeMessage(long);
    expect(result).not.toBeNull();
    expect(result).toBe("x".repeat(237) + "...");
  });

  it("returns trimmed message for valid input", () => {
    expect(sanitizeMessage("  请求失败  ")).toBe("请求失败");
  });

  it("returns null for messages matching stack trace pattern", () => {
    expect(sanitizeMessage("Error: fail\n  at Object.<anonymous>")).toBeNull();
  });

  it("returns null for messages matching file path pattern", () => {
    expect(sanitizeMessage("/home/user/src/app/page.tsx")).toBeNull();
  });

  it("returns null for messages matching secret pattern", () => {
    expect(sanitizeMessage("api_key=sk-abc123")).toBeNull();
  });

  it("returns null for messages containing 'password'", () => {
    expect(sanitizeMessage("password is wrong")).toBeNull();
  });
});

describe("RouteError", () => {
  const error = new Error("网络请求失败，请检查连接");
  const reset = vi.fn();

  it("renders error heading", () => {
    render(<RouteError error={error} reset={reset} />);
    expect(screen.getByRole("heading")).toHaveTextContent("出错了");
  });

  it("renders custom title", () => {
    render(
      <RouteError error={error} reset={reset} title="视频处理失败" />,
    );
    expect(screen.getByRole("heading")).toHaveTextContent("视频处理失败");
  });

  it("renders sanitized error message", () => {
    render(<RouteError error={error} reset={reset} />);
    expect(screen.getByText("网络请求失败，请检查连接")).toBeInTheDocument();
  });

  it("shows fallback text when error message is sanitized away", () => {
    const secretError = new Error("password is wrong");
    render(<RouteError error={secretError} reset={reset} />);
    expect(
      screen.getByText("该页面加载失败，未返回可展示的错误详情。"),
    ).toBeInTheDocument();
  });

  it("renders custom hint", () => {
    render(
      <RouteError
        error={error}
        reset={reset}
        hint="请确认 .env 配置正确"
      />,
    );
    expect(screen.getByText("请确认 .env 配置正确")).toBeInTheDocument();
  });

  it("renders custom detail", () => {
    render(
      <RouteError
        error={error}
        reset={reset}
        detail="Status: 500"
      />,
    );
    expect(screen.getByText("Status: 500")).toBeInTheDocument();
  });

  it("shows error digest when available", () => {
    const errorWithDigest = Object.assign(new Error("fail"), {
      digest: "abc123",
    });
    render(<RouteError error={errorWithDigest} reset={reset} />);
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("renders retry button that calls reset on click", () => {
    const resetFn = vi.fn();
    render(<RouteError error={error} reset={resetFn} />);
    screen.getByText("重试").click();
    expect(resetFn).toHaveBeenCalledTimes(1);
  });

  it("renders link back to workspace", () => {
    render(<RouteError error={error} reset={reset} />);
    const link = screen.getByText("返回工作台");
    expect(link.closest("a")).toHaveAttribute("href", "/");
  });
});
