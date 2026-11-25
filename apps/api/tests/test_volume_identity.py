"""Recorded external-volume identity fails closed on a missing or replaced disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config as config_module
from app import volume_identity
from app.config import Settings, StorageConfigurationError, validate_storage_configuration
from app.volume_identity import (
    VolumeIdentity,
    VolumeIdentityError,
    load_volume_identity_record,
    record_path_for_env,
    save_volume_identity_record,
    verify_recorded_volumes,
)


def _identity(uuid: str = "uuid-original") -> VolumeIdentity:
    return VolumeIdentity(
        mount_point="/Volumes/dataset", uuid=uuid,
        volume_name="dataset", filesystem="apfs",
    )


def test_identity_record_is_written_beside_the_dotenv_and_merges_volumes(tmp_path):
    env_path = tmp_path / "config" / ".env"
    record_path = record_path_for_env(env_path)
    save_volume_identity_record(record_path, [_identity()])
    save_volume_identity_record(record_path, [
        VolumeIdentity(mount_point="/Volumes/models", uuid="uuid-models"),
    ])

    record = load_volume_identity_record(record_path)
    assert record["/Volumes/dataset"]["uuid"] == "uuid-original"
    assert record["/Volumes/models"]["uuid"] == "uuid-models"


def test_recorded_volume_with_a_different_uuid_fails_closed(tmp_path, monkeypatch):
    record_path = tmp_path / "record.json"
    save_volume_identity_record(record_path, [_identity()])
    monkeypatch.setattr(Path, "is_mount", lambda _self: True)

    with pytest.raises(VolumeIdentityError, match="身份不匹配"):
        verify_recorded_volumes(
            [Path("/Volumes/dataset")], record_path=record_path,
            probe=lambda _path: _identity("uuid-replacement"),
        )


def test_recorded_volume_that_is_not_mounted_fails_closed(tmp_path, monkeypatch):
    record_path = tmp_path / "record.json"
    save_volume_identity_record(record_path, [_identity()])
    monkeypatch.setattr(Path, "is_mount", lambda _self: False)

    with pytest.raises(VolumeIdentityError, match="未挂载"):
        verify_recorded_volumes(
            [Path("/Volumes/dataset")], record_path=record_path,
            probe=lambda _path: _identity(),
        )


def test_unreadable_identity_is_a_failure_not_a_skip(tmp_path, monkeypatch):
    record_path = tmp_path / "record.json"
    save_volume_identity_record(record_path, [_identity()])
    monkeypatch.setattr(Path, "is_mount", lambda _self: True)

    def fail(_path):
        raise VolumeIdentityError("无法读取外接盘身份")

    with pytest.raises(VolumeIdentityError, match="无法读取"):
        verify_recorded_volumes(
            [Path("/Volumes/dataset")], record_path=record_path, probe=fail,
        )


def test_volume_without_a_record_is_not_yet_protected(tmp_path):
    record_path = tmp_path / "record.json"
    assert verify_recorded_volumes(
        [Path("/Volumes/dataset")], record_path=record_path,
        probe=lambda _path: _identity(),
    ) == {}


def test_corrupt_record_fails_closed(tmp_path):
    record_path = tmp_path / "record.json"
    record_path.write_text(json.dumps({"format": "unknown", "volumes": {}}))
    with pytest.raises(VolumeIdentityError, match="格式无效"):
        load_volume_identity_record(record_path)


def test_storage_validation_surfaces_a_recorded_volume_mismatch(tmp_path, monkeypatch):
    settings = Settings(
        database_path=str(tmp_path / "agent.sqlite3"),
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        temporary_root_path=str(tmp_path / "temporary"),
    )
    monkeypatch.setattr(
        config_module, "_configured_external_volumes", lambda _settings: [Path("/Volumes/dataset")],
    )

    def reject(_volumes, *, record_path, probe=None):
        raise volume_identity.VolumeIdentityError("外接盘身份不匹配：/Volumes/dataset")

    monkeypatch.setattr(config_module.volume_identity, "verify_recorded_volumes", reject)

    with pytest.raises(StorageConfigurationError, match="身份不匹配"):
        validate_storage_configuration(settings)


def test_configured_external_volumes_are_deduplicated_and_include_derived_temp(tmp_path):
    settings = Settings(
        database_path="/Volumes/dataset/agent.sqlite3",
        video_inbox_path="/Volumes/dataset/inbox",
        video_output_path="/Volumes/dataset/output",
        draft_output_path="/Volumes/dataset/drafts",
        model_root_path="/Volumes/dataset/models",
        temporary_root_path=None,
    )
    assert config_module._configured_external_volumes(settings) == [Path("/Volumes/dataset")]
