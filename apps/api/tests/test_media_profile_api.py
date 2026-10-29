from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.avatar_lipsync import UnavailableAvatarAndLipSyncProvider
from app.external_sources import ExternalSourceResponseError
from app.production import ProductionRepository
from app.source_repository import SourceRepository
from app.production import MaterialAsset
from app.local_members import LocalMemberService
from _media_preview_test_fixtures import synthetic_wav_bytes, write_synthetic_video
from PIL import Image


class _UnavailableVoiceCloneProvider:
    model_id = "voice-clone-unconfigured"
    ready = False
    message = "本地声音复制运行环境尚未配置。"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    library = tmp_path / "library"
    library.mkdir()
    database_path = tmp_path / "agent.sqlite3"
    monkeypatch.setattr(main_module, "source_repository", SourceRepository(database_path))
    monkeypatch.setattr(
        main_module,
        "production_repo",
        ProductionRepository(
            database_path=database_path,
            video_library_path=library,
            duration_reader=lambda _path: 10.0,
            scene_reader=lambda _path, _duration: [],
        ),
    )
    monkeypatch.setattr(main_module, "voice_clone_provider", _UnavailableVoiceCloneProvider())
    monkeypatch.setattr(
        main_module,
        "avatar_lipsync_provider",
        UnavailableAvatarAndLipSyncProvider(),
    )
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", "workspace-a"),
    )
    repository = main_module.production_repo
    members = LocalMemberService(database_path)
    owner, token = members.whoami(None)
    monkeypatch.setattr(main_module, "local_members_service", members)
    approved = library / "approved"
    approved.mkdir(exist_ok=True)
    image_path = approved / "profile-reference.png"
    Image.new("RGB", (16, 16), (32, 96, 160)).save(image_path, format="PNG")
    repository._save_asset(MaterialAsset(
        id="profile-reference", workspace_id="workspace-a", file_name=image_path.name,
        path=str(image_path), size_bytes=image_path.stat().st_size,
        modified_at=datetime.fromtimestamp(image_path.stat().st_mtime, timezone.utc),
        media_type="image", owner_user_id=owner.member_id,
    ))
    voice_reference, _, _ = repository.import_voice_reference_wav(
        "workspace-a", synthetic_wav_bytes(), verified_owner_user_id=owner.member_id,
    )
    voice_path = repository.resolve_workspace_media_reference("workspace-a", voice_reference)
    voice_asset = repository._asset_by_path("workspace-a", str(voice_path))
    client = TestClient(main_module.app, headers={"X-Local-Member-Token": token})
    client.profile_owner_id = owner.member_id
    client.voice_reference = voice_reference
    client.voice_asset_id = voice_asset.id
    return client


def _authorization_payload(
    authorization_id: str,
    *,
    version: str = "v1",
    status: str = "active",
    content_type: str = "person_likeness",
    usage_scope: str = "avatar_lipsync",
) -> dict[str, object]:
    return {
        "id": authorization_id,
        "version": version,
        "rights_holder": "权利人",
        "content_types": [content_type],
        "usage_scopes": [usage_scope],
        "effective_from": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
        "expires_at": None,
        "evidence_reference": "consent://record/1",
        "status": status,
    }


def _person_payload(
    *,
    version: str = "person-v1",
    authorization_version: str = "v1",
) -> dict[str, object]:
    return {
        "id": "person-a",
        "display_name": "主播 A",
        "version": version,
        "avatar_reference": "workspace://approved/profile-reference.png",
        "background_capabilities": ["studio"],
        "shot_capabilities": ["medium"],
        "authorization": _authorization_payload(
            "person-authorization", version=authorization_version
        ),
        "reference": {"asset_id": "profile-reference"},
    }


