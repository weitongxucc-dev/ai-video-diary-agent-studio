"""A bounded evidence view must retain complete event totals for the Agent."""
from copy import deepcopy

from app.creation.quality_projection import bounded_quality_evidence, bounded_quality_findings


def test_quality_observation_counts_all_events_without_double_counting_examples():
    evidence = {"findings": ([{"kind": "black_frame"}] * 2
        + [{"kind": "clipping"}] * 3 + [{"kind": "timestamp_offset"}]),
        "black_frame_summary": {"event_count": 100, "total_black_seconds": 25},
        "clipping_summary": {"event_count": 50, "total_clipped_seconds": 3}}
    record = {"check_id": "check", "timeline_version_id": "timeline", "render_ref": "render",
        "check_state": "done", "result_status": "issues_found", "checked_at": "now",
        "error": None, "evidence": evidence}
    original = deepcopy(record)
    result = bounded_quality_evidence(record)
    assert len(result["findings"]) == 6
    assert result["findings_total"] == 151
    assert result["findings_truncated"] is True
    assert result["black_frame_summary"]["event_count"] == 100
    assert result["clipping_summary"]["event_count"] == 50
    assert record == original
    legacy = bounded_quality_findings({"findings": [{"kind": "black_frame"}] * 25})
    assert len(legacy["findings"]) == 20
    assert legacy["findings_total"] == 25 and legacy["findings_truncated"] is True
