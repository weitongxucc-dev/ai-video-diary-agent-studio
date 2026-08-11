import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Skeleton, LoadingShell, MetricSkeletonRow, ListSkeleton, SidebarSkeletonCards } from "../../components/Skeleton";

describe("Skeleton", () => {
  it("renders with default block variant", () => {
    const { container } = render(<Skeleton />);
    const span = container.querySelector("span");
    expect(span).toBeInTheDocument();
    expect(span?.className).toContain("skeleton-block");
  });

  it("renders with text variant", () => {
    const { container } = render(<Skeleton variant="text" />);
    const span = container.querySelector("span");
    expect(span?.className).toContain("skeleton-text");
  });

  it("renders with circle variant", () => {
    const { container } = render(<Skeleton variant="circle" />);
    const span = container.querySelector("span");
    expect(span?.className).toContain("skeleton-circle");
  });

  it("applies width and height as inline styles", () => {
    const { container } = render(<Skeleton width={200} height={40} />);
    const span = container.querySelector("span") as HTMLSpanElement;
    expect(span.style.width).toBe("200px");
    expect(span.style.height).toBe("40px");
  });

  it("has aria-hidden=true by default", () => {
    const { container } = render(<Skeleton />);
    const span = container.querySelector("span");
    expect(span?.getAttribute("aria-hidden")).toBe("true");
  });

  it("exposes an accessible label when ariaLabel is set", () => {
    render(<Skeleton ariaLabel="正在加载图表..." />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "正在加载图表...",
    );
  });

  it("merges custom className", () => {
    const { container } = render(<Skeleton className="my-custom" />);
    const span = container.querySelector("span");
    expect(span?.className).toContain("my-custom");
  });
});

describe("LoadingShell", () => {
  it("renders default label", () => {
    render(<LoadingShell />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
    const shell = document.querySelector('[aria-busy="true"]');
    expect(shell).toBeInTheDocument();
    expect(shell?.getAttribute("aria-live")).toBe("polite");
  });

  it("renders custom label", () => {
    render(<LoadingShell label="正在初始化..." />);
    expect(screen.getByText("正在初始化...")).toBeInTheDocument();
  });

  it("renders children inside the shell", () => {
    render(
      <LoadingShell>
        <div data-testid="child">测试内容</div>
      </LoadingShell>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });
});

describe("MetricSkeletonRow", () => {
  it("renders default count of 4 skeleton cards", () => {
    render(<MetricSkeletonRow />);
    const cards = document.querySelectorAll(".loading-metric-card");
    expect(cards).toHaveLength(4);
  });

  it("renders custom count", () => {
    render(<MetricSkeletonRow count={6} />);
    const cards = document.querySelectorAll(".loading-metric-card");
    expect(cards).toHaveLength(6);
  });
});

describe("ListSkeleton", () => {
  it("renders default count of 4 list items", () => {
    render(<ListSkeleton />);
    const items = document.querySelectorAll(".loading-list-item");
    expect(items).toHaveLength(4);
  });

  it("applies custom itemClassName", () => {
    render(<ListSkeleton itemClassName="compact" />);
    const item = document.querySelector(".loading-list-item");
    expect(item?.className).toContain("compact");
  });
});

describe("SidebarSkeletonCards", () => {
  it("renders 3 sidebar cards by default", () => {
    render(<SidebarSkeletonCards />);
    const cards = document.querySelectorAll(".loading-side-card");
    expect(cards).toHaveLength(3);
  });
});
