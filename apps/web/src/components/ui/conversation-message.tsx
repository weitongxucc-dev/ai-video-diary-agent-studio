"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";

/** Clip the actual message, measuring rendered content instead of inventing summaries. */
export function ConversationMessage({ children, className, savedOpen = false, onOpenChange }: {
  children: ReactNode; className: string; savedOpen?: boolean; onOpenChange?: (open: boolean) => void;
}) {
  const content = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(savedOpen);
  const [truncated, setTruncated] = useState(false);
  useEffect(() => {
    const node = content.current;
    if (!node) return;
    const measure = () => setTruncated(node.scrollHeight > 170);
    const observer = new ResizeObserver(measure);
    observer.observe(node); measure();
    return () => observer.disconnect();
  }, [children]);
  return <div className={`assistant-message ${className}`}>
    <div ref={content} className="assistant-message-content" data-expanded={expanded || undefined}>{children}</div>
    {truncated ? <button className="assistant-text-action" type="button" aria-expanded={expanded} onClick={() => {
      const next = !expanded; setExpanded(next); onOpenChange?.(next);
    }}>{expanded ? "收起" : "展开全文"}</button> : null}
  </div>;
}
