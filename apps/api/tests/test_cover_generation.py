from __future__ import annotations

import hashlib
import builtins
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError

from app.cover_generation import (
    COVER_HEIGHT,
    COVER_WIDTH,
    CoverGenerationRequest,
    CoverGenerationService,
    CoverSafeArea,
    _fit_title,
    _layout_cover,
    unconfigured_image_generation_capability,
)
from app.schemas import CreationTaskExactEditRequest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS cover typography")
def test_cover_title_balances_chinese_lines():
    _, lines = _fit_title("本地创作，数据更清楚", 848, 1400)
    assert lines == ["本地创作，", "数据更清楚"]


@pytest.mark.parametrize("title_position", ["center", "bottom"])
def test_cover_position_parameter_keeps_exact_request_and_renderer_in_sync(title_position):
    payload = CreationTaskExactEditRequest(
        operation_type="GenerateCoverFromFrame",
        title="布局核验",
        frame_time_seconds=0.5,
        title_position=title_position,
    )
    request = CoverGenerationRequest("source.mp4", "cover.png", 0.5, payload.title,
                                     title_position=payload.title_position)
    assert payload.model_dump()["title_position"] == title_position
    assert request.title_position == title_position
    assert CoverGenerationService._validated_request(request) == "png"


def test_cover_position_defaults_to_bottom_and_rejects_unknown_values(monkeypatch, tmp_path):
    payload = CreationTaskExactEditRequest(operation_type="GenerateCoverFromFrame", title="标题", frame_time_seconds=0)
    assert payload.title_position == "bottom"
    assert CoverGenerationRequest("source.mp4", "cover.png", 0, "标题").title_position == "bottom"
    with pytest.raises(ValidationError):
        CreationTaskExactEditRequest(operation_type="GenerateCoverFromFrame", title="标题", frame_time_seconds=0, title_position="middle")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"controlled parameter-validation input")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("invalid position invoked a media tool"))
    with pytest.raises(ValueError, match="标题位置"):
        CoverGenerationService(tmp_path).generate(CoverGenerationRequest(source, "cover.png", 0, "标题", title_position="middle"))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS cover typography")
def test_cover_title_positions_render_distinct_pngs_inside_safe_area_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("layout invoked a media tool"))
    base = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), "red")
    frame = tmp_path / "synthetic-frame.png"
    base.save(frame)
    area = CoverSafeArea()
    boxes = {}
    outputs = {}
    for position in ("center", "bottom"):
        output = tmp_path / f"{position}.png"
        _layout_cover(frame, output, title="真实中文布局核验", safe_area=area,
                      output_format="png", title_position=position)
        with Image.open(output) as image:
            assert image.format == "PNG" and image.size == base.size
            bounds = ImageChops.difference(base, image.convert("RGB")).getbbox()
            assert bounds is not None
            left, top, right, bottom = bounds
            assert left >= area.left and right <= COVER_WIDTH - area.right + 1
            assert top >= area.top and bottom <= COVER_HEIGHT - area.bottom + 1
            boxes[position] = bounds
        outputs[position] = output.read_bytes()
    safe_center = (area.top + COVER_HEIGHT - area.bottom) / 2
    assert abs((boxes["center"][1] + boxes["center"][3]) / 2 - safe_center) <= 2
    assert abs(boxes["bottom"][3] - (COVER_HEIGHT - area.bottom)) <= 2
    assert boxes["center"][3] < boxes["bottom"][1]
    assert outputs["center"] != outputs["bottom"]
    default = tmp_path / "default.png"
    _layout_cover(frame, default, title="真实中文布局核验", safe_area=area, output_format="png")
    assert default.read_bytes() == outputs["bottom"]
    assert Image.open(frame).tobytes() == base.tobytes()


