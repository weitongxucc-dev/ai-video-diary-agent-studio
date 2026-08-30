"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useToast } from "@/components/PixelToast";
import { toggleHelpPanel } from "@/lib/helpPanel";

type RouteShortcut = {
  keys: string[];
  href: string;
  label: string;
};

const ROUTE_SHORTCUTS: RouteShortcut[] = [
  { keys: ["Ctrl", "1"], href: "/", label: "今日工作台" },
  { keys: ["Ctrl", "2"], href: "/script", label: "口播稿" },
  { keys: ["Ctrl", "3"], href: "/production", label: "创作任务" },
  { keys: ["Ctrl", "5"], href: "/draft", label: "剪映草稿" },
  { keys: ["Ctrl", "6"], href: "/learning", label: "反馈学习" },
  { keys: ["Ctrl", "7"], href: "/settings", label: "设置" },
];

const HELP_TOGGLE_KEYS = ["Ctrl", "Shift", "H"];

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  return target.isContentEditable;
}

function matchesBinding(event: KeyboardEvent, keys: string[]): boolean {
  const wantCtrl = keys.includes("Ctrl");
  const wantShift = keys.includes("Shift");
  const wantAlt = keys.includes("Alt");
  const wantMeta = keys.includes("Meta");
  const otherKeys = keys.filter(
    (key) =>
      key !== "Ctrl" && key !== "Shift" && key !== "Alt" && key !== "Meta",
  );

  if (event.ctrlKey !== wantCtrl) return false;
  if (event.shiftKey !== wantShift) return false;
  if (event.altKey !== wantAlt) return false;
  if (event.metaKey !== wantMeta) return false;

  if (otherKeys.length === 0) {
    return false;
  }
  return otherKeys.every((key) => {
    if (key === "?") {
      return event.key === "?";
    }
    if (key.length === 1) {
      return event.key.toLowerCase() === key.toLowerCase();
    }
    return event.key === key;
  });
}

export function KeyboardShortcuts() {
  const router = useRouter();
  const { pushToast } = useToast();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target)) {
        return;
      }

      if (
        event.ctrlKey &&
        event.shiftKey &&
        !event.altKey &&
        !event.metaKey &&
        matchesBinding(event, HELP_TOGGLE_KEYS)
      ) {
        event.preventDefault();
        toggleHelpPanel();
        return;
      }

      if (event.ctrlKey && !event.metaKey && !event.altKey) {
        const navBinding = ROUTE_SHORTCUTS.find((entry) =>
          matchesBinding(event, entry.keys),
        );
        if (navBinding) {
          event.preventDefault();
          router.push(navBinding.href);
          pushToast({
            kind: "info",
            message: `已跳转 · ${navBinding.label}`,
            durationMs: 1800,
          });
        }
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [router, pushToast]);

  return null;
}
