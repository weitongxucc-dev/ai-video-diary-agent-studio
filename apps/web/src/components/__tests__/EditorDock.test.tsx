import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { EditorDockProvider, useEditorDockHost } from "../workbench/EditorDock";
import { EditorResourcePane } from "../workbench/EditorResourcePane";
import { WorkspacePageContext, WorkspacePanelContext, type WorkspacePageSlot } from "../workbench/context";

afterEach(cleanup);
const page = { id: "/tasks/one", href: "/tasks/one", active: true, viewState: new Map() } as WorkspacePageSlot;
function EditingSurface() {
  const host = useEditorDockHost("auxiliary");
  return <><div ref={host} data-testid="preview-host"/><section aria-label="时间线">完整时间线</section></>;
}
function Workbench({ inspectorOpen = false }: { inspectorOpen?: boolean }) {
  return <EditorDockProvider activePageId={page.id}><WorkspacePageContext.Provider value={page}>
    <WorkspacePanelContext.Provider value={{ inspectorOpen, resourceOpen: true, revealTaskPanel() {} }}>
      <EditingSurface/>
      <EditorResourcePane title="素材库" onClose={() => {}}><input aria-label="素材筛选" defaultValue="原筛选"/></EditorResourcePane>
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
