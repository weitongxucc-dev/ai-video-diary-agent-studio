"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import { useWorkspaceViewState } from "./workbench/context";
import ui from "./VideoEditor.module.css";

function tickInterval(pixelsPerSecond: number) {
  const target = 88 / pixelsPerSecond;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  return [1, 2, 5, 10].find(value => value * magnitude >= target)! * magnitude;
}

/** All lanes use the same time canvas; zoom and scroll are view state only. */
export function TimelineViewport({ duration, time, playing, selectedTime, selectionKey, onSeek, formatTime, children }: {
  duration: number; time: number; playing: boolean; selectedTime?: number; selectionKey: string | null;
  onSeek: (time: number) => void; formatTime: (time: number) => string; children: ReactNode;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const ruler = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, label: 72 });
  const [density, setDensity] = useWorkspaceViewState<number | null>("timeline.pixels-per-second", 12);
  const [scroll, setScroll] = useWorkspaceViewState("timeline.scroll", 0);
  const initialScroll = useRef(scroll);
  const restored = useRef(false);
  const fit = Math.max(1, size.width - size.label) / duration;
  const pixelsPerSecond = Math.max(fit, density ?? fit);
  const laneWidth = duration * pixelsPerSecond;
  const interval = tickInterval(pixelsPerSecond);
  const ticks = Array.from({ length: Math.ceil(duration / interval) }, (_, index) => index * interval);
  const drag = useRef(false);
  const previousSelection = useRef(selectionKey);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const measure = () => {
      if (!node.clientWidth) return;
      const style = getComputedStyle(node);
      setSize({ width: node.clientWidth, label: parseFloat(style.getPropertyValue("--timeline-label-width")) + parseFloat(style.getPropertyValue("--timeline-column-gap")) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (size.width && viewport.current && !restored.current) { viewport.current.scrollLeft = initialScroll.current; restored.current = true; }
  }, [size.width]);
  useEffect(() => {
    if (previousSelection.current === selectionKey) return;
    previousSelection.current = selectionKey;
    const node = viewport.current;
    if (!node || selectedTime === undefined || drag.current) return;
    const x = selectedTime * pixelsPerSecond;
    const visibleWidth = node.clientWidth - size.label;
    if (x < node.scrollLeft || x > node.scrollLeft + visibleWidth - 24) node.scrollLeft = Math.max(0, x - visibleWidth / 3);
  }, [selectionKey, selectedTime, pixelsPerSecond, size.label]);
  useEffect(() => {
    const node = viewport.current;
    if (!node || !playing) return;
    const x = time * pixelsPerSecond;
    const visibleWidth = node.clientWidth - size.label;
    if (x < node.scrollLeft || x > node.scrollLeft + visibleWidth - 24) node.scrollLeft = Math.max(0, x - 24);
  }, [time, playing, pixelsPerSecond, size.label]);
  function zoom(next: number | null) {
    const node = viewport.current;
    const center = node ? (node.scrollLeft + (node.clientWidth - size.label) / 2) / pixelsPerSecond : time;
    setDensity(next);
    if (node) requestAnimationFrame(() => { node.scrollLeft = next === null ? 0 : Math.max(0, center * Math.max(fit, next) - (node.clientWidth - size.label) / 2); });
  }
  function seekAt(clientX: number) {
    const rect = ruler.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(duration, (clientX - rect.left) / rect.width * duration)));
  }
  return <section className={ui.timelinePanel} aria-label="配音与字幕时间范围">
    <div className={ui.timelineToolbar}>
      <span>时间线</span><time>{formatTime(time)}</time>
      <div className={ui.timelineZoom}>
        <button className={ui.iconButton} type="button" aria-label="缩小时间线" title="缩小时间线" disabled={pixelsPerSecond <= fit} onClick={() => zoom(Math.max(fit, pixelsPerSecond / 1.5))}><Minus size={15}/></button>
        <input aria-label="时间线缩放" type="range" min={fit} max={Math.max(160, fit)} step="any" value={pixelsPerSecond} onChange={event => zoom(Number(event.target.value))}/>
        <button className={ui.iconButton} type="button" aria-label="放大时间线" title="放大时间线" disabled={pixelsPerSecond >= Math.max(160, fit)} onClick={() => zoom(Math.min(Math.max(160, fit), pixelsPerSecond * 1.5))}><Plus size={15}/></button>
        <button className={ui.iconButton} type="button" aria-label="时间线适应窗口" title="时间线适应窗口" onClick={() => zoom(null)}><Maximize2 size={15}/></button>
      </div>
    </div>
    <div ref={viewport} className={ui.timelineScroll} data-workspace-scroll="timeline" onScroll={event => setScroll(event.currentTarget.scrollLeft)}>
      <div className={ui.tracks} style={{ width: size.width ? size.label + laneWidth : "100%" }}>
        <div className={ui.ruler}><span>时间</span><div ref={ruler} className={ui.timeRuler} role="slider" tabIndex={0} aria-label="时间线播放位置" aria-valuemin={0} aria-valuemax={duration} aria-valuenow={Math.min(duration, time)} aria-valuetext={formatTime(time)}
          onPointerDown={event => { drag.current = true; event.currentTarget.setPointerCapture(event.pointerId); seekAt(event.clientX); }}
          onPointerMove={event => { if (drag.current) seekAt(event.clientX); }} onPointerUp={() => { drag.current = false; }} onPointerCancel={() => { drag.current = false; }}
          onKeyDown={event => { const step = event.shiftKey ? 10 : 1; const next = event.key === "Home" ? 0 : event.key === "End" ? duration : event.key === "ArrowRight" ? time + step : event.key === "ArrowLeft" ? time - step : null; if (next !== null) { event.preventDefault(); onSeek(Math.max(0, Math.min(duration, next))); } }}>
          {ticks.filter(tick => tick === 0 || laneWidth - tick * pixelsPerSecond > 55).map(tick => <time key={tick} style={{ left: tick / duration * 100 + "%" }}>{formatTime(tick)}</time>)}
        </div></div>
        <div className={ui.timelinePlayhead} aria-hidden style={{ left: size.label + Math.min(duration, time) * pixelsPerSecond }}/>
        {children}
      </div>
    </div>
  </section>;
}
