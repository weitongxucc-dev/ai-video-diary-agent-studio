import type {
  RuntimeHealth,
  RuntimeHealthItem,
  RuntimeSettings,
  SetupCheckItem,
  SetupCheckStatus,
  SetupStatus,
  SystemStatus,
} from "./types";

export type OnboardingChecklistId =
  | "deepseek_api_key"
  | "obsidian_vault"
  | "video_inbox"
  | "video_output"
  | "draft_output"
  | "database"
  | "ffmpeg"
  | "product_hunt_token"
  | "tencentcloud_asr"
  | "local_whisper";

export type OnboardingChecklistState = "ready" | "attention" | "missing" | "unknown";

export type OnboardingChecklistEntry = {
  id: OnboardingChecklistId;
  title: string;
  detail: string;
  impact: string;
  state: OnboardingChecklistState;
  statusLabel: string;
  isSecret: boolean;
  fixHref: string;
  fixLabel: string;
  secretSummary: string;
};

export type OnboardingChecklistSection = {
  id: string;
  title: string;
  description: string;
  entries: OnboardingChecklistEntry[];
};

export type RedactedSetting = {
  name: string;
  configured: boolean;
  secret: boolean;
};

export type RedactedSettings = {
  settings: RedactedSetting[];
};

export type OnboardingSnapshot = {
  ready: boolean;
  completion: { ready: number; total: number; percent: number };
  blockingCount: number;
  attentionCount: number;
  sources: {
    setup: SetupStatus | null;
    setupError: string | null;
    health: RuntimeHealth | null;
    healthError: string | null;
    settings: RedactedSettings | null;
    settingsError: string | null;
    status: SystemStatus | null;
    statusError: string | null;
  };
  sections: OnboardingChecklistSection[];
  generatedAt: string;
};

const SETUP_TONE: Record<SetupCheckStatus, OnboardingChecklistState> = {
  ready: "ready",
  missing: "missing",
  degraded: "attention",
  invalid: "attention",
};

const HEALTH_TONE: Record<RuntimeHealthItem["status"], OnboardingChecklistState> = {
  ready: "ready",
  missing: "missing",
  degraded: "attention",
  invalid: "attention",
};

function describeState(state: OnboardingChecklistState): string {
  if (state === "ready") return "已就绪";
  if (state === "missing") return "未配置";
  if (state === "attention") return "需要关注";
  return "未知";
}

function settingsValue(
  settings: RuntimeSettings | null,
  name: string,
): { configured: boolean; value: string | null; isSecret: boolean } {
  if (!settings) {
    return { configured: false, value: null, isSecret: false };
  }
  const item = settings.settings.find((entry) => entry.name === name) ?? null;
  if (!item) {
    return { configured: false, value: null, isSecret: false };
  }
  return {
    configured: item.configured,
    value: item.value,
    isSecret: item.secret,
  };
}

function pathConfigured(item: { configured: boolean } | undefined): boolean {
  return Boolean(item?.configured);
}

function statusPathConfigured(path: string | null | undefined, exists: boolean): boolean {
  if (!path || path.length === 0) {
    return false;
  }
  return exists;
}

function buildEntry(args: {
  id: OnboardingChecklistId;
  title: string;
  detail: string;
  impact: string;
  state: OnboardingChecklistState;
  fixHref: string;
  fixLabel: string;
  isSecret: boolean;
  secretSummary: string;
}): OnboardingChecklistEntry {
  return {
    ...args,
    statusLabel: describeState(args.state),
  };
}

function pickSetupItem(
  setup: SetupStatus | null,
  matches: (item: SetupCheckItem) => boolean,
): SetupCheckItem | null {
  if (!setup) {
    return null;
  }
  return setup.items.find(matches) ?? null;
}

function pickHealthItem(
  health: RuntimeHealth | null,
  matches: (item: RuntimeHealthItem) => boolean,
): RuntimeHealthItem | null {
  if (!health) {
    return null;
  }
  return health.items.find(matches) ?? null;
}

function secretSummary(prefix: string, configured: boolean): string {
  return `${prefix}：${configured ? "已配置（值不展示）" : "未配置"}`;
}

