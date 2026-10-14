"""Profile registration, fixed selections and explicit legacy mapping."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import replace
import sqlite3
from app.creation.contracts import CreationPlanVoiceReference, CreationPlanPersonReference
from app.materials.assets import MaterialAssetStore
from app.materials.workspace_references import WorkspaceMediaReferenceAccess
from app.profiles.authorizations import MediaAuthorizationStore
from app.profiles.contracts import Authorization, PersonProfile, ProfileReference, VoiceProfile as VersionedVoiceProfile
from app.profiles.legacy_voice import LegacyVoiceProfileStore
from app.profiles.records import ProfileRecordStore
from app.profiles.reference_access import ProfileReferenceAccess, _validate_profile_reference_fields
from app.profiles.workspace_profile import WorkspaceProductionProfileStore
from app.source_policy import content_owner


class ProfileRegistrationUseCase:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection],
                 records: ProfileRecordStore, authorizations: MediaAuthorizationStore,
                 workspace_profiles: WorkspaceProductionProfileStore,
                 legacy_voice_profiles: LegacyVoiceProfileStore, references: ProfileReferenceAccess,
                 workspace_references: WorkspaceMediaReferenceAccess, assets: MaterialAssetStore) -> None:
        self._connect = connection_factory
        self._records = records
        self._authorizations = authorizations
        self._workspace_profiles = workspace_profiles
        self._legacy_voice_profiles = legacy_voice_profiles
        self._references = references
        self._workspace_references = workspace_references
        self._assets = assets

    def save_person_profile(
        self, person: PersonProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> PersonProfile:
        person = self._retain_profile_authorization("person_profiles", person)
        _validate_person_profile(person)
        if person.reference is not None:
            person = replace(person, owner_user_id=content_owner(verified_owner_user_id))
            self._references.resolve_profile_reference(person, viewer_user_id=verified_owner_user_id,
                                           local_diagnostic=local_diagnostic, for_generation=False)
            if person.reference.asset_id is None:
                path = self._workspace_references.resolve_workspace_media_reference(person.workspace_id, person.avatar_reference)
                asset = self._assets.get_asset_by_path(person.workspace_id, str(path))
                person = replace(person, reference=replace(person.reference, asset_id=asset.id))
        with self._connect() as conn:
            if person.reference is not None:
                self._records._require_profile_write_owner(conn, "person_profiles", person, verified_owner_user_id, local_diagnostic)
            if person.avatar_reference.startswith("workspace://"):
                self._workspace_references.resolve_workspace_media_reference(
                    person.workspace_id,
                    person.avatar_reference,
                )
                self._workspace_references.claim_workspace_media_reference(
                    conn,
                    workspace_id=person.workspace_id,
                    reference=person.avatar_reference,
                )
            if person.authorization is not None:
                self._authorizations._save_authorization(conn, person.workspace_id, person.authorization)
            self._records._save_person_profile(conn, person)
        return person

    def save_versioned_voice_profile(
        self,
        voice: VersionedVoiceProfile, *, verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> VersionedVoiceProfile:
        voice = self._retain_profile_authorization("versioned_voice_profiles", voice)
        _validate_versioned_voice_profile(voice)
        if voice.reference is not None:
            voice = replace(voice, owner_user_id=content_owner(verified_owner_user_id))
            self._references.resolve_profile_reference(voice, viewer_user_id=verified_owner_user_id,
                                          local_diagnostic=local_diagnostic, for_generation=False)
            if voice.reference.asset_id is None:
                path = self._workspace_references.resolve_workspace_media_reference(voice.workspace_id, voice.reference_audio_path)
                with self._connect() as conn:
                    row = self._assets._asset_id_row_by_path(
                        conn, voice.workspace_id, str(path))
                if row is not None:
                    voice = replace(voice, reference=replace(voice.reference, asset_id=str(row["id"])))
        with self._connect() as conn:
            if voice.reference is not None:
                self._records._require_profile_write_owner(conn, "versioned_voice_profiles", voice, verified_owner_user_id, local_diagnostic)
            self._workspace_references.resolve_workspace_media_reference(
                voice.workspace_id,
                voice.reference_audio_path,
            )
            if voice.person_profile_id:
                self._records._get_person_profile(conn, voice.workspace_id, voice.person_profile_id, voice.person_version)
            if voice.authorization is not None:
                self._authorizations._save_authorization(conn, voice.workspace_id, voice.authorization)
            self._workspace_references.claim_workspace_media_reference(
                conn,
                workspace_id=voice.workspace_id,
                reference=voice.reference_audio_path,
            )
            self._records._save_versioned_voice_profile(conn, voice)
        return voice

    def set_default_versioned_voice(self, workspace_id: str, voice_profile_id: str, version: str, *,
                                    viewer_user_id: str | None = None, local_diagnostic: bool = False) -> VersionedVoiceProfile:
        if not voice_profile_id.strip() or not version.strip():
            raise ValueError("默认声音必须指定准确档案与版本")
        voice = self._records.get_versioned_voice_profile(workspace_id, voice_profile_id, version)
        # A default can be stored without a model, but must point at a readable real reference.
        self._references.resolve_profile_reference(voice, viewer_user_id=viewer_user_id,
                                       local_diagnostic=local_diagnostic, for_generation=False)
        profile = self._workspace_profiles.get_profile(workspace_id)
        profile.default_voice_profile_id, profile.default_voice_version = voice.id, voice.version
        self._workspace_profiles.save_profile(profile)
        return voice

    def get_default_versioned_voice(self, workspace_id: str) -> VersionedVoiceProfile | None:
        profile = self._workspace_profiles.get_profile(workspace_id)
        if profile.default_voice_profile_id is None and profile.default_voice_version is None:
            return None
        if not profile.default_voice_profile_id or not profile.default_voice_version:
            raise ValueError("默认声音引用不完整，请重新选择准确版本")
        return self._records.get_versioned_voice_profile(workspace_id, profile.default_voice_profile_id,
                                                profile.default_voice_version)

    def creation_plan_voice_reference(self, workspace_id, profile_id, version, *, viewer_user_id=None, local_diagnostic=False):
        voice = self._records.get_versioned_voice_profile(workspace_id, profile_id, version)
        self._references.profile_reference_input(voice, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        auth = self._authorizations.get_authorization(voice.workspace_id, voice.authorization.id) if voice.authorization else None
        return CreationPlanVoiceReference(profile_id=voice.id, version=voice.version,
            person_profile_id=voice.person_profile_id, person_version=voice.person_version,
            person_authorization_id="", person_authorization_version="",
            authorization_id=auth.id if auth else "", authorization_version=auth.version if auth else "",
            language=voice.language, model_id=voice.model_id)

    def creation_plan_person_reference(self, workspace_id: str, profile_id: str, version: str, *,
                                       viewer_user_id: str | None = None, local_diagnostic: bool = False) -> CreationPlanPersonReference:
        selected = CreationPlanPersonReference(profile_id, version)
        person = self._records.get_person_profile(workspace_id, selected.profile_id, selected.version)
        self._references.profile_reference_input(person, viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
        return selected

    def migrate_legacy_voice_profile(
        self,
        *,
        workspace_id: str,
        legacy_voice_profile_id: str,
        person_profile_id: str,
        person_version: str,
        version: str,
        language: str,
        authorization: Authorization,
        verified_owner_user_id: str | None = None,
        local_diagnostic: bool = False,
    ) -> VersionedVoiceProfile:
        """Explicitly map a legacy voice row; no automatic migration is performed."""
        if language not in {"zh", "en", "zh-en"}:
            raise ValueError("配音语言必须是 zh、en 或 zh-en")
        legacy = self._legacy_voice_profiles.get_voice_profile(workspace_id, legacy_voice_profile_id)
        versioned = VersionedVoiceProfile(
            id=legacy.id,
            workspace_id=workspace_id,
            person_profile_id=person_profile_id,
            person_version=person_version,
            version=version,
            language=language,
            reference_audio_path=legacy.reference_audio_path,
            reference_text=legacy.reference_text,
            model_id=legacy.model_id,
            authorization=authorization,
            reference=ProfileReference(),
            display_name=legacy.name,
        )
        return self.save_versioned_voice_profile(versioned, verified_owner_user_id=verified_owner_user_id,
                                                 local_diagnostic=local_diagnostic)

    def _retain_profile_authorization(self, table, profile):
        with self._connect() as conn:
            return self._records._retain_profile_authorization(conn, table, profile)


def _validate_person_profile(person: PersonProfile) -> None:
    if not all(
        value.strip()
        for value in (person.id, person.workspace_id, person.display_name, person.version, person.avatar_reference)
    ):
        raise ValueError("人物档案缺少必要标识或形象引用")
    _validate_profile_reference_fields(person)
    if person.authorization is not None and person.authorization.workspace_id != person.workspace_id:
        raise ValueError("人物授权不属于当前工作区")


def _validate_versioned_voice_profile(voice: VersionedVoiceProfile) -> None:
    if not all(
        value.strip()
        for value in (
            voice.id,
            voice.workspace_id,
            voice.version,
            voice.reference_audio_path,
        )
    ):
        raise ValueError("声音档案缺少必要标识或参考资料")
    if voice.language not in {"zh", "en", "zh-en"}:
        raise ValueError("声音语言必须是 zh、en 或 zh-en")
    if bool(voice.person_profile_id) != bool(voice.person_version):
        raise ValueError("人物关系必须同时指定人物和版本")
    _validate_profile_reference_fields(voice)
    if voice.reference is not None and not voice.display_name.strip():
        raise ValueError("独立声音需要名称")
    if voice.authorization is not None and voice.authorization.workspace_id != voice.workspace_id:
        raise ValueError("声音授权不属于当前工作区")
