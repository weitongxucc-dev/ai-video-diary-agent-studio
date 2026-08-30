from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.models import (
    Episode,
    EpisodeStatus,
    ScriptPackage,
    ScriptSection,
    utc_now,
)
from app.montage import (
    MontageClip,
    MontagePlan,
    MontagePlanner,
    MontageRenderError,
    MontageRenderer,
    MontageRepository,
    NarrationCue,
)
from app.narration import NarrationArtifact
from app.production import (
    MaterialAsset,
    MaterialSegment,
    NarrationArtifactRecord,
    ProductionBatch,
    ProductionBatchItem,
    ProductionProfile,
    ProductionRepository,
)
from app.repository import LEGACY_WORKSPACE_ID, EpisodeRepository


def _episode(workspace_id: str = "workspace_a") -> Episode:
    return Episode(
        id="episode_ready",
        day_number=1,
        episode_date=date(2026, 8, 24),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id=workspace_id,
        script=ScriptPackage(
            id="script_1",
            hook="人工智能工具正在改变团队效率。",
            sections=[
                ScriptSection(
                    id="section_1",
                    title="方法",
                    estimated_seconds=24,
                    content="先识别重复工作，再用自动化工具处理，最后保留人工审核。",
                )
            ],
            title_options=["AI 工具如何提升团队效率"],
            cover_copy="提升效率",
            publish_copy="",
            comment_prompt="",
        ),
    )


def _asset(
    tmp_path: Path,
    *,
    with_metadata: bool = True,
    workspace_id: str = "workspace_a",
) -> MaterialAsset:
    path = tmp_path / "人工智能工具演示.mp4"
    path.write_bytes(b"video")
    now = utc_now()
    descriptions = (
        ["人工智能工具界面和团队效率演示", "自动化处理重复工作流程", "人工审核最终结果"]
        if with_metadata
        else ["", "", ""]
    )
    return MaterialAsset(
        id="asset_1",
        workspace_id=workspace_id,
        file_name=path.name,
        path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=now,
        duration_seconds=24,
        usage_role="b_roll",
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        analysis_status="ready",
        segments=[
            MaterialSegment(
                id=f"segment_{index}",
                asset_id="asset_1",
                start_seconds=float(index * 8),
                end_seconds=float((index + 1) * 8),
                description=description,
                review_status="approved",
            )
            for index, description in enumerate(descriptions)
        ],
    )


def _batch(workspace_id: str = "workspace_a") -> ProductionBatch:
    return ProductionBatch(
        id="batch_1",
        workspace_id=workspace_id,
        name="Batch",
        target_count=1,
        status="created",
    )


def _a_roll_asset(tmp_path: Path) -> MaterialAsset:
    path = tmp_path / "人物主镜头.mp4"
    path.write_bytes(b"a-roll")
    now = utc_now()
    return MaterialAsset(
        id="asset_a_roll",
        workspace_id="workspace_a",
        file_name=path.name,
        path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=now,
        duration_seconds=24,
        usage_role="a_roll",
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        analysis_status="ready",
        segments=[
            MaterialSegment(
                id=f"a_roll_segment_{index}",
                asset_id="asset_a_roll",
                start_seconds=float(index * 8),
                end_seconds=float((index + 1) * 8),
                review_status="approved",
            )
            for index in range(3)
        ],
    )


def _profile() -> ProductionProfile:
    return ProductionProfile(
        workspace_id="workspace_a",
        brand_name="Studio",
        brand_template_name="Vertical",
    )


def test_planner_matches_approved_segments_with_traceable_reasons(tmp_path):
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[_asset(tmp_path)],
    )

    assert plan.status == "ready"
    assert plan.clips
    assert plan.low_confidence_clip_count == 0
    assert all(clip.match_reasons for clip in plan.clips)
    assert 30 <= plan.duration_seconds <= 90


def test_planner_uses_real_narration_duration_for_timeline(tmp_path):
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[_asset(tmp_path)],
        narration_audio_path=str(tmp_path / "voice.wav"),
        narration_duration_seconds=18.0,
    )

    assert sum(cue.duration_seconds for cue in plan.cues) == pytest.approx(18.0, abs=0.01)
    assert plan.duration_seconds == pytest.approx(18.0, abs=0.01)


