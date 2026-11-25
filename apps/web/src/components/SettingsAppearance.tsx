"use client";

import { useEffect, useState } from "react";
import {
  DEFAULT_PALETTE, WARM_PALETTE,
  readAppearancePalette, setAppearancePalette, type AppearancePalette,
} from "@/lib/appearance-preferences";
import formStyles from "./SettingsForm.module.css";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { applyTheme, type ThemeMode } from "./AppShell";
import { Button } from "./ui/button";

export function SettingsAppearance() {
  const [mode, setMode] = useState<ThemeMode>("system");
  const [palette, setPalette] = useState<AppearancePalette>(DEFAULT_PALETTE);

  useEffect(() => {
    const sync = () => {
      const stored = window.localStorage.getItem("ai-video-diary-theme");
      if (stored === "light" || stored === "dark" || stored === "system") setMode(stored);
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme-mode"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setPalette(readAppearancePalette());
  }, []);

  function choosePalette(value: AppearancePalette) {
    setAppearancePalette(value);
    setPalette(value);
  }

  return <>
    <div className={formStyles.field}>
      <span>界面主题</span>
      <AppSelect aria-label="界面主题" value={mode} onValueChange={value => {
        if (value !== "system" && value !== "light" && value !== "dark") return;
        window.localStorage.setItem("ai-video-diary-theme", value);
        applyTheme(value);
        setMode(value);
      }}>
        <AppSelectOption value="system">跟随系统</AppSelectOption>
        <AppSelectOption value="light">浅色</AppSelectOption>
        <AppSelectOption value="dark">深色</AppSelectOption>
      </AppSelect>
    </div>
    <div className={formStyles.field}>
      <span>配色</span>
      <AppSelect aria-label="配色" value={palette} onValueChange={value => {
        if (value === DEFAULT_PALETTE || value === WARM_PALETTE) choosePalette(value);
      }}>
        <AppSelectOption value={DEFAULT_PALETTE}>蓝橙（默认）</AppSelectOption>
        <AppSelectOption value={WARM_PALETTE}>暖橙</AppSelectOption>
      </AppSelect>
      <div>
        <Button variant="outline" type="button" onClick={() => choosePalette(DEFAULT_PALETTE)}>恢复默认配色</Button>
      </div>
    </div>
  </>;
}
