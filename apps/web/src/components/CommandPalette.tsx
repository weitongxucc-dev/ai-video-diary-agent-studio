"use client";

import { Search } from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
} from "react";

export type CommandPaletteItem = {
  id: string;
  label: string;
  description: string;
  href: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
};

type Props = {
  items: CommandPaletteItem[];
  onClose: () => void;
  onNavigate: (href: string) => void;
  open: boolean;
};

export function CommandPalette({ items, onClose, onNavigate, open }: Props) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    if (!normalized) return items.slice(0, 12);
    return items.filter((item) =>
      `${item.label} ${item.description}`.toLocaleLowerCase("zh-CN").includes(normalized),
    ).slice(0, 12);
  }, [items, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    setActiveIndex((current) => Math.min(current, Math.max(0, results.length - 1)));
  }, [results.length]);

  if (!open) return null;

  function choose(item: CommandPaletteItem) {
    onNavigate(item.href);
    onClose();
  }

  return (
    <div className="command-palette-layer">
      <button aria-label="关闭搜索" className="command-palette-backdrop" onClick={onClose} type="button" />
      <section
        aria-label="搜索页面和创作任务"
        aria-modal="true"
        className="command-palette"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          } else if (event.key === "ArrowDown" && results.length) {
            event.preventDefault();
            setActiveIndex((current) => (current + 1) % results.length);
          } else if (event.key === "ArrowUp" && results.length) {
            event.preventDefault();
            setActiveIndex((current) => (current - 1 + results.length) % results.length);
          } else if (event.key === "Enter" && results[activeIndex]) {
            event.preventDefault();
            choose(results[activeIndex]);
          }
        }}
        role="dialog"
      >
        <label className="command-palette-search">
          <Search aria-hidden size={18} strokeWidth={1.8} />
          <input
            aria-label="搜索页面和创作任务"
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
            placeholder="搜索页面、创作任务或设置"
            ref={inputRef}
            value={query}
          />
          <kbd>ESC</kbd>
        </label>

        <div className="command-palette-results" role="listbox">
          {results.length ? results.map((item, index) => {
            const ItemIcon = item.icon;
            return (
              <button
                aria-selected={index === activeIndex}
                className={index === activeIndex ? "is-active" : undefined}
                key={item.id}
                onClick={() => choose(item)}
                onMouseEnter={() => setActiveIndex(index)}
                role="option"
                type="button"
              >
                <span className="command-palette-result-icon"><ItemIcon size={17} strokeWidth={1.8} /></span>
                <span><strong>{item.label}</strong><small>{item.description}</small></span>
                <kbd>↵</kbd>
              </button>
            );
          }) : (
            <div className="command-palette-empty">没有匹配的页面或创作任务</div>
          )}
        </div>
        <footer>↑↓ 选择 · Enter 打开 · ⌘K 随时搜索</footer>
      </section>
    </div>
  );
}
