import { fireEvent, render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TimelineViewport } from "../TimelineViewport";
import { WorkspacePageContext, type WorkspacePageSlot } from "../workbench/context";

let resize: () => void;
let width: number;
beforeEach(() => {
  width = 688;
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(() => width);
  vi.spyOn(window, "getComputedStyle").mockReturnValue({getPropertyValue: (key: string) => key === "--timeline-label-width" ? "80px" : "8px"} as CSSStyleDeclaration);
  vi.stubGlobal("ResizeObserver", class { constructor(callback: () => void) { resize = callback; } observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 0; });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const props = { duration:115.8,time:0,playing:false,selectionKey:"clip-1",selectedTime:0,formatTime:(n:number)=>n.toFixed(1),onSeek:vi.fn() };

it("keeps 21 shots on one scalable canvas, fits on request and restores scroll", () => {
  const cache = new Map<string, unknown>([["timeline.scroll", 200]]);
  const page = {id:"task",href:"/tasks/task",active:true,viewState:cache} as WorkspacePageSlot;
  const view=render(<WorkspacePageContext.Provider value={page}><TimelineViewport {...props}>{Array.from({length:21},(_,i)=><button key={i}>镜头 {i+1}</button>)}</TimelineViewport></WorkspacePageContext.Provider>);
  const viewport=view.container.querySelector('[data-workspace-scroll="timeline"]') as HTMLDivElement;
  const canvas=viewport.firstElementChild as HTMLDivElement;
  expect(canvas.style.width).toBe("1477.6px");
  expect(viewport.scrollLeft).toBe(200);
  expect(screen.getAllByRole("button",{name:/镜头/})).toHaveLength(21);
  fireEvent.click(screen.getByRole("button",{name:"放大时间线"}));
  expect(canvas.style.width).toBe("2172.4px");
  fireEvent.click(screen.getByRole("button",{name:"时间线适应窗口"}));
  expect(canvas.style.width).toBe("688px");
  width=488; act(()=>resize());
  expect(canvas.style.width).toBe("488px");
  expect(cache.get("timeline.pixels-per-second")).toBeNull();
  viewport.scrollLeft=100;fireEvent.scroll(viewport);
  expect(cache.get("timeline.scroll")).toBe(100);
});

it("seeks accurately by ruler pointer and keyboard without changing clips", () => {
  const onSeek=vi.fn();
  render(<TimelineViewport {...props} onSeek={onSeek}><button>镜头</button></TimelineViewport>);
  const ruler=screen.getByRole("slider",{name:"时间线播放位置"});
  ruler.setPointerCapture=vi.fn();
  vi.spyOn(ruler,"getBoundingClientRect").mockReturnValue({left:88,width:1389.6} as DOMRect);
  // jsdom PointerEvent is not implemented, so use a mouse-backed pointer event.
  fireEvent(ruler,new MouseEvent("pointerdown",{bubbles:true,clientX:688}));
  expect(onSeek.mock.lastCall![0]).toBeCloseTo(50);
  fireEvent.pointerUp(ruler);
  fireEvent.keyDown(ruler,{key:"End"});
  expect(onSeek).toHaveBeenLastCalledWith(115.8);
  fireEvent.keyDown(ruler,{key:"ArrowRight",shiftKey:true});
  expect(onSeek).toHaveBeenLastCalledWith(10);
});