def test_planner_never_truncates_cues_to_fit_short_segments(tmp_path):
    asset = _asset(tmp_path)
    asset.segments[0].end_seconds = 2.0
    asset.segments[0].description = "人工智能工具正在改变团队效率"

    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
        narration_audio_path=str(tmp_path / "voice.wav"),
        narration_duration_seconds=18.0,
    )

    cue_by_id = {cue.id: cue for cue in plan.cues}
    assert all(
        clip.timeline_end_seconds - clip.timeline_start_seconds
        == pytest.approx(cue_by_id[clip.cue_id].duration_seconds, abs=0.001)
        for clip in plan.clips
    )
    assert plan.duration_seconds == pytest.approx(18.0, abs=0.01)


def test_planner_rejects_narration_longer_than_profile_limit(tmp_path):
    with pytest.raises(ValueError, match="实际配音时长.*超过生产档案上限"):
        MontagePlanner().build(
            workspace_id="workspace_a",
            batch=_batch(),
            episode=_episode(),
            profile=_profile(),
            assets=[_asset(tmp_path)],
            narration_audio_path=str(tmp_path / "voice.wav"),
            narration_duration_seconds=91.0,
        )


def test_planner_keeps_duration_only_matches_waiting_for_confirmation(tmp_path):
    asset = _asset(tmp_path, with_metadata=False)
    asset.file_name = "素材001.mp4"
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )

    assert plan.status == "waiting_for_user"
    assert plan.low_confidence_clip_count > 0
    assert any(clip.needs_confirmation for clip in plan.clips)


def test_planner_penalizes_segments_reused_in_historical_renders(tmp_path):
    asset = _asset(tmp_path)
    for segment in asset.segments:
        segment.description = "人工智能工具和自动化工作流程"
    asset.segments[0].usage_count = 10

    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )

    assert plan.clips[0].segment_id != asset.segments[0].id


def test_planner_does_not_use_talking_head_assets_as_b_roll(tmp_path):
    asset = _asset(tmp_path)
    asset.usage_role = "a_roll"

    with pytest.raises(ValueError, match="B-roll"):
        MontagePlanner().build(
            workspace_id="workspace_a",
            batch=_batch(),
            episode=_episode(),
            profile=_profile(),
            assets=[asset],
        )


def test_planner_anchors_with_a_roll_and_inserts_b_roll(tmp_path):
    a_roll = _a_roll_asset(tmp_path)
    b_roll = _asset(tmp_path)

    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[a_roll, b_roll],
    )

    assert len(plan.clips) >= 3
    assert plan.clips[0].role == "a_roll"
    assert plan.clips[-1].role == "a_roll"
    assert all(not clip.needs_confirmation for clip in (plan.clips[0], plan.clips[-1]))
    assert any(clip.role == "b_roll" for clip in plan.clips[1:-1])
    assert all(
        clip.asset_id == ("asset_a_roll" if clip.role == "a_roll" else "asset_1")
        for clip in plan.clips
    )


def test_planner_honors_segment_role_overrides_in_a_mixed_long_video(tmp_path):
    mixed = _a_roll_asset(tmp_path)
    mixed.segments[1].usage_role = "b_roll"
    mixed.segments[1].description = "人工智能工具自动混剪团队工作流程"

    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[mixed],
    )

    assert plan.clips[0].role == "a_roll"
    assert plan.clips[-1].role == "a_roll"
    assert all(
        clip.segment_id == "a_roll_segment_1"
        for clip in plan.clips
        if clip.role == "b_roll"
    )


def test_planner_uses_cached_visual_vectors_but_keeps_uncalibrated_matches_gated(
    tmp_path,
):
    asset = _asset(tmp_path, with_metadata=False)
    asset.file_name = "素材001.mp4"

    class SemanticProvider:
        model_id = "test-chinese-clip"
        ready = True

        def embed_texts(self, texts):
            return [[1.0, 0.0] for _text in texts]

    vectors = {
        "segment_0": [1.0, 0.0],
        "segment_1": [0.0, 1.0],
        "segment_2": [-1.0, 0.0],
    }
    plan = MontagePlanner(
        semantic_provider=SemanticProvider(),
        visual_feature_loader=lambda _workspace, segment_id, _model: vectors[segment_id],
    ).build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )

    assert plan.clips[0].segment_id == "segment_0"
    assert any("画面向量相似度" in reason for reason in plan.clips[0].match_reasons)
    assert plan.status == "waiting_for_user"