def _write_two_scene_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=360x640:r=25:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=360x640:r=25:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_real_ffmpeg_selects_requested_frame_and_renders_visible_chinese_title(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)
    source_hash = _sha256(source)

    generated = CoverGenerationService(output_root).generate(
        CoverGenerationRequest(
            source_video_path=source,
            output_ref="artifacts/task-1/version-3/cover.png",
            frame_time_seconds=1.25,
            title="别只讲概念",
            safe_area=CoverSafeArea(left=80, top=140, right=80, bottom=180),
        )
    )

    cover = Path(generated.output_path)
    assert cover.is_file() and cover.stat().st_size > 0
    assert _sha256(source) == source_hash
    with Image.open(cover) as image:
        assert image.format == "PNG"
        assert image.size == (COVER_WIDTH, COVER_HEIGHT)
        top_pixel = image.convert("RGB").getpixel((100, 100))
        assert top_pixel[2] > top_pixel[0] * 3
        assert image.convert("RGB").getpixel((100, 1600)) == (24, 25, 23)
        title_region = image.convert("RGB").crop((80, 1200, 1000, 1740))
        colors = title_region.getcolors(maxcolors=title_region.width * title_region.height)
        assert colors is not None
        bright_pixels = sum(
            count for count, (red, green, blue) in colors
            if red > 225 and green > 225 and blue > 225
        )
        assert bright_pixels > 500

    evidence = generated.evidence
    assert evidence.input_digest == source_hash
    assert evidence.input_width == 360
    assert evidence.input_height == 640
    assert evidence.input_duration_seconds == pytest.approx(2.0, abs=0.1)
    assert evidence.engine == "ffmpeg"
    assert evidence.engine_version.startswith("ffmpeg version")
    assert evidence.layout_engine == "Pillow"
    assert evidence.font_path
    if sys.platform == "darwin":
        assert "PingFang" in evidence.font_family
    assert evidence.parameters["frame_time_seconds"] == 1.25
    assert evidence.parameters["safe_area"] == {
        "left": 80,
        "top": 140,
        "right": 80,
        "bottom": 180,
    }
    assert evidence.output_sha256 == _sha256(cover)
    assert evidence.output_size_bytes == cover.stat().st_size
    assert (evidence.output_width, evidence.output_height) == (1080, 1920)
    assert evidence.output_format == "png"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_jpeg_output_is_nonempty_and_has_matching_evidence(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)

    generated = CoverGenerationService(output_root).generate(
        CoverGenerationRequest(
            source_video_path=source,
            output_ref="artifacts/task-1/version-3/cover.jpg",
            frame_time_seconds=0.25,
            title="真实运行",
        )
    )

    with Image.open(generated.output_path) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1920)
    assert generated.evidence.output_format == "jpeg"
    assert generated.evidence.output_size_bytes > 0


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_rejects_invalid_frame_times_without_publishing(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    _write_two_scene_video(source)
    service = CoverGenerationService(output_root)

    with pytest.raises(ValueError, match="非负有限数"):
        service.generate(
            CoverGenerationRequest(source, "covers/negative.png", -0.1, "标题")
        )
    with pytest.raises(ValueError, match="早于成片结束"):
        service.generate(
            CoverGenerationRequest(source, "covers/after-end.png", 2.0, "标题")
        )
    assert not (output_root / "covers").exists()


def test_rejects_source_and_output_paths_outside_controlled_root(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    outside_source = tmp_path / "outside.mp4"
    outside_source.write_bytes(b"not read")
    service = CoverGenerationService(output_root)

    with pytest.raises(ValueError, match="受控任务输出目录"):
        service.generate(
            CoverGenerationRequest(outside_source, "covers/cover.png", 0, "标题")
        )
    inside_source = output_root / "current-timeline.mp4"
    inside_source.write_bytes(b"not probed")
    for output_ref in ("../escape.png", "/tmp/escape.png", "covers\\escape.png"):
        with pytest.raises(ValueError, match="产物引用"):
            service.generate(
                CoverGenerationRequest(inside_source, output_ref, 0, "标题")
            )


def test_rejects_invalid_safe_area_before_running_ffmpeg(tmp_path):
    output_root = tmp_path / "task-output"
    output_root.mkdir()
    source = output_root / "current-timeline.mp4"
    source.write_bytes(b"not probed")

    with pytest.raises(ValueError, match="安全区宽度不足"):
        CoverGenerationService(output_root).generate(
            CoverGenerationRequest(
                source,
                "covers/cover.png",
                0,
                "标题",
                CoverSafeArea(left=600, right=600),
            )
        )


def test_ai_image_generation_is_explicitly_unavailable():
    capability = unconfigured_image_generation_capability()

    assert capability.capability_state == "unavailable"
    assert "ImageGenerationProvider" in capability.failure_reason
    assert "不提供 AI 生图" in capability.impact
    assert "验真" in capability.remediation


def test_cover_owner_cold_import_does_not_load_main():
    result = subprocess.run([sys.executable, '-B', '-c',
        "import sys; import app.creation.cover_use_case; import app.member_permits; "
        "assert 'app.main' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cover_owner_declared_worker_adopts_once_without_parent_file_hashing(tmp_path, monkeypatch):
    """Metadata-only worker/process ports; no native, host, model or main import."""
    previously_loaded = {name for name in sys.modules if name == 'app.main' or name.startswith('app.main.')}
    original_import = builtins.__import__

    def metadata_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'app.main' or name.startswith('app.main.') or name == 'app' and 'main' in (fromlist or ()):
            pytest.fail('metadata cover owner imported application composition')
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', metadata_import)
    import json
    from dataclasses import asdict
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.artifact_store import ArtifactStore
    from app.cover_generation import CoverGenerationEvidence
    from app.creation.contracts import ArtifactRecord, EditIntent, EditProposal, JobRun, TimelineVersion
    from app.creation.cover_use_case import (CoverCommand, CoverStagedReceipt, CoverUnavailable,
        CoverUseCase, file_identity)
    from app.creation.editing.metadata_use_case import MetadataEditUseCase
    from app.creation_execution import CreationExecutionActor
    from app.device_runtime import build_device_runtime
    from app.device_scheduler import ProcessIdentity
    from app.granted_subprocess import GrantedCommandResult
    from app.host_resource_probes import CoverBudget, HostResourceDecision
    from app.member_permits import creation_job_registration
    from app.montage import CreationTaskMontagePlan, creation_task_montage_plan_to_payload
    from app.production import ProductionRepository
    from app.creation.plan_contracts import CreationPlan
    from app.creation.contracts import CreationTask

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("metadata node invoked native"))
    root = tmp_path / "output"
    store = ArtifactStore(root)
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(tmp_path / "cover.sqlite3", library)
    plan = CreationPlan("plan", "workspace", "制作封面", "cover", 1, "zh-CN", "9:16", 15,
        [], [], None, [], 0, 0, "declared metadata fixture", [], [], [], "fixture")
    task = CreationTask("task", "workspace", plan.id, plan.name, "declared-cover-fixture")
    repo.plan_confirmation.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    montage = CreationTaskMontagePlan("montage", "workspace", task.id, "script", "cover", "rendered", [], [], 1.0, 0)
    base = repo.timeline_records.create_timeline_version(TimelineVersion("base", "workspace", task.id, None, 1,
        {"montage": creation_task_montage_plan_to_payload(montage), "subtitle_track_ids": []}, None))
    published = store.publish_bytes("artifacts/task/source/current.mp4", b"declared source metadata")
    source_job = repo.job_records.create_job_run(JobRun("source", "workspace", task.id, "declared_render_metadata",
        "fixture:source", published.sha256))
    repo.job_records.claim_job_run("workspace", source_job.id)
    repo.execution_records.create_artifact_record(ArtifactRecord("source-artifact", "workspace", task.id, "rendered_video",
        published.logical_ref, published.sha256, published.size_bytes, source_job.id, []))
    repo.job_records.finish_job_run("workspace", source_job.id, status="succeeded", output_refs=[published.logical_ref])
    intent = repo.edit_records.create_edit_intent(EditIntent("intent", "workspace", task.id, "选帧封面", "review", [], None,
        "GenerateCoverFromFrame", {}, {}, 1.0, "proposed"))
    operation = {"base_version_id": base.id, "source_video_ref": published.logical_ref,
                 "frame_time_seconds": 0.25, "title": " 声明  封面 ", "title_position": "bottom", "aspect_ratio": "9:16"}
    proposal = repo.edit_records.create_edit_proposal(EditProposal("proposal", "workspace", task.id, intent.id, "封面", [published.logical_ref],
        {"operation_type": "GenerateCoverFromFrame", "payload": operation}, [], {}, []))
    actor = CreationExecutionActor("workspace", "member")
    def verify(value):
        return "ready" if value == actor else "unauthorized"
    metadata = MetadataEditUseCase(repo, store, output_root=root, audio_service=None)
    unconfigured = CoverUseCase(repo, store, metadata, verify_identity=verify, output_root=root)
    with pytest.raises(CoverUnavailable):
        unconfigured.submit(None, actor, task.id, proposal.id, request_id="original-request")
    assert len(repo.job_records.list_job_runs("workspace", task.id)) == 1

    # Explicit declaration for this metadata fixture; these are not host measurements.
    budget = CoverBudget(1024, 4096, 32768, 1024, 1024, 1, 10.0, 1048576, 4, .01, 1048576, 65536)
    owner = CoverUseCase(repo, store, metadata, verify_identity=verify, output_root=root, budget=budget)
    registration = creation_job_registration(repo, render_action=lambda *a: pytest.fail("render invoked"),
        render_is_valid=lambda *a: False, verify_identity=verify, cover=owner)

    class DeclaredResources:
        def admit(self, request):
            assert request.resources["kind"] == "cover_generation"
            return HostResourceDecision(True, request.resources, {"declared_metadata": {"passed": True}})
        def continue_unit(self, grant):
            return True
        def released(self, grant):
            return True

    class DeclaredProcesses:
        active = False
        def current(self):
            return ProcessIdentity(73001, "declared-holder", "declared-boot")
        def children_released(self, children):
            return not self.active
        def recovery_state(self, holder, children):
            return "owned" if self.active else "released"

    processes = DeclaredProcesses()
    access = SimpleNamespace(verify_session=lambda *a: pytest.fail("session invoked"),
                             can_view_device_summary=lambda *a: False)
    runtime = build_device_runtime(database_path=repo.database_path, device_lock_path=tmp_path / "device.lock",
        device_access=access, registrations={"creation_job": registration}, resources=DeclaredResources(),
        processes=processes, activity_source_reliable=True, poll_interval_seconds=budget.runtime_sample_seconds)
    # Wake drives the actual public one-unit dispatcher synchronously in this fixture.
    monkeypatch.setattr(runtime, "wake", runtime.run_once)
    phase = {"name": "parent"}
    hashes = []
    original_describe = ArtifactStore._describe
    original_describe_stream = ArtifactStore._describe_stream
    def record_hash(logical_ref):
        if logical_ref.endswith(("current.mp4", "cover.png")):
            hashes.append((logical_ref, phase["name"]))
            assert phase["name"] == "declared_worker", "parent performed a full media hash"
    def audited_describe(logical_ref, path, check=None):
        record_hash(logical_ref)
        return original_describe(logical_ref, path, check)
    def audited_describe_stream(logical_ref, stream, check=None):
        record_hash(logical_ref)
        return original_describe_stream(logical_ref, stream, check)
    monkeypatch.setattr(ArtifactStore, "_describe", staticmethod(audited_describe))
    monkeypatch.setattr(ArtifactStore, "_describe_stream", staticmethod(audited_describe_stream))
    calls = []
    def declared_worker(scheduler, grant, argv, **limits):
        calls.append(grant.request_id)
        assert argv[-3] == "--cover-worker"
        assert limits["max_output_file_bytes"] == budget.additional_disk_bytes + budget.max_output_bytes + budget.max_receipt_bytes
        scheduler.register_child(grant, ProcessIdentity(73002, "declared-child", "declared-boot"))
        processes.active = True
        control = json.loads(Path(argv[-2]).read_bytes())
        command = CoverCommand.from_payload(control["command"])
        job = repo.job_records.get_job_run(command.workspace_id, control["job_id"])
        phase["name"] = "declared_worker"
        assert hashlib.sha256(store.resolve_file(published.logical_ref).read_bytes()).hexdigest() == command.source_sha256
        output = store.publish_bytes(owner.output_ref(job), b"declared image metadata; not a physical PNG")
        evidence = CoverGenerationEvidence(published.logical_ref, published.sha256, published.size_bytes,
            1.0, 2, 2, "ffmpeg", "declared", "Pillow", "declared", "declared", "declared-font",
            {"frame_time_seconds": .25, "title": "声明 封面", "safe_area": asdict(CoverSafeArea()),
             "title_position": "bottom", "canvas": {"width": 1080, "height": 1920}},
            output.logical_ref, output.sha256, output.size_bytes, 1080, 1920, "png")
        receipt = CoverStagedReceipt(command, job.id, evidence, datetime.now(timezone.utc),
            file_identity(store.resolve_file(output.logical_ref)))
        store.publish_bytes(owner.receipt_ref(job), json.dumps(receipt.payload(), ensure_ascii=False).encode())
        phase["name"] = "parent"
        processes.active = False
        return GrantedCommandResult(0, b"", b"")
    monkeypatch.setattr("app.granted_subprocess.run_granted_command", declared_worker)
    try:
        result = owner.submit(runtime, actor, task.id, proposal.id, request_id="original-request")
        again = owner.submit(runtime, actor, task.id, proposal.id, request_id="original-request")
        assert result.operation.id == again.operation.id
        assert result.version.id == again.version.id
        assert repo.task_records.get_creation_task("workspace", task.id).current_timeline_version_id == result.version.id
        assert result.job.status == "succeeded" and len(calls) == 1
        assert result.job.idempotency_key == "cover:proposal"
        assert result.job.input_digest == hashlib.sha256(json.dumps(operation, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        assert len(repo.job_records.list_job_runs("workspace", task.id)) == 2
        assert len(repo.edit_records.list_edit_operations("workspace", task.id)) == 1
        states = repo.job_records.list_creation_job_request_states("workspace", result.job.id)
        assert len(states) == 1 and states[0]["id"] == calls[0] and states[0]["state"] == "done"
        runtime.scheduler.reconcile_released()
        assert hashes and all(label == "declared_worker" for _, label in hashes)
        assert {name for name in sys.modules if name == 'app.main' or name.startswith('app.main.')} == previously_loaded
    finally:
        runtime.stop(timeout_seconds=1)


def test_cover_confirmation_http_budget_refusal_and_adopted_replay_are_metadata_only(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import socket
    import threading
    import wave
    import httpx
    from fastapi import HTTPException
    from app import main
    from app.creation.contracts import EditIntent, EditProposal, EditOperation, TimelineVersion
    from app.creation.cover_use_case import CoverUseCase, CoverUnavailable
    from app.creation_execution import CreationExecutionActor
    from app.creation_task_responses import CreationTaskResponseAdapter
    from app.device_runtime import DeviceRuntime
    from app.task_edit_routes import CoverConfirmationHttpAdapter
    from test_metadata_edit_use_case import metadata_state

    def forbidden(*args, **kwargs):
        pytest.fail('cover confirmation metadata forbids media, source verification and execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(threading.Thread, 'start', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(Image, 'open', forbidden)
    monkeypatch.setattr(Image, 'new', forbidden)
    monkeypatch.setattr(Image.Image, 'save', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    for name in ('start', 'run_once', 'wake'):
        monkeypatch.setattr(DeviceRuntime, name, forbidden)
    for name in ('_build_device_components', '_creation_runtime', '_start_device_runtime',
                 'run_task_turn', 'run_plan_turn', '_device_operation_budget'):
        monkeypatch.setattr(main, name, forbidden)
    state = metadata_state(tmp_path)
    task = state.task
    actor = CreationExecutionActor(task.workspace_id, state.member.member_id)
    declared = []
    def identity(value):
        assert value is actor
        declared.append('identity')
        return 'ready'
    owner = CoverUseCase(state.repo, state.store, state.case, verify_identity=identity,
        output_root=tmp_path / 'cover-output', budget=None)
    codec = CreationTaskResponseAdapter(repository_for=lambda: state.repo, person_render_media_path=forbidden)
    intent = EditIntent('cover-http-intent', task.workspace_id, task.id, '声明封面', 'subtitle_cover', [], None,
        'GenerateCoverFromFrame', {}, {'request_source': {'source_facts': {'metadata_only': True},
            'action': {'operation_type': 'GenerateCoverFromFrame', 'frame_time_seconds': 0.0,
                'title': '声明封面', 'title_position': 'bottom'}}}, 1.0, 'interpreted')
    original = {'base_version_id': state.base.id, 'frame_time_seconds': 0.0, 'title': '声明封面'}
    proposal = EditProposal('cover-http-proposal', task.workspace_id, task.id, intent.id, '声明封面',
        [f'timeline://{task.id}/{state.base.id}'], {'operation_type': 'GenerateCoverFromFrame', 'payload': original},
        [], {'metadata_only': True}, [])
    state.repo.edit_records.create_edit_intent(intent, source=None)
    state.repo.edit_records.create_edit_proposal(proposal)
    request = object()
    trace = []
    def open_task(value, workspace_id, task_id):
        assert value is request and (workspace_id, task_id) == (task.workspace_id, task.id)
        trace.append('open')
    def require_actor(value, workspace_id):
        assert value is request and workspace_id == task.workspace_id
        trace.append('actor')
        return actor
    def owner_for():
        trace.append('owner')
        return owner
    adapter = CoverConfirmationHttpAdapter(repository_for=lambda: state.repo,
        require_automation_task_open=open_task, require_permit_actor=require_actor,
        cover_use_case_for=owner_for, exact_edit_source_facts=forbidden, creation_runtime_for=forbidden,
        montage_response=codec.creation_task_montage_response, topic_failure_response=main._topic_failure_response)
    def facts():
        with state.repo._connect() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr) for name in tables}
    before = facts()
    with pytest.raises(HTTPException) as refused:
        adapter.confirm_cover_response(task, proposal, request, request_id='declared-cover-request')
    assert refused.value.status_code == 503 and isinstance(refused.value.__cause__, CoverUnavailable)
    assert refused.value.detail == {'error': 'cover_budget_unavailable', 'reason': str(refused.value.__cause__),
        'impact': '当前时间线和已有成片保持不变。', 'repair_action': '先完成独立封面资源标定。', 'retryable': False}
    assert trace == ['open', 'actor', 'owner'] and declared == ['identity']
    assert facts() == before and state.repo.job_records.list_job_runs(task.workspace_id, task.id) == []
    # Declare adopted history through real record writers, not cover generation.
    operation_id = 'declared-cover-operation'
    snapshot = {**state.base.snapshot, 'montage': {**state.base.snapshot['montage'],
        'message': '声明的封面采用版本'}}
    version = TimelineVersion('declared-cover-result', task.workspace_id, task.id, state.base.id, 2,
        snapshot, operation_id)
    state.repo.timeline_records.create_timeline_version(version)
    state.repo.edit_records.transition_edit_proposal(task.workspace_id, task.id, proposal.id, status='confirmed')
    now = datetime.now(timezone.utc)
    operation = EditOperation(operation_id, task.workspace_id, task.id, proposal.id, 'GenerateCoverFromFrame',
        {**original, 'title_position': 'bottom'}, state.base.id, version.id, 'applied', created_at=now, applied_at=now)
    state.repo.edit_records.create_edit_operation(operation)
    monkeypatch.setattr(owner, 'require_budget', forbidden)
    before = facts()
    trace.clear()
    for _ in range(2):
        response = adapter.confirm_cover_response(task, proposal, request, request_id='declared-cover-request')
        assert response.operation.id == operation.id and response.operation.result_version_id == version.id
        assert response.montage.id == state.plan.id and response.montage.message == '声明的封面采用版本'
        assert response.narration is None
        assert facts() == before
    assert trace == ['open', 'actor', 'owner'] * 2 and declared == ['identity'] * 3
    assert state.repo.job_records.list_job_runs(task.workspace_id, task.id) == []
