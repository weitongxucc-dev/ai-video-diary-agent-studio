import Link from "next/link";
import Image from "next/image";
import { redirect } from "next/navigation";
import { LockKeyhole, UserRound } from "lucide-react";
import { signIn } from "./actions";
import { getSupabasePublicConfig } from "@/lib/supabase/config";
import { createClient } from "@/lib/supabase/server";

type SignInPageProps = { searchParams?: Promise<{ error?: string; next?: string }> };

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const params = searchParams ? await searchParams : {};
  const config = getSupabasePublicConfig();
  if (config.ok) {
    try {
      const supabase = await createClient();
      const { data } = await supabase.auth.getClaims();
      if (data?.claims?.sub) redirect("/");
    } catch { /* Render a recoverable sign-in state. */ }
  }
  const error = !config.ok
    ? "账号服务尚未配置，请联系管理员完成部署设置。"
    : params.error === "configuration"
      ? "账号服务暂时不可用，请联系管理员检查配置。"
      : params.error;

  return (
    <main className="modern-auth-page">
      <section className="modern-auth-card" aria-labelledby="signin-heading">
        <div className="modern-auth-brand"><span><Image alt="" aria-hidden height={20} src="/yaohe-icon.png" unoptimized width={20} /></span><strong>曜核1.0</strong></div>
        <div className="modern-auth-heading"><span>桌面账号</span><h1 id="signin-heading">登录工作区</h1><p>输入管理员为你设置的账号和密码。</p></div>
        {error ? <div className="modern-auth-error" role="alert">{error}</div> : null}
        <form action={signIn} className="modern-auth-form">
          <input name="next" type="hidden" value={params.next ?? ""} />
          <label><span>账号</span><div><UserRound size={16} /><input autoCapitalize="none" autoComplete="username" maxLength={32} minLength={3} name="account" pattern="[A-Za-z0-9._-]{3,32}" placeholder="例如：dujiangyan" required spellCheck={false} type="text" /></div></label>
          <label><span>密码</span><div><LockKeyhole size={16} /><input autoComplete="current-password" minLength={10} name="password" required type="password" /></div></label>
          <button disabled={!config.ok} type="submit">登录</button>
        </form>
        <div className="modern-auth-help"><p>忘记账号或密码？请让工作区管理员直接重置。</p><nav aria-label="法律信息"><Link href="/privacy">隐私政策</Link><Link href="/terms">使用条款</Link></nav></div>
      </section>
    </main>
  );
}