def test_montage_repository_is_workspace_scoped_and_persistent(tmp_path):
    repository = MontageRepository(tmp_path / "studio.sqlite3")
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[_asset(tmp_path)],
    )
    repository.save(plan)

    assert repository.get("workspace_a", plan.id).script_title == plan.script_title
    assert repository.list_for_batch("workspace_a", "batch_1")[0].clips
    with pytest.raises(KeyError):
        repository.get("workspace_b", plan.id)


def test_renderer_rejects_placeholder_output_after_running_command(tmp_path):
    asset = _asset(tmp_path)
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )
    commands: list[list[str]] = []

    def runner(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered")

    with pytest.raises(OSError, match="ffprobe|输出"):
        MontageRenderer(tmp_path / "output", runner=runner).render(plan, audio_path)

    assert plan.status == "failed"
    subtitle = (tmp_path / "output" / "batch_1_episode_ready.srt").read_text(encoding="utf-8")
    assert "人工智能工具正在改变团队效率" in subtitle
    command = commands[0]
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in " ".join(command)
    assert "-shortest" in command
    assert command[command.index("-ar") + 1] == "48000"
    assert command[-1].endswith("batch_1_episode_ready_preview.mp4")


def test_renderer_keeps_ffmpeg_exit_code_and_stderr(tmp_path):
    asset = _asset(tmp_path)
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )

    def runner(_command: list[str]) -> None:
        raise subprocess.CalledProcessError(23, ["ffmpeg"], stderr="invalid filter")

    with pytest.raises(MontageRenderError) as error:
        MontageRenderer(tmp_path / "output", runner=runner).render(plan, audio_path)

    assert plan.status == "failed"
    assert error.value.exit_code == 23
    assert error.value.stderr == "invalid filter"
    assert "23" in plan.message
    assert "invalid filter" in plan.message


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_renderer_default_runner_keeps_real_ffmpeg_exit_code_and_stderr(tmp_path):
    with pytest.raises(MontageRenderError) as error:
        MontageRenderer(tmp_path / "output")._run(
            ["ffmpeg", "-f", "unknown-input-format", "-i", "ignored", "-f", "null", "-"]
        )

    assert error.value.exit_code != 0
    assert error.value.stderr != "(no stderr produced)"


def test_renderer_rejects_unconfirmed_plan(tmp_path):
    asset = _asset(tmp_path, with_metadata=False)
    asset.file_name = "素材001.mp4"
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=_episode(),
        profile=_profile(),
        assets=[asset],
    )
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")

    with pytest.raises(ValueError, match="待确认"):
        MontageRenderer(tmp_path / "output", runner=lambda _command: None).render(
            plan, audio_path
        )


