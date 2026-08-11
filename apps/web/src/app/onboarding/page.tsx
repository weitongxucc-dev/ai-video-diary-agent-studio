import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { OnboardingChecklist } from "@/components/OnboardingChecklist";
import { getRuntimeHealth, getRuntimeSettings, getSetupStatus, getSystemStatus } from "@/lib/api";
import { buildOnboardingSnapshot, summarizeOnboardingHealth } from "@/lib/onboarding";

export const dynamic = "force-dynamic";

async function loadOnboardingSnapshot() {
  const [setupResult, healthResult, settingsResult, statusResult] = await Promise.allSettled([
    getSetupStatus(),
    getRuntimeHealth(),
    getRuntimeSettings(),
    getSystemStatus(),
  ]);

  return buildOnboardingSnapshot({
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

export default async function OnboardingPage() {
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
          className="workspace-main pixel-card onboarding-main"
        >
          <div className="workspace-header">
            <div>
              <span className="section-kicker">First-run Readiness</span>
              <h1 id="onboarding-heading">创作者启动检查</h1>
              <p>
                集中检查 AI 模型、本地目录、系统依赖是否就绪。
                不会展示任何密钥原文，只会显示「已配置 / 未配置」状态。
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
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

          {(snapshot.sources.setupError ||
            snapshot.sources.healthError ||
            snapshot.sources.settingsError ||
            snapshot.sources.statusError) ? (
            <div className="onboarding-source-errors" role="alert">
              <strong>部分检查接口未连通：</strong>
              <ul>
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
        </section>

        <aside className="workspace-side onboarding-side">
          <div className="pixel-card side-note">
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
                  <Link href="/">回到今日工作台开始第一条 Episode</Link>
                </li>
              ) : (
                <li>
                  <Link href="/welcome">使用引导式 onboarding 重新检查</Link>
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
          <div className="pixel-card side-note">
            <h2>隐私边界</h2>
            <p>
              本页面只显示每项配置的「已配置 / 未配置」状态与必要说明。
              任何标记为 secret 的字段都不会回显其值。
            </p>
          </div>
          <div className="pixel-card side-note">
            <h2>数据来源</h2>
            <ul className="onboarding-source-list">
              <li>
                <code>GET /api/is_setup</code>
                <span>{snapshot.sources.setup ? "已就绪" : "未连通"}</span>
              </li>
              <li>
                <code>GET /api/runtime/health</code>
                <span>{snapshot.sources.health ? "已就绪" : "未连通"}</span>
              </li>
              <li>
                <code>GET /api/settings/runtime</code>
                <span>{snapshot.sources.settings ? "已就绪" : "未连通"}</span>
              </li>
              <li>
                <code>GET /api/system/status</code>
                <span>{snapshot.sources.status ? "已就绪" : "未连通"}</span>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
