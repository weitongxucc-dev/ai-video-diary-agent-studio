"""One native SQLite/public workspace-profile storage difference."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app import production
from app.profiles import workspace_profile
from app.persistence_json import _json_payload


WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)


def snapshot(repo):
    with repo._connect() as conn:
        return [tuple(row) for row in conn.execute("SELECT * FROM production_profiles ORDER BY workspace_id")]


def test_workspace_profile_public_store_keeps_unsaved_defaults_roundtrip_and_late_failure(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("store construction must not request a connection")

    workspace_profile.WorkspaceProductionProfileStore(forbidden)
    assert production.ProductionProfile is workspace_profile.ProductionProfile
    repo = production.ProductionRepository(tmp_path / "profiles.sqlite3", tmp_path / "library")
    store = repo._workspace_profiles
    assert isinstance(store, workspace_profile.WorkspaceProductionProfileStore)
    assert store._connect.__self__ is repo
    original_factory = store._connect
    traces, opened = [], []

    def connect():
        conn = original_factory()
        conn.set_trace_callback(traces.append)
        opened.append(conn)
        return conn

    store._connect = connect
    missing = repo.get_profile("workspace-a")
    another_missing = repo.get_profile("workspace-a")
    assert missing is not another_missing
    assert missing.workspace_id == "workspace-a" and missing.workspace_name == "AI 视频日记"
    assert missing.default_voice_profile_id is None and missing.default_voice_version is None
    missing.brand_colors.append("not persisted")
    assert another_missing.brand_colors == []
    assert snapshot(repo) == []
    assert not any(sql.lstrip().startswith("INSERT") for sql in traces)

    clock = {"now": WHEN}
    monkeypatch.setattr(workspace_profile, "utc_now", lambda: clock["now"])
    profile = production.ProductionProfile(
        workspace_id="workspace-a", workspace_name="合成工作区", brand_name="样例品牌",
        primary_platform="bilibili", aspect_ratio="16:9",
        target_duration_min_seconds=15, target_duration_max_seconds=120,
        brand_template_name="synthetic-template", brand_colors=["#123456", "#ffffff"],
        subtitle_style="样例字幕", intro_text="片头", outro_text="片尾",
        background_music_path="workspace://synthetic-music.wav",
        default_voice_profile_id="fixed-voice", default_voice_version="v3",
        voice_name="旧 metadata", voice_reference_path="workspace://synthetic-voice.wav",
        voice_reference_text="仅元数据", voice_consent_confirmed=False,
        voice_model_status="blocked", voice_model_message="未验证模型",
        source_urls=["https://fixture.invalid/source"], use_obsidian=False, use_hermes=False,
        factuality_gate_enabled=False, cloud_enhancement_enabled=True,
        updated_at=WHEN - timedelta(days=1),
    )
    assert repo.save_profile(profile) is profile and profile.updated_at == WHEN
    assert asdict(repo.get_profile("workspace-a")) == asdict(profile)
    with repo._connect() as conn:
        row = conn.execute("SELECT * FROM production_profiles WHERE workspace_id=?", (profile.workspace_id,)).fetchone()
        assert dict(row) == {"workspace_id": profile.workspace_id, "payload": _json_payload(profile), "updated_at": WHEN.isoformat()}

    clock["now"] = WHEN + timedelta(seconds=1)
    foreign = production.ProductionProfile("workspace-b", brand_name="other", updated_at=WHEN)
    repo.save_profile(foreign)
    before_update = snapshot(repo)
    clock["now"] = WHEN + timedelta(seconds=2)
    profile.brand_name = "更新品牌"
    profile.target_duration_max_seconds = 127
    profile.source_urls.append("https://fixture.invalid/second")
    repo.save_profile(profile)
    assert asdict(repo.get_profile("workspace-a")) == asdict(profile)
    assert asdict(repo.get_profile("workspace-b")) == asdict(foreign)
    assert repo.get_profile("not-persisted").workspace_id == "not-persisted"
    after_update = snapshot(repo)
    assert len(after_update) == 2 and after_update[1] == before_update[1]
    assert profile.default_voice_profile_id == "fixed-voice" and profile.default_voice_version == "v3"

    with repo._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_late_workspace_profile
            AFTER UPDATE ON production_profiles WHEN NEW.workspace_id = 'workspace-a'
            BEGIN SELECT RAISE(ABORT, 'synthetic late workspace UPSERT refusal'); END""")
    before_failure = snapshot(repo)
    clock["now"] = WHEN + timedelta(seconds=3)
    profile.brand_name = "不会提交"
    traces.clear()
    opened_before = len(opened)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic late workspace UPSERT refusal"):
        repo.save_profile(profile)
    assert len(opened) == opened_before + 1
    assert "BEGIN IMMEDIATE" not in traces and traces[-1] == "ROLLBACK"
    assert any(sql.lstrip().startswith("INSERT INTO production_profiles") for sql in traces)
    assert snapshot(repo) == before_failure
    assert profile.updated_at == clock["now"]  # Original caller mutation survives the failed persistence.
