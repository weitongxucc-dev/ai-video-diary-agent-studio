"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export function ProcessingRefresh() {
  const router = useRouter();

  useEffect(() => {
    const timer = window.setInterval(() => router.refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [router]);

  return <span className="video-processing-note">正在后台处理，页面会自动刷新。</span>;
}
