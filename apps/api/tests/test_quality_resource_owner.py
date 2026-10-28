"""Declared metadata ports only; this node is not native/resource calibration."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import pytest

from app.artifact_store import ArtifactStore
from app.creation.quality_execution import quality_check_registration
from app.creation.quality_inspection_use_case import QUALITY_KIND, QualityInspectionUseCase
from app.creation.output_observation_use_case import OutputObservationUseCase
from app.creation_execution import CreationExecutionActor
from app.device_runtime import build_device_runtime
from app.device_scheduler import ProcessIdentity, RecoveryBlocked, VerifiedSession
from app.host_resource_probes import HostResourceDecision
from app.montage import CreationTaskMontagePlan, MontageClip, creation_task_montage_plan_to_payload
from app.creation.contracts import ArtifactRecord, CreationTask, JobRun, TimelineVersion
from app.production import CreationPlan, ProductionRepository
from app.quality_checks import QualityReport, QualityUnavailable, quality_report_evidence
from app.quality_inspection import QualityCheckRepository
from app.quality_worker_runtime import (QualityBudget, source_receipt, QUALITY_PHASES,
    quality_phase_request, quality_unit_key)
from app.task_audio import TaskAudio


def licensed_native_port(request):
    """The separate native gate supplies calibrated grant/process ports explicitly."""
    import pytest
    try:
        return request.getfixturevalue("licensed_quality_native")
    except pytest.FixtureLookupError:
        pytest.fail("质检 native fixture 尚未绑定独立标定预算与真实许可；本轮不能执行此矩阵。", pytrace=False)


class QualityCheckFixtureRepository(QualityCheckRepository):
    """Stamp a declared metadata source for the existing repository unit fixtures."""
    def finish(self, workspace_id, task_id, check_id, *, evidence=None, error=None, connection=None):
        if evidence is not None and error is None:
            record = self.get_check(workspace_id, task_id, check_id, connection=connection)
            source = _unit_source(record)
            evidence = {**evidence, "_source_receipt": source_receipt(source, check_id)}
        return super().finish(workspace_id, task_id, check_id, evidence=evidence, error=error, connection=connection)


def _unit_source(record):
    return {key: record[key] for key in ("workspace_id", "task_id", "timeline_version_id", "source_ref", "render_ref")} | {
        "artifact_id": "declared-unit-artifact", "size_bytes": 1,
        "file_identity": {"device": 1, "inode": 1, "size_bytes": 1, "mtime_ns": 1, "ctime_ns": 1}}


def unit_hash(path):
    raw = Path(path).read_bytes()
    assert len(raw) <= 1024  # These persistence fixtures are tiny non-media bytes.
    return hashlib.sha256(raw).hexdigest()


def unit_projection(record, plan, **kwargs):
    from app.quality_inspection import final_quality_check_payload
    return final_quality_check_payload(record, plan, current_source=_unit_source(record) if record else None, **kwargs)


def finish_report_fixture(repo, workspace_id, task_id, check_id, path, render_ref, *, inspector):
    """An explicit declared report port for persistence assertions, never native work."""
    from app.quality_checks import QualityCheckError
    try:
        result = inspector(path)
        if Path(path).exists() and unit_hash(path) != render_ref:
            raise QualityCheckError("检查期间成片内容已变化，请重新检查当前成片。")
        repo.finish(workspace_id, task_id, check_id, evidence=quality_report_evidence(result))
    except QualityCheckError as exc:
        repo.finish(workspace_id, task_id, check_id, error=str(exc))


def fixture_budget():
    # Synthetic port facts, with no executable or real host capacity claim.
    return QualityBudget(max_input_bytes=1024, max_pcm_bytes=2048, max_frame_json_bytes=4096,
        max_report_bytes=16384, max_stderr_bytes=1024, max_timeout_seconds=10., decoder_threads=1,
        max_process_rss_bytes=1024, max_process_threads=2, runtime_sample_seconds=.1,
        additional_memory_bytes=1024, additional_disk_bytes=1024, max_tool_bytes=1024,
        ffmpeg_path="/fixture-not-executable/ffmpeg", ffprobe_path="/fixture-not-executable/ffprobe",
        ffmpeg_sha256="1" * 64, ffprobe_sha256="2" * 64, worker_sha256="3" * 64)


class MetadataAccess:
    def verify_session(self, heartbeat, actor):
        return VerifiedSession(heartbeat.session_id, heartbeat.workspace_id, actor.member_id)

    def can_view_device_summary(self, actor):
        return actor.workspace_id == "local"


class MetadataResources:
    def __init__(self):
        self.admissions, self.releases = [], []

    def admit(self, request):
        self.admissions.append(request.id)
        return HostResourceDecision(True, dict(request.resources), {"declared_metadata_port": {}})

    def continue_unit(self, grant):
        return True

    def released(self, grant):
        self.releases.append(grant.request_id)
        return True


class MetadataProcesses:
    def __init__(self):
        self.recovery = "released"

    def current(self):
        return ProcessIdentity(os.getpid(), "metadata-fixture-start", "metadata-fixture-boot")

    def children_released(self, children):
        assert not children
        return True

    def recovery_state(self, holder, children):
        assert not children
        return self.recovery


def build_metadata_runtime(root, service, worker, *, resources=None, processes=None):
    return build_device_runtime(database_path=service.production.database_path,
        device_lock_path=(root / "device.lock").resolve(), device_access=MetadataAccess(),
        registrations={QUALITY_KIND: quality_check_registration(service, worker=worker)},
        resources=resources or MetadataResources(), processes=processes or MetadataProcesses(),
        poll_interval_seconds=.01)


def fixture_montage(ref):
    return CreationTaskMontagePlan(id="montage", workspace_id="local", task_id="task", script_id="script",
        script_title="测试", status="rendered", cues=[], duration_seconds=2, low_confidence_clip_count=0,
        output_path=ref, clips=[MontageClip("clip-1", "cue", "asset", "segment", "source", 0, 2, 0, 2, 1, [], False)])


def seed_source(repository, store, version="v1"):
    ref = f"artifacts/{version}.mp4"
    publication = store.publish_bytes(ref, f"non-media metadata fixture {version}".encode())
    producer = f"fixture-producer-{version}"
    repository.create_job_run(JobRun(id=producer, workspace_id="local", task_id="task", kind="creation_task_montage_render",
        idempotency_key=producer, input_digest=version))
    repository.execution_records.create_artifact_record(ArtifactRecord(f"artifact-{version}", "local", "task", "rendered_video", ref,
        publication.sha256, publication.size_bytes, producer, []))
    payload = creation_task_montage_plan_to_payload(fixture_montage(ref))
    repository.save_creation_task_montage_plan("local", "task", "montage", payload)
    parent = repository.get_creation_task("local", "task").current_timeline_version_id
    sequence = repository.get_timeline_version("local", parent).sequence + 1 if parent else 1
    repository.create_timeline_version(TimelineVersion(id=version, workspace_id="local", task_id="task",
        parent_version_id=parent, sequence=sequence,
        snapshot={"montage": payload, "output_refs": [ref]}, created_by_operation_id=None))
    return publication


def metadata_result(request):
    report = QualityReport("fixture.mp4", (), (QualityUnavailable("clipping", "声明端口无 native 音频"),), 2, "declared-metadata-port")
    result = {"evidence": quality_report_evidence(report), "receipt": source_receipt(request["source"], request["check_id"]),
        "worker_sha256": request["budget"]["worker_sha256"]}
    if "phase" in request:
        phase = request["phase"]
        result.update(phase=phase, checkpoint_digest=request["checkpoint_digest"])
        if phase in {"metadata", "finalize"}:
            result["file_sha256"] = request["source"]["render_ref"]
        if phase != "finalize":
            result.pop("evidence")
            result["data"] = ({"streams": [], "format": {"duration": "2"}} if phase == "metadata" else
                {"findings": [], "unavailable": [], "clipping_summary": None})
    return result


def stage_metadata_prefix(runtime, service, actor, check_id):
    """Declare completed non-native phases through the real owner transaction."""
    for phase in QUALITY_PHASES[:-1]:
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.unit_key == f"quality:{check_id}:{phase}"
        job, _, record, sealed = service.read(actor.workspace_id, grant.owner_id)
        if job.status in {"queued", "interrupted"}:
            job = service.production.claim_job_run(actor.workspace_id, job.id)
        request = quality_phase_request(sealed, record["evidence"])
        result = metadata_result(request)
        service.validate_result(request, result)
        service.checks.store_worker_result(job.workspace_id, job.task_id, check_id, result)
        runtime.scheduler.complete_unit(grant, checkpoint_database_path=service.production.database_path,
            checkpoint_write=lambda conn, current: service.complete_result(conn, job, check_id, result))
        service.submit_next(runtime.scheduler, job.workspace_id, job.id, actor)
    _, _, record, sealed = service.read(actor.workspace_id, f"job_quality_{check_id}")
    return quality_phase_request(sealed, record["evidence"])


def test_quality_owner_metadata_current_history_terminal_and_recovery(tmp_path):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id="original-owner")
    repository.set_creation_task_visibility("local", "task", "shared")
    actor = CreationExecutionActor("local", "shared-reader")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)
    calls, newer = [], []

    def worker(context, request, temporary_root):
        context.check()
        assert context.grant.owner_type == QUALITY_KIND
        assert repository.get_job_run("local", context.grant.owner_id).status == "running"
        calls.append(request["check_id"])
        if len(calls) == 1:
            seed_source(repository, store, "v2")
            newer.append(service.start(runtime, actor, "local", "task", force=True))
        return metadata_result(request)

    resources = MetadataResources()
    processes = MetadataProcesses()
    runtime = build_metadata_runtime(tmp_path, service, worker, resources=resources, processes=processes)
    try:
        old_id = service.start(runtime, actor, "local", "task", force=True)
        assert calls == []
        assert service.start(runtime, actor, "local", "task", force=True) == old_id
        with repository._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM device_work_requests WHERE owner_type=?", (QUALITY_KIND,)).fetchone()[0] == 1
        assert runtime.run_once() is True
        assert runtime.stop(1)
        runtime = build_metadata_runtime(tmp_path, service, worker, resources=resources, processes=processes)
        assert checks.get_check("local", "task", old_id)["result_status"] is None
        assert list(checks.get_check("local", "task", old_id)["evidence"]["_phases"]) == ["metadata"]
        assert checks.get("local", "task")["check_id"] == newer[0]
        assert checks.get("local", "task")["check_state"] == "checking"
        service.recover(runtime.scheduler)
        assert calls == [old_id]  # A new runtime reuses the committed metadata phase.
        with repository._connect() as conn:
            units = [tuple(row) for row in conn.execute("SELECT unit_key,state FROM device_work_requests WHERE owner_id=? ORDER BY unit_key", (f"job_quality_{old_id}",))]
        assert units == [(f"quality:{old_id}:black_frames", "waiting"), (f"quality:{old_id}:metadata", "done")]
        for _ in range(2 * len(QUALITY_PHASES) - 1):
            ran = runtime.run_once()
            with repository._connect() as conn:
                queue = [dict(row) for row in conn.execute("SELECT unit_key,state,wait_reason,last_failure_reason FROM device_work_requests")]
            assert ran is True, queue
        current = service.projection("local", "task")
        assert current["check_id"] == newer[0] and current["result_status"] == "unavailable"
        assert current["stale"] is False and current["findings"] == []
        assert not any(key.startswith("_") for key in current)
        reopened = QualityCheckRepository(repository.database_path)
        for check_id in (old_id, newer[0]):
            job = repository.get_job_run("local", f"job_quality_{check_id}")
            assert job.status == "succeeded"
            terminal = repository.execution_records.get_task_event("local", f"event_{job.id}_quality_terminal")
            assert terminal.payload == {"check_id": check_id, "status": "succeeded"}
            assert reopened.get_check("local", "task", check_id)["check_state"] == "done"
        assert calls.count(old_id) == calls.count(newer[0]) == len(QUALITY_PHASES)
        assert len(set(resources.admissions)) == 2 * len(QUALITY_PHASES)
        assert set(resources.admissions) <= set(resources.releases)
        released_result = service.start(runtime, actor, "local", "task", force=True)
        request = stage_metadata_prefix(runtime, service, actor, released_result)
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.owner_id == f"job_quality_{released_result}"
        job = repository.get_job_run("local", grant.owner_id)
        _, _, record, _ = service.read("local", job.id)
        # A declared already-returned worker receipt, not the separate late-SQL gate.
        checks.store_worker_result("local", "task", record["check_id"], metadata_result(request))
        seed_source(repository, store, "v3")
        interrupted = service.start(runtime, actor, "local", "task", force=True)
        assert interrupted != released_result
        assert runtime.stop(1)
        runtime = build_metadata_runtime(tmp_path, service, worker, resources=resources, processes=processes)

        def persisted_facts():
            with repository._connect() as conn:
                return {table: sorted([dict(row) for row in conn.execute(f"SELECT * FROM {table}")], key=lambda row: json.dumps(row, sort_keys=True))
                    for table in ("creation_task_quality_checks", "creation_task_quality_history", "job_runs", "task_events",
                                  "device_permit", "device_work_requests", "device_request_events")}

        before = persisted_facts()
        admissions_before = len(resources.admissions)
        for state in ("unknown", "active"):
            processes.recovery = state
            with pytest.raises(RecoveryBlocked):
                service.recover(runtime.scheduler)
            assert persisted_facts() == before
            assert len(calls) == 2 * len(QUALITY_PHASES) and len(resources.admissions) == admissions_before
        # Metadata settlement of the sealed old receipt does not borrow a new profile.
        service.budget = None
        processes.recovery = "released"
        service.recover(runtime.scheduler)
        assert repository.get_job_run("local", f"job_quality_{released_result}").status == "succeeded"
        assert reopened.get_check("local", "task", released_result)["result_status"] == "unavailable"
        assert repository.execution_records.get_task_event("local", f"event_job_quality_{released_result}_quality_terminal").payload == {
            "check_id": released_result, "status": "succeeded"}
        assert reopened.get("local", "task")["check_id"] == interrupted
        assert reopened.get("local", "task")["check_state"] == "checking"
        assert service.projection("local", "task")["result_status"] is None
        assert repository.get_job_run("local", f"job_quality_{interrupted}").status == "interrupted"
        assert len(calls) == 2 * len(QUALITY_PHASES)
        evidence_root = os.environ.get("QUALITY_RESOURCE_EVIDENCE_DIR")
        if evidence_root:
            Path(evidence_root, "NORMAL_FACTS.json").write_text(json.dumps({"port": "non-native metadata only",
                "checks": [old_id, newer[0], released_result, interrupted], "worker_calls": calls, "shared_reader": actor.member_id,
                "terminal_status": "unavailable", "recovery_status": "interrupted", "runtime_run_once_calls": 2 * len(QUALITY_PHASES),
                "unknown_active_full_DB_unchanged": True, "released_receipt_resumed_without_worker": True,
                "recovery_current_profile": None, "reconciliation_extra_admissions": len(resources.admissions) - admissions_before}, indent=2))
    finally:
        assert runtime.stop(1)


def test_frozen_worker_identity_hashes_binary_content_and_honors_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app import quality_worker
    from app.quality_worker_runtime import QualityWorkerError
    binary = tmp_path/'api-server'
    contents = b'final-signed-binary' * 5000
    binary.write_bytes(contents)
    monkeypatch.setattr(quality_worker, 'sys', SimpleNamespace(frozen=True, executable=str(binary)))
    expected = hashlib.sha256(contents).hexdigest()
    assert quality_worker.worker_identity() == expected
    binary.write_bytes(contents + b'changed')
    assert quality_worker.worker_identity() == hashlib.sha256(contents + b'changed').hexdigest() != expected
    def expired():
        raise QualityWorkerError('original deadline expired')
    with pytest.raises(QualityWorkerError, match='original deadline expired'):
        quality_worker.worker_identity(check=expired)


class StreamingQualityFixture:
    """Chunked decoded bytes only; no subprocess or media capability claim."""
    def __init__(self, raw, chunk_size=13):
        self.raw, self.chunk_size, self.calls = raw, chunk_size, 0

    def run(self, command, timeout, *, stdout_consumer, pcm=False):
        import subprocess
        self.calls += 1
        for offset in range(0, len(self.raw), self.chunk_size):
            stdout_consumer(self.raw[offset:offset + self.chunk_size])
        return subprocess.CompletedProcess(command, 0, b'', b'')


def test_streaming_clipping_preserves_cross_chunk_runs_and_all_event_totals():
    import struct
    from app.quality_checks import detect_clipping, CLIP_FINDING_EVENT_LIMIT
    count = CLIP_FINDING_EVENT_LIMIT + 7
    # A negative s16 minimum has magnitude 32768. The last plateau has no
    # trailing zero, exercising both the byte carry and end-of-stream flush.
    samples = ([0] + [-32768] * 8) * count
    context = StreamingQualityFixture(struct.pack('<' + 'h' * len(samples), *samples))
    result = detect_clipping('declared.wav', sample_rate=1000,
        audio_start_seconds=1, timeline_origin_seconds=.5, context=context)
    assert context.calls == 2
    assert result.unavailable == []
    assert len(result.findings) == CLIP_FINDING_EVENT_LIMIT
    summary = result.summary
    assert summary['event_count'] == count
    assert summary['findings_truncated_count'] == 7
    assert summary['total_clipped_seconds'] == round(count * .008, 3)
    assert summary['first_start_seconds'] == .501
    assert summary['last_end_seconds'] == round(.5 + len(samples) / 1000, 3)
    assert summary['full_scale_duty'] == round(8 / 9, 6)
    assert result.findings[0].start_seconds == .501


@pytest.mark.parametrize('samples, reason', [([], '没有解出'), ([0] * 9, '全为静音')])
def test_streaming_empty_or_silent_pcm_remains_unknown(samples, reason):
    import struct
    from app.quality_checks import detect_clipping
    result = detect_clipping('declared.wav', sample_rate=1000,
        context=StreamingQualityFixture(struct.pack('<' + 'h' * len(samples), *samples)))
    assert not result.findings and result.summary is None
    assert reason in result.unavailable[0].reason


def test_streaming_clipping_uses_global_peak_and_rejects_partial_sample():
    import struct
    from app.quality_checks import detect_clipping, QualityCheckError
    samples = [100] * 50 + [0] * 50 + [1000] * 8
    result = detect_clipping('declared.wav', sample_rate=1000,
        context=StreamingQualityFixture(struct.pack('<' + 'h' * len(samples), *samples)))
    assert result.findings == [] and result.unavailable == []  # 8/108 < 10%
    with pytest.raises(QualityCheckError, match='不完整采样'):
        detect_clipping('declared.wav', sample_rate=1000, context=StreamingQualityFixture(b'\x00'))


def test_streaming_frames_use_max_decoded_end_and_reject_malformed_records():
    from app.quality_checks import _decoded_stream_end_seconds, QualityCheckError
    context = StreamingQualityFixture(b'pts_time=2.0|duration_time=0.04\n'
        b'pts_time=1.0|duration_time=0.04\npts_time=9.0|duration_time=N/A', chunk_size=7)
    assert _decoded_stream_end_seconds('declared.mkv', 'video', context=context) == 2.04
    with pytest.raises(QualityCheckError, match='invalid frame metadata'):
        _decoded_stream_end_seconds('declared.mkv', 'video',
            context=StreamingQualityFixture(b'pts_time=1|pts_time=2|duration_time=.04\n'))


def test_streaming_frames_reject_unterminated_record_before_total_output_quota():
    from app.quality_checks import _decoded_stream_end_seconds, QualityCheckError

    class UnterminatedFrame:
        consumed = 0

        def run(self, command, timeout, *, stdout_consumer):
            # No aggregate quota in this declared port: the parser itself must
            # bound its carry, without waiting for EOF or the runtime byte cap.
            while self.consumed < 1024 * 1024:
                self.consumed += 17
                stdout_consumer(b'9' * 17)
            pytest.fail('unterminated frame record was retained without a format bound')

    source = UnterminatedFrame()
    with pytest.raises(QualityCheckError, match='scalar protocol limit'):
        _decoded_stream_end_seconds('declared.mkv', 'video', context=source)
    assert source.consumed < 1024
    with pytest.raises(QualityCheckError, match='scalar protocol limit'):
        _decoded_stream_end_seconds('declared.mkv', 'video',
            context=StreamingQualityFixture(b'pts_time=' + b'9' * 4096 + b'\n', chunk_size=8192))


@pytest.mark.parametrize('outcome', ['complete', 'quota', 'cancel', 'stream', 'stream_cancel', 'stream_deadline', 'stream_stderr', 'stream_diagnostics'])
def test_streaming_worker_drains_with_bounded_output_and_cleans_up(monkeypatch, outcome):
    import time
    from app import quality_worker_runtime as worker
    payload = b'0123456789' * 20

    def pipe(data):
        reader, writer = os.pipe()
        os.write(writer, data)
        os.close(writer)
        return os.fdopen(reader, 'rb')

    class Process:
        def __init__(self):
            is_stderr = outcome in {'stream_stderr', 'stream_diagnostics'}
            self.stdout, self.stderr = pipe(b'' if is_stderr else payload), pipe(payload if is_stderr else b'')
            self.returncode, self.killed = None, False
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            self.returncode = -9 if self.killed else 0
            return self.returncode
        def kill(self):
            self.killed = True

    process = Process()
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: process)
    context = object.__new__(worker.QualityWorkerContext)
    context.budget = replace(fixture_budget(), max_pcm_bytes=100 if outcome in {'quota', 'stream', 'stream_cancel'} else 200,
        max_stderr_bytes=100 if outcome in {'stream_stderr', 'stream_diagnostics'} else 1024)
    context.resources = {'read_mode': 'stream'} if outcome.startswith('stream') else None
    context.processing = []
    context.deadline, context._verified_tools = time.monotonic() + 3, {'ffmpeg'}
    consumed = 0

    def consume(raw):
        nonlocal consumed
        consumed += len(raw)
        if outcome in {'cancel', 'stream_cancel'}:
            raise worker.QualityWorkerError('cancelled original unit')
        if outcome == 'stream_deadline':
            context.deadline = time.monotonic() - 1

    consumers = {'stderr_consumer': consume} if outcome == 'stream_stderr' else {'pcm': True, 'stdout_consumer': consume}
    if outcome in {'complete', 'stream', 'stream_stderr'}:
        result = context.run(['ffmpeg', '-ar', '48000'], 0 if outcome == 'stream' else 2, **consumers)
        assert result.stdout == b'' and consumed == len(payload)
        assert not process.killed
    else:
        with pytest.raises(worker.QualityWorkerError, match='输出超过|cancelled|总时限'):
            context.run(['ffmpeg', '-ar', '48000'], 2, **consumers)
        assert process.killed
    assert process.stdout.closed and process.stderr.closed
    if outcome == 'stream':
        assert context.processing[0]['stdout_bytes'] == 200 > context.budget.max_pcm_bytes
        assert context.processing[0]['complete'] is True
    if outcome == 'stream_stderr':
        assert context.processing[0]['stderr_bytes'] == 200 > context.budget.max_stderr_bytes


@pytest.mark.parametrize("case", ["normal", "clipped", "black"])
def test_streaming_native_compact_frames_and_pcm_agree_on_tiny_media(tmp_path, granted_media, case):
    """One 0.3s synthetic clip through an actual grant, not device calibration."""
    import shutil
    import sys
    from dataclasses import asdict
    from app.granted_subprocess import run_granted_command

    binaries = {name: shutil.which(name) for name in ('ffmpeg', 'ffprobe')}
    if not all(binaries.values()):
        pytest.skip('synthetic streaming verification requires ffmpeg/ffprobe')
    identities = {}
    for name, path in binaries.items():
        with open(path, 'rb') as source:
            identities[name + '_sha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
    budget = replace(fixture_budget(), max_input_bytes=1024 ** 2, max_pcm_bytes=128 * 1024,
        max_frame_json_bytes=64 * 1024, max_report_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        max_timeout_seconds=15, max_process_rss_bytes=512 * 1024 ** 2,
        additional_memory_bytes=512 * 1024 ** 2, additional_disk_bytes=1024 ** 2,
        max_tool_bytes=max(Path(path).stat().st_size for path in binaries.values()),
        ffmpeg_path=binaries['ffmpeg'], ffprobe_path=binaries['ffprobe'], **identities)
    request = tmp_path / 'streaming-native.json'
    request.write_text(json.dumps({'budget': asdict(budget), 'root': str(tmp_path), 'case': case}))
    program = '''
import json, sys, time
from pathlib import Path
from dataclasses import asdict, replace
from app.quality_worker_runtime import QualityBudget, QualityWorkerContext, QUALITY_PHASES, QUALITY_STREAM_PROTOCOL, quality_phase_request, file_identity
from app.quality_worker import inspect_request, worker_identity
from app.quality_checks import inspect_media_quality, quality_report_evidence
import hashlib
request=json.loads(Path(sys.argv[1]).read_text())
context=QualityWorkerContext(QualityBudget(**request['budget']), time.monotonic()+15)
reports=[]
for name, amplitude in [('normal', .5), ('clipped', 3), ('black', .5)]:
    if name != request['case']: continue
    target=Path(request['root'])/(name+'.mkv')
    result=context.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        ('color=black:size=64x48:rate=10:duration=0.3' if name=='black' else 'testsrc=size=64x48:rate=10:duration=0.3'),'-f','lavfi','-i',
        f'aevalsrc={amplitude}*sin(2*PI*440*t):s=48000:d=0.3',
        '-c:v','ffv1','-threads:v','1','-pix_fmt','yuv420p','-c:a','pcm_s16le',str(target)],3)
    if result.returncode: raise RuntimeError(result.stderr.decode())
    report=quality_report_evidence(inspect_media_quality(target,context=context))
    if name in {'normal', 'black'}:
        with target.open('rb') as source_file:
            sha = hashlib.file_digest(source_file, 'sha256').hexdigest()
        source=dict(workspace_id='fixture', task_id='fixture', timeline_version_id='v1', source_ref='fixture.mkv',
            artifact_id='fixture', render_ref=sha, size_bytes=target.stat().st_size, file_identity=file_identity(target), path=str(target))
        sealed=dict(protocol=QUALITY_STREAM_PROTOCOL, source=source, check_id='synthetic-phase-check', thresholds={},
            budget=asdict(replace(context.budget, max_input_bytes=1, max_pcm_bytes=2, max_frame_json_bytes=3, worker_sha256=worker_identity())))
        checkpoints={}
        for phase in QUALITY_PHASES:
            phase_request=quality_phase_request(sealed, {'_phases':checkpoints})
            response=inspect_request({**phase_request, 'deadline_monotonic':time.monotonic()+15})
            assert response['phase']==phase
            if phase=='finalize':
                assert response['file_sha256']==sha
                assert response['evidence']==report
            else:
                checkpoints[phase]=response['data']
                if phase=='truncation':
                    assert response['data']['decoded_streams']['audio']['sample_count']==14400
                    assert response['data']['decoded_streams']['video']['frame_count']==3
                if phase=='clipping':
                    assert response['data']['decoded_pcm']['frame_sample_mapping_matches'] is True
                    assert [p['sample_count'] for p in response['data']['decoded_pcm']['passes']]==[14400,14400]
    reports.append({key:report[key] for key in ('status','unavailable','clipping_summary','black_frame_summary')})
print(json.dumps(reports))
'''
    with granted_media() as context:
        result = run_granted_command(context.scheduler, context.grant,
            [sys.executable, '-c', program, str(request)], timeout_seconds=20,
            max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
            cancel_event=context.cancel_event)
    assert result.returncode == 0, result.stderr.decode()
    report, = json.loads(result.stdout)
    if case == "normal":
        assert report['status'] == 'clean'
    elif case == "clipped":
        assert report['unavailable'] == []
        assert report['clipping_summary']['event_count'] > 0
        assert report['clipping_summary']['full_scale_duty'] > .7
        assert report['clipping_summary']['first_start_seconds'] >= 0
        assert report['clipping_summary']['last_end_seconds'] <= .301
    else:
        assert report['status'] == 'issues_found'
        summary = report['black_frame_summary']
        assert summary['event_count'] == 1
        assert (summary['first_start_seconds'], summary['last_end_seconds'], summary['total_black_seconds']) == (0, .2, .2)


def test_quality_phase_cancellation_preserves_prefix_without_completing_current_phase(tmp_path):
    repository = ProductionRepository(tmp_path / "studio.sqlite", tmp_path / "library")
    plan = CreationPlan(id="plan", workspace_id="local", goal="测试", name="质检", target_count=1, language="zh-CN",
        aspect_ratio="16:9", duration_target_seconds=2, knowledge_references=[], source_references=[], selected_voice=None,
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="isolated")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="质检", scenario="knowledge_explanation", stage="review")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    actor = CreationExecutionActor("local", "member")
    store = ArtifactStore(tmp_path / "artifacts")
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: "ready" if current == actor else "unauthorized", temporary_root=tmp_path / "worker")
    seed_source(repository, store)
    calls = []

    def worker(context, request, temporary_root):
        calls.append(request["phase"])
        if request["phase"] == "black_frames":
            context.cancel_event.set()
        return metadata_result(request)

    runtime = build_metadata_runtime(tmp_path, service, worker)
    try:
        check_id = service.start(runtime, actor, "local", "task", force=True)
        assert runtime.run_once()
        assert runtime.run_once()
        record = checks.get_check("local", "task", check_id)
        assert list(record["evidence"]["_phases"]) == ["metadata"]
        assert record["evidence"]["_worker_result"]["phase"] == "black_frames"
        assert record["check_state"] == "failed" and record["result_status"] is None
        assert repository.get_job_run("local", f"job_quality_{check_id}").status == "cancelled"
        with repository._connect() as conn:
            units = [tuple(row) for row in conn.execute("SELECT unit_key,state FROM device_work_requests WHERE owner_id=? ORDER BY unit_key", (f"job_quality_{check_id}",))]
            assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
        assert units == [(f"quality:{check_id}:black_frames", "cancelled"), (f"quality:{check_id}:metadata", "done")]
        assert calls == ["metadata", "black_frames"]
    finally:
        assert runtime.stop(1)


def test_black_event_stream_keeps_all_totals_and_bounds_diagnostics_and_report():
    from app.quality_checks import (detect_black_frames, QualityCheckError, QualityFinding,
        QualityReport, quality_report_evidence, fit_quality_result, _BlackFrameLines, _clipping_summary)
    import subprocess

    count = 2051
    raw = b"codec ready\n" + b"".join(
        f"[blackdetect @ 0x1] black_start:{2 + i * .4:.3f} black_end:{2 + i * .4 + .2:.3f} black_duration:0.2\n".encode()
        for i in range(count))

    class Context:
        budget = replace(fixture_budget(), max_stderr_bytes=16)
        def run(self, command, timeout, *, stderr_consumer):
            for offset in range(0, len(raw), 13):
                stderr_consumer(raw[offset:offset+13])
            return subprocess.CompletedProcess(command, 0, b"", b"")

    inspected = detect_black_frames("declared.mp4", timeline_origin_seconds=2, context=Context())
    summary = inspected.summary
    assert inspected.diagnostic_bytes == len(b"codec ready\n")
    assert summary['event_count'] == count
    assert summary['total_black_seconds'] == pytest.approx(count * .2)
    assert (summary['first_start_seconds'], summary['last_end_seconds']) == (0, (count-1)*.4+.2)
    assert len(inspected.findings) < count and summary['findings_truncated']
    assert len(summary['events']) <= summary['event_list_limit']

    clipped = [QualityFinding('clipping', 1, 1.2, 'declared clipped samples')]
    tail = QualityFinding('truncation', 898, 900, 'decoded tail gap')
    report = QualityReport("declared.mp4", tuple(inspected.findings + clipped + [tail]), (), 900, "declared-port",
        clipping_summary=_clipping_summary(clipped, .2), black_frame_summary=summary)
    original = {"evidence": quality_report_evidence(report), "receipt": {"check_id": "original", "render_ref": "a"*64},
        "worker_sha256": "b"*64}
    bounded = fit_quality_result(original, 2200)
    assert len(json.dumps(bounded, ensure_ascii=False).encode()) <= 2200
    assert bounded['receipt'] == original['receipt'] and bounded['worker_sha256'] == original['worker_sha256']
    assert bounded['evidence']['status'] == 'issues_found' and not bounded['evidence']['is_clean']
    assert bounded['evidence']['black_frame_summary']['event_count'] == count
    assert bounded['evidence']['black_frame_summary']['total_black_seconds'] == pytest.approx(count * .2)
    assert bounded['evidence']['clipping_summary']['event_count'] == 1
    assert any(item['kind']=='truncation' and item['detail']==tail.detail for item in bounded['evidence']['findings'])
    kept = sum(f['kind']=='black_frame' for f in bounded['evidence']['findings'])
    assert bounded['evidence']['black_frame_summary']['findings_truncated_count'] == count-kept
    assert QualityReport('declared', (), (), 900, 'declared-port', black_frame_summary=summary).status == 'issues_found'
    with pytest.raises(ValueError, match='不能截断'):
        fit_quality_result(original, 1)
    negative_clock = _BlackFrameLines(16, origin=-1, example_bytes=1024)
    negative_clock.feed(b'[blackdetect @ 0x1] black_start:-1 black_end:-.5 black_duration:5e-1\n')
    assert (negative_clock.events.examples[0].start_seconds, negative_clock.events.examples[0].end_seconds) == (0, .5)
    lines = _BlackFrameLines(16, example_bytes=1024)
    with pytest.raises(QualityCheckError, match='诊断输出'):
        lines.feed(b'genuine decoder error\n')
    with pytest.raises(QualityCheckError, match='诊断输出'):
        _BlackFrameLines(16, example_bytes=1024).feed(b'metadata: black_start:0 black_end:1 black_duration:1\n')
    with pytest.raises(QualityCheckError, match='invalid interval'):
        _BlackFrameLines(16, example_bytes=1024).feed(b'[blackdetect @ 0x1] black_start:broken\n')
    with pytest.raises(QualityCheckError, match='bounded protocol'):
        _BlackFrameLines(16, example_bytes=1024).feed(b'9'*4096)
    class TimedOut(Context):
        def run(self, command, timeout, **kwargs):
            raise subprocess.TimeoutExpired(command, timeout)
    with pytest.raises(QualityCheckError, match='检查超时'):
        detect_black_frames('declared.mp4', context=TimedOut())


def test_sealed_phase_envelope_preserves_multiple_bounded_reports():
    from dataclasses import asdict
    from app.quality_worker_runtime import QUALITY_STREAM_PROTOCOL, quality_request_byte_limit
    source = _unit_source(dict(workspace_id='local', task_id='task', timeline_version_id='v1',
        source_ref='fixture.mp4', render_ref='a'*64))
    budget = replace(fixture_budget(), max_report_bytes=2048)
    prefix = {'metadata': {'declared_metadata': 'm'*1200}, 'black_frames': {'declared_summary': 'b'*1200}}
    sealed = {'protocol': QUALITY_STREAM_PROTOCOL, 'budget': asdict(budget), 'source': source, 'check_id': 'original'}
    request = quality_phase_request(sealed, {'_phases': prefix})
    result = metadata_result(request)
    service = object.__new__(QualityInspectionUseCase)
    service.validate_result(request, result)
    assert len(json.dumps(prefix).encode()) > budget.max_report_bytes
    assert len(json.dumps(request).encode()) <= quality_request_byte_limit(request)
    assert quality_request_byte_limit(request) == len(QUALITY_PHASES)*budget.max_report_bytes
    assert quality_request_byte_limit({**request, 'protocol':'quality_phases_v1'}) == budget.max_report_bytes


def test_quality_worker_measurement_records_shape_work_and_eof(tmp_path, monkeypatch):
    """Declared decoder ports, not a measured device capacity claim."""
    from dataclasses import asdict
    from types import SimpleNamespace
    from app import quality_worker
    from app.quality_worker_runtime import (QUALITY_STREAM_PROTOCOL, file_identity,
        phase_measurement, validate_phase_measurement)
    path = tmp_path / 'declared-media'
    path.write_bytes(b'declared')
    source = {**_unit_source(dict(workspace_id='local', task_id='task', timeline_version_id='v1',
        source_ref='fixture', render_ref=unit_hash(path))), 'path': str(path),
        'size_bytes': path.stat().st_size, 'file_identity': file_identity(path)}
    sealed = dict(protocol=QUALITY_STREAM_PROTOCOL, source=source, check_id='measurement',
        budget=asdict(fixture_budget()), thresholds={})
    metadata = {'streams': [{'codec_type': 'video', 'codec_name': 'ffv1', 'width': 64,
        'height': 48, 'pix_fmt': 'yuv420p'}, {'codec_type': 'audio', 'codec_name': 'pcm_s16le',
        'channels': 1, 'sample_rate': '48000'}]}
    command = dict(tool='ffprobe', pcm_s16le_mono=False, stdout_bytes=701,
        stderr_bytes=0, elapsed_seconds=.2, complete=True)
    class Context:
        def __init__(self, *args, **kwargs):
            self.processing = [dict(command)]
        def check(self):
            pass
        def run(self, argv, *args, **kwargs):
            entries = argv[argv.index('-show_entries') + 1]
            assert all(key in entries for key in ('codec_name', 'width', 'height', 'pix_fmt', 'channels'))
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode(), stderr=b'')
    monkeypatch.setattr(quality_worker, 'QualityWorkerContext', Context)
    monkeypatch.setattr(quality_worker, 'worker_identity', lambda **kwargs: fixture_budget().worker_sha256)
    times = iter([100., 100.01, 101.])
    monkeypatch.setattr(quality_worker, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    request = quality_phase_request(sealed, {'_phases': {}})
    response = quality_worker.inspect_request({**request, 'deadline_monotonic': 110.})
    measured = response['measurement']
    assert measured['worker_elapsed_seconds'] == 1
    assert measured['commands'][0]['elapsed_seconds'] == .2
    assert measured['media_streams'] == metadata['streams']
    assert measured['workload']['full_sha_bytes'] == path.stat().st_size
    assert measured['backend']['worker_sha256'] == response['worker_sha256']
    assert 'execution' not in measured
    validate_phase_measurement(request, response)
    # A decoder failure represented as unavailable must retain that verdict;
    # it must neither fail the whole check nor publish a throughput sample.
    times = iter([102., 102.01])
    monkeypatch.setattr(quality_worker, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(quality_worker.quality_checks, 'inspect_quality_phase',
        lambda *args, **kwargs: {'findings': [], 'unavailable': [{'kind': 'black_frame', 'reason': 'declared decode failure'}]})
    command['complete'] = False
    black_request = quality_phase_request(sealed, {'_phases': {'metadata': response['data']}})
    unavailable = quality_worker.inspect_request({**black_request, 'deadline_monotonic': 110.})
    assert unavailable['data']['unavailable']
    assert 'measurement' not in unavailable
    command['complete'] = True
    prefix = {'metadata': response['data'], 'black_frames': {}}
    frame_request = quality_phase_request(sealed, {'_phases': prefix})
    frames = {'video': {'frame_count': 3, 'complete': True},
              'audio': {'sample_count': 14400, 'sample_rate': 48000, 'complete': True}}
    frame_result = {'data': {'decoded_streams': frames}}
    frame_result['measurement'] = phase_measurement(frame_request, frame_result,
        commands=[command], elapsed_seconds=1.)
    assert frame_result['measurement']['workload']['decoded_streams'] == frames
    assert 'stdout_bytes' not in frame_result['measurement']['workload']
    validate_phase_measurement(frame_request, frame_result)
    frame_result['measurement']['commands'] = [{**command, 'complete': False}]
    with pytest.raises(ValueError, match='未完整执行'):
        validate_phase_measurement(frame_request, frame_result)
    prefix['truncation'] = {'decoded_streams': frames}
    pcm_request = quality_phase_request(sealed, {'_phases': prefix})
    passes = [dict(sample_count=14400, sample_rate=48000, channels=1,
        sample_format='s16le', complete=True)] * 2
    pcm_result = {'data': {'decoded_pcm': {'passes': passes}}}
    pcm_measurement = phase_measurement(pcm_request, pcm_result, commands=[], elapsed_seconds=1.)
    assert pcm_measurement['workload']['pcm_passes'] == passes
    assert pcm_measurement['workload']['full_sha_bytes'] == 0


@pytest.mark.parametrize('fail_next', [False, True])
def test_quality_measurement_requires_commit_and_keeps_successful_prefix_on_failure(tmp_path, monkeypatch, fail_next):
    from types import SimpleNamespace
    from app.creation import quality_execution
    from app.quality_worker_runtime import phase_measurement
    repository = ProductionRepository(tmp_path / 'studio.sqlite', tmp_path / 'library')
    plan = CreationPlan(id='plan', workspace_id='local', goal='测量', name='测量', target_count=1,
        language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2, knowledge_references=[],
        source_references=[], selected_voice=None, pronunciation_references=[], approved_asset_count=0,
        approved_clip_count=0, material_rule_summary='', model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint='isolated')
    task = CreationTask(id='task', workspace_id='local', plan_id='plan', name='测量',
        scenario='knowledge_explanation', stage='review')
    repository.confirm_creation_plan(plan, task, verified_owner_user_id='owner')
    actor = CreationExecutionActor('local', 'owner')
    store = ArtifactStore(tmp_path / 'artifacts')
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: 'ready' if current == actor else 'unauthorized', temporary_root=tmp_path / 'worker')
    seed_source(repository, store)
    observations = []
    def command(scheduler, grant, argv, **kwargs):
        request = json.loads(Path(argv[-2]).read_text())
        result = metadata_result(request)
        if fail_next and request['phase'] == 'black_frames':
            return SimpleNamespace(returncode=1, stdout=b'{"error":"declared deadline failure"}')
        result['measurement'] = phase_measurement(request, result, commands=[], elapsed_seconds=.000001)
        return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode())
    monkeypatch.setattr(quality_execution, 'run_granted_command', command)
    def worker(context, request, root):
        result = quality_execution.run_quality_worker(context, request, root)
        observations.append(result['measurement'])
        assert len(service.read_measurements('local', context.grant.owner_id, actor)) == len(observations) - 1
        return result
    runtime = build_metadata_runtime(tmp_path, service, worker)
    try:
        check_id = service.start(runtime, actor, 'local', 'task', force=True)
        job_id = f'job_quality_{check_id}'
        assert runtime.run_once() is True
        measured, = service.read_measurements('local', job_id, actor)
        assert measured == observations[0]
        execution = measured['execution']
        assert execution['input_digest'] == repository.get_job_run('local', job_id).input_digest
        assert execution['process_released'] is True
        assert execution['elapsed_seconds'] >= measured['worker_elapsed_seconds']
        with pytest.raises(PermissionError):
            service.read_measurements('local', job_id, CreationExecutionActor('local', 'other'))
        for _ in range(1 if fail_next else len(QUALITY_PHASES) - 1):
            assert runtime.run_once() is True
        assert repository.get_job_run('local', job_id).status == ('failed' if fail_next else 'succeeded')
        assert service.read_measurements('local', job_id, actor) == observations
        assert list(checks.get_check('local', 'task', check_id)['evidence']['_phases']) == (
            ['metadata'] if fail_next else list(QUALITY_PHASES[:-1]))
        with repository._connect() as conn:
            conn.execute('UPDATE device_work_requests SET last_grant_generation=last_grant_generation+1 WHERE id=?',
                (execution['request_id'],))
        assert service.read_measurements('local', job_id, actor) == observations[1:]
    finally:
        assert runtime.stop(1)

def test_failed_quality_phase_continues_original_job_with_sealed_policy_and_prefix(tmp_path, monkeypatch):
    """Real repository/scheduler/owner transaction, declared worker, no media."""
    from app.quality_worker_runtime import phase_measurement
    repository = ProductionRepository(tmp_path / 'studio.sqlite', tmp_path / 'library')
    plan = CreationPlan(id='plan', workspace_id='local', goal='恢复', name='恢复', target_count=1,
        language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2, knowledge_references=[],
        source_references=[], selected_voice=None, pronunciation_references=[], approved_asset_count=0,
        approved_clip_count=0, material_rule_summary='', model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint='isolated')
    task = CreationTask(id='task', workspace_id='local', plan_id='plan', name='恢复',
        scenario='knowledge_explanation', stage='review')
    repository.confirm_creation_plan(plan, task, verified_owner_user_id='owner')
    actor = CreationExecutionActor('local', 'owner')
    store = ArtifactStore(tmp_path / 'artifacts')
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: 'ready' if current == actor else 'unauthorized', temporary_root=tmp_path / 'worker')
    seed_source(repository, store)
    calls, failures = [], [True, True]
    def worker(context, request, root):
        calls.append((request['phase'], request['budget']['max_timeout_seconds']))
        assert quality_phase_request(request, {'_phases': request['checkpoints']}) == request
        if request['phase'] == 'black_frames' and failures:
            failures.pop()
            raise RuntimeError('declared finite preparation timeout')
        result = metadata_result(request)
        result['measurement'] = phase_measurement(request, result, commands=[], elapsed_seconds=.001)
        grant = context.grant
        result['measurement']['execution'] = dict(request_id=grant.request_id, generation=grant.generation,
            attempt_number=grant.attempt_number, unit_key=grant.unit_key, input_digest=grant.input_digest,
            process_released=True, elapsed_seconds=.002)
        return result
    runtime = build_metadata_runtime(tmp_path, service, worker)
    try:
        check_id = service.start(runtime, actor, 'local', 'task', force=True)
        job_id = f'job_quality_{check_id}'
        assert runtime.run_once() is True
        assert runtime.run_once() is True
        job, _, record, sealed = service.read('local', job_id)
        original_prefix = record['evidence']['_phases']
        with repository._connect() as conn:
            failed = dict(conn.execute("SELECT * FROM device_work_requests WHERE owner_id=? AND state='failed'", (job_id,)).fetchone())
        assert service.continued_phase_receipt('local', job_id, failed['id'], actor) is None
        with pytest.raises(ValueError, match='当前设备策略来源'):
            service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor)
        profile = dict(profile_id='declared-profile', revision=None, digest='a'*64, source_kind='device_profile')
        service.current_policy = lambda: {'budget': service.budget, 'profile': profile}
        with pytest.raises(ValueError, match='当前运行设备档'):
            service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor)
        profile['revision'] = 2
        service.budget = replace(service.budget, worker_sha256='4'*64)
        with pytest.raises(ValueError, match='算法或工具'):
            service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor)
        assert runtime.stop(1)
        service.budget = replace(fixture_budget(), max_timeout_seconds=12.)
        runtime = build_metadata_runtime(tmp_path, service, worker)
        first_successor = service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor)
        assert service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor) == first_successor
        resumed = repository.get_job_run('local', job_id)
        assert (resumed.status, resumed.input_digest, resumed.attempt) == ('queued', job.input_digest, job.attempt)
        current = checks.get_check('local', 'task', check_id)
        assert current['evidence']['_request'] == sealed
        assert current['evidence']['_phases'] == original_prefix
        assert runtime.run_once() is True  # This explicitly continued attempt also fails.
        assert repository.get_job_run('local', job_id).status == 'failed'
        assert runtime.stop(1)
        profile.update(revision=3, digest='b'*64)
        service.budget = replace(service.budget, max_timeout_seconds=14.)
        runtime = build_metadata_runtime(tmp_path, service, worker)
        successor = service.continue_failed_phase(runtime.scheduler, 'local', job_id, first_successor, actor)
        assert successor != first_successor
        for _ in QUALITY_PHASES[1:]:
            assert runtime.run_once() is True
        final_job, _, final, original = service.read('local', job_id)
        assert final_job.status == 'succeeded'
        assert final_job.input_digest == job.input_digest
        assert original == sealed
        assert final['evidence']['_phases']['metadata'] == original_prefix['metadata']
        measurements = service.read_measurements('local', job_id, actor)
        assert [item['phase'] for item in measurements] == list(QUALITY_PHASES)
        assert measurements[0]['execution']['request_id'] != first_successor
        assert measurements[1]['execution']['request_id'] == successor
        assert [phase for phase, _ in calls].count('metadata') == 1
        assert calls == [('metadata', 10.), ('black_frames', 10.), ('black_frames', 12.),
                         ('black_frames', 14.), ('truncation', 14.), ('clipping', 14.), ('finalize', 14.)]
        assert service.continue_failed_phase(runtime.scheduler, 'local', job_id, failed['id'], actor) == first_successor
        with repository._connect() as conn:
            assert dict(conn.execute('SELECT * FROM device_work_requests WHERE id=?', (failed['id'],)).fetchone()) == failed
            assert conn.execute("SELECT count(*) FROM job_runs WHERE kind=?", (QUALITY_KIND,)).fetchone()[0] == 1
            events = conn.execute("SELECT id,payload_json FROM task_events WHERE job_id=? AND event_type='quality_check_terminal'", (job_id,)).fetchall()
            assert len(events) == 3
            assert len({row['id'] for row in events}) == 3
            assert [json.loads(row['payload_json'])['status'] for row in events].count('failed') == 2
            before_replay = list(conn.iterdump())
        def forbidden(*args, **kwargs):
            raise AssertionError('receipt replay must not consult current policy or media')
        service.budget = None
        service.current_policy = forbidden
        monkeypatch.setattr(service, 'verify_source', forbidden)
        receipt = service.continued_phase_receipt('local', job_id, failed['id'], actor)
        assert receipt == {'check_id': check_id, 'job_id': job_id, 'request_id': first_successor}
        assert service.continue_failed_phase(None, 'local', job_id, failed['id'], actor) == first_successor
        with pytest.raises(PermissionError):
            service.continued_phase_receipt('local', job_id, failed['id'], CreationExecutionActor('local', 'other'))
        with repository._connect() as conn:
            assert list(conn.iterdump()) == before_replay
            refs = conn.execute('SELECT source_refs_json FROM device_work_requests WHERE id=?', (first_successor,)).fetchone()[0]
            conn.execute("UPDATE device_work_requests SET source_refs_json='[]' WHERE id=?", (first_successor,))
        with pytest.raises(PermissionError, match='固定来源'):
            service.continued_phase_receipt('local', job_id, failed['id'], actor)
        with repository._connect() as conn:
            conn.execute('UPDATE device_work_requests SET source_refs_json=? WHERE id=?', (refs, first_successor))
            conn.execute('UPDATE device_work_requests SET last_grant_generation=last_grant_generation+1 WHERE id=?', (failed['id'],))
        with pytest.raises(ValueError, match='原失败尝试回执'):
            service.continued_phase_receipt('local', job_id, failed['id'], actor)
    finally:
        assert runtime.stop(1)


def test_quality_continuation_projection_is_read_only_and_declares_conditions(tmp_path, monkeypatch):
    from app import quality_worker_runtime, quality_worker
    repository = ProductionRepository(tmp_path / 'studio.sqlite', tmp_path / 'library')
    plan = CreationPlan(id='plan', workspace_id='local', goal='读取', name='读取', target_count=1,
        language='zh-CN', aspect_ratio='16:9', duration_target_seconds=2, knowledge_references=[],
        source_references=[], selected_voice=None, pronunciation_references=[], approved_asset_count=0,
        approved_clip_count=0, material_rule_summary='', model_snapshot=[], confirmation_points=[],
        blockers=[], preflight_fingerprint='isolated')
    task = CreationTask(id='task', workspace_id='local', plan_id='plan', name='读取',
        scenario='knowledge_explanation', stage='review')
    repository.confirm_creation_plan(plan, task, verified_owner_user_id='owner')
    actor = CreationExecutionActor('local', 'owner')
    store = ArtifactStore(tmp_path / 'artifacts')
    checks = QualityCheckRepository(repository.database_path)
    outputs = OutputObservationUseCase(repository, store, TaskAudio(repository, store, tmp_path), tmp_path, None)
    service = QualityInspectionUseCase(repository, checks, outputs, budget=fixture_budget(),
        verify_identity=lambda current: 'ready' if current == actor else 'unauthorized', temporary_root=tmp_path / 'worker')
    seed_source(repository, store)
    def worker(context, request, root):
        if request['phase'] == 'black_frames':
            raise RuntimeError('declared failure')
        return metadata_result(request)
    runtime = build_metadata_runtime(tmp_path, service, worker)
    try:
        assert service.continuation_projection('local', 'task', actor, runtime_available=True) is None
        check_id = service.start(runtime, actor, 'local', 'task', force=True)
        assert runtime.run_once() is True
        assert runtime.run_once() is True
        with repository._connect() as conn:
            before = list(conn.iterdump())
        def no_hash(*args, **kwargs):
            raise AssertionError('polling must not hash media')
        monkeypatch.setattr(quality_worker_runtime, 'hash_file', no_hash)
        monkeypatch.setattr(quality_worker, 'hash_file', no_hash)
        # No scheduler/recovery port is even accepted by this read method.
        assert service.continuation_projection('local', 'task', None)['reason_code'] == 'actor_unavailable'
        assert service.continuation_projection('local', 'task', actor)['reason_code'] == 'runtime_unavailable'
        def read(current=actor):
            return service.continuation_projection('local', 'task', current, runtime_available=True)
        assert read(CreationExecutionActor('local', 'other'))['reason_code'] == 'original_actor_required'
        assert read()['reason_code'] == 'policy_unavailable'
        profile = dict(profile_id='declared-profile', revision=1, digest='a'*64, source_kind='device_profile')
        service.current_policy = lambda: {'budget': service.budget, 'profile': profile}
        ready = read()
        assert ready == dict(job_id=f'job_quality_{check_id}', failed_request_id=ready['failed_request_id'],
            phase='black_frames', completed_phases=['metadata'], can_request=True,
            reason_code=None, release_check='on_submit')
        with repository._connect() as conn:
            assert list(conn.iterdump()) == before
        service.budget = replace(service.budget, ffmpeg_sha256='4'*64)
        assert read()['reason_code'] == 'algorithm_changed'
        service.budget = fixture_budget()
        with repository._connect() as conn:
            conn.execute("UPDATE device_work_requests SET state='waiting' WHERE id=?", (ready['failed_request_id'],))
        assert read()['reason_code'] == 'request_in_flight'
        with repository._connect() as conn:
            conn.execute("UPDATE device_work_requests SET state='failed' WHERE id=?", (ready['failed_request_id'],))
        sealed = checks.get_check('local', 'task', check_id)['evidence']['_request']
        # Identity/stat observes replacement; it does not open or rehash the film.
        source_path = Path(sealed['source']['path'])
        source_path.write_bytes(b'changed declared source')
        assert read()['reason_code'] == 'source_changed'
    finally:
        assert runtime.stop(1)
