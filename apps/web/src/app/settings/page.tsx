import Link from "next/link";
import {
  createVoiceProfileAction,
  setDefaultVoiceProfileAction,
  updateProductionProfileAction,
  updateRuntimeSettingsAction,
} from "@/app/actions";
import { AppShell } from "@/components/AppShell";
import { DirectoryPickerButton } from "@/components/DirectoryPickerButton";
import { FormErrorBanner } from "@/components/FormErrorBanner";
import { LlmProviderFields } from "@/components/LlmProviderFields";
import { ModelInstallPanel } from "@/components/ModelInstallPanel";
import {
  getLocalModels,
  getProductionFoundation,
  getRuntimeSettings,
  getVoiceProfiles,
} from "@/lib/api.server";
import type {
  LocalModelStatus,
  ProductionFoundation,
  RuntimeSettingItem,
  RuntimeSettings,
  SystemStatus,
  VoiceProfile,
} from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadSettings(): Promise<{
  runtime: RuntimeSettings | null;
  models: LocalModelStatus[];
  foundation: ProductionFoundation | null;
  voices: VoiceProfile[];
  offline: boolean;
}> {
  const [runtime, catalog, foundation, voiceCatalog] = await Promise.all([
    getRuntimeSettings().catch(() => null),
    getLocalModels().catch(() => ({ models: [] })),
    getProductionFoundation().catch(() => null),
    getVoiceProfiles().catch(() => ({ voices: [], total_count: 0 })),
  ]);
  return {
    runtime,
    models: catalog.models,
    foundation,
    voices: voiceCatalog.voices,
    offline: runtime === null,
  };
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
  kind: "text" | "secret" | "directory" | "textarea" | "select";
  options?: { value: string; label: string }[];
  defaultValue?: string;
  placeholder?: string;
  configured?: boolean;
};

