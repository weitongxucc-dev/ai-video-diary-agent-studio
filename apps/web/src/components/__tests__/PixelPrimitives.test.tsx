import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  PixelBlockedState,
  PixelButton,
  PixelEmptyState,
  PixelErrorState,
  PixelInput,
  PixelLoadingState,
  PixelPanel,
  PixelScrollableRegion,
  PixelStatus,
} from "@/components/PixelPrimitives";

describe("PixelPrimitives", () => {
  it("renders a restrained panel with one heading", () => {
    render(
      <PixelPanel description="辅助说明" title="当前任务">
        <p>主内容</p>
      </PixelPanel>,
    );
    const heading = screen.getByRole("heading", { name: "当前任务" });
    expect(heading).toBeInTheDocument();
    expect(heading.closest("section")).toHaveAttribute(
      "aria-labelledby",
      heading.id,
    );
    expect(screen.getByText("辅助说明")).toBeInTheDocument();
  });

  it("allows the panel heading id to be controlled", () => {
    render(
      <PixelPanel headingId="task-heading" title="当前任务">
        <p>主内容</p>
      </PixelPanel>,
    );
    expect(screen.getByRole("heading", { name: "当前任务" })).toHaveAttribute(
      "id",
      "task-heading",
    );
    expect(screen.getByRole("region", { name: "当前任务" })).toBeInTheDocument();
  });

  it("exposes loading and disabled button state", () => {
    render(<PixelButton loading>正在保存</PixelButton>);
    const button = screen.getByRole("button", { name: "正在保存" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toHaveClass(
      "pixel-control-button",
      "pixel-control-button--secondary",
    );
    expect(button).not.toHaveClass("pixel-button");
  });

  it("keeps the input label visible and associates its error", () => {
    render(<PixelInput error="目录不可访问" label="视频目录" />);
    const input = screen.getByRole("textbox", { name: "视频目录" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("目录不可访问");
  });

  it.each([
    ["idle", "待处理"],
    ["running", "进行中"],
    ["waiting_for_user", "等待确认"],
    ["succeeded", "已完成"],
    ["failed", "失败"],
    ["blocked", "已阻断"],
  ] as const)("renders %s with a text label", (kind, label) => {
    render(<PixelStatus kind={kind} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("gives empty, loading, blocked and error states explicit semantics", () => {
    render(
      <>
        <PixelEmptyState description="暂无素材" title="等待素材" />
        <PixelLoadingState description="正在读取" title="读取素材" />
        <PixelBlockedState description="权限不足" title="目录阻断" />
        <PixelErrorState description="服务未响应" title="处理失败" />
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("读取素材");
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(screen.getByText("等待素材")).toBeInTheDocument();
  });

  it("runs the state recovery action", () => {
    const onClick = vi.fn();
    render(
      <PixelErrorState
        action={{ kind: "button", label: "重新检查", onClick }}
        description="服务未响应"
        title="处理失败"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "重新检查" })).toHaveClass(
      "pixel-control-button--primary",
    );
  });

  it("renders state links through the controlled action union", () => {
    render(
      <PixelBlockedState
        action={{ kind: "link", label: "检查运行状态", href: "/health" }}
        description="依赖不可用"
        secondaryAction={{
          kind: "button",
          label: "稍后重试",
          onClick: () => undefined,
          tone: "secondary",
        }}
        title="处理被阻断"
      />,
    );
    expect(screen.getByRole("link", { name: "检查运行状态" })).toHaveAttribute(
      "href",
      "/health",
    );
    expect(screen.getByRole("button", { name: "稍后重试" })).toHaveClass(
      "pixel-control-button--secondary",
    );
  });

  it("makes local horizontal overflow labelled and keyboard reachable", () => {
    const { container } = render(
      <PixelScrollableRegion label="生产阶段轨道">
        <div>阶段内容</div>
      </PixelScrollableRegion>,
    );
    const viewport = screen.getByRole("region", { name: "生产阶段轨道" });
    expect(viewport).toHaveAttribute("tabindex", "0");
    expect(viewport).toHaveAttribute("data-scroll-region", "local-x");

    Object.defineProperty(viewport, "clientWidth", { configurable: true, value: 200 });
    Object.defineProperty(viewport, "scrollWidth", { configurable: true, value: 600 });
    Object.defineProperty(viewport, "scrollLeft", { configurable: true, writable: true, value: 400 });
    fireEvent.scroll(viewport);
    expect(container.querySelector(".pixel-scrollable-region")).toHaveAttribute(
      "data-scroll-position",
      "end",
    );
    expect(screen.getByRole("button", { name: "向右滚动生产阶段轨道" })).toBeDisabled();
  });

  it("supports visible controls plus Home and End for horizontal overflow", () => {
    render(
      <PixelScrollableRegion label="生产阶段轨道">
        <div>阶段内容</div>
      </PixelScrollableRegion>,
    );
    const viewport = screen.getByRole("region", { name: "生产阶段轨道" });
    Object.defineProperty(viewport, "clientWidth", { configurable: true, value: 200 });
    Object.defineProperty(viewport, "scrollWidth", { configurable: true, value: 600 });
    Object.defineProperty(viewport, "scrollLeft", { configurable: true, writable: true, value: 0 });
    Object.defineProperty(viewport, "scrollTo", {
      configurable: true,
      value: ({ left }: { left: number }) => {
        viewport.scrollLeft = left;
        fireEvent.scroll(viewport);
      },
    });
    fireEvent(window, new Event("resize"));

    expect(screen.getByText("可横向滚动查看更多")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "向左滚动生产阶段轨道" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "向右滚动生产阶段轨道" }));
    expect(viewport.scrollLeft).toBe(160);

    fireEvent.keyDown(viewport, { key: "End" });
    expect(viewport.scrollLeft).toBe(400);
    fireEvent.keyDown(viewport, { key: "Home" });
    expect(viewport.scrollLeft).toBe(0);
  });
});
