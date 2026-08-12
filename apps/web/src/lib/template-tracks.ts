import type {
  DraftAsset,
  Episode,
  RuntimeSettings,
  TemplateTrackEntry,
  TemplateTrackKind,
  TemplateTrackSnapshot,
  TemplateTrackSource,
} from "./types";

type SettingsLike = RuntimeSettings | null | undefined;

type TrackSpec = {
  kind: TemplateTrackKind;
  label: string;
  description: string;
  impact: string;
  fixHref?: string;
};

const TRACK_SPECS: TrackSpec[] = [
  {
    kind: "title_card",
    label: "Title card",
    description: "模板名称生成的开场标题字幕（前 2 秒）",
    impact: "用于在视频前 2 秒内亮出选题/模板名；缺失时观众对主题的预期会偏弱。",
  },
  {
    kind: "chapter_labels",
    label: "Chapter labels",
    description: "每个 script_section 一个 2 秒章节标签",
    impact: "缺失时回看时缺乏章节提示，剪辑师与观众都更难定位段落。",
  },
  {
    kind: "keyword_emphasis",
    label: "Keyword emphasis",
    description: "从模板 default_feedback_dimensions 派生的关键词字幕",
    impact: "没有它就只是普通正文字幕；强化记忆点的视觉重音会消失。",
  },
  {
    kind: "background_music",
    label: "Background music",
    description: "配置 DRAFT_BACKGROUND_MUSIC_PATH 时由后端自动混入音轨",
    impact: "未配置本地 BGM 时整段视频没有配乐；该轨道被后端跳过但其它轨道仍生效。",
    fixHref: "/settings",
  },
];

const KNOWN_KINDS: ReadonlySet<string> = new Set(
  TRACK_SPECS.map((spec) => spec.kind),
);

function findSetting(settings: SettingsLike, name: string) {
  if (!settings) {
    return null;
  }
  return settings.settings.find((entry) => entry.name === name) ?? null;
}