def test_person_profile_allows_empty_optional_capabilities(
    client: TestClient,
) -> None:
    payload = _person_payload()
    payload["background_capabilities"] = []
    payload["shot_capabilities"] = []

    saved = client.post("/api/media/person-profiles", json=payload)

    assert saved.status_code == 200, saved.text
    assert saved.json()["background_capabilities"] == []
    assert saved.json()["shot_capabilities"] == []
    listed = client.get("/api/media/person-profiles")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["background_capabilities"] == []
    assert listed.json()["items"][0]["shot_capabilities"] == []


def test_person_profile_reports_body_reference_source_and_missing_file(
    client: TestClient,
) -> None:
    asset_backed = client.post("/api/media/person-profiles", json=_person_payload())
    assert asset_backed.status_code == 200, asset_backed.text
    assert asset_backed.json()["body_reference_kind"] == "workspace"
    assert asset_backed.json()["body_reference_available"] is True
    (main_module.production_repo.video_library_path / "approved" / "profile-reference.png").unlink()
    missing = client.get("/api/media/person-profiles/person-a")
    assert missing.status_code == 200
    assert missing.json()["body_reference_available"] is False
    assert missing.json()["body_reference_reason"]
    assert asset_backed.json()["runnable"] is True

    library = main_module.production_repo.video_library_path
    (library / "approved").mkdir(exist_ok=True)
    video_path = write_synthetic_video(library / "approved" / "person-a.mp4", seconds=10)
    stat = video_path.stat()
    main_module.production_repo._save_asset(MaterialAsset(
        id="person-a-video", workspace_id="workspace-a", file_name=video_path.name,
        path=str(video_path), size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        media_type="video", duration_seconds=10, owner_user_id=client.profile_owner_id,
    ))
    payload = _person_payload()
    payload["version"] = "person-v1-workspace"
    payload["avatar_reference"] = "workspace://approved/person-a.mp4"
    payload["reference"] = {"asset_id": "person-a-video"}

    saved = client.post("/api/media/person-profiles", json=payload)
    assert saved.status_code == 200
    assert saved.json()["body_reference_kind"] == "workspace"
    assert saved.json()["body_reference_name"] == "person-a.mp4"
    assert saved.json()["body_reference_available"] is True
    assert saved.json()["body_reference_reason"] is None

    payload["version"] = "person-v2-missing"
    payload["avatar_reference"] = "workspace://approved/removed.mp4"
    missing_path = write_synthetic_video(library / "approved" / "removed.mp4", seconds=10)
    missing_stat = missing_path.stat()
    main_module.production_repo._save_asset(MaterialAsset(
        id="person-a-removed-video", workspace_id="workspace-a", file_name=missing_path.name,
        path=str(missing_path), size_bytes=missing_stat.st_size,
        modified_at=datetime.fromtimestamp(missing_stat.st_mtime, timezone.utc),
        media_type="video", duration_seconds=10, owner_user_id=client.profile_owner_id,
    ))
    payload["reference"] = {"asset_id": "person-a-removed-video"}
    missing = client.post("/api/media/person-profiles", json=payload)
    assert missing.status_code == 200
    assert missing.json()["body_reference_available"] is True
    missing_path.unlink()
    missing = client.get("/api/media/person-profiles/person-a?version=person-v2-missing")
    assert missing.status_code == 200
    assert missing.json()["body_reference_available"] is False
    assert missing.json()["reference"]["asset_id"] == "person-a-removed-video"
    assert missing.json()["body_reference_reason"] == "参考文件失联或为空，请重新定位原参考"
    assert missing.json()["body_reference_repair_action"]

    (library / "approved" / "a-folder.mp4").mkdir()
    payload["version"] = "person-v3-directory"
    payload["avatar_reference"] = "workspace://approved/a-folder.mp4"
    directory = client.post("/api/media/person-profiles", json=payload)
    assert directory.status_code == 422

    versions = client.get("/api/media/person-profiles/person-a/versions")
    assert versions.status_code == 200
    by_version = {item["version"]: item for item in versions.json()["items"]}
    assert set(by_version) == {
        "person-v1",
        "person-v1-workspace",
        "person-v2-missing",
    }
    assert by_version["person-v1-workspace"]["body_reference_available"] is True
    assert by_version["person-v2-missing"]["body_reference_available"] is False


