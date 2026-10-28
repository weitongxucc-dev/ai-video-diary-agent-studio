from __future__ import annotations

import hashlib
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


def test_cover_owner_declared_worker_adopts_once_without_parent_file_hashing(tmp_path, monkeypatch):
    """Metadata-only worker/process ports; no native, host, model or main import."""
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
    from app.production import CreationPlan, ProductionRepository
    from app.creation.contracts import CreationTask

    assert "app.main" not in sys.modules
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("metadata node invoked native"))
    root = tmp_path / "output"
    store = ArtifactStore(root)
    library = tmp_path / "library"
    library.mkdir()
    repo = ProductionRepository(tmp_path / "cover.sqlite3", library)
    plan = CreationPlan("plan", "workspace", "制作封面", "cover", 1, "zh-CN", "9:16", 15,
        [], [], None, [], 0, 0, "declared metadata fixture", [], [], [], "fixture")
    task = CreationTask("task", "workspace", plan.id, plan.name, "declared-cover-fixture")
    repo.confirm_creation_plan(plan, task, verified_owner_user_id="member")
    montage = CreationTaskMontagePlan("montage", "workspace", task.id, "script", "cover", "rendered", [], [], 1.0, 0)
    base = repo.create_timeline_version(TimelineVersion("base", "workspace", task.id, None, 1,
        {"montage": creation_task_montage_plan_to_payload(montage), "subtitle_track_ids": []}, None))
    published = store.publish_bytes("artifacts/task/source/current.mp4", b"declared source metadata")
    source_job = repo.create_job_run(JobRun("source", "workspace", task.id, "declared_render_metadata",
        "fixture:source", published.sha256))
    repo.claim_job_run("workspace", source_job.id)
    repo.execution_records.create_artifact_record(ArtifactRecord("source-artifact", "workspace", task.id, "rendered_video",
        published.logical_ref, published.sha256, published.size_bytes, source_job.id, []))
    repo.finish_job_run("workspace", source_job.id, status="succeeded", output_refs=[published.logical_ref])
    intent = repo.create_edit_intent(EditIntent("intent", "workspace", task.id, "选帧封面", "review", [], None,
        "GenerateCoverFromFrame", {}, {}, 1.0, "proposed"))
    operation = {"base_version_id": base.id, "source_video_ref": published.logical_ref,
                 "frame_time_seconds": 0.25, "title": " 声明  封面 ", "title_position": "bottom", "aspect_ratio": "9:16"}
    proposal = repo.create_edit_proposal(EditProposal("proposal", "workspace", task.id, intent.id, "封面", [published.logical_ref],
        {"operation_type": "GenerateCoverFromFrame", "payload": operation}, [], {}, []))
    actor = CreationExecutionActor("workspace", "member")
    verify = lambda value: "ready" if value == actor else "unauthorized"
    metadata = MetadataEditUseCase(repo, store, output_root=root, audio_service=None)
    unconfigured = CoverUseCase(repo, store, metadata, verify_identity=verify, output_root=root)
    with pytest.raises(CoverUnavailable):
        unconfigured.submit(None, actor, task.id, proposal.id, request_id="original-request")
    assert len(repo.list_job_runs("workspace", task.id)) == 1

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
    def audited_describe(logical_ref, path):
        if logical_ref.endswith(("current.mp4", "cover.png")):
            hashes.append((logical_ref, phase["name"]))
            assert phase["name"] == "declared_worker", "parent performed a full media hash"
        return original_describe(logical_ref, path)
    monkeypatch.setattr(ArtifactStore, "_describe", staticmethod(audited_describe))
    calls = []
    def declared_worker(scheduler, grant, argv, **limits):
        calls.append(grant.request_id)
        assert argv[-3] == "--cover-worker"
        assert limits["max_output_file_bytes"] == budget.additional_disk_bytes + budget.max_output_bytes + budget.max_receipt_bytes
        scheduler.register_child(grant, ProcessIdentity(73002, "declared-child", "declared-boot"))
        processes.active = True
        control = json.loads(Path(argv[-2]).read_bytes())
        command = CoverCommand.from_payload(control["command"])
        job = repo.get_job_run(command.workspace_id, control["job_id"])
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
        assert repo.get_creation_task("workspace", task.id).current_timeline_version_id == result.version.id
        assert result.job.status == "succeeded" and len(calls) == 1
        assert result.job.idempotency_key == "cover:proposal"
        assert result.job.input_digest == hashlib.sha256(json.dumps(operation, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        assert len(repo.list_job_runs("workspace", task.id)) == 2
        assert len(repo.list_edit_operations("workspace", task.id)) == 1
        states = repo.list_creation_job_request_states("workspace", result.job.id)
        assert len(states) == 1 and states[0]["id"] == calls[0] and states[0]["state"] == "done"
        runtime.scheduler.reconcile_released()
        assert hashes and all(label == "declared_worker" for _, label in hashes)
        assert "app.main" not in sys.modules
    finally:
        runtime.stop(timeout_seconds=1)
