"use client";

import type { ChangeEvent } from "react";

function readSelectedDirectoryName(event: ChangeEvent<HTMLInputElement>): string {
  const files = event.target.files;
  if (!files || files.length === 0) {
    return "";
  }
  const first = files[0] as File & { webkitRelativePath?: string };
  const relative = first.webkitRelativePath ?? "";
  if (!relative) {
    return "";
  }
  return relative.split("/")[0] ?? "";
}

/**
 * Folder picker used inside server-rendered settings forms. Browsers cannot
 * hand us an absolute path, so we copy the picked folder name into the
 * sibling text input and let the user confirm the full path.
 */
export function DirectoryPickerButton({ targetName }: { targetName: string }) {
  return (
    <label className="pixel-button secondary settings-pick-button">
      选择目录
      <input
        multiple
        name={`${targetName}__picker`}
        onChange={(event) => {
          const folderName = readSelectedDirectoryName(event);
          if (!folderName) {
            return;
          }
          const textInput = event.currentTarget
            .closest(".settings-field-row")
            ?.querySelector(`input[name="${targetName}"]`) as
            | HTMLInputElement
            | null;
          if (textInput) {
            textInput.value = folderName;
          }
        }}
        type="file"
        // @ts-expect-error: webkitdirectory is a non-standard attribute for picking folders.
        webkitdirectory=""
        directory=""
      />
    </label>
  );
}