def test_source_run_history_persists_after_failure_and_is_workspace_scoped(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def __init__(self, _urls: list[str]) -> None:
            pass

        def fetch_items(self):
            raise ExternalSourceResponseError(
                "authentication required",
                kind="authentication_required",
                impact="No source items were imported.",
                repair_action="Sign in to the source account.",
                retryable=False,
            )

    monkeypatch.setattr(main_module, "HtmlPageSourceClient", FailingClient)
    connector_response = client.post(
        "/api/source-connectors",
        json={
            "platform": "external_urls",
            "protocol": "https",
            "account_or_rule": "https://example.com/feed",
            "frequency": "hourly",
            "permissions": ["read:items"],
        },
    )
    connector = connector_response.json()

    failed = client.post(f"/api/source-connectors/{connector['id']}/sync")

    assert failed.status_code == 401
    run = failed.json()["detail"]["run"]
    main_module.source_repository = SourceRepository(main_module.source_repository._database_path)
    history = client.get(f"/api/source-connectors/{connector['id']}/runs")
    latest = client.get(f"/api/source-connectors/{connector['id']}/runs/latest")

    assert history.json() == {"items": [run], "total_count": 1}
    assert latest.json() == run
    assert run["status"] == "failed"
    assert run["item_count"] == 0
    assert run["reason"] == "authentication required"
    assert run["impact"] == "No source items were imported."
    assert run["repair_action"] == "Sign in to the source account."
    assert run["retryable"] is False
    other_workspace = {"x-test-workspace": "workspace-b"}
    assert client.get(
        f"/api/source-connectors/{connector['id']}/runs", headers=other_workspace
    ).status_code == 404
    assert client.get(
        f"/api/source-connectors/{connector['id']}/runs/{run['id']}",
        headers=other_workspace,
    ).status_code == 404


def test_media_profiles_are_versioned_workspace_scoped_and_honest_about_revocation(
    client: TestClient,
) -> None:
    person_v1 = client.post("/api/media/person-profiles", json=_person_payload())
    person_v2 = client.post(
        "/api/media/person-profiles",
        json=_person_payload(version="person-v2"),
    )
    voice = client.post(
        "/api/media/voice-profiles",
        json={
            "id": "voice-a",
            "person_profile_id": "person-a",
            "person_version": "person-v2",
            "version": "voice-v1",
            "language": "zh-en",
            "display_name": "主播声音 A",
            "reference_audio_path": client.voice_reference,
            "reference": {"asset_id": client.voice_asset_id},
            "reference_text": "主播 A 的声音参考文字。",
            "model_id": "qwen3-tts-v1",
            "authorization": _authorization_payload(
                "voice-authorization",
                content_type="voice",
                usage_scope="narration",
            ),
        },
    )
    pronunciation = client.post(
        "/api/media/pronunciation-entries",
        json={
            "id": "term-codex",
            "term": "Codex",
            "language": "en",
            "target_pronunciation": "KO-deks",
            "scope": "person",
            "scope_reference": "person-a",
            "version": "v1",
        },
    )

    assert person_v1.status_code == 200
    assert person_v2.status_code == 200
    assert voice.status_code == 200, voice.text
    assert voice.json()["reference_audio_path"] == client.voice_reference
    assert pronunciation.status_code == 200
    assert client.get("/api/media/person-profiles").json()["items"][0]["version"] == "person-v2"
    assert [item["version"] for item in client.get(
        "/api/media/person-profiles/person-a/versions"
    ).json()["items"]] == ["person-v2", "person-v1"]
    assert client.get("/api/media/voice-profiles").json()["items"][0]["id"] == "voice-a"
    assert client.get(
        "/api/media/voice-profiles/voice-a/versions"
    ).json()["total_count"] == 1
    assert client.get(
        "/api/media/authorizations/person-authorization/versions"
    ).json()["total_count"] == 1
    assert client.get("/api/media/pronunciation-entries").json()["items"][0]["id"] == "term-codex"
    assert client.get(
        "/api/media/pronunciation-entries/term-codex/versions"
    ).json()["total_count"] == 1

    revoked = client.post(
        "/api/media/authorizations",
        json=_authorization_payload(
            "person-authorization", version="v2", status="revoked"
        ),
    )
    historical = client.get("/api/media/person-profiles/person-a?version=person-v1")

    assert revoked.status_code == 200
    assert historical.status_code == 200
    assert historical.json()["authorization"]["status"] == "active"
    assert historical.json()["current_authorization"]["status"] == "revoked"
    assert historical.json()["runnable"] is False
    assert historical.json()["runnable_reason"] == "参考使用受限：authorization_revoked"
    assert client.get(
        "/api/media/person-profiles/person-a", headers={"x-test-workspace": "workspace-b"}
    ).status_code == 404
    assert client.get(
        "/api/media/voice-profiles/voice-a?version=missing"
    ).status_code == 404
    assert client.get("/api/media/authorizations/missing").status_code == 404


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/media/authorizations",
            {
                **_authorization_payload("authorization-a"),
                "rights_holder": "   ",
            },
        ),
        (
            "/api/media/person-profiles",
            {
                **_person_payload(),
                "display_name": "   ",
            },
        ),
        (
            "/api/media/pronunciation-entries",
            {
                "id": "term-a",
                "term": "   ",
                "language": "en",
                "target_pronunciation": "A",
                "scope": "workspace",
                "scope_reference": None,
                "version": "v1",
            },
        ),
        (
            "/api/media/voice-profiles",
            {
                "id": "voice-a",
                "person_profile_id": "person-a",
                "person_version": "person-v1",
                "version": "voice-v1",
                "language": "zh",
                "display_name": "   ",
                "reference_audio_path": "__VOICE_REFERENCE__",
                "reference": {"asset_id": "__VOICE_ASSET_ID__"},
                "reference_text": "与合成参考一致的文字",
                "model_id": "voice-model-v1",
                "authorization": _authorization_payload(
                    "voice-authorization", content_type="voice", usage_scope="narration"
                ),
            },
        ),
    ],
)
def test_media_management_rejects_blank_required_values(
    request: pytest.FixtureRequest,
    path: str,
    payload: dict[str, object],
) -> None:
    client = request.getfixturevalue(
        "authorization_client" if path == "/api/media/pronunciation-entries" else "client"
    )
    if path == "/api/media/voice-profiles":
        assert client.post("/api/media/person-profiles", json=_person_payload()).status_code == 200
        payload = {
            **payload,
            "reference_audio_path": client.voice_reference,
            "reference": {"asset_id": client.voice_asset_id},
        }
    assert client.post(path, json=payload).status_code == 422

    if path == "/api/media/pronunciation-entries":
        endpoint = "/api/media/pronunciation-entries"
        assert client.get(endpoint).json() == {"items": [], "total_count": 0}
        first_payload = {**payload, "term": "Codex", "target_pronunciation": "KO-deks"}
        first = client.post(endpoint, json=first_payload)
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["workspace_id"] == "workspace-a"
        conflict = client.post(endpoint, json={**first_payload, "target_pronunciation": "changed"})
        assert conflict.status_code == 409
        missing_person = client.post(endpoint, json={**first_payload, "id": "missing-person-entry",
                                                     "scope": "person", "scope_reference": "missing"})
        assert missing_person.status_code == 404
        second = client.post(endpoint, json={**first_payload, "version": "v2", "target_pronunciation": "new"})
        assert second.status_code == 200
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(main_module, "production_repo", ProductionRepository(
                database_path=client.authorization_database, video_library_path=client.authorization_library,
            ))
            entry_id = first_payload["id"]
            assert client.get(f"{endpoint}/{entry_id}").json() == second.json()
            assert client.get(f"{endpoint}/{entry_id}?version=v1").json() == first_body
            assert client.get(f"{endpoint}/{entry_id}/versions").json() == {
                "items": [second.json(), first_body], "total_count": 2,
            }
            assert client.get(endpoint).json() == {"items": [second.json()], "total_count": 1}
            assert client.get(f"{endpoint}/{entry_id}?version=missing").status_code == 404
            assert client.get(f"{endpoint}/missing-person-entry").status_code == 404
            foreign = {"x-test-workspace": "workspace-b"}
            assert client.get(endpoint, headers=foreign).json() == {"items": [], "total_count": 0}
            for suffix in [entry_id, f"{entry_id}?version=v1", f"{entry_id}/versions"]:
                assert client.get(f"{endpoint}/{suffix}", headers=foreign).status_code == 404
            saved_foreign = client.post(endpoint, headers=foreign,
                                        json={**first_payload, "target_pronunciation": "foreign"})
            assert saved_foreign.status_code == 200
            assert client.get(f"{endpoint}/{entry_id}?version=v1").json() == first_body


