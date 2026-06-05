import Link from "next/link";
import { updateRuntimeSettingsAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { getRuntimeSettings } from "@/lib/api";
import type { RuntimeSettings } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadSettings(): Promise<{ runtime: RuntimeSettings | null; offline: boolean }> {
  try {
    return { runtime: await getRuntimeSettings(), offline: false };
  } catch {
    return { runtime: null, offline: true };
  }
}

export default async function SettingsPage() {
  const { runtime, offline } = await loadSettings();
  const settingMap = new Map(
    (runtime?.settings ?? []).map((item) => [item.name, item.value ?? ""]),
  );
  const sourceUrls = runtime?.status.external_sources.urls.join("\n") ?? "";

  return (
    <AppShell>
      <div className="workspace-page">
        <section className="workspace-main pixel-card">
          <div className="workspace-header">
            <div>
              <span className="section-kicker">设置</span>
              <h1>本地运行配置</h1>
              <p>
                {offline
                  ? "暂时无法连接本地 API。"
                  : "这里只显示安全配置快照，密钥不会明文返回前端。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <form action={updateRuntimeSettingsAction} className="settings-form">
            <div className="settings-grid">
              <label className="settings-field">
                <span>DeepSeek Model</span>
                <input
                  name="deepseek_model"
                  type="text"
                  defaultValue={settingMap.get("DEEPSEEK_MODEL") ?? ""}
                />
              </label>
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
                <span>Product Hunt Token</span>
                <input
                  autoComplete="off"
                  name="product_hunt_token"
                  placeholder={settingMap.get("PRODUCT_HUNT_TOKEN") === "已配置" ? "留空不修改" : "可选"}
                  type="password"
                />
              </label>
              <label className="settings-field">
                <span>Tencent SecretId</span>
                <input
                  autoComplete="off"
                  name="tencentcloud_secret_id"
                  placeholder={settingMap.get("TENCENTCLOUD_SECRET_ID") === "已配置" ? "留空不修改" : "粘贴 SecretId"}
                  type="password"
                />
              </label>
              <label className="settings-field">
                <span>Tencent SecretKey</span>
                <input
                  autoComplete="off"
                  name="tencentcloud_secret_key"
                  placeholder={settingMap.get("TENCENTCLOUD_SECRET_KEY") === "已配置" ? "留空不修改" : "粘贴 SecretKey"}
                  type="password"
                />
              </label>
              <label className="settings-field">
                <span>ASR Region</span>
                <input
                  name="tencentcloud_asr_region"
                  type="text"
                  defaultValue={settingMap.get("TENCENTCLOUD_ASR_REGION") ?? "ap-shanghai"}
                />
              </label>
              <label className="settings-field">
                <span>ASR Engine</span>
                <input
                  name="tencentcloud_asr_engine_model_type"
                  type="text"
                  defaultValue={settingMap.get("TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE") ?? "16k_zh_en"}
                />
              </label>
              <label className="settings-field">
                <span>Local Whisper Python</span>
                <input
                  name="local_whisper_python_path"
                  type="text"
                  defaultValue={settingMap.get("LOCAL_WHISPER_PYTHON_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Local Whisper Model</span>
                <input
                  name="local_whisper_model_path"
                  type="text"
                  defaultValue={settingMap.get("LOCAL_WHISPER_MODEL_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Local Whisper Language</span>
                <input
                  name="local_whisper_language"
                  type="text"
                  defaultValue={settingMap.get("LOCAL_WHISPER_LANGUAGE") ?? "zh"}
                />
              </label>
              <label className="settings-field">
                <span>Obsidian Vault</span>
                <input
                  name="obsidian_vault_path"
                  type="text"
                  defaultValue={settingMap.get("OBSIDIAN_VAULT_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Hermes Radar</span>
                <input
                  name="hermes_radar_path"
                  type="text"
                  defaultValue={settingMap.get("HERMES_RADAR_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Video Inbox</span>
                <input
                  name="video_inbox_path"
                  type="text"
                  defaultValue={settingMap.get("VIDEO_INBOX_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Video Output</span>
                <input
                  name="video_output_path"
                  type="text"
                  defaultValue={settingMap.get("VIDEO_OUTPUT_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Draft Output</span>
                <input
                  name="draft_output_path"
                  type="text"
                  defaultValue={settingMap.get("DRAFT_OUTPUT_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>Jianying Root</span>
                <input
                  name="jianying_draft_root_path"
                  type="text"
                  defaultValue={settingMap.get("JIANYING_DRAFT_ROOT_PATH") ?? ""}
                />
              </label>
              <label className="settings-field">
                <span>SQLite DB</span>
                <input
                  name="database_path"
                  type="text"
                  defaultValue={settingMap.get("DATABASE_PATH") ?? ""}
                />
              </label>
              <label className="settings-field wide">
                <span>Topic Source URLs</span>
                <textarea
                  name="topic_source_urls"
                  rows={3}
                  defaultValue={sourceUrls}
                />
              </label>
            </div>
            <div className="settings-actions">
              <button className="pixel-button" disabled={offline} type="submit">
                保存配置
              </button>
              <span>保存后会重载本地 API 依赖，密钥不会回显。</span>
            </div>
          </form>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>安全边界</h2>
            <p>页面不会展示 DeepSeek、Product Hunt 或腾讯云密钥原文。密钥输入框留空时不会修改现有值。</p>
          </div>
          <div className="pixel-card side-note">
            <h2>当前状态</h2>
            <p>DeepSeek：{runtime?.status.llm.configured ? "已配置" : "未配置"}</p>
            <p>腾讯云 ASR：{runtime?.status.asr.configured ? "已配置" : "未配置"}</p>
            <p>本地 Whisper：{runtime?.status.asr.local_whisper_configured ? "可用" : "未配置"}</p>
            <p>ASR 引擎：{runtime?.status.asr.engine_model_type ?? "--"}</p>
            <p>ffmpeg：{runtime?.status.video.ffmpeg_available ? "可用" : "不可用"}</p>
            <p>SQLite：{runtime?.status.storage.database_exists ? "已创建" : "未创建"}</p>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
