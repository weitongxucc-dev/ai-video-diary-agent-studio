"use client";

// shadcn/ui Base Nova Button (MIT); reviewed source:
// https://ui.shadcn.com/r/styles/base-nova/button.json
// designs/yaohe-desktop-language-20261020/candidate-v2/src/registry/base-nova/ui/button.tsx
// Keep the existing Base UI props; map variant states to DESIGN.md tokens and 13/21 typography.
import { Button as ButtonPrimitive } from "@base-ui/react/button";
import { buttonClassName, type ButtonVariant } from "./button-styles";

export function Button({ className = "", variant = "default", size = "default", ...props }: ButtonPrimitive.Props & {
  variant?: ButtonVariant;
  size?: "default" | "icon";
}) {
  return <ButtonPrimitive data-slot="button" data-variant={variant} data-size={size} className={typeof className === "function"
    ? state => buttonClassName({ variant, size, className: className(state) })
    : buttonClassName({ variant, size, className })} {...props} />;
}
