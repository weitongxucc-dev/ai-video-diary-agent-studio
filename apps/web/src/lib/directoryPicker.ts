/**
 * Shared directory-picking bridge.
 *
 * The desktop shell exposes `window.electronAPI.openDirectory()`
 * (see apps/desktop/preload.js → apps/desktop/main.js `open-directory`),
 * which resolves to the picked directory's absolute path, or `null` when the
 * user cancels. Plain browsers cannot obtain an absolute path from a file
 * input, so callers must fall back to manual input instead of faking a path
 * from the picked folder's basename.
 */

declare global {
  interface Window {
    electronAPI?: {
      openDirectory?: () => Promise<string | null>;
      openWavFile?: () => Promise<string | null>;
      saveRenderedVideo?: (taskId: string) => Promise<{
        saved: boolean;
        canceled?: boolean;
        fileName?: string;
        error?: string;
      }>;
    };
  }
}

export type DirectoryPickResult =
  | { kind: "picked"; path: string }
  | { kind: "canceled" }
  | { kind: "unavailable" };

export function hasDirectoryPicker(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.electronAPI?.openDirectory === "function"
  );
}

export async function pickDirectory(): Promise<DirectoryPickResult> {
  if (!hasDirectoryPicker()) {
    return { kind: "unavailable" };
  }
  try {
    const path = await window.electronAPI?.openDirectory?.();
    if (typeof path === "string" && path.length > 0) {
      return { kind: "picked", path };
    }
    return { kind: "canceled" };
  } catch {
    return { kind: "canceled" };
  }
}

export function hasWavFilePicker(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.electronAPI?.openWavFile === "function"
  );
}

export async function pickWavFile(): Promise<DirectoryPickResult> {
  if (!hasWavFilePicker()) {
    return { kind: "unavailable" };
  }
  try {
    const path = await window.electronAPI?.openWavFile?.();
    if (typeof path === "string" && path.length > 0) {
      return { kind: "picked", path };
    }
    return { kind: "canceled" };
  } catch {
    return { kind: "canceled" };
  }
}
