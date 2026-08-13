import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  isPublicApiPath,
  isPublicAuthPath,
  signInRedirectUrl,
  trustedApiHeaders,
} from "@/lib/auth/routing";
import {
  copyRefreshedSession,
  redirectWithRefreshedCookies,
  refreshSession,
} from "@/lib/supabase/middleware";

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

  if (pathname.startsWith("/api/")) {
    if (isPublicApiPath(pathname)) {
      return proxyApiRequest(request, null);
    }

    const session = await refreshSession(request);
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

  return session.response;
}

export const config = {
  matcher: [
    "/api/:path*",
    "/((?!_next/static|_next/image|favicon.ico|favicon.svg|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
