"use client";

import { X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

export function HomeCardDialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);

  useEffect(() => {
    const dialogNode = dialog.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
      if (event.key !== "Tab" || !dialog.current) return;
      const focusable = Array.from(dialog.current.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary'));
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (dialogNode?.contains(document.activeElement) || document.activeElement === document.body) previousFocus?.focus();
    };
  }, []);

  return <div className="home-card-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section aria-label={title} aria-modal="true" className="home-card-dialog" ref={dialog} role="dialog">
      <header><h2>{title}</h2><button aria-label="关闭卡片" onClick={onClose} ref={closeButton} type="button"><X size={17} /></button></header>
      <div className="home-card-dialog__body">{children}</div>
    </section>
  </div>;
}
