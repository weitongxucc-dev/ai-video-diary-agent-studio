import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CardSelect, HomeCardDialog } from "../HomeCardDialog";

function Cards() {
  const [open, setOpen] = useState(true);
  const [child, setChild] = useState<"config" | "confirmation" | null>(null);
  const [value, setValue] = useState("one");
  return <><button onClick={() => setOpen(true)}>恢复详情</button><HomeCardDialog open={open} kind="detail" title="资料详情" onClose={() => setOpen(false)}>
    <input aria-label="未应用草稿" defaultValue="正文草稿" />
    <button onClick={() => setChild("config")}>版本与出处</button>
    <button onClick={() => setChild("confirmation")}>应用参数</button>
    {child ? <HomeCardDialog kind={child} title={child === "config" ? "版本配置" : "确认对象参数"} onClose={() => setChild(null)}>
      {child === "config" ? <CardSelect label="查看版本" value={value} onChange={setValue} options={[{value:"one",label:"版本1"},{value:"two",label:"版本2"}]} /> : <><button data-card-cancel onClick={() => setChild(null)}>返回详情</button><button>确认应用</button></>}
    </HomeCardDialog> : null}
  </HomeCardDialog></>;
}

describe("v5.1 object card stack", () => {
  it("keeps draft/scroll mounted and returns focus one layer per Escape", () => {
    render(<Cards />);
    const parent = screen.getByRole("dialog", {name:"资料详情"});
    const body = parent.querySelector('[class*="body"]') as HTMLElement;
    body.scrollTop = 120;
    fireEvent.change(screen.getByLabelText("未应用草稿"), {target:{value:"保留这段草稿"}});
    screen.getByRole("button", {name:"版本与出处"}).focus();
    fireEvent.click(screen.getByRole("button", {name:"版本与出处"}));
    const field = screen.getByRole("button", {name:"查看版本"}); field.focus(); fireEvent.click(field);
    expect(screen.getByRole("dialog", {name:"资料详情"})).toBe(parent);
    expect(parent).toHaveStyle({width:"360px"});
    expect(screen.getByRole("dialog", {name:"版本配置"})).toHaveStyle({width:"320px"});
    expect(screen.getByRole("dialog", {name:"查看版本"})).toHaveStyle({width:"240px",zIndex:"100"});
    fireEvent.keyDown(document.activeElement!, {key:"Escape"});
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(); expect(field).toHaveFocus();
    fireEvent.keyDown(field, {key:"Escape"});
    expect(screen.queryByRole("dialog", {name:"版本配置"})).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name:"版本与出处"})).toHaveFocus();
    expect(screen.getByLabelText("未应用草稿")).toHaveValue("保留这段草稿"); expect(body.scrollTop).toBe(120);
    fireEvent.keyDown(document.activeElement!, {key:"Escape"});
    expect(screen.queryByRole("dialog", {name:"资料详情"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"恢复详情"}));
    expect(screen.getByRole("dialog", {name:"资料详情"})).toBe(parent);
    expect(screen.getByLabelText("未应用草稿")).toHaveValue("保留这段草稿"); expect(body.scrollTop).toBe(120);
  });
  it("consumes the first outside click and closes only the selection", () => {
    render(<Cards />); fireEvent.click(screen.getByRole("button", {name:"版本与出处"})); fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    const parentAction = screen.getByRole("button", {name:"应用参数"});
    fireEvent.pointerDown(parentAction); fireEvent.click(parentAction);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", {name:"版本配置"})).toBeInTheDocument();
    expect(screen.queryByRole("dialog", {name:"确认对象参数"})).not.toBeInTheDocument();
  });
  it("closes a selection on its owner's scroll without changing its value", () => {
    render(<Cards />); fireEvent.click(screen.getByRole("button", {name:"版本与出处"})); fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    fireEvent.click(screen.getByRole("option", {name:"版本2"}));
    fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    fireEvent.scroll(screen.getByRole("dialog", {name:"版本配置"}).querySelector('[class*="body"]')!);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument(); expect(screen.getByRole("button", {name:"查看版本"})).toHaveTextContent("版本2");
  });
  it("scopes ordinary confirmation inert/focus to its parent, not the application", () => {
    render(<Cards />); const parent = screen.getByRole("dialog", {name:"资料详情"}); fireEvent.click(screen.getByRole("button", {name:"应用参数"}));
    const confirmation = screen.getByRole("dialog", {name:"确认对象参数"});
    expect(confirmation).not.toHaveAttribute("aria-modal"); expect(parent.inert).toBe(true);
    expect(screen.getByRole("button", {name:"返回详情"})).toHaveFocus();
    const last = within(confirmation).getByRole("button", {name:"确认应用"}); last.focus(); fireEvent.keyDown(last, {key:"Tab"});
    expect(within(confirmation).getByRole("button", {name:"关闭卡片"})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, {key:"Escape"}); expect(parent.inert).toBe(false);
  });
  it("keeps a real modal's selection above its 480px global parent", () => {
    const close = vi.fn(); render(<HomeCardDialog title="重要确认" onClose={close}><CardSelect label="范围" value="one" onChange={vi.fn()} options={[{value:"one",label:"仅此对象"}]} /></HomeCardDialog>);
    fireEvent.click(screen.getByRole("button", {name:"范围"}));
    expect(screen.getByRole("dialog", {name:"重要确认"})).toHaveStyle({width:"480px",zIndex:"111"});
    expect(screen.getByRole("dialog", {name:"范围"})).toHaveStyle({zIndex:"121"});
    fireEvent.keyDown(document.activeElement!, {key:"Escape"}); expect(close).not.toHaveBeenCalled();
  });
  it("repositions fields inside the viewport on resize", () => {
    render(<Cards />); fireEvent.click(screen.getByRole("button", {name:"版本与出处"})); fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    act(() => window.dispatchEvent(new Event("resize")));
    expect(Number.parseFloat(screen.getByRole("dialog", {name:"查看版本"}).style.left)).toBeGreaterThanOrEqual(12);
  });
});
