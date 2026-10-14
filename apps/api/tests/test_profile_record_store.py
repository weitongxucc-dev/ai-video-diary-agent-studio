"""Person/voice record persistence inside the existing parent's SQLite transaction."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.profiles import records, versioned_payloads
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_payload
from app.profiles.contracts import Authorization, PersonProfile, ProfileReference, VoiceProfile
from app.profiles.records import ProfileRecordStore


WORKSPACE = "workspace-a"
WHEN = datetime(2026, 10, 14, tzinfo=timezone.utc)
TABLES = ("media_authorizations", "person_profiles", "versioned_voice_profiles", "workspace_media_references")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "profile-records.sqlite3"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    with connect() as conn:
        for table in TABLES[:3]:
            conn.execute(f"""
                CREATE TABLE {table} (
                    workspace_id TEXT NOT NULL, id TEXT NOT NULL, version TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, id, version)
                )
            """)
        conn.execute("""
            CREATE TABLE workspace_media_references (
                reference TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
    return connect


def authorization(**changes):
    return replace(Authorization(
        id="grant", version="v1", rights_holder="原登记人", content_types=("voice", "avatar"),
        usage_scopes=("preview", "publish"), effective_from=WHEN - timedelta(days=1),
        expires_at=None, evidence_reference="fixture://proof", status="active", workspace_id=WORKSPACE,
    ), **changes)


def profile(kind, **changes):
    common = dict(
        id="profile", workspace_id=WORKSPACE, version="v1", authorization=authorization(),
        reference=ProfileReference(asset_id="asset", segment_id="segment", start_seconds=0, end_seconds=2),
        owner_user_id="member-a", visibility="shared", use_state="not_for_automation",
    )
    if kind == "person":
        value = PersonProfile(
            **common, display_name="原人物", avatar_reference="asset://asset",
            background_capabilities=("office",), shot_capabilities=(),
        )
    else:
        value = VoiceProfile(
            **common, language="zh", reference_audio_path="workspace://reference.wav",
            reference_text="原参考文字", display_name="原声音", model_id="model",
            person_profile_id="person", person_version="fixed-person-v1",
        )
    return replace(value, **changes)


def methods(store, kind):
    if kind == "person":
        return (store._save_person_profile, store.get_person_profile, store.list_person_profiles,
                store.list_person_profile_versions, store._get_person_profile,
                records._person_profile_payload, "person_profiles")
    return (store._save_versioned_voice_profile, store.get_versioned_voice_profile,
            store.list_versioned_voice_profiles, store.list_versioned_voice_profile_versions,
            store._get_versioned_voice_profile, records._versioned_voice_profile_payload,
            "versioned_voice_profiles")


def snapshot(connect):
    with connect() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in TABLES
        }


def test_import_and_construction_do_not_start_runtime_or_access_database(tmp_path):
    source_root = str(Path(__file__).resolve().parents[1])
    script = f"import sys; sys.path.insert(0, {source_root!r})\n" + textwrap.dedent("""
        import importlib.abc
        import socket
        import sqlite3
        import subprocess

        class RejectExecution(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                blocked = {'app.production', 'app.main', 'app.repository', 'app.asr',
                    'app.avatar_lipsync', 'app.narration', 'app.profile_reference_media',
                    'app.device_runtime', 'app.device_scheduler', 'app.model_runtime'}
                if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
                    raise AssertionError('execution dependency: ' + fullname)

        def forbidden(*args, **kwargs):
            raise AssertionError('runtime work during import/construction')

        sys.meta_path.insert(0, RejectExecution())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        from app.profiles.authorizations import MediaAuthorizationStore
        from app.profiles.records import ProfileRecordStore
        from app.profiles import contracts, records
        assert records.PersonProfile is contracts.PersonProfile
        assert records.VersionedVoiceProfile is contracts.VoiceProfile
        assert records._as_utc is contracts._as_utc
        ProfileRecordStore(forbidden, MediaAuthorizationStore(forbidden))
    """)
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("kind", ["person", "voice"])
def test_roundtrip_fixed_current_versions_immutability_and_workspace_scope(database, monkeypatch, kind):
    times = iter(WHEN + timedelta(seconds=i) for i in range(10))
    monkeypatch.setattr(versioned_payloads, "utc_now", lambda: next(times))
    grants = MediaAuthorizationStore(database)
    store = ProfileRecordStore(database, grants)
    save, get, listing, versions, _get, _payload, _table = methods(store, kind)
    first = profile(kind)
    second = replace(first, version="v2", use_state="excluded")
    foreign = replace(first, workspace_id="workspace-b", authorization=None)
    with database() as conn:
        grants._save_authorization(conn, WORKSPACE, first.authorization)
        save(conn, first)
        save(conn, first)
        save(conn, second)
        save(conn, foreign)
    restarted = ProfileRecordStore(database, grants)
    _save, get, listing, versions, _get, _payload, _table = methods(restarted, kind)
    assert get(WORKSPACE, first.id, "v1") == first
    assert get(WORKSPACE, first.id) == second
    assert listing(WORKSPACE) == [second]
    assert versions(WORKSPACE, first.id) == [second, first]
    assert listing("workspace-b") == [foreign]
    with pytest.raises(KeyError):
        get("workspace-missing", first.id, "v1")
    with pytest.raises(KeyError):
        get(WORKSPACE, first.id, "missing-fixed-version")
    before = snapshot(database)
    with pytest.raises(ValueError, match="语义变更必须使用新版本"):
        with database() as conn:
            save(conn, replace(first, display_name="不能覆盖原版本"))
    assert snapshot(database) == before


