from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def test_system_diagnostics_and_settings_routes_are_split_from_main():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    expected_modules = {
        "system.py": [
            '"/api/system/status"',
            '"/api/workflow/status"',
            '"/api/content-templates"',
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
    }

    for module_name, route_literals in expected_modules.items():
        router_source = (APP_ROOT / "routers" / module_name).read_text(encoding="utf-8")
        for route_literal in route_literals:
            assert route_literal in router_source
            assert route_literal not in main_source

    assert "include_router" in main_source
    assert "get_app_context" in main_source


def test_manual_source_items_state_is_not_owned_by_main_module():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
    context_source = (APP_ROOT / "routers" / "context.py").read_text(encoding="utf-8")

    assert "manual_source_items:" not in main_source
    assert "manual_source_items" in context_source


def test_media_router_dependencies_are_supplied_by_app_context():
    main_source = (APP_ROOT / "main.py").read_text(encoding="utf-8")
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
        assert f"{dependency_name}={dependency_name}" in main_source
