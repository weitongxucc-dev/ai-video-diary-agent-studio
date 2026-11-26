"use client";

import { useState } from "react";

export function ResourceAudioPreview({ name, src }: { name: string; src: string }) {
  const [failed, setFailed] = useState(false);

  return <div>
    <audio controls preload="none" src={src} aria-label={`预览：${name}`}
      onError={() => setFailed(true)} onCanPlay={() => setFailed(false)} />
    <span role="status">{failed ? "暂时无法试听此版本。收起后重新打开预览可重试。" : null}</span>
  </div>;
}
