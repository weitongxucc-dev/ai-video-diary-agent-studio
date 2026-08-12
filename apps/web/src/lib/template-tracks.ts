import type {
  DraftAsset,
  Episode,
  RuntimeSettings,
  TemplateTrackEntry,
  TemplateTrackKind,
  TemplateTrackSnapshot,
} from "./types";

const TRACK_DEFINITIONS: Array<{
  kind: TemplateTrackKind;
  label: string;
  description: string;
  impact: string;
  requiresTemplate: boolean;
  requiresMusicPath: boolean;
  fixHref?: string;
}> = [
  {
    kind: "title_card",
    label: "Title card",
    description: "模板名称生成的开场标题字幕（前 2 秒）",
    impact: "用于在视频前 2 秒内亮出选题/模板名；缺失时观众对主题的预期会偏弱。",
    requiresTemplate: true,
    requiresMusicPath: false,
  },
  {
    kind: "chapter_labels",
    label: "Chapter labels",
    description: "每个 script_section 一个 2 秒章节标签",
    impact: "缺失时回看时缺乏章节提示，剪辑师与观众都更难定位段落。",
    requiresTemplate: true,
    requiresMusicPath: false,
  },
  {
    kind: "keyword_emphasis",
    label: "Keyword emphasis",
    description: "从模板 default_feedback_dimensions 派生的关键词字幕",
    impact: "没有它就只是普通正文字幕；强化记忆点的视觉重音会消失。",
    requiresTemplate: true,
    requiresMusicPath: false,
  },
  {
    kind: "background_music",
    label: "Background music",
    description: "配置 draft_background_music_path 时由后端自动混入音轨",
    impact: "未配置本地 BGM 时整段视频没有配乐；该轨道被后端跳过但其它轨道仍生效。",
    requiresTemplate: false,
    requiresMusicPath: true,
    fixHref: "/settings",
  },
];

type SettingsLike = RuntimeSettings | null | undefined;

function findSetting(settings: SettingsLike, name: string) {
  if (!settings) {
    return null;
  }
  return settings.settings.find((entry) => entry.name === name) ?? null;
}