export function buildOnboardingSnapshot(input: {
  setup: SetupStatus | null;
  setupError: string | null;
  health: RuntimeHealth | null;
  healthError: string | null;
  settings: RuntimeSettings | null;
  settingsError: string | null;
  status: SystemStatus | null;
  statusError: string | null;
}): OnboardingSnapshot {
  const { setup, health, settings, status, settingsError } = input;

  const deepseek = settingsValue(settings, "DEEPSEEK_API_KEY");
  const productHunt = settingsValue(settings, "PRODUCT_HUNT_TOKEN");
  const tencentSecretId = settingsValue(settings, "TENCENTCLOUD_SECRET_ID");
  const tencentSecretKey = settingsValue(settings, "TENCENTCLOUD_SECRET_KEY");
  const localWhisperPython = settingsValue(settings, "LOCAL_WHISPER_PYTHON_PATH");
  const localWhisperModel = settingsValue(settings, "LOCAL_WHISPER_MODEL_PATH");
  const draftOutput = settingsValue(settings, "DRAFT_OUTPUT_PATH");
  const jianyingRoot = settingsValue(settings, "JIANYING_DRAFT_ROOT_PATH");

  const obsidianConfigured = pathConfigured(
    status?.obsidian.exists ? { configured: true } : undefined,
  );
  const videoInboxConfigured = statusPathConfigured(
    status?.video.inbox_path,
    status?.video.inbox_exists ?? false,
  );
  const videoOutputConfigured = statusPathConfigured(
    status?.video.output_path,
    status?.video.output_exists ?? false,
  );
  const databaseConfigured = status?.storage.database_exists ?? false;
  const ffmpegConfigured = status?.video.ffmpeg_available ?? false;

  const modelEntry: OnboardingChecklistEntry = (() => {
    const llmSetup = pickSetupItem(setup, (item) =>
      item.id === "llm" || item.label.includes("DeepSeek") || item.label.includes("AI"),
    );
    const llmHealth = pickHealthItem(health, (item) =>
      item.id.toLowerCase().includes("llm") ||
      item.id.toLowerCase().includes("deepseek") ||
      item.label.includes("DeepSeek"),
    );

    let state: OnboardingChecklistState = "unknown";
    let detail = "尚未读取 AI 模型状态。";

    if (llmSetup) {
      state = SETUP_TONE[llmSetup.status];
      detail = llmSetup.detail;
    } else if (llmHealth) {
      state = HEALTH_TONE[llmHealth.status];
      detail = llmHealth.reason;
    } else if (deepseek.configured) {
      state = "ready";
      detail = "DeepSeek API Key 已配置。";
    } else if (!deepseek.configured) {
      state = "missing";
      detail = "请在运行配置中填入 DeepSeek API Key。";
    }

    return buildEntry({
      id: "deepseek_api_key",
      title: "DeepSeek API Key",
      detail,
      impact: "没有可用模型，AI 生成、语音转写、运行健康都会失败。",
      state,
      fixHref: "/settings",
      fixLabel: "去设置 API Key",
      isSecret: true,
      secretSummary: secretSummary("DeepSeek API Key", deepseek.configured),
    });
  })();

  const obsidianEntry: OnboardingChecklistEntry = (() => {
    const pathSetup = pickSetupItem(setup, (item) =>
      item.label.includes("Obsidian") || item.id.toLowerCase().includes("obsidian"),
    );
    let state: OnboardingChecklistState = "unknown";
    let detail = "尚未读取 Obsidian 目录状态。";

    if (pathSetup) {
      state = SETUP_TONE[pathSetup.status];
      detail = pathSetup.detail;
    } else if (status?.obsidian.path) {
      state = obsidianConfigured ? "ready" : "missing";
      detail = obsidianConfigured
        ? `已发现 ${status.obsidian.markdown_count} 篇笔记。`
        : `路径未找到：${status.obsidian.path}`;
    } else if (status) {
      state = "missing";
      detail = "Obsidian Vault 路径未填写。";
    }

    return buildEntry({
      id: "obsidian_vault",
      title: "Obsidian Vault 路径",
      detail,
      impact: "没有 Vault，Agent 拿不到笔记、选题和日记上下文。",
      state,
      fixHref: "/settings",
      fixLabel: "去设置路径",
      isSecret: false,
      secretSummary: "Obsidian Vault：未配置",
    });
  })();

  const videoInboxEntry: OnboardingChecklistEntry = (() => {
    const pathSetup = pickSetupItem(setup, (item) =>
      item.label.includes("视频") || item.id.toLowerCase().includes("video"),
    );
    let state: OnboardingChecklistState = "unknown";
    let detail = "尚未读取视频待处理目录。";

    if (pathSetup) {
      state = SETUP_TONE[pathSetup.status];
      detail = pathSetup.detail;
    } else if (status?.video.inbox_path) {
      state = videoInboxConfigured ? "ready" : "missing";
      detail = videoInboxConfigured
        ? `已就绪，当前 ${status.video.pending_count} 个待处理视频。`
        : `路径未找到：${status.video.inbox_path}`;
    } else if (status) {
      state = "missing";
      detail = "视频待处理目录未填写。";
    }

    return buildEntry({
      id: "video_inbox",
      title: "视频待处理目录",
      detail,
      impact: "没有 inbox，新视频无法被发现和处理。",
      state,
      fixHref: "/settings",
      fixLabel: "去设置路径",
      isSecret: false,
      secretSummary: "视频待处理目录：未配置",
    });
  })();

  const videoOutputEntry: OnboardingChecklistEntry = buildEntry({
    id: "video_output",
    title: "视频输出目录",
    detail: videoOutputConfigured
      ? "输出目录可写。"
      : status?.video.output_path
        ? `路径未找到：${status.video.output_path}`
        : "视频输出目录未填写。",
    impact: "没有输出目录，精剪视频和重录报告会落空。",
    state: videoOutputConfigured
      ? "ready"
      : status?.video.output_path
        ? "missing"
        : "missing",
    fixHref: "/settings",
    fixLabel: "去设置路径",
    isSecret: false,
    secretSummary: "视频输出目录：未配置",
  });

  const draftOutputEntry: OnboardingChecklistEntry = (() => {
    if (draftOutput.configured) {
      return buildEntry({
        id: "draft_output",
        title: "剪映素材包输出",
        detail: jianyingRoot.configured
          ? "draft_output_path 与 jianying_draft_root_path 均已配置，可自动生成素材包并尝试导入剪映。"
          : "draft_output_path 已配置；jianying_draft_root_path 未填写时只会写素材包 fallback。",
        impact: "已具备剪映素材包输出能力。",
        state: jianyingRoot.configured ? "ready" : "attention",
        fixHref: "/settings",
        fixLabel: jianyingRoot.configured ? "查看路径" : "去补 jianying_draft_root_path",
        isSecret: false,
        secretSummary: `剪映素材包：${jianyingRoot.configured ? "已配置" : "draft_output 已配置 / 剪映根目录未配置"}`,
      });
    }
    if (settingsError) {
      return buildEntry({
        id: "draft_output",
        title: "剪映素材包输出",
        detail: `无法读取运行配置：${settingsError}`,
        impact: "设置读取失败，先修复 /api/settings/runtime。",
        state: "unknown",
        fixHref: "/settings",
        fixLabel: "查看设置",
        isSecret: false,
        secretSummary: "剪映素材包：未读取",
      });
    }
    return buildEntry({
      id: "draft_output",
      title: "剪映素材包输出",
      detail: "需在设置中填写 draft_output_path，用于把素材包自动落到剪映目录。",
      impact: "没有输出目录，剪映素材包无法落地。",
      state: "missing",
      fixHref: "/settings",
      fixLabel: "去设置路径",
      isSecret: false,
      secretSummary: "剪映素材包：未配置",
    });
  })();

  const databaseEntry: OnboardingChecklistEntry = buildEntry({
    id: "database",
    title: "SQLite 数据库",
    detail: databaseConfigured
      ? `已就绪：${status?.storage.database_path ?? ""}`
      : "数据库文件尚未生成，请确认 database_path 指向的目录可写。",
    impact: "没有数据库，Episode 状态、偏好、反馈学习都会丢失。",
    state: databaseConfigured ? "ready" : "missing",
    fixHref: "/settings",
    fixLabel: "去设置路径",
    isSecret: false,
    secretSummary: "数据库：未配置",
  });

  const ffmpegEntry: OnboardingChecklistEntry = buildEntry({
    id: "ffmpeg",
    title: "ffmpeg 可执行",
    detail: ffmpegConfigured
      ? "ffmpeg 可用，视频流水线可以工作。"
      : "未检测到 ffmpeg；视频精剪、合并、静音切除都无法执行。",
    impact: "ffmpeg 不可用会直接阻断视频流水线。",
    state: ffmpegConfigured ? "ready" : "missing",
    fixHref: "https://ffmpeg.org/download.html",
    fixLabel: "安装 ffmpeg",
    isSecret: false,
    secretSummary: "ffmpeg：不可用",
  });

  const productHuntEntry: OnboardingChecklistEntry = buildEntry({
    id: "product_hunt_token",
    title: "Product Hunt Token",
    detail: productHunt.configured
      ? "已配置（值不展示）。"
      : "可选；不填会跳过 Product Hunt 来源。",
    impact: "跳过 Product Hunt 会让信息源少一类。",
    state: productHunt.configured ? "ready" : "attention",
    fixHref: "/settings",
    fixLabel: "去设置 Token",
    isSecret: true,
    secretSummary: secretSummary("Product Hunt Token", productHunt.configured),
  });

  const asrEntry: OnboardingChecklistEntry = (() => {
    const hasTencent = tencentSecretId.configured && tencentSecretKey.configured;
    const hasLocal =
      localWhisperPython.configured && localWhisperModel.configured;
    if (hasTencent || hasLocal) {
      const provider = hasTencent && hasLocal
        ? "腾讯云 ASR + 本地 Whisper"
        : hasTencent
          ? "腾讯云 ASR"
          : "本地 Whisper";
      return buildEntry({
        id: "tencentcloud_asr",
        title: "语音识别 (ASR)",
        detail: `${provider} 已配置，语音转写可用。`,
        impact: "已具备 ASR 能力。",
        state: "ready",
        fixHref: "/settings",
        fixLabel: "查看 ASR 设置",
        isSecret: true,
        secretSummary: `ASR：${provider}`,
      });
    }
    return buildEntry({
      id: "tencentcloud_asr",
      title: "语音识别 (ASR)",
      detail: "腾讯云 SecretId/SecretKey 与本地 Whisper 路径都未配置，语音指令无法转写。",
      impact: "没有 ASR，语音指令功能会完全不可用。",
      state: "missing",
      fixHref: "/settings",
      fixLabel: "去设置 ASR",
      isSecret: true,
      secretSummary: secretSummary("ASR 凭据", false),
    });
  })();

  const localWhisperEntry: OnboardingChecklistEntry = buildEntry({
    id: "local_whisper",
    title: "本地 Whisper 路径",
    detail: localWhisperPython.configured && localWhisperModel.configured
      ? "已配置 Python 解释器和模型路径。腾讯云不可用时会降级到本地。"
      : "本地 Whisper 路径未填写，会依赖腾讯云。",
    impact: "没有本地 Whisper，离线场景下无法转写。",
    state:
      localWhisperPython.configured && localWhisperModel.configured
        ? "ready"
        : "attention",
    fixHref: "/settings",
    fixLabel: "去设置本地 Whisper",
    isSecret: false,
    secretSummary: "本地 Whisper：未配置",
  });

  const sections: OnboardingChecklistSection[] = [
    {
      id: "model",
      title: "AI 模型",
      description: "确保 AI 模型可用，才能继续生成选题、口播稿、剪辑计划。",
      entries: [modelEntry, productHuntEntry, asrEntry, localWhisperEntry],
    },
    {
      id: "paths",
      title: "本地目录",
      description: "Obsidian、视频、剪映与数据库都依赖本地路径。",
      entries: [
        obsidianEntry,
        videoInboxEntry,
        videoOutputEntry,
        draftOutputEntry,
        databaseEntry,
      ],
    },
    {
      id: "system",
      title: "系统依赖",
      description: "ffmpeg 等系统依赖必须可用，否则视频流水线会被阻断。",
      entries: [ffmpegEntry],
    },
  ];

  const allEntries = sections.flatMap((section) => section.entries);
  const readyCount = allEntries.filter((entry) => entry.state === "ready").length;
  const blockingCount = allEntries.filter((entry) => entry.state === "missing").length;
  const attentionCount = allEntries.filter(
    (entry) => entry.state === "attention" || entry.state === "unknown",
  ).length;
  const total = allEntries.length;
  const percent = total === 0 ? 0 : Math.round((readyCount / total) * 100);

  return {
    ready: blockingCount === 0 && attentionCount === 0,
    completion: { ready: readyCount, total, percent },
    blockingCount,
    attentionCount,
    sources: {
      setup: input.setup,
      setupError: input.setupError,
      health: input.health,
      healthError: input.healthError,
      settings: input.settings
        ? {
            settings: input.settings.settings.map((entry) => ({
              name: entry.name,
              configured: entry.configured,
              secret: entry.secret,
            })),
          }
        : null,
      settingsError: input.settingsError,
      status: input.status,
      statusError: input.statusError,
    },
    sections,
    generatedAt: new Date().toISOString(),
  };
}

export function summarizeOnboardingHealth(snapshot: OnboardingSnapshot): {
  tone: "green" | "yellow" | "red";
  headline: string;
  detail: string;
} {
  if (snapshot.ready) {
    return {
      tone: "green",
      headline: "首次启动检查全部通过",
      detail: `${snapshot.completion.ready}/${snapshot.completion.total} 项就绪，可以开始今日工作。`,
    };
  }
  if (snapshot.blockingCount > 0) {
    return {
      tone: "red",
      headline: `还有 ${snapshot.blockingCount} 项必须配置`,
      detail: `在它们补齐之前，AI 流水线会持续报错。${
        snapshot.attentionCount > 0
          ? `另有 ${snapshot.attentionCount} 项建议关注。`
          : ""
      }`,
    };
  }
  return {
    tone: "yellow",
    headline: `有 ${snapshot.attentionCount} 项需要关注`,
    detail: "没有阻断项，但建议补齐这些项以获得最佳体验。",
  };
}
