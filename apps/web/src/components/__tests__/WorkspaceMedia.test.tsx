import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StrictMode, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { WorkspaceAudio, WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { WorkspacePageContext, type WorkspacePageSlot } from "../workbench/context";
import { useWorkspaceViewState } from "../workbench/context";
import { WorkspaceInspector } from "../workbench/WorkspaceInspector";
import { Dialog, Sheet } from "../ui/overlay";

afterEach(cleanup);

function mockMediaEscape() {
  const listeners = new Set<(id: string) => void>();
  let owner: string | null = null;
  vi.stubGlobal("electronAPI", {
    setCardMediaEscape: (id: string | null) => { owner = id; },
    onCardMediaEscape: (listener: (id: string) => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
  return {
    get owner() { return owner; },
    deliver: (id: string) => act(() => { listeners.forEach(listener => listener(id)); }),
  };
}
it("keeps a source preview's playhead separate from a thumbnail of the same file", () => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  const slot: WorkspacePageSlot = { id: "/library", href: "/library", active: true, updateShell: vi.fn(), viewState: new Map() };
  const content = <WorkspacePageContext.Provider value={slot}><WorkspaceVideo data-testid="thumbnail" aria-hidden src="/api/material/media" /><WorkspaceVideo aria-label="原素材预览" src="/api/material/media" /></WorkspacePageContext.Provider>;
  const first = render(content); const preview = screen.getByLabelText("原素材预览") as HTMLVideoElement;
  preview.currentTime = 6.5; fireEvent.timeUpdate(preview); first.unmount();
  render(content); const thumbnail = screen.getByTestId("thumbnail") as HTMLVideoElement;
  fireEvent.loadedMetadata(thumbnail); fireEvent.loadedMetadata(screen.getByLabelText("原素材预览"));
  expect(thumbnail.currentTime).toBe(0); expect((screen.getByLabelText("原素材预览") as HTMLVideoElement).currentTime).toBe(6.5);
});
it("releases media in a kept-mounted closed overlay and restores a paused position on reopen", () => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const content = (open: boolean) => <Dialog title="真实预览容器" open={open} onClose={vi.fn()}><WorkspaceVideo aria-label="弹层预览" src="/api/material/media" /></Dialog>;
  const view = render(content(true));
  const media = screen.getByLabelText("弹层预览") as HTMLVideoElement;
  media.currentTime = 6.5; fireEvent.timeUpdate(media);
  view.rerender(content(false)); expect(media).not.toHaveAttribute("src");
  media.currentTime = 0; fireEvent.timeUpdate(media);
  view.rerender(content(true)); fireEvent.loadedMetadata(media);
  expect(media).toHaveAttribute("src", "/api/material/media"); expect(media.currentTime).toBe(6.5); expect(play).not.toHaveBeenCalled();
});
it("keeps the active source through React's StrictMode ref cleanup and reattachment", () => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  render(<StrictMode><WorkspaceVideo aria-label="严格模式预览" src="/api/material/media" /></StrictMode>);
  expect(screen.getByLabelText("严格模式预览")).toHaveAttribute("src", "/api/material/media");
});
it("releases an inactive preview source, preserves its position and never resumes playing on return", () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const slot: WorkspacePageSlot = { id: "/tasks/a", href: "/tasks/a", active: true, updateShell: vi.fn(), viewState: new Map() };
  const content = (active: boolean) => <WorkspacePageContext.Provider value={{ ...slot, active }}><WorkspaceVideo aria-label="实际任务预览" src="/api/task/output?version=1" /></WorkspacePageContext.Provider>;
  const view = render(content(true));
  const video = screen.getByLabelText("实际任务预览") as HTMLVideoElement;
  video.currentTime = 4.5; fireEvent.timeUpdate(video);
  view.rerender(content(false));
  expect(pause).toHaveBeenCalled(); expect(load).toHaveBeenCalled();
  expect(video).not.toHaveAttribute("src");
  video.currentTime = 0; fireEvent.timeUpdate(video);
  view.rerender(content(true)); fireEvent.loadedMetadata(video);
  expect(video.currentTime).toBe(4.5); expect(play).not.toHaveBeenCalled();
  view.unmount(); expect(pause.mock.calls.length).toBeGreaterThan(1);
});

