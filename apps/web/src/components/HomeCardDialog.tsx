"use client";

import { ChevronDown, X } from "lucide-react";
import { createContext, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import styles from "./SideCards.module.css";

type Kind = "detail" | "config" | "confirmation" | "selection" | "modal" | "navigation";
type Layer = { id: string; kind: Kind; active: boolean; zIndex: number; parentId?: string; node: HTMLElement; close: () => void; position: CSSProperties; setPosition: (value: CSSProperties) => void; anchor?: RefObject<HTMLElement | null> };
const layers: Layer[] = [];
const ParentCard = createContext<Layer | null>(null);
const topLayer = () => layers.at(-1);

function syncDesktopMediaEscape() {
  const current = topLayer();
  const focused = document.activeElement;
  const mediaCard = current && focused instanceof HTMLMediaElement && current.node.contains(focused) && !document.fullscreenElement;
  window.electronAPI?.setCardMediaEscape?.(mediaCard ? current.id : null);
}

/** Resolve palette ownership from its actual trigger, skipping short menus. */
export function homeCardParentForTarget(target: EventTarget | null) {
  if (!(target instanceof Node)) return undefined;
  const owner = [...layers].reverse().find(layer => layer.active && layer.node.contains(target));
  if (!owner) return undefined;
  return owner.kind === "selection" ? owner.parentId : owner.id;
}

/** Measure the actual workspace once, then position every open card from that shared box. */
function positionLayers() {
  const measuredWorkspace = document.querySelector<HTMLElement>(".desktop-workspace")?.getBoundingClientRect();
  const contentElement = document.querySelector<HTMLElement>(".desktop-main-scroll");
  const measuredContent = contentElement?.getBoundingClientRect();
  const hasWorkArea = Boolean(measuredWorkspace && measuredWorkspace.width > 0 && measuredWorkspace.height > 0);
  const workspace = hasWorkArea ? measuredWorkspace : undefined;
  const hasVisibleMain = Boolean(measuredContent && measuredContent.width > 0 && measuredContent.height > 0);
  const mainArea = hasVisibleMain ? measuredContent : workspace;
  const left = mainArea?.left ?? 0;
  const right = mainArea?.right ?? window.innerWidth;
  const toolbarBottom = document.querySelector<HTMLElement>(".desktop-toolbar")?.getBoundingClientRect().bottom ?? 56;
  const phaseNav = contentElement?.querySelector<HTMLElement>('[aria-label="制作阶段"]')?.getBoundingClientRect();
  const contentTop = measuredContent?.top ?? 56;
  const top = Math.max(contentTop, toolbarBottom, phaseNav && phaseNav.bottom > contentTop && phaseNav.top < (measuredContent?.bottom ?? window.innerHeight) ? phaseNav.bottom : 0);
  const bottom = Math.min(mainArea?.bottom ?? window.innerHeight, workspace?.bottom ?? window.innerHeight);
  const width = Math.max(0, Math.min(360, right - left - 48));
  const rootTop = top + 16;
  const height = Math.max(0, Math.min(560, bottom - rootTop - 80));
  const positioned = new Map<string, CSSProperties>();

  for (const layer of layers) {
    const parentPosition = layer.parentId ? positioned.get(layer.parentId) : undefined;
    let position: CSSProperties;
    if (layer.kind === "selection") {
      const trigger = layer.anchor?.current?.getBoundingClientRect();
      const menuWidth = Math.max(0, Math.min(240, right - left - 24));
      const menuHeight = Math.max(32, Math.min(layer.node.scrollHeight || 240, bottom - top - 24));
      const menuLeft = trigger ? Math.max(left + 12, Math.min(trigger.right - menuWidth, right - menuWidth - 12)) : right - menuWidth - 16;
      const menuTop = trigger && trigger.bottom + 6 + menuHeight <= bottom - 12 ? trigger.bottom + 6 : Math.max(top + 12, (trigger?.top ?? top + 12) - menuHeight - 6);
      position = { width: menuWidth, left: menuLeft, top: menuTop, maxHeight: Math.max(0, bottom - top - 24) };
    } else if (parentPosition) {
      position = { width, height, left: Number(parentPosition.left) - 12, top: Number(parentPosition.top) + 32 };
    } else {
      position = { width, height, left: right - 16 - width, top: rootTop };
    }
    layer.position = position;
    positioned.set(layer.id, position);
    layer.setPosition(position);
  }
}

/** Object-card children portal out of the parent's scroll clip. */
export function HomeCardDialog({ title, children, onClose, kind = "modal", anchor, footer, open = true, parentLayerId }: {
  title: string; children: ReactNode; onClose: () => void; kind?: Kind;
  anchor?: RefObject<HTMLElement | null>; footer?: ReactNode;
  open?: boolean;
  parentLayerId?: string;
}) {
  const id = useId();
  const contextParent = useContext(ParentCard);
  const parent = parentLayerId ? layers.find(layer => layer.id === parentLayerId) ?? contextParent : contextParent;
  const [clientReady, setClientReady] = useState(false);
  useEffect(() => { setClientReady(true); }, []);
  const active = clientReady && open && parent?.active !== false;
  const modal = kind === "modal" || kind === "navigation" || kind === "confirmation";
  const blocksApplication = modal;
  const zIndex = parent ? parent.zIndex + 10 : modal ? 111 : kind === "detail" ? 80 : kind === "selection" ? 100 : 90;
  const node = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const close = useRef(onClose); close.current = onClose;
  const [layer, setLayer] = useState<Layer | null>(null);
  const [position, setPosition] = useState<CSSProperties>({ visibility: "hidden" });
  const anchorRef = useRef(anchor); anchorRef.current = anchor;
  useLayoutEffect(() => {
    if (!active) return;
    const card = node.current!;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const entry: Layer = {
      id, kind, active: true, zIndex, parentId: parent?.id, node: card,
      close: () => {
        layers.filter(value => value.parentId === id).forEach(value => value.close());
        close.current();
      },
      position: {}, setPosition, anchor: anchorRef.current,
    };
    // Replace a sibling, never its parent or the parent's scroll/draft state.
    layers.filter(value => value.parentId === entry.parentId && value.id !== id && (entry.parentId || value.kind === kind)).forEach(value => value.close());
    layers.push(entry); setLayer(entry);
    const measure = () => positionLayers();
    measure(); const frame = window.requestAnimationFrame(measure); window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    const workArea = document.querySelector<HTMLElement>(".desktop-workspace");
    const contentArea = document.querySelector<HTMLElement>(".desktop-main-scroll");
    const toolbar = document.querySelector<HTMLElement>(".desktop-toolbar");
    if (workArea) observer.observe(workArea);
    if (contentArea) observer.observe(contentArea);
    if (toolbar) observer.observe(toolbar);
    card.querySelector<HTMLElement>(kind === "confirmation" || kind === "modal" ? "[data-card-cancel], button" : "button, input, select, textarea")?.focus();
    const syncMediaFocus = () => { if (topLayer() === entry) syncDesktopMediaEscape(); };
    const releaseMediaEscape = window.electronAPI?.onCardMediaEscape?.((cardId) => {
      if (cardId === entry.id && topLayer() === entry && !document.fullscreenElement && document.activeElement instanceof HTMLMediaElement && card.contains(document.activeElement)) entry.close();
    });
    document.addEventListener("focusin", syncMediaFocus, true);
    document.addEventListener("focusout", syncMediaFocus, true);
    document.addEventListener("fullscreenchange", syncMediaFocus);
    syncMediaFocus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (topLayer() !== entry) return;
      if (event.key === "Escape") {
        if (card.querySelector('[data-app-select-popup][data-open]')) return;
        event.preventDefault(); event.stopImmediatePropagation(); entry.close(); return;
      }
      if (event.key !== "Tab" || !modal) return;
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
    const shell = document.querySelector<HTMLElement>(".desktop-shell");
    const wasShellInert = shell?.inert ?? false;
    const blockedLayers = blocksApplication ? layers.filter(value => value.id !== id) : [];
    const previousLayerInert = new Map(blockedLayers.map(value => [value.node, value.node.inert === true]));
    if (blocksApplication && shell) shell.inert = true;
    previousLayerInert.forEach((_inert, cardNode) => { cardNode.inert = true; });
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("scroll", fieldScroll, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("scroll", fieldScroll, true);
      document.removeEventListener("focusin", syncMediaFocus, true);
      document.removeEventListener("focusout", syncMediaFocus, true);
      document.removeEventListener("fullscreenchange", syncMediaFocus);
      releaseMediaEscape?.();
      const index = layers.indexOf(entry); if (index !== -1) layers.splice(index, 1);
      syncDesktopMediaEscape();
      previousLayerInert.forEach((inert, cardNode) => { cardNode.inert = inert; });
      if (shell && blocksApplication) shell.inert = wasShellInert || layers.some(value => value.kind === "modal" || value.kind === "navigation" || (value.kind === "confirmation" && !value.parentId));
      positionLayers();
    };
  }, [id, kind, parent, anchor, active, zIndex]);
  useEffect(() => {
    if (!active) return;
    const card = node.current!;
    const previousFocus = returnFocus.current;
    return () => {
      // React restores pre-commit selection after layout-effect cleanup.
      if (card.contains(document.activeElement) || document.activeElement === document.body) previousFocus?.focus();
    };
  }, [active]);
  useLayoutEffect(() => {
    if (!layer || !active) return;
    const target = kind === "selection" ? node.current?.querySelector<HTMLElement>('[role="option"][aria-selected="true"]') ?? node.current?.querySelector<HTMLElement>('[role="option"]') : node.current?.querySelector<HTMLElement>('[data-card-cancel]');
    target?.focus();
  }, [layer, kind, active]);
  const childContext = useMemo(() => layer ? { ...layer, active } : null, [layer, active]);
  const card = <ParentCard.Provider value={childContext}>
    <section hidden={!active} aria-label={title} aria-modal={blocksApplication ? true : undefined} className={`${styles.card} ${styles[kind]}`} ref={node} role="dialog" style={{ ...position, zIndex }} data-card-kind={kind}>
      <header><h2>{title}</h2><button aria-label="关闭卡片" onClick={onClose} type="button"><X size={16} /></button></header>
      <div className={styles.body}>{layer ? children : null}</div>
      {kind !== "selection" && footer ? <footer>{footer}</footer> : null}
    </section>
  </ParentCard.Provider>;
  return clientReady ? createPortal(modal ? <div hidden={!active} className={styles.modalShade} style={{ zIndex: zIndex - 1 }}>{card}</div> : card, document.body) : null;
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
