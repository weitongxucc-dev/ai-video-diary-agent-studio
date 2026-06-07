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
    }

    for module_name, route_literals in expected_modules.items():
        router_source = (APP_ROOT / "routers" / module_name).read_text(encoding="utf-8")
        for route_literal in route_literals:
            assert route_literal in router_source
            assert route_literal not in main_source

    assert "include_router" in main_source
    assert "get_app_context" in main_source
