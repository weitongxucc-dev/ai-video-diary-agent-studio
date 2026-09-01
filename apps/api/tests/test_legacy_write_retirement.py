from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.drafts import DraftWorkspace
from app.montage import MontageRepository
from app.production import ProductionRepository
from app.repository import EpisodeRepository


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/episodes"),
        ("POST", "/api/episodes/episode-1/generate-topics"),
        ("PUT", "/api/episodes/episode-1/publish-metrics"),
        ("POST", "/api/drafts/package"),
        ("POST", "/api/drafts/import"),
        ("POST", "/api/production/batches"),
        ("POST", "/api/production/batches/batch-1/run"),
        ("POST", "/api/production/batches/batch-1/montage-plans"),
        ("PUT", "/api/production/episodes/episode-1/voice"),
        ("POST", "/api/production/episodes/episode-1/narration"),
        ("PATCH", "/api/production/montage-plans/plan-1/clips/clip-1"),
        ("POST", "/api/production/montage-plans/plan-1/render"),
    ],
)
def test_legacy_creation_write_routes_return_gone(method: str, path: str) -> None:
    response = TestClient(main_module.app).request(method, path, json={})

    assert response.status_code == 410
    assert response.json()["detail"]["error"] == "legacy_creation_write_retired"


def test_legacy_repositories_no_longer_expose_business_writers() -> None:
    assert not hasattr(EpisodeRepository, "create")
    assert not hasattr(EpisodeRepository, "today")
    assert not hasattr(EpisodeRepository, "save")
    assert not hasattr(ProductionRepository, "create_batch")
    assert not hasattr(ProductionRepository, "save_batch")
    assert not hasattr(ProductionRepository, "select_episode_versioned_voice")
    assert not hasattr(MontageRepository, "save")
    assert not hasattr(DraftWorkspace, "create_package")
    assert not hasattr(DraftWorkspace, "create_package_and_import_to_jianying")
    assert not hasattr(DraftWorkspace, "import_package_to_jianying")


def test_packaged_api_no_longer_includes_legacy_draft_tooling() -> None:
    spec = (Path(__file__).resolve().parents[1] / "pyinstaller.spec").read_text(
        encoding="utf-8"
    )

    for retired_dependency in ("pyJianYingDraft", "jianyingdraft", "capcut_mate"):
        assert retired_dependency not in spec
