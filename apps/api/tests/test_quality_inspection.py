from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

import app.quality_inspection as inspection
from app.montage import CreationTaskMontagePlan, MontageClip
from app.quality_checks import QualityCheckError, QualityFinding, QualityReport, QualityUnavailable
from app.schemas import CreationTaskQualityCheckResponse


def montage(output_path="artifacts/video.mp4"):
    return CreationTaskMontagePlan(
        id="montage", workspace_id="local", task_id="task", script_id="script",
        script_title="测试", status="rendered", cues=[],
        clips=[MontageClip("clip-1", "cue", "asset", "segment", "source", 0, 1, 0, 1, 1, [], False),
               MontageClip("clip-2", "cue", "asset", "segment", "source", 1, 2, 1, 2, 1, [], False)],
        duration_seconds=2, low_confidence_clip_count=0, output_path=output_path,
    )


def report(findings=(), unavailable=()):
    return QualityReport("video.mp4", findings, unavailable, 2, "ffmpeg+ffprobe")


@pytest.mark.parametrize("quality_report,expected", [
    (report(), "clean"),
    (report(unavailable=(QualityUnavailable("clipping", "缺少音频"),)), "unavailable"),
    (report((QualityFinding("black_frame", .8, 1.2, "黑场"),),
            (QualityUnavailable("clipping", "缺少音频"),)), "issues_found"),
])
def test_persisted_three_results_and_clip_intersection(tmp_path, monkeypatch, quality_report, expected):
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test render")
    render_ref = inspection.compute_render_ref(path)
    check_id = repo.begin("local", "task", "v1", montage().output_path, render_ref)
    assert repo.get("local", "task")["check_state"] == "checking"
    monkeypatch.setattr(inspection, "inspect_media_quality", lambda _path: quality_report)
    inspection._inspect_and_finalize(repo, "local", "task", check_id, path, render_ref)
    reopened = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    payload = inspection.final_quality_check_payload(reopened.get("local", "task"), montage(),
                                                     timeline_version_id="v1", current_render_ref=render_ref)
    assert payload["check_state"] == "done"
    assert payload["result_status"] == expected
    assert payload["unavailable"] == inspection.quality_report_evidence(quality_report)["unavailable"]
    if quality_report.findings:
        assert payload["findings"][0]["clip_ids"] == ["clip-1", "clip-2"]
    assert reopened.get("other-workspace", "task") is None


@pytest.mark.parametrize("version,output_path,render_ref", [
    ("v2", "artifacts/video.mp4", "old-hash"),
    ("v1", None, None),
    ("v1", "artifacts/new.mp4", "old-hash"),
    ("v1", "artifacts/video.mp4", None),
    ("v1", "artifacts/video.mp4", "new-hash"),
])
def test_old_clean_invalidated_by_version_output_or_missing_content(tmp_path, version, output_path, render_ref):
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    check_id = repo.begin("local", "task", "v1", montage().output_path, "old-hash")
    repo.finish("local", "task", check_id, evidence=inspection.quality_report_evidence(report()))
    payload = inspection.final_quality_check_payload(repo.get("local", "task"), replace(montage(), output_path=output_path),
                                                     timeline_version_id=version, current_render_ref=render_ref)
    assert payload["stale"] is True
    assert payload["result_status"] is None
    assert payload["findings"] == payload["unavailable"] == []


def test_detection_failure_never_becomes_clean(tmp_path, monkeypatch):
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    check_id = repo.begin("local", "task", "v1", montage().output_path, "hash")
    def fail(_path):
        raise QualityCheckError("ffmpeg unavailable")
    monkeypatch.setattr(inspection, "inspect_media_quality", fail)
    inspection._inspect_and_finalize(repo, "local", "task", check_id, Path("unused"), "hash")
    payload = inspection.final_quality_check_payload(repo.get("local", "task"), montage(),
                                                     timeline_version_id="v1", current_render_ref="hash")
    assert payload["check_state"] == "failed"
    assert payload["result_status"] is None
    assert payload["error"] == "ffmpeg unavailable"


