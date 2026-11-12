"use client";

// AI Elements' header/content/step composition, adapted to the existing Base UI
// primitives and DESIGN tokens. Only real public activity is supplied by callers.
// Reference: https://elements.ai-sdk.dev/components/chain-of-thought
import { Collapsible } from "@base-ui/react/collapsible";
import { ChevronDown, Circle, type LucideIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import styles from "./chain-of-thought.module.css";

export function ChainOfThought({ className = "", ...props }: Collapsible.Root.Props) {
  return <Collapsible.Root className={`${styles.root} ${className}`} {...props}/>;
}

export function ChainOfThoughtHeader({ children, ...props }: Collapsible.Trigger.Props) {
  return <Collapsible.Trigger className={styles.trigger} {...props}>
    {children}<ChevronDown aria-hidden className={styles.chevron} size={14}/>
  </Collapsible.Trigger>;
}

export function ChainOfThoughtContent({ children }: { children: ReactNode }) {
  return <Collapsible.Panel keepMounted className={styles.content}><ol className={styles.steps}>{children}</ol></Collapsible.Panel>;
}

export function ChainOfThoughtStep({ icon: Icon = Circle, label, status = "complete", children, ...props }: Omit<ComponentProps<"li">, "title"> & {
  icon?: LucideIcon; label: ReactNode; status?: "complete" | "active" | "pending" | "error";
}) {
  return <li className={styles.step} data-state={status} {...props}>
    <span className={styles.marker}><Icon aria-hidden size={14} className={status === "active" ? "is-spinning" : undefined}/></span>
    <div className={styles.copy}><div className={styles.label}>{label}</div>{children ? <div className={styles.description}>{children}</div> : null}</div>
  </li>;
}
