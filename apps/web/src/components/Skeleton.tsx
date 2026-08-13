import type { CSSProperties } from "react";
import { PixelLoadingState } from "@/components/PixelPrimitives";

export type SkeletonProps = {
  width?: string | number;
  height?: string | number;
  variant?: "block" | "text" | "circle";
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
};

export function Skeleton({
  width,
  height,
  variant = "block",
  className,
  style,
  ariaLabel,
}: SkeletonProps) {
  const inlineStyle: CSSProperties = {
    width,
    height,
    ...style,
  };
  const baseClass = `skeleton skeleton-${variant}${className ? ` ${className}` : ""}`;
  return (
    <span
      aria-hidden={ariaLabel ? undefined : true}
      aria-label={ariaLabel}
      className={baseClass}
      role={ariaLabel ? "status" : undefined}
      style={inlineStyle}
    />
  );
}

export type LoadingShellProps = {
  label?: string;
  children?: React.ReactNode;
};

export function LoadingShell({
  label = "加载中...",
  children,
}: LoadingShellProps) {
  return (
    <div aria-busy="true" aria-live="polite" className="loading-shell">
      <PixelLoadingState
        description="内容会在准备完成后自动更新，你可以继续停留在此页面。"
        title={label}
      />
      {children}
    </div>
  );
}

export type MetricSkeletonProps = {
  count?: number;
};

/**
 * @deprecated Card-wall loading presets are not part of the approved design
 * system. Existing pages may keep this temporarily; new pages must use a
 * skeleton that matches their final task geometry.
 */
export function MetricSkeletonRow({ count = 4 }: MetricSkeletonProps) {
  return (
    <div className="loading-metric-row">
      {Array.from({ length: count }).map((_, index) => (
        <div className="loading-metric-card" key={`metric-${index}`}>
          <Skeleton height={18} variant="circle" width={18} />
          <Skeleton height={26} width="60%" />
          <Skeleton height={10} width="40%" />
          <Skeleton height={6} width="90%" />
        </div>
      ))}
    </div>
  );
}

export type ListSkeletonProps = {
  count?: number;
  itemClassName?: string;
};

export function ListSkeleton({
  count = 4,
  itemClassName,
}: ListSkeletonProps) {
  return (
    <div className="loading-list">
      {Array.from({ length: count }).map((_, index) => (
        <div
          className={`loading-list-item${itemClassName ? ` ${itemClassName}` : ""}`}
          key={`list-${index}`}
        >
          <Skeleton height={18} variant="circle" width={18} />
          <div className="loading-list-item-body">
            <Skeleton height={14} width="55%" />
            <Skeleton height={10} width="80%" />
            <Skeleton height={10} width="40%" />
          </div>
          <Skeleton height={28} width={92} />
        </div>
      ))}
    </div>
  );
}

export type SidebarSkeletonCardsProps = {
  count?: number;
};

/**
 * @deprecated Generic sidebar card placeholders reinforce the old card wall.
 * Existing pages may keep this temporarily; new Inspector/loading views must
 * use a structure-specific skeleton.
 */
export function SidebarSkeletonCards({ count = 3 }: SidebarSkeletonCardsProps) {
  return (
    <div className="loading-side-list">
      {Array.from({ length: count }).map((_, index) => (
        <div className="loading-side-card" key={`side-${index}`}>
          <Skeleton height={12} width="40%" />
          <Skeleton height={20} width="60%" />
          <Skeleton height={10} width="80%" />
        </div>
      ))}
    </div>
  );
}
