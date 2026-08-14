/**
 * Cross-platform absolute path detection for directory inputs.
 *
 * The desktop shell fills directory fields from the Electron native dialog
 * (`window.electronAPI.openDirectory()`), which resolves to an absolute path
 * (POSIX on macOS/Linux, Windows on Windows). In a plain browser there is no
 * native dialog, so the user types the path manually; the same check must
 * accept both POSIX and Windows absolute syntax without guessing via an HTML
 * `pattern` attribute.
 *
 * `isAbsolutePath` mirrors Node's `path.isAbsolute` semantics for both
 * `path.posix` and `path.win32`, so the frontend and the Electron/API layer
 * agree on what counts as an absolute path.
 */

const WINDOWS_DRIVE_ABSOLUTE_RE = /^[A-Za-z]:[\\/]/;

export function isAbsolutePath(value: string): boolean {
  if (typeof value !== "string" || value.length === 0) {
    return false;
  }

  const first = value.charCodeAt(0);
  // POSIX absolute ("/foo"), Windows rooted-on-current-drive ("\foo"), and
  // Windows UNC ("\\server\share") all begin with a separator.
  if (first === 47 /* "/" */ || first === 92 /* "\" */) {
    return true;
  }

  // Windows drive-letter absolute ("C:\foo", "C:/foo"). A drive-relative path
  // such as "C:foo" is not absolute and falls through to `false`.
  return WINDOWS_DRIVE_ABSOLUTE_RE.test(value);
}

export type AbsolutePathValidation =
  | { kind: "empty" }
  | { kind: "absolute"; path: string }
  | { kind: "relative"; path: string };

export function validateAbsolutePath(value: string): AbsolutePathValidation {
  const trimmed = value.trim();

  if (trimmed.length === 0) {
    return { kind: "empty" };
  }

  if (isAbsolutePath(trimmed)) {
    return { kind: "absolute", path: trimmed };
  }

  return { kind: "relative", path: trimmed };
}
