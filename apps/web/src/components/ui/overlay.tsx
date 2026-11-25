"use client";

import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { AlertDialog as BaseAlertDialog } from "@base-ui/react/alert-dialog";
import { Popover as BasePopover } from "@base-ui/react/popover";
import { X } from "lucide-react";
import { useContext, useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { MediaVisibilityContext } from "./media-visibility";
import styles from "../SideCards.module.css";

export type OverlayProps = {
  title: string; children: ReactNode; footer?: ReactNode; open: boolean;
  onClose: () => void; anchor?: RefObject<HTMLElement | null>;
};

// The desktop window holds one registration, even when multiple cards are open.
let mediaEscapeOwner: string | null = null;

export function useMediaEscape<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const id = useId();
  const node = useRef<T>(null);
  useEffect(() => {
    const { setCardMediaEscape, onCardMediaEscape } = window.electronAPI ?? {};
    if (!open || !setCardMediaEscape || !onCardMediaEscape) return;
    const hasFocusedMedia = () => {
      const focused = document.activeElement;
      return focused instanceof HTMLMediaElement && node.current?.contains(focused)
        && !focused.closest("[hidden]") && !document.fullscreenElement;
    };
    const release = () => {
      if (mediaEscapeOwner !== id) return;
      mediaEscapeOwner = null;
      setCardMediaEscape(null);
    };
    const sync = () => {
      if (!hasFocusedMedia()) {
        release();
      } else if (mediaEscapeOwner !== id) {
        mediaEscapeOwner = id;
        setCardMediaEscape(id);
      }
    };
    document.addEventListener("focusin", sync);
    document.addEventListener("focusout", release);
    document.addEventListener("fullscreenchange", sync);
    const unsubscribe = onCardMediaEscape(value => {
      if (value === id && mediaEscapeOwner === id && hasFocusedMedia()) onClose();
    });
    sync();
    return () => {
      document.removeEventListener("focusin", sync);
      document.removeEventListener("focusout", release);
      document.removeEventListener("fullscreenchange", sync);
      unsubscribe();
      release();
    };
  }, [id, open, onClose]);
  return { id, node };
}

export function Dialog({ title, children, footer, open, onClose, anchor, sheet = false }: OverlayProps & { sheet?: boolean }) {
  const { id, node } = useMediaEscape<HTMLDivElement>(open, onClose);
  const visible = useContext(MediaVisibilityContext) && open;
  return <BaseDialog.Root open={open} modal={!sheet} disablePointerDismissal={sheet} onOpenChange={(next, details) => {
    if (document.fullscreenElement && details.reason === "escape-key") { details.cancel(); return; }
    if (!next) onClose();
  }}>
    <BaseDialog.Portal keepMounted>
      {!sheet ? <BaseDialog.Backdrop className={styles.modalShade} /> : null}
      <BaseDialog.Popup ref={node} aria-modal={sheet ? undefined : true} finalFocus={anchor} className={`${styles.card} ${sheet ? styles.sheet : styles.modal}`} data-overlay-id={id}>
        <header><BaseDialog.Title>{title}</BaseDialog.Title><BaseDialog.Close aria-label="关闭卡片"><X size={16}/></BaseDialog.Close></header>
        <MediaVisibilityContext.Provider value={visible}><div className={styles.body}>{children}</div></MediaVisibilityContext.Provider>
        {footer ? <footer>{footer}</footer> : null}
      </BaseDialog.Popup>
    </BaseDialog.Portal>
  </BaseDialog.Root>;
}

export function Sheet(props: OverlayProps) { return <Dialog {...props} sheet/>; }

export function AlertDialog({ title, children, footer, open, onClose, anchor }: OverlayProps) {
  const popup = useRef<HTMLDivElement>(null);
  const visible = useContext(MediaVisibilityContext) && open;
  return <BaseAlertDialog.Root open={open} onOpenChange={(next, details) => {
    if (details.reason === "escape-key" || details.reason === "outside-press") { details.cancel(); return; }
    if (!next) onClose();
  }}>
    <BaseAlertDialog.Portal keepMounted>
      <BaseAlertDialog.Backdrop className={styles.modalShade}/>
      <BaseAlertDialog.Popup ref={popup} aria-modal="true" className={`${styles.card} ${styles.modal}`} finalFocus={anchor}
        initialFocus={() => popup.current?.querySelector<HTMLElement>("[data-card-cancel]") ?? popup.current?.querySelector<HTMLElement>("button") ?? true}>
        <header><BaseAlertDialog.Title>{title}</BaseAlertDialog.Title></header>
        <MediaVisibilityContext.Provider value={visible}><div className={styles.body}>{children}</div></MediaVisibilityContext.Provider>
        {footer ? <footer>{footer}</footer> : null}
      </BaseAlertDialog.Popup>
    </BaseAlertDialog.Portal>
  </BaseAlertDialog.Root>;
}

export function Popover({ title, children, footer, open, onClose, anchor }: OverlayProps) {
  const visible = useContext(MediaVisibilityContext) && open;
  return <BasePopover.Root open={open} onOpenChange={next => { if (!next) onClose(); }}>
    <BasePopover.Portal keepMounted>
      <BasePopover.Positioner anchor={anchor} side="bottom" align="end" sideOffset={6} collisionPadding={12} className={styles.positioner}>
        <BasePopover.Popup className={`${styles.card} ${styles.selection}`} finalFocus={anchor}>
          <header><BasePopover.Title>{title}</BasePopover.Title><BasePopover.Close aria-label="关闭卡片"><X size={16}/></BasePopover.Close></header>
          <MediaVisibilityContext.Provider value={visible}><div className={styles.body}>{children}</div></MediaVisibilityContext.Provider>
          {footer ? <footer>{footer}</footer> : null}
        </BasePopover.Popup>
      </BasePopover.Positioner>
    </BasePopover.Portal>
  </BasePopover.Root>;
}
