import Link from "next/link";
import { type ChangeEvent } from "react";
import { updateRuntimeSettingsAction } from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { FormErrorBanner } from "@/components/FormErrorBanner";
import { getRuntimeSettings } from "@/lib/api";
import type {
  RuntimeSettingItem,
  RuntimeSettings,
  SystemStatus,
} from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadSettings(): Promise<{
  runtime: RuntimeSettings | null;
  offline: boolean;
}> {
  try {
    return { runtime: await getRuntimeSettings(), offline: false };
  } catch {
    return { runtime: null, offline: true };
  }
}

type SectionDef = {
  id: string;
  title: string;
  description: string;
  tone: "blue" | "green" | "orange" | "purple";
};

type FieldDef = {
  name: string;
  label: string;
  helper?: string;
  kind: "text" | "secret" | "directory" | "textarea";
  defaultValue?: string;
  placeholder?: string;
  configured?: boolean;
};

const SECTIONS: SectionDef[] = [
  {
    id: "model",
    title: "AI 模型",
    description:
      "对话、选题、文案都依赖 DeepSeek。API Key 不会明文返回，留空表示保持原值。",
    tone: "blue",
  },
  {
    id: "paths",
    title: "本地目录",
    description:
      "Obsidian、视频、剪映与数据库都要求本地路径存在；点击「选择目录」会用浏览器原生目录选择器填入。",
    tone: "green",
  },
  {
    id: "asr",
    title: "语音识别 (ASR)",
    description:
      "腾讯云 ASR 与本地 Whisper 二选一或叠加；填错会导致字幕生成失败。",
    tone: "orange",
  },
  {
    id: "sources",
    title: "外部信息源",
    description:
      "Product Hunt、Hermes Radar 与外链 URL 用于选题池；URL 用换行或逗号分隔。",
    tone: "purple",
  },
];

function readSelectedDirectoryName(event: ChangeEvent<HTMLInputElement>): string {
  const files = event.target.files;
  if (!files || files.length === 0) {
    return "";
  }
  const first = files[0] as File & { webkitRelativePath?: string };
  const relative = first.webkitRelativePath ?? "";
  if (!relative) {
    return "";
  }
  return relative.split("/")[0] ?? "";
}

function statusTone(configured: boolean): "green" | "red" {
  return configured ? "green" : "red";
}

function statusLabel(configured: boolean): string {
  return configured ? "已配置" : "未配置";
}

function getSetting(
  settings: RuntimeSettingItem[],
  name: string,
): RuntimeSettingItem | null {
  return settings.find((entry) => entry.name === name) ?? null;
}