def test_supplied_connection_reads_uncommitted_person_voice_and_authorization_then_rolls_back(database):
    def forbidden():
        raise AssertionError("existing-connection operation requested another connection")

    grants = MediaAuthorizationStore(forbidden)
    store = ProfileRecordStore(forbidden, grants)
    person = profile("person", id="person", version="fixed-person-v1")
    voice = profile("voice")
    with database() as conn:
        conn.execute("BEGIN")
        store._require_profile_write_owner(conn, "person_profiles", person, "member-a", False)
        conn.execute("INSERT INTO workspace_media_references VALUES (?, ?, ?)",
                     ("workspace://person", WORKSPACE, WHEN.isoformat()))
        grants._save_authorization(conn, WORKSPACE, person.authorization)
        store._save_person_profile(conn, person)
        assert store._get_person_profile(conn, WORKSPACE, "person", "fixed-person-v1") == person
        with pytest.raises(KeyError):
            store._get_person_profile(conn, WORKSPACE, "person", "wrong-person-version")
        store._require_profile_write_owner(conn, "versioned_voice_profiles", voice, "member-a", False)
        grants._save_authorization(conn, WORKSPACE, voice.authorization)
        conn.execute("INSERT INTO workspace_media_references VALUES (?, ?, ?)",
                     (voice.reference_audio_path, WORKSPACE, WHEN.isoformat()))
        store._save_versioned_voice_profile(conn, voice)
        assert store._get_versioned_voice_profile(conn, WORKSPACE, voice.id, voice.version) == voice
        assert store._retain_profile_authorization(conn, "person_profiles", person) is person
        assert conn.in_transaction
        with pytest.raises(KeyError):
            ProfileRecordStore(database, MediaAuthorizationStore(database)).get_person_profile(
                WORKSPACE, "person", "fixed-person-v1",
            )
        conn.rollback()
    assert all(not rows for rows in snapshot(database).values())


@pytest.mark.parametrize("kind", ["person", "voice"])
def test_actual_record_write_failure_rolls_back_parent_media_and_authorization_writes(database, kind):
    grants = MediaAuthorizationStore(database)
    store = ProfileRecordStore(database, grants)
    save, _get, _listing, _versions, _connected, _payload, table = methods(store, kind)
    value = profile(kind)
    with database() as conn:
        conn.execute(f"""
            CREATE TRIGGER reject_record BEFORE INSERT ON {table}
            BEGIN SELECT RAISE(ABORT, 'profile-record-write-rejected'); END
        """)
    before = snapshot(database)
    with pytest.raises(sqlite3.IntegrityError, match="profile-record-write-rejected"):
        with database() as conn:
            conn.execute("INSERT INTO workspace_media_references VALUES (?, ?, ?)",
                         ("workspace://reference", WORKSPACE, WHEN.isoformat()))
            grants._save_authorization(conn, WORKSPACE, value.authorization)
            save(conn, value)
    assert snapshot(database) == before


