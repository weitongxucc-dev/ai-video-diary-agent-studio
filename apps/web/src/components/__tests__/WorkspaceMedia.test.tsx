import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";
import { WorkspacePageContext, type WorkspacePageSlot } from "../workbench/context";
import { useWorkspaceViewState } from "../workbench/context";
import { Dialog } from "../ui/overlay";

afterEach(cleanup);
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
