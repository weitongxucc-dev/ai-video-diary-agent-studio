import styles from "./workbench-controls.module.css";

export type ButtonVariant = "default" | "outline" | "secondary" | "ghost" | "destructive" | "link";

/** The same control appearance for native buttons, links, and Base UI buttons. */
export function buttonClassName({ variant = "default", size = "default", className = "" }: {
  variant?: ButtonVariant; size?: "default" | "compact" | "icon"; className?: string;
} = {}) {
  return [styles.button, variant !== "default" && styles[variant], size !== "default" && styles[size], className].filter(Boolean).join(" ");
}