def test_media_management_rejects_semantic_change_without_new_version_and_reports_capabilities(
    client: TestClient,
) -> None:
    assert client.post("/api/media/person-profiles", json=_person_payload()).status_code == 200
    conflict = client.post(
        "/api/media/person-profiles",
        json={
            **_person_payload(),
            "display_name": "主播 A 修订",
        },
    )
    voice_capability = client.get("/api/media/capabilities/voice-clone")
    avatar_capability = client.get("/api/media/capabilities/avatar-lipsync")

    assert conflict.status_code == 409
    assert voice_capability.status_code == 501
    assert voice_capability.json()["detail"] == {
        "error": "capability_unavailable",
        "capability_id": "voice_clone",
        "detail": "本地声音复制运行环境尚未配置。",
        "impact": "声音复制未执行，不能产出或返回配音媒体。",
        "repair_action": "安装并配置本地声音复制运行环境后重新查询。",
        "retryable": False,
    }
    assert avatar_capability.status_code == 501
    assert avatar_capability.json()["detail"]["error"] == "capability_unavailable"
    assert "media_url" not in avatar_capability.json()["detail"]


@pytest.fixture
def authorization_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    database = tmp_path / "authorization.sqlite3"
    library = tmp_path / "metadata-library"
    repository = ProductionRepository(database_path=database, video_library_path=library)
    monkeypatch.setattr(main_module, "production_repo", repository)
    monkeypatch.setattr(main_module, "current_workspace_id",
                        lambda request: request.headers.get("x-test-workspace", "workspace-a"))
    client = TestClient(main_module.app)
    client.authorization_database = database
    client.authorization_library = library
    return client


