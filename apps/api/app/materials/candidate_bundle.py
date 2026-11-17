"""Read-only envelope joining existing material and knowledge candidates.

This is a projection over existing search/governance records. It does not
create a second candidate store or promote a candidate into a fact.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _status(value: object, default: str = "unknown") -> str:
    return value if isinstance(value, str) and value else default


def _declared_language(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _material_candidate(
    item: Any,
    source_revision: dict[str, str] | None,
    *,
    asset: Any | None = None,
    segment: Any | None = None,
    quality_profile: Any | None = None,
) -> dict[str, object]:
    evidence = list(getattr(item, "evidence_ref_ids", []) or [])
    needs_confirmation = bool(getattr(item, "needs_confirmation", True))
    use_state = _status(getattr(asset, "use_state", None)) if asset is not None else "unknown"
    if use_state == "excluded":
        authorization_status = "excluded"
    elif needs_confirmation:
        authorization_status = "needs_confirmation"
    elif use_state != "unknown":
        authorization_status = use_state
    else:
        authorization_status = "unknown"
    quality: dict[str, object] = {
        "status": _status(getattr(quality_profile, "status", None)) if quality_profile is not None else "unknown",
        "needs_confirmation": needs_confirmation,
    }
    if quality_profile is not None:
        quality.update({
            "metrics": dict(getattr(quality_profile, "metrics", {}) or {}),
            "profile_version": getattr(quality_profile, "profile_version", ""),
            "source_revision": dict(getattr(quality_profile, "source_revision", {}) or {}),
            "unavailable_reasons": list(getattr(quality_profile, "unavailable_reasons", ()) or ()),
        })
    metadata = getattr(asset, "metadata", None) if asset is not None else None
    language = _declared_language(
        getattr(segment, "language", None),
        getattr(item, "language", None),
        getattr(asset, "language", None) if asset is not None else None,
        metadata.get("language") if isinstance(metadata, dict) else None,
    )
    language_projection: dict[str, object] = (
        {"status": "declared", "value": language}
        if language is not None
        else {"status": "unknown", "reason": "素材没有声明语言，不能从文字内容推断语言。"}
    )
    details: dict[str, object] = {
        "time_range": getattr(item, "time_range", ""),
        "relevance_score": getattr(item, "score", None),
        "reasons": list(getattr(item, "reasons", []) or []),
        "match_tier": getattr(item, "match_tier", "unknown"),
    }
    if segment is not None and getattr(segment, "analysis_version", ""):
        details["analysis_version"] = segment.analysis_version
    if asset is not None:
        details["source"] = {
            "status": "known",
            "kind": "external_url" if getattr(asset, "source_url", "") else "local_library",
            "media_type": _status(getattr(asset, "media_type", None)),
        }
    return {
        "kind": "material_segment",
        "ref": getattr(item, "segment_ref", None),
        "asset_ref": getattr(item, "asset_ref", None),
        "status": "candidate",
        "quality": quality,
        "source_revision": source_revision or {"status": "unknown"},
        "evidence": {"status": "available" if evidence else "unknown", "ref_ids": evidence},
        "authorization": {
            "status": authorization_status,
            "use_state": use_state,
            "visibility": _status(getattr(asset, "visibility", None)) if asset is not None else "unknown",
            "owner_known": bool(getattr(asset, "owner_user_id", None)) if asset is not None else False,
            "purpose": "manual",
        },
        "language": language_projection,
        "details": details,
    }


def _knowledge_candidate(item: Any) -> dict[str, object]:
    evidence = list(getattr(item, "evidence_refs", []) or [])
    status = _status(getattr(item, "status", None), "candidate")
    return {
        "kind": "knowledge_claim",
        "ref": getattr(item, "id", None),
        "asset_ref": getattr(item, "knowledge_item_id", None),
        "status": status,
        "quality": {"status": "observed" if getattr(item, "confidence", None) is not None else "unknown",
                    "confidence": getattr(item, "confidence", None)},
        "source_revision": {"knowledge_version": _status(getattr(item, "knowledge_version", None))},
        "evidence": {"status": "available" if evidence else "unknown", "refs": evidence},
        "authorization": {"status": "needs_confirmation" if status != "confirmed" else "confirmed"},
        "language": {"status": "unknown"},
        "details": {"field_ref": getattr(item, "field_ref", None), "value": getattr(item, "value", None),
                    "unit": getattr(item, "unit", None), "origin": getattr(item, "origin", "unknown")},
    }


def build_candidate_bundle(
    material_matches: Iterable[Any] = (), knowledge_claims: Iterable[Any] = (),
    *, material_source_revision_for=None, material_asset=None, material_segment_for=None,
    material_quality_profile=None, material_quality_profile_for=None, knowledge_requested: bool = False,
    profile_candidates: Iterable[dict[str, object]] = (),
) -> dict[str, object]:
    """Return a bounded, explicitly uncertain candidate envelope."""
    candidates = []
    for item in material_matches:
        revision = material_source_revision_for(item) if material_source_revision_for else None
        segment = material_segment_for(item) if material_segment_for else None
        profile = (
            material_quality_profile_for(item)
            if material_quality_profile_for is not None
            else material_quality_profile
        )
        candidates.append(_material_candidate(
            item, revision, asset=material_asset, segment=segment,
            quality_profile=profile,
        ))
    candidates.extend(_knowledge_candidate(item) for item in knowledge_claims)
    profiles = list(profile_candidates)
    candidates.extend(profiles)
    included_domains = {item["kind"].removesuffix("_profile") for item in profiles}
    return {
        "schema": "candidate-bundle/v1",
        "candidates": candidates,
        "summary": {
            "total": len(candidates),
            "material_candidates": sum(item["kind"] == "material_segment" for item in candidates),
            "knowledge_candidates": sum(item["kind"] == "knowledge_claim" for item in candidates),
            "confirmed": sum(item["status"] == "confirmed" for item in candidates),
            "needs_confirmation": sum(item["authorization"]["status"] == "needs_confirmation" for item in candidates),
        },
        "missing_domains": [domain for domain in (["person", "voice"] if knowledge_requested else ["knowledge", "person", "voice"])
                            if domain not in included_domains],
        "instruction": "候选、证据、授权和质量状态必须按原值解释；unknown/needs_confirmation 不能当作确认事实或采用许可。",
    }
