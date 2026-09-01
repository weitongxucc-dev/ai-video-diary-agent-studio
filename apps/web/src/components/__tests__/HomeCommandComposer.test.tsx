import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HomeCommandComposer } from "@/components/HomeCommandComposer";

describe("HomeCommandComposer", () => {
  it("carries a goal to the real preflight route without making a homepage request", () => {
    const fetch = vi.fn();
    const assignLocation = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<HomeCommandComposer assignLocation={assignLocation} />);

    expect(screen.getByRole("button", { name: "检查可用内容" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("输入目标后，系统会先检查素材、声音和模型");

    fireEvent.change(screen.getByRole("textbox", { name: "制作指令" }), {
      target: { value: "用已审核素材制作一条竖版产品视频" },
    });
    fireEvent.submit(screen.getByRole("button", { name: "检查可用内容" }).closest("form")!);

    expect(fetch).not.toHaveBeenCalled();
    expect(assignLocation).toHaveBeenCalledWith("/production?new=1&goal=%E7%94%A8%E5%B7%B2%E5%AE%A1%E6%A0%B8%E7%B4%A0%E6%9D%90%E5%88%B6%E4%BD%9C%E4%B8%80%E6%9D%A1%E7%AB%96%E7%89%88%E4%BA%A7%E5%93%81%E8%A7%86%E9%A2%91");
  });
});
