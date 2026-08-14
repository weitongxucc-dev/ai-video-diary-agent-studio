import { updatePassword } from "./actions";
import { PixelButton } from "@/components/PixelPrimitives";

type UpdatePasswordPageProps = {
  searchParams?: Promise<{ error?: string; mode?: string }>;
};

export default async function UpdatePasswordPage({ searchParams }: UpdatePasswordPageProps) {
  const params = searchParams ? await searchParams : {};
  const recovery = params.mode === "recovery";

  return (
    <main className="auth-page">
      <section className="pixel-card auth-card" aria-labelledby="password-heading">
        <span className="section-kicker">Secure Account</span>
        <h1 id="password-heading">{recovery ? "设置新密码" : "完成邀请"}</h1>
        <p>
          {recovery
            ? "为账号设置一个新密码，完成后继续恢复未完成的启动流程。"
            : "邮箱已确认。请设置登录密码，然后继续创建你的创作者工作区。"}
        </p>

        {params.error ? (
          <div className="welcome-feedback red" role="alert">{params.error}</div>
        ) : null}

        <form action={updatePassword} className="auth-form">
          <input name="mode" type="hidden" value={params.mode ?? "invite"} />
          <label className="auth-field">
            <span>新密码（至少 10 位）</span>
            <input autoComplete="new-password" minLength={10} name="password" required type="password" />
          </label>
          <label className="auth-field">
            <span>再次输入新密码</span>
            <input autoComplete="new-password" minLength={10} name="password_confirmation" required type="password" />
          </label>
          <PixelButton type="submit" variant="primary">保存密码并继续</PixelButton>
        </form>
      </section>
    </main>
  );
}
