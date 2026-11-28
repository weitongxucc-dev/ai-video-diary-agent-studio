"""Recorded external-volume identity fails closed on a missing or replaced disk."""

from __future__ import annotations

from functools import partial
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


@pytest.mark.parametrize("second_volume", ["matching", "replacement", "missing_unrecorded"])
def test_storage_checks_all_volumes_before_directory_write_probes(tmp_path, monkeypatch, second_volume):
    data, models = tmp_path / "data", tmp_path / "models"
    data.mkdir()
    models.mkdir()
    settings = Settings(
        database_path=str(data / "agent.sqlite3"),
        model_root_path=str(models),
    )
    volumes = [data, models]
    identities = [VolumeIdentity(str(data.resolve()), "data-original")]
    if second_volume != "missing_unrecorded":
        identities.append(VolumeIdentity(str(models.resolve()), "models-original"))
    record = tmp_path / volume_identity.IDENTITY_RECORD_FILENAME
    save_volume_identity_record(record, identities)
    monkeypatch.setattr(config_module, "runtime_env_file_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(
        config_module, "_external_volume",
        lambda path: next((volume for volume in volumes if path.is_relative_to(volume)), None),
    )
    def mounted(path):
        return path == data or path == models and second_volume != "missing_unrecorded"
    monkeypatch.setattr(config_module.os.path, "ismount", mounted)
    monkeypatch.setattr(Path, "is_mount", mounted)
    events = []

    def probe(path):
        events.append(("identity", path))
        uuid = "replacement" if path == models and second_volume == "replacement" else f"{path.name}-original"
        return VolumeIdentity(str(path.resolve()), uuid)

    monkeypatch.setattr(volume_identity, "verify_recorded_volumes", partial(verify_recorded_volumes, probe=probe))
    monkeypatch.setattr(config_module, "_storage_write_probe", lambda directory: events.append(("write", directory)))
    if second_volume == "matching":
        validate_storage_configuration(settings)
        assert events == [("identity", data), ("identity", models), ("write", data), ("write", models)]
    else:
        message = "身份不匹配" if second_volume == "replacement" else "未挂载"
        with pytest.raises(StorageConfigurationError, match=message):
            validate_storage_configuration(settings)
        assert events == [("identity", volume) for volume in volumes[:len(identities)]]


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
