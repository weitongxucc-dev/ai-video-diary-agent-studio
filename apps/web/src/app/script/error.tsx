"use client";

import { AppShell } from "@/components/AppShell";
import { RouteError } from "@/components/RouteError";

export default function ScriptError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="workspace-page">
        <div className="workspace-main">
          <RouteError
            detail="口播稿加载或保存失败，可能是脚本生成 API 异常或网络中断。"
            error={error}
            hint="请确认 DeepSeek 配置与本地 API 状态，或稍后再试。"
            reset={reset}
          />
        </div>
      </div>
    </AppShell>
  );
}