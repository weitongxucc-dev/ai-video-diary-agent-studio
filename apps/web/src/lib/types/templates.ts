import type { ContentVisibility } from './common';
import type { CreationPlanInput } from './creation-plans';
export type VisualTemplate = {
    engine: 'openfilm-html-v1';
    category: 'title' | 'chapter' | 'callout' | 'data' | 'product' | 'ending';
    width: number; height: number; duration_seconds: number; source: string;
    bindings: {key: string; label: string; kind: 'text' | 'variable'; target: string; default: string}[];
    values: Record<string, string>;
    provenance: {repository: string; revision: string; path: string; license: string; source_sha256: string}[];
    dependencies: string[];
};
export type VideoTemplateSettings = {
    kind?: 'visual_component' | 'composition';
    visual?: VisualTemplate;
    visual_components?: Record<string, unknown>[];
    language: CreationPlanInput["language"];
    aspect_ratio: "9:16" | "16:9" | "1:1";
    duration_target_seconds: number;
    presenter_share_target?: 0 | 20 | 40 | 60 | 80 | 100;
    lipsync_quality?: "high" | "fast";
    voice: {
        profile_id: string;
        version: string;
    } | null;
    script_structure?: string[];
    script_section_count?: number | null;
    style_instruction: string;
    fit_mode: "fill" | "fit";
    subtitle_style: Record<string, unknown>;
    color_style?: {
        exposure: number;
        contrast: number;
        saturation: number;
        temperature: number;
    } | null;
};
export type VideoTemplate = {
    can_edit?: boolean;
    id: string;
    version: number;
    visibility: ContentVisibility;
    name: string;
    origin: "built_in" | "user";
    settings: VideoTemplateSettings;
    source_task_id: string | null;
    source_timeline_version_id: string | null;
    created_at: string;
};
export type ContentTemplate = {
    template_id: string;
    name: string;
    scenario: string;
    recommended_duration: string;
    topic_structure: string[];
    script_structure: string[];
    material_requirements: string[];
    jianying_style: string;
    subtitle_style: string;
    title_rules: string[];
    cover_copy_rules: string[];
    publish_copy_rules: string[];
    default_feedback_dimensions: string[];
};
export type ContentTemplates = {
    templates: ContentTemplate[];
};
export type TemplateTrackKind = "title_card" | "chapter_labels" | "keyword_emphasis" | "background_music";
export type TemplateTrackAvailability = "available" | "absent" | "unknown";
export type TemplateTrackSource = "package-metadata" | "package-fallback-empty" | "package-fallback-absent" | "settings" | "none";
export type TemplateTrackEntry = {
    kind: TemplateTrackKind;
    label: string;
    description: string;
    availability: TemplateTrackAvailability;
    detail: string;
    impact: string;
    fixHref?: string;
    source: TemplateTrackSource;
    raw: string | null;
};
export type TemplateTrackSnapshot = {
    source: "episode+settings" | "episode" | "settings" | "package" | "none";
    tracks: TemplateTrackEntry[];
    readyCount: number;
    totalCount: number;
    lastPackageImportedAt: string | null;
    contentTemplateId: string | null;
    backgroundMusicConfigured: boolean;
    packageMetadata: string[] | null;
    packageImportStatus: string | null;
};
