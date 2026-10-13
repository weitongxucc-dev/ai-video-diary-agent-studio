"""Profile values and pure rules must load without model, media or repository code."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app.profiles import contracts


def _authorization(**changes):
    value = contracts.Authorization(
        id="consent", version="1", rights_holder="本人", content_types=("voice",),
        usage_scopes=("narration",), effective_from=datetime(2020, 1, 1),
        expires_at=None, evidence_reference="fixture://consent", status="active",
        workspace_id="workspace-a",
    )
    return replace(value, **changes)


def _voice(**changes):
    value = contracts.VoiceProfile(
        id="voice-a", workspace_id="workspace-a", version="1", language="zh",
        reference_audio_path="workspace://fixture.wav", reference_text="测试原文",
        authorization=_authorization(), reference=contracts.ProfileReference(asset_id="asset-a"),
        owner_user_id="uploader-a",
    )
    return replace(value, **changes)


def test_original_avatar_exports_share_the_same_contract_objects():
    from app import avatar_lipsync

    for name in (
        "AuthorizationStatus", "Authorization", "ProfileReference", "PersonProfile",
        "VoiceProfile", "PronunciationEntry", "profile_use_failure", "profile_cache_identity",
        "_as_utc", "_authorization_cache_identity",
    ):
        assert getattr(avatar_lipsync, name) is getattr(contracts, name)


def test_profile_contracts_import_without_application_runtime_or_side_effects():
    script = textwrap.dedent("""
        import importlib.abc
        from pathlib import Path
        import sys

        class RejectRuntime(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.startswith('app.') and fullname not in {'app.profiles', 'app.profiles.contracts'}:
                    raise AssertionError(f'Contract imported runtime module: {fullname}')

        def reject_side_effects(event, args):
            if event in {'sqlite3.connect', 'subprocess.Popen', 'socket.connect'}:
                raise AssertionError(f'Contract started runtime side effect: {event}')

        sys.meta_path.insert(0, RejectRuntime())
        sys.addaudithook(reject_side_effects)
        Path.rglob = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Vault scan forbidden'))
        from app.profiles.contracts import PersonProfile, ProfileReference, VoiceProfile, profile_use_failure
        person = PersonProfile('person', 'workspace-a', '本人', '1', 'workspace://person.png', (), ())
        voice = VoiceProfile('voice', 'workspace-a', '1', 'zh', 'workspace://voice.wav', '测试原文')
        assert profile_use_failure(person, content_type='person_likeness', usage_scope='avatar_lipsync') is None
        assert profile_use_failure(voice, content_type='voice', usage_scope='narration') is None
        assert ProfileReference().asset_id is None
    """)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_contract_dates_keep_naive_utc_and_offset_conversion():
    naive = datetime(2020, 1, 1)
    offset = timezone(timedelta(hours=8))
    assert contracts._as_utc(naive) == naive.replace(tzinfo=timezone.utc)
    assert contracts._as_utc(datetime(2020, 1, 1, 8, tzinfo=offset)) == naive.replace(tzinfo=timezone.utc)
    assert _authorization().failure_for(
        content_type="voice", usage_scope="narration", at=datetime(2026, 10, 14),
    ) is None
    assert _authorization(expires_at=datetime(2026, 10, 14)).failure_for(
        content_type="voice", usage_scope="narration", at=datetime(2026, 10, 14),
    ) == "authorization_expired"


def test_profile_reference_policy_and_strict_grant_policy_keep_their_existing_difference():
    pending = _authorization(status="pending", content_types=(), usage_scopes=())
    assert pending.failure_for(content_type="voice", usage_scope="narration") == "authorization_pending"
    # Local references retain the existing permissive optional-proof contract.
    assert contracts.profile_use_failure(
        _voice(authorization=pending), content_type="voice", usage_scope="narration",
    ) is None


@pytest.mark.parametrize(("changes", "failure"), [
    ({"use_state": "excluded"}, "profile_excluded"),
    ({"reference_issue": "source_missing"}, "source_missing"),
    ({"authorization": _authorization(status="revoked")}, "authorization_revoked"),
    ({"authorization": _authorization(workspace_id="workspace-b")}, "authorization_workspace_mismatch"),
    ({"authorization": _authorization(expires_at=datetime(2020, 1, 2))}, "authorization_expired"),
])
def test_profile_restrictions_and_original_authorization_remain_binding(changes, failure):
    assert contracts.profile_use_failure(
        _voice(**changes), content_type="voice", usage_scope="narration",
    ) == failure


def test_cache_and_pronunciation_identity_keep_exact_saved_values():
    voice = _voice()
    assert contracts.profile_cache_identity(voice) == {
        "id": "voice-a", "version": "1", "reference": asdict(voice.reference),
        "use_state": "available",
        "authorization": {"id": "consent", "version": "1", "status": "active", "expires_at": None},
    }
    assert contracts.profile_cache_identity(_voice(version="2")) != contracts.profile_cache_identity(voice)
    entry = contracts.PronunciationEntry(
        id="term", workspace_id="workspace-a", term="曜核", language="zh",
        target_pronunciation="耀核", scope="person", scope_reference="person-a", version="1",
    )
    assert entry.applies_to(workspace_id="workspace-a", person_profile_id="person-a", task_id="task")
    assert not entry.applies_to(workspace_id="workspace-b", person_profile_id="person-a", task_id="task")
    assert entry.task_input() == {
        "id": "term", "version": "1", "term": "曜核", "language": "zh",
        "target_pronunciation": "耀核", "scope": "person", "scope_reference": "person-a",
    }