@pytest.mark.parametrize("kind", ["person", "voice"])
def test_fixed_authorization_missing_incomplete_or_absent_preserves_original_decode_result(database, kind):
    grants = MediaAuthorizationStore(database)
    store = ProfileRecordStore(database, grants)
    _save, get, _listing, _versions, _connected, payload_of, table = methods(store, kind)
    grants.save_authorization(authorization(version="newest"))
    cases = [
        ("missing", "missing-version", "historical_authorization_reference_missing"),
        ("incomplete", None, "historical_authorization_reference_incomplete"),
        ("absent", None, None),
    ]
    for record_id, authorization_version, issue in cases:
        payload = payload_of(profile(kind, id=record_id))
        payload["authorization_version"] = authorization_version
        if record_id == "absent":
            payload["authorization_id"] = None
        with database() as conn:
            versioned_payloads._save_versioned_payload(
                conn, table=table, workspace_id=WORKSPACE, record_id=record_id, version="v1", payload=payload,
            )
        before = snapshot(database)
        decoded = get(WORKSPACE, record_id, "v1")
        assert decoded.authorization is None
        assert decoded.reference_issue == issue
        assert decoded.owner_user_id == "member-a"
        assert snapshot(database) == before


@pytest.mark.parametrize("kind", ["person", "voice"])
def test_historical_authorization_and_legacy_missing_fields_are_decoded_without_upgrade_or_write(database, monkeypatch, kind):
    monkeypatch.setattr(records, "utc_now", lambda: WHEN)
    grants = MediaAuthorizationStore(database)
    store = ProfileRecordStore(database, grants)
    _save, get, _listing, _versions, _connected, payload_of, table = methods(store, kind)
    for number, changes in enumerate([
        {"status": "revoked"}, {"status": "expired"}, {"expires_at": WHEN - timedelta(seconds=1)},
    ]):
        original = authorization(id=f"grant-{number}", **changes)
        grants.save_authorization(original)
        grants.save_authorization(replace(original, version="v2", status="active", expires_at=None))
        payload = payload_of(profile(kind, id=f"profile-{number}", authorization=original))
        for field in ["reference", "owner_user_id", "visibility", "use_state"]:
            payload.pop(field)
        with database() as conn:
            versioned_payloads._save_versioned_payload(
                conn, table=table, workspace_id=WORKSPACE, record_id=payload["id"], version="v1", payload=payload,
            )
        before = snapshot(database)
        decoded = get(WORKSPACE, payload["id"], "v1")
        assert decoded.authorization == original
        assert decoded.reference is None
        assert decoded.owner_user_id is None
        assert decoded.visibility == "private"
        assert decoded.use_state == "excluded"
        assert snapshot(database) == before


@pytest.mark.parametrize("kind", ["person", "voice"])
def test_original_owner_rule_and_legacy_embedded_authorization_retention(database, kind):
    grants = MediaAuthorizationStore(database)
    store = ProfileRecordStore(database, grants)
    save, _get, _listing, _versions, _connected, payload_of, table = methods(store, kind)
    original = profile(kind)
    later_authorization = authorization(version="v2", status="revoked")
    grants.save_authorization(original.authorization)
    grants.save_authorization(later_authorization)
    with database() as conn:
        save(conn, original)
        store._require_profile_write_owner(conn, table, original, "member-a", False)
        with pytest.raises(PermissionError, match="只有档案原登记成员"):
            store._require_profile_write_owner(conn, table, original, "member-b", False)
        store._require_profile_write_owner(conn, table, original, "member-b", True)
        incoming = replace(original, version="v3", authorization=None)
        # Current payload shape has only ID/version: retain does not invent a grant.
        assert store._retain_profile_authorization(conn, table, incoming) is incoming
        payload = payload_of(original)
        payload["authorization"] = _authorization_payload(original.authorization)
        conn.execute(f"UPDATE {table} SET payload=? WHERE id=?", (json.dumps(payload), original.id))
        retained = store._retain_profile_authorization(conn, table, incoming)
        assert retained.authorization == later_authorization
        assert store._retain_profile_authorization(conn, table, original) is original
        payload["owner_user_id"] = None
        conn.execute(f"UPDATE {table} SET payload=? WHERE id=?", (json.dumps(payload), original.id))
        with pytest.raises(PermissionError, match="历史归属缺失"):
            store._require_profile_write_owner(conn, table, original, "member-a", False)