def test_media_requests_reject_forged_workspace(authorization_client: TestClient, monkeypatch) -> None:
    client = authorization_client
    response = client.post(
        "/api/media/authorizations",
        json={
            **_authorization_payload("authorization-a"),
            "workspace_id": "forged-workspace",
        },
    )

    assert response.status_code == 422


    assert client.get("/api/media/authorizations").json() == {"items": [], "total_count": 0}
    payload = _authorization_payload("authorization-a")
    first = client.post("/api/media/authorizations", json=payload)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["workspace_id"] == "workspace-a"
    conflict = client.post("/api/media/authorizations", json={**payload, "rights_holder": "其他人"})
    assert conflict.status_code == 409
    invalid = client.post("/api/media/authorizations", json={**payload, "rights_holder": "   "})
    assert invalid.status_code == 422
    assert client.get("/api/media/authorizations/authorization-a?version=v1").json() == first_body
    revoked = client.post("/api/media/authorizations", json={**payload, "version": "v2", "status": "revoked"})
    assert revoked.status_code == 200
    monkeypatch.setattr(main_module, "production_repo", ProductionRepository(
        database_path=client.authorization_database, video_library_path=client.authorization_library,
    ))
    assert client.get("/api/media/authorizations/authorization-a").json() == revoked.json()
    assert client.get("/api/media/authorizations/authorization-a?version=v1").json() == first_body
    history = client.get("/api/media/authorizations/authorization-a/versions").json()
    assert history == {"items": [revoked.json(), first_body], "total_count": 2}
    assert client.get("/api/media/authorizations").json() == {"items": [revoked.json()], "total_count": 1}
    foreign = {"x-test-workspace": "workspace-b"}
    assert client.get("/api/media/authorizations", headers=foreign).json() == {"items": [], "total_count": 0}
    for path in ["authorization-a", "authorization-a/versions", "authorization-a?version=v1", "missing"]:
        assert client.get("/api/media/authorizations/" + path, headers=foreign).status_code == 404
    assert client.get("/api/media/authorizations/authorization-a?version=missing").status_code == 404
    foreign_save = client.post("/api/media/authorizations", headers=foreign,
                               json={**payload, "rights_holder": "其他空间"})
    assert foreign_save.status_code == 200
    assert client.get("/api/media/authorizations/authorization-a?version=v1").json() == first_body


