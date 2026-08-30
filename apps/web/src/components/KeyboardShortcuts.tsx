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
  { keys: ["Meta", "1"], href: "/", label: "首页" },
  { keys: ["Meta", "2"], href: "/library", label: "素材库" },
  { keys: ["Meta", "3"], href: "/knowledge", label: "内容资料" },
  { keys: ["Meta", "4"], href: "/profiles", label: "人物与声音" },
  { keys: ["Meta", "5"], href: "/templates", label: "视频模板" },
  { keys: ["Meta", "6"], href: "/production?view=automations", label: "自动任务" },
  { keys: ["Meta", "7"], href: "/history", label: "发布与数据" },
];

const HELP_TOGGLE_KEYS = ["Meta", "Shift", "H"];

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

export function KeyboardShortcuts({ onOpenSearch }: { onOpenSearch: () => void }) {
  const router = useRouter();
  const { pushToast } = useToast();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenSearch();
        return;
      }

      if (event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "n") {
        event.preventDefault();
        router.push("/production?new=1");
        return;
      }

      if (isEditableTarget(event.target)) {
        return;
      }

      if (
        event.metaKey &&
        event.shiftKey &&
        !event.altKey &&
        !event.ctrlKey &&
        matchesBinding(event, HELP_TOGGLE_KEYS)
      ) {
        event.preventDefault();
        toggleHelpPanel();
        return;
      }

      if (event.metaKey && !event.ctrlKey && !event.altKey) {
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
  }, [onOpenSearch, router, pushToast]);

  return null;
}
