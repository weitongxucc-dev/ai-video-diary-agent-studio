import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import Loading from "../loading";
import { WorkbenchRegistry } from "@/components/workbench/context";

const navigation = vi.hoisted(() => ({ path: "/templates", search: "", readSearch: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path, useSearchParams: () => navigation.readSearch() }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main aria-label="独立页面外壳">{children}</main> }));

beforeEach(() => {
  navigation.search = "";
  navigation.readSearch.mockReset();
  navigation.readSearch.mockImplementation(() => new URLSearchParams(navigation.search));
});

it.each(["/", "/tasks/task-a", "/library", "/knowledge", "/profiles", "/templates"])("leaves loading to the persistent workbench for %s", path => {
  navigation.path = path;
  const view = render(<WorkbenchRegistry.Provider value={{ register: vi.fn(), forget: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(view.container).toBeEmptyDOMElement();
});

it.each(["/settings", "/production", "/history", "/health"])("retains the independent loading shell for %s", path => {
  navigation.path = path;
  render(<WorkbenchRegistry.Provider value={{ register: vi.fn(), forget: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(screen.getByRole("main", { name: "独立页面外壳" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
});

it("still renders loading when a resource route has no workbench owner", () => {
  navigation.path = "/templates";
  render(<Loading />);
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
});

it("hands device summary loading to the workbench and keeps a fallback for direct visits", () => {
  navigation.path = "/device-status";
  navigation.search = "mode=panel";
  const view = render(<WorkbenchRegistry.Provider value={{ register: vi.fn(), forget: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(view.container).toBeEmptyDOMElement();
  view.rerender(<Loading />);
  expect(screen.getByRole("main", { name: "独立页面外壳" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
});

it("leaves the new-video draft loading to the workbench", () => {
  navigation.path = "/production";
  navigation.search = "new=1";
  const view = render(<WorkbenchRegistry.Provider value={{ register: vi.fn(), forget: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(view.container).toBeEmptyDOMElement();
});

it("keeps public auth loading independent of the workspace and query hooks", () => {
  navigation.path = "/auth/update-password";
  render(<Loading />);
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
  expect(screen.queryByRole("main", { name: "独立页面外壳" })).not.toBeInTheDocument();
  expect(navigation.readSearch).not.toHaveBeenCalled();
});
