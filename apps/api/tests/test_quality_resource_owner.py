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
from app.production import ArtifactRecord, CreationPlan, CreationTask, JobRun, ProductionRepository, TimelineVersion
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
    repository.create_artifact_record(ArtifactRecord(f"artifact-{version}", "local", "task", "rendered_video", ref,
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
            terminal = repository.get_task_event("local", f"event_{job.id}_quality_terminal")
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
        assert repository.get_task_event("local", f"event_job_quality_{released_result}_quality_terminal").payload == {
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


@pytest.mark.parametrize('outcome', ['complete', 'quota', 'cancel'])
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
            self.stdout, self.stderr = pipe(payload), pipe(b'')
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
    context.budget = replace(fixture_budget(), max_pcm_bytes=100 if outcome == 'quota' else 200)
    context.deadline, context._verified_tools = time.monotonic() + 3, {'ffmpeg'}
    consumed = 0

    def consume(raw):
        nonlocal consumed
        consumed += len(raw)
        if outcome == 'cancel':
            raise worker.QualityWorkerError('cancelled original unit')

    if outcome == 'complete':
        result = context.run(['ffmpeg'], 2, pcm=True, stdout_consumer=consume)
        assert result.stdout == b'' and consumed == len(payload)
        assert not process.killed
    else:
        with pytest.raises(worker.QualityWorkerError, match='输出超过|cancelled'):
            context.run(['ffmpeg'], 2, pcm=True, stdout_consumer=consume)
        assert process.killed
    assert process.stdout.closed and process.stderr.closed


def test_streaming_native_compact_frames_and_pcm_agree_on_tiny_media(tmp_path, granted_media):
    """Two 0.3s synthetic clips through an actual grant, not device calibration."""
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
    request.write_text(json.dumps({'budget': asdict(budget), 'root': str(tmp_path)}))
    program = '''
import json, sys, time
from pathlib import Path
from dataclasses import asdict, replace
from app.quality_worker_runtime import QualityBudget, QualityWorkerContext, QUALITY_PHASES, QUALITY_PHASE_PROTOCOL, quality_phase_request, file_identity
from app.quality_worker import inspect_request, worker_identity
from app.quality_checks import inspect_media_quality, quality_report_evidence
import hashlib
request=json.loads(Path(sys.argv[1]).read_text())
context=QualityWorkerContext(QualityBudget(**request['budget']), time.monotonic()+15)
reports=[]
for name, amplitude in [('normal', .5), ('clipped', 3)]:
    target=Path(request['root'])/(name+'.mkv')
    result=context.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'testsrc=size=64x48:rate=10:duration=0.3','-f','lavfi','-i',
        f'aevalsrc={amplitude}*sin(2*PI*440*t):s=48000:d=0.3',
        '-c:v','ffv1','-threads:v','1','-pix_fmt','yuv420p','-c:a','pcm_s16le',str(target)],3)
    if result.returncode: raise RuntimeError(result.stderr.decode())
    report=quality_report_evidence(inspect_media_quality(target,context=context))
    if name == 'normal':
        with target.open('rb') as source_file:
            sha = hashlib.file_digest(source_file, 'sha256').hexdigest()
        source=dict(workspace_id='fixture', task_id='fixture', timeline_version_id='v1', source_ref='fixture.mkv',
            artifact_id='fixture', render_ref=sha, size_bytes=target.stat().st_size, file_identity=file_identity(target), path=str(target))
        sealed=dict(protocol=QUALITY_PHASE_PROTOCOL, source=source, check_id='synthetic-phase-check', thresholds={},
            budget=asdict(replace(context.budget, worker_sha256=worker_identity())))
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
    reports.append({key:report[key] for key in ('status','unavailable','clipping_summary')})
print(json.dumps(reports))
'''
    with granted_media() as context:
        result = run_granted_command(context.scheduler, context.grant,
            [sys.executable, '-c', program, str(request)], timeout_seconds=20,
            max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
            cancel_event=context.cancel_event)
    assert result.returncode == 0, result.stderr.decode()
    normal, clipped = json.loads(result.stdout)
    assert normal['status'] == 'clean'
    assert clipped['unavailable'] == []
    assert clipped['clipping_summary']['event_count'] > 0
    assert clipped['clipping_summary']['full_scale_duty'] > .7
    assert clipped['clipping_summary']['first_start_seconds'] >= 0
    assert clipped['clipping_summary']['last_end_seconds'] <= .301


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
