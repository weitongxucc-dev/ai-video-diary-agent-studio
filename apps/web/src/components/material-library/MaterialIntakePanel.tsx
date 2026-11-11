"use client";
import { Button } from "../ui/button";
import Link from "next/link";
import { FolderPlus, FolderSearch, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { MaterialIntakeStatus, MaterialPendingCopy } from "@/lib/types";
import type { useLocalMaterialImport } from "./useLocalMaterialImport";
import type { Busy } from './material-state';
import { directoryStateLabel } from './material-state';
import styles from "../MaterialLibraryControls.module.css";
type Props = {
    intake: MaterialIntakeStatus | null;
    intakeError: string;
    intakeBusy: "bind" | "refresh" | "confirm" | null;
    busy: Busy;
    importBusy: boolean;
    importResult: ReturnType<typeof useLocalMaterialImport>["importResult"];
    onRefresh: () => void;
    onBind: () => void;
    onLocalImport: () => void;
    onScan: () => void;
    onConfirmCopy: (file: MaterialPendingCopy) => void;
    mediaLinkImport: ReactNode;
};
export function MaterialIntakePanel({ intake, intakeError, intakeBusy, busy, importBusy, importResult, onRefresh, onBind, onLocalImport, onScan, onConfirmCopy, mediaLinkImport }: Props) {
    return (<section className={styles.intakePanel} aria-label="素材自动接入">
          <div className={styles.intakeHeading}>
            <strong>素材自动接入</strong>
            <span>{intake ? directoryStateLabel[intake.directory_state] : "正在核对目录"}</span>
          </div>
          {intakeError ? (<p className={styles.intakeError} role="alert">{intakeError}
              <Button variant="outline" disabled={intakeBusy !== null} onClick={onRefresh} type="button">重新检查</Button>
            </p>) : null}
          {!intake ? (<p className={styles.intakeDescription}>{intakeError ? "暂时无法确认目录状态，请重新检查。" : "正在确认素材目录与当前工作区的关系…"}</p>) : !intake.binding_available ? (<p className={styles.intakeDescription}>该目录已接入其他工作区。请切换到所属工作区处理，当前工作区不能直接接入。</p>) : intake.directory_state === "changed" ? (<>
              <p className={styles.intakeDescription}>素材目录状态已变化。请到<Link href="/settings">设置</Link>核对后再继续；工作区归属不会自动更改。</p>
              <details className={styles.intakeDetails}>
                <summary>查看目录位置和原因</summary>
                <code>{intake.configured_directory ?? "未设置"}</code>
                <p>{intake.directory_message} {intake.repair_action}</p>
              </details>
            </>) : !intake.configured_directory ? (<p className={styles.intakeDescription}>尚未选择素材目录。请先到<Link href="/settings">设置</Link>选择目录。</p>) : !intake.bound_to_current_workspace ? (<div className={styles.intakeRow}>
              <div className={styles.intakeCopy}>
                <p className={styles.intakeDescription}>接入后每 {intake.scan_interval_seconds} 秒检查新文件，确认复制完成后才接收并分析，不会自动创建视频任务。</p>
                <details className={styles.intakeDetails}>
                  <summary>查看接入位置和检查结果</summary>
                  <code>{intake.configured_directory}</code>
                  {intake.directory_state !== "ready" ? <p>{intake.directory_message} {intake.repair_action ?? ""}</p> : null}
                </details>
              </div>
              <button className={styles.intakePrimary} disabled={intakeBusy !== null || intake.directory_state !== "ready"} onClick={onBind} type="button">
                {intakeBusy === "bind" ? "正在接入…" : "确认接入"}
              </button>
            </div>) : (<>
              <div className={styles.intakeRow}>
                <div className={styles.intakeCopy}>
                  <p className={styles.intakeDescription}>已接入素材目录，每 {intake.scan_interval_seconds} 秒自动检查。外部文件须确认复制完成后才接收；应用内导入会自动确认复制完成。</p>
                  <details className={styles.intakeDetails}>
                    <summary>查看目录位置和检查结果</summary>
                    <code>{intake.bound_directory ?? "未记录"}</code>
                    {intake.directory_state !== "ready" ? <p>{intake.directory_message} {intake.repair_action ?? ""}</p> : null}
                  </details>
                </div>
                {intake.directory_state === "ready" ? (<span className={styles.intakeRow}>
                    <button className={styles.intakeSecondary} disabled={importBusy || busy !== null || intakeBusy !== null} onClick={onLocalImport} type="button">
                      {importBusy ? <LoaderCircle className="is-spinning" size={15}/> : <FolderPlus size={15}/>}
                      {importBusy ? "正在导入…" : "导入本地素材"}
                    </button>
                    <button className={styles.intakeSecondary} disabled={busy !== null || intakeBusy !== null || importBusy} onClick={onScan} type="button">
                      {busy === "scan" ? <LoaderCircle className="is-spinning" size={15}/> : <FolderSearch size={15}/>}
                      {busy === "scan" ? "正在检查…" : "立即检查"}
                    </button>
                  </span>) : null}
              </div>
              {intake.directory_state === "ready" && intake.pending_files.length > 0 ? (<section aria-label="待确认复制完成的文件">
                  <p className={styles.intakeDescription}>请确认这些文件已复制完成。此声明不代表文件已经通过解析或内容校验。</p>
                  {intake.pending_files.map((file) => (<div className={styles.intakeRow} key={file.relative_path}>
                      <code>{file.relative_path}</code>
                      <button className={styles.intakePrimary} disabled={intakeBusy !== null || importBusy || busy !== null} onClick={() => onConfirmCopy(file)} type="button">
                        复制完成，开始接收
                      </button>
                    </div>))}
                </section>) : null}
              {importResult ? (<p className={styles.intakeDescription} role={importResult.error || importResult.failures.length > 0 ? "alert" : "status"}>
                  {importResult.canceled ? "已取消导入，源文件保持不变。" : null}
                  {!importResult.canceled && importResult.error ? importResult.error : null}
                  {!importResult.error && importResult.copied.length > 0
                    ? `已复制 ${importResult.copied.length} 个文件到素材目录：${importResult.copied.map((entry) => entry.fileName).join("、")}。复制完成确认已保存，等待接收并分析，源文件保持不变。`
                    : null}
                  {!importResult.error && importResult.copied.length === 0 && !importResult.canceled
                    && importResult.failures.length === 0 && importResult.skipped.length > 0
                    ? `没有可导入的文件：${importResult.skipped.join("、")}。仅支持 mp4、mov、m4v。`
                    : null}
                  {importResult.failures.length > 0
                    ? `导入失败 ${importResult.failures.length} 个：${importResult.failures.map((entry) => `${entry.sourceName}（${entry.error}）`).join("；")}`
                    : null}
                  {importResult.skipped.length > 0 && importResult.copied.length > 0
                    ? `跳过不支持的文件：${importResult.skipped.join("、")}。`
                    : null}
                  {importResult.warning ? ` ${importResult.warning}` : null}
                </p>) : null}
              {mediaLinkImport}
              <div className={styles.intakeCounts} aria-label="自动接入进度">
                <span>待确认复制 {intake.pending_files.length}</span>
                <span>分析中 {intake.file_counts.running}</span>
                <span>已分析 {intake.file_counts.ready}</span>
                <span>失败 {intake.file_counts.failed}</span>
                <span>等待磁盘 {intake.file_counts.blocked_disk}</span>
              </div>
            </>)}
        </section>);
}
