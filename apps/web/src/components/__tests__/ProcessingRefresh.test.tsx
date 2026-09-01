import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProcessingRefresh } from "@/components/ProcessingRefresh";

describe("ProcessingRefresh", () => {
  afterEach(() => vi.useRealTimers());

  it("schedules one full document reload without a router refresh", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    render(<ProcessingRefresh reload={reload} />);

    act(() => vi.advanceTimersByTime(4999));
    expect(reload).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(reload).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(5000));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
