from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def test_system_diagnostics_and_settings_routes_are_split_from_main():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    factory_source = (APP_ROOT / "app_factory.py").read_text(encoding="utf-8")
    expected_modules = {
        "system.py": [
            '"/api/system/status"',
            '"/api/workflow/status"',
            '"/api/content-templates"',
            '"/api/runtime/ping"',
            '"/api/runtime/health"',
        ],
        "diagnostics.py": [
            '"/api/diagnostics/status"',
            '"/api/diagnostics/report"',
        ],
        "settings.py": [
            '"/api/settings/runtime"',
        ],
        "sources.py": [
            '"/api/sources/items"',
            '"/api/sources/sync"',
            '"/api/sources/links"',
        ],
        "creator_profile.py": [
            '"/api/creator-profile"',
        ],
        "knowledge.py": [
            '"/api/knowledge/notes"',
        ],
        "retake_reports.py": [
            '"/api/episodes/{episode_id}/retake-report"',
            '"/api/retake-reports/latest"',
        ],
        "edit_plan.py": [
            '"/api/episodes/{episode_id}/edit-plan"',
            '"/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/decision"',
            '"/api/episodes/{episode_id}/edit-plan/segments/{segment_id}/revert"',
        ],
        "feedback.py": [
            '"/api/feedback/summary"',
        ],
        "preferences.py": [
            '"/api/preferences"',
            '"/api/preferences/confirm"',
            '"/api/preferences/{preference_id}/disable"',
            '"/api/preferences/{preference_id}/restore"',
            '"/api/preferences/{preference_id}/delete"',
        ],
        "videos.py": [
            '"/api/videos/pending"',
            '"/api/videos/process"',
            '"/api/videos/processed/{file_name}"',
        ],
        "drafts.py": [
            '"/api/drafts/status"',
            '"/api/drafts/package"',
            '"/api/drafts/import"',
        ],
        "episode_media.py": [
            '"/api/episodes/{episode_id}/upload-video"',
            '"/api/episodes/{episode_id}/process-video"',
            '"/api/episodes/{episode_id}/process-ad-hoc-video"',
            '"/api/episodes/{episode_id}/process-ad-hoc-video-batch"',
            '"/api/episodes/{episode_id}/create-draft"',
        ],
        "episode_workflow.py": [
            '"/api/script/rewrite-intents"',
            '"/api/episodes/today"',
            '"/api/episodes"',
            '"/api/episodes/{episode_id}"',
            '"/api/episodes/{episode_id}/content-template"',
            '"/api/episodes/{episode_id}/generate-topics"',
            '"/api/episodes/{episode_id}/regenerate-topics"',
            '"/api/episodes/{episode_id}/approve-topic"',
            '"/api/episodes/{episode_id}/generate-script"',
            '"/api/episodes/{episode_id}/script"',
            '"/api/episodes/{episode_id}/script/regenerate"',
            '"/api/episodes/{episode_id}/script/sections/{section_id}/regenerate"',
            '"/api/episodes/{episode_id}/script/revert"',
            '"/api/episodes/{episode_id}/approve-script"',
            '"/api/voice/transcribe"',
            '"/api/episodes/{episode_id}/voice-command/preview"',
            '"/api/episodes/{episode_id}/voice-command/execute"',
            '"/api/episodes/{episode_id}/mark-published"',
            '"/api/episodes/{episode_id}/comment-insights"',
            '"/api/episodes/{episode_id}/feedback"',
            '"/api/episodes/{episode_id}/feedback/revert"',
        ],
        "cover.py": [
            '"/api/episodes/{episode_id}/cover"',
        ],
    }

    for module_name, route_literals in expected_modules.items():
        router_source = (APP_ROOT / "routers" / module_name).read_text(encoding="utf-8")
        for route_literal in route_literals:
            assert route_literal in router_source
            assert route_literal not in main_source

    assert "include_router" in factory_source
    assert "get_app_context" in factory_source


def test_manual_source_items_state_is_not_owned_by_main_module():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    context_source = (APP_ROOT / "routers" / "context.py").read_text(encoding="utf-8")

    assert "manual_source_items:" not in main_source
    assert "manual_source_items" in context_source


