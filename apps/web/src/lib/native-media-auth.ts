export const LOCAL_MEMBER_TOKEN_HEADER = "X-Local-Member-Token";

const COOKIE_PREFIX = "ai-video-diary-local-member-token-port-";

function effectivePort(protocol: string, port: string): string {
  return port || (protocol === "https:" ? "443" : "80");
}

export function localMemberTokenCookieName(protocol: string, port: string): string {
  return `${COOKIE_PREFIX}${effectivePort(protocol, port)}`;
}

export function localMemberTokenCookieAssignment(
  token: string | null,
  protocol: string,
  port: string,
): string {
  const secure = protocol === "https:" ? "; Secure" : "";
  const name = localMemberTokenCookieName(protocol, port);
  if (token === null) return `${name}=; Path=/api; SameSite=Strict; Max-Age=0${secure}`;
  return `${name}=${encodeURIComponent(token)}; Path=/api; SameSite=Strict${secure}`;
}

const NATIVE_MEDIA_PATHS = [
  /^\/api\/media\/(?:person-profiles|voice-profiles)\/[^/]+\/reference$/,
  /^\/api\/media\/previews\/(?:voice|avatar_lipsync)\/[^/]+\/media$/,
  /^\/api\/creation-tasks\/[^/]+\/person-renders\/[^/]+\/media$/,
  /^\/api\/creation-tasks\/[^/]+\/output$/,
  /^\/api\/production\/materials\/[^/]+\/media$/,
  /^\/api\/production\/montage-plans\/[^/]+\/output$/,
  /^\/api\/creation-tasks\/[^/]+\/audio\/artifacts\/[^/]+$/,
  /^\/api\/creation-tasks\/[^/]+\/narration\/(?:audio|segments\/[^/]+\/audio)$/,
];

export function isLocalNativeMediaRequest(method: string, pathname: string): boolean {
  return (method === "GET" || method === "HEAD")
    && NATIVE_MEDIA_PATHS.some((pattern) => pattern.test(pathname));
}

export function localNativeMediaHeaders(
  headers: Headers,
  cookieToken: string | null | undefined,
  method: string,
  pathname: string,
): Headers {
  const forwarded = new Headers(headers);
  if (!isLocalNativeMediaRequest(method, pathname) || forwarded.has(LOCAL_MEMBER_TOKEN_HEADER)) {
    return forwarded;
  }
  const token = cookieToken?.trim();
  if (token) forwarded.set(LOCAL_MEMBER_TOKEN_HEADER, token);
  return forwarded;
}
