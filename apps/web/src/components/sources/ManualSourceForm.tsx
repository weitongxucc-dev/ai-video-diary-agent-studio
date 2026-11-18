"use client";

import { useState, type FormEvent } from "react";
import { FileText } from "lucide-react";
import { AppSelect, AppSelectOption } from "../AppSelect";
import { createManualSourceItem, type ContentUseState, type SourceItem } from "@/lib/source-api";
import styles from "@/app/sources/SourcesPage.module.css";

export function ManualSourceForm({ busy, onStart, onCreated, onError, onEnd }: {
  busy: boolean;
  onStart: () => void;
  onCreated: (item: SourceItem) => void;
  onError: (cause: unknown) => void;
  onEnd: () => void;
}) {
  const [manualType, setManualType] = useState("text");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const data = new FormData(element);
    const field = (name: string) => String(data.get(name) ?? "").trim();
    onStart();
    try {
      const license = field("license");
      const item = await createManualSourceItem({
        title: field("title"), content_type: manualType, source_url: field("source_url") || null,
        body: String(data.get("body") ?? "") || null, subtitles: null, media_references: [],
        file_reference: field("file_reference") || null, author: null, ...(license ? { license } : {}),
        use_state: (field("use_state") || "available") as ContentUseState,
      });
      onCreated(item);
      element.reset();
      setManualType("text");
    } catch (cause) { onError(cause); }
    finally { onEnd(); }
  }
  return (
    <form className={styles.entryForm} onSubmit={submit}>
      <label><span>标题</span><input name="title" placeholder="给这条内容一个名称" required /></label>
      <label><span>正文</span><textarea name="body" placeholder="粘贴已有资料正文" required={manualType === "text"} rows={5} /></label>
      <details className={styles.more}><summary>出处与使用范围</summary>
        <label><span>类型</span><AppSelect name="content_type" value={manualType} onValueChange={newValue => setManualType(newValue)}><AppSelectOption value="text">文字</AppSelectOption><AppSelectOption value="link">链接</AppSelectOption><AppSelectOption value="video">视频</AppSelectOption><AppSelectOption value="file">文件引用</AppSelectOption></AppSelect></label>
        <label><span>原文链接（可选）</span><input name="source_url" placeholder="https://" type="url" /></label>
        <label><span>文件引用（可选）</span><input name="file_reference" placeholder="已有文件的路径" /></label>
        <label><span>来源许可记录（可选）</span><input name="license" placeholder="记录来源中已有的许可说明" /></label>
        <UseStateField />
      </details>
      <button disabled={busy} type="submit"><FileText size={15} />添加信息条目</button>
    </form>
  );
}

export function UseStateField() {
  return <label><span>保存后的使用范围</span><AppSelect defaultValue="available" name="use_state"><AppSelectOption value="available">可正常使用</AppSelectOption><AppSelectOption value="reference_only">仅作参考，不直接进入成片</AppSelectOption><AppSelectOption value="excluded">排除，不用于检索、生成或成片</AppSelectOption><AppSelectOption value="not_for_automation">不进入自动选片，可由我手动选择</AppSelectOption><AppSelectOption value="not_for_publish">可制作预览，不进入发布产物</AppSelectOption></AppSelect></label>;
}