function buildFields(
  settings: RuntimeSettingItem[],
  status: SystemStatus,
): Record<string, FieldDef[]> {
  const lookup = (name: string): RuntimeSettingItem | null =>
    getSetting(settings, name);
  const valueOf = (name: string): string =>
    lookup(name)?.value ?? "";
  const configuredOf = (name: string): boolean =>
    lookup(name)?.configured ?? false;

  return {
    model: [
      {
        name: "deepseek_api_key",
        label: "DeepSeek API Key",
        helper: "密钥不会回显，留空表示保持现有值。",
        kind: "secret",
        placeholder: configuredOf("DEEPSEEK_API_KEY")
          ? "已配置 · 留空不修改"
          : "粘贴新密钥",
        configured: configuredOf("DEEPSEEK_API_KEY"),
      },
      {
        name: "deepseek_model",
        label: "DeepSeek Model",
        helper: "默认 deepseek-chat，可改为 deepseek-reasoner 等其它模型。",
        kind: "text",
        defaultValue: valueOf("DEEPSEEK_MODEL") || "deepseek-chat",
      },
    ],
    paths: [
      {
        name: "obsidian_vault_path",
        label: "Obsidian Vault 路径",
        helper: "存放选题素材与每日日记的 Vault 根目录。",
        kind: "directory",
        defaultValue: valueOf("OBSIDIAN_VAULT_PATH"),
        placeholder: "/Users/you/Documents/ObsidianVault",
      },
      {
        name: "video_inbox_path",
        label: "视频待处理目录",
        helper: "新视频落到这里，会被自动扫描。",
        kind: "directory",
        defaultValue: valueOf("VIDEO_INBOX_PATH"),
        placeholder: "/Users/you/Movies/AI-Video-Inbox",
      },
      {
        name: "video_output_path",
        label: "视频输出目录",
        helper: "剪完的成片和重录报告写到这里。",
        kind: "directory",
        defaultValue: valueOf("VIDEO_OUTPUT_PATH"),
        placeholder: "/Users/you/Movies/AI-Video-Output",
      },
      {
        name: "draft_output_path",
        label: "剪映草稿目录",
        helper: "剪映草稿素材包 (subtitle / cover / video) 输出位置。",
        kind: "directory",
        defaultValue: valueOf("DRAFT_OUTPUT_PATH"),
        placeholder: "/Users/you/Movies/JianyingDrafts",
      },
      {
        name: "jianying_draft_root_path",
        label: "剪映根目录（自动导入检测）",
        helper: "10.7 检测会扫描这里，确认草稿可编辑。",
        kind: "directory",
        defaultValue: valueOf("JIANYING_DRAFT_ROOT_PATH"),
        placeholder: "/Users/you/Library/.../JianyingPro",
      },
      {
        name: "database_path",
        label: "SQLite 数据库",
        helper: "存放 Episode、选题、反馈事件等结构化数据。",
        kind: "directory",
        defaultValue: valueOf("DATABASE_PATH"),
        placeholder: "/Users/you/.ai-video-diary/studio.db",
      },
    ],
    asr: [
      {
        name: "tencentcloud_secret_id",
        label: "Tencent SecretId",
        helper: "腾讯云访问凭证 SecretId。",
        kind: "secret",
        placeholder: configuredOf("TENCENTCLOUD_SECRET_ID")
          ? "已配置 · 留空不修改"
          : "粘贴 SecretId",
        configured: configuredOf("TENCENTCLOUD_SECRET_ID"),
      },
      {
        name: "tencentcloud_secret_key",
        label: "Tencent SecretKey",
        helper: "腾讯云访问凭证 SecretKey。",
        kind: "secret",
        placeholder: configuredOf("TENCENTCLOUD_SECRET_KEY")
          ? "已配置 · 留空不修改"
          : "粘贴 SecretKey",
        configured: configuredOf("TENCENTCLOUD_SECRET_KEY"),
      },
      {
        name: "tencentcloud_asr_region",
        label: "ASR Region",
        helper: "腾讯云 ASR 服务区域，常见 ap-shanghai / ap-guangzhou。",
        kind: "text",
        defaultValue:
          valueOf("TENCENTCLOUD_ASR_REGION") || status.asr.region || "ap-shanghai",
      },
      {
        name: "tencentcloud_asr_engine_model_type",
        label: "ASR Engine",
        helper: "腾讯云引擎模型类型，常见 16k_zh_en / 16k_zh。",
        kind: "text",
        defaultValue:
          valueOf("TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE") ||
          status.asr.engine_model_type ||
          "16k_zh_en",
      },
      {
        name: "local_whisper_python_path",
        label: "Local Whisper Python",
        helper: "本地 Whisper 解释器绝对路径，例如 /opt/homebrew/bin/python3。",
        kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_PYTHON_PATH"),
        placeholder: "/opt/homebrew/bin/python3",
      },
      {
        name: "local_whisper_model_path",
        label: "Local Whisper Model",
        helper: "本地 Whisper 模型目录或文件路径。",
        kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_MODEL_PATH"),
        placeholder: "/Users/you/.cache/whisper/medium",
      },
      {
        name: "local_whisper_language",
        label: "Local Whisper Language",
        helper: "默认 zh；空表示自动检测。",
        kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_LANGUAGE") || "zh",
      },
    ],
    sources: [
      {
        name: "product_hunt_token",
        label: "Product Hunt Token",
        helper: "可选；不填则跳过 Product Hunt 来源。",
        kind: "secret",
        placeholder: configuredOf("PRODUCT_HUNT_TOKEN")
          ? "已配置 · 留空不修改"
          : "粘贴 Product Hunt Token",
        configured: configuredOf("PRODUCT_HUNT_TOKEN"),
      },
      {
        name: "hermes_radar_path",
        label: "Hermes Radar 路径",
        helper: "Hermes 日报落盘的目录路径。",
        kind: "directory",
        defaultValue: valueOf("HERMES_RADAR_PATH"),
        placeholder: "/Users/you/.ai-video-diary/hermes-radar",
      },
      {
        name: "topic_source_urls",
        label: "Topic Source URLs",
        helper: "外链素材列表，每行一个 URL 或逗号分隔。",
        kind: "textarea",
        defaultValue: status.external_sources.urls.join("\n"),
      },
    ],
  };
}

