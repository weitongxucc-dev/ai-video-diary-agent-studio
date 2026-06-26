import Link from "next/link";
import { updateRuntimeSettingsAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { PixelBlockMeter } from "@/components/PixelBlockMeter";
import { PixelIcon } from "@/components/PixelIcon";
import { getRuntimeHealth, getRuntimeSettings } from "@/lib/api";
import type {
  RuntimeHealth,
  RuntimeHealthItem,
  RuntimeHealthStatus,
  RuntimeSettings,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const MAX_VISIBLE_HEALTH_ITEMS = 8;

async function loadSetupState(): Promise<{
  runtime: RuntimeSettings | null;
  health: RuntimeHealth | null;
  offline: boolean;
}> {
  try {
    const [runtime, health] = await Promise.all([
      getRuntimeSettings(),
      getRuntimeHealth(),
    ]);
    return { runtime, health, offline: false };
  } catch {
    return { runtime: null, health: null, offline: true };
  }
}

function statusLabel(status: RuntimeHealthStatus): string {
  const labels: Record<RuntimeHealthStatus, string> = {
    degraded: "降级",
    invalid: "无效",
    missing: "缺失",
    ready: "就绪",
  };
  return labels[status];
}

function statusProgress(status: RuntimeHealthStatus): number {
  const progress: Record<RuntimeHealthStatus, number> = {
    degraded: 50,
    invalid: 25,
    missing: 0,
    ready: 100,
  };
  return progress[status];
}

function visibleHealthItems(items: RuntimeHealthItem[]): RuntimeHealthItem[] {
  const priority = ["deepseek", "obsidian", "ffmpeg", "local_whisper"];
  return [...items].sort((left, right) => {
    const leftReady = left.status === "ready";
    const rightReady = right.status === "ready";
    if (leftReady !== rightReady) {
      return leftReady ? 1 : -1;
    }
    const leftIndex = priority.indexOf(left.id);
    const rightIndex = priority.indexOf(right.id);
    if (leftIndex === -1 && rightIndex === -1) {
      return left.label.localeCompare(right.label);
    }
    if (leftIndex === -1) {
      return 1;
    }
    if (rightIndex === -1) {
      return -1;
    }
    return leftIndex - rightIndex;
  }).slice(0, MAX_VISIBLE_HEALTH_ITEMS);
}

export default async function SetupPage() {
  const { runtime, health, offline } = await loadSetupState();
  const settingMap = new Map(
    (runtime?.settings ?? []).map((item) => [item.name, item.value ?? ""]),
  );
  const sourceUrls = runtime?.status.external_sources.urls.join("\n") ?? "";
  const healthItems = visibleHealthItems(health?.items ?? []);
  const hiddenHealthCount = Math.max(
    0,
    (health?.items.length ?? 0) - healthItems.length,
  );
  const completeCount = health?.summary.ready ?? 0;
  const totalCount = health?.summary.total ?? 0;
  const completeProgress = totalCount > 0 ? Math.round((completeCount / totalCount) * 100) : 0;

  return (
    <AppShell>
      <div className="workspace-page setup-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">首次配置向导</span>
              <h1>把本地环境接起来</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "先配置模型、知识库、视频目录、Whisper 和剪映路径，再进入今日生产流程。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/health">
              查看运行健康
            </Link>
          </div>

          <div className="setup-progress-panel">
            <div>
              <strong>{completeCount}/{totalCount}</strong>
              <span>依赖就绪</span>
            </div>
            <PixelBlockMeter value={completeProgress} />
          </div>

          <form action={updateRuntimeSettingsAction} className="settings-form">
            <input name="redirect_to" type="hidden" value="/setup" />

            <div className="setup-step-grid">
              <section className="setup-step-card">
                <div className="setup-step-head">
                  <PixelIcon name="agent" />
                  <div>
                    <span>step 1</span>
                    <h2>基础模型</h2>
                  </div>
                </div>
                <label className="settings-field">
                  <span>DeepSeek API Key</span>
                  <input
                    autoComplete="off"
                    name="deepseek_api_key"
                    placeholder={settingMap.get("DEEPSEEK_API_KEY") === "已配置" ? "留空不修改" : "粘贴新密钥"}
                    type="password"
                  />
                </label>
                <label className="settings-field">
                  <span>DeepSeek Model</span>
                  <input
                    defaultValue={settingMap.get("DEEPSEEK_MODEL") ?? ""}
                    name="deepseek_model"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Seedance API Key</span>
                  <input
                    autoComplete="off"
                    name="seedance_api_key"
                    placeholder={settingMap.get("SEEDANCE_API_KEY") === "已配置" ? "留空不修改" : "粘贴新密钥"}
                    type="password"
                  />
                </label>
                <label className="settings-field">
                  <span>Seedance Base URL</span>
                  <input
                    defaultValue={settingMap.get("SEEDANCE_BASE_URL") ?? ""}
                    name="seedance_base_url"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Seedance Status URL</span>
                  <input
                    defaultValue={settingMap.get("SEEDANCE_STATUS_URL_TEMPLATE") ?? ""}
                    name="seedance_status_url_template"
                    placeholder="https://.../{job_id}"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Seedance Model</span>
                  <input
                    defaultValue={settingMap.get("SEEDANCE_MODEL") ?? "seedance-2-0"}
                    name="seedance_model"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>CosyVoice API Key</span>
                  <input
                    autoComplete="off"
                    name="cosyvoice_api_key"
                    placeholder={settingMap.get("COSYVOICE_API_KEY") === "已配置" ? "留空不修改" : "可选密钥"}
                    type="password"
                  />
                </label>
                <label className="settings-field">
                  <span>CosyVoice Base URL</span>
                  <input
                    defaultValue={settingMap.get("COSYVOICE_BASE_URL") ?? ""}
                    name="cosyvoice_base_url"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>CosyVoice Status URL</span>
                  <input
                    defaultValue={settingMap.get("COSYVOICE_STATUS_URL_TEMPLATE") ?? ""}
                    name="cosyvoice_status_url_template"
                    placeholder="https://.../{job_id}"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>CosyVoice Model</span>
                  <input
                    defaultValue={settingMap.get("COSYVOICE_MODEL") ?? "cosyvoice-v3-flash"}
                    name="cosyvoice_model"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>CosyVoice Voice ID</span>
                  <input
                    defaultValue={settingMap.get("COSYVOICE_VOICE_ID") ?? "default"}
                    name="cosyvoice_voice_id"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>HeyGem API Key</span>
                  <input
                    autoComplete="off"
                    name="heygem_api_key"
                    placeholder={settingMap.get("HEYGEM_API_KEY") === "已配置" ? "留空不修改" : "可选密钥"}
                    type="password"
                  />
                </label>
                <label className="settings-field">
                  <span>HeyGem Base URL</span>
                  <input
                    defaultValue={settingMap.get("HEYGEM_BASE_URL") ?? ""}
                    name="heygem_base_url"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>HeyGem Status URL</span>
                  <input
                    defaultValue={settingMap.get("HEYGEM_STATUS_URL_TEMPLATE") ?? ""}
                    name="heygem_status_url_template"
                    placeholder="https://.../{job_id}"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>HeyGem Avatar ID</span>
                  <input
                    defaultValue={settingMap.get("HEYGEM_AVATAR_ID") ?? "default"}
                    name="heygem_avatar_id"
                    type="text"
                  />
                </label>
              </section>

              <section className="setup-step-card wide">
                <div className="setup-step-head">
                  <PixelIcon name="folder" />
                  <div>
                    <span>step 2</span>
                    <h2>本地路径</h2>
                  </div>
                </div>
                <div className="setup-field-grid">
                  <label className="settings-field">
                    <span>Obsidian Vault</span>
                    <input
                      defaultValue={settingMap.get("OBSIDIAN_VAULT_PATH") ?? ""}
                      name="obsidian_vault_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Video Inbox</span>
                    <input
                      defaultValue={settingMap.get("VIDEO_INBOX_PATH") ?? ""}
                      name="video_inbox_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Video Output</span>
                    <input
                      defaultValue={settingMap.get("VIDEO_OUTPUT_PATH") ?? ""}
                      name="video_output_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Draft Output</span>
                    <input
                      defaultValue={settingMap.get("DRAFT_OUTPUT_PATH") ?? ""}
                      name="draft_output_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>BGM Library</span>
                    <input
                      defaultValue={settingMap.get("BGM_LIBRARY_PATH") ?? ""}
                      name="bgm_library_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>yt-dlp Cookies</span>
                    <input
                      defaultValue={settingMap.get("YT_DLP_COOKIES_PATH") ?? ""}
                      name="yt_dlp_cookies_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Auto Upload API Key</span>
                    <input
                      autoComplete="off"
                      name="social_auto_upload_api_key"
                      placeholder={settingMap.get("SOCIAL_AUTO_UPLOAD_API_KEY") === "已配置" ? "留空不修改" : "可选密钥"}
                      type="password"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Auto Upload Base URL</span>
                    <input
                      defaultValue={settingMap.get("SOCIAL_AUTO_UPLOAD_BASE_URL") ?? ""}
                      name="social_auto_upload_base_url"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Auto Upload Status URL</span>
                    <input
                      defaultValue={settingMap.get("SOCIAL_AUTO_UPLOAD_STATUS_URL_TEMPLATE") ?? ""}
                      name="social_auto_upload_status_url_template"
                      placeholder="https://.../{job_id}"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>Jianying Root</span>
                    <input
                      defaultValue={settingMap.get("JIANYING_DRAFT_ROOT_PATH") ?? ""}
                      name="jianying_draft_root_path"
                      type="text"
                    />
                  </label>
                  <label className="settings-field">
                    <span>SQLite DB</span>
                    <input
                      defaultValue={settingMap.get("DATABASE_PATH") ?? ""}
                      name="database_path"
                      type="text"
                    />
                  </label>
                </div>
              </section>

              <section className="setup-step-card">
                <div className="setup-step-head">
                  <PixelIcon name="script" />
                  <div>
                    <span>step 3</span>
                    <h2>转写能力</h2>
                  </div>
                </div>
                <label className="settings-field">
                  <span>Local Whisper Python</span>
                  <input
                    defaultValue={settingMap.get("LOCAL_WHISPER_PYTHON_PATH") ?? ""}
                    name="local_whisper_python_path"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Local Whisper Model</span>
                  <input
                    defaultValue={settingMap.get("LOCAL_WHISPER_MODEL_PATH") ?? ""}
                    name="local_whisper_model_path"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Local Whisper Language</span>
                  <input
                    defaultValue={settingMap.get("LOCAL_WHISPER_LANGUAGE") ?? "zh"}
                    name="local_whisper_language"
                    type="text"
                  />
                </label>
              </section>

              <section className="setup-step-card">
                <div className="setup-step-head">
                  <PixelIcon name="link" />
                  <div>
                    <span>step 4</span>
                    <h2>信息源</h2>
                  </div>
                </div>
                <label className="settings-field">
                  <span>Hermes Radar</span>
                  <input
                    defaultValue={settingMap.get("HERMES_RADAR_PATH") ?? ""}
                    name="hermes_radar_path"
                    type="text"
                  />
                </label>
                <label className="settings-field">
                  <span>Product Hunt Token</span>
                  <input
                    autoComplete="off"
                    name="product_hunt_token"
                    placeholder={settingMap.get("PRODUCT_HUNT_TOKEN") === "已配置" ? "留空不修改" : "可选"}
                    type="password"
                  />
                </label>
                <label className="settings-field">
                  <span>Topic Source URLs</span>
                  <textarea
                    defaultValue={sourceUrls}
                    name="topic_source_urls"
                    rows={4}
                  />
                </label>
              </section>
            </div>

            <div className="settings-actions">
              <button className="pixel-button" disabled={offline} type="submit">
                保存并重新检查
              </button>
              <span>密钥留空不会覆盖已有配置；保存后会回到配置向导。</span>
            </div>
          </form>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>配置结果</h2>
            <p>DeepSeek：{runtime?.status.llm.configured ? "已配置" : "未配置"}</p>
            <p>Obsidian：{runtime?.status.obsidian.exists ? "可访问" : "未发现"}</p>
            <p>ffmpeg：{runtime?.status.video.ffmpeg_available ? "可用" : "不可用"}</p>
            <p>Whisper：{runtime?.status.asr.local_whisper_configured ? "可用" : "未配置"}</p>
          </div>

          <div className="pixel-card side-note">
            <h2>修复清单</h2>
            <div className="setup-health-list bounded-list">
              {healthItems.length > 0 ? (
                healthItems.map((item) => (
                  <div className={`setup-health-item ${item.status}`} key={item.id}>
                    <div>
                      <strong>{item.label}</strong>
                      <span>{statusLabel(item.status)}</span>
                    </div>
                    <p>{item.reason}</p>
                    <em>{item.repair_action}</em>
                    <PixelBlockMeter value={statusProgress(item.status)} />
                  </div>
                ))
              ) : (
                <div className="setup-health-item missing">
                  <div>
                    <strong>API</strong>
                    <span>缺失</span>
                  </div>
                  <p>无法读取运行健康状态。</p>
                  <em>启动本地 API 后刷新。</em>
                  <PixelBlockMeter value={0} />
                </div>
              )}
              {hiddenHealthCount > 0 ? (
                <p className="bounded-list-note">还有 {hiddenHealthCount} 项依赖在运行健康页查看。</p>
              ) : null}
            </div>
          </div>

          <div className="pixel-card side-note">
            <h2>下一步</h2>
            <p>关键依赖就绪后，回到今日工作台生成选题；降级项可以保留，但内测前需要在日志页导出一次报告包确认状态。</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
