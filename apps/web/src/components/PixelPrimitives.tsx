"use client";

import Link from "next/link";
import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

export type PixelStatusKind =
  | "idle"
  | "running"
  | "waiting_for_user"
  | "succeeded"
  | "failed"
  | "blocked";

const statusLabels: Record<PixelStatusKind, string> = {
  blocked: "已阻断",
  failed: "失败",
  idle: "待处理",
  running: "进行中",
  succeeded: "已完成",
  waiting_for_user: "等待确认",
};

export type PixelPanelProps = {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside";
  headingId?: string;
};

export function PixelPanel({
  title,
  description,
  actions,
  children,
  className,
  as: Component = "section",
  headingId,
}: PixelPanelProps) {
  const generatedHeadingId = useId();
  const resolvedHeadingId = headingId ?? `${generatedHeadingId}-heading`;

  return (
    <Component
      aria-labelledby={title ? resolvedHeadingId : undefined}
      className={`pixel-panel${className ? ` ${className}` : ""}`}
    >
      {title || description || actions ? (
        <header className="pixel-panel__header">
          <div>
            {title ? <h2 id={resolvedHeadingId}>{title}</h2> : null}
            {description ? <p>{description}</p> : null}
          </div>
          {actions ? <div className="pixel-panel__actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="pixel-panel__body">{children}</div>
    </Component>
  );
}

export type PixelButtonTone = "primary" | "secondary" | "danger";

export type PixelButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: PixelButtonTone;
  size?: "default" | "compact";
  loading?: boolean;
};

export const PixelButton = forwardRef<HTMLButtonElement, PixelButtonProps>(
  function PixelButton(
    {
      children,
      className,
      disabled,
      loading = false,
      size = "default",
      type = "button",
      variant = "secondary",
      ...props
    },
    ref,
  ) {
    return (
      <button
        {...props}
        aria-busy={loading || undefined}
        className={`pixel-button pixel-button--${variant} pixel-button--${size}${className ? ` ${className}` : ""}`}
        disabled={disabled || loading}
        ref={ref}
        type={type}
      >
        {loading ? <span aria-hidden="true" className="pixel-button__busy" /> : null}
        <span>{children}</span>
      </button>
    );
  },
);

export type PixelInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> & {
  label: string;
  hint?: string;
  error?: string;
};

export const PixelInput = forwardRef<HTMLInputElement, PixelInputProps>(
  function PixelInput(
    { className, error, hint, id, label, ...props },
    ref,
  ) {
    const generatedId = useId();
    const inputId = id ?? generatedId;
    const messageId = `${inputId}-message`;

    return (
      <div className={`pixel-field${error ? " is-invalid" : ""}${className ? ` ${className}` : ""}`}>
        <label className="pixel-field__label" htmlFor={inputId}>{label}</label>
        <input
          {...props}
          aria-describedby={hint || error ? messageId : undefined}
          aria-invalid={error ? true : undefined}
          className="pixel-input"
          id={inputId}
          ref={ref}
        />
        {error || hint ? (
          <span className="pixel-field__message" id={messageId}>
            {error ?? hint}
          </span>
        ) : null}
      </div>
    );
  },
);

export type PixelStatusProps = {
  kind: PixelStatusKind;
  label?: string;
  className?: string;
};

export function PixelStatus({
  kind,
  label,
  className,
}: PixelStatusProps) {
  return (
    <span
      className={`pixel-status pixel-status--${kind}${className ? ` ${className}` : ""}`}
      data-status={kind}
    >
      <span aria-hidden="true" className="pixel-status__mark" />
      <span>{label ?? statusLabels[kind]}</span>
    </span>
  );
}

export type PixelStateAction =
  | {
      kind: "button";
      label: string;
      tone?: PixelButtonTone;
      onClick: () => void;
    }
  | {
      kind: "link";
      label: string;
      tone?: PixelButtonTone;
      href: string;
    };

export type PixelStateProps = {
  title: string;
  description: string;
  action?: PixelStateAction;
  secondaryAction?: PixelStateAction;
  children?: ReactNode;
  className?: string;
};

export type PixelEmptyStateProps = PixelStateProps;
export type PixelErrorStateProps = PixelStateProps;
export type PixelBlockedStateProps = PixelStateProps;
export type PixelLoadingStateProps = Omit<
  PixelStateProps,
  "action" | "secondaryAction"
>;

function PixelStateActionControl({
  action,
  defaultTone,
}: {
  action: PixelStateAction;
  defaultTone: PixelButtonTone;
}) {
  const tone = action.tone ?? defaultTone;

  if (action.kind === "link") {
    return (
      <Link
        className={`pixel-button pixel-button--${tone} pixel-button--default`}
        href={action.href}
      >
        {action.label}
      </Link>
    );
  }

  return (
    <PixelButton onClick={action.onClick} variant={tone}>
      {action.label}
    </PixelButton>
  );
}

function PixelState({
  kind,
  title,
  description,
  action,
  secondaryAction,
  children,
  className,
  role,
}: PixelStateProps & {
  kind: PixelStatusKind;
  role?: "alert" | "status";
}) {
  return (
    <section
      className={`pixel-state pixel-state--${kind}${className ? ` ${className}` : ""}`}
      role={role}
    >
      <PixelStatus kind={kind} />
      <div className="pixel-state__copy">
        <h2>{title}</h2>
        <p>{description}</p>
        {children}
      </div>
      {action || secondaryAction ? (
        <div className="pixel-state__actions">
          {action ? (
            <PixelStateActionControl action={action} defaultTone="primary" />
          ) : null}
          {secondaryAction ? (
            <PixelStateActionControl
              action={secondaryAction}
              defaultTone="secondary"
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function PixelEmptyState(props: PixelEmptyStateProps) {
  return <PixelState {...props} kind="idle" />;
}

export function PixelErrorState(props: PixelErrorStateProps) {
  return <PixelState {...props} kind="failed" role="alert" />;
}

export function PixelLoadingState(props: PixelLoadingStateProps) {
  return <PixelState {...props} kind="running" role="status" />;
}

export function PixelBlockedState(props: PixelBlockedStateProps) {
  return <PixelState {...props} kind="blocked" role="alert" />;
}

export type PixelScrollableRegionProps = {
  label: string;
  children: ReactNode;
  className?: string;
  hint?: string;
  showControls?: boolean;
};

export function PixelScrollableRegion({
  label,
  children,
  className,
  hint = "可横向滚动查看更多",
  showControls = true,
}: PixelScrollableRegionProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<"start" | "middle" | "end" | "static">("static");

  const updatePosition = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport || viewport.scrollWidth <= viewport.clientWidth + 1) {
      setPosition("static");
      return;
    }
    if (viewport.scrollLeft <= 1) {
      setPosition("start");
      return;
    }
    if (viewport.scrollLeft + viewport.clientWidth >= viewport.scrollWidth - 1) {
      setPosition("end");
      return;
    }
    setPosition("middle");
  }, []);

  useEffect(() => {
    updatePosition();
    window.addEventListener("resize", updatePosition);
    const viewport = viewportRef.current;
    const content = viewport?.firstElementChild;
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updatePosition);
    if (viewport) {
      resizeObserver?.observe(viewport);
    }
    if (content) {
      resizeObserver?.observe(content);
    }
    return () => {
      window.removeEventListener("resize", updatePosition);
      resizeObserver?.disconnect();
    };
  }, [updatePosition]);

  const scrollToPosition = useCallback(
    (next: "start" | "end" | "previous" | "next") => {
      const viewport = viewportRef.current;
      if (!viewport) {
        return;
      }
      const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
      const pageSize = Math.max(32, Math.round(viewport.clientWidth * 0.8));
      const nextLeft =
        next === "start"
          ? 0
          : next === "end"
            ? maxScroll
            : next === "previous"
              ? Math.max(0, viewport.scrollLeft - pageSize)
              : Math.min(maxScroll, viewport.scrollLeft + pageSize);
      viewport.scrollTo({
        left: nextLeft,
        behavior: next === "start" || next === "end" ? "auto" : "smooth",
      });
      window.requestAnimationFrame(updatePosition);
    },
    [updatePosition],
  );

  const hasOverflow = position !== "static";

  return (
    <section
      className={`pixel-scrollable-region${className ? ` ${className}` : ""}`}
      data-scroll-position={position}
    >
      <div
        aria-label={label}
        className="pixel-scrollable-region__viewport"
        data-scroll-region="local-x"
        onKeyDown={(event) => {
          if (event.key === "Home") {
            event.preventDefault();
            scrollToPosition("start");
          }
          if (event.key === "End") {
            event.preventDefault();
            scrollToPosition("end");
          }
        }}
        onScroll={updatePosition}
        ref={viewportRef}
        role="region"
        tabIndex={0}
      >
        {children}
      </div>
      {hasOverflow ? (
        <div className="pixel-scrollable-region__footer">
          <p aria-live="polite" className="pixel-scrollable-region__hint">
            {hint}
          </p>
          {showControls ? (
            <div aria-label={`${label}滚动控制`} className="pixel-scrollable-region__controls">
              <PixelButton
                aria-label={`向左滚动${label}`}
                disabled={position === "start"}
                onClick={() => scrollToPosition("previous")}
                size="compact"
              >
                ←
              </PixelButton>
              <PixelButton
                aria-label={`向右滚动${label}`}
                disabled={position === "end"}
                onClick={() => scrollToPosition("next")}
                size="compact"
              >
                →
              </PixelButton>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