it("keeps unsaved inputs bound to their original version when a closed view reopens on newer facts", () => {
  const slot: WorkspacePageSlot = { id: "/tasks/a", href: "/tasks/a", active: true, updateShell: vi.fn(), viewState: new Map() };
  function Draft({ version }: { version: string }) {
    const [value, setValue] = useWorkspaceViewState(`trim:${version}`, "1");
    return <input aria-label="裁剪起点草稿" value={value} onChange={event => setValue(event.target.value)} />;
  }
  const content = (version: string) => <WorkspacePageContext.Provider value={slot}><Draft version={version} /></WorkspacePageContext.Provider>;
  const view = render(content("v1"));
  fireEvent.change(screen.getByLabelText("裁剪起点草稿"), { target: { value: "1.5" } });
  view.rerender(content("v2"));
  expect(screen.getByLabelText("裁剪起点草稿")).toHaveValue("1");
  view.rerender(content("v1"));
  expect(screen.getByLabelText("裁剪起点草稿")).toHaveValue("1.5");
});

it("closes the dock without losing drafts, releases hidden media and returns keyboard focus", () => {
  const pause=vi.spyOn(HTMLMediaElement.prototype,"pause").mockImplementation(()=>undefined);
  vi.spyOn(HTMLMediaElement.prototype,"load").mockImplementation(()=>undefined);
  const content=(open:boolean)=><><button>素材属性</button><WorkspaceInspector title="素材属性" open={open} onClose={vi.fn()}><input aria-label="未保存说明" defaultValue=""/><WorkspaceVideo aria-label="属性媒体" src="/api/material/media"/></WorkspaceInspector></>;
  const view=render(content(false));
  const trigger=screen.getByRole("button",{name:"素材属性"});trigger.focus();
  view.rerender(content(true));
  fireEvent.change(screen.getByLabelText("未保存说明"),{target:{value:"保留的草稿"}});
  const media=screen.getByLabelText("属性媒体");
  expect(media).toHaveAttribute("src");
  view.rerender(content(false));
  expect(pause).toHaveBeenCalled();expect(media).not.toHaveAttribute("src");expect(trigger).toHaveFocus();
  view.rerender(content(true));
  expect(screen.getByLabelText("未保存说明")).toHaveValue("保留的草稿");
});

it("returns focus to the resource row once its portal list becomes visible", async () => {
  function ResourceList({ hidden, opener }: { hidden: boolean; opener: RefObject<HTMLButtonElement | null> }) {
    const region = useRef<HTMLDivElement>(null);
    useLayoutEffect(() => { region.current!.hidden = hidden; }, [hidden]);
    return createPortal(<div ref={region}><button ref={opener}>声音档案</button></div>, document.body);
  }
  function ResourceDock({ open }: { open: boolean }) {
    const opener = useRef<HTMLButtonElement>(null);
    return <>
      <WorkspaceInspector title="声音详情" open={open} onClose={vi.fn()} opener={opener}>声音 v1</WorkspaceInspector>
      <ResourceList hidden={open} opener={opener} />
    </>;
  }
  const view = render(<ResourceDock open={false} />);
  const row = screen.getByRole("button", { name: "声音档案" });
  row.focus();
  view.rerender(<ResourceDock open />);
  const heading = screen.getByRole("heading", { name: "声音详情" });
  expect(heading).toHaveFocus();
  view.rerender(<ResourceDock open={false} />);
  expect(row).toBeVisible();
  await waitFor(() => expect(row).toHaveFocus());
});