def test_montage_api_creates_and_lists_plan(tmp_path, monkeypatch):
    workspace_id = LEGACY_WORKSPACE_ID
    production = main_module.ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
        duration_reader=lambda _path: 24.0,
        scene_reader=lambda _path, _duration: [8.0, 16.0],
    )
    video_path = tmp_path / "人工智能工具演示.mp4"
    video_path.write_bytes(b"video")
    asset = production.scan_library(workspace_id)[0]
    production.analyze_asset(workspace_id, asset.id)
    production.update_asset_review(
        workspace_id,
        asset.id,
        review_status="approved",
        rights_confirmed=True,
        brand_safe=True,
        usage_role="b_roll",
    )
    asset = production.get_asset(workspace_id, asset.id)
    for index, segment in enumerate(asset.segments):
        production.update_segment(
            workspace_id,
            asset.id,
            segment.id,
            description=[
                "人工智能工具提升团队效率",
                "自动化处理重复工作",
                "人工审核最终结果",
            ][index],
            transcript="",
            review_status="approved",
        )
    production.save_batch(_batch(workspace_id))

    episode_repository = EpisodeRepository(database_path=tmp_path / "episodes.sqlite3")
    episode = episode_repository.create(date(2026, 8, 24), workspace_id=workspace_id)
    episode.status = EpisodeStatus.SCRIPT_APPROVED
    episode.script = _episode(workspace_id).script
    episode_repository.save(episode)
    narration = tmp_path / "approved" / "narration.wav"
    narration.parent.mkdir()
    narration.write_bytes(b"audio")
    narration_cache_key = "fixture_narration"
    production.save_narration_artifact(
        NarrationArtifactRecord(
            id="narration_fixture",
            workspace_id=workspace_id,
            episode_id=episode.id,
            script_id="script_fixture",
            voice_profile_id=None,
            cache_key=narration_cache_key,
            audio_path=str(narration),
            duration_seconds=24.0,
            sample_rate=24_000,
            model_id="fixture-narration",
        )
    )

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "montage_repo", MontageRepository(tmp_path / "studio.sqlite3"))
    monkeypatch.setattr(main_module, "repo", episode_repository)
    monkeypatch.setattr(main_module.montage_renderer, "audio_duration", lambda _path: 24.0)
    monkeypatch.setattr(
        main_module,
        "_narration_for_episode",
        lambda _workspace_id, _episode, _profile: NarrationArtifact(
            episode_id=episode.id,
            audio_path=str(narration),
            duration_seconds=24.0,
            sample_rate=24_000,
            model_id="fixture-narration",
            cache_key=narration_cache_key,
        ),
    )
    client = TestClient(main_module.app)

    response = client.post(
        "/api/production/batches/batch_1/montage-plans",
        json={"episode_id": episode.id},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["clips"]
    assert all(clip["source_path"].startswith("workspace://") for clip in payload["clips"])
    assert "narration_audio_path" not in payload
    assert "output_path" not in payload
    assert "subtitle_path" not in payload

    listed = client.get("/api/production/batches/batch_1/montage-plans")
    assert listed.status_code == 200
    assert listed.json()["total_count"] == 1
    assert listed.json()["plans"][0]["clips"][0]["source_path"].startswith("workspace://")

    legacy_payload = client.post(
        "/api/production/batches/batch_1/montage-plans",
        json={
            "episode_id": episode.id,
            "narration_audio_path": "workspace://approved/narration.wav",
        },
    )
    assert legacy_payload.status_code == 422

    duplicate = client.post(
        "/api/production/batches/batch_1/montage-plans",
        json={"episode_id": episode.id},
    )
    assert duplicate.status_code == 409


def test_batch_executor_isolates_failure_and_resumes_without_redoing_success(
    tmp_path,
    monkeypatch,
):
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    batch = ProductionBatch(
        id="batch_resume",
        workspace_id=workspace_id,
        name="Resume",
        target_count=2,
        status="running",
        started_at=utc_now(),
    )
    production.save_batch(batch)
    episode_repository = EpisodeRepository(database_path=tmp_path / "episodes.sqlite3")
    for day in (24, 25):
        episode = episode_repository.create(date(2026, 8, day), workspace_id=workspace_id)
        episode.status = EpisodeStatus.SCRIPT_APPROVED
        episode.script = _episode(workspace_id).script
        episode_repository.save(episode)

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "repo", episode_repository)
    processed: list[str] = []

    def fail_second(_workspace_id, _batch, item: ProductionBatchItem):
        processed.append(item.episode_id)
        if len(processed) == 2:
            raise RuntimeError("模拟单条渲染失败")
        item.status = "rendered"
        item.output_path = str(tmp_path / f"{item.episode_id}.mp4")
        item.message = "初版成片已生成。"

    monkeypatch.setattr(main_module, "_process_production_batch_item", fail_second)
    main_module._complete_production_batch(workspace_id, batch.id)

    first_run = production.get_batch(workspace_id, batch.id)
    assert first_run.rendered_count == 1
    assert first_run.failed_count == 1
    assert first_run.status == "created"
    assert [item.attempts for item in first_run.items] == [1, 1]

    def finish_retry(_workspace_id, _batch, item: ProductionBatchItem):
        processed.append(item.episode_id)
        item.status = "rendered"
        item.output_path = str(tmp_path / f"{item.episode_id}.mp4")
        item.message = "初版成片已生成。"

    monkeypatch.setattr(main_module, "_process_production_batch_item", finish_retry)
    main_module._complete_production_batch(workspace_id, batch.id)

    resumed = production.get_batch(workspace_id, batch.id)
    assert resumed.status == "ready"
    assert resumed.rendered_count == 2
    assert [item.attempts for item in resumed.items] == [1, 2]


