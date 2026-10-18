"use client";

// Adapted from shadcn/ui base-nova DropdownMenu (MIT), 2026-10-18:
// https://ui.shadcn.com/r/styles/base-nova/dropdown-menu.json
import { Menu as MenuPrimitive } from "@base-ui/react/menu";
import styles from "./workbench-controls.module.css";
export const DropdownMenu = MenuPrimitive.Root;
export const DropdownMenuTrigger = MenuPrimitive.Trigger;
export function DropdownMenuContent({ align = "start", side = "bottom", sideOffset = 4, className = "", ...props }: MenuPrimitive.Popup.Props & Pick<MenuPrimitive.Positioner.Props, "align" | "side" | "sideOffset">) {
  return <MenuPrimitive.Portal><MenuPrimitive.Positioner align={align} side={side} sideOffset={sideOffset} collisionPadding={8} className={styles.menuPositioner}>
    <MenuPrimitive.Popup data-slot="dropdown-menu-content" className={`${styles.menu} ${className}`} {...props} />
  </MenuPrimitive.Positioner></MenuPrimitive.Portal>;
}
export function DropdownMenuItem({ className = "", ...props }: MenuPrimitive.Item.Props) {
  return <MenuPrimitive.Item data-slot="dropdown-menu-item" className={`${styles.menuItem} ${className}`} {...props} />;
}
