"""Read-only envelope joining existing material and knowledge candidates.

This is a projection over existing search/governance records. It does not
create a second candidate store or promote a candidate into a fact.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _status(value: object, default: str = "unknown") -> str:
    return value if isinstance(value, str) and value else default


def _material_candidate(item: Any, source_revision: dict[str, str] | None) -> dict[str, object]:
    evidence = list(getattr(item, "evidence_ref_ids", []) or [])
    needs_confirmation = bool(getattr(item, "needs_confirmation", True))
    return {
        "kind": "material_segment",
        "ref": getattr(item, "segment_ref", None),
        "asset_ref": getattr(item, "asset_ref", None),
        "status": "candidate",
        "quality": {
            "status": "unknown",
            "score": getattr(item, "score", None),
            "needs_confirmation": needs_confirmation,
        },
        "source_revision": source_revision or {"status": "unknown"},
        "evidence": {"status": "available" if evidence else "unknown", "ref_ids": evidence},
        "authorization": {
            "status": "needs_confirmation" if needs_confirmation else "unknown",
            "purpose": "manual_or_automatic",
        },
        "language": {"status": "unknown"},
        "details": {
            "time_range": getattr(item, "time_range", ""),
            "reasons": list(getattr(item, "reasons", []) or []),
            "match_tier": getattr(item, "match_tier", "unknown"),
        },
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
    *, material_source_revision_for=None,
) -> dict[str, object]:
    """Return a bounded, explicitly uncertain candidate envelope."""
    candidates = []
    for item in material_matches:
        revision = material_source_revision_for(item) if material_source_revision_for else None
        candidates.append(_material_candidate(item, revision))
    candidates.extend(_knowledge_candidate(item) for item in knowledge_claims)
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
        "missing_domains": ["person", "voice"],
        "instruction": "候选、证据、授权和质量状态必须按原值解释；unknown/needs_confirmation 不能当作确认事实或采用许可。",
    }
