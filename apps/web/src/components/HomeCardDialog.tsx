"use client";

import { ChevronDown, X } from "lucide-react";
import { createContext, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import styles from "./SideCards.module.css";

type Kind = "detail" | "config" | "confirmation" | "selection" | "modal" | "navigation";
type Layer = { id: string; kind: Kind; active: boolean; zIndex: number; parentId?: string; node: HTMLElement; close: () => void };
const layers: Layer[] = [];
const ParentCard = createContext<Layer | null>(null);
const topLayer = () => layers.at(-1);

/** Object-card children portal out of the parent's scroll clip. */
export function HomeCardDialog({ title, children, onClose, kind = "modal", anchor, footer, open = true }: {
  title: string; children: ReactNode; onClose: () => void; kind?: Kind;
  anchor?: RefObject<HTMLElement | null>; footer?: ReactNode;
  open?: boolean;
}) {
  const id = useId();
  const parent = useContext(ParentCard);
  const [clientReady, setClientReady] = useState(false);
  useEffect(() => { setClientReady(true); }, []);
  const active = clientReady && open && parent?.active !== false;
  const zIndex = kind === "modal" || kind === "navigation" ? 111 : parent ? parent.zIndex + 10 : kind === "detail" ? 80 : kind === "selection" ? 100 : 90;
  const node = useRef<HTMLElement>(null);
  const close = useRef(onClose); close.current = onClose;
  const [layer, setLayer] = useState<Layer | null>(null);
  const [position, setPosition] = useState<CSSProperties>({ visibility: "hidden" });
  useLayoutEffect(() => {
    if (!active) return;
    const card = node.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const entry: Layer = { id, kind, active: true, zIndex, parentId: parent?.id, node: card, close: () => close.current() };
    // Replace a sibling, never its parent or the parent's scroll/draft state.
    layers.filter(value => value.parentId === entry.parentId && value.id !== id && (entry.parentId || value.kind === kind)).forEach(value => value.close());
    layers.push(entry); setLayer(entry);
    const measure = () => {
      const margin = kind === "detail" ? 16 : 12;
      const width = Math.min(kind === "navigation" ? 580 : kind === "modal" ? 480 : kind === "detail" ? 360 : kind === "selection" ? 240 : 320, window.innerWidth - margin * 2);
      if (kind === "modal" || kind === "navigation") { setPosition({ width }); return; }
      const bounds = parent?.node.getBoundingClientRect();
      if (kind === "detail") {
        const workspace = document.querySelector(".desktop-main-scroll")?.getBoundingClientRect();
        const transport = document.querySelector(".video-editor-shell [data-editor-transport]")?.getBoundingClientRect();
        setPosition({ width, top: (workspace?.top ?? 56) + 16, right: 16, bottom: transport ? Math.max(16, window.innerHeight - transport.top + 16) : 16 });
      } else if (kind === "selection" && anchor?.current) {
        const trigger = anchor.current.getBoundingClientRect();
        const height = Math.min(card.scrollHeight || 240, window.innerHeight - 24);
        setPosition({ width, left: Math.max(12, Math.min(trigger.right - width, window.innerWidth - width - 12)), top: trigger.bottom + 6 + height <= window.innerHeight - 12 ? trigger.bottom + 6 : Math.max(12, trigger.top - height - 6), maxHeight: window.innerHeight - 24 });
      } else {
        setPosition({ width, right: bounds ? window.innerWidth - bounds.right + 12 : 16, ...(kind === "confirmation" ? { bottom: bounds ? window.innerHeight - bounds.bottom + 12 : 16 } : { top: (bounds?.top ?? 56) + 44 }), maxHeight: window.innerHeight - (bounds?.top ?? 56) - 60 });
      }
    };
    measure(); const frame = window.requestAnimationFrame(measure); window.addEventListener("resize", measure);
    card.querySelector<HTMLElement>(kind === "confirmation" || kind === "modal" ? "[data-card-cancel], button" : "button, input, select, textarea")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (topLayer() !== entry) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); entry.close(); return; }
      if (event.key !== "Tab" || (kind !== "modal" && kind !== "confirmation" && kind !== "navigation")) return;
      const focusable = Array.from(card.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]'));
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const release = () => {
      window.removeEventListener("pointerup", release, true); window.removeEventListener("pointercancel", release, true);
      window.setTimeout(() => window.removeEventListener("click", swallowClick, true), 0);
    };
    const swallowClick = (event: MouseEvent) => { event.preventDefault(); event.stopImmediatePropagation(); release(); };
    const outside = (event: PointerEvent) => {
      if (topLayer() !== entry || card.contains(event.target as Node)) return;
      event.preventDefault(); event.stopImmediatePropagation();
      window.addEventListener("click", swallowClick, { capture: true, once: true });
      window.addEventListener("pointerup", release, { capture: true, once: true });
      window.addEventListener("pointercancel", release, { capture: true, once: true });
      entry.close();
    };
    const fieldScroll = (event: Event) => {
      if (kind === "selection" && topLayer() === entry && event.target instanceof Node && !card.contains(event.target)) entry.close();
    };
    if (kind === "confirmation" && parent) parent.node.inert = true;
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("scroll", fieldScroll, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("scroll", fieldScroll, true);
      const index = layers.indexOf(entry); if (index !== -1) layers.splice(index, 1);
      if (kind === "confirmation" && parent) parent.node.inert = false;
      if (card.contains(document.activeElement) || document.activeElement === document.body) previousFocus?.focus();
    };
  }, [id, kind, parent, anchor, active, zIndex]);
  useLayoutEffect(() => {
    if (!layer || !active) return;
    const target = kind === "selection" ? node.current?.querySelector<HTMLElement>('[role="option"][aria-selected="true"]') ?? node.current?.querySelector<HTMLElement>('[role="option"]') : node.current?.querySelector<HTMLElement>('[data-card-cancel]');
    target?.focus();
  }, [layer, kind, active]);
  const childContext = useMemo(() => layer ? { ...layer, active } : null, [layer, active]);
  const card = <ParentCard.Provider value={childContext}>
    {active && kind === "confirmation" && parent ? <div className={styles.localShade} style={{ ...(() => { const r = parent.node.getBoundingClientRect(); return { top: r.top, left: r.left, width: r.width, height: r.height }; })() }} /> : null}
    <section hidden={!active} aria-label={title} aria-modal={kind === "modal" || kind === "navigation" ? true : undefined} className={`${styles.card} ${styles[kind]}`} ref={node} role="dialog" style={{ ...position, zIndex }} data-card-kind={kind}>
      <header><h2>{title}</h2><button aria-label="关闭卡片" onClick={onClose} type="button"><X size={16} /></button></header>
      <div className={styles.body}>{layer ? children : null}</div>
      {layer && (footer || kind === "detail") ? <footer>{footer ?? <button type="button" onClick={onClose}>收起详情</button>}</footer> : null}
    </section>
  </ParentCard.Provider>;
  return clientReady ? createPortal(kind === "modal" || kind === "navigation" ? <div hidden={!active} className={styles.modalShade}>{card}</div> : card, document.body) : null;
}

