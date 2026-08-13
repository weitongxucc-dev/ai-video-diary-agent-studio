export type SupabasePublicConfig = {
  url: string;
  publishableKey: string;
};

export const SUPABASE_CONFIG_ERROR =
  "登录服务尚未配置。请在环境变量中设置 NEXT_PUBLIC_SUPABASE_URL 和 NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY，然后重新启动应用。";

export function getSupabasePublicConfig():
  | { ok: true; config: SupabasePublicConfig }
  | { ok: false; message: string } {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const publishableKey =
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim();

  if (!url || !publishableKey) {
    return { ok: false, message: SUPABASE_CONFIG_ERROR };
  }

  try {
    const parsedUrl = new URL(url);
    if (parsedUrl.protocol !== "https:" && parsedUrl.hostname !== "localhost") {
      return { ok: false, message: SUPABASE_CONFIG_ERROR };
    }
  } catch {
    return { ok: false, message: SUPABASE_CONFIG_ERROR };
  }

  return { ok: true, config: { url, publishableKey } };
}
