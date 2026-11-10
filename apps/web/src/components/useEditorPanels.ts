"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export function useEditorPanels(enabled: boolean) {
  const [left, setLeft] = useState(200);
  // Only an explicit user resize becomes a remembered width. Responsive defaults
  // must not turn into a permanent 360px preference on the first visit.
  const [right, setRight] = useState<number | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  const [workspaceCollapsed, setWorkspaceCollapsed] = useState(false);
  const [viewport, setViewport] = useState(1440);
  const [loaded, setLoaded] = useState(false);
  const drag = useRef<{ side: "left" | "right"; x: number; width: number } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const savedLeft = Number(localStorage.getItem("video-editor-left-width"));
    const savedRight = Number(localStorage.getItem("video-workspace-chat-width"));
    if (Number.isFinite(savedLeft) && savedLeft >= 160) setLeft(clamp(savedLeft, 160, 280));
    if (Number.isFinite(savedRight) && savedRight >= 300) setRight(clamp(savedRight, 300, 480));
    const savedCollapse = localStorage.getItem("video-editor-left-collapsed");
    setCollapsed(savedCollapse === null ? window.innerWidth < 1200 : savedCollapse === "true");
    setChatCollapsed(localStorage.getItem("video-workspace-chat-collapsed") === "true");
    setWorkspaceCollapsed(localStorage.getItem("video-editor-workspace-collapsed") === "true");
    setLoaded(true);
    const resize = () => setViewport(window.innerWidth);
    resize(); window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [enabled]);
  useEffect(() => {
    if (!enabled || !loaded) return;
    localStorage.setItem("video-editor-left-width", String(left));
    if (right !== null) localStorage.setItem("video-workspace-chat-width", String(right));
    localStorage.setItem("video-editor-left-collapsed", String(collapsed));
    localStorage.setItem("video-workspace-chat-collapsed", String(chatCollapsed));
    localStorage.setItem("video-editor-workspace-collapsed", String(workspaceCollapsed));
  }, [enabled, loaded, left, right, collapsed, chatCollapsed, workspaceCollapsed]);
  const centerMinimum = 440;
  const leftWidth = collapsed ? 56 : Math.min(left, Math.max(160, viewport - 300 - centerMinimum - 8));
  const maxRight = Math.max(300, Math.min(480, viewport - leftWidth - centerMinimum - 8));
  const chatWidth = clamp(right ?? (viewport >= 1200 ? 380 : 300), 300, maxRight);
  const rightWidth = chatCollapsed ? 44 : chatWidth;
  function resize(side: "left" | "right", width: number) {
    if (side === "left") setLeft(clamp(width, 160, Math.max(160, Math.min(280, viewport - chatWidth - centerMinimum))));
    else setRight(clamp(width, 300, maxRight));
  }
  function separator(side: "left" | "right") {
    return {
      role: "separator" as const, tabIndex: 0, "aria-orientation": "vertical" as const,
      "aria-label": side === "left" ? "调整导航栏宽度" : "调整对话栏宽度",
      "aria-valuemin": side === "left" ? 160 : 300,
      "aria-valuemax": side === "left" ? 280 : maxRight,
      "aria-valuenow": Math.round(side === "left" ? leftWidth : chatWidth),
      title: "拖动或方向键调整；双击或 Home 恢复默认宽度",
      onDoubleClick() { if (side === "left") setLeft(200); else { setRight(null); localStorage.removeItem("video-workspace-chat-width"); } },
      onPointerDown(event: PointerEvent<HTMLDivElement>) {
        if (event.button !== 0) return;
        event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { side, x: event.clientX, width: side === "left" ? left : chatWidth };
      },
      onPointerMove(event: PointerEvent<HTMLDivElement>) {
        const start = drag.current;
        if (start?.side === side) resize(side, start.width + (event.clientX - start.x) * 1);
      },
      onPointerUp(event: PointerEvent<HTMLDivElement>) { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); },
      onPointerCancel() { drag.current = null; },
      onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        if (event.key === "Home") { event.preventDefault(); if (side === "left") setLeft(200); else { setRight(null); localStorage.removeItem("video-workspace-chat-width"); } return; }
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        resize(side, (side === "left" ? left : chatWidth) + (event.key === "ArrowRight" ? 16 : -16) * 1);
      },
    };
  }
  return {
    leftWidth, rightWidth, collapsed, chatCollapsed, workspaceCollapsed,
    toggleLeft: () => setCollapsed(value => !value),
    toggleChat: () => setChatCollapsed(value => !value),
    toggleWorkspace: () => setWorkspaceCollapsed(value => !value),
    separator,
  };
}
