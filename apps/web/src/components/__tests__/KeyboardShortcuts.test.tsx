import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ push: vi.fn(), pushToast: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/PixelToast", () => ({ useToast: () => ({ pushToast: mocks.pushToast }) }));

import { KeyboardShortcuts } from "@/components/KeyboardShortcuts";

describe("saved scheduled definition preview shortcut", () => {
  beforeEach(() => vi.clearAllMocks());

  it("opens the retained preview URL and labels the jump as a definition preview", () => {
    render(<KeyboardShortcuts onOpenSearch={vi.fn()} />);

    fireEvent.keyDown(window, { key: "6", metaKey: true });

    expect(mocks.push).toHaveBeenCalledExactlyOnceWith("/production?view=automations");
    expect(mocks.pushToast).toHaveBeenCalledExactlyOnceWith({
      kind: "info",
      message: "已跳转 · 定时定义预览",
      durationMs: 1800,
    });
  });

  it("keeps the preview shortcut inactive while editing an input", () => {
    render(<><KeyboardShortcuts onOpenSearch={vi.fn()} /><input aria-label="草稿" /></>);

    fireEvent.keyDown(screen.getByRole("textbox", { name: "草稿" }), { key: "6", metaKey: true });

    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.pushToast).not.toHaveBeenCalled();
  });
});
