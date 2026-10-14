"""Versioned person and voice records, without media or generation execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
import json
import sqlite3

from app.models import utc_now
from app.persistence_json import _required_text, _optional_string_tuple
from app.profiles.authorizations import MediaAuthorizationStore, _authorization_from_payload
from app.profiles.contracts import (
    Authorization, PersonProfile, ProfileReference, VoiceProfile as VersionedVoiceProfile, _as_utc,
)
from app.profiles.versioned_payloads import (
    _save_versioned_payload, _list_versioned_payloads, _versioned_row,
)


class ProfileRecordStore:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        authorizations: MediaAuthorizationStore,
    ) -> None:
        self._connect = connection_factory
        self._authorizations = authorizations

    def get_person_profile(
        self,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        with self._connect() as conn:
            return self._get_person_profile(conn, workspace_id, person_id, version)

    def list_person_profiles(self, workspace_id: str) -> list[PersonProfile]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="person_profiles",
                workspace_id=workspace_id,
                current_only=True,
            )
            return [
                self._person_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def list_person_profile_versions(
        self,
        workspace_id: str,
        person_id: str,
    ) -> list[PersonProfile]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="person_profiles",
                workspace_id=workspace_id,
                record_id=person_id,
            )
            return [
                self._person_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def get_versioned_voice_profile(
        self,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        with self._connect() as conn:
            return self._get_versioned_voice_profile(
                conn,
                workspace_id,
                voice_profile_id,
                version,
            )

    def list_versioned_voice_profiles(
        self,
        workspace_id: str,
    ) -> list[VersionedVoiceProfile]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="versioned_voice_profiles",
                workspace_id=workspace_id,
                current_only=True,
            )
            return [
                self._versioned_voice_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def list_versioned_voice_profile_versions(
        self,
        workspace_id: str,
        voice_profile_id: str,
    ) -> list[VersionedVoiceProfile]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="versioned_voice_profiles",
                workspace_id=workspace_id,
                record_id=voice_profile_id,
            )
            return [
                self._versioned_voice_profile_from_payload(conn, workspace_id, payload)
                for payload in payloads
            ]

    def _get_person_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        person_id: str,
        version: str | None = None,
    ) -> PersonProfile:
        row = _versioned_row(
            conn,
            table="person_profiles",
            workspace_id=workspace_id,
            record_id=person_id,
            version=version,
        )
        return self._person_profile_from_payload(conn, workspace_id, json.loads(str(row["payload"])))

    def _get_versioned_voice_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        voice_profile_id: str,
        version: str | None = None,
    ) -> VersionedVoiceProfile:
        row = _versioned_row(
            conn,
            table="versioned_voice_profiles",
            workspace_id=workspace_id,
            record_id=voice_profile_id,
            version=version,
        )
        return self._versioned_voice_profile_from_payload(
            conn,
            workspace_id,
            json.loads(str(row["payload"])),
        )

    def _profile_authorization_from_payload(self, conn, workspace_id, data):
        if not data.get("authorization_id") and not data.get("authorization_version"):
            return None, None
        if not data.get("authorization_id") or not data.get("authorization_version"):
            return None, "historical_authorization_reference_incomplete"
        try:
            return self._authorizations._get_authorization(
                conn, workspace_id, str(data["authorization_id"]), str(data["authorization_version"])
            ), None
        except KeyError:
            return None, "historical_authorization_reference_missing"

    def _person_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> PersonProfile:
        authorization, reference_issue = self._profile_authorization_from_payload(conn, workspace_id, data)
        return PersonProfile(
            id=_required_text(data, "id"),
            workspace_id=_required_text(data, "workspace_id"),
            display_name=_required_text(data, "display_name"),
            version=_required_text(data, "version"),
            avatar_reference=str(data.get("avatar_reference") or ""),
            background_capabilities=_optional_string_tuple(
                data, "background_capabilities"
            ),
            shot_capabilities=_optional_string_tuple(data, "shot_capabilities"),
            authorization=authorization,
            reference_issue=reference_issue,
            **_profile_reference_fields(data, authorization),
        )

    def _versioned_voice_profile_from_payload(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        data: dict[str, object],
    ) -> VersionedVoiceProfile:
        authorization, reference_issue = self._profile_authorization_from_payload(conn, workspace_id, data)
        return VersionedVoiceProfile(
            id=_required_text(data, "id"),
            workspace_id=_required_text(data, "workspace_id"),
            person_profile_id=str(data.get("person_profile_id") or ""),
            person_version=str(data.get("person_version") or ""),
            version=_required_text(data, "version"),
            language=_required_text(data, "language"),  # type: ignore[arg-type]
            reference_audio_path=str(data.get("reference_audio_path") or ""),
            reference_text=str(data.get("reference_text") or ""),
            model_id=str(data.get("model_id") or ""),
            authorization=authorization,
            reference_issue=reference_issue,
            display_name=str(data.get("display_name") or ""),
            **_profile_reference_fields(data, authorization),
        )

    def _require_profile_write_owner(self, conn, table: str, profile, verified_owner_user_id, local_diagnostic):
        row = conn.execute(f"SELECT payload FROM {table} WHERE workspace_id=? AND id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                           (profile.workspace_id, profile.id)).fetchone()
        if row is not None and not local_diagnostic:
            owner = json.loads(row["payload"]).get("owner_user_id")
            if not owner or owner != verified_owner_user_id:
                raise PermissionError("只有档案原登记成员可以保存新版本；历史归属缺失需要重新定位")

    def _retain_profile_authorization(self, conn: sqlite3.Connection, table, profile):
        row = conn.execute(f"SELECT payload FROM {table} WHERE workspace_id=? AND id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (profile.workspace_id, profile.id)).fetchone()
        if row is not None and profile.authorization is None:
            data = json.loads(row["payload"])
            prior = data.get("authorization")
            if prior is not None:
                authorization = _authorization_from_payload(prior)
                profile = replace(profile, authorization=self._authorizations._current_authorization(conn, profile.workspace_id, authorization))
        return profile

    def _save_person_profile(self, conn: sqlite3.Connection, person: PersonProfile) -> None:
        _save_versioned_payload(
            conn,
            table="person_profiles",
            workspace_id=person.workspace_id,
            record_id=person.id,
            version=person.version,
            payload=_person_profile_payload(person),
        )

    def _save_versioned_voice_profile(self, conn: sqlite3.Connection, voice: VersionedVoiceProfile) -> None:
        _save_versioned_payload(
            conn,
            table="versioned_voice_profiles",
            workspace_id=voice.workspace_id,
            record_id=voice.id,
            version=voice.version,
            payload=_versioned_voice_profile_payload(voice),
        )


def _person_profile_payload(person: PersonProfile) -> dict[str, object]:
    return {
        "id": person.id,
        "workspace_id": person.workspace_id,
        "display_name": person.display_name,
        "version": person.version,
        "avatar_reference": person.avatar_reference,
        "background_capabilities": list(person.background_capabilities),
        "shot_capabilities": list(person.shot_capabilities),
        "authorization_id": person.authorization.id if person.authorization else None,
        "authorization_version": person.authorization.version if person.authorization else None,
        **_profile_reference_payload(person),
    }


def _versioned_voice_profile_payload(voice: VersionedVoiceProfile) -> dict[str, object]:
    return {
        "id": voice.id,
        "workspace_id": voice.workspace_id,
        "person_profile_id": voice.person_profile_id,
        "person_version": voice.person_version,
        "version": voice.version,
        "language": voice.language,
        "reference_audio_path": voice.reference_audio_path,
        "reference_text": voice.reference_text,
        "model_id": voice.model_id,
        "authorization_id": voice.authorization.id if voice.authorization else None,
        "authorization_version": voice.authorization.version if voice.authorization else None,
        "display_name": voice.display_name,
        **_profile_reference_payload(voice),
    }


def _profile_reference_payload(profile) -> dict[str, object]:
    return {
        "reference": asdict(profile.reference) if profile.reference else None,
        "use_state": profile.use_state,
        "owner_user_id": profile.owner_user_id,
        "visibility": profile.visibility,
    }


def _profile_reference_fields(data: dict[str, object], authorization: Authorization | None) -> dict[str, object]:
    # Old payloads stay unchanged in SQLite. Missing provenance is not ownership.
    reference = data.get("reference")
    return {
        "reference": ProfileReference(**reference) if reference is not None else None,
        "use_state": data.get("use_state", "excluded" if authorization is not None and (
            authorization.status in {"revoked", "expired"} or
            (authorization.expires_at is not None and _as_utc(authorization.expires_at) <= utc_now())
        ) else "available"),
        "owner_user_id": data.get("owner_user_id"),
        "visibility": data.get("visibility", "private"),
    }
