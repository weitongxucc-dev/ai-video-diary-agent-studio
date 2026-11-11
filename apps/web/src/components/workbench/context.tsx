"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode, type SetStateAction } from "react";
import { useSearchParams } from "next/navigation";
import type { AppShellProps } from "../AppShell";

export type WorkspacePage = { id: string; href: string; label: string; content: ReactNode; open: boolean };
export type WorkspacePageSlot = {
  id: string; href: string; active: boolean; auxiliary?: boolean;
  updateShell: (id: string, props: AppShellProps) => void;
  viewState: Map<string, unknown>;
};
export const WorkspacePanelContext = createContext<{
  inspectorOpen: boolean; resourceOpen: boolean; revealTaskPanel: () => void;
  openQuickInspector?: () => void;
}>({ inspectorOpen: false, resourceOpen: false, revealTaskPanel: () => {} });
export const WorkbenchRegistry = createContext<{ register: (page: Omit<WorkspacePage, "open">) => void } | null>(null);
export const WorkspacePageContext = createContext<WorkspacePageSlot | null>(null);
export const WorkbenchAssistantTargetContext = createContext<AppShellProps["assistantContext"]>(undefined);

export const workspaceResourcePaths = ["/library", "/knowledge", "/profiles", "/templates"];
export function isWorkbenchManagedPath(pathname: string) {
  return pathname === "/" || workspaceResourcePaths.includes(pathname) || pathname.startsWith("/tasks/");
}

export function useWorkspacePage() { return useContext(WorkspacePageContext); }
/** Unsaved view inputs only; never a task, conversation or media receipt store. */
export function useWorkspaceViewState<T>(key: string, initial: T | (() => T)) {
  const cache = useWorkspacePage()?.viewState;
  const initialValue = useRef(initial);
  initialValue.current = initial;
  const read = () => cache?.has(key) ? cache.get(key) as T : typeof initialValue.current === "function" ? (initialValue.current as () => T)() : initialValue.current;
  const [entry, setEntry] = useState(() => ({ key, value: read() }));
  const value = entry.key === key ? entry.value : read();
  if (entry.key !== key) setEntry({ key, value });
  const setValue = useCallback((next: SetStateAction<T>) => setEntry(previous => {
    const old = previous.key === key ? previous.value : cache?.has(key) ? cache.get(key) as T : typeof initialValue.current === "function" ? (initialValue.current as () => T)() : initialValue.current;
    const value = typeof next === "function" ? (next as (value: T) => T)(old) : next;
    cache?.set(key, value);
    return { key, value };
  }), [cache, key]);
  return [value, setValue] as const;
}
export function useWorkspaceSearchParams() {
  const page = useWorkspacePage();
  const params = useSearchParams();
  const search = page ? page.href.split("?")[1] ?? "" : params?.toString() ?? "";
  return useMemo(() => new URLSearchParams(search), [search]);
}
export function workspacePageId(pathname: string, search: Pick<URLSearchParams, "get">) {
  if (pathname.startsWith("/tasks/")) return pathname;
  if (pathname === "/production" && search.get("view") === "automations") return "/production?view=automations";
  return pathname;
}
