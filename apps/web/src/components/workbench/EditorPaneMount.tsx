"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useActiveEditorDock } from "./EditorDock";
import { useWorkspacePage } from "./context";

/** Move one stable portal destination; never remount a form when docking changes. */
export function EditorPaneMount({ docked, hidden, homeClassName, mountClassName, children }: {
  docked: boolean; hidden: boolean; homeClassName: string; mountClassName: string; children: ReactNode;
}) {
  const host = useActiveEditorDock("auxiliary");
  const active = useWorkspacePage()?.active !== false;
  const invisible = hidden || !active;
  const home = useRef<HTMLDivElement>(null);
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const node = document.createElement("div"); setContainer(node);
    return () => node.remove();
  }, []);
  useLayoutEffect(() => {
    const destination = active && docked && host ? host : home.current;
    if (!container || !destination) return;
    if (container.parentElement !== destination) destination.appendChild(container);
    container.hidden = invisible; container.className = mountClassName;
  }, [container, active, docked, host, invisible, mountClassName]);
  return <><div ref={home} className={homeClassName} data-docked={docked || undefined} hidden={invisible || Boolean(docked && host)}/>
    {container ? createPortal(children, container) : null}</>;
}
