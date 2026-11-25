"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type MouseEvent } from "react";

const shellInsets = 16;
const minimumEditorWidth = 640;
const minimumNavigationWidth = 160;
const minimumConversationWidth = 300;
const railWidth = 48;
const narrowNavigationBreakpoint = 1152;

const keys = {
  left: "video-editor-left-width",
  right: "video-workspace-chat-width",
  navigation: "video-editor-left-collapsed",
  conversation: "video-workspace-chat-collapsed",
  workspace: "video-editor-workspace-collapsed",
};

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

function preferredWidth(key: string, min: number, max: number) {
  const value = Number(localStorage.getItem(key));
  return Number.isFinite(value) && value >= min ? clamp(value, min, max) : null;
}

function storeWidth(key: string, value: number | null) {
  if (value === null) localStorage.removeItem(key);
  else localStorage.setItem(key, String(value));
}

export function useEditorPanels(enabled: boolean, useNavigationRail = false) {
  const [left, setLeft] = useState<number | null>(null);
  const [right, setRight] = useState<number | null>(null);
  const [navigationCollapsed, setNavigationCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [viewport, setViewport] = useState(1440);
  const [loaded, setLoaded] = useState(false);
  const drag = useRef<{ side: "left" | "right"; x: number; width: number } | null>(null);

  useEffect(() => {
    if (!enabled) return;
    setLeft(preferredWidth(keys.left, minimumNavigationWidth, 280));
    setRight(preferredWidth(keys.right, minimumConversationWidth, 480));
    setNavigationCollapsed(localStorage.getItem(keys.navigation) === "true");
    setChatCollapsed(localStorage.getItem(keys.conversation) === "true");
    setWorkspaceCollapsed(localStorage.getItem(keys.workspace) === "true");
    setLoaded(true);
    const resize = () => setViewport(document.documentElement.clientWidth || window.innerWidth);
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !loaded) return;
    storeWidth(keys.left, left);
    storeWidth(keys.right, right);
    localStorage.setItem(keys.navigation, String(navigationCollapsed));
    localStorage.setItem(keys.conversation, String(chatCollapsed));
    localStorage.setItem(keys.workspace, String(workspaceCollapsed));
  }, [enabled, loaded, left, right, navigationCollapsed, chatCollapsed, workspaceCollapsed]);

  // Keep a useful editor at the desktop minimum; the drawer never consumes a grid track.
  const narrow = useNavigationRail || viewport < narrowNavigationBreakpoint;
  const collapsed = narrow || navigationCollapsed;
  const defaultLeft = viewport >= 1560 ? 192 : viewport >= 1360 ? 176 : minimumNavigationWidth;
  const defaultRight = viewport >= 1560 ? 400 : viewport >= 1360 ? 368 : viewport >= 1280 ? 344 : narrow ? 320 : 336;
  const roomForPanels = viewport - minimumEditorWidth - shellInsets;
  const maxLeft = clamp(roomForPanels - defaultRight, minimumNavigationWidth, 280);
  const leftWidth = collapsed ? railWidth : clamp(left ?? defaultLeft, minimumNavigationWidth, maxLeft);
  const maxRight = clamp(roomForPanels - leftWidth, minimumConversationWidth, 480);
  const chatWidth = clamp(right ?? defaultRight, minimumConversationWidth, maxRight);
  const rightWidth = chatCollapsed ? 44 : chatWidth;
  const openChat = useCallback(() => setChatCollapsed(false), []);

  function separator(side: "left" | "right") {
    const width = side === "left" ? leftWidth : chatWidth;
    const min = side === "left" ? minimumNavigationWidth : minimumConversationWidth;
    const max = side === "left" ? maxLeft : maxRight;
    const set = side === "left" ? setLeft : setRight;
    return {
      role: "separator" as const, tabIndex: 0, "aria-orientation": "vertical" as const,
      "aria-label": side === "left" ? "调整导航栏宽度" : "调整对话栏宽度",
      "aria-valuemin": min, "aria-valuemax": max, "aria-valuenow": width,
      title: "拖动或按方向键调整，双击或 Home 恢复默认宽度",
      onClick(event: MouseEvent<HTMLDivElement>) { event.currentTarget.focus(); },
      onDoubleClick() { set(null); },
      onPointerDown(event: PointerEvent<HTMLDivElement>) {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { side, x: event.clientX, width };
      },
      onPointerMove(event: PointerEvent<HTMLDivElement>) {
        const start = drag.current;
        if (start?.side === side) set(clamp(start.width + event.clientX - start.x, min, max));
      },
      onLostPointerCapture() { drag.current = null; },
      onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        if (event.key === "Home") { event.preventDefault(); set(null); return; }
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        set(clamp(width + (event.key === "ArrowRight" ? 16 : -16), min, max));
      },
    };
  }

  return {
    leftWidth, rightWidth, narrow, collapsed, chatCollapsed, workspaceCollapsed, openChat,
    canResizeNavigation: !collapsed && maxLeft > minimumNavigationWidth,
    canResizeConversation: maxRight > minimumConversationWidth,
    toggleLeft: () => setNavigationCollapsed(value => !value),
    toggleChat: () => setChatCollapsed(value => !value),
    toggleWorkspace: () => setWorkspaceCollapsed(value => !value),
    separator,
  };
}
