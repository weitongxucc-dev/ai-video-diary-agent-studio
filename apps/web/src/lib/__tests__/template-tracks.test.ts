import { describe, it, expect } from "vitest";
import {
  describeTrackTone,
  resolveTemplateTracks,
} from "../template-tracks";
import type {
  DraftAsset,
  Episode,
  RuntimeSettings,
  ScriptPackage,
  ScriptSection,
  TemplateTrackEntry,
} from "../types";

function makeSettings(musicConfigured: boolean): RuntimeSettings {
  return {
    settings: [
      {
        name: "DRAFT_BACKGROUND_MUSIC_PATH",
        configured: musicConfigured,
        value: musicConfigured ? "/Users/me/Music/bgm.mp3" : null,
        secret: false,
      },
    ],
    status: {
      llm: { provider: "deepseek", configured: true, model: null, base_url: "" },
      obsidian: { configured: true, exists: true, path: null, markdown_count: 0 },
      product_hunt: { configured: false, api_url: "" },
      external_sources: { configured: false, urls: [] },
      hermes_radar: {
        configured: true,
        exists: true,
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
        ffmpeg_available: true,
      },
      storage: { database_path: "", database_exists: true },
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
    },
  } as RuntimeSettings;
}

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  const script: ScriptPackage = {
    id: "script_1",
    hook: "",
    sections: [
      { id: "s1", title: "开篇", estimated_seconds: 30, content: "x", feedback: null },
      { id: "s2", title: "中段", estimated_seconds: 30, content: "y", feedback: null },
    ] as ScriptSection[],
    title_options: ["A"],
    cover_copy: "",
    publish_copy: "",
    comment_prompt: "",
  };
  return {
    id: "ep_1",
    day_number: 1,
    episode_date: "2026-08-12",
    status: "draft_ready",
    intake_mode: "planned_single",
    content_template_id: "tpl_3min",
    topics: [],
    selected_topic_id: null,
    script,
    script_versions: [],
    video_source_file_name: null,
    video_source_file_names: [],
    video_processed_file_name: null,
    video_processed_path: null,
    video_edit_plan_path: null,
    video_dedup_report_path: null,
    video_job_status: "idle",
    video_job_message: null,
    video_job_error: null,
    video_job_started_at: null,
    video_job_finished_at: null,
    draft_package_name: null,
    draft_package_path: null,
    publish_url: null,
    platform: null,
    notes: null,
    play_count: null,
    like_count: null,
    comment_count: null,
    published_at: null,
    publish_feedback: null,
    feedback_events: [],
    production_mode: null,
    archived_source_file_names: [],
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function makePackage(overrides: Partial<DraftAsset> = {}): DraftAsset {
  return {
    file_name: "pkg_20260812.zip",
    path: "/Users/me/Drafts/pkg.zip",
    size_bytes: 1024,
    modified_at: "2026-08-12T01:23:45Z",
    import_status: "imported",
    import_message: "已通过原生写入生成剪映兼容草稿。",
    draft_path: "/Users/me/Jianying/draft1",
    template_tracks: null,
    ...overrides,
  };
}

function findTrack(
  tracks: TemplateTrackEntry[],
  kind: TemplateTrackEntry["kind"],
) {
  return tracks.find((track) => track.kind === kind);
}

describe("resolveTemplateTracks - real backend metadata", () => {
  it("uses actual template_tracks entries with kind+payload when provided", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({
        template_tracks: [
          "title_card:tpl_3min",
          "chapter_labels",
          "keyword_emphasis",
        ],
      }),
    });
    const title = findTrack(snapshot.tracks, "title_card");
    const chapter = findTrack(snapshot.tracks, "chapter_labels");
    const keyword = findTrack(snapshot.tracks, "keyword_emphasis");
    const bgm = findTrack(snapshot.tracks, "background_music");

    expect(title?.availability).toBe("available");
    expect(title?.source).toBe("package-metadata");
    expect(title?.raw).toBe("title_card:tpl_3min");
    expect(title?.detail).toContain("title_card:tpl_3min");
    expect(chapter?.availability).toBe("available");
    expect(keyword?.availability).toBe("available");
    expect(bgm?.availability).toBe("absent");
    expect(bgm?.source).toBe("settings");
  });

  it("ignores unknown kinds in template_tracks (e.g. legacy tokens) without claiming they exist", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({
        template_tracks: [
          "title_card:tpl_x",
          "unknown_kind:foo",
          "another_legacy",
        ],
      }),
    });
    expect(snapshot.packageMetadata).toEqual([
      "title_card:tpl_x",
      "unknown_kind:foo",
      "another_legacy",
    ]);
    const title = findTrack(snapshot.tracks, "title_card");
    expect(title?.availability).toBe("available");
    // No track should be falsely reported as present because of unknown tokens.
    expect(snapshot.tracks).toHaveLength(4);
  });

  it("marks BGM available when backend metadata includes background_music", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({
        template_tracks: ["title_card:tpl_x", "background_music"],
      }),
    });
    const bgm = findTrack(snapshot.tracks, "background_music");
    expect(bgm?.availability).toBe("available");
    expect(bgm?.source).toBe("package-metadata");
    expect(bgm?.raw).toBe("background_music");
  });
});

