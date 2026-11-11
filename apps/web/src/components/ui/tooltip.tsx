"use client";

import { Tooltip as Primitive } from "@base-ui/react/tooltip";
import { cloneElement, useId, useState, type ReactElement, type ReactNode } from "react";
import styles from "./workbench-controls.module.css";

/** Shared hover and keyboard help, positioned outside clipped editor panels. */
export function Tooltip({ children, label, side = "bottom" }: {
  children: ReactElement; label: ReactNode; side?: Primitive.Positioner.Props["side"];
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const child = children as ReactElement<{ disabled?: boolean; "aria-label"?: string; "aria-describedby"?: string }>;
  const control = child.props;
  const describedBy = [control["aria-describedby"], id].filter(Boolean).join(" ");
  // Keep the same native control mounted when its availability changes.
  const trigger = <span className={styles.tooltipAnchor} tabIndex={control.disabled ? 0 : undefined} aria-label={control.disabled ? control["aria-label"] : undefined}
    onFocusCapture={() => setOpen(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    {cloneElement(child, { "aria-describedby": describedBy })}
  </span>;
  return <Primitive.Root open={open} onOpenChange={setOpen}>
    <Primitive.Trigger delay={400} render={trigger} aria-describedby={id} />
    <Primitive.Portal><Primitive.Positioner side={side} sideOffset={7} collisionPadding={8} className={styles.tooltipPositioner}>
      <Primitive.Popup id={id} role="tooltip" className={styles.tooltip}>{label}</Primitive.Popup>
    </Primitive.Positioner></Primitive.Portal>
  </Primitive.Root>;
}
