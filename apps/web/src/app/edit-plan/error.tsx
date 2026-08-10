"use client";

import { AppShell } from "@/components/AppShell";
import { RouteError } from "@/components/RouteError";

export default function EditPlanError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="edit-plan-page">
        <section className="edit-plan-main">
          <RouteError
            detail="剪辑计划加载或保存失败。可能是视频处理还没完成，或剪辑 API 异常。"
            error={error}
            hint="请确认视频处理已完成，或稍后再试。"
            reset={reset}
          />
        </section>
      </div>
    </AppShell>
  );
}