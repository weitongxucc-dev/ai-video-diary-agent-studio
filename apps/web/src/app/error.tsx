"use client";

import { AppShell } from "@/components/AppShell";
import { RouteError } from "@/components/RouteError";

export default function RootError({
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
            detail="首页在加载今日 Episode 或运行状态时失败。可能是本地 API 没启动。"
            error={error}
            hint="请检查本地 API 是否启动，或稍后再试一次。"
            reset={reset}
          />
        </div>
      </div>
    </AppShell>
  );
}