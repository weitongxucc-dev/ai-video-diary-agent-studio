"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

type ProcessingRefreshProps = {
  label?: string;
};

export function ProcessingRefresh({
  label = "正在后台处理，页面会自动刷新。",
}: ProcessingRefreshProps) {
  const router = useRouter();

  useEffect(() => {
    const timer = window.setInterval(() => router.refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [router]);

  return <span className="video-processing-note">{label}</span>;
}
