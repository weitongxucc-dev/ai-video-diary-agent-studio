"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { PixelIcon } from "@/components/PixelIcon";

const routeByKeyword: Array<[string[], string]> = [
  [["工作台", "today", "home"], "/"],
  [["选题", "topic"], "/topics"],
  [["同款", "爆款", "复刻", "same style"], "/same-style-script"],
  [["口播", "脚本", "script"], "/script"],
  [["封面", "cover"], "/cover"],
  [["视频", "video"], "/video"],
  [["剪辑", "edit"], "/edit-plan"],
  [["草稿", "draft"], "/draft"],
  [["反馈", "publish"], "/publish-feedback"],
  [["模板", "template"], "/templates"],
  [["画像", "profile"], "/profile"],
  [["知识", "knowledge", "obsidian"], "/knowledge"],
  [["信息源", "source"], "/sources"],
  [["学习", "learning"], "/learning"],
  [["健康", "health"], "/health"],
  [["日志", "log"], "/logs"],
  [["配置", "设置", "setup", "setting"], "/settings"],
];

type GlobalSearchBoxProps = {
  className?: string;
  inputClassName?: string;
  placeholder?: string;
  showIcon?: boolean;
};

export function GlobalSearchBox({
  className = "global-search-form",
  inputClassName = "search-box",
  placeholder = "搜索页面、选题、素材...",
  showIcon = false,
}: GlobalSearchBoxProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim().toLowerCase();
    const match = routeByKeyword.find(([keywords]) =>
      keywords.some((keyword) => normalized.includes(keyword)),
    );
    router.push(match?.[1] ?? "/topics");
  }

  return (
    <form className={className} onSubmit={submit}>
      {showIcon ? <PixelIcon name="search" /> : null}
      <input
        aria-label="搜索页面、选题、素材"
        className={inputClassName}
        onChange={(event) => setQuery(event.currentTarget.value)}
        placeholder={placeholder}
        type="search"
        value={query}
      />
    </form>
  );
}