function isPathConfigured(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

function describeEpisodeTemplate(episode: Episode | null | undefined): {
  contentTemplateId: string | null;
  hasScriptSections: boolean;
  importStatus: string | null;
} {
  if (!episode) {
    return { contentTemplateId: null, hasScriptSections: false, importStatus: null };
  }
  return {
    contentTemplateId: episode.content_template_id || null,
    hasScriptSections: Array.isArray(episode.script?.sections)
      ? episode.script!.sections.length > 0
      : false,
    importStatus: episode.status,
  };
}

function describeImportedPackage(draft: DraftAsset | null | undefined): {
  imported: boolean;
  packageName: string | null;
  modifiedAt: string | null;
} {
  if (!draft) {
    return { imported: false, packageName: null, modifiedAt: null };
  }
  return {
    imported: draft.import_status === "imported",
    packageName: draft.file_name,
    modifiedAt: draft.modified_at,
  };
}

export function resolveTemplateTracks(input: {
  episode: Episode | null | undefined;
  settings: SettingsLike;
  draftPackage?: DraftAsset | null | undefined;
}): TemplateTrackSnapshot {
  const { episode, settings, draftPackage } = input;
  const epMeta = describeEpisodeTemplate(episode);
  const pkg = describeImportedPackage(draftPackage);
  const musicSetting = findSetting(settings, "DRAFT_BACKGROUND_MUSIC_PATH");
  const musicConfigured = isPathConfigured(musicSetting?.value);
  const hasTemplate = Boolean(epMeta.contentTemplateId);

  const tracks: TemplateTrackEntry[] = TRACK_DEFINITIONS.map((def) => {
    if (def.requiresMusicPath) {
      if (musicConfigured) {
        return {
          kind: def.kind,
          label: def.label,
          description: def.description,
          availability: pkg.imported ? "available" : "absent",
          detail: pkg.imported
            ? `已导入素材包 ${pkg.packageName ?? ""}，后端在最后一次生成时检测到 BGM 路径。`
            : "检测到 BGM 路径，但还未导入任何素材包；下次生成时生效。",
          impact: def.impact,
          fixHref: def.fixHref,
        };
      }
      if (musicSetting === null) {
        return {
          kind: def.kind,
          label: def.label,
          description: def.description,
          availability: "unknown",
          detail: "后端未在运行时配置中返回 DRAFT_BACKGROUND_MUSIC_PATH；无法判断是否已配置。",
          impact: def.impact,
          fixHref: def.fixHref,
        };
      }
      return {
        kind: def.kind,
        label: def.label,
        description: def.description,
        availability: "absent",
        detail: "未在运行时配置中填写 DRAFT_BACKGROUND_MUSIC_PATH；轨道被后端跳过。",
        impact: def.impact,
        fixHref: def.fixHref,
      };
    }
    // requiresTemplate tracks
    if (!hasTemplate) {
      return {
        kind: def.kind,
        label: def.label,
        description: def.description,
        availability: "unknown",
        detail: epMeta.contentTemplateId === null
          ? "Episode 尚未选择内容模板；该轨道由后端依据模板生成。"
          : "Episode 已有内容模板，但暂未在 API 中确认模板字段。",
        impact: def.impact,
      };
    }
    if (def.kind === "chapter_labels" && !epMeta.hasScriptSections) {
      return {
        kind: def.kind,
        label: def.label,
        description: def.description,
        availability: "absent",
        detail: "模板已选，但当前 Episode 还没有口播稿段落；后端会跳过 chapter labels。",
        impact: def.impact,
      };
    }
    return {
      kind: def.kind,
      label: def.label,
      description: def.description,
      availability: pkg.imported ? "available" : "absent",
      detail: pkg.imported
        ? `已导入素材包 ${pkg.packageName ?? ""}（${pkg.modifiedAt ?? ""}），native writer 在生成时按模板写入。`
        : "模板已选，最近一次生成时由 native writer 写入；下次重新生成会再次应用。",
      impact: def.impact,
    };
  });

  const source: TemplateTrackSnapshot["source"] = (() => {
    if (episode && settings) return "episode+settings";
    if (episode) return "episode";
    if (settings) return "settings";
    if (draftPackage) return "package";
    return "none";
  })();

  const readyCount = tracks.filter((entry) => entry.availability === "available").length;
  return {
    source,
    tracks,
    readyCount,
    totalCount: tracks.length,
    lastPackageImportedAt: pkg.modifiedAt,
    contentTemplateId: epMeta.contentTemplateId,
    backgroundMusicConfigured: musicConfigured,
  };
}

export function describeTrackTone(snapshot: TemplateTrackSnapshot): {
  tone: "green" | "yellow" | "red" | "muted";
  headline: string;
  detail: string;
} {
  if (snapshot.source === "none") {
    return {
      tone: "muted",
      headline: "尚未读取到模板与素材数据",
      detail: "需要先连通 /api/episodes/today 与 /api/drafts/status 才能评估轨道状态。",
    };
  }
  if (snapshot.readyCount === snapshot.totalCount) {
    return {
      tone: "green",
      headline: "Native 模板轨道全部就绪",
      detail: `已生成 ${snapshot.readyCount}/${snapshot.totalCount} 个轨道，最近一次导入：${snapshot.lastPackageImportedAt ?? "—"}`,
    };
  }
  if (snapshot.readyCount === 0) {
    return {
      tone: "yellow",
      headline: "Native 模板轨道尚未在最近一次生成中应用",
      detail: snapshot.backgroundMusicConfigured
        ? "BGM 路径已配置，但还没有可对照的素材包；导入一次素材包后会看到状态切换。"
        : "模板与素材都未到位，先确认 Episode 已选择内容模板，再生成素材包。",
    };
  }
  return {
    tone: "yellow",
    headline: `部分模板轨道已生效（${snapshot.readyCount}/${snapshot.totalCount}）`,
    detail: "未生效的轨道主要因为模板未提供章节、BGM 路径未配置或最近一次生成时未应用。",
  };
}
