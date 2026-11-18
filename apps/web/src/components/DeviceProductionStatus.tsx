"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getDeviceResourceStatus, type DeviceResourceStatus } from "@/lib/api/device-preparation";
import { Button } from "./ui/button";

function statusMessage(status: DeviceResourceStatus): string | null {
  if (status.production_blocker) return status.production_blocker;
  if (!status.preparation_configured) return "本机尚未配置制作资源。";
  if (status.profile_status === "invalid") return "本机制作配置需要重新核对。";
  if (status.profile_status === "unprepared" || !status.available_operations.includes("cpu_render")) {
    return "本机尚未完成成片渲染所需的设备准备。";
  }
  return null;
}

export function DeviceProductionStatus() {
  const [message, setMessage] = useState<string | null>(null);
  const [reading, setReading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true;
    setReading(true);
    void getDeviceResourceStatus().then(status => {
      if (current) setMessage(statusMessage(status));
    }).catch(() => {
      if (current) setMessage("未能读取本机制作状态。");
    }).finally(() => { if (current) setReading(false); });
    return () => { current = false; };
  }, [revision]);

  if (!message) return reading ? <p role="status">正在检查本机制作条件…</p> : null;
  return <section aria-label="本机制作条件">
    <p role="status">{message}</p>
    <Link href="/settings?section=runtime">查看设备准备</Link>{" "}
    <Button type="button" variant="ghost" disabled={reading} onClick={() => setRevision(value => value + 1)}>重新检查设备</Button>
  </section>;
}
