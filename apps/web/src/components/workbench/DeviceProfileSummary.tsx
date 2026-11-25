"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getSavedDeviceProfileSummary, type SavedDeviceProfileSummary } from "@/lib/api/device-preparation";
import { DEVICE_SETTINGS_HREF, useWorkspacePage } from "./context";
import styles from "./DeviceProfileSummary.module.css";

const labels = {
  missing: "本机暂无已保存的设备资源档。",
  device_unbound: "当前设备身份未绑定，无法确认已保存的资源档归属。",
  device_mismatch: "已保存的资源档与当前设备不匹配。",
  unreadable: "无法读取已保存的设备资源档。",
};

function DeviceProfileSummary() {
  const [summary, setSummary] = useState<SavedDeviceProfileSummary | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void getSavedDeviceProfileSummary(controller.signal).then(value => {
      if (!controller.signal.aborted) setSummary(value);
    }).catch(() => {
      if (!controller.signal.aborted) setReadFailed(true);
    });
    return () => controller.abort();
  }, []);

  return <div className={styles.summary}>
    {!summary && !readFailed ? <p>正在读取已保存的设备资源档…</p> : null}
    <p role="status">
      {readFailed ? "设备资源档读取失败，保存情况未知。"
        : summary?.status === "saved" ? `已保存设备资源档 · 版本 ${summary.revision}`
        : summary ? labels[summary.status] : null}
    </p>
    <p className={styles.hint}>当前是否可用请到设置检查。</p>
  </div>;
}

export function DeviceProfileRouteView() {
  const page = useWorkspacePage();
  const router = useRouter();
  const standalone = page?.active && !page.auxiliary;
  useEffect(() => {
    if (standalone) router.replace(DEVICE_SETTINGS_HREF);
  }, [router, standalone]);
  return page?.active && page.auxiliary ? <DeviceProfileSummary /> : null;
}
