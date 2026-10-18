"use client";

// shadcn/ui base-nova Input/InputGroup (MIT), retrieved 2026-10-18.
// https://ui.shadcn.com/r/styles/base-nova/input-group.json
// https://ui.shadcn.com/r/styles/base-nova/input.json
// Official slots/primitive composition, mapped to CSS Modules and DESIGN tokens.
import type { ComponentProps } from "react";
import { Input as InputPrimitive } from "@base-ui/react/input";
import styles from "./workbench-controls.module.css";

export function InputGroup({ className = "", ...props }: ComponentProps<"div">) {
  return <div data-slot="input-group" role="group" className={`${styles.inputGroup} ${className}`} {...props} />;
}
export function InputGroupAddon({ className = "", align = "inline-start", ...props }: ComponentProps<"div"> & { align?: "inline-start" | "inline-end" }) {
  return <div data-slot="input-group-addon" data-align={align} className={`${styles.inputAddon} ${className}`} onClick={event => {
    if (!(event.target as HTMLElement).closest("button")) event.currentTarget.parentElement?.querySelector("input")?.focus();
  }} {...props} />;
}
export function InputGroupInput({ className = "", ...props }: ComponentProps<"input">) {
  return <InputPrimitive data-slot="input-group-control" className={`${styles.inputControl} ${className}`} {...props} />;
}
export function InputGroupTextarea({ className = "", ...props }: ComponentProps<"textarea">) {
  return <textarea data-slot="input-group-control" className={`${styles.inputControl} ${className}`} {...props} />;
}