function isPathConfigured(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

function describeEpisode(episode: Episode | null | undefined): {
  contentTemplateId: string | null;
  scriptSectionCount: number;
} {
  if (!episode) {
    return { contentTemplateId: null, scriptSectionCount: 0 };
  }
  return {
    contentTemplateId: episode.content_template_id || null,
    scriptSectionCount: Array.isArray(episode.script?.sections)
      ? episode.script!.sections.length
      : 0,
  };
}

function describeImportedPackage(draft: DraftAsset | null | undefined): {
  imported: boolean;
  packageName: string | null;
  modifiedAt: string | null;
  templateTracks: string[] | null;
  importStatus: string | null;
} {
  if (!draft) {
    return {
      imported: false,
      packageName: null,
      modifiedAt: null,
      templateTracks: null,
      importStatus: null,
    };
  }
  return {
    imported: draft.import_status === "imported",
    packageName: draft.file_name,
    modifiedAt: draft.modified_at,
    templateTracks: Array.isArray(draft.template_tracks)
      ? draft.template_tracks.filter((entry): entry is string =>
          typeof entry === "string",
        )
      : null,
    importStatus: draft.import_status,
  };
}

type ParsedBackendTrack = {
  kind: TemplateTrackKind;
  raw: string;
  payload: string | null;
};

function parseBackendTrack(entry: string): ParsedBackendTrack | null {
  if (typeof entry !== "string" || entry.length === 0) {
    return null;
  }
  const separator = entry.indexOf(":");
  const head = separator === -1 ? entry : entry.slice(0, separator);
  if (!KNOWN_KINDS.has(head)) {
    return null;
  }
  return {
    kind: head as TemplateTrackKind,
    raw: entry,
    payload: separator === -1 ? null : entry.slice(separator + 1) || null,
  };
}

function pickLatestPackage(draftPackages: DraftAsset[] | undefined): DraftAsset | null {
  if (!draftPackages || draftPackages.length === 0) {
    return null;
  }
  const imported = draftPackages.filter((pkg) => pkg.import_status === "imported");
  if (imported.length > 0) {
    return imported[0] ?? null;
  }
  return draftPackages[0] ?? null;
}

function entryFromBackend(args: {
  spec: TrackSpec;
  parsed: ParsedBackendTrack;
  packageName: string | null;
}): TemplateTrackEntry {
  const { spec, parsed, packageName } = args;
  const detailParts: string[] = [];
  detailParts.push(
    `后端 DraftAsset.template_tracks 返回 “${parsed.raw}”`,
  );
  if (parsed.kind === "title_card" && parsed.payload) {
    detailParts.push(`模板 ID ${parsed.payload}`);
  }
  detailParts.push(
    packageName
      ? `（最新导入素材包 ${packageName}）`
      : "（最近一次导入响应）",
  );
  return {
    kind: spec.kind,
    label: spec.label,
    description: spec.description,
    availability: "available",
    detail: detailParts.join("，"),
    impact: spec.impact,
    fixHref: spec.fixHref,
    source: "package-metadata",
    raw: parsed.raw,
  };
}

function entryExplicitNone(args: {
  spec: TrackSpec;
  packageName: string | null;
}): TemplateTrackEntry {
  const { spec, packageName } = args;
  return {
    kind: spec.kind,
    label: spec.label,
    description: spec.description,
    availability: "absent",
    detail: packageName
      ? `后端 DraftAsset.template_tracks 为空数组；${packageName} 显式表示未生成该轨道。`
      : "后端 DraftAsset.template_tracks 为空数组；当前素材包显式表示未生成该轨道。",
    impact: spec.impact,
    fixHref: spec.fixHref,
    source: "package-fallback-empty",
    raw: null,
  };
}

function entryNoMetadata(args: {
  spec: TrackSpec;
  packageName: string | null;
}): TemplateTrackEntry {
  const { spec, packageName } = args;
  return {
    kind: spec.kind,
    label: spec.label,
    description: spec.description,
    availability: "unknown",
    detail: packageName
      ? `${packageName} 没有 template_tracks 字段（老包 / 写入 manifest 之前生成的素材包）。`
      : "后端 DraftAsset.template_tracks 为 null；这条素材包在 manifest 元数据写入前生成。",
    impact: spec.impact,
    fixHref: spec.fixHref,
    source: "package-fallback-absent",
    raw: null,
  };
}

function entrySettingsDriven(args: {
  spec: TrackSpec;
  configured: boolean;
  settingReturned: boolean;
}): TemplateTrackEntry {
  const { spec, configured, settingReturned } = args;
  if (!settingReturned) {
    return {
      kind: spec.kind,
      label: spec.label,
      description: spec.description,
      availability: "unknown",
      detail: "后端未在 RuntimeSettings 中返回 DRAFT_BACKGROUND_MUSIC_PATH；无法判断 BGM 路径是否已配置。",
      impact: spec.impact,
      fixHref: spec.fixHref,
      source: "settings",
      raw: null,
    };
  }
  return {
    kind: spec.kind,
    label: spec.label,
    description: spec.description,
    availability: configured ? "available" : "absent",
    detail: configured
      ? "RuntimeSettings.DRAFT_BACKGROUND_MUSIC_PATH 已配置；后端在生成时会把 BGM 注入音轨。"
      : "RuntimeSettings.DRAFT_BACKGROUND_MUSIC_PATH 未配置；后端会跳过该轨道。",
    impact: spec.impact,
    fixHref: spec.fixHref,
    source: "settings",
    raw: configured ? "background_music(settings)" : null,
  };
}

export function resolveTemplateTracks(input: {
  episode: Episode | null | undefined;
  settings: SettingsLike;
  draftPackages?: DraftAsset[] | undefined;
  preferredPackage?: DraftAsset | null | undefined;
}): TemplateTrackSnapshot {
  const { episode, settings, draftPackages, preferredPackage } = input;
  const epMeta = describeEpisode(episode);
  const musicSetting = findSetting(settings, "DRAFT_BACKGROUND_MUSIC_PATH");
  const musicConfigured = isPathConfigured(musicSetting?.value);
  const musicReturned = musicSetting !== null;

  const targetPackage: DraftAsset | null =
    preferredPackage !== undefined
      ? preferredPackage
      : pickLatestPackage(draftPackages);
  const pkg = describeImportedPackage(targetPackage);

  // Parse backend metadata once, preserving raw entries and order.
  const backendEntries: ParsedBackendTrack[] = (pkg.templateTracks ?? [])
    .map(parseBackendTrack)
    .filter((entry): entry is ParsedBackendTrack => entry !== null);
  const backendByKind = new Map<TemplateTrackKind, ParsedBackendTrack>();
  for (const entry of backendEntries) {
    if (!backendByKind.has(entry.kind)) {
      backendByKind.set(entry.kind, entry);
    }
  }

  // Determine which fallback bucket we fall into.
  const metadataMode: TemplateTrackSource = (() => {
    if (pkg.templateTracks === null) return "package-fallback-absent";
    if (pkg.templateTracks.length === 0) return "package-fallback-empty";
    return "package-metadata";
  })();

  const tracks: TemplateTrackEntry[] = TRACK_SPECS.map((spec) => {
    // BGM is a special case: backend metadata is the source of truth for
    // "was BGM written into the draft", and RuntimeSettings is the source
    // of truth for "is the music path currently configured". We require
    // both signals before claiming availability.
    if (spec.kind === "background_music") {
      const backendTrack = backendByKind.get(spec.kind);
      if (backendTrack) {
        return entryFromBackend({
          spec,
          parsed: backendTrack,
          packageName: pkg.packageName,
        });
      }
      if (pkg.templateTracks === null) {
        return entryNoMetadata({ spec, packageName: pkg.packageName });
      }
      if (pkg.templateTracks.length === 0) {
        // Backend said: empty list. We still want to surface the runtime
        // setting so the operator can see why BGM is absent.
        const settingEntry = entrySettingsDriven({
          spec,
          configured: musicConfigured,
          settingReturned: musicReturned,
        });
        return {
          ...settingEntry,
          detail: `${settingEntry.detail}（后端在 ${pkg.packageName ?? "最近一次导入"} 的 template_tracks 为空数组，未列出 background_music。）`,
        };
      }
      // Metadata is present but does not mention background_music.
      return entrySettingsDriven({
        spec,
        configured: musicConfigured,
        settingReturned: musicReturned,
      });
    }

    const backendTrack = backendByKind.get(spec.kind);
    if (backendTrack) {
      return entryFromBackend({
        spec,
        parsed: backendTrack,
        packageName: pkg.packageName,
      });
    }
    if (pkg.templateTracks === null) {
      return entryNoMetadata({ spec, packageName: pkg.packageName });
    }
    if (pkg.templateTracks.length === 0) {
      return entryExplicitNone({ spec, packageName: pkg.packageName });
    }
    // Metadata is present but the backend did not list this kind.
    return {
      kind: spec.kind,
      label: spec.label,
      description: spec.description,
      availability: "absent",
      detail: pkg.packageName
        ? `${pkg.packageName} 的 template_tracks 中未列出 “${spec.kind}”；后端确认未生成该轨道。`
        : "最近一次导入响应的 template_tracks 中未列出该轨道；后端确认未生成。",
      impact: spec.impact,
      fixHref: spec.fixHref,
      source: "package-fallback-empty",
      raw: null,
    };
  });

  const source: TemplateTrackSnapshot["source"] = (() => {
    if (episode && settings) return "episode+settings";
    if (episode) return "episode";
    if (settings) return "settings";
    if (targetPackage) return "package";
    return "none";
  })();

  // For the snapshot's headline-level source, surface the metadata mode so
  // callers can see whether they are looking at real backend data or a
  // fallback. We retain the higher-level source label for backward
  // compatibility with the existing UI summary.
  void metadataMode;
  const readyCount = tracks.filter((entry) => entry.availability === "available").length;
  return {
    source,
    tracks,
    readyCount,
    totalCount: tracks.length,
    lastPackageImportedAt: pkg.modifiedAt,
    contentTemplateId: epMeta.contentTemplateId,
    backgroundMusicConfigured: musicConfigured,
    packageMetadata: pkg.templateTracks,
    packageImportStatus: pkg.importStatus,
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
  if (snapshot.packageMetadata === null) {
    return {
      tone: "yellow",
      headline: "尚未读取到 native 模板轨道元数据",
      detail:
        "当前素材包没有 template_tracks 字段；属于老包 / 写入 manifest 元数据之前的生成结果。请重新生成素材包以获得精确状态。",
    };
  }
  if (snapshot.packageMetadata.length === 0) {
    return {
      tone: "yellow",
      headline: "native writer 显式确认未写入任何模板轨道",
      detail: snapshot.lastPackageImportedAt
        ? `最近一次导入 (${snapshot.lastPackageImportedAt}) 报告 template_tracks 为空数组。`
        : "最近一次导入报告 template_tracks 为空数组。",
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
      headline: "最近一次生成未应用任何模板轨道",
      detail: snapshot.backgroundMusicConfigured
        ? "BGM 路径已配置；当前导入响应未包含 background_music，请确认后端或重新生成素材包。"
        : "后端未在最近一次响应中列出任何轨道；先生成或重新导入一次素材包。",
    };
  }
  return {
    tone: "yellow",
    headline: `部分模板轨道已生效（${snapshot.readyCount}/${snapshot.totalCount}）`,
    detail: "未生效的轨道由后端 DraftAsset.template_tracks 显式排除。",
  };
}
