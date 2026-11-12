import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { afterEach, expect, it } from "vitest";
import { EditorDockProvider, useActiveEditorDock, useEditorDockHost } from "../workbench/EditorDock";
import { EditorResourcePane } from "../workbench/EditorResourcePane";
import { WorkspacePageContext, WorkspacePanelContext, type WorkspacePageSlot } from "../workbench/context";

afterEach(cleanup);
const page = { id: "/tasks/one", href: "/tasks/one", active: true, viewState: new Map() } as WorkspacePageSlot;
function EditingSurface() {
  const host = useEditorDockHost("auxiliary");
  return <><div ref={host} data-testid="preview-host"/><section aria-label="时间线">完整时间线</section></>;
}
function Workbench({ inspectorOpen = false, docked = true, active = true, surface = true, pageActive = true }: { inspectorOpen?: boolean; docked?: boolean; active?: boolean; surface?: boolean; pageActive?: boolean }) {
  return <EditorDockProvider activePageId={page.id}><WorkspacePageContext.Provider value={{ ...page, active: pageActive }}>
    <WorkspacePanelContext.Provider value={{ inspectorOpen, resourceOpen: true, conversationFocused: false, revealTaskPanel() {} }}>
      {surface ? <EditingSurface/> : null}
      <EditorResourcePane title="素材库" active={active} docked={docked} onClose={() => {}}><input aria-label="素材筛选" defaultValue="原筛选"/></EditorResourcePane>
    </WorkspacePanelContext.Provider>
  </WorkspacePageContext.Provider></EditorDockProvider>;
}

it("docks resources beside the preview and retains the input while properties take their place", () => {
  const view = render(<Workbench/>);
  const host = screen.getByTestId("preview-host");
  const input = screen.getByRole("textbox", { name: "素材筛选" });
  expect(host).toContainElement(input);
  expect(host).not.toContainElement(screen.getByRole("region", { name: "时间线" }));
  fireEvent.change(input, { target: { value: "保留这次筛选" } });
  view.rerender(<Workbench inspectorOpen/>);
  expect(input).not.toBeVisible();
  view.rerender(<Workbench/>);
  expect(screen.getByRole("textbox", { name: "素材筛选" })).toBe(input);
  expect(input).toHaveValue("保留这次筛选");
});

it("retains an unsubmitted local draft across resource switches and preview host removal", () => {
  const view = render(<Workbench/>);
  const input = screen.getByRole("textbox", { name: "素材筛选" });
  fireEvent.change(input, { target: { value: "https://example.com/unsubmitted-video" } });
  view.rerender(<Workbench active={false} docked={false}/>);
  expect(input).not.toBeVisible();
  view.rerender(<Workbench/>);
  expect(screen.getByRole("textbox", { name: "素材筛选" })).toBe(input);
  expect(input).toHaveValue("https://example.com/unsubmitted-video");
  view.rerender(<Workbench pageActive={false}/>);
  expect(input).not.toBeVisible();
  expect(screen.getByTestId("preview-host")).not.toContainElement(input);
  view.rerender(<Workbench/>);
  expect(screen.getByRole("textbox", { name: "素材筛选" })).toBe(input);
  view.rerender(<Workbench surface={false}/>);
  expect(screen.getByRole("textbox", { name: "素材筛选" })).toBe(input);
  view.rerender(<Workbench/>);
  expect(screen.getByTestId("preview-host")).toContainElement(input);
  expect(input).toHaveValue("https://example.com/unsubmitted-video");
});

it("keeps inactive cached pages' tools out of the current preview toolbar", () => {
  function Tools({ label }: { label: string }) {
    const host = useActiveEditorDock("tools");
    return host ? createPortal(<button>{label}</button>, host) : null;
  }
  function Host() { return <div data-testid="tools-host" ref={useEditorDockHost("tools")} />; }
  const fixture = (active: string) => <EditorDockProvider activePageId={active}>
    {["/tasks/a", "/tasks/b"].map(id => <WorkspacePageContext.Provider key={id} value={{ ...page, id, active: id === active }}>
      <div hidden={id !== active}><Host/><Tools label={id}/></div>
    </WorkspacePageContext.Provider>)}
  </EditorDockProvider>;
  const view = render(fixture("/tasks/a"));
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getByRole("button")).toHaveTextContent("/tasks/a");
  view.rerender(fixture("/tasks/b"));
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getByRole("button")).toHaveTextContent("/tasks/b");
});
