"use client";

import { useEffect } from "react";

function reloadDocument() {
  window.location.reload();
}

export function ProcessingRefresh({ reload = reloadDocument }: { reload?: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(reload, 5000);
    return () => window.clearTimeout(timer);
  }, [reload]);

  return <span className="video-processing-note">正在后台处理，页面会自动刷新。</span>;
}
