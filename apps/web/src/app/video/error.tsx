"use client";

import { AppShell } from "@/components/AppShell";
import { RouteError } from "@/components/RouteError";

export default function VideoError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <AppShell>
      <div className="video-page">
        <section className="video-main">
          <RouteError
            detail="视频扫描、剪辑计划或后台上传失败。可能是 ffmpeg 不可用或视频目录权限不足。"
            error={error}
            hint="请检查 ffmpeg / 视频目录权限，或稍后再试。"
            reset={reset}
          />
        </section>
      </div>
    </AppShell>
  );
}