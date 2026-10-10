/**
 * 本地成员令牌读写（localStorage）与请求头注入。
 *
 * 诚实边界（务必保留，改动前先读后端 `apps/api/app/main.py` 的
 * `LOCAL_MEMBER_TOKEN_HEADER` 与 `apps/api/app/local_members.py`）：
 *
 * 1. 后端是「方案 A」：不带 `X-Local-Member-Token` 视为匿名，按 owner 放行；
 *    带上未知令牌则是硬凭据失败（401「成员凭据无效」），不是匿名。
 *    因此**没有令牌时必须完全不注入这个 header**——注入空串/占位值会被后端
 *    当成非法凭据，导致 401。`localMemberTokenHeader()` 在无令牌时返回 `{}`。
 * 2. 该信任模型下 member 不带 header 就能以 owner 身份通过后端，前端
 *    **不能**被视为一层已生效的权限隔离；本模块只负责携带令牌，不做鉴权。
 * 3. 令牌存在 localStorage，服务端渲染读不到：首帧必然按 owner 渲染，加载完
 *    才收敛。这是已知闪烁，cookie 方案才能根治，本包不做。
 * 4. whoami 在方案 A 下对匿名调用方返回种子 owner 及其令牌，属过渡态；
 *    切到严格方案 B 时这里要重新设计。
 * 5. 原生媒体 GET/HEAD 不能带自定义 header；受限会话 cookie 只把同一已有令牌
 *    传给 Next local proxy 的明确媒体路由，后端仍逐请求验证成员、workspace 与授权。
 */

import { LOCAL_MEMBER_TOKEN_HEADER, localMemberTokenCookieAssignment } from "./native-media-auth";

export const LOCAL_MEMBER_TOKEN_STORAGE_KEY = "ai-video-diary-local-member-token";
export { LOCAL_MEMBER_TOKEN_HEADER } from "./native-media-auth";

export type LocalMemberRole = "owner" | "member";

export type LocalMemberIdentity = {
  member_id: string;
  name: string;
  role: LocalMemberRole;
  created_at: string;
  member_token: string;
};

function storage(): Storage | null {
  // SSR 与隐私模式都可能拿不到 localStorage，一律 try/catch 降级。
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readLocalMemberToken(): string | null {
  try {
    const value = storage()?.getItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY);
    const token = value?.trim();
    return token ? token : null;
  } catch {
    return null;
  }
}

export function storeLocalMemberToken(token: string): void {
  const cleaned = token.trim();
  if (!cleaned) return;
  try {
    const localStorage = storage();
    if (!localStorage) return;
    localStorage.setItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY, cleaned);
  } catch {
    // 写不进去（隐私模式/配额）时保持匿名，后端会按方案 A 放行。
    return;
  }
  syncLocalMemberTokenCookie(cleaned);
}

export function clearLocalMemberToken(): void {
  try {
    storage()?.removeItem(LOCAL_MEMBER_TOKEN_STORAGE_KEY);
  } catch {
    // 即使 localStorage 不可写，也清除原生媒体请求使用的会话副本。
  }
  syncLocalMemberTokenCookie(null);
}

function syncLocalMemberTokenCookie(token: string | null): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  try {
    document.cookie = localMemberTokenCookieAssignment(token, window.location.protocol, window.location.port);
  } catch {
    // Cookie transport may be unavailable; API fetches still use localStorage.
  }
}

/**
 * 请求头片段。**没有令牌时返回空对象**——后端靠「header 缺席」判定匿名放行，
 * 任何占位值（空串 / undefined / "null"）都会被当作凭据送去比对并 401。
 */
export function localMemberTokenHeader(): Record<string, string> {
  const token = readLocalMemberToken();
  return token ? { [LOCAL_MEMBER_TOKEN_HEADER]: token } : {};
}

function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? "";
}

/**
 * 调 `GET /api/members/whoami` 取当前身份。
 * 匿名（方案 A）时后端返回种子 owner 及其令牌；传了无效令牌会 401，
 * 由调用方如实展示错误，不在此静默退回匿名。
 */
export async function fetchLocalMemberIdentity(token?: string | null): Promise<LocalMemberIdentity> {
  const effective = token ?? readLocalMemberToken();
  const response = await fetch(`${apiBase()}/api/members/whoami`, {
    method: "GET",
    headers: { Accept: "application/json", ...(effective ? { [LOCAL_MEMBER_TOKEN_HEADER]: effective } : {}) },
    cache: "no-store",
  });
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
    const message = typeof detail === "string" ? detail : `请求失败：${response.status}`;
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  const identity = payload as LocalMemberIdentity | null;
  if (!identity || typeof identity.member_token !== "string" || typeof identity.role !== "string") {
    throw new Error("本地成员身份响应无法解析");
  }
  return identity;
}

/**
 * 读本地令牌；取不到就用匿名 whoami 换一份（方案 A 过渡态会给种子 owner
 * 及其令牌）并存下，供后续请求携带。
 */
export async function ensureLocalMemberIdentity(): Promise<LocalMemberIdentity> {
  const stored = readLocalMemberToken();
  const identity = await fetchLocalMemberIdentity(stored);
  if (identity.member_token) storeLocalMemberToken(identity.member_token);
  return identity;
}
