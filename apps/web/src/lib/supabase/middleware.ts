import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { getSupabasePublicConfig } from "./config";

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
    const { data, error } = await supabase.auth.getClaims();
    if (error || !data?.claims?.sub) {
      return {
        response,
        userId: null,
        accessToken: null,
        onboardingComplete: false,
        configurationError: null,
      };
    }
    const { data: sessionData } = await supabase.auth.getSession();
    const { data: onboardingState, error: onboardingError } = await supabase
      .from("onboarding_states")
      .select("completed_at, workspaces!inner(owner_id)")
      .eq("user_id", data.claims.sub)
      .eq("workspaces.owner_id", data.claims.sub)
      .maybeSingle();
    return {
      response,
      userId: data.claims.sub,
      accessToken: sessionData.session?.access_token ?? null,
      onboardingComplete:
        !onboardingError && Boolean(onboardingState?.completed_at),
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
