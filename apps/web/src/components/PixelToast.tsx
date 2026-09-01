"use client";

import {
  Suspense,
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type ToastKind = "success" | "error" | "info";

export type ToastEntry = {
  id: string;
  kind: ToastKind;
  message: string;
  createdAt: number;
};

export type ToastInput = {
  kind?: ToastKind;
  message: string;
  durationMs?: number;
};

type ToastContextValue = {
  toasts: ToastEntry[];
  pushToast: (input: ToastInput) => string;
  dismissToast: (id: string) => void;
  clearToasts: () => void;
};

const DEFAULT_DURATION_MS = 3000;
const MAX_VISIBLE = 5;

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const counterRef = useRef(0);

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  const pushToast = useCallback(
    (input: ToastInput): string => {
      counterRef.current += 1;
      const id = `toast-${counterRef.current}-${Date.now()}`;
      const entry: ToastEntry = {
        id,
        kind: input.kind ?? "info",
        message: input.message,
        createdAt: Date.now(),
      };
      setToasts((current) => {
        const next = [...current, entry];
        return next.length > MAX_VISIBLE ? next.slice(-MAX_VISIBLE) : next;
      });
      const duration = input.durationMs ?? DEFAULT_DURATION_MS;
      if (duration > 0 && typeof window !== "undefined") {
        window.setTimeout(() => dismissToast(id), duration);
      }
      return id;
    },
    [dismissToast],
  );

  const value = useMemo<ToastContextValue>(
    () => ({ toasts, pushToast, dismissToast, clearToasts }),
    [toasts, pushToast, dismissToast, clearToasts],
  );

  return (
    <ToastContext.Provider value={value}>
      <Suspense fallback={null}>
        <ToastSearchBridge />
      </Suspense>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismissToast} />
    </ToastContext.Provider>
  );
}

function ToastSearchBridge() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const { pushToast } = useToast();
  const lastToastKey = useRef<string | null>(null);

  useEffect(() => {
    if (!searchParams) {
      return;
    }
    const rawKind = searchParams.get("toast");
    const rawMessage = searchParams.get("toast_message");
    if (!rawKind || !rawMessage) {
      return;
    }
    if (
      rawKind !== "success" &&
      rawKind !== "error" &&
      rawKind !== "info"
    ) {
      return;
    }
    const key = `${rawKind}:${rawMessage}`;
    if (lastToastKey.current === key) {
      return;
    }
    lastToastKey.current = key;
    pushToast({ kind: rawKind, message: rawMessage });
    const next = new URLSearchParams(searchParams.toString());
    next.delete("toast");
    next.delete("toast_message");
    const query = next.toString();
    const target = query.length > 0 ? `${pathname}?${query}` : pathname;
    router.replace(target, { scroll: false });
  }, [searchParams, pathname, pushToast, router]);

  return null;
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used inside <ToastProvider>");
  }
  return context;
}

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: ToastEntry[];
  onDismiss: (id: string) => void;
}) {
  if (toasts.length === 0) {
    return null;
  }
  return (
    <div aria-label="通知" className="ui-toast-viewport" role="status">
      {toasts.map((toast) => (
        <PixelToast
          key={toast.id}
          kind={toast.kind}
          message={toast.message}
          onDismiss={() => onDismiss(toast.id)}
        />
      ))}
    </div>
  );
}

type PixelToastProps = {
  kind: ToastKind;
  message: string;
  onDismiss: () => void;
};

export function PixelToast({ kind, message, onDismiss }: PixelToastProps) {
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    timerRef.current = window.setTimeout(onDismiss, DEFAULT_DURATION_MS);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [onDismiss]);

  return (
    <div
      aria-live="polite"
      className={`ui-toast ${kind}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <span aria-hidden="true" className={`ui-toast-light ${kind}`} />
      <div className="ui-toast-body">
        <strong>{kindLabel(kind)}</strong>
        <span>{message}</span>
      </div>
      <button
        aria-label="关闭通知"
        className="ui-toast-close"
        onClick={onDismiss}
        type="button"
      >
        ×
      </button>
    </div>
  );
}

function kindLabel(kind: ToastKind): string {
  if (kind === "success") return "成功";
  if (kind === "error") return "出错";
  return "提示";
}