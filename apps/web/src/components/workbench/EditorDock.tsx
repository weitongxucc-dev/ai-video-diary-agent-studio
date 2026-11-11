"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useWorkspacePage } from "./context";

type Slot = "tools" | "auxiliary" | "timelineTools";
type Hosts = Record<string, Partial<Record<Slot, HTMLElement>>>;
const EditorDock = createContext<{
  activePageId?: string; hosts: Hosts;
  register: (pageId: string, slot: Slot, node: HTMLElement | null) => void;
} | null>(null);

/** DOM destinations only. Task, conversation and editing state retain their owners. */
export function EditorDockProvider({ activePageId, children }: { activePageId?: string; children: ReactNode }) {
  const [hosts, setHosts] = useState<Hosts>({});
  const register = useCallback((pageId: string, slot: Slot, node: HTMLElement | null) => {
    setHosts(current => {
      if ((current[pageId]?.[slot] ?? null) === node) return current;
      const next = { ...current, [pageId]: { ...current[pageId] } };
      if (node) next[pageId][slot] = node;
      else { delete next[pageId][slot]; if (!Object.keys(next[pageId]).length) delete next[pageId]; }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ activePageId, hosts, register }), [activePageId, hosts, register]);
  return <EditorDock.Provider value={value}>{children}</EditorDock.Provider>;
}

export function useEditorDockHost(slot: Slot) {
  const register = useContext(EditorDock)?.register;
  const pageId = useWorkspacePage()?.id;
  return useCallback((node: HTMLDivElement | null) => {
    if (pageId) register?.(pageId, slot, node);
  }, [pageId, register, slot]);
}

export function useActiveEditorDock(slot: Slot) {
  const dock = useContext(EditorDock);
  return dock?.activePageId ? dock.hosts[dock.activePageId]?.[slot] ?? null : null;
}
