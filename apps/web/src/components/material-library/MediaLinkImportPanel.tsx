"use client";
import { FolderPlus, LoaderCircle, ShieldCheck } from "lucide-react";
import type { MediaLinkCheck, MediaLinkImport } from "@/lib/media-link-api";
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    linkUrl: string;
    linkBusy: "check" | "import" | null;
    linkCheck: MediaLinkCheck | null;
    linkImport: MediaLinkImport | null;
    linkError: string;
    disabled: boolean;
    onUrlChange: (url: string) => void;
    onCheck: () => void;
    onImport: () => void;
};
export function MediaLinkImportPanel({ linkUrl, linkBusy, linkCheck, linkImport, linkError, disabled, onUrlChange, onCheck, onImport }: Props) {
    return <>
    <div className={`${styles.intakeRow} ${styles.intakeLinkRow}`}>
                <input aria-label="媒体文件直链" className={styles.intakeLinkInput} disabled={linkBusy !== null} onChange={(event) => onUrlChange(event.target.value)} placeholder="粘贴公开的视频、音频或图片直链" type="url" value={linkUrl}/>
                <span className={styles.intakeRow}>
                  <button className={styles.intakeSecondary} disabled={linkBusy !== null || !linkUrl.trim() || disabled} onClick={onCheck} type="button">
                    {linkBusy === "check" ? <LoaderCircle className="is-spinning" size={15}/> : <ShieldCheck size={15}/>}
                    {linkBusy === "check" ? "正在检查…" : "检查直链"}
                  </button>
                  <button className={styles.intakeSecondary} disabled={linkBusy !== null || !linkCheck?.supported || disabled} onClick={onImport} type="button">
                    {linkBusy === "import" ? <LoaderCircle className="is-spinning" size={15}/> : <FolderPlus size={15}/>}
                    {linkBusy === "import" ? "正在导入…" : "导入这条链接"}
                  </button>
                </span>
              </div>
              {linkCheck || linkImport || linkError ? (<p className={styles.intakeDescription} role={linkError ? "alert" : "status"}>
                  {linkError ? linkError : null}
                  {!linkError && linkCheck?.supported ? linkCheck.message : null}
                  {!linkError && linkImport
                ? `已导入${linkImport.reused_existing_file ? "（复用已有素材）" : ""}：${linkImport.file_name}，${linkImport.size_bytes} 字节。${linkImport.media_type === "video"
                    ? "已登记进素材库，等待文件稳定后分析。"
                    : linkImport.analysis_message}来源：${linkImport.url}`
                : null}
                </p>) : null}
  </>;
}
