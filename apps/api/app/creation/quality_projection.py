"""Finite quality previews with complete event totals, independent of HTTP."""


def bounded_quality_findings(evidence):
    findings = evidence.get("findings", [])
    total = len(findings)
    summaries = {}
    for kind, field in (("clipping", "clipping_summary"), ("black_frame", "black_frame_summary")):
        summary = evidence.get(field)
        if summary is not None:
            summaries[field] = summary
            examples = sum(item.get("kind") == kind for item in findings)
            total += max(0, summary["event_count"] - examples)
    shown = findings[:20]
    return {"findings": shown, "findings_total": total,
            "findings_truncated": total > len(shown), **summaries}


def bounded_quality_evidence(record):
    evidence = record["evidence"]
    return {"check_id": record["check_id"], "timeline_version_id": record["timeline_version_id"],
        "render_ref": record["render_ref"], "check_state": record["check_state"],
        "result_status": record["result_status"], "checked_at": record["checked_at"],
        "error": record["error"], "validation_scope": "deterministic_media_errors_only",
        **bounded_quality_findings(evidence), "unavailable": evidence.get("unavailable", [])}
