"use client";

import { useContext, useEffect, type ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { WorkbenchRegistry, workspacePageId } from "./context";

/** Register the actual page component, outside Next's disposable route subtree. */
export function WorkspaceRoute({ children, label }: { children: ReactNode; label: string }) {
  const registry = useContext(WorkbenchRegistry);
  const pathname = usePathname() ?? "/";
  const params = useSearchParams();
  const search = params?.toString() ?? "";
  const id = workspacePageId(pathname, params ?? new URLSearchParams());
  const href = pathname + (search ? `?${search}` : "");
  useEffect(() => { registry?.register({ id, href, label, content: children }); }, [registry, id, href, label, children]);
  return registry ? null : children;
}
