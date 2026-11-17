"""Selected, versioned profile metadata for candidate bundles; never runs media."""
from dataclasses import replace

from app.profiles.contracts import profile_use_failure


class ProfileCandidateReader:
    def __init__(self, repository):
        self.repository = repository

    def __call__(self, workspace_id, kind, profile_id, version, user_id, local_diagnostic=False):
        if kind not in {"person", "voice"} or not profile_id or not version:
            raise ValueError("人物或声音候选必须明确类型、ID和版本")
        repo = self.repository
        getter = (repo.profile_records.get_person_profile if kind == "person"
                  else repo.profile_records.get_versioned_voice_profile)
        fixed = getter(workspace_id, profile_id, version)
        current = getter(workspace_id, profile_id)
        for profile in (fixed, current):
            if (profile.workspace_id != workspace_id or profile.id != profile_id
                    or not repo.profile_readable(profile, user_id, local_diagnostic)):
                raise KeyError(profile_id)
        if fixed.version != version:
            raise KeyError(profile_id)
        reasons = []
        grants = []
        for profile in (fixed, current):
            try:
                grant = repo.current_profile_authorization(profile)
            except KeyError:
                reasons.append("authorization_missing")
                grant = None
            grants.append(grant)
            failure = profile_use_failure(replace(profile, authorization=grant),
                content_type="person_likeness" if kind == "person" else "voice",
                usage_scope="avatar_lipsync" if kind == "person" else "narration")
            if failure:
                reasons.append(failure)
        reasons = list(dict.fromkeys(reasons))
        reference = fixed.reference
        language = getattr(fixed, "language", None)
        return {
            "kind": kind + "_profile", "ref": f"{kind}:{fixed.id}", "asset_ref": None,
            "status": "candidate", "quality": {"status": "unknown", "reason": "未执行媒体质量测量"},
            "source_revision": {"profile_version": fixed.version, "current_profile_version": current.version},
            "evidence": {"status": "available" if reference else "unknown",
                "source": ({"asset_id": reference.asset_id, "segment_id": reference.segment_id,
                            "start_seconds": reference.start_seconds, "end_seconds": reference.end_seconds}
                           if reference else None)},
            "authorization": {"status": "unavailable" if reasons else "policy_allowed",
                "purpose": "avatar_lipsync" if kind == "person" else "narration",
                "reasons": reasons, "use_state": current.use_state, "visibility": current.visibility,
                "grants": [{"id": grant.id, "version": grant.version, "status": grant.status}
                           for grant in grants if grant is not None]},
            "language": {"status": "declared", "value": language} if language else {"status": "unknown"},
            "details": {"display_name": fixed.display_name, "runtime_status": "unknown",
                "runtime_reason": "仅读取版本和用途策略，未检查参考媒体、模型或生成执行能力"},
        }