describe("resolveTemplateTracks - BGM configuration from runtime setting", () => {
  it("uses RuntimeSettings DRAFT_BACKGROUND_MUSIC_PATH when metadata does not mention background_music", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(true),
      preferredPackage: makePackage({
        template_tracks: ["title_card:tpl_3min", "chapter_labels"],
      }),
    });
    const bgm = findTrack(snapshot.tracks, "background_music");
    // BGM is sourced from the runtime setting per task contract — settings
    // configured → BGM available, regardless of whether the latest package
    // listed it. The detail still surfaces the metadata state for honesty.
    expect(bgm?.availability).toBe("available");
    expect(bgm?.source).toBe("settings");
    expect(bgm?.detail).toContain("DRAFT_BACKGROUND_MUSIC_PATH");
  });

  it("returns unknown BGM when RuntimeSettings does not expose the setting", () => {
    const settings: RuntimeSettings = {
      settings: [],
      status: makeSettings(false).status,
    } as RuntimeSettings;
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings,
      preferredPackage: makePackage({
        template_tracks: ["title_card:tpl_3min"],
      }),
    });
    const bgm = findTrack(snapshot.tracks, "background_music");
    expect(bgm?.availability).toBe("unknown");
    expect(bgm?.source).toBe("settings");
    expect(bgm?.detail).toContain("DRAFT_BACKGROUND_MUSIC_PATH");
  });
});

describe("resolveTemplateTracks - explicit fallbacks", () => {
  it("marks every track as package-fallback-absent when template_tracks is null (legacy packages)", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({ template_tracks: null }),
    });
    expect(snapshot.packageMetadata).toBeNull();
    for (const track of snapshot.tracks) {
      expect(track.availability).toBe("unknown");
      expect(track.source).toBe("package-fallback-absent");
      expect(track.detail).toMatch(/老包|template_tracks 字段/);
    }
  });

  it("marks template-bound tracks as package-fallback-empty when template_tracks is empty (BGM still from settings)", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({ template_tracks: [] }),
    });
    expect(snapshot.packageMetadata).toEqual([]);
    for (const track of snapshot.tracks) {
      if (track.kind === "background_music") {
        // BGM is sourced from the runtime setting, independent of metadata.
        expect(track.source).toBe("settings");
        continue;
      }
      expect(track.availability).toBe("absent");
      expect(track.source).toBe("package-fallback-empty");
    }
    const bgm = findTrack(snapshot.tracks, "background_music");
    expect(bgm?.detail).toContain("template_tracks 为空数组");
  });

  it("marks a missing kind as package-fallback-empty when other kinds are present", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({
        template_tracks: ["title_card:tpl_3min"],
      }),
    });
    const chapter = findTrack(snapshot.tracks, "chapter_labels");
    const keyword = findTrack(snapshot.tracks, "keyword_emphasis");
    expect(chapter?.availability).toBe("absent");
    expect(chapter?.source).toBe("package-fallback-empty");
    expect(keyword?.availability).toBe("absent");
    expect(keyword?.source).toBe("package-fallback-empty");
    expect(chapter?.detail).toContain("未列出");
  });
});

describe("resolveTemplateTracks - secrets + redaction", () => {
  it("does not leak the BGM path into any detail string", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(true),
      preferredPackage: makePackage({
        template_tracks: ["background_music"],
      }),
    });
    const json = JSON.stringify(snapshot);
    expect(json).not.toContain("/Users/me/Music/bgm.mp3");
    const bgm = findTrack(snapshot.tracks, "background_music");
    expect(bgm?.detail).not.toContain("/Users/me/Music/bgm.mp3");
  });
});

describe("describeTrackTone - fallback-aware headlines", () => {
  it("returns yellow headline when packageMetadata is null", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({ template_tracks: null }),
    });
    const tone = describeTrackTone(snapshot);
    expect(tone.tone).toBe("yellow");
    expect(tone.headline).toMatch(/尚未读取到 native 模板轨道元数据/);
  });

  it("returns yellow headline when packageMetadata is an empty array", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      preferredPackage: makePackage({ template_tracks: [] }),
    });
    const tone = describeTrackTone(snapshot);
    expect(tone.tone).toBe("yellow");
    expect(tone.headline).toMatch(/显式确认未写入/);
  });

  it("returns green when all four kinds are in real metadata", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(true),
      preferredPackage: makePackage({
        template_tracks: [
          "title_card:tpl_3min",
          "chapter_labels",
          "keyword_emphasis",
          "background_music",
        ],
      }),
    });
    const tone = describeTrackTone(snapshot);
    expect(tone.tone).toBe("green");
  });
});
