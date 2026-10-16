import Link from "next/link";
import { completeOnboardingAction } from "./actions";
import { AppShell } from "@/components/AppShell";
import { PixelButton } from "@/components/PixelPrimitives";
import { OnboardingChecklist } from "@/components/OnboardingChecklist";
import { getRuntimeHealth, getRuntimeSettings, getSetupPreflight, getSetupStatus, getSystemStatus } from "@/lib/api.server";
import { buildOnboardingSnapshot, summarizeOnboardingHealth } from "@/lib/onboarding";

export const dynamic = "force-dynamic";

async function loadOnboardingSnapshot() {
  const [preflightResult, setupResult, healthResult, settingsResult, statusResult] = await Promise.allSettled([
    getSetupPreflight(),
    getSetupStatus(),
    getRuntimeHealth(),
    getRuntimeSettings(),
    getSystemStatus(),
  ]);

  return buildOnboardingSnapshot({
    preflight: preflightResult.status === "fulfilled" ? preflightResult.value : null,
    preflightError: preflightResult.status === "rejected"
      ? extractMessage(preflightResult.reason)
      : null,
    setup: setupResult.status === "fulfilled" ? setupResult.value : null,
    setupError: setupResult.status === "rejected"
      ? extractMessage(setupResult.reason)
      : null,
    health: healthResult.status === "fulfilled" ? healthResult.value : null,
    healthError: healthResult.status === "rejected"
      ? extractMessage(healthResult.reason)
      : null,
    settings: settingsResult.status === "fulfilled" ? settingsResult.value : null,
    settingsError: settingsResult.status === "rejected"
      ? extractMessage(settingsResult.reason)
      : null,
    status: statusResult.status === "fulfilled" ? statusResult.value : null,
    statusError: statusResult.status === "rejected"
      ? extractMessage(statusResult.reason)
      : null,
  });
}

function extractMessage(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (typeof err === "string") {
    return err;
  }
  return "请求失败";
}

type OnboardingPageProps = {
  searchParams?: Promise<{ error?: string }>;
};

export default async function OnboardingPage({ searchParams }: OnboardingPageProps) {
  const params = searchParams ? await searchParams : {};
  const snapshot = await loadOnboardingSnapshot();
  const summary = summarizeOnboardingHealth(snapshot);
  const generatedAtLabel = new Date(snapshot.generatedAt).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });

  return (
    <AppShell>
      <div className="workspace-page onboarding-page">
        <section
          aria-labelledby="onboarding-heading"
          className="workspace-main ui-panel onboarding-main"
        >
          <div className="workspace-header">
            <div>
              <span className="section-kicker">首次设置</span>
              <h1 id="onboarding-heading">准备你的创作工作区</h1>
              <p>
                先确认既定工作区存储可用。
                模型、人物、声音、素材与媒体工具都可以在需要时配置。
              </p>
            </div>
            {snapshot.ready ? (
              <Link className="ui-button secondary" href="/">
                进入工作台
              </Link>
            ) : null}
          </div>

          <div
            aria-live="polite"
            className={`onboarding-summary tone-${summary.tone}`}
            role="status"
          >
            <div className="onboarding-summary-headline">
              <strong>{summary.headline}</strong>
              <span>{summary.detail}</span>
            </div>
            <div
              aria-label="完成度"
              aria-valuenow={snapshot.completion.percent}
              aria-valuemin={0}
              aria-valuemax={100}
              className="onboarding-progress"
              role="progressbar"
            >
              <span
                className="onboarding-progress-bar"
                style={{ width: `${snapshot.completion.percent}%` }}
              />
              <em>{snapshot.completion.percent}%</em>
            </div>
            <p className="onboarding-summary-meta">
              共 {snapshot.completion.total} 项 · 就绪 {snapshot.completion.ready} ·
              {" "}阻断 {snapshot.blockingCount} · 关注 {snapshot.attentionCount}
              <span> · 检测时间 {generatedAtLabel}</span>
            </p>
          </div>

          {(snapshot.sources.preflightError ||
            snapshot.sources.setupError ||
            snapshot.sources.healthError ||
            snapshot.sources.settingsError ||
            snapshot.sources.statusError) ? (
            <div className="onboarding-source-errors" role="alert">
              <strong>部分检查接口未连通：</strong>
              <ul>
                {snapshot.sources.preflightError ? (
                  <li>/api/setup/preflight · {snapshot.sources.preflightError}</li>
                ) : null}
                {snapshot.sources.setupError ? (
                  <li>/api/is_setup · {snapshot.sources.setupError}</li>
                ) : null}
                {snapshot.sources.healthError ? (
                  <li>/api/runtime/health · {snapshot.sources.healthError}</li>
                ) : null}
                {snapshot.sources.settingsError ? (
                  <li>/api/settings/runtime · {snapshot.sources.settingsError}</li>
                ) : null}
                {snapshot.sources.statusError ? (
                  <li>/api/system/status · {snapshot.sources.statusError}</li>
                ) : null}
              </ul>
              <p>
                这些接口的失败不会阻塞页面渲染，但相应项目会显示「未知」。可以在
                <Link href="/health"> 运行健康</Link>
                页面查看更多诊断信息。
              </p>
            </div>
          ) : null}

          <OnboardingChecklist initialSnapshot={snapshot} />

          {params.error === "completion" ? (
            <div className="welcome-feedback red" role="alert">
              完成启动流程失败。请重新检查后再试；问题持续时请联系内测管理员。
            </div>
          ) : null}

          <div className="onboarding-completion-gate">
            <strong>
              {snapshot.ready ? "工作区存储已就绪" : "还有必需项未就绪"}
            </strong>
            <span>
              {snapshot.ready
                ? `可以进入工作台。${snapshot.attentionCount > 0 ? ` ${snapshot.attentionCount} 项增强能力不会影响进入，可以稍后完成。` : ""}`
                : "请只处理上方「工作区存储」中的阻断项，然后重新检查。"}
            </span>
            {snapshot.ready ? (
              <form action={completeOnboardingAction}>
                <PixelButton type="submit" variant="primary">
                  进入工作台
                </PixelButton>
              </form>
            ) : (
              <PixelButton disabled type="button" variant="primary">
                预检未通过
              </PixelButton>
            )}
          </div>
        </section>

        <aside className="workspace-side onboarding-side">
          <div className="ui-panel side-note">
            <h2>下一步建议</h2>
            <ol className="onboarding-next-steps">
              {snapshot.blockingCount > 0 ? (
                <li>
                  <Link href="/settings">前往设置补齐 {snapshot.blockingCount} 项关键配置</Link>
                </li>
              ) : null}
              {snapshot.attentionCount > 0 ? (
                <li>
                  <Link href="/settings">查看 {snapshot.attentionCount} 项建议关注</Link>
                </li>
              ) : null}
              {snapshot.ready ? (
                <li>
                  <Link href="/">回到今日工作台开始第一条创作内容</Link>
                </li>
              ) : (
                <li>
                  <Link href="/welcome">返回首次使用引导</Link>
                </li>
              )}
              <li>
                <Link href="/welcome">首次使用引导</Link>
              </li>
              <li>
                <Link href="/health">运行健康详情</Link>
              </li>
            </ol>
          </div>
          <div className="ui-panel side-note">
            <h2>隐私边界</h2>
            <p>
              本页面只显示每项配置的「已配置 / 未配置」状态与必要说明。
              密钥和凭据不会回显原文。
            </p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
