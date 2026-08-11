"use client";

import Link from "next/link";
import { useMemo } from "react";
import { PixelIcon } from "@/components/PixelIcon";

const STACK_LINE_PATTERN = /\s+at\s+/;
const FILE_PATH_PATTERN = /(?:\/(?:[\w.-]+\/)+[\w.-]+|\bat\b\s+\w+)/i;
const SECRET_PATTERN =
  /(api[_-]?key|secret|token|password|passwd|sk-[a-z0-9]|bearer\s+[a-z0-9._-]+)/i;

export type RouteErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
  title?: string;
  hint?: string;
  detail?: string;
};

export function sanitizeMessage(raw: string | undefined): string | null {
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (trimmed.length > 240) {
    return null;
  }
  if (STACK_LINE_PATTERN.test(trimmed)) {
    return null;
  }
  if (FILE_PATH_PATTERN.test(trimmed)) {
    return null;
  }
  if (SECRET_PATTERN.test(trimmed)) {
    return null;
  }
  return trimmed;
}

export function RouteError({
  error,
  reset,
  title,
  hint,
  detail,
}: RouteErrorProps) {
  const safeMessage = useMemo(
    () => sanitizeMessage(error.message),
    [error.message],
  );
  const safeDetail = useMemo(
    () => sanitizeMessage(detail),
    [detail],
  );
  const safeHint = hint ?? "请检查本地 API 是否启动，或稍后再试一次。";
  const heading = title ?? "出错了";

  return (
    <section
      aria-label="页面错误"
      className="route-error pixel-card"
      role="alert"
    >
      <header className="route-error-head">
        <span className="route-error-light" aria-hidden="true" />
        <div>
          <span className="section-kicker">错误</span>
          <h1>{heading}</h1>
        </div>
      </header>

      <p className="route-error-hint">{safeHint}</p>

      {safeMessage ? (
        <p className="route-error-message">{safeMessage}</p>
      ) : (
        <p className="route-error-message muted">
          该页面加载失败，未返回可展示的错误详情。
        </p>
      )}

      {safeDetail ? <p className="route-error-detail">{safeDetail}</p> : null}

      {error.digest ? (
        <p className="route-error-digest">
          <span>错误指纹</span>
          <code>{error.digest}</code>
        </p>
      ) : null}

      <div className="route-error-actions">
        <button className="pixel-button" onClick={reset} type="button">
          重试
        </button>
        <Link className="pixel-button secondary" href="/">
          返回工作台
        </Link>
      </div>

      <span aria-hidden="true" className="route-error-sprite">
        <PixelIcon name="check" />
      </span>
    </section>
  );
}