def test_media_router_dependencies_are_supplied_by_app_context():
    factory_source = (APP_ROOT / "app_factory.py").read_text(encoding="utf-8")
    context_source = (APP_ROOT / "routers" / "context.py").read_text(encoding="utf-8")

    for dependency_name in (
        "creator_profile_repo",
        "service",
        "video_inbox",
        "video_pipeline",
        "draft_workspace",
        "video_job_runner",
        "edit_plan_apply_runner",
        "voice_transcriber",
    ):
        assert f"{dependency_name}:" in context_source
        assert f"{dependency_name}=runtime.{dependency_name}" in factory_source


def test_runtime_dependencies_are_owned_by_runtime_container():
    factory_source = (APP_ROOT / "app_factory.py").read_text(encoding="utf-8")

    for assignment in (
        "settings = Settings.from_env()",
        "repo = EpisodeRepository(",
        "creator_profile_repo = CreatorProfileRepository(",
        "preference_repo = LongTermPreferenceRepository(",
        "vault = ObsidianVault(",
        "video_inbox = VideoInbox(",
        "video_processor = FfmpegVideoProcessor(",
        "video_pipeline = video_processing_pipeline_from_settings(",
        "draft_workspace = DraftWorkspace(",
        "voice_transcriber = asr_transcriber_from_settings(",
        "service = EpisodeService(",
        "workflow = LangGraphEpisodeWorkflow(",
    ):
        assert assignment not in factory_source

    assert "create_runtime(" in factory_source
    assert "app.state.runtime" in factory_source


def test_main_module_is_only_the_asgi_entrypoint():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")

    assert "create_app(" in main_source
    assert "include_router" not in main_source
    assert "@app." not in main_source
    for forbidden in (
        "EpisodeRepository",
        "CreatorProfileRepository",
        "LongTermPreferenceRepository",
        "FfmpegVideoProcessor",
        "LangGraphEpisodeWorkflow",
        "episode_media_flow",
    ):
        assert forbidden not in main_source


def test_draft_data_models_are_split_from_workspace_facade():
    draft_models_source = (APP_ROOT / "draft_models.py").read_text(encoding="utf-8")
    drafts_source = (APP_ROOT / "drafts.py").read_text(encoding="utf-8")

    for model_name in (
        "DraftAsset",
        "DraftToolStatus",
        "DraftEnvironment",
        "DraftPackage",
        "DraftImportResult",
    ):
        assert f"class {model_name}" in draft_models_source
        assert f"from app.draft_models import" in drafts_source
        assert f"class {model_name}" not in drafts_source


def test_draft_index_projection_is_split_from_workspace_facade():
    draft_index_source = (APP_ROOT / "draft_index.py").read_text(encoding="utf-8")
    drafts_source = (APP_ROOT / "drafts.py").read_text(encoding="utf-8")

    for helper_name in (
        "asset_from_path",
        "package_asset_from_path",
        "package_dirs",
        "episode_summary",
        "episode_source_mode",
    ):
        assert f"def {helper_name}" in draft_index_source
        assert helper_name in drafts_source

    for former_method in (
        "def _package_asset_from_path",
        "def _template_summary",
        "def _track_preview",
        "def _episode_summary",
        "def _episode_source_mode",
    ):
        assert former_method not in drafts_source


def test_draft_artifact_generation_is_split_from_workspace_facade():
    draft_artifacts_source = (APP_ROOT / "draft_artifacts.py").read_text(encoding="utf-8")
    drafts_source = (APP_ROOT / "drafts.py").read_text(encoding="utf-8")

    for helper_name in (
        "write_script_artifacts",
        "write_asr_subtitle_if_available",
        "script_to_srt",
        "write_cover",
    ):
        assert f"def {helper_name}" in draft_artifacts_source

    assert "from app.draft_artifacts import" in drafts_source
    for implementation_detail in (
        "Image.new(",
        "transcription_to_srt",
        "build_extract_audio_command",
        "textwrap.wrap",
    ):
        assert implementation_detail not in drafts_source
