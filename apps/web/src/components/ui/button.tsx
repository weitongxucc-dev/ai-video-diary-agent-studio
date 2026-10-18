"use client";

// shadcn/ui base-nova Button (MIT), CSS/token adaptation of the official source:
// https://ui.shadcn.com/r/styles/base-nova/button.json (2026-10-18).
import { Button as ButtonPrimitive } from "@base-ui/react/button";
import styles from "./workbench-controls.module.css";

export function Button({ className = "", variant = "default", size = "default", ...props }: ButtonPrimitive.Props & {
  variant?: "default" | "outline" | "secondary" | "ghost" | "destructive" | "link";
  size?: "default" | "icon";
}) {
  return <ButtonPrimitive data-slot="button" data-variant={variant} data-size={size} className={`${styles.button} ${className}`} {...props} />;
}
