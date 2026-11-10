"use client";
import type { GraphicOverlay } from "@/lib/types/timeline";
import styles from "../VideoEditor.module.css";

export function OverlayTimeline({ items, duration, selectedId, disabled, onSelect, formatTime }: {
  items: GraphicOverlay[]; duration: number; selectedId?: string | null;
  disabled: boolean; onSelect: (id: string) => void;
  formatTime: (seconds: number) => string;
}) {
  return <div className={styles.overlayTracks} aria-label="图文叠加层">{items.map((item, index) => {
    const kind = item.kind === "template" ? "动画" : item.kind === "image" ? "图片" : item.kind === "arrow" ? "箭头" : "标题";
    const name = "name" in item ? item.name : item.text;
    const label = `${kind}${index + 1} · ${name}`;
    return <div className={styles.track} key={item.id}><span title={label}>{kind} {index + 1}</span>
      <div className={styles.overlayLane}><button type="button" disabled={disabled} aria-label={`${label}：${formatTime(item.start_seconds)}–${formatTime(item.end_seconds)}`} title={label}
        aria-pressed={selectedId === item.id} onClick={() => onSelect(item.id)}
        style={{left:`${item.start_seconds / duration * 100}%`,width:`${(item.end_seconds - item.start_seconds) / duration * 100}%`}}>{name || kind}</button></div>
    </div>;
  })}</div>;
}
