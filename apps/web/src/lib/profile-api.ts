import { API_BASE } from "./api";
import { isLocalAuthMode } from "./auth/routing";
import { ensureLocalMemberIdentity, localMemberTokenHeader, readLocalMemberToken } from "./local-member-auth";
import type { CreatorProfile, CreatorProfileUpdate } from "./types";

export class CreatorProfileApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "CreatorProfileApiError";
  }
}

async function profileRequest(path: string, init?: RequestInit): Promise<Response> {
  let memberHeaders: Record<string, string> = {};
  if (isLocalAuthMode()) {
    if (!readLocalMemberToken()) await ensureLocalMemberIdentity();
    memberHeaders = localMemberTokenHeader();
    if (!Object.keys(memberHeaders).length) throw new Error("无法确认当前创作者，请重新确认身份后再读取偏好。");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
      ...memberHeaders,
    },
    credentials: "same-origin",
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    let detail: unknown;
    try { detail = JSON.parse(body)?.detail; } catch { detail = body; }
    throw new CreatorProfileApiError(response.status, typeof detail === "string" && detail ? detail : `创作偏好请求失败（${response.status}）`);
  }

  return response;
}

export async function getCreatorProfile(): Promise<CreatorProfile> {
  return (await profileRequest("/api/creator-profile")).json() as Promise<CreatorProfile>;
}

export async function updateCreatorProfile(
  update: CreatorProfileUpdate,
): Promise<CreatorProfile> {
  return (await profileRequest("/api/creator-profile", {
    method: "PATCH",
    body: JSON.stringify(update),
  })).json() as Promise<CreatorProfile>;
}

export async function exportCreatorPreferences(): Promise<Blob> {
  return (await profileRequest("/api/assistant/memory/user.md", { headers: { Accept: "text/markdown" } })).blob();
}
