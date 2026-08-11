import type {
  CreatorProfile,
  CreatorProfileUpdate,
  CreatorProfileLearnResult,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function profileRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text().catch(() => "Unknown error");
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function getCreatorProfile(): Promise<CreatorProfile> {
  return profileRequest<CreatorProfile>("/api/creator-profile");
}

export async function updateCreatorProfile(
  update: CreatorProfileUpdate,
): Promise<CreatorProfile> {
  return profileRequest<CreatorProfile>("/api/creator-profile", {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export async function learnCreatorProfile(): Promise<CreatorProfileLearnResult> {
  return profileRequest<CreatorProfileLearnResult>(
    "/api/creator-profile/learn",
    { method: "POST" },
  );
}
