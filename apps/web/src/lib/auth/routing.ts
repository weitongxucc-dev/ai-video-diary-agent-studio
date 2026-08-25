const PUBLIC_PATHS = new Set([
  "/signin",
  "/privacy",
  "/terms",
  "/auth/callback",
  "/auth/update-password",
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

const ONBOARDING_SUPPORT_PATHS = new Set([
  "/onboarding",
  "/settings",
  "/health",
  "/welcome",
]);

const ONBOARDING_SUPPORT_API_PATHS = new Set([
  "/api/onboarding/complete",
  "/api/settings/runtime",
]);

export function isLocalAuthMode(): boolean {
  return process.env.NEXT_PUBLIC_AUTH_MODE === "local";
}

export function isPublicAuthPath(pathname: string): boolean {
  return PUBLIC_PATHS.has(pathname) || pathname.startsWith("/auth/signout");
}

export function isPublicApiPath(pathname: string): boolean {
  return PUBLIC_API_PATHS.has(pathname);
}

export function isOnboardingSupportPath(pathname: string): boolean {
  return ONBOARDING_SUPPORT_PATHS.has(pathname);
}

export function isOnboardingSupportApiPath(pathname: string): boolean {
  return (
    ONBOARDING_SUPPORT_API_PATHS.has(pathname) ||
    pathname === "/api/models" ||
    pathname.startsWith("/api/models/") ||
    pathname.startsWith("/api/model-install-jobs/")
  );
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
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  ) {
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
