"""读取权限的 HTTP 真实入口：跨身份、撤销共享与无效令牌。

这些用例只证明「读到的内容受同一判据约束」：owner 读不到别人的私有内容、
显式收回后所有读路径立即失效、无效成员令牌不会被当成「没带令牌所以按 owner
看全部」。这仍不是传输层隔离，也不冒充认证体系已完备。

全部使用 tmp_path 合成库与真实成员令牌，不连接真实用户库、不启动认证后端。
"""
from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

import app.main as main_module
from app.local_members import LocalMemberService
from app.source_repository import SourceRepository

client = TestClient(main_module.app)
WORKSPACE = "ws-110-http"


def _setup(tmp_path, monkeypatch):
    service = LocalMemberService(tmp_path / "agent.sqlite3")
    monkeypatch.setattr(main_module, "local_members_service", service)
    monkeypatch.setattr(
        main_module, "source_repository", SourceRepository(tmp_path / "sources.sqlite3")
    )
    monkeypatch.setattr(
        main_module, "current_workspace_id",
        lambda request: request.headers.get("x-test-workspace", WORKSPACE),
    )
    # 所有者用真实成员令牌：空字符串只是匿名本机诊断，不能拿来当 owner 身份。
    owner, owner_token = service.join(service.create_invite_code(), "所有者")
    service.set_role(owner.member_id, "owner")
    member, member_token = service.join(service.create_invite_code(), "小王")
    return {
        "owner_token": owner_token,
        "owner_member": owner,
        "member_token": member_token,
        "member": member,
    }


def _headers(token: str) -> dict[str, str]:
    return {"X-Local-Member-Token": token}


