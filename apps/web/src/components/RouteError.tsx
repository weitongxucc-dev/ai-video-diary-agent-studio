"use client";

import { useMemo } from "react";
import { PixelErrorState } from "@/components/PixelPrimitives";

const STACK_LINE_PATTERN = /\s+at\s+/;
const FILE_PATH_PATTERN = /(?:\/(?:[\w.-]+\/)+[\w.-]+|\bat\b\s+\w+)/i;
const SECRET_PATTERN =
  /(api[_-]?key|secret|token|password|passwd|sk-[a-z0-9]|bearer\s+[a-z0-9._-]+)/i;

export type RouteErrorCategory =
  | "generic"
  | "network"
  | "permission"
  | "configuration"
  | "dependency";

export type RouteErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
  title?: string;
  errorCategory?: RouteErrorCategory;
  domainHint?: string;
  /** @deprecated Prefer domainHint for new callers. */
  hint?: string;
  detail?: string;
};

const categoryHints: Record<RouteErrorCategory, string> = {
  configuration: "当前配置不足以完成此页面。请检查相关设置后重试。",
  dependency: "完成此页面所需的本机依赖当前不可用。请检查运行状态后重试。",
  generic: "当前页面暂时无法完成加载。请重试；若问题持续，可展开技术详情用于排查。",
  network: "当前页面暂时无法连接所需服务。请检查连接后重试。",
  permission: "当前页面缺少完成操作所需的访问权限。请检查授权后重试。",
};

const permissionPattern = /(?:权限|授权|无权|permission|forbidden|unauthorized|eacces|eperm)/i;
const configurationPattern = /(?:配置|密钥|环境变量|config(?:uration)?|api[_ -]?key|credential|\.env)/i;
const dependencyPattern = /(?:依赖|ffmpeg|whisper|python|executable|binary|command not found)/i;
const networkPattern = /(?:网络|连接|超时|远端|服务不可用|network|fetch|timeout|timed out|econn|502|503|504)/i;

export function classifyRouteError(error: Error): RouteErrorCategory {
  const message = error.message;
  if (permissionPattern.test(message)) {
    return "permission";
  }
  if (configurationPattern.test(message)) {
    return "configuration";
  }
  if (dependencyPattern.test(message)) {
    return "dependency";
  }
  if (networkPattern.test(message)) {
    return "network";
  }
  return "generic";
}

export function sanitizeMessage(raw: string | undefined): string | null {
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
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
  if (trimmed.length > 240) {
    return trimmed.slice(0, 237) + "...";
  }
  return trimmed;
}

export function RouteError({
  error,
  reset,
  title,
  errorCategory = "generic",
  domainHint,
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
  const safeHint = domainHint ?? hint ?? categoryHints[errorCategory];
  const heading = title ?? "出错了";

  return (
    <PixelErrorState
      action={{ kind: "button", label: "重新加载页面", onClick: reset }}
      className="route-error"
      description={safeHint}
      secondaryAction={{ kind: "link", label: "返回工作台", href: "/" }}
      title={heading}
    >
      <details className="route-error-detail">
        <summary>查看技术详情</summary>
        <p>
          {safeMessage ?? "该页面加载失败，未返回可安全展示的错误详情。"}
        </p>
        {safeDetail ? <p>{safeDetail}</p> : null}
        {error.digest ? (
          <p className="route-error-digest">
            <span>错误指纹</span>
            <code>{error.digest}</code>
          </p>
        ) : null}
      </details>
    </PixelErrorState>
  );
}
