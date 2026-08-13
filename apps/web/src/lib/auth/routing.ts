const PUBLIC_PATHS = new Set([
  "/signin",
  "/privacy",
  "/terms",
  "/auth/callback",
]);

const PUBLIC_API_PATHS = new Set([
  "/api/runtime/health",
  "/api/setup/preflight",
  "/api/is_setup",
  "/api/system/status",
  "/api/version",
  "/api/workflow/status",
  "/api/content-templates",
]);

export function isPublicAuthPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || pathname.startsWith("/auth/signout");
}

export function isPublicApiPath(pathname: string): boolean {
  return PUBLIC_API_PATHS.has(pathname);
}

export function trustedApiHeaders(
  incoming: Headers,
  accessToken: string | null,
): Headers {
  const headers = new Headers(incoming);
  headers.delete("authorization");
  if (accessToken) {
    headers.set("authorization", `Bearer ${accessToken}`);
  }
  return headers;
}

export function sanitizeNextPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return null;
  }

  return value.startsWith("/signin") || value.startsWith("/auth/")
    ? null
    : value;
}

export function signInRedirectUrl(requestUrl: URL, reason?: string): URL {
  const target = new URL("/signin", requestUrl);
  const next = `${requestUrl.pathname}${requestUrl.search}`;
  if (sanitizeNextPath(next)) {
    target.searchParams.set("next", next);
  }
  if (reason) {
    target.searchParams.set("error", reason);
  }
  return target;
}
