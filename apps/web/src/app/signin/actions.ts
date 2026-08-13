"use server";

import { redirect } from "next/navigation";
import { bootstrapPersonalWorkspace } from "@/lib/auth/bootstrap";
import { readableAuthError } from "@/lib/auth/errors";
import { sanitizeNextPath } from "@/lib/auth/routing";
import { createClient } from "@/lib/supabase/server";

function signInErrorUrl(message: string, next: string | null): string {
  const params = new URLSearchParams({ error: message });
  if (next) params.set("next", next);
  return `/signin?${params.toString()}`;
}

export async function signIn(formData: FormData): Promise<never> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = sanitizeNextPath(String(formData.get("next") ?? ""));

  if (!email || !password) {
    redirect(signInErrorUrl("请输入受邀邮箱和密码。", next));
  }

  let destination: string | null = null;
  let failure: string | null = null;
  try {
    const supabase = await createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      failure = readableAuthError(error);
    } else {
      const { data, error: userError } = await supabase.auth.getUser();
      if (userError || !data.user) {
        failure = readableAuthError(userError);
      } else {
        const bootstrap = await bootstrapPersonalWorkspace(supabase, data.user.email);
        if (!bootstrap.ok) {
          failure = bootstrap.message;
        } else {
          destination = bootstrap.nextPath === "/" && next ? next : bootstrap.nextPath;
        }
      }
    }
  } catch (error) {
    failure = readableAuthError(error);
  }

  if (failure || !destination) {
    redirect(signInErrorUrl(failure ?? "登录失败，请重试。", next));
  }
  redirect(destination);
}
