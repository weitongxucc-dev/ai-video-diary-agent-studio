"""Project exact segment evidence for the already authorized material-library page."""

from dataclasses import dataclass

from app.materials.contracts import MaterialAsset, material_usage_eligibility
from app.materials.evidence_text import segment_local_text_evidence


@dataclass(frozen=True)
class MaterialLibraryHit:
    segment_id: str
    basis: str
    excerpt: str
    reference_allowed: bool
    manual_use_allowed: bool
    restriction: str | None


def material_library_hits(asset: MaterialAsset, *, query: str = "", tag: str | None = None) -> list[MaterialLibraryHit]:
    query = query.strip().casefold()
    if not query and not tag:
        return []
    hits = []
    for segment in asset.segments:
        if segment.end_seconds <= segment.start_seconds:
            continue
        tags = (*segment.user_tags, *segment.system_tags)
        if tag and tag not in tags and (not query or tag not in asset.user_tags):
            continue
        evidence = [
            *segment_local_text_evidence(asset, segment),
            *(("片段标签", value) for value in tags),
        ]
        if query:
            matched = next(((label, value) for label, value in evidence if query in value.casefold()), None)
            if matched is None:
                continue
        else:
            matched = ("片段标签", tag)
        basis, text = matched
        # Keep a late match in view when the original evidence is a long transcript.
        offset = max(0, text.casefold().find(query) - 36) if query else 0
        excerpt = ("…" if offset else "") + text[offset:offset + 160]
        if offset + 160 < len(text):
            excerpt += "…"
        reference = material_usage_eligibility(asset, segment, purpose="reference")
        manual = material_usage_eligibility(asset, segment, purpose="manual")
        hits.append(MaterialLibraryHit(
            segment.id, basis, excerpt, reference.eligible, manual.eligible,
            None if manual.eligible else manual.reason,
        ))
    return hits
