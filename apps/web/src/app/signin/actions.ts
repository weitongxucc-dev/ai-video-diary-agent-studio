"use server";

import { redirect } from "next/navigation";
import { createHash } from "node:crypto";
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
  const account = String(formData.get("account") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = sanitizeNextPath(String(formData.get("next") ?? ""));

  if (!account || !password) {
    redirect(signInErrorUrl("请输入账号和密码。", next));
  }

  if (!/^[A-Za-z0-9._-]{3,32}$/.test(account)) {
    redirect(signInErrorUrl("账号需为 3–32 位字母、数字、点、横线或下划线。", next));
  }

  // Supabase remains an internal identity provider during migration. Plain
  // account names are deterministically mapped and the provider identifier is
  // never exposed as a product login concept.
  const email = `u_${createHash("sha256").update(account.normalize("NFKC").toLowerCase()).digest("hex").slice(0, 24)}@account.ai-video-diary.local`;

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
        const bootstrap = await bootstrapPersonalWorkspace(supabase, account);
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