export function CardSelect({ label, value, options, onChange, disabled = false, name }: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (value: string) => void; disabled?: boolean; name?: string;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <>
    {name ? <input type="hidden" name={name} value={value} /> : null}
    <button className={styles.select} aria-label={label} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={() => setOpen(value => !value)} ref={trigger} type="button" onKeyDown={event => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); } }}><span>{options.find(option => option.value === value)?.label ?? value}</span><ChevronDown size={16} /></button>
    {open ? <HomeCardDialog kind="selection" title={label} anchor={trigger} onClose={() => setOpen(false)}><div role="listbox" aria-label={label} onKeyDown={event => {
      const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="option"]'));
      const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : event.key === "ArrowDown" ? (current + 1) % buttons.length : event.key === "ArrowUp" ? (current - 1 + buttons.length) % buttons.length : null;
      if (next !== null) { event.preventDefault(); buttons[next]?.focus(); }
    }}>{options.map(option => <button className={styles.option} key={option.value} role="option" aria-selected={value === option.value} type="button" onClick={() => { onChange(option.value); setOpen(false); trigger.current?.focus(); }}>{option.label}</button>)}</div></HomeCardDialog> : null}
  </>;
}

/** Shared by the general and task conversations; this is disclosure, not execution. */
export function CardDisclosure({ title, children }: { title:string; children:ReactNode }) {
  const [open,setOpen]=useState(false);
  const trigger=useRef<HTMLButtonElement>(null);
  return <><button className={styles.disclosure} aria-expanded={open} onClick={()=>setOpen(value=>!value)} ref={trigger} type="button">{title}</button>{open ? <HomeCardDialog kind="selection" title={title} anchor={trigger} onClose={()=>setOpen(false)}>{children}</HomeCardDialog> : null}</>;
}
