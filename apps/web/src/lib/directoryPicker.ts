import { readLocalMemberToken } from "./local-member-auth";
import { isLocalAuthMode } from "./auth/routing";

export type MaterialImportIdentity = {
  localMemberToken?: string;
  authorization?: string;
};

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
      companionStatus?: () => Promise<CompanionStatus>;
      configureCompanion?: (input: { address: string; accessToken: string; workspaceId: string }) => Promise<CompanionStatus>;
      disableCompanion?: () => Promise<CompanionStatus>;
      companionInvitation?: (input: { purpose: "pair"; token: string; expires_at: string; workspace_id: string }) => Promise<CompanionInvitationResult>;
      setCardMediaEscape?: (cardId: string | null) => void;
      onCardMediaEscape?: (callback: (cardId: string) => void) => () => void;
      openDirectory?: () => Promise<string | null>;
      openFile?: () => Promise<string | null>;
      openWavFile?: () => Promise<string | null>;
      importMaterials?: (identity: MaterialImportIdentity) => Promise<MaterialImportResult>;
      saveRenderedVideo?: (taskId: string) => Promise<{
        saved: boolean;
        canceled?: boolean;
        fileName?: string;
        error?: string;
      }>;
      saveCoverImage?: (taskId: string, coverId: string) => Promise<{
        saved: boolean;
        canceled?: boolean;
        fileName?: string;
        error?: string;
      }>;
      saveSubtitleFile?: (selection: {
        taskId: string;
        expectedVersionId: string;
        mode: "zh" | "en" | "zh-en";
        zhTrackId?: string;
        enTrackId?: string;
      }) => Promise<{
        saved: boolean;
        canceled?: boolean;
        fileName?: string;
        error?: string;
      }>;
    };
  }
}

export type CompanionStatus = { enabled: boolean; endpoint: string | null; addresses: string[]; device_id: string };
export type CompanionInvitationResult = {
  payload: { type: "yaohe-companion"; version: 1; purpose: "pair" | "invite"; token: string; expires_at: string; workspace_id: string; device_id: string; endpoint: string; tls_ca_pem: string; auth: { supabase_url: string; publishable_key: string } };
  qr_data_url: string;
};

export type DirectoryPickResult =
  | { kind: "picked"; path: string }
  | { kind: "canceled" }
  | { kind: "unavailable" }
  | { kind: "failed" };

export type MaterialImportFailure = { sourceName: string; error: string };

export type MaterialImportResult = {
  canceled?: boolean;
  copied?: { sourceName: string; fileName: string }[];
  skipped?: string[];
  failures?: MaterialImportFailure[];
  error?: string;
  warning?: string;
};

export function hasMaterialImporter(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.electronAPI?.importMaterials === "function"
  );
}

/**
 * Ask the desktop shell to import local material. The renderer passes no path:
 * the shell reads the controlled intake binding and returns only what it copied.
 */
export async function importLocalMaterials(): Promise<
  MaterialImportResult | { kind: "unavailable" }
> {
  if (!hasMaterialImporter()) {
    return { kind: "unavailable" };
  }
  try {
    const token = readLocalMemberToken();
    const identity: MaterialImportIdentity = token ? { localMemberToken: token } : {};
    if (!isLocalAuthMode()) {
      const { createClient } = await import("./supabase/client");
      const { data, error } = await createClient().auth.getSession();
      if (error || !data.session) return { error: "请先登录，再导入素材。" };
      identity.authorization = `Bearer ${data.session.access_token}`;
    }
    return (await window.electronAPI?.importMaterials?.(identity)) ?? { canceled: true };
  } catch {
    return { error: "导入失败，源文件保持不变。" };
  }
}

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

export function hasFilePicker(): boolean {
  return typeof window !== "undefined" && typeof window.electronAPI?.openFile === "function";
}

export async function pickFile(): Promise<DirectoryPickResult> {
  if (!hasFilePicker()) return { kind: "unavailable" };
  try {
    const path = await window.electronAPI!.openFile!();
    return path ? { kind: "picked", path } : { kind: "canceled" };
  } catch {
    return { kind: "failed" };
  }
}
