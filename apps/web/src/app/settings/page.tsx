import formStyles from "@/components/SettingsForm.module.css";
import voiceStyles from "@/components/SettingsVoices.module.css";
import { buttonClassName } from "@/components/ui/button-styles";
import Link from "next/link";
import { PeopleAndVoiceWorkspace } from "@/components/PeopleAndVoiceWorkspace";
import {
  updateProductionProfileAction,
  updateRuntimeSettingsAction,
} from "@/app/actions";
import { SettingsHelpButton } from "@/components/SettingsHelpButton";
import { SettingsAppearance } from "@/components/SettingsAppearance";
import { SettingsCreators } from "@/components/SettingsCreators";
import { AppShell } from "@/components/AppShell";
import { FormErrorBanner } from "@/components/FormErrorBanner";
import { LlmProviderFields } from "@/components/LlmProviderFields";
import { AsrFields, FieldRow, type FieldDef } from "@/components/SettingsFields";
import { ModelInstallPanel } from "@/components/ModelInstallPanel";
import { DeviceResourcePreparationPanel } from "@/components/DeviceResourcePreparationPanel";
import {
  getLocalModels,
  getProductionFoundation,
  getRuntimeSettings,
} from "@/lib/api.server";
import type {
  LocalModelStatus,
  LlmServiceState,
  ProductionFoundation,
  RuntimeSettingItem,
  RuntimeSettings,
  SystemStatus,
} from "@/lib/types";

import styles from "./llm-service-state.module.css";
import own from "./SettingsWorkspace.module.css";

export const dynamic = "force-dynamic";

async function loadSettings(): Promise<{
  runtime: RuntimeSettings | null;
  models: LocalModelStatus[];
  foundation: ProductionFoundation | null;
  offline: boolean;
}> {
  const [runtime, catalog, foundation] = await Promise.all([
    getRuntimeSettings().catch(() => null),
    getLocalModels().catch(() => ({ models: [] })),
    getProductionFoundation().catch(() => null),
  ]);
  return {
    runtime,
    models: catalog.models,
    foundation,
    offline: runtime === null,
  };
}

type SectionDef = {
  id: string;
  title: string;
  description: string;
  tone: "blue" | "green" | "orange" | "purple";
};

