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
        "preferences.py": [
            '"/api/feedback/summary"',
            '"/api/preferences"',
            '"/api/preferences/confirm"',
            '"/api/preferences/{preference_id}/disable"',
            '"/api/preferences/{preference_id}/restore"',
            '"/api/preferences/{preference_id}/delete"',
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