function FieldRow({ field }: { field: FieldDef }) {
  if (field.kind === "secret") {
    return (
      <label className="settings-field">
        <span>
          {field.label}
          <span
            className={`settings-secret-badge ${statusTone(Boolean(field.configured))}`}
          >
            {statusLabel(Boolean(field.configured))}
          </span>
        </span>
        <input
          aria-label={field.label}
          autoComplete="off"
          maxLength={field.name === "deepseek_api_key" ? 256 : 512}
          minLength={field.name === "deepseek_api_key" ? 20 : 4}
          name={field.name}
          pattern={
            field.name === "deepseek_api_key"
              ? "^sk-[A-Za-z0-9_-]{8,}$"
              : undefined
          }
          placeholder={field.placeholder}
          title={
            field.name === "deepseek_api_key"
              ? "DeepSeek API Key 必须以 sk- 开头，长度 ≥ 20"
              : `${field.label} 至少 4 字符`
          }
          type="password"
        />
        {field.helper ? (
          <small className="settings-field-helper">{field.helper}</small>
        ) : null}
      </label>
    );
  }

  if (field.kind === "directory") {
    return (
      <label className="settings-field">
        <span>{field.label}</span>
        <div className="settings-field-row">
          <input
            aria-label={field.label}
            maxLength={512}
            name={field.name}
            pattern="^[/~\u4e00-\u9fa5\w.\-\s]*$"
            placeholder={field.placeholder}
            title="仅支持绝对路径，禁止特殊符号"
            type="text"
            defaultValue={field.defaultValue ?? ""}
          />
          <label className="pixel-button secondary settings-pick-button">
            选择目录
            <input
              multiple
              name={`${field.name}__picker`}
              onChange={(event) => {
                const folderName = readSelectedDirectoryName(event);
                if (!folderName) {
                  return;
                }
                const textInput = event.currentTarget
                  .closest(".settings-field-row")
                  ?.querySelector(`input[name="${field.name}"]`) as
                  | HTMLInputElement
                  | null;
                if (textInput) {
                  textInput.value = folderName;
                }
              }}
              type="file"
              // @ts-expect-error: webkitdirectory is a non-standard attribute for picking folders.
              webkitdirectory=""
              directory=""
            />
          </label>
        </div>
        {field.helper ? (
          <small className="settings-field-helper">{field.helper}</small>
        ) : null}
      </label>
    );
  }

  if (field.kind === "textarea") {
    return (
      <label className="settings-field wide">
        <span>{field.label}</span>
        <textarea
          aria-label={field.label}
          maxLength={2000}
          name={field.name}
          rows={4}
          title="每行一个以 http(s):// 开头的 URL"
          defaultValue={field.defaultValue ?? ""}
        />
        {field.helper ? (
          <small className="settings-field-helper">{field.helper}</small>
        ) : null}
      </label>
    );
  }

  return (
    <label className="settings-field">
      <span>{field.label}</span>
      <input
        aria-label={field.label}
        maxLength={512}
        minLength={1}
        name={field.name}
        placeholder={field.placeholder}
        title={`${field.label} 最多 512 字符`}
        type="text"
        defaultValue={field.defaultValue ?? ""}
      />
      {field.helper ? (
        <small className="settings-field-helper">{field.helper}</small>
      ) : null}
    </label>
  );
}

