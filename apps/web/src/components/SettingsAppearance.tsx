"use client";
import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { useEffect, useState } from "react";
import { applyTheme, type ThemeMode } from "./AppShell";

export function SettingsAppearance() {
  const [mode, setMode] = useState<ThemeMode>("system");
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
  return <label>界面主题<AppSelect aria-label="界面主题" value={mode} onValueChange={newValue => {
    const selected = newValue as ThemeMode;
    setMode(selected); window.localStorage.setItem("ai-video-diary-theme", selected); applyTheme(selected);
  }}><AppSelectOption value="system">跟随系统</AppSelectOption><AppSelectOption value="light">浅色</AppSelectOption><AppSelectOption value="dark">深色</AppSelectOption></AppSelect></label>;
}
