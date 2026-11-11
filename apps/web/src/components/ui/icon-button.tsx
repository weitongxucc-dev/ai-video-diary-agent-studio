"use client";

import type { ComponentProps } from "react";
import { Button } from "./button";
import { Tooltip } from "./tooltip";

export function IconButton({ label, description, shortcut, ...props }: Omit<ComponentProps<typeof Button>, "aria-label" | "title"> & {
  label: string; description?: string; shortcut?: string;
}) {
  return <Tooltip label={<><span>{description ?? label}</span>{shortcut ? <kbd>{shortcut}</kbd> : null}</>}>
    <Button variant="ghost" size="icon" {...props} aria-label={label} />
  </Tooltip>;
}