def test_running_batch_recovers_as_interrupted_after_restart(tmp_path, monkeypatch):
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    batch = ProductionBatch(
        id="batch_interrupted",
        workspace_id="workspace_a",
        name="Interrupted",
        target_count=1,
        status="running",
        started_at=utc_now(),
        items=[ProductionBatchItem(episode_id="episode_1", status="running")],
    )
    production.save_batch(batch)
    monkeypatch.setattr(main_module, "production_repo", production)

    recovered = main_module._recover_production_batch_if_needed(batch)

    assert recovered.status == "interrupted"
    assert recovered.items[0].status == "interrupted"
    assert production.get_batch("workspace_a", batch.id).status == "interrupted"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
def test_real_ffmpeg_renders_playable_vertical_preview(tmp_path):
    video_path = tmp_path / "source.mp4"
    audio_path = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:d=1.5",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )
    plan = MontagePlan(
        id="montage_real",
        workspace_id="workspace_a",
        batch_id="batch_real",
        episode_id="episode_real",
        script_title="Real render",
        status="ready",
        cues=[NarrationCue("cue_001", "hook", "真实渲染验收", 1.5)],
        clips=[
            MontageClip(
                id="clip_real",
                cue_id="cue_001",
                asset_id="asset_real",
                segment_id="segment_real",
                source_path=str(video_path),
                source_start_seconds=0,
                source_end_seconds=1.5,
                timeline_start_seconds=0,
                timeline_end_seconds=1.5,
                match_score=1,
                match_reasons=["test"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=1.5,
        low_confidence_clip_count=0,
    )

    rendered = MontageRenderer(tmp_path / "output").render(plan, audio_path)
    output_path = Path(rendered.output_path or "")
    assert output_path.stat().st_size > 0
    stream_summary = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    streams = json.loads(stream_summary)["streams"]
    assert {stream["codec_type"]: stream["codec_name"] for stream in streams} == {
        "video": "h264",
        "audio": "aac",
    }
    video = next(stream for stream in streams if stream["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)


def test_montage_review_api_supports_candidates_trim_lock_and_confirm(tmp_path, monkeypatch):
    workspace_id = LEGACY_WORKSPACE_ID
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    for segment in asset.segments:
        segment.usage_role = "b_roll"
    production._save_asset(asset)
    plan = MontagePlan(
        id="montage_review",
        workspace_id=workspace_id,
        batch_id="batch_review",
        episode_id="episode_review",
        script_title="Review",
        status="waiting_for_user",
        cues=[NarrationCue("cue_review", "hook", "人工智能工具演示", 4.0)],
        clips=[
            MontageClip(
                id="clip_review",
                cue_id="cue_review",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path=asset.path,
                source_start_seconds=0,
                source_end_seconds=4,
                timeline_start_seconds=0,
                timeline_end_seconds=4,
                match_score=0.2,
                match_reasons=["low confidence"],
                needs_confirmation=True,
            )
        ],
        duration_seconds=4,
        low_confidence_clip_count=1,
    )
    montage = MontageRepository(tmp_path / "studio.sqlite3")
    montage.save(plan)
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "montage_repo", montage)
    client = TestClient(main_module.app)

    candidates = client.get(
        "/api/production/montage-plans/montage_review/clips/clip_review/candidates"
    )
    assert candidates.status_code == 200
    assert candidates.json()

    updated = client.patch(
        "/api/production/montage-plans/montage_review/clips/clip_review",
        json={"source_start_seconds": 1.0, "locked": True, "confirmed": True},
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["status"] == "ready"
    assert payload["clips"][0]["source_start_seconds"] == 1.0
    assert payload["clips"][0]["source_end_seconds"] == 5.0
    assert payload["clips"][0]["locked"] is True
    assert payload["clips"][0]["needs_confirmation"] is False
