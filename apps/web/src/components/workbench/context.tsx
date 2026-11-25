"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode, type SetStateAction } from "react";
import { useSearchParams } from "next/navigation";
import type { AppShellProps } from "../AppShell";
import { NEW_VIDEO_HREF } from "@/lib/preflight-draft-url";

export type WorkspacePage = { id: string; href: string; label: string; content: ReactNode; open: boolean };
export type WorkspacePageSlot = {
  id: string; href: string; active: boolean; auxiliary?: boolean;
  updateShell: (id: string, props: AppShellProps) => void;
  viewState: Map<string, unknown>;
};
export const WorkspacePanelContext = createContext<{
  inspectorOpen: boolean; resourceOpen: boolean; conversationFocused: boolean; revealTaskPanel: () => void;
  resourceInspectorOpen?: boolean;
  openResourceInspector?: (opener?: HTMLElement) => void;
  openQuickInspector?: () => void;
}>({ inspectorOpen: false, resourceOpen: false, conversationFocused: false, revealTaskPanel: () => {} });
export const WorkbenchRegistry = createContext<{ register: (page: Omit<WorkspacePage, "open">) => void; forget: (id: string) => void } | null>(null);
export const WorkspacePageContext = createContext<WorkspacePageSlot | null>(null);
export const WorkbenchAssistantTargetContext = createContext<AppShellProps["assistantContext"]>(undefined);

export const workspaceResourcePaths = ["/library", "/knowledge", "/profiles", "/templates"];
const workResourcePanels = new Map([
  ["/library", "/library?mode=panel&resource=materials"],
  ["/knowledge", "/knowledge?mode=panel"],
  ["/profiles", "/profiles?mode=panel"],
  ["/templates", "/templates?mode=panel"],
]);

export function workspaceNavigationHref(href: string, hasActiveWork: boolean) {
  return hasActiveWork ? workResourcePanels.get(href.split("?")[0]) ?? href : href;
}

export function isWorkResourcePanel(pathname: string, search?: Pick<URLSearchParams, "get"> | null) {
  const mode = search?.get("mode");
  if (mode === "panel") return workResourcePanels.has(pathname);
  // Existing fixed profile references use locate_ref without a mode.
  return !mode && pathname === "/profiles" && Boolean(search?.get("locate_ref")) && !search?.get("task");
}

export function shouldDockWorkspaceResource(pathname: string, search?: Pick<URLSearchParams, "get"> | null) {
  return isWorkResourcePanel(pathname, search)
    || (search?.get("mode") !== "manage" && (pathname === "/library" || pathname === "/knowledge"));
}

export function isCreationDraftRoute(pathname: string, search?: Pick<URLSearchParams, "get"> | null) {
  return pathname === "/production" && search?.get("new") === "1" && search.get("view") !== "automations";
}

export function isCreationDraftPage(id: string) {
  return id.startsWith(NEW_VIDEO_HREF);
}

export function isWorkbenchManagedPath(pathname: string, search?: Pick<URLSearchParams, "get"> | null) {
  return isCreationDraftRoute(pathname, search) || pathname === "/" || workspaceResourcePaths.includes(pathname) || pathname.startsWith("/tasks/");
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
  // The active page follows the browser URL. A cached page can retain an
  // older href while Next has already navigated to a new query (for example
  // switching `/library?mode=manage` to an Asset Library category). Auxiliary
  // pages still use their own href so a docked resource pane keeps its scope.
  const search = page?.active ? params?.toString() ?? "" : page ? page.href.split("?")[1] ?? "" : params?.toString() ?? "";
  return useMemo(() => new URLSearchParams(search), [search]);
}
export function workspacePageId(pathname: string, search: Pick<URLSearchParams, "get">) {
  if (pathname.startsWith("/tasks/")) return pathname;
  if (isCreationDraftRoute(pathname, search)) {
    const id = search.get("draft_id");
    const requestId = search.get("request_id");
    if (id) return `${NEW_VIDEO_HREF}&draft_id=${encodeURIComponent(id)}`;
    return requestId ? `${NEW_VIDEO_HREF}&request_id=${encodeURIComponent(requestId)}` : NEW_VIDEO_HREF;
  }
  if (pathname === "/production" && search.get("view") === "automations") return "/production?view=automations";
  return pathname;
}
