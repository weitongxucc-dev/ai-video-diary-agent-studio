"use client";

import { useEffect, useState, type MouseEvent } from "react";
import {
  hasDirectoryPicker,
  hasWavFilePicker,
  pickDirectory,
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
  kind?: "directory" | "wav";
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
  const [showBrowserHint, setShowBrowserHint] = useState(false);

  useEffect(() => {
    setShowBrowserHint(
      props.kind === "wav" ? !hasWavFilePicker() : !hasDirectoryPicker(),
    );
  }, [props.kind]);

  async function handleClick(event: MouseEvent<HTMLButtonElement>) {
    // React clears `currentTarget` once dispatch ends, so capture it before
    // awaiting the native dialog.
    const button = event.currentTarget;
    const result = props.kind === "wav" ? await pickWavFile() : await pickDirectory();
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
        className="ui-button secondary settings-pick-button"
        onClick={handleClick}
        type="button"
      >
        {props.kind === "wav" ? "选择 WAV" : "选择目录"}
      </button>
      {showBrowserHint ? (
        <small className="settings-field-helper">{BROWSER_HINT}</small>
      ) : null}
    </span>
  );
}