const SECTIONS: SectionDef[] = [
  {
    id: "paths",
    title: "保存位置",
    description:
      "Obsidian、视频、剪映、数据库与本地模型都使用绝对路径；大模型建议放在空间充足的移动硬盘。",
    tone: "green",
  },
  {
    id: "asr",
    title: "听写设置",
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

function getSetting(
  settings: RuntimeSettingItem[],
  name: string,
): RuntimeSettingItem | null {
  return settings.find((entry) => entry.name === name) ?? null;
}

// ── 外部文字模型服务四档徽章（B03 前端）──────────────────────────────────
// 数据只来自后端 `/api/settings/runtime` 的 `LLM_SERVICE_STATE` 行。这个行
// 的 value 就是后端 `app/config.py` 的 `llm_service_status()["label"]`，也就是
// `_LLM_SERVICE_LABELS` 里那四个中文。这里按字面认，认不出就说认不出。
//
// 明确不做的事：
// · 不用 `status.llm.configured` 硬凑四档。填了密钥只是「已填写，未验证」。
// · 失败详情只消费后端安全状态行，不读取模型原始响应或日志。
// · 任何一档都不显示密钥本身。
const LLM_SERVICE_STATE_BY_LABEL: Record<string, LlmServiceState> = {
  "未填写": "missing",
  "已填写，未验证": "unverified",
  "可用": "available",
  "失败": "failed",
};

const LLM_SERVICE_VIEWS: Record<
  LlmServiceState,
  { label: string; detail: string; nextStep: string; tone: string }
> = {
  missing: {
    label: "未配置",
    detail:
      "服务地址、模型或 API Key 没有填全，现在走不了外部文字模型。",
    nextStep: "下一步：在下面填齐服务商、连接地址、模型和 API Key 后保存。",
    tone: "missing",
  },
  unverified: {
    label: "已配置·待验证",
    detail:
      "地址、模型和密钥都填了，但还没有真的连过一次服务；这把密钥能不能被认，要等下一次真实对话或写稿才知道。",
    nextStep:
      "下一步：保存后发起一次对话或写稿，再回到这一页查看最近实际调用结果。",
    tone: "unverified",
  },
  available: {
    label: "最近调用成功",
    detail:
      "上一次真实调用成功返回了回复；这里只记录那次结果，不代表下一次调用一定成功。",
    nextStep:
      "下一步：改动地址、模型或密钥之后会退回「已配置·待验证」，需要重新验证一次；上次成功不保证本次可用。",
    tone: "available",
  },
  failed: {
    label: "最近调用失败",
    detail:
      "上一次真实调用没有成功。当前状态接口没有返回失败原因，所以这里拿不到更具体的原因，不替它猜一个。",
    nextStep:
      "下一步：到对话里看那一次失败提示写的原因和修复办法；或先核对服务地址与 API Key，保存后重试一次。",
    tone: "failed",
  },
};

const LLM_SERVICE_UNKNOWN_VIEW = {
  label: "暂时无法读取连接状态",
  detail:
    "这一页没有取到后端的服务状态行，所以不知道现在是可用还是失败。字段填了也不等于服务认得它。",
  nextStep:
    "下一步：刷新这一页再试。如果仍然拿不到，说明本地服务还没提供四档状态接口。",
  tone: "unknown",
} as const;

type LlmServiceView = {
  state: LlmServiceState | "unknown";
  label: string;
  detail: string;
  nextStep: string;
  tone: string;
};

function llmServiceView(settings: RuntimeSettingItem[]): LlmServiceView {
  const label = getSetting(settings, "LLM_SERVICE_STATE")?.value ?? "";
  const state = LLM_SERVICE_STATE_BY_LABEL[label];
  if (!state) {
    return { state: "unknown", ...LLM_SERVICE_UNKNOWN_VIEW };
  }
  const view = { state, ...LLM_SERVICE_VIEWS[state] };
  if (state === "failed") {
    const detail = getSetting(settings, "LLM_SERVICE_DETAIL")?.value;
    const repairAction = getSetting(settings, "LLM_SERVICE_REPAIR_ACTION")?.value;
    if (detail) view.detail = detail;
    if (repairAction) view.nextStep = `下一步：${repairAction}`;
  }
  return view;
}

function LlmServiceBadge({ settings }: { settings: RuntimeSettingItem[] }) {
  const view = llmServiceView(settings);
  return (
    <span
      className={`settings-connection-state ${own[view.tone]} ${
        view.state === "available" ? "is-ready" : ""
      }`}
      data-llm-service-state={view.state}
      data-testid="llm-service-badge"
      title={view.detail}
    >
      {view.label}
    </span>
  );
}

function LlmServiceDetail({ settings }: { settings: RuntimeSettingItem[] }) {
  const view = llmServiceView(settings);
  return (
    <details className={own.help}><summary>状态依据与修复</summary><p className={styles.detail} data-testid="llm-service-detail">
      {view.detail} {view.nextStep}
    </p></details>
  );
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
      { name: "understanding_runtime_python_path", label: "画面理解程序", kind: "file", defaultValue: valueOf("UNDERSTANDING_RUNTIME_PYTHON_PATH"), helper: "Qwen3-VL 独立运行环境；模型、环境和缓存建议一起放在外接盘。" },
      { name: "native_ocr_worker_path", label: "画面文字程序", kind: "file", defaultValue: valueOf("NATIVE_OCR_WORKER_PATH"), helper: "macOS Vision OCR 的本地可执行文件。" },
      {
        name: "obsidian_vault_path",
        label: "本地资料目录",
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
        label: "数据库文件",
        helper: "选择已有的 SQLite 数据库文件，保存只更改使用位置，不会搬迁现有数据。",
        kind: "file",
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
        name: "local_whisper_cli_path", label: "whisper.cpp 程序", kind: "file",
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
          ? "已填写 · 留空不修改"
          : "粘贴 SecretId",
        configured: configuredOf("TENCENTCLOUD_SECRET_ID"),
      },
      {
        name: "tencentcloud_secret_key",
        label: "Tencent SecretKey",
        helper: "腾讯云访问凭证 SecretKey。",
        kind: "secret",
        placeholder: configuredOf("TENCENTCLOUD_SECRET_KEY")
          ? "已填写 · 留空不修改"
          : "粘贴 SecretKey",
        configured: configuredOf("TENCENTCLOUD_SECRET_KEY"),
      },
      {
        name: "tencentcloud_asr_region",
        label: "腾讯云区域",
        helper: "腾讯云 ASR 服务区域，常见 ap-shanghai / ap-guangzhou。",
        kind: "text",
        defaultValue:
          valueOf("TENCENTCLOUD_ASR_REGION") ||
          status.asr.region ||
          "ap-shanghai",
      },
      {
        name: "tencentcloud_asr_engine_model_type",
        label: "腾讯云引擎",
        helper: "腾讯云引擎模型类型，常见 16k_zh_en / 16k_zh。",
        kind: "text",
        defaultValue:
          valueOf("TENCENTCLOUD_ASR_ENGINE_MODEL_TYPE") ||
          status.asr.engine_model_type ||
          "16k_zh_en",
      },
      {
        name: "local_whisper_python_path",
        label: "Python 程序文件",
        helper: "本地 Whisper 解释器绝对路径，例如 /opt/homebrew/bin/python3。",
        kind: "file",
        defaultValue: valueOf("LOCAL_WHISPER_PYTHON_PATH"),
        placeholder: "/opt/homebrew/bin/python3",
      },
      {
        name: "local_whisper_model_path",
        label: "听写模型文件",
        helper: "模型文件路径：whisper.cpp 使用 ggml .bin，Python 使用 .pt。",
        kind: "file",
        defaultValue: valueOf("LOCAL_WHISPER_MODEL_PATH"),
        placeholder: "/Users/you/.cache/whisper/medium",
      },
      {
        name: "local_whisper_language",
        label: "听写语言",
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
          ? "已填写 · 留空不修改"
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
        label: "资料来源链接",
        helper: "外链素材列表，每行一个 URL 或逗号分隔。",
        kind: "textarea",
        defaultValue: status.external_sources.urls.join("\n"),
      },
    ],
  };
}


