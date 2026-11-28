"use client";

import { useEffect, useState } from "react";
import { FileText, X } from "lucide-react";
import type { AssistantTurnPdfReference } from "@/lib/types/assistant";
import { getAssistantTurnPdf } from "@/lib/api/assistant";
import { Button } from "./button";
import { Popover, PopoverClose, PopoverContent, PopoverTitle, PopoverTrigger } from "./popover";

export function TurnPdfPreview({ attachment, conversationId, turnId }: {
  attachment: AssistantTurnPdfReference;
  conversationId: string;
  turnId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [pages, setPages] = useState<{locator: string; text: string}[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setPages(null);
    setError(null);
    void getAssistantTurnPdf(conversationId, attachment.attachment_id, turnId, controller.signal)
      .then(result => { if (!controller.signal.aborted) setPages(result.pages); })
      .catch(cause => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "PDF 暂时无法读取。");
      });
    return () => controller.abort();
  }, [attachment.attachment_id, attempt, conversationId, open, turnId]);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger render={<Button variant="ghost" type="button"/>} className="assistant-reference-open"
      title={`预览 PDF：${attachment.label}`} aria-label={`预览 PDF：${attachment.label}，仅本轮使用`}>
      <FileText size={16} aria-hidden/><span>{attachment.label} · 仅本轮使用</span>
    </PopoverTrigger>
    <PopoverContent className="assistant-reference-popover">
      <div className="assistant-reference-heading">
        <PopoverTitle>{attachment.label}</PopoverTitle>
        <PopoverClose render={<Button variant="ghost" size="icon" type="button"/>} aria-label="关闭 PDF 预览"><X size={16}/></PopoverClose>
      </div>
      <p>第 1–{attachment.page_count} 页 · 仅本轮使用</p>
      {error ? <div><p role="alert">{error}</p><Button type="button" variant="ghost" onClick={() => setAttempt(value => value + 1)}>重试预览</Button></div>
        : pages ? <ol className="assistant-reference-list" aria-label="PDF 分页正文" tabIndex={0}>{pages.map(page =>
          <li key={page.locator}><strong>{page.locator}</strong><p className="whitespace-pre-wrap break-words">{page.text}</p></li>
        )}</ol> : <p role="status">正在读取 PDF…</p>}
    </PopoverContent>
  </Popover>;
}
