import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CardSelect, HomeCardDialog, homeCardParentForTarget } from "../HomeCardDialog";
import { CommandPalette } from "../CommandPalette";

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

describe("v5.3 object card stack", () => {
  it("focuses the real navigation input when its portal mounts on first open", async () => {
    function SearchConsumer() {
      const [open, setOpen] = useState(false);
      return <>
        <button onClick={() => setOpen(true)} type="button">搜索入口</button>
        <CommandPalette items={[]} onClose={() => setOpen(false)} onNavigate={vi.fn()} open={open} />
      </>;
    }
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
    const view = render(<SearchConsumer />);
    try {
      const trigger = screen.getByRole("button", { name: "搜索入口" });
      trigger.focus(); fireEvent.click(trigger);
      const input = await screen.findByRole("textbox", { name: "搜索页面和创作任务" });
      expect(input).toHaveFocus();
    } finally {
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("keeps toolbar search at the root but attaches a shortcut to its actual owning card", () => {
    function SearchOwnership() {
      const [open, setOpen] = useState(false);
      const [parentId, setParentId] = useState<string | undefined>();
      const [selectionOpen, setSelectionOpen] = useState(false);
      return <>
        <button type="button" onClick={(event) => { setParentId(homeCardParentForTarget(event.currentTarget)); setOpen(true); }}>顶栏搜索</button>
        <HomeCardDialog kind="detail" title="父卡" onClose={() => undefined}>
          <button type="button" onClick={(event) => { setParentId(homeCardParentForTarget(event.currentTarget)); setOpen(true); }}>卡内搜索</button>
          <button type="button" onClick={() => setSelectionOpen(true)}>打开短菜单</button>
          {selectionOpen ? <HomeCardDialog kind="selection" title="短菜单" onClose={() => setSelectionOpen(false)}><button type="button" onClick={(event) => { setParentId(homeCardParentForTarget(event.currentTarget)); setOpen(true); }}>短菜单内搜索</button></HomeCardDialog> : null}
        </HomeCardDialog>
        <CommandPalette items={[]} onClose={() => setOpen(false)} onNavigate={vi.fn()} open={open} parentLayerId={parentId} />
      </>;
    }
    render(<SearchOwnership />);
    fireEvent.click(screen.getByRole("button", { name: "顶栏搜索" }));
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toHaveStyle({ zIndex: "111" });
    fireEvent.click(screen.getAllByRole("button", { name: "关闭卡片" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "卡内搜索" }));
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toHaveStyle({ zIndex: "90" });
    fireEvent.click(screen.getAllByRole("button", { name: "关闭卡片" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "打开短菜单" }));
    fireEvent.click(screen.getByRole("button", { name: "短菜单内搜索" }));
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toHaveStyle({ zIndex: "90" });
    expect(screen.queryByRole("dialog", { name: "短菜单" })).not.toBeInTheDocument();
  });

  it("hydrates a server-rendered workspace with a closed card before enabling its client portal", async () => {
    function Workspace() {
      const [open, setOpen] = useState(false);
      return <main>
        <button onClick={() => setOpen(true)}>查看历史</button>
        <HomeCardDialog open={open} kind="detail" title="历史版本" onClose={() => setOpen(false)}>
          <input aria-label="历史草稿" defaultValue="旧版说明" />
        </HomeCardDialog>
        <div aria-label="剪辑工作区"><video aria-label="当前播放器" /></div>
      </main>;
    }
    let html: string;
    try {
      vi.stubGlobal("document", undefined);
      vi.stubGlobal("window", undefined);
      html = renderToString(<Workspace />);
    } finally {
      vi.unstubAllGlobals();
    }
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const stage = within(container).getByLabelText("剪辑工作区");
    const player = within(container).getByLabelText("当前播放器") as HTMLVideoElement;
    player.currentTime = 1.25;
    const errors: unknown[] = [];
    let root: Root | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, <Workspace />, { onRecoverableError: error => errors.push(error) });
      });
      expect(errors).toEqual([]);
      expect(within(container).getByLabelText("剪辑工作区")).toBe(stage);
      expect(within(container).getByLabelText("当前播放器")).toBe(player);
      expect(player.currentTime).toBe(1.25);
      const trigger = within(container).getByRole("button", { name: "查看历史" });
      trigger.focus(); fireEvent.click(trigger);
      const card = await screen.findByRole("dialog", { name: "历史版本" });
      expect(container).not.toContainElement(card);
      const body = screen.getByLabelText("历史草稿").parentElement!;
      body.scrollTop = 120;
      fireEvent.change(screen.getByLabelText("历史草稿"), { target: { value: "保留历史选择草稿" } });
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "历史版本" })).not.toBeInTheDocument());
      await waitFor(() => expect(trigger).toHaveFocus());
      fireEvent.click(trigger);
      expect(await screen.findByRole("dialog", { name: "历史版本" })).toBe(card);
      expect(screen.getByLabelText("历史草稿")).toHaveValue("保留历史选择草稿");
      expect(body.scrollTop).toBe(120);
      expect(within(container).getByLabelText("当前播放器")).toBe(player);
      expect(player.currentTime).toBe(1.25);
      expect(errors).toEqual([]);
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
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
    expect(parent).toHaveStyle({width:"360px",height:"560px"});
    const child = screen.getByRole("dialog", {name:"版本配置"});
    expect(child).toHaveStyle({width:"360px",height:"560px"});
    expect(Number.parseFloat(child.style.left)).toBe(Number.parseFloat(parent.style.left) - 12);
    expect(Number.parseFloat(child.style.top)).toBe(Number.parseFloat(parent.style.top) + 32);
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
  it("scopes ordinary confirmation inert/focus and modal semantics to its parent", () => {
    render(<Cards />); const parent = screen.getByRole("dialog", {name:"资料详情"}); fireEvent.click(screen.getByRole("button", {name:"应用参数"}));
    const confirmation = screen.getByRole("dialog", {name:"确认对象参数"});
    expect(confirmation).not.toHaveAttribute("aria-modal"); expect(parent.inert).toBe(true);
    expect(confirmation.parentElement).toHaveStyle({ background: "transparent", pointerEvents: "none" });
    const parentShade = confirmation.parentElement?.querySelector('[class*="localShade"]') as HTMLElement;
    expect(parentShade.style.left).toBe(parent.style.left);
    expect(parentShade.style.top).toBe(parent.style.top);
    expect(parentShade.style.width).toBe(parent.style.width);
    expect(parentShade.style.height).toBe(parent.style.height);
    expect(screen.getByRole("button", {name:"返回详情"})).toHaveFocus();
    const last = within(confirmation).getByRole("button", {name:"确认应用"}); last.focus(); fireEvent.keyDown(last, {key:"Tab"});
    expect(within(confirmation).getByRole("button", {name:"关闭卡片"})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, {key:"Escape"}); expect(parent.inert).toBe(false);
  });
  it("keeps the real modal and its short selection in the shared right-side geometry", () => {
    const close = vi.fn(); render(<HomeCardDialog title="重要确认" onClose={close}><CardSelect label="范围" value="one" onChange={vi.fn()} options={[{value:"one",label:"仅此对象"}]} /></HomeCardDialog>);
    fireEvent.click(screen.getByRole("button", {name:"范围"}));
    expect(screen.getByRole("dialog", {name:"重要确认"})).toHaveStyle({width:"360px",height:"560px",zIndex:"111"});
    expect(screen.getByRole("dialog", {name:"范围"})).toHaveStyle({zIndex:"121"});
    fireEvent.keyDown(document.activeElement!, {key:"Escape"}); expect(close).not.toHaveBeenCalled();
  });
  it("repositions fields inside the viewport on resize", () => {
    render(<Cards />); fireEvent.click(screen.getByRole("button", {name:"版本与出处"})); fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    act(() => window.dispatchEvent(new Event("resize")));
    expect(Number.parseFloat(screen.getByRole("dialog", {name:"查看版本"}).style.left)).toBeGreaterThanOrEqual(12);
  });
});
