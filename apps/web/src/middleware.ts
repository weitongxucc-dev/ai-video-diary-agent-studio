import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  isLocalAuthMode,
  isOnboardingSupportPath,
  isPublicApiPath,
  isPublicAuthPath,
  shouldBlockOnboarding,
  signInRedirectUrl,
  trustedApiHeaders,
} from "@/lib/auth/routing";
import {
  copyRefreshedSession,
  redirectWithRefreshedCookies,
  refreshSession,
} from "@/lib/supabase/middleware";
import { localMemberTokenCookieName, localNativeMediaHeaders } from "@/lib/native-media-auth";

const API_PORT = process.env.API_PORT ?? "8000";

async function proxyApiRequest(
  request: NextRequest,
  accessToken: string | null,
  sessionResponse?: NextResponse,
) {
  const { pathname, search } = request.nextUrl;
  const apiUrl = `http://127.0.0.1:${API_PORT}${pathname}${search}`;

  try {
    const fetchOptions: RequestInit = {
      method: request.method,
      headers: trustedApiHeaders(request.headers, accessToken),
    };
    if (isLocalAuthMode()) {
      const cookieName = localMemberTokenCookieName(request.nextUrl.protocol, request.nextUrl.port);
      fetchOptions.headers = localNativeMediaHeaders(
        fetchOptions.headers as Headers,
        request.cookies.get(cookieName)?.value,
        request.method,
        pathname,
      );
    }

    if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
      fetchOptions.body = await request.clone().arrayBuffer();
    }

    const response = await fetch(apiUrl, fetchOptions);
    const proxied = new NextResponse(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
    return sessionResponse
      ? copyRefreshedSession(proxied, sessionResponse)
      : proxied;
  } catch {
    return new NextResponse(
      JSON.stringify({ detail: "API server unreachable" }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isLocalAuthMode()) {
    if (pathname.startsWith("/api/")) {
      return proxyApiRequest(request, null);
    }

    if (pathname === "/signin" || pathname.startsWith("/auth/")) {
      return NextResponse.redirect(new URL("/", request.nextUrl));
    }

    return NextResponse.next();
  }

  if (pathname.startsWith("/api/")) {
    if (isPublicApiPath(pathname)) {
      return proxyApiRequest(request, null);
    }

    const session = await refreshSession(request);
    if (pathname === "/api/runtime/health" && (!session.userId || !session.accessToken)) {
      return copyRefreshedSession(
        NextResponse.json(
          { error: "authentication_required", detail: "Sign in before reading workspace diagnostics." },
          { status: 401 },
        ),
        session.response,
      );
    }
    if (
      shouldBlockOnboarding(
        session.userId,
        session.onboardingComplete,
        request.method,
        pathname,
      )
    ) {
      return NextResponse.json(
        { error: "onboarding_required", detail: "Complete onboarding first." },
        { status: 403 },
      );
    }
    return proxyApiRequest(request, session.accessToken, session.response);
  }

  const session = await refreshSession(request);
  if (isPublicAuthPath(pathname)) {
    return session.response;
  }

  if (!session.userId) {
    const error = session.configurationError ? "configuration" : undefined;
    return redirectWithRefreshedCookies(
      signInRedirectUrl(request.nextUrl, error),
      session.response,
    );
  }

  if (!session.onboardingComplete && !isOnboardingSupportPath(pathname)) {
    return redirectWithRefreshedCookies(
      new URL("/onboarding", request.nextUrl),
      session.response,
    );
  }

  return session.response;
}

export const config = {
  matcher: [
    "/api/:path*",
    "/((?!_next/static|_next/image|favicon.ico|favicon.svg|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
