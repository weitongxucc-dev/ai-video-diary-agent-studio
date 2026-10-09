import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { hydrateRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CardSelect, HomeCardDialog, homeCardParentForTarget } from "../HomeCardDialog";
import { CommandPalette } from "../CommandPalette";
import { HelpPanel } from "../HelpPanel";
import { closeHelpPanel, openHelpPanel } from "@/lib/helpPanel";

const navigation = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.pathname }));

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
  it("omits unused footers and leaves the last reading item in the scrolling body", () => {
    const view = render(<HomeCardDialog kind="detail" title="长阅读" onClose={vi.fn()}><p>正文末尾</p></HomeCardDialog>);
    const card = screen.getByRole("dialog", { name: "长阅读" });
    expect(card.querySelector(":scope > footer")).toBeNull();
    expect(card.querySelector('[class*="body"]')).toContainElement(screen.getByText("正文末尾"));
    view.rerender(<HomeCardDialog kind="detail" title="长阅读" onClose={vi.fn()} footer={<button>保存</button>}><p>正文末尾</p></HomeCardDialog>);
    expect(card.querySelector(":scope > footer")).toContainElement(screen.getByRole("button", { name: "保存" }));
    expect(card.querySelector('[class*="body"]')).not.toContainElement(screen.getByRole("button", { name: "保存" }));
    view.rerender(<HomeCardDialog kind="detail" title="长阅读" onClose={vi.fn()} footer={false}><p>正文末尾</p></HomeCardDialog>);
    expect(card.querySelector(":scope > footer")).toBeNull();
  });

  it.each(["/library", "/knowledge", "/profiles", "/tasks/fixture"])("uses the shell's single help title/close and current content boundary for %s", path => {
    navigation.pathname = path;
    openHelpPanel();
    const view = render(<HelpPanel />);
    try {
      const card = screen.getByRole("dialog", { name: "应用帮助" });
      expect(within(card).getAllByRole("button")).toHaveLength(1);
      expect(within(card).getByRole("heading", { level: 2, name: "应用帮助" })).toBeInTheDocument();
      expect(within(card).getByRole("heading", { level: 3 })).toBeInTheDocument();
      expect(card.querySelector(":scope > footer")).toBeNull();
      expect(card).not.toHaveTextContent(/已审核|授权证据|批准用途|生成授权配音/);
      if (path === "/library") expect(card).toHaveTextContent("默认可用于创作");
      if (path === "/profiles") {
        expect(card).toHaveTextContent("声音可独立登记");
        expect(card).toHaveTextContent("缺少模型只影响生成");
        expect(card).toHaveTextContent("发送范围");
      }
      if (path === "/knowledge") expect(card).toHaveTextContent("外发和发布另行确认");
      if (path === "/tasks/fixture") expect(card).toHaveTextContent("平台发布另行确认");
      fireEvent.click(within(card).getByRole("button", { name: "关闭卡片" }));
      expect(screen.queryByRole("dialog", { name: "应用帮助" })).not.toBeInTheDocument();
    } finally {
      view.unmount();
      closeHelpPanel();
      navigation.pathname = "/";
    }
  });

  it("closes help on Escape and returns focus to its actual trigger", () => {
    closeHelpPanel();
    const view = render(<><button onClick={openHelpPanel}>查看帮助</button><HelpPanel /></>);
    try {
      const trigger = screen.getByRole("button", { name: "查看帮助" });
      trigger.focus(); fireEvent.click(trigger);
      expect(screen.getByRole("dialog", { name: "应用帮助" })).toBeInTheDocument();
      fireEvent.keyDown(document.activeElement!, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "应用帮助" })).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    } finally {
      view.unmount();
      closeHelpPanel();
    }
  });

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
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "关闭卡片" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "卡内搜索" }));
    expect(screen.getByRole("dialog", { name: "父卡" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "关闭卡片" }).at(-1)!);
    fireEvent.click(screen.getByRole("button", { name: "打开短菜单" }));
    fireEvent.click(screen.getByRole("button", { name: "短菜单内搜索" }));
    expect(screen.queryByRole("dialog", { name: "短菜单" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "父卡" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "搜索页面和创作任务" })).toBeInTheDocument();
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
    const child = screen.getByRole("dialog", {name:"版本配置"});
    expect(child).toBeInTheDocument();
    expect(screen.getByRole("dialog", {name:"查看版本"})).toBeInTheDocument();
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
  it("keeps a nested destructive confirmation modal and restores its parent after Escape", () => {
    render(<div className="desktop-shell"><Cards /></div>); const parent = screen.getByRole("dialog", {name:"资料详情"});
    const trigger = screen.getByRole("button", {name:"应用参数"}); trigger.focus(); fireEvent.click(trigger);
    const confirmation = screen.getByRole("dialog", {name:"确认对象参数"});
    expect(confirmation).toHaveAttribute("aria-modal", "true"); expect(parent.inert).toBe(true);
    expect((document.querySelector(".desktop-shell") as HTMLElement).inert).toBe(true);
    expect(screen.getByRole("button", {name:"返回详情"})).toHaveFocus();
    const last = within(confirmation).getByRole("button", {name:"确认应用"}); last.focus(); fireEvent.keyDown(last, {key:"Tab"});
    expect(within(confirmation).getByRole("button", {name:"关闭卡片"})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, {key:"Escape"});
    expect(screen.queryByRole("dialog", {name:"确认对象参数"})).not.toBeInTheDocument();
    expect(parent.inert).toBe(false);
    expect((document.querySelector(".desktop-shell") as HTMLElement).inert).toBe(false);
    expect(trigger).toHaveFocus();
  });
  it("keeps a short selection subordinate to its modal and Escape closes only the selection", () => {
    const close = vi.fn(); render(<HomeCardDialog title="重要确认" onClose={close}><CardSelect label="范围" value="one" onChange={vi.fn()} options={[{value:"one",label:"仅此对象"}]} /></HomeCardDialog>);
    expect(screen.getByRole("dialog", {name:"重要确认"})).toHaveAttribute("aria-modal", "true");
    fireEvent.click(screen.getByRole("button", {name:"范围"}));
    const parent = screen.getByRole("dialog", {name:"重要确认"});
    const menu = screen.getByRole("dialog", {name:"范围"});
    expect(menu).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement!, {key:"Escape"});
    expect(screen.queryByRole("dialog", {name:"范围"})).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", {name:"重要确认"})).toBe(parent);
    expect(close).not.toHaveBeenCalled();
  });
  it("keeps the active object and its action available after a workspace resize", () => {
    render(<Cards />); fireEvent.click(screen.getByRole("button", {name:"版本与出处"})); fireEvent.click(screen.getByRole("button", {name:"查看版本"}));
    act(() => window.dispatchEvent(new Event("resize")));
    expect(screen.getByRole("dialog", {name:"资料详情"})).toBeInTheDocument();
    expect(screen.getByRole("dialog", {name:"版本配置"})).toBeInTheDocument();
    expect(screen.getByRole("button", {name:"应用参数"})).toBeInTheDocument();
  });
});