const SECTIONS: SectionDef[] = [
  {
    id: "paths",
    title: "本地目录",
    description:
      "Obsidian、视频、剪映、数据库与本地模型都使用绝对路径；大模型建议放在空间充足的移动硬盘。",
    tone: "green",
  },
  {
    id: "asr",
    title: "语音识别 (ASR)",
    description:
      "默认本地 Whisper；只有主动选择腾讯云才会外发音频，失败不自动切换。",
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
  const valueOf = (name: string): string => lookup(name)?.value ?? "";
  const configuredOf = (name: string): boolean =>
    lookup(name)?.configured ?? false;

  return {
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
        helper: "存放创作内容、选题和反馈等结构化数据。",
        kind: "directory",
        defaultValue: valueOf("DATABASE_PATH"),
        placeholder: "/Users/you/.ai-video-diary/studio.db",
      },
      {
        name: "model_root_path",
        label: "本地模型与缓存目录",
        helper:
          "模型、运行环境和下载缓存会优先放在这里；移动硬盘未挂载时安装会直接停止，不会回退到内置硬盘。",
        kind: "directory",
        defaultValue: valueOf("MODEL_ROOT_PATH"),
        placeholder: "/Volumes/External/AI视频日记/models",
      },
      {
        name: "temporary_root_path",
        label: "临时处理目录",
        helper:
          "抽帧、ASR、配音和草稿中间文件写到这里；应与模型一起放在移动硬盘。",
        kind: "directory",
        defaultValue: valueOf("TEMPORARY_ROOT_PATH"),
        placeholder: "/Volumes/External/AI视频日记/temporary",
      },
    ],
    asr: [
      {
        name: "local_whisper_backend", label: "本地运行方式", kind: "select",
        defaultValue: valueOf("LOCAL_WHISPER_BACKEND") || "openai_whisper",
        options: [{ value: "whisper_cpp", label: "whisper.cpp（原生运行）" }, { value: "openai_whisper", label: "OpenAI Whisper（Python）" }],
      },
      {
        name: "local_whisper_cli_path", label: "whisper.cpp 程序", kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_CLI_PATH"), helper: "选择 whisper.cpp 时使用；模型须为 GGML 格式。",
      },
      {
        name: "asr_provider", label: "转写方式", kind: "select",
        defaultValue: valueOf("ASR_PROVIDER") || "local_whisper",
        options: [{ value: "local_whisper", label: "本地 Whisper" }, { value: "tencentcloud", label: "腾讯云（音频外发，可能产生费用）" }],
      },
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
          valueOf("TENCENTCLOUD_ASR_REGION") ||
          status.asr.region ||
          "ap-shanghai",
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
        helper: "模型文件路径：whisper.cpp 使用 ggml .bin，Python 使用 .pt。",
        kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_MODEL_PATH"),
        placeholder: "/Users/you/.cache/whisper/medium",
      },
      {
        name: "local_whisper_language",
        label: "Local Whisper Language",
        helper: "auto 自动检测；也可指定 zh 中文或 en 英文。",
        kind: "text",
        defaultValue: valueOf("LOCAL_WHISPER_LANGUAGE") || "auto",
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
  if (field.kind === "select") {
    return <label className="settings-field"><span>{field.label}</span>
      <select aria-label={field.label} name={field.name} defaultValue={field.defaultValue}>
        {field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      {field.helper ? <small className="settings-field-helper">{field.helper}</small> : null}
    </label>;
  }
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
            placeholder={field.placeholder}
            title="支持 POSIX 与 Windows 绝对路径，留空保持现有值"
            type="text"
            defaultValue={field.defaultValue ?? ""}
          />
          <DirectoryPickerButton targetName={field.name} />
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

type SettingsCategory =
  "model" | "production" | "voices" | "paths" | "asr" | "sources";

const SETTINGS_CATEGORIES: Array<{
  id: SettingsCategory;
  label: string;
  description: string;
}> = [
  { id: "model", label: "模型与 API", description: "模型服务、能力与连接" },
  { id: "production", label: "生产默认值", description: "文案时长与品牌" },
  { id: "voices", label: "声音库", description: "默认声音与授权" },
  { id: "paths", label: "存储与本地模型", description: "移动硬盘与输出" },
  { id: "asr", label: "语音识别", description: "云端与本地 ASR" },
  { id: "sources", label: "信息源", description: "知识库与外部来源" },
];

export default async function SettingsPage({
  searchParams,
}: {
  searchParams?: Promise<{ section?: string }>;
}) {
  const params = searchParams ? await searchParams : {};
  const requestedSection = params.section ?? "model";
  const activeCategory = SETTINGS_CATEGORIES.some(
    (item) => item.id === requestedSection,
  )
    ? (requestedSection as SettingsCategory)
    : "model";
  const { runtime, models, foundation, voices, offline } = await loadSettings();
  const profile = foundation?.profile;
  const settingItems = runtime?.settings ?? [];
  const status = runtime?.status;
  const fields = buildFields(settingItems, runtime?.status ?? defaultStatus);
  const activeRuntimeSection = SECTIONS.find(
    (section) => section.id === activeCategory,
  );

  return (
    <AppShell
      inspector={
        <div className="settings-status-inspector">
          <h3>当前生效配置</h3>
          <dl>
            <div>
              <dt>文案模型</dt>
              <dd>
                {status?.llm.configured
                  ? `${status.llm.provider} / ${status.llm.model}`
                  : "本地摘要模式"}
              </dd>
            </div>
            <div>
              <dt>语音识别</dt>
              <dd>
                {status?.asr.configured
                  ? status.asr.engine_model_type
                  : status?.asr.local_whisper_configured
                    ? "本地 Whisper"
                    : "未配置"}
              </dd>
            </div>
            <div>
              <dt>ffmpeg</dt>
              <dd>{status?.video.ffmpeg_available ? "可用" : "不可用"}</dd>
            </div>
            <div>
              <dt>数据库</dt>
              <dd>{status?.storage.database_exists ? "已创建" : "未创建"}</dd>
            </div>
          </dl>
          <p>密钥只显示是否已配置，不会返回原文。</p>
        </div>
      }
      inspectorTitle="设置状态"
    >
      <div className="workspace-page settings-page">
        <aside className="settings-category-panel" aria-label="设置分类">
          <div className="settings-category-heading">
            <span className="section-kicker">偏好设置</span>
            <strong>工作区配置</strong>
          </div>
          <nav className="settings-category-nav">
            {SETTINGS_CATEGORIES.map((item) => (
              <Link
                aria-current={activeCategory === item.id ? "page" : undefined}
                className={activeCategory === item.id ? "is-active" : ""}
                href={`/settings?section=${item.id}`}
                key={item.id}
              >
                <strong>{item.label}</strong>
                <span>{item.description}</span>
              </Link>
            ))}
          </nav>
          <nav aria-label="法律信息" className="settings-legal-links">
            <Link href="/privacy">隐私政策</Link>
            <Link href="/terms">使用条款</Link>
          </nav>
        </aside>
        <section className="workspace-main ui-panel">
          {offline ? <p role="alert">暂时无法连接本地服务。</p> : null}

          {activeCategory === "model" ? (
            <FormErrorBanner
              action={updateRuntimeSettingsAction}
              className="settings-form settings-focused-form"
              emptyMessage="请检查模型连接配置"
            >
              <input name="settings_section" type="hidden" value="model" />
              <div className="settings-section-header">
                <div>
                  <h2>文案与选题模型</h2>
                  <p>
                    选择模型服务后再选择具体模型；也可以接入自有服务或本地
                    OpenAI-compatible 服务。
                  </p>
                </div>
                <span
                  className={`settings-connection-state ${status?.llm.configured ? "is-ready" : ""}`}
                >
                  {status?.llm.configured ? "已连接" : "本地降级模式"}
                </span>
              </div>
              <LlmProviderFields
                apiKeyConfigured={Boolean(
                  getSetting(settingItems, "LLM_API_KEY")?.configured,
                )}
                baseUrl={
                  valueOfSetting(settingItems, "LLM_BASE_URL") ||
                  "https://api.deepseek.com"
                }
                model={
                  valueOfSetting(settingItems, "LLM_MODEL") || "deepseek-chat"
                }
                provider={
                  valueOfSetting(settingItems, "LLM_PROVIDER") || "deepseek"
                }
              />
              <div className="settings-actions">
                <button
                  className="ui-button"
                  disabled={offline}
                  type="submit"
                >
                  保存模型连接
                </button>
                <span>保存后立即用于新的选题和文案任务。</span>
              </div>
            </FormErrorBanner>
          ) : null}

          {profile &&
          (activeCategory === "production" || activeCategory === "voices") ? (
            <div className="settings-product-block">
              <div
                className="settings-product-heading"
                hidden={activeCategory !== "production"}
              >
                <div>
                  <span className="section-kicker">生产默认值</span>
                  <h2>文案与成片</h2>
                  <p>
                    新内容默认以 90 秒为目标，但可按工作区设置制作 5
                    分钟或更长视频。
                  </p>
                </div>
              </div>
              <FormErrorBanner
                action={updateProductionProfileAction}
                className="settings-form settings-product-form"
                emptyMessage="请检查生产默认值"
                hidden={activeCategory !== "production"}
              >
                <div className="settings-grid">
                  <label className="settings-field">
                    <span>工作区名称</span>
                    <input
                      name="workspace_name"
                      required
                      defaultValue={profile.workspace_name}
                    />
                  </label>
                  <label className="settings-field">
                    <span>品牌名称</span>
                    <input
                      name="brand_name"
                      defaultValue={profile.brand_name}
                    />
                  </label>
                  <label className="settings-field">
                    <span>最短成片（秒）</span>
                    <input
                      name="target_duration_min_seconds"
                      type="number"
                      min={5}
                      max={1800}
                      required
                      defaultValue={profile.target_duration_min_seconds}
                    />
                    <small className="settings-field-helper">
                      文案和混剪可接受的下限。
                    </small>
                  </label>
                  <label className="settings-field">
                    <span>最长成片（秒）</span>
                    <input
                      name="target_duration_max_seconds"
                      type="number"
                      min={5}
                      max={1800}
                      required
                      defaultValue={profile.target_duration_max_seconds}
                    />
                    <small className="settings-field-helper">
                      300 秒即 5 分钟，上限可设为 1800 秒。
                    </small>
                  </label>
                  <label className="settings-field">
                    <span>品牌模板</span>
                    <input
                      name="brand_template_name"
                      defaultValue={profile.brand_template_name}
                    />
                  </label>
                  <label className="settings-field">
                    <span>字幕风格</span>
                    <input
                      name="subtitle_style"
                      defaultValue={profile.subtitle_style}
                    />
                  </label>
                </div>
                <input
                  type="hidden"
                  name="brand_colors"
                  value={profile.brand_colors.join(",")}
                />
                <input
                  type="hidden"
                  name="intro_text"
                  value={profile.intro_text}
                />
                <input
                  type="hidden"
                  name="outro_text"
                  value={profile.outro_text}
                />
                <input
                  type="hidden"
                  name="background_music_path"
                  value={profile.background_music_path ?? ""}
                />
                <input
                  type="hidden"
                  name="voice_name"
                  value={profile.voice_name}
                />
                <input
                  type="hidden"
                  name="voice_reference_path"
                  value={profile.voice_reference_path ?? ""}
                />
                <input
                  type="hidden"
                  name="voice_reference_text"
                  value={profile.voice_reference_text}
                />
                <input
                  type="hidden"
                  name="source_urls"
                  value={profile.source_urls.join("\n")}
                />
                {profile.voice_consent_confirmed ? (
                  <input
                    type="hidden"
                    name="voice_consent_confirmed"
                    value="on"
                  />
                ) : null}
                {profile.use_obsidian ? (
                  <input type="hidden" name="use_obsidian" value="on" />
                ) : null}
                {profile.use_hermes ? (
                  <input type="hidden" name="use_hermes" value="on" />
                ) : null}
                {profile.cloud_enhancement_enabled ? (
                  <input
                    type="hidden"
                    name="cloud_enhancement_enabled"
                    value="on"
                  />
                ) : null}
                <div className="settings-actions">
                  <button className="ui-button" type="submit">
                    保存生产默认值
                  </button>
                  <span>后续可在单个内容中临时覆盖时长。</span>
                </div>
              </FormErrorBanner>

              <section
                className="settings-voice-section"
                aria-labelledby="voice-library-title"
                hidden={activeCategory !== "voices"}
              >
                <div className="settings-product-heading">
                  <div>
                    <span className="section-kicker">授权资产</span>
                    <h2 id="voice-library-title">声音库</h2>
                    <p>
                      可保存多个声音。默认声音用于新内容，单条创作内容
                      仍可临时切换。
                    </p>
                  </div>
                </div>
                <div className="settings-voice-list">
                  {voices.map((voice) => (
                    <div className="settings-voice-row" key={voice.id}>
                      <div className="settings-voice-copy">
                        <strong>{voice.name}</strong>
                        <span>
                          {voice.is_default ? "默认声音" : "可选声音"} ·{" "}
                          {voice.status === "ready"
                            ? "已就绪"
                            : voice.message || voice.status}
                        </span>
                      </div>
                      <audio
                        controls
                        preload="none"
                        src={`/api/production/voices/${voice.id}/media`}
                      />
                      {voice.is_default ? (
                        <span className="settings-voice-default">当前默认</span>
                      ) : (
                        <form action={setDefaultVoiceProfileAction}>
                          <input
                            type="hidden"
                            name="voice_profile_id"
                            value={voice.id}
                          />
                          <input type="hidden" name="name" value={voice.name} />
                          <input
                            type="hidden"
                            name="reference_audio_path"
                            value={voice.reference_audio_path}
                          />
                          <input
                            type="hidden"
                            name="reference_text"
                            value={voice.reference_text}
                          />
                          <input
                            type="hidden"
                            name="consent_confirmed"
                            value={String(voice.consent_confirmed)}
                          />
                          <button
                            className="ui-button secondary"
                            type="submit"
                          >
                            设为默认
                          </button>
                        </form>
                      )}
                    </div>
                  ))}
                  {!voices.length ? (
                    <p className="settings-voice-empty">
                      还没有可用声音，请添加已获授权的 WAV 参考音频。
                    </p>
                  ) : null}
                </div>
                <FormErrorBanner
                  action={createVoiceProfileAction}
                  className="settings-voice-create"
                  emptyMessage="请补全声音授权信息"
                >
                  <div className="settings-grid">
                    <label className="settings-field">
                      <span>声音名称</span>
                      <input
                        name="name"
                        required
                        placeholder="例如：张三旁白"
                      />
                    </label>
                    <label className="settings-field">
                      <span>WAV 参考音频</span>
                      <div className="settings-field-row">
                        <input
                          name="reference_audio_path"
                          required
                          placeholder="/Volumes/External/voices/name.wav"
                        />
                        <DirectoryPickerButton
                          kind="wav"
                          targetName="reference_audio_path"
                        />
                      </div>
                      <small className="settings-field-helper">
                        仅记录原文件绝对路径，不复制到内置硬盘。
                      </small>
                    </label>
                    <label className="settings-field wide">
                      <span>参考音频文字</span>
                      <textarea
                        name="reference_text"
                        required
                        rows={3}
                        placeholder="逐字填写参考音频中的内容"
                      />
                    </label>
                  </div>
                  <div className="settings-voice-consent">
                    <label>
                      <input
                        name="consent_confirmed"
                        required
                        type="checkbox"
                      />
                      我确认已获得该声音所有者授权
                    </label>
                    <label>
                      <input name="is_default" type="checkbox" />
                      添加后设为默认声音
                    </label>
                  </div>
                  <div className="settings-actions">
                    <button className="ui-button" type="submit">
                      添加声音
                    </button>
                    <span>模型就绪前会保留参考档案和可解释状态。</span>
                  </div>
                </FormErrorBanner>
              </section>
            </div>
          ) : null}

          {activeRuntimeSection ? (
            <FormErrorBanner
              action={updateRuntimeSettingsAction}
              className="settings-form settings-focused-form"
              emptyMessage="请检查设置表单字段"
            >
              <input
                name="settings_section"
                type="hidden"
                value={activeCategory}
              />
              {[activeRuntimeSection].map((section) => {
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

              {activeCategory === "paths" ? (
                <ModelInstallPanel
                  modelRootPath={valueOfSetting(
                    settingItems,
                    "MODEL_ROOT_PATH",
                  )}
                  models={models}
                />
              ) : null}

              <div className="settings-actions">
                <button
                  className="ui-button"
                  disabled={offline}
                  type="submit"
                >
                  保存配置
                </button>
                <span>保存后会重载本地服务；密钥留空时保持原值。</span>
              </div>
            </FormErrorBanner>
          ) : null}
        </section>
      </div>
    </AppShell>
  );
}

function valueOfSetting(settings: RuntimeSettingItem[], name: string): string {
  return settings.find((entry) => entry.name === name)?.value ?? "";
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