@pytest.mark.parametrize(
    "reference",
    [
        "/tmp/voice.wav",
        "file:///tmp/voice.wav",
        "http://example.com/voice.wav",
        "https://example.com/voice.wav",
        "workspace://approved\\voice.wav",
        "workspace://approved/./voice.wav",
        "workspace://approved/../voice.wav",
        "workspace:///approved/voice.wav",
    ],
)
def test_voice_write_endpoints_reject_nonlogical_or_escaping_references(
    client: TestClient,
    reference: str,
) -> None:
    assert client.post("/api/media/person-profiles", json=_person_payload()).status_code == 200
    versioned = client.post(
        "/api/media/voice-profiles",
        json={
            "id": "voice-a",
            "person_profile_id": "person-a",
            "person_version": "person-v1",
            "version": "voice-v1",
            "language": "zh",
            "display_name": "声音 A",
            "reference_audio_path": reference,
            "reference": {"asset_id": client.voice_asset_id},
            "reference_text": "参考文字",
            "model_id": "voice-model-v1",
            "authorization": _authorization_payload(
                "voice-authorization", content_type="voice", usage_scope="narration"
            ),
        },
    )
    legacy = client.post(
        "/api/production/voices",
        json={
            "name": "legacy",
            "reference_audio_path": reference,
            "reference_text": "参考文字",
            "consent_confirmed": True,
        },
    )

    assert versioned.status_code == 422
    assert legacy.status_code == 422


@pytest.mark.parametrize(
    "reference",
    [
        "/tmp/avatar.png",
        "file:///tmp/avatar.png",
        "https://example.com/avatar.png",
        "workspace://approved/../avatar.png",
        "asset://avatar\\person-a.png",
    ],
)
def test_person_write_endpoint_rejects_nonlogical_or_escaping_avatar_references(
    client: TestClient,
    reference: str,
) -> None:
    response = client.post(
        "/api/media/person-profiles",
        json={
            **_person_payload(),
            "avatar_reference": reference,
        },
    )

    assert response.status_code == 422


def test_production_profile_rejects_absolute_voice_reference(client: TestClient) -> None:
    response = client.put(
        "/api/production/profile",
        json={
            "workspace_name": "Studio",
            "brand_name": "Brand",
            "brand_template_name": "Vertical",
            "voice_reference_path": "/tmp/voice.wav",
        },
    )

    assert response.status_code == 422
