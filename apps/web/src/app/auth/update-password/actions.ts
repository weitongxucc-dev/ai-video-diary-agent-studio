"use server";

import { redirect } from "next/navigation";
import { readableAuthError } from "@/lib/auth/errors";
import { createClient } from "@/lib/supabase/server";

export async function updatePassword(formData: FormData): Promise<never> {
  const password = String(formData.get("password") ?? "");
  const confirmation = String(formData.get("password_confirmation") ?? "");
  const mode = String(formData.get("mode") ?? "setup");

  if (password.length < 10 || password !== confirmation) {
    const params = new URLSearchParams({
      mode,
      error: "密码至少需要 10 位，且两次输入必须一致。",
    });
    redirect(`/auth/update-password?${params.toString()}`);
  }

  let failure: string | null = null;
  try {
    const supabase = await createClient();
    const { data } = await supabase.auth.getClaims();
    if (!data?.claims?.sub) {
      failure = "账号设置会话已失效，请联系管理员重新设置。";
    } else {
      const { error } = await supabase.auth.updateUser({ password });
      if (error) failure = readableAuthError(error);
    }
  } catch (error) {
    failure = readableAuthError(error);
  }

  if (failure) {
    const params = new URLSearchParams({ mode, error: failure });
    redirect(`/auth/update-password?${params.toString()}`);
  }

  redirect("/onboarding");
}
