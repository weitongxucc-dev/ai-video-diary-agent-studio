"use server";

import { redirect } from "next/navigation";
import { readableAuthError } from "@/lib/auth/errors";
import { createClient } from "@/lib/supabase/server";

export async function updatePassword(formData: FormData): Promise<never> {
  const password = String(formData.get("password") ?? "");
  const confirmation = String(formData.get("password_confirmation") ?? "");
  const mode = String(formData.get("mode") ?? "invite");

  if (password.length < 8 || password !== confirmation) {
    const params = new URLSearchParams({
      mode,
      error: "密码至少需要 8 位，且两次输入必须一致。",
    });
    redirect(`/auth/update-password?${params.toString()}`);
  }

  let failure: string | null = null;
  try {
    const supabase = await createClient();
    const { data } = await supabase.auth.getClaims();
    if (!data?.claims?.sub) {
      failure = "邀请或恢复会话已失效，请重新打开邮件链接。";
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
