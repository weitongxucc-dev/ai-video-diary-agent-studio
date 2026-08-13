import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { bootstrapPersonalWorkspace } from "@/lib/auth/bootstrap";
import { readableAuthError } from "@/lib/auth/errors";
import { sanitizeNextPath } from "@/lib/auth/routing";
import { createClient } from "@/lib/supabase/server";

const OTP_TYPES = new Set<EmailOtpType>([
  "email",
  "email_change",
  "invite",
  "magiclink",
  "recovery",
  "signup",
]);

function callbackError(requestUrl: URL, error: unknown): NextResponse {
  const target = new URL("/signin", requestUrl);
  target.searchParams.set("error", readableAuthError(error));
  return NextResponse.redirect(target);
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const tokenHash = url.searchParams.get("token_hash");
  const rawType = url.searchParams.get("type");
  const next = sanitizeNextPath(url.searchParams.get("next"));

  try {
    const supabase = await createClient();
    let error: { message: string } | null = null;

    if (code) {
      ({ error } = await supabase.auth.exchangeCodeForSession(code));
    } else if (tokenHash && rawType && OTP_TYPES.has(rawType as EmailOtpType)) {
      ({ error } = await supabase.auth.verifyOtp({
        token_hash: tokenHash,
        type: rawType as EmailOtpType,
      }));
    } else {
      return callbackError(url, new Error("Token has expired or is invalid"));
    }

    if (error) return callbackError(url, error);

    const { data, error: userError } = await supabase.auth.getUser();
    if (userError || !data.user) return callbackError(url, userError);

    const bootstrap = await bootstrapPersonalWorkspace(supabase, data.user.email);
    if (!bootstrap.ok) return callbackError(url, new Error(bootstrap.message));

    if (code || rawType === "invite" || rawType === "recovery") {
      const passwordUrl = new URL("/auth/update-password", url);
      passwordUrl.searchParams.set(
        "mode",
        rawType === "recovery" ? "recovery" : "invite",
      );
      return NextResponse.redirect(passwordUrl);
    }

    const destination = bootstrap.nextPath === "/" && next ? next : bootstrap.nextPath;
    return NextResponse.redirect(new URL(destination, url));
  } catch (error) {
    return callbackError(url, error);
  }
}
