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
    ...overrides,
  };
}

describe("resolveTemplateTracks", () => {
  it("returns none source when no inputs are provided", () => {
    const snapshot = resolveTemplateTracks({
      episode: null,
      settings: null,
      draftPackage: null,
    });
    expect(snapshot.source).toBe("none");
    expect(snapshot.totalCount).toBeGreaterThan(0);
    snapshot.tracks.forEach((track) => {
      expect(track.availability).toBe("unknown");
    });
  });

  it("marks title/chapter/keyword as available when template is selected and an imported package exists", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(false),
      draftPackage: makePackage(),
    });
    const title = snapshot.tracks.find((t) => t.kind === "title_card");
    const chapter = snapshot.tracks.find((t) => t.kind === "chapter_labels");
    const keyword = snapshot.tracks.find((t) => t.kind === "keyword_emphasis");
    const bgm = snapshot.tracks.find((t) => t.kind === "background_music");
    expect(title?.availability).toBe("available");
    expect(chapter?.availability).toBe("available");
    expect(keyword?.availability).toBe("available");
    expect(bgm?.availability).toBe("absent");
  });

  it("marks BGM available when settings expose a configured DRAFT_BACKGROUND_MUSIC_PATH", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(true),
      draftPackage: makePackage(),
    });
    const bgm = snapshot.tracks.find((t) => t.kind === "background_music");
    expect(bgm?.availability).toBe("available");
  });

  it("marks chapter_labels as absent when template is selected but script has no sections", () => {
    const episode = makeEpisode();
    episode.script = {
      ...episode.script!,
      sections: [],
    };
    const snapshot = resolveTemplateTracks({
      episode,
      settings: makeSettings(false),
      draftPackage: makePackage(),
    });
    const chapter = snapshot.tracks.find((t) => t.kind === "chapter_labels");
    expect(chapter?.availability).toBe("absent");
  });

  it("marks template tracks as unknown when content_template_id is empty", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode({ content_template_id: "" }),
      settings: makeSettings(false),
      draftPackage: makePackage(),
    });
    const title = snapshot.tracks.find((t) => t.kind === "title_card");
    expect(title?.availability).toBe("unknown");
  });

  it("preserves template-tracks field name and never fabricates availability", () => {
    const snapshot = resolveTemplateTracks({
      episode: null,
      settings: null,
      draftPackage: null,
    });
    const kinds = new Set(snapshot.tracks.map((t) => t.kind));
    expect(kinds.has("title_card")).toBe(true);
    expect(kinds.has("chapter_labels")).toBe(true);
    expect(kinds.has("keyword_emphasis")).toBe(true);
    expect(kinds.has("background_music")).toBe(true);
    // BGM should never claim available without settings + imported package.
    const bgm = snapshot.tracks.find((t) => t.kind === "background_music")!;
    expect(bgm.availability).toBe("unknown");
  });
});

describe("describeTrackTone", () => {
  it("returns muted tone when source is none", () => {
    const snapshot = resolveTemplateTracks({
      episode: null,
      settings: null,
      draftPackage: null,
    });
    const tone = describeTrackTone(snapshot);
    expect(tone.tone).toBe("muted");
    expect(tone.headline).toMatch(/尚未/);
  });

  it("returns green when every track is available", () => {
    const snapshot = resolveTemplateTracks({
      episode: makeEpisode(),
      settings: makeSettings(true),
      draftPackage: makePackage(),
    });
    const tone = describeTrackTone(snapshot);
    expect(tone.tone).toBe("green");
  });
});