def test_late_background_completion_cannot_overwrite_new_check(tmp_path, monkeypatch):
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"first render")
    started, release = Event(), Event()
    def inspect(_path):
        started.set()
        assert release.wait(5)
        return report()
    monkeypatch.setattr(inspection, "inspect_media_quality", inspect)
    old_id = inspection.start_final_video_quality_check(repo, "local", "task", "v1", "old.mp4", path,
                                                        inspection.compute_render_ref(path))
    assert started.wait(5)
    new_id = repo.begin("local", "task", "v2", "new.mp4", "new-hash")
    repo.finish("local", "task", new_id, error="new render unavailable")
    release.set()
    # Join our own worker, proving it completed before asserting persistence.
    import threading
    for thread in threading.enumerate():
        if thread.name == f"quality-{old_id}":
            thread.join(5)
            assert not thread.is_alive()
    assert repo.get("local", "task")["check_id"] == new_id
    assert repo.get("local", "task")["error"] == "new render unavailable"


def test_content_changed_during_detection_is_failed(tmp_path, monkeypatch):
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    render_ref = inspection.compute_render_ref(path)
    check_id = repo.begin("local", "task", "v1", "video.mp4", render_ref)
    def mutate(_path):
        path.write_bytes(b"replacement")
        return report()
    monkeypatch.setattr(inspection, "inspect_media_quality", mutate)
    inspection._inspect_and_finalize(repo, "local", "task", check_id, path, render_ref)
    assert repo.get("local", "task")["check_state"] == "failed"
    assert repo.get("local", "task")["result_status"] is None


def test_service_restart_exposes_interrupted_check_as_failed_and_retryable(tmp_path):
    database = tmp_path / "checks.sqlite"
    repo = inspection.QualityCheckRepository(database)
    repo.begin("local", "task", "v1", montage().output_path, "hash")
    restarted = inspection.QualityCheckRepository(database)
    restarted.recover_interrupted()
    payload = inspection.final_quality_check_payload(restarted.get("local", "task"), montage(),
                                                     timeline_version_id="v1", current_render_ref="hash")
    assert payload["check_state"] == "failed" and payload["result_status"] is None
    assert "服务中断" in payload["error"]
    check_id = restarted.begin("local", "task", "v1", montage().output_path, "hash")
    restarted.finish("local", "task", check_id, evidence=inspection.quality_report_evidence(report()))
    assert restarted.get("local", "task")["result_status"] == "clean"


def test_clipping_summary_forwarded_without_loss_and_hidden_when_stale(tmp_path):
    from app.quality_checks import _clipping_summary

    findings = [QualityFinding("clipping", start, start + .1, "削平") for start in (0, .5, 1.2)]
    summary = _clipping_summary(findings, .25, limit=1, findings_limit=2)
    evidence = inspection.quality_report_evidence(replace(report(tuple(findings[:2])), clipping_summary=summary))
    repo = inspection.QualityCheckRepository(tmp_path / "checks.sqlite")
    check_id = repo.begin("local", "task", "v1", montage().output_path, "hash")
    repo.finish("local", "task", check_id, evidence=evidence)
    record = repo.get("local", "task")
    payload = inspection.final_quality_check_payload(record, montage(),
                                                     timeline_version_id="v1", current_render_ref="hash")
    assert payload["clipping_summary"] == summary
    assert CreationTaskQualityCheckResponse.model_validate(payload).model_dump()["clipping_summary"] == summary
    assert summary["event_count"] == 3
    assert summary["truncated_event_count"] == 2
    assert summary["findings_truncated_count"] == 1
    stale = inspection.final_quality_check_payload(record, montage(),
                                                   timeline_version_id="v2", current_render_ref="hash")
    assert stale["stale"] and stale["clipping_summary"] is None
    missing = inspection.final_quality_check_payload(record, montage(),
                                                     timeline_version_id="v1", current_render_ref=None)
    assert missing["stale"] and missing["clipping_summary"] is None