type SettingsCategory =
  "model" | "production" | "voices" | "paths" | "asr" | "sources" | "creators" | "appearance" | "runtime" | "about";

const SETTINGS_CATEGORIES: Array<{
  id: SettingsCategory;
  label: string;
  description: string;
}> = [
  { id: "creators", label: "创作者与成员", description: "当前身份与成员名册" },
  { id: "appearance", label: "外观", description: "界面主题" },
  { id: "model", label: "对话服务", description: "模型服务、能力与连接" },
  { id: "production", label: "制作习惯", description: "文案时长与品牌" },
  { id: "voices", label: "声音库", description: "默认声音与授权" },
  { id: "paths", label: "保存位置", description: "移动硬盘与输出" },
  { id: "asr", label: "听写设置", description: "云端与本地 ASR" },
  { id: "sources", label: "资料来源", description: "知识库与外部来源" },
  { id: "runtime", label: "运行与诊断", description: "本机检查和修复" },
  { id: "about", label: "关于与隐私", description: "帮助、法律和使用说明" },
];

export default async function SettingsPage({
  searchParams,
}: {
  searchParams?: Promise<{ section?: string; member?: string }>;
}) {
  const params = searchParams ? await searchParams : {};
  const requestedSection = params.section ?? "model";
  const activeCategory = SETTINGS_CATEGORIES.some(
    (item) => item.id === requestedSection,
  )
    ? (requestedSection as SettingsCategory)
    : "model";
  const { runtime, models, foundation, offline } = await loadSettings();
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
              <dt>对话与文字模型</dt>
              <dd>
                {status?.llm.configured
                  ? `${llmServiceView(settingItems).label} · ${status.llm.provider} / ${status.llm.model}`
                  : llmServiceView(settingItems).label}
              </dd>
            </div>
            <div>
              <dt>语音识别</dt>
              <dd>
                {!status ? "暂时无法读取配置" : valueOfSetting(settingItems, "ASR_PROVIDER") === "tencentcloud"
                  ? `腾讯云 · ${status.asr.configured ? "已配置·待验证" : "未配置"}`
                  : `本地 Whisper · ${status.asr.local_whisper_configured ? "已配置·待验证" : "未配置"}`}
              </dd>
            </div>
            <div>
              <dt>视频编码工具</dt>
              <dd>{!status ? "暂时无法读取配置" : status.video.ffmpeg_available ? "已找到程序" : "未找到程序"}</dd>
            </div>
            <div>
              <dt>数据库</dt>
              <dd>{!status ? "暂时无法读取配置" : status.storage.database_exists ? "已创建" : "未创建"}</dd>
            </div>
          </dl>
          <p>密钥只显示是否已填写，不会返回原文。</p>
        </div>
      }
      inspectorTitle="设置状态"
    >
      <div className={`workspace-page ${own.page}`}>
        <nav className={own.categoryNav} aria-label="设置分类">
          {SETTINGS_CATEGORIES.map(category => <Link
            aria-current={activeCategory === category.id ? "page" : undefined}
            href={`/settings?section=${category.id}`}
            key={category.id}
          >{category.label}</Link>)}
        </nav>
        <section className={`${own.main} ${formStyles.surface} ${voiceStyles.surface}`}>
          {offline ? <p role="alert">暂时无法连接本地服务。</p> : null}
          {activeCategory === "creators" ? <SettingsCreators selectedMemberId={params.member} /> : null}
          {activeCategory === "appearance" ? <section className={own.auxiliary} aria-label="外观设置"><h2>外观</h2><SettingsAppearance /></section> : null}
          {activeCategory === "runtime" ? <section className={own.auxiliary} aria-label="运行与诊断"><h2>运行与诊断</h2><DeviceResourcePreparationPanel/><nav aria-label="运行帮助"><Link href="/health">运行情况与修复</Link><Link href="/settings?section=paths">本地模型与路径</Link><Link href="/settings?section=model">对话服务连接</Link></nav></section> : null}
          {activeCategory === "about" ? <section className={own.auxiliary} aria-label="关于与隐私"><h2>曜核AI剪辑</h2><SettingsHelpButton/><nav aria-label="法律与帮助"><Link href="/privacy">隐私政策</Link><Link href="/terms">使用条款</Link><Link href="/health">运行检查与技术诊断</Link></nav><details><summary>数据与模型使用说明</summary><p>本地素材处理使用本机能力；对话和文案使用你配置的文字服务。发送范围、凭据与外部服务费用在相应设置中说明。原始音视频不会因普通文字对话自动上传。</p></details></section> : null}


          {activeCategory === "model" ? (
            <FormErrorBanner
              action={updateRuntimeSettingsAction}
              className={`settings-form settings-focused-form ${own.modelForm}`}
              emptyMessage="请检查模型连接配置"
            >
              <input name="settings_section" type="hidden" value="model" />
              <div className="settings-section-header">
                <div>
                  <h2>对话服务</h2>
                  <p>
                    用于聊天和写稿，素材仍在本机整理。
                    仅发送相关文字，不发送原视频。
                  </p>
                </div>
                <LlmServiceBadge settings={settingItems} />
              </div>
              <LlmServiceDetail settings={settingItems} />
              {/* 这三处 `status?.llm.configured` 只是回填表单的兵底，不是状态显示。
                  未配置时后端把 llm.base_url / model / provider 换成占位值
                  （"local" / "source-grounded-v1" / "local-extractive"），
                  那些不能当成用户的真实配置填回去。状态看上面的四档徽章。 */}
              <LlmProviderFields
                apiKeyConfigured={Boolean(
                  getSetting(settingItems, "LLM_API_KEY")?.configured,
                )}
                baseUrl={
                  valueOfSetting(settingItems, "LLM_BASE_URL") ||
                  (status?.llm.configured ? status.llm.base_url : null) ||
                  "https://api.minimax.cn/v1"
                }
                model={
                  valueOfSetting(settingItems, "LLM_MODEL") ||
                  (status?.llm.configured ? status.llm.model : null) || "MiniMax-M3"
                }
                provider={
                  valueOfSetting(settingItems, "LLM_PROVIDER") ||
                  (status?.llm.configured ? status.llm.provider : "minimax")
                }
              />
              <div className="settings-actions">
                <button
                  className={buttonClassName({ variant: "default" })}
                  disabled={offline}
                  type="submit"
                >
                  保存连接
                </button>
                <span>保存后用于下一次对话和写稿。</span>
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
                  <h2>文案与成片</h2>
                  <p>
                    作为新任务的制作范围，单个任务可另行调整。
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
                    <span>品牌名称（可选）</span>
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
                    <span>品牌样式备注（可选）</span>
                    <input
                      name="brand_template_name"
                      defaultValue={profile.brand_template_name}
                      maxLength={80}
                      placeholder="例如：公司产品介绍风格"
                    />
                    <small className="settings-field-helper">记录常用样式的名称；保存备注不会自动改变画面。</small>
                  </label>
                  <label className="settings-field">
                    <span>字幕偏好备注（可选）</span>
                    <input
                      name="subtitle_style"
                      defaultValue={profile.subtitle_style}
                      maxLength={240}
                      placeholder="例如：白字黑边，重点词用黄色"
                    />
                    <small className="settings-field-helper">记录希望的字幕样式；目前不会自动应用到成片字幕，请在任务中调整字幕。</small>
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
                  <button className={buttonClassName({ variant: "default" })} type="submit">
                    保存生产默认值
                  </button>
                  <span>后续可在单个内容中临时覆盖时长。</span>
                </div>
              </FormErrorBanner>

              {activeCategory === "voices" ? <PeopleAndVoiceWorkspace initialView="voices" embedded /> : null}
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
                    className={`settings-section ${own.section}`}
                    key={section.id}
                  >
                    <legend className="settings-section-legend">
                      <strong id={`settings-section-${section.id}`}>
                        {section.title}
                      </strong>
                    </legend>
                    <details className={own.help}><summary>使用说明</summary><p>
                      {section.description}
                    </p></details>
                    <div className="settings-grid">
                      {section.id === "asr" ? <AsrFields fields={sectionFields} /> : sectionFields.map((field) => (
                        <FieldRow field={field} key={field.name} />
                      ))}
                    </div>
                  </fieldset>
                );
              })}

              {activeCategory === "paths" ? (
                <details className={own.help}><summary>本地模型安装与诊断</summary><ModelInstallPanel
                  modelRootPath={valueOfSetting(
                    settingItems,
                    "MODEL_ROOT_PATH",
                  )}
                  models={models}
                /></details>
              ) : null}

              <div className="settings-actions">
                <button
                  className={buttonClassName({ variant: "default" })}
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
