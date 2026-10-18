import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { getSupabasePublicConfig } from "./config";
import { readVerifiedWorkspaceSession } from "./workspace-session";

export type SessionRefreshResult = {
  response: NextResponse;
  userId: string | null;
  accessToken: string | null;
  onboardingComplete: boolean;
  configurationError: string | null;
};

export async function refreshSession(
  request: NextRequest,
): Promise<SessionRefreshResult> {
  const config = getSupabasePublicConfig();
  let response = NextResponse.next({ request });

  if (!config.ok) {
    return {
      response,
      userId: null,
      accessToken: null,
      onboardingComplete: false,
      configurationError: config.message,
    };
  }

  const supabase = createServerClient(config.config.url, config.config.publishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll(cookiesToSet, headers) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) => {
          response.cookies.set(name, value, options);
        });
        Object.entries(headers).forEach(([name, value]) => {
          response.headers.set(name, value);
        });
      },
    },
  });

  try {
    const session = await readVerifiedWorkspaceSession(supabase);
    return {
      response,
      userId: session.userId,
      accessToken: session.accessToken,
      onboardingComplete: session.onboardingComplete,
      configurationError: null,
    };
  } catch {
    return {
      response,
      userId: null,
      accessToken: null,
      onboardingComplete: false,
      configurationError: null,
    };
  }
}

export function redirectWithRefreshedCookies(
  target: URL,
  refreshedResponse: NextResponse,
): NextResponse {
  const redirect = NextResponse.redirect(target);
  refreshedResponse.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
  for (const header of ["cache-control", "expires", "pragma"]) {
    const value = refreshedResponse.headers.get(header);
    if (value) redirect.headers.set(header, value);
  }
  return redirect;
}

export function copyRefreshedSession(
  target: NextResponse,
  refreshedResponse: NextResponse,
): NextResponse {
  refreshedResponse.cookies.getAll().forEach((cookie) => target.cookies.set(cookie));
  for (const header of ["cache-control", "expires", "pragma"]) {
    const value = refreshedResponse.headers.get(header);
    if (value) target.headers.set(header, value);
  }
  return target;
}
