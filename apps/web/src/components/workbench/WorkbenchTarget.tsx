"use client";

import { createContext, Suspense, useCallback, useContext, useState, type ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { validDraftId } from "@/lib/preflight-draft";
import { isCreationDraftRoute } from "./context";

type Target = { targetId: string | null; draftId: string | null };
const homeTarget: Target = { targetId: null, draftId: null };
const WorkbenchTarget = createContext<Target & { clearTarget: () => void }>({ ...homeTarget, clearTarget: () => {} });
export function useWorkbenchTarget() { return useContext(WorkbenchTarget); }

/** The explicit business target survives resource browsing; it also owns chat scope. */
function RoutedWorkbenchTarget({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const isDraftRoute = isCreationDraftRoute(pathname, params);
  const rawDraftId = isDraftRoute ? params?.get("draft_id") : null;
  const draftId = rawDraftId && validDraftId(rawDraftId) ? rawDraftId.toLowerCase() : null;
  const targetId = pathname.startsWith("/tasks/") ? pathname : null;
  const mode = params?.get("mode");
  const browsingHome = pathname === "/" && (mode === "panel" || mode === "manage");
  const routeKey = isDraftRoute ? `draft:${draftId ?? ""}` : browsingHome ? `home:${mode}` : pathname;
  const [selection, setSelection] = useState({ routeKey, target: { targetId, draftId } });
  if (selection.routeKey !== routeKey) {
    const explicitTarget = (pathname === "/" && !browsingHome) || targetId !== null || isDraftRoute;
    setSelection({ routeKey, target: explicitTarget ? { targetId, draftId } : selection.target });
  }
  const clearTarget = useCallback(() => setSelection(current => ({ ...current, target: homeTarget })), []);
  return <WorkbenchTarget.Provider value={{ ...selection.target, clearTarget }}>{children}</WorkbenchTarget.Provider>;
}

export function WorkbenchTargetProvider({ children }: { children: ReactNode }) {
  return <Suspense fallback={children}><RoutedWorkbenchTarget>{children}</RoutedWorkbenchTarget></Suspense>;
}
