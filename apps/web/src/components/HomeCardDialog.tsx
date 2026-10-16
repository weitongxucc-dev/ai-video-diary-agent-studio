"use client";

import { ChevronDown } from "lucide-react";
import { useRef, useState, type ReactNode, type RefObject } from "react";
import { AlertDialog, Dialog, Popover, Sheet } from "./ui/overlay";
import styles from "./SideCards.module.css";

type Kind = "detail" | "config" | "confirmation" | "selection" | "modal" | "navigation";

export function homeCardParentForTarget(target: EventTarget | null) {
  return target instanceof Element ? target.closest<HTMLElement>("[data-overlay-id]")?.dataset.overlayId : undefined;
}

/** Existing callers map their content role to the standard Base UI primitive. */
export function HomeCardDialog({ kind = "modal", open = true, parentLayerId: _parentLayerId, ...props }: {
  title: string; children: ReactNode; onClose: () => void; kind?: Kind;
  anchor?: RefObject<HTMLElement | null>; footer?: ReactNode; open?: boolean; parentLayerId?: string;
}) {
  const Component = kind === "selection" ? Popover : kind === "confirmation" ? AlertDialog : kind === "detail" ? Sheet : Dialog;
  return <Component {...props} open={open}/>;
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
