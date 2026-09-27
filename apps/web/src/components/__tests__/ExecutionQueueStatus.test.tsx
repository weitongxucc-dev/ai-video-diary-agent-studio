import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExecutionQueueStatus } from "../ExecutionQueueStatus";

describe("ExecutionQueueStatus", () => {
  it("leaves the preview state empty before a job is scheduled", () => {
    const { container } = render(<ExecutionQueueStatus status={null} currentStage={null} waitingReason={null} statusUpdatedAt={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows unknown queue facts without inventing a stage, reason, or ETA", () => {
    render(<ExecutionQueueStatus status="queued" currentStage={null} waitingReason={null} statusUpdatedAt={null} />);
    const status = screen.getByLabelText("制作安排状态");
    expect(within(status).getByText("尚未开始")).toBeInTheDocument();
    expect(within(status).getByText("暂未提供等待原因")).toBeInTheDocument();
    expect(within(status).getByText("更新时间未知")).toBeInTheDocument();
    expect(status).not.toHaveTextContent(/预计|倒计时/);
  });

  it("shows a persisted running stage and its update time", () => {
    render(<ExecutionQueueStatus status="running" currentStage="montage_plan" waitingReason={null} statusUpdatedAt="2026-09-27T08:30:00Z" />);
    const status = screen.getByLabelText("制作安排状态");
    expect(within(status).getByText("安排镜头与人物口型")).toBeInTheDocument();
    expect(status.querySelector("time")?.getAttribute("datetime")).toBe("2026-09-27T08:30:00Z");
    expect(within(status).queryByText("等待原因")).not.toBeInTheDocument();
  });

  it("shows a real queue reason and the last failed stage", () => {
    const { rerender } = render(<ExecutionQueueStatus status="queued" currentStage="render" waitingReason="等待设备空闲" statusUpdatedAt="2026-09-27T08:30:00Z" />);
    expect(screen.getByText("等待设备空闲")).toBeInTheDocument();
    rerender(<ExecutionQueueStatus status="failed" currentStage="render" waitingReason={null} statusUpdatedAt={null} />);
    expect(screen.getByText("生成成片和字幕")).toBeInTheDocument();
    expect(screen.queryByText("等待设备空闲")).not.toBeInTheDocument();
  });
});
