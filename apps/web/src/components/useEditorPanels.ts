"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
export function useEditorPanels(enabled: boolean) {
  const [left, setLeft] = useState(224);
  const [right, setRight] = useState(350);
  const [collapsed, setCollapsed] = useState(false);
  const [viewport, setViewport] = useState(1440);
  const [loaded, setLoaded] = useState(false);
  const drag = useRef<{ side: "left" | "right"; x: number; width: number } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const savedLeft = Number(localStorage.getItem("video-editor-left-width"));
    const savedRight = Number(localStorage.getItem("video-workspace-chat-width"));
    if (Number.isFinite(savedLeft) && savedLeft >= 192) setLeft(clamp(savedLeft, 192, 280));
    if (Number.isFinite(savedRight) && savedRight >= 280) setRight(clamp(savedRight, 280, 400));
    setCollapsed(localStorage.getItem("video-editor-left-collapsed") === "true");
    setLoaded(true);
    const resize = () => setViewport(window.innerWidth);
    resize(); window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, [enabled]);
  useEffect(() => {
    if (!enabled || !loaded) return;
    localStorage.setItem("video-editor-left-width", String(left));
    localStorage.setItem("video-workspace-chat-width", String(right));
    localStorage.setItem("video-editor-left-collapsed", String(collapsed));
  }, [enabled, loaded, left, right, collapsed]);
  const centerMinimum = viewport < 1280 ? 480 : 560;
  const leftWidth = collapsed ? 64 : Math.min(left, Math.max(192, viewport - 280 - centerMinimum));
  const maxRight = Math.min(400, viewport - leftWidth - centerMinimum - 40);
  const rightWidth = clamp(right, 280, Math.max(280, maxRight));
  function resize(side: "left" | "right", width: number) {
    if (side === "left") setLeft(clamp(width, 192, Math.max(192, Math.min(280, viewport - rightWidth - centerMinimum))));
    else setRight(clamp(width, 280, Math.max(280, maxRight)));
  }
  function separator(side: "left" | "right") {
    return {
      role: "separator" as const, tabIndex: 0, "aria-orientation": "vertical" as const,
      "aria-label": side === "left" ? "调整导航栏宽度" : "调整对话栏宽度",
      "aria-valuemin": side === "left" ? 192 : 280,
      "aria-valuemax": side === "left" ? 280 : Math.max(280, maxRight),
      "aria-valuenow": Math.round(side === "left" ? left : rightWidth),
      onPointerDown(event: PointerEvent<HTMLDivElement>) {
        if (event.button !== 0) return;
        event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { side, x: event.clientX, width: side === "left" ? left : rightWidth };
      },
      onPointerMove(event: PointerEvent<HTMLDivElement>) {
        const start = drag.current;
        if (start?.side === side) resize(side, start.width + (event.clientX - start.x) * 1);
      },
      onPointerUp(event: PointerEvent<HTMLDivElement>) { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); },
      onPointerCancel() { drag.current = null; },
      onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        resize(side, (side === "left" ? left : rightWidth) + (event.key === "ArrowRight" ? 16 : -16) * 1);
      },
    };
  }
  return { leftWidth, rightWidth, collapsed, toggleLeft: () => setCollapsed(value => !value), separator };
}
