import Link from "next/link";
import { redirect } from "next/navigation";
import { signIn } from "./actions";
import { PixelButton } from "@/components/PixelPrimitives";
import { getSupabasePublicConfig } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

type SignInPageProps = {
  searchParams?: Promise<{ error?: string; next?: string }>;
};

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const params = searchParams ? await searchParams : {};
  const config = getSupabasePublicConfig();

  if (config.ok) {
    try {
      const supabase = await createClient();
      const { data } = await supabase.auth.getClaims();
      if (data?.claims?.sub) redirect("/");
    } catch {
      // Render the sign-in form with a useful error instead of crashing.
    }
  }

  const error = !config.ok
    ? config.message
    : params.error === "configuration"
      ? "登录服务配置不完整。请联系内测管理员检查 Supabase 环境变量。"
      : params.error;

  return (
    <main className="auth-page">
      <section className="pixel-card auth-card" aria-labelledby="signin-heading">
        <span className="section-kicker">Invite-only Beta</span>
        <h1 id="signin-heading">登录 AI 视频日记</h1>
        <p>使用管理员邀请的邮箱和你设置的密码登录。这里不提供公开注册。</p>

        {error ? (
          <div className="welcome-feedback red" role="alert">
            {error}
          </div>
        ) : null}

        <form action={signIn} className="auth-form">
          <input name="next" type="hidden" value={params.next ?? ""} />
          <label className="auth-field">
            <span>受邀邮箱</span>
            <input
              autoComplete="email"
              name="email"
              placeholder="you@example.com"
              required
              type="email"
            />
          </label>
          <label className="auth-field">
            <span>密码</span>
            <input
              autoComplete="current-password"
              minLength={10}
              name="password"
              required
              type="password"
            />
          </label>
          <PixelButton disabled={!config.ok} type="submit" variant="primary">
            登录并继续
          </PixelButton>
        </form>

        <div className="auth-help">
          <p>没有邀请或链接已失效？请联系内测管理员重新发送。</p>
          <nav aria-label="法律信息">
            <Link href="/privacy">隐私政策</Link>
            <Link href="/terms">使用条款</Link>
          </nav>
        </div>
      </section>
    </main>
  );
}
