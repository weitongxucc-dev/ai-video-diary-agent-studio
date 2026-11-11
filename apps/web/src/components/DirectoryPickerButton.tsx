"use client";

import { buttonClassName } from "@/components/ui/button-styles";
import { useEffect, useState, type MouseEvent } from "react";
import {
  hasDirectoryPicker,
  hasFilePicker,
  hasWavFilePicker,
  pickDirectory,
  pickFile,
  pickWavFile,
} from "@/lib/directoryPicker";

/**
 * Two writeback modes, exactly one must be provided:
 *   - onPick: controlled forms (welcome) receive the absolute path.
 *   - targetName: server-rendered uncontrolled forms (settings) get the
 *     sibling text input with that `name` updated in place.
 */
type WritebackProps =
  | { onPick: (path: string) => void; targetName?: never }
  | { onPick?: never; targetName: string };

type DirectoryPickerButtonProps = WritebackProps & {
  kind?: "directory" | "file" | "wav";
};

const BROWSER_HINT = "桌面端可选择目录；浏览器中请手动输入绝对路径。";

/**
 * Shared directory picker used by the settings and welcome forms. In the
 * desktop shell it opens the native dialog via `electronAPI.openDirectory()`
 * and writes the returned absolute path; in a plain browser it never fakes a
 * basename as a path — it shows a non-blocking hint and leaves manual input
 * untouched. Canceling the dialog writes nothing.
 */
export function DirectoryPickerButton(props: DirectoryPickerButtonProps) {
  // Determined after mount so SSR output matches the first client render;
  // the desktop shell never sees the browser hint, and plain browsers get
  // the hint as soon as hydration runs.
  const [error, setError] = useState<string | null>(null);
  const [showBrowserHint, setShowBrowserHint] = useState(false);

  useEffect(() => {
    setShowBrowserHint(
      props.kind === "wav" ? !hasWavFilePicker() : props.kind === "file" ? !hasFilePicker() : !hasDirectoryPicker(),
    );
  }, [props.kind]);

  async function handleClick(event: MouseEvent<HTMLButtonElement>) {
    // React clears `currentTarget` once dispatch ends, so capture it before
    // awaiting the native dialog.
    const button = event.currentTarget;
    setError(null);
    const result = props.kind === "wav" ? await pickWavFile() : props.kind === "file" ? await pickFile() : await pickDirectory();
    if (result.kind === "failed") setError("暂时无法打开文件选择窗口，请重试或手动输入路径。");
    if (result.kind !== "picked") {
      return;
    }
    if (props.onPick) {
      props.onPick(result.path);
      return;
    }
    const input = button
      .closest(".settings-field-row")
      ?.querySelector<HTMLInputElement>(`input[name="${props.targetName}"]`);
    if (input) {
      input.value = result.path;
    }
  }

  return (
    <span className="settings-pick-wrap">
      <button
        className={buttonClassName({ variant: "outline", className: "settings-pick-button" })}
        onClick={handleClick}
        type="button"
      >
        {props.kind === "wav" ? "选择 WAV" : props.kind === "file" ? "选择文件" : "选择目录"}
      </button>
      {error ? <small role="alert">{error}</small> : null}
      {showBrowserHint ? (
        <small className="settings-field-helper">{props.kind === "file" ? "桌面端可选择文件；浏览器中请手动输入绝对路径。" : BROWSER_HINT}</small>
      ) : null}
    </span>
  );
}