def _save_private_knowledge(title: str, token: str) -> dict:
    """以该令牌的真实身份保存一条私有资料，归属绑定到这次身份。"""
    headers = _headers(token)
    source = client.post(
        "/api/source-items/manual",
        json={"title": f"{title}来源", "content_type": "text", "body": "正文", "license": "owned"},
        headers=headers,
    )
    assert source.status_code == 200, source.text
    saved = client.post(
        f"/api/source-items/{source.json()['id']}/save-to-knowledge",
        json={},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_owner_token_does_not_grant_another_members_private_content(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    owner_saved = _save_private_knowledge("所有者私有", party["owner_token"])
    member_saved = _save_private_knowledge("小王的私有", party["member_token"])
    assert owner_saved["visibility"] == "private"
    assert member_saved["visibility"] == "private"

    # 上传者本人读得到自己刚存的私有内容。
    assert client.get(
        f"/api/knowledge-items/{member_saved['id']}", headers=_headers(party["member_token"])
    ).status_code == 200
    assert client.get(
        f"/api/knowledge-items/{owner_saved['id']}", headers=_headers(party["owner_token"])
    ).status_code == 200

    # owner 身份读不到别人的私有内容：读权限只跟上传者本人绑定。
    owner_denied = client.get(
        f"/api/knowledge-items/{member_saved['id']}", headers=_headers(party["owner_token"])
    )
    assert owner_denied.status_code == 404

    # 成员之间也互相读不到，按不存在处理而不是 403。
    denied = client.get(
        f"/api/knowledge-items/{owner_saved['id']}", headers=_headers(party["member_token"])
    )
    assert denied.status_code == 404

    # 列表里同样不出现别人的私有条目。
    listed = client.get("/api/knowledge-items", headers=_headers(party["member_token"]))
    assert listed.status_code == 200
    member_ids = {item["id"] for item in listed.json()["items"]}
    assert member_saved["id"] in member_ids
    assert owner_saved["id"] not in member_ids


def test_shared_becomes_readable_and_revoke_takes_effect_immediately(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("共享后可见", party["owner_token"])

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "shared"},
        headers=_headers(party["owner_token"]),
    )
    readable = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert readable.status_code == 200

    client.put(
        f"/api/knowledge-items/{saved['id']}/visibility",
        json={"visibility": "private"},
        headers=_headers(party["owner_token"]),
    )
    revoked = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert revoked.status_code == 404
    listed = client.get("/api/knowledge-items", headers=_headers(party["member_token"]))
    assert all(item["id"] != saved["id"] for item in listed.json()["items"])


def test_invalid_member_token_is_rejected_and_never_treated_as_owner(tmp_path, monkeypatch):
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("私有内容", party["owner_token"])

    denied = client.get(
        f"/api/knowledge-items/{saved['id']}",
        headers=_headers("not-a-real-member-token"),
    )

    assert denied.status_code == 401
    listed = client.get("/api/knowledge-items", headers=_headers("not-a-real-member-token"))
    assert listed.status_code == 401


def test_cited_version_facts_are_not_returned_to_a_reader_without_permission(tmp_path, monkeypatch):
    """引用回查不得把无权读取的私有快照的来源链接与指纹带出去。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("被引用的私有资料", party["owner_token"])
    item_id, version = saved["id"], saved["version"]
    item = main_module.source_repository.get_knowledge_item(WORKSPACE, item_id)

    from types import SimpleNamespace

    def reader(token: str):
        return SimpleNamespace(
            state=SimpleNamespace(principal=None),
            headers={"X-Local-Member-Token": token},
        )

    member_request = reader(party["member_token"])

    # 归属是别人时，带令牌的成员读不到。
    assert main_module._content_identity_http_adapter().visible(
        member_request, WORKSPACE, "private", "somebody-else"
    ) is False

    # 这条以所有者身份保存，归属就是所有者本人：本人读得到，别人读不到。
    assert item.owner_user_id == party["owner_member"].member_id
    assert main_module._content_identity_http_adapter().visible(
        member_request, WORKSPACE, "private", item.owner_user_id
    ) is False
    assert main_module._content_identity_http_adapter().visible(
        reader(party["owner_token"]), WORKSPACE, "private", item.owner_user_id
    ) is True

    # 无权读取的一版不给出任何快照事实：来源链接、抓取时刻和指纹都为空。
    denied = main_module.ScriptLibrarySourceResponse(
        reference_id=f"knowledge:{item_id}:{version}", status="missing"
    )
    assert denied.status != "readable"
    assert not denied.cited_source_url
    assert not denied.cited_content_hash
    assert not denied.cited_fetched_at


@pytest.fixture(autouse=True)
def material_insights_metadata_guard(request, monkeypatch, tmp_path):
    if request.node.originalname != 'test_material_insights_do_not_count_another_members_private_content':
        return None
    import sqlite3
    import subprocess
    import wave
    from types import SimpleNamespace
    from PIL import Image
    from app.artifact_store import ArtifactStore
    from app.auth import SupabaseAuthContext
    from app.config import Settings
    from app.device_runtime import DeviceRuntime
    from app.device_scheduler import DeviceScheduler
    from app.montage import MontageRepository

    violations = []

    def forbidden(*args, **kwargs):
        violations.append('material-insights-refresh-or-execution')
        pytest.fail('empty-montage metadata forbids refresh factory, runtime, grants, media and resolve')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(ArtifactStore, 'resolve_file', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    monkeypatch.setattr(DeviceScheduler, 'pick_and_grant', forbidden)
    for name in ('_creation_runtime', '_start_device_runtime', 'run_task_turn', 'run_plan_turn',
                 'start_assistant_continuation_driver', '_render_stage_use_case'):
        monkeypatch.setattr(main_module, name, forbidden)
    monkeypatch.setattr(main_module, 'montage_renderer', SimpleNamespace(render=forbidden))
    monkeypatch.setattr(main_module, 'narration_generator', SimpleNamespace(generate_for_creation_task=forbidden))
    settings = Settings(database_path=str(tmp_path / 'studio.sqlite3'),
        video_inbox_path=str(tmp_path / 'library'), video_output_path=str(tmp_path / 'output'),
        model_root_path=str(tmp_path / 'models'), temporary_root_path=str(tmp_path / 'temporary'),
        draft_output_path=str(tmp_path / 'drafts'), jianying_draft_root_path=str(tmp_path / 'jianying'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None, model_runtime_python_path=None,
        native_portrait_worker_path=None, supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main_module, 'settings', settings)
    monkeypatch.setattr(main_module, '_workspace_settings_for', lambda workspace_id: settings)
    monkeypatch.setattr(main_module, 'auth_context', SupabaseAuthContext(settings))
    monkeypatch.setattr(main_module, 'device_runtime', None)
    monkeypatch.setattr(main_module, 'artifact_store', ArtifactStore(tmp_path / 'artifacts'))
    monkeypatch.setattr(main_module, 'montage_repo', MontageRepository(tmp_path / 'montage.sqlite'))

    def snapshot():
        databases = {}
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
                    tables = [row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                    databases[str(path.relative_to(tmp_path))] = {
                        table: sorted((tuple(row) for row in conn.execute(
                            'SELECT * FROM "' + table.replace('"', '""') + '"')), key=repr) for table in tables}
        assert set(databases) == {'agent.sqlite3', 'sources.sqlite3', 'studio.sqlite3', 'montage.sqlite'}
        return databases, {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                           for path in tmp_path.rglob('*')}

    def check_no_execution():
        assert violations == []

    request.addfinalizer(check_no_execution)
    return SimpleNamespace(snapshot=snapshot, violations=violations)


def test_material_insights_do_not_count_another_members_private_content(tmp_path, monkeypatch, material_insights_metadata_guard):
    """统计数字本身不能泄露别人的私有内容：聚合与列表同一判据。"""
    party = _setup(tmp_path, monkeypatch)
    from datetime import datetime

    from app.production import MaterialAsset, ProductionRepository

    library = tmp_path / "library"
    library.mkdir()
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    production._save_asset(MaterialAsset(
        id="private-asset", workspace_id=WORKSPACE, file_name="mine.mp4",
        path=f"{WORKSPACE}/mine.mp4", size_bytes=10, modified_at=datetime(2026, 1, 1),
        owner_user_id=party["owner_member"].member_id,
    ))

    before_metadata = material_insights_metadata_guard.snapshot()
    assert main_module.montage_repo.recent(WORKSPACE, limit=50) == []
    denied = client.get("/api/production/materials/insights", headers=_headers(party["member_token"]))

    assert denied.status_code == 200
    body = denied.text
    assert "mine.mp4" not in body

    assert denied.json()['asset_count'] == 0
    owner = client.get('/api/production/materials/insights', headers=_headers(party['owner_token']))
    assert owner.status_code == 200 and owner.json()['asset_count'] == 1
    assert client.get('/api/production/materials/insights', headers=_headers('invalid-token')).status_code == 401
    assert material_insights_metadata_guard.snapshot() == before_metadata
    with production._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM material_segment_usage').fetchone()[0] == 0
    assert material_insights_metadata_guard.violations == []


def test_anonymous_read_outside_the_reserved_local_workspace_is_not_local_diagnostic(
    tmp_path, monkeypatch
):
    """受控 local 只覆盖保留的 local 工作区；别的 workspace 匿名请求读不到私有内容。"""
    party = _setup(tmp_path, monkeypatch)
    saved = _save_private_knowledge("匿名读不到", party["owner_token"])

    # 带令牌的有效成员在别的 workspace 上按成员隔离（读不到他人私有）。
    denied = client.get(
        f"/api/knowledge-items/{saved['id']}", headers=_headers(party["member_token"])
    )
    assert denied.status_code == 404