export default async function SettingsPage() {
  const { runtime, offline } = await loadSettings();
  const settingItems = runtime?.settings ?? [];
  const status = runtime?.status;
  const fields = buildFields(settingItems, runtime?.status ?? defaultStatus);
  const sourceUrls = runtime?.status?.external_sources?.urls?.join("\n") ?? "";

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
                  : "分区展示运行时配置，密钥不会明文回显。"}
              </p>
            </div>
            <Link className="pixel-button secondary" href="/">
              返回工作台
            </Link>
          </div>

          <FormErrorBanner
            action={updateRuntimeSettingsAction}
            className="settings-form"
            emptyMessage="请检查设置表单字段"
          >
            {SECTIONS.map((section) => {
              const sectionFields = fields[section.id] ?? [];
              return (
                <fieldset
                  aria-labelledby={`settings-section-${section.id}`}
                  className={`settings-section ${section.tone}`}
                  key={section.id}
                >
                  <legend className="settings-section-legend">
                    <span
                      className={`settings-section-light ${section.tone}`}
                    />
                    <strong id={`settings-section-${section.id}`}>
                      {section.title}
                    </strong>
                  </legend>
                  <p className="settings-section-description">
                    {section.description}
                  </p>
                  <div className="settings-grid">
                    {sectionFields.map((field) => (
                      <FieldRow field={field} key={field.name} />
                    ))}
                  </div>
                </fieldset>
              );
            })}

            <div className="settings-actions">
              <button className="pixel-button" disabled={offline} type="submit">
                保存配置
              </button>
              <span>保存后会重载本地 API 依赖，密钥不会回显。</span>
            </div>
          </FormErrorBanner>
        </section>

        <aside className="workspace-side">
          <div className="pixel-card side-note">
            <h2>安全边界</h2>
            <p>页面不会展示 DeepSeek、Product Hunt 或腾讯云密钥原文。密钥输入框留空时不会修改现有值。</p>
          </div>
          <div className="pixel-card side-note">
            <h2>当前状态</h2>
            <p>DeepSeek：{status?.llm.configured ? "已配置" : "未配置"}</p>
            <p>腾讯云 ASR：{status?.asr.configured ? "已配置" : "未配置"}</p>
            <p>本地 Whisper：{status?.asr.local_whisper_configured ? "可用" : "未配置"}</p>
            <p>ASR 引擎：{status?.asr.engine_model_type ?? "--"}</p>
            <p>ffmpeg：{status?.video.ffmpeg_available ? "可用" : "不可用"}</p>
            <p>SQLite：{status?.storage.database_exists ? "已创建" : "未创建"}</p>
            <code>{sourceUrls ? `外部源 ${runtime?.status?.external_sources?.urls?.length ?? 0} 条` : "无外部源"}</code>
          </div>
          <div className="pixel-card side-note">
            <h2>法律信息</h2>
            <nav aria-label="法律信息" className="settings-legal-links">
              <Link href="/privacy">隐私政策 →</Link>
              <Link href="/terms">使用条款 →</Link>
            </nav>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}

const defaultStatus: SystemStatus = {
  llm: {
    provider: "deepseek",
    configured: false,
    model: null,
    base_url: "",
  },
  obsidian: {
    configured: false,
    exists: false,
    path: null,
    markdown_count: 0,
  },
  product_hunt: {
    configured: false,
    api_url: "",
  },
  external_sources: {
    configured: false,
    urls: [],
  },
  hermes_radar: {
    configured: false,
    exists: false,
    path: null,
    latest_report: null,
    total_count: 0,
    product_hunt_count: 0,
    watcha_count: 0,
  },
  video: {
    inbox_path: "",
    output_path: "",
    inbox_exists: false,
    output_exists: false,
    pending_count: 0,
    ffmpeg_available: false,
  },
  storage: {
    database_path: "",
    database_exists: false,
  },
  asr: {
    provider: "tencent",
    configured: false,
    region: "ap-shanghai",
    engine_model_type: "16k_zh_en",
    max_local_audio_mb: 0,
    local_whisper_configured: false,
    local_whisper_python_path: null,
    local_whisper_model_path: null,
  },
};