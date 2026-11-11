import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import Loading from "../loading";
import { WorkbenchRegistry } from "@/components/workbench/context";

const navigation = vi.hoisted(() => ({ path: "/templates" }));
vi.mock("next/navigation", () => ({ usePathname: () => navigation.path }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: ReactNode }) => <main aria-label="独立页面外壳">{children}</main> }));

it.each(["/", "/tasks/task-a", "/library", "/knowledge", "/profiles", "/templates"])("leaves loading to the persistent workbench for %s", path => {
  navigation.path = path;
  const view = render(<WorkbenchRegistry.Provider value={{ register: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(view.container).toBeEmptyDOMElement();
});

it.each(["/settings", "/production", "/history", "/health"])("retains the independent loading shell for %s", path => {
  navigation.path = path;
  render(<WorkbenchRegistry.Provider value={{ register: vi.fn() }}><Loading /></WorkbenchRegistry.Provider>);
  expect(screen.getByRole("main", { name: "独立页面外壳" })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
});

it("still renders loading when a resource route has no workbench owner", () => {
  navigation.path = "/templates";
  render(<Loading />);
  expect(screen.getByRole("status")).toHaveTextContent("正在读取");
});