it.each([WorkspaceAudio, WorkspaceVideo])("closes resource details for focused native media Escape and lets fullscreen handle Escape", Media => {
  const bridge = mockMediaEscape();
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  const fullscreenProperty = Object.getOwnPropertyDescriptor(document, "fullscreenElement");
  let fullscreen: Element | null = null;
  Object.defineProperty(document, "fullscreenElement", { configurable: true, get: () => fullscreen });
  function ResourceDock() {
    const [open, setOpen] = useState(false);
    const opener = useRef<HTMLButtonElement>(null);
    return <>
      <button ref={opener} onClick={() => setOpen(true)}>资源行</button>
      <audio controls tabIndex={0} aria-label="其他播放器" />
      <WorkspaceInspector title="资源详情" open={open} onClose={() => setOpen(false)} opener={opener} mediaEscape>
        <Media controls tabIndex={0} aria-label="资源预览" src="/api/resource/preview" />
      </WorkspaceInspector>
    </>;
  }
  try {
    render(<ResourceDock />);
    const row = screen.getByRole("button", { name: "资源行" });
    const media = screen.getByLabelText("资源预览");
    row.focus();
    fireEvent.click(row);
    screen.getByLabelText("其他播放器").focus();
    expect(bridge.owner).toBeNull();
    media.focus();
    expect(media).toHaveFocus();
    const owner = bridge.owner;
    expect(owner).toEqual(expect.any(String));
    fullscreen = media;
    fireEvent(document, new Event("fullscreenchange"));
    expect(bridge.owner).toBeNull();
    bridge.deliver(owner!);
    fireEvent.keyDown(media, { key: "Escape" });
    expect(screen.getByRole("heading", { name: "资源详情" })).toBeVisible();

    fullscreen = null;
    fireEvent(document, new Event("fullscreenchange"));
    expect(bridge.owner).toBe(owner);
    media.blur();
    expect(bridge.owner).toBeNull();
    bridge.deliver(owner!);
    expect(screen.getByRole("heading", { name: "资源详情" })).toBeVisible();
    media.focus();
    bridge.deliver(owner!);
    expect(media).not.toHaveAttribute("src");
    expect(pause).toHaveBeenCalled();
    expect(row).toHaveFocus();
    expect(bridge.owner).toBeNull();

    fireEvent.click(row);
    fireEvent.keyDown(screen.getByRole("heading", { name: "资源详情" }), { key: "Escape" });
    expect(row).toHaveFocus();
    expect(media).not.toHaveAttribute("src");
  } finally {
    cleanup();
    vi.unstubAllGlobals();
    if (fullscreenProperty) Object.defineProperty(document, "fullscreenElement", fullscreenProperty);
    else Reflect.deleteProperty(document, "fullscreenElement");
  }
});

it("keeps the focused resource media registered when another card opens and closes", () => {
  const bridge = mockMediaEscape();
  const closeResource = vi.fn();
  const closeSheet = vi.fn();
  const content = (sheetOpen: boolean) => <>
    <WorkspaceInspector title="资源详情" open onClose={closeResource} mediaEscape>
      <audio controls tabIndex={0} aria-label="详情播放器" />
    </WorkspaceInspector>
    <Sheet title="另一张卡片" open={sheetOpen} onClose={closeSheet}>
      <video controls tabIndex={0} aria-label="卡片播放器" />
    </Sheet>
  </>;
  try {
    const view = render(content(true));
    const resource = screen.getByLabelText("详情播放器");
    resource.focus();
    expect(resource).toHaveFocus();
    expect(bridge.owner).toEqual(expect.any(String));
    const resourceId = bridge.owner!;

    screen.getByLabelText("卡片播放器").focus();
    expect(bridge.owner).toEqual(expect.any(String));
    expect(bridge.owner).not.toBe(resourceId);
    resource.focus();
    expect(bridge.owner).toBe(resourceId);
    view.rerender(content(false));
    expect(bridge.owner).toBe(resourceId);
    bridge.deliver(resourceId);
    expect(closeResource).toHaveBeenCalledOnce();
    expect(closeSheet).not.toHaveBeenCalled();
  } finally {
    cleanup();
    vi.unstubAllGlobals();
  }
});
