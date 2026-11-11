"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";

const WorkbenchTarget = createContext<{ targetId: string | null; clearTarget: () => void }>({ targetId: null, clearTarget: () => {} });
export function useWorkbenchTarget() { return useContext(WorkbenchTarget); }

/** The explicit business target survives resource browsing; it also owns chat scope. */
export function WorkbenchTargetProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const [target, setTarget] = useState({ pathname, id: pathname.startsWith("/tasks/") ? pathname : null });
  if (target.pathname !== pathname) setTarget({ pathname, id: pathname.startsWith("/tasks/") ? pathname : target.id });
  const clearTarget = useCallback(() => setTarget(current => ({ ...current, id: null })), []);
  return <WorkbenchTarget.Provider value={{ targetId: target.id, clearTarget }}>{children}</WorkbenchTarget.Provider>;
}
