import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const API_PORT = process.env.API_PORT ?? "8000";

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (pathname.startsWith("/api/")) {
    const apiUrl = `http://127.0.0.1:${API_PORT}${pathname}${search}`;

    try {
      const fetchOptions: RequestInit = {
        method: request.method,
        headers: request.headers,
      };

      if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
        fetchOptions.body = await request.clone().arrayBuffer();
      }

      const response = await fetch(apiUrl, fetchOptions);

      return new NextResponse(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    } catch {
      return new NextResponse(
        JSON.stringify({ detail: "API server unreachable" }),
        { status: 502, headers: { "content-type": "application/json" } },
      );
    }
  }
}

export const config = {
  matcher: "/api/:path*",
};
