"use client";

import {
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

type ChildAriaProps = {
  disabled?: boolean;
  "aria-label"?: string;
  "aria-describedby"?: string;
};

type PixelTooltipProps = {
  label: string;
  children: ReactElement<ChildAriaProps>;
};

export function PixelTooltip({ label, children }: PixelTooltipProps) {
  if (!isValidElement<ChildAriaProps>(children)) {
    return <>{children}</>;
  }
  const original = children;
  const existingLabel = original.props["aria-label"];
  const mergedProps: ChildAriaProps = {
    ...original.props,
    "aria-label": existingLabel ?? label,
    "aria-describedby": `pixel-tooltip-${slugify(label)}`,
  };
  return (
    <span className="pixel-tooltip-wrap">
      {cloneElement(original, mergedProps)}
      <span
        className="pixel-tooltip-bubble"
        id={`pixel-tooltip-${slugify(label)}`}
        role="tooltip"
      >
        <span aria-hidden="true" className="pixel-tooltip-arrow" />
        <span className="pixel-tooltip-text">{label}</span>
      </span>
    </span>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export function PixelTooltipText({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <span className="pixel-tooltip-wrap inline">
      <span
        aria-describedby={`pixel-tooltip-${slugify(label)}`}
        className="pixel-tooltip-trigger"
      >
        {children}
      </span>
      <span
        className="pixel-tooltip-bubble"
        id={`pixel-tooltip-${slugify(label)}`}
        role="tooltip"
      >
        <span aria-hidden="true" className="pixel-tooltip-arrow" />
        <span className="pixel-tooltip-text">{label}</span>
      </span>
    </span>
  );
}