"""Explicit local adoption maintenance with no startup provisioning."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from app.materials.assets import MaterialAssetStore
from app.materials.contracts import MaterialAsset
from app.materials.source_files import MaterialSourceFileAccess
from app.materials.workspace_references import WorkspaceMediaReferenceAccess
from app.models import utc_now
from app.persistence_json import _json_payload
from app.database_migrations import LOCAL_ADOPTION_RECEIPTS_DDL
from app.profiles.authorizations import MediaAuthorizationStore
from app.profiles.contracts import ProfileReference, profile_use_failure
from app.profiles.records import ProfileRecordStore
from app.profiles.reference_access import _profile_wav_duration


class LocalAdoptionMaintenance:
    def __init__(self, database_path: str | Path, media_root: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self._material_source_files = MaterialSourceFileAccess(self._connect, Path(media_root).expanduser())
        self._material_assets = MaterialAssetStore(self._connect)
        self._workspace_media_references = WorkspaceMediaReferenceAccess(
            self._connect, self._material_source_files, self._material_assets)
        self._media_authorizations = MediaAuthorizationStore(self._connect)
        self._profile_records = ProfileRecordStore(self._connect, self._media_authorizations)

    @property
    def video_library_path(self) -> Path:
        return self._material_source_files.video_library_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def plan_local_adoption(
        cls, database_path: str | Path, video_library_path: str | Path, *,
        member_id: str, operation_id: str, asset_ids: list[str],
        voice_id: str, voice_version: str, new_voice_version: str,
        authorization_basis: str,
    ) -> dict:
        """Explicit read-only maintenance plan, with no startup provisioning."""
        repository = _local_adoption_repository(cls, database_path, video_library_path)
        with closing(sqlite3.connect(repository.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            manifest, _ = _local_adoption_plan(repository, conn,
                member_id=member_id, operation_id=operation_id, asset_ids=asset_ids,
                voice_id=voice_id, voice_version=voice_version,
                new_voice_version=new_voice_version, authorization_basis=authorization_basis)
            return manifest

    @classmethod
    def apply_local_adoption(
        cls, database_path: str | Path, video_library_path: str | Path, *,
        member_id: str, manifest: dict, manifest_sha256: str,
    ) -> dict:
        """CAS all selected inputs; receipt and content commit as one transaction."""
        if _local_adoption_digest(manifest) != manifest_sha256:
            raise ValueError("local_adoption_manifest_digest_mismatch")
        if manifest.get("workspace_id") != "local" or manifest.get("member_id") != member_id:
            raise ValueError("local_adoption_target_mismatch")
        repository = _local_adoption_repository(cls, database_path, video_library_path)
        with closing(sqlite3.connect(repository.database_path.resolve().as_uri() + "?mode=rw", uri=True)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            _local_adoption_target(repository, conn, member_id)
            if manifest.get("database_identity") != _local_adoption_database_identity(repository):
                raise ValueError("local_adoption_database_changed")
            if manifest.get("media_root") != str(repository.video_library_path.resolve()):
                raise ValueError("local_adoption_root_changed")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_adoption_receipts'").fetchone()
            if exists:
                receipt = conn.execute("SELECT manifest_sha256, result_json FROM local_adoption_receipts WHERE operation_id=?",
                                       (manifest["operation_id"],)).fetchone()
                if receipt is not None:
                    if receipt["manifest_sha256"] != manifest_sha256:
                        raise ValueError("local_adoption_operation_conflict")
                    return json.loads(receipt["result_json"])
            current, originals = _local_adoption_plan(repository, conn,
                member_id=member_id, operation_id=manifest["operation_id"],
                asset_ids=[item["id"] for item in manifest["assets"]],
                voice_id=manifest["voice"]["id"], voice_version=manifest["voice"]["version"],
                new_voice_version=manifest["voice"]["new_version"],
                authorization_basis=manifest["authorization_basis"])
            if current != manifest:
                raise ValueError("local_adoption_inputs_changed")
            now = utc_now().isoformat()
            conn.execute(LOCAL_ADOPTION_RECEIPTS_DDL)
            after = []
            for row in originals["assets"]:
                payload = json.loads(row["payload"])
                payload.update(owner_user_id=member_id, visibility="private")
                serialized = json.dumps(payload, ensure_ascii=False)
                repository._material_assets.privatize_local_adoption_asset(conn, row["id"], serialized)
                after.append({**row, "payload": serialized, "visibility": "private"})
            for reference in current["references"]:
                if reference["before_sha256"] is None:
                    conn.execute("INSERT INTO workspace_media_references(reference,workspace_id,updated_at) VALUES (?,'local',?)",
                                 (reference["reference"], now))
            source = current["voice"]["source"]
            # The new source is generated from the verified WAV facts, never an external body.
            source_asset = MaterialAsset(id=source["id"], workspace_id="local",
                file_name=Path(originals["voice_path"]).name, path=originals["voice_path"],
                size_bytes=source["file_revision"]["size_bytes"],
                modified_at=datetime.fromtimestamp(source["file_revision"]["mtime_ns"] / 1e9),
                media_type="audio", duration_seconds=source["duration_seconds"],
                owner_user_id=member_id, visibility="private", created_at=utc_now(), updated_at=utc_now())
            source_payload = _json_payload(source_asset)
            repository._material_assets.insert_local_voice_source_asset(conn, source_asset, source_payload, now)
            after.append({"id": source_asset.id, "workspace_id": "local", "file_path": source_asset.path,
                          "payload": source_payload, "visibility": "private", "updated_at": now})
            # Preserve every historical field; v1 stays byte-for-byte in its original row.
            voice_payload = json.loads(originals["voice"]["payload"])
            voice_payload.update(version=current["voice"]["new_version"], owner_user_id=member_id,
                                 visibility="private", reference=asdict(ProfileReference(asset_id=source_asset.id)))
            serialized_voice = json.dumps(voice_payload, ensure_ascii=False)
            conn.execute("INSERT INTO versioned_voice_profiles(workspace_id,id,version,payload,created_at) VALUES ('local',?,?,?,?)",
                         (current["voice"]["id"], current["voice"]["new_version"], serialized_voice, now))
            # Files are outside SQLite. Recheck after all writes, immediately before the commit.
            for item in [*current["assets"], source]:
                path = repository._workspace_media_references.resolve_workspace_media_reference("local", item["reference"])
                if _local_adoption_file_revision(path) != item["file_revision"]:
                    raise ValueError("local_adoption_file_changed")
            result = {"operation_id": current["operation_id"], "workspace_id": "local", "member_id": member_id,
                      "manifest_sha256": manifest_sha256, "asset_ids": [item["id"] for item in current["assets"]],
                      "voice_id": current["voice"]["id"], "voice_version": current["voice"]["new_version"],
                      "voice_source_asset_id": source_asset.id, "applied_at": now}
            private_snapshot = {"before": originals, "after_assets": after,
                                "after_voice_payload": serialized_voice, "approved_manifest": current}
            conn.execute("INSERT INTO local_adoption_receipts VALUES (?,'local',?,?,?,?,?)",
                         (current["operation_id"], member_id, manifest_sha256,
                          json.dumps(private_snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False), now))
            return result


def _local_adoption_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _local_adoption_repository(cls, database_path, media_root):
    return cls(database_path, media_root)


def _local_adoption_database_identity(repository) -> dict:
    stat = repository.database_path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino}


def _local_adoption_target(repository, conn, member_id):
    member = conn.execute("SELECT member_id,name,role,created_at FROM local_members WHERE member_id=?",
                          (member_id,)).fetchone()
    if member is None or member["role"] != "owner":
        raise ValueError("local_adoption_existing_owner_required")
    binding = conn.execute("SELECT * FROM material_ingestion_binding WHERE id=1").fetchone()
    if (binding is None or binding["workspace_id"] != "local" or
            Path(binding["library_path"]).resolve() != repository.video_library_path.resolve()):
        raise ValueError("local_adoption_existing_local_root_required")
    return dict(member), dict(binding)


def _local_adoption_file_revision(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError("local_adoption_regular_source_required")
    stat = path.stat()
    if stat.st_size <= 0:
        raise ValueError("local_adoption_empty_source")
    return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino, "device": stat.st_dev}


def _local_adoption_plan(repository, conn, *, member_id, operation_id, asset_ids,
                         voice_id, voice_version, new_voice_version, authorization_basis):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
        raise ValueError("local_adoption_operation_id_invalid")
    if not authorization_basis.strip() or not asset_ids or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("local_adoption_explicit_selection_and_consent_required")
    if not voice_id or not voice_version or not new_voice_version or voice_version == new_voice_version:
        raise ValueError("local_adoption_new_voice_version_required")
    member, binding = _local_adoption_target(repository, conn, member_id)
    root = repository.video_library_path.resolve()
    assets, references = [], {}
    originals = {"assets": [], "references": [], "member": member, "binding": binding}

    def source(reference, *, must_be_registered=False):
        path = repository._workspace_media_references.resolve_workspace_media_reference("local", reference)
        canonical = "workspace://" + path.relative_to(root).as_posix()
        if reference != canonical:
            raise ValueError("local_adoption_canonical_reference_required")
        row = conn.execute("SELECT * FROM workspace_media_references WHERE reference=?", (reference,)).fetchone()
        if row is not None and row["workspace_id"] != "local":
            raise ValueError("local_adoption_reference_other_workspace")
        if must_be_registered and row is None:
            raise ValueError("local_adoption_existing_voice_reference_required")
        # A differently encoded logical spelling cannot conceal another space's claim.
        for other in conn.execute("SELECT reference FROM workspace_media_references WHERE workspace_id!='local'"):
            if repository._workspace_media_references.resolve_workspace_media_reference("local", other["reference"]) == path:
                raise ValueError("local_adoption_reference_other_workspace")
        for other in repository._material_assets._asset_file_path_rows_outside_workspace(conn, "local"):
            if Path(other["file_path"]).resolve() == path:
                raise ValueError("local_adoption_source_other_workspace")
        if reference not in references:
            references[reference] = {"reference": reference,
                "before_sha256": _local_adoption_digest(dict(row)) if row else None}
            originals["references"].append(dict(row) if row else {"reference": reference, "absent": True})
        return path, _local_adoption_file_revision(path)

    for asset_id in sorted(asset_ids):
        row = repository._material_assets._asset_row_by_id(conn, "local", asset_id)
        if row is None:
            raise ValueError("local_adoption_selected_local_asset_missing")
        data = json.loads(row["payload"])
        if data.get("owner_user_id") is not None:
            raise ValueError("local_adoption_asset_already_owned")
        if data["id"] != asset_id or data["workspace_id"] != "local" or data["path"] != row["file_path"]:
            raise ValueError("local_adoption_asset_record_mismatch")
        path = Path(row["file_path"])
        if path != path.resolve():
            raise ValueError("local_adoption_canonical_path_required")
        try:
            reference = "workspace://" + path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("local_adoption_source_outside_root") from exc
        path, revision = source(reference)
        if (data["size_bytes"] != revision["size_bytes"] or
                abs(datetime.fromisoformat(data["modified_at"]).timestamp() - path.stat().st_mtime) > .00001):
            raise ValueError("local_adoption_asset_file_revision_mismatch")
        assets.append({"id": asset_id, "row_sha256": _local_adoption_digest(dict(row)),
            "payload_sha256": hashlib.sha256(row["payload"].encode()).hexdigest(),
            "visibility": row["visibility"], "updated_at": row["updated_at"],
            "reference": reference, "file_revision": revision,
            "target_owner_user_id": member_id, "target_visibility": "private"})
        originals["assets"].append(dict(row))

    voice_row = repository._profile_records._voice_version_row(conn, "local", voice_id, voice_version)
    latest = repository._profile_records._latest_voice_version_row(conn, "local", voice_id)
    if voice_row is None or latest["version"] != voice_version:
        raise ValueError("local_adoption_selected_current_voice_missing")
    if repository._profile_records._voice_version_existence_row(conn, "local", voice_id, new_voice_version):
        raise ValueError("local_adoption_voice_version_exists")
    data = json.loads(voice_row["payload"])
    if data.get("owner_user_id") is not None or data.get("reference") is not None:
        raise ValueError("local_adoption_voice_not_unowned_legacy")
    if data["id"] != voice_id or data["workspace_id"] != "local" or data["version"] != voice_version:
        raise ValueError("local_adoption_voice_record_mismatch")
    voice = repository._profile_records._versioned_voice_profile_from_payload(conn, "local", data)
    current_authorization = repository._media_authorizations._current_authorization(conn, "local", voice.authorization)
    if voice.authorization is None or current_authorization is None:
        raise ValueError("local_adoption_existing_voice_authorization_required")
    for authorization in (voice.authorization, current_authorization):
        failure = profile_use_failure(replace(voice, authorization=authorization),
                                      content_type="voice", usage_scope="narration")
        if failure:
            raise ValueError("local_adoption_voice_" + failure)
    path, revision = source(voice.reference_audio_path, must_be_registered=True)
    if repository._material_assets._asset_file_path_existence_row(conn, str(path)):
        raise ValueError("local_adoption_voice_source_already_registered")
    duration = _profile_wav_duration(path)
    if _local_adoption_file_revision(path) != revision:
        raise ValueError("local_adoption_voice_file_changed")
    authorization_rows = [dict(row) for row in repository._media_authorizations._authorization_history_rows(
        conn, "local", voice.authorization.id)]
    source_id = "voice_source_" + hashlib.sha256((operation_id + ":" + voice_id).encode()).hexdigest()[:32]
    if repository._material_assets._asset_id_existence_row(conn, source_id):
        raise ValueError("local_adoption_voice_source_id_exists")
    originals.update(voice=dict(voice_row), voice_path=str(path), authorizations=authorization_rows)
    manifest = {"format": "local-adoption/v1", "operation_id": operation_id, "workspace_id": "local",
        "member_id": member_id, "member_sha256": _local_adoption_digest(member),
        "database_identity": _local_adoption_database_identity(repository),
        "media_root": str(root), "root_binding_sha256": _local_adoption_digest(binding),
        "authorization_basis": authorization_basis, "assets": assets,
        "references": [references[key] for key in sorted(references)],
        "voice": {"id": voice_id, "version": voice_version, "new_version": new_voice_version,
            "row_sha256": _local_adoption_digest(dict(voice_row)),
            "payload_sha256": hashlib.sha256(voice_row["payload"].encode()).hexdigest(),
            "created_at": voice_row["created_at"], "authorization_sha256": _local_adoption_digest(authorization_rows),
            "target_owner_user_id": member_id, "target_visibility": "private",
            "source": {"id": source_id, "reference": voice.reference_audio_path,
                       "file_revision": revision, "duration_seconds": duration}}}
    return manifest, originals
