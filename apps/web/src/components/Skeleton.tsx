import type { CSSProperties } from "react";

type SkeletonProps = {
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

type LoadingShellProps = {
  label?: string;
  children?: React.ReactNode;
};

export function LoadingShell({
  label = "加载中...",
  children,
}: LoadingShellProps) {
  return (
    <div aria-busy="true" aria-live="polite" className="loading-shell">
      <div className="loading-banner">
        <span aria-hidden="true" className="loading-cursor" />
        <strong>{label}</strong>
        <span aria-hidden="true" className="loading-cursor" />
      </div>
      {children}
    </div>
  );
}

type MetricSkeletonProps = {
  count?: number;
};

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

type ListSkeletonProps = {
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

export function SidebarSkeletonCards({ count = 3 }: { count?: number }) {
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