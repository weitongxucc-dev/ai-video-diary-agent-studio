"use client";

import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { AlertDialog as BaseAlertDialog } from "@base-ui/react/alert-dialog";
import { Popover as BasePopover } from "@base-ui/react/popover";
import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import styles from "../SideCards.module.css";

export type OverlayProps = {
  title: string; children: ReactNode; footer?: ReactNode; open: boolean;
  onClose: () => void; anchor?: RefObject<HTMLElement | null>;
};

function useMediaEscape(open: boolean, onClose: () => void) {
  const id = useId();
  const node = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const sync = () => {
      const focused = document.activeElement;
      const selected = focused instanceof HTMLMediaElement && node.current?.contains(focused) && !document.fullscreenElement;
      window.electronAPI?.setCardMediaEscape?.(selected ? id : null);
    };
    document.addEventListener("focusin", sync);
    document.addEventListener("fullscreenchange", sync);
    const unsubscribe = window.electronAPI?.onCardMediaEscape?.(value => {
      if (value === id && !document.fullscreenElement && document.activeElement instanceof HTMLMediaElement
          && node.current?.contains(document.activeElement)) onClose();
    });
    return () => { document.removeEventListener("focusin", sync); document.removeEventListener("fullscreenchange", sync); unsubscribe?.(); window.electronAPI?.setCardMediaEscape?.(null); };
  }, [id, open, onClose]);
  return { id, node };
}

export function Dialog({ title, children, footer, open, onClose, anchor, sheet = false }: OverlayProps & { sheet?: boolean }) {
  const { id, node } = useMediaEscape(open, onClose);
  return <BaseDialog.Root open={open} modal={!sheet} disablePointerDismissal={sheet} onOpenChange={(next, details) => {
    if (document.fullscreenElement && details.reason === "escape-key") { details.cancel(); return; }
    if (!next) onClose();
  }}>
    <BaseDialog.Portal keepMounted>
      {!sheet ? <BaseDialog.Backdrop className={styles.modalShade} /> : null}
      <BaseDialog.Popup ref={node} aria-modal={sheet ? undefined : true} finalFocus={anchor} className={`${styles.card} ${sheet ? styles.sheet : styles.modal}`} data-overlay-id={id}>
        <header><BaseDialog.Title>{title}</BaseDialog.Title><BaseDialog.Close aria-label="关闭卡片"><X size={16}/></BaseDialog.Close></header>
        <div className={styles.body}>{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </BaseDialog.Popup>
    </BaseDialog.Portal>
  </BaseDialog.Root>;
}

export function Sheet(props: OverlayProps) { return <Dialog {...props} sheet/>; }

export function AlertDialog({ title, children, footer, open, onClose, anchor }: OverlayProps) {
  const popup = useRef<HTMLDivElement>(null);
  return <BaseAlertDialog.Root open={open} onOpenChange={(next, details) => {
    if (details.reason === "escape-key" || details.reason === "outside-press") { details.cancel(); return; }
    if (!next) onClose();
  }}>
    <BaseAlertDialog.Portal keepMounted>
      <BaseAlertDialog.Backdrop className={styles.modalShade}/>
      <BaseAlertDialog.Popup ref={popup} aria-modal="true" className={`${styles.card} ${styles.modal}`} finalFocus={anchor}
        initialFocus={() => popup.current?.querySelector<HTMLElement>("[data-card-cancel]") ?? popup.current?.querySelector<HTMLElement>("button") ?? true}>
        <header><BaseAlertDialog.Title>{title}</BaseAlertDialog.Title></header>
        <div className={styles.body}>{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </BaseAlertDialog.Popup>
    </BaseAlertDialog.Portal>
  </BaseAlertDialog.Root>;
}

export function Popover({ title, children, footer, open, onClose, anchor }: OverlayProps) {
  return <BasePopover.Root open={open} onOpenChange={next => { if (!next) onClose(); }}>
    <BasePopover.Portal keepMounted>
      <BasePopover.Positioner anchor={anchor} side="bottom" align="end" sideOffset={6} collisionPadding={12} className={styles.positioner}>
        <BasePopover.Popup className={`${styles.card} ${styles.selection}`} finalFocus={anchor}>
          <header><BasePopover.Title>{title}</BasePopover.Title><BasePopover.Close aria-label="关闭卡片"><X size={16}/></BasePopover.Close></header>
          <div className={styles.body}>{children}</div>
          {footer ? <footer>{footer}</footer> : null}
        </BasePopover.Popup>
      </BasePopover.Positioner>
    </BasePopover.Portal>
  </BasePopover.Root>;
}
