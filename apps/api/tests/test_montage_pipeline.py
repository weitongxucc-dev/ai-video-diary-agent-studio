from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.avatar_lipsync import AvatarAndLipSyncResult, avatar_lipsync_cache_key
from app.models import (
    Episode,
    EpisodeStatus,
    ScriptPackage,
    ScriptSection,
    utc_now,
)
from app.montage import (
    CreationTaskMontagePlan,
    MontageClip,
    MontagePlan,
    MontagePlanner,
    MontageRenderError,
    MontageRenderer,
    NarrationCue,
    _plan_to_srt,
    _subtitle_font,
    _subtitle_font_and_lines,
    _write_subtitle_overlays,
    creation_task_montage_plan_from_payload,
    creation_task_montage_plan_to_payload,
)
from app.production import (
    Authorization,
    CoverAsset,
    CreationPlan,
    CreationPlanVoiceReference,
    CreationTask,
    CreationTaskScript,
    CreationTaskScriptSection,
    MaterialAsset,
    MaterialSegment,
    MaterialSegmentMatch,
    NarrationArtifactRecord,
    NarrationSegment,
    PersonProfile,
    PersonRender,
    ProductionBatch,
    ProductionProfile,
    ProductionRepository,
    SubtitleCue as ProductionSubtitleCue,
    SubtitleTrack,
    TimelineVersion,
    VersionedVoiceProfile,
)
from app.video_jobs import VideoJobRunner


def test_srt_preserves_user_authored_subtitle_line_breaks() -> None:
    plan = CreationTaskMontagePlan(
        id="montage-line-break",
        workspace_id="workspace_a",
        task_id="task-line-break",
        script_id="script-line-break",
        script_title="字幕换行",
        status="ready",
        cues=[
            NarrationCue(
                id="cue-line-break",
                source_section_id="section-line-break",
                narration_segment_id="narration-line-break",
                text="本机页面记录来源，\n每一步都能追溯",
                duration_seconds=4.0,
            )
        ],
        clips=[
            MontageClip(
                id="clip-line-break",
                cue_id="cue-line-break",
                asset_id="asset-line-break",
                segment_id="segment-line-break",
                source_path="workspace://approved/example.mp4",
                source_start_seconds=0.0,
                source_end_seconds=4.0,
                timeline_start_seconds=0.0,
                timeline_end_seconds=4.0,
                match_score=1.0,
                match_reasons=["test"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=4.0,
        low_confidence_clip_count=0,
    )

    assert "本机页面记录来源，\n每一步都能追溯" in _plan_to_srt(plan)


def test_render_plan_applies_track_line_breaks_and_emphasis_metadata() -> None:
    plan = CreationTaskMontagePlan(
        id="montage-track-style",
        workspace_id="workspace_a",
        task_id="task-track-style",
        script_id="script-track-style",
        script_title="字幕轨渲染",
        status="ready",
        cues=[NarrationCue("cue-track-style", "section", "旧字幕", 4.0)],
        clips=[],
        duration_seconds=4.0,
        low_confidence_clip_count=0,
    )
    track = SubtitleTrack(
        id="subtitle-track-style",
        workspace_id="workspace_a",
        task_id="task-track-style",
        timeline_version_id="timeline-track-style",
        language="zh-CN",
        source_language=None,
        style={"name": "emphasis", "emphasis_color": "#FFD54F"},
        cues=[
            ProductionSubtitleCue(
                id="cue-track-style",
                source_cue_id=None,
                text="本机页面记录来源，每一步都能追溯",
                start_seconds=0.0,
                end_seconds=4.0,
                line_breaks=[9],
                emphasis=["本机页面"],
            )
        ],
        generation_method="timeline",
        model_id=None,
        artifact_ref="artifacts/subtitles.srt",
        transform_id="transform-track-style",
        status="valid",
    )

    main_module._apply_subtitle_tracks_to_render_plan(plan, [track])

    assert plan.cues[0].text == "本机页面记录来源，\n每一步都能追溯"
    assert plan.cues[0].subtitle_emphasis == ["本机页面"]
    assert plan.cues[0].subtitle_style["emphasis_color"] == "#FFD54F"
    english = replace(track, id="english", language="en-US", source_language="zh-CN", cues=[
        replace(track.cues[0], id="english-cue", source_cue_id=track.cues[0].id,
                text="本机页面 English", line_breaks=[], emphasis=["English"])
    ])
    main_module._apply_subtitle_tracks_to_render_plan(plan, [track, english])
    assert plan.cues[0].subtitle_emphasis_ranges == [(0, 4), (len(track.cues[0].text) + 4, len(track.cues[0].text) + 11)]


@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
@pytest.mark.parametrize("size", [28, 72])
def test_subtitle_overlay_renders_confirmed_style_and_manual_breaks(tmp_path, position, size):
    family = _subtitle_font(40).getname()[0]
    style = {
        "font_family": family,
        "font_size": size,
        "primary_color": "#00FF00",
        "emphasis_color": "#FFD54F",
        "position": position,
    }
    font, lines = _subtitle_font_and_lines("真实运行\n字幕效果", style)
    assert font.getname()[0] == family
    assert font.size == size
    assert lines == ["真实运行", "字幕效果"]
    plan = SimpleNamespace(
        cues=[NarrationCue("cue", "section", "真实运行\n字幕效果", 1, subtitle_style=style,
                           subtitle_emphasis=["真实运行"])],
        clips=[SimpleNamespace(cue_id="cue")],
    )
    path = _write_subtitle_overlays(plan, tmp_path)[0]
    with Image.open(path) as overlay:
        assert overlay.size == (1080, 1920)
        assert overlay.getpixel((0, 0)) == (0, 0, 0, 0)
        colors = {color for _, color in overlay.getcolors(1080 * 1920)}
        assert (0, 255, 0, 255) in colors
        assert (255, 213, 79, 255) in colors
        left, top, right, bottom = overlay.getbbox()
        assert 0 < left < right < 1080
        if position == "top":
            assert top == 250
        elif position == "middle":
            assert abs((top + bottom) / 2 - 960) <= 1
        else:
            assert bottom == 1671
        assert bottom - top > size * 1.5


def test_subtitle_layout_wraps_to_safe_width_without_resetting_font_size():
    font, lines = _subtitle_font_and_lines("字幕参数真正进入画面，" * 6, {"font_size": 72})
    assert font.size == 72
    assert len(lines) > 2
    assert all(font.getbbox(line, stroke_width=3)[2] <= 880 for line in lines)


def test_subtitle_font_reports_missing_confirmed_family():
    with pytest.raises(OSError, match="未安装.*请安装"):
        _subtitle_font_and_lines("真实运行", {"font_family": "Missing Subtitle Family 123456"})


def test_subtitle_font_resolves_pingfang_sc_collection_face():
    try:
        font = _subtitle_font(72, family="PingFang SC")
    except OSError:
        pytest.skip("PingFang SC is not installed on this host")
    assert font.getname()[0] == "PingFang SC"
    assert font.size == 72


@pytest.mark.parametrize("style, message", [
    ({"font_size": 0}, "字号"),
    ({"font_size": True}, "字号"),
    ({"font_size": 1921}, "字号"),
    ({"font_family": ""}, "字体名称"),
    ({"position": "center"}, "字幕位置"),
])
def test_subtitle_overlay_rejects_unrenderable_style(tmp_path, style, message):
    plan = SimpleNamespace(
        cues=[NarrationCue("cue", "section", "真实运行", 1, subtitle_style=style)],
        clips=[SimpleNamespace(cue_id="cue")],
    )
    with pytest.raises(OSError, match=message):
        _write_subtitle_overlays(plan, tmp_path)


def test_subtitle_emphasis_survives_line_break_inside_term(tmp_path):
    plan = SimpleNamespace(
        cues=[NarrationCue(
            "cue", "section", "真实\n运行", 1,
            subtitle_style={"font_size": 72, "primary_color": "#00FF00"},
            subtitle_emphasis=["真实运行"],
        )],
        clips=[SimpleNamespace(cue_id="cue")],
    )
    with Image.open(_write_subtitle_overlays(plan, tmp_path)[0]) as overlay:
        colors = {color for _, color in overlay.getcolors(1080 * 1920)}
        assert (255, 213, 79, 255) in colors
        assert (0, 255, 0, 255) not in colors


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


@pytest.fixture
def person_render_edit_state(tmp_path, monkeypatch):
    workspace_id = "workspace_a"
    output_root = tmp_path / "output"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    production._save_asset(asset)
    visual_reference = production.workspace_media_reference_for_asset(
        workspace_id, asset.id
    )
    voice_path = tmp_path / "voice-reference.wav"
    voice_path.write_bytes(b"voice-reference")
    voice_reference = "workspace://voice-reference.wav"
    effective_from = datetime(2025, 1, 1, tzinfo=timezone.utc)
    person_authorization = Authorization(
        id="person-authorization",
        workspace_id=workspace_id,
        version="v1",
        rights_holder="本人",
        content_types=("person_likeness", "avatar_video"),
        usage_scopes=("avatar_lipsync",),
        effective_from=effective_from,
        expires_at=None,
        evidence_reference="consent://person/v1",
        status="active",
    )
    person = production.save_person_profile(
        PersonProfile(
            id="person-a",
            workspace_id=workspace_id,
            display_name="cc",
            version="v1",
            avatar_reference=visual_reference,
            background_capabilities=("office",),
            shot_capabilities=("medium",),
            authorization=person_authorization,
        )
    )
    voice_authorization = Authorization(
        id="voice-authorization",
        workspace_id=workspace_id,
        version="v1",
        rights_holder="本人",
        content_types=("voice",),
        usage_scopes=("avatar_lipsync",),
        effective_from=effective_from,
        expires_at=None,
        evidence_reference="consent://voice/v1",
        status="active",
    )
    voice = production.save_versioned_voice_profile(
        VersionedVoiceProfile(
            id="voice-person",
            workspace_id=workspace_id,
            person_profile_id=person.id,
            person_version=person.version,
            version="v1",
            language="zh",
            reference_audio_path=voice_reference,
            reference_text="参考声音",
            model_id="qwen3-tts",
            authorization=voice_authorization,
        )
    )
    selected_voice = CreationPlanVoiceReference(
        profile_id=voice.id,
        version=voice.version,
        person_profile_id=person.id,
        person_version=person.version,
        person_authorization_id=person_authorization.id,
        person_authorization_version=person_authorization.version,
        authorization_id=voice_authorization.id,
        authorization_version=voice_authorization.version,
        language="zh",
        model_id=voice.model_id,
    )
    creation_plan = CreationPlan(
        id="plan-person-edit",
        workspace_id=workspace_id,
        goal="只重新生成目标人物口型",
        name="人物口型编辑",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=selected_voice,
        pronunciation_references=[],
        approved_asset_count=1,
        approved_clip_count=2,
        material_rule_summary="使用当前人物与素材",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="p" * 64,
        material_matches=[
            MaterialSegmentMatch(
                asset_ref=f"asset:{asset.id}",
                segment_ref=f"segment:{asset.segments[0].id}",
                score=1,
                reasons=["测试素材"],
            )
        ],
    )
    task = CreationTask(
        id="task-person-edit",
        workspace_id=workspace_id,
        plan_id=creation_plan.id,
        name=creation_plan.name,
        scenario=creation_plan.goal,
    )
    production.confirm_creation_plan(creation_plan, task)
    now = datetime.now(timezone.utc)
    script = CreationTaskScript(
        id="script-person-edit",
        workspace_id=workspace_id,
        task_id=task.id,
        run_id="run-person-edit",
        topic_id="topic-person-edit",
        title="人物口型编辑",
        language="zh-CN",
        sections=[
            CreationTaskScriptSection(
                id="section-person-edit",
                title="正文",
                content="人物出镜讲解。",
                visual_prompt="人物中景",
                evidence_reference_ids=["knowledge:test"],
            )
        ],
        selection_status="confirmed",
        confirmed_at=now,
    )
    task = production.get_creation_task(workspace_id, task.id)
    task.confirmed_script_id = script.id
    task.stage = "narration"
    task.stage_status = "running"
    task.status = "running"
    with production._connect() as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts "
            "(id, workspace_id, task_id, run_id, payload, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                script.id,
                workspace_id,
                task.id,
                script.run_id,
                json.dumps(
                    asdict(script),
                    ensure_ascii=False,
                    default=lambda value: value.isoformat(),
                ),
                now.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? "
            "WHERE workspace_id = ? AND id = ?",
            (
                json.dumps(
                    task.__dict__,
                    ensure_ascii=False,
                    default=lambda value: value.isoformat(),
                ),
                now.isoformat(),
                workspace_id,
                task.id,
            ),
        )
    narration_path = tmp_path / "person-narration.wav"
    narration_path.write_bytes(b"person-narration")
    narration_cache_key = "person-segment-cache"
    narration_reference = production.narration_artifact_reference(
        narration_cache_key
    )
    segment = NarrationSegment(
        id="narration-person-edit",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        source_section_id=script.sections[0].id,
        text=script.sections[0].content,
        audio_reference=narration_reference,
        duration_seconds=4,
        sample_rate=24_000,
        model_id="qwen3-tts",
        voice_profile_id=voice.id,
        voice_version=voice.version,
        language="zh",
        pronunciation_version="p1",
        status="ready",
    )
    production.complete_creation_task_narration(
        NarrationArtifactRecord(
            id="narration-artifact-person-edit",
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id=voice.id,
            cache_key=narration_cache_key,
            audio_path=str(narration_path),
            duration_seconds=4,
            sample_rate=24_000,
            model_id="qwen3-tts",
        ),
        segments=[segment],
    )
    provider = SimpleNamespace(
        model_id="musetalk-test",
        supported_languages=("zh",),
        ready=True,
        message="ready",
        output_cache_keys=[],
    )

    def fake_person_render(*, request, output_path):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"new-person-render")
        provider.output_cache_keys.append(output.stem)
        return AvatarAndLipSyncResult(
            status="succeeded",
            cache_key=output.stem,
            artifact_path=str(output.resolve()),
            duration_seconds=4,
            alignment_evidence='{"engine":"fake-musetalk"}',
            failure_code=None,
            failure_impact=None,
            remediation_action=None,
        )

    provider.render = fake_person_render
    render_request = production.resolve_avatar_lipsync_request(
        workspace_id=workspace_id,
        person_id=person.id,
        person_version=person.version,
        voice_profile_id=voice.id,
        voice_version=voice.version,
        audio_path=str(narration_path),
        audio_cache_key=narration_reference,
        language="zh",
        motion="natural",
        expression="neutral",
        authorization_id=person_authorization.id,
        authorization_version=person_authorization.version,
        model_id=provider.model_id,
    )
    base_cache_key = avatar_lipsync_cache_key(render_request)
    old_render = production.create_person_render(
        PersonRender(
            id=f"person_render_{base_cache_key[:24]}",
            workspace_id=workspace_id,
            task_id=task.id,
            person_profile_id=person.id,
            person_version=person.version,
            voice_profile_id=voice.id,
            voice_version=voice.version,
            narration_segment_id=segment.id,
            visual_reference=visual_reference,
            model_id=provider.model_id,
            motion="natural",
            expression="neutral",
            cache_key=base_cache_key,
            status="pending",
        )
    )
    production.claim_person_render(workspace_id, task.id, old_render.id)
    old_render_path = output_root / "legacy" / "old-person-render.mp4"
    old_render_path.parent.mkdir(parents=True, exist_ok=True)
    old_render_path.write_bytes(b"old-person-render")
    old_render = production.finish_person_render(
        workspace_id,
        task.id,
        old_render.id,
        status="ready",
        output_path=str(old_render_path),
        duration_seconds=4,
        alignment_evidence='{"engine":"existing-musetalk"}',
    )
    montage = CreationTaskMontagePlan(
        id="montage-person-edit",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        script_title=script.title,
        status="rendered",
        cues=[
            NarrationCue(
                id="cue-person-edit",
                source_section_id=script.sections[0].id,
                text=script.sections[0].content,
                duration_seconds=4,
                narration_segment_id=segment.id,
            )
        ],
        clips=[
            MontageClip(
                id="clip-person-target",
                cue_id="cue-person-edit",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path=visual_reference,
                source_start_seconds=0,
                source_end_seconds=4,
                timeline_start_seconds=0,
                timeline_end_seconds=4,
                match_score=1,
                match_reasons=["已生成人物口型"],
                needs_confirmation=False,
                role="a_roll",
                manually_selected=True,
                person_render_id=old_render.id,
            ),
            MontageClip(
                id="clip-person-unchanged",
                cue_id="cue-person-edit",
                asset_id=asset.id,
                segment_id=asset.segments[1].id,
                source_path=visual_reference,
                source_start_seconds=4,
                source_end_seconds=8,
                timeline_start_seconds=4,
                timeline_end_seconds=8,
                match_score=1,
                match_reasons=["不受影响镜头"],
                needs_confirmation=False,
            ),
        ],
        duration_seconds=8,
        low_confidence_clip_count=0,
        narration_audio_path=narration_reference,
        output_path="artifacts/task-person-edit/rendered/preview.mp4",
        subtitle_path="artifacts/task-person-edit/rendered/subtitles.srt",
    )
    production.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        montage.id,
        creation_task_montage_plan_to_payload(montage),
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(output_root))
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(video_output_path=str(output_root)),
    )
    monkeypatch.setattr(main_module, "avatar_lipsync_provider", provider)
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda _request: workspace_id
    )
    return SimpleNamespace(
        workspace_id=workspace_id,
        production=production,
        task=task,
        montage=montage,
        old_render=old_render,
        provider=provider,
        client=TestClient(main_module.app),
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
        cues=[NarrationCue(
            "cue_001", "hook", "真实运行\n字幕效果", 1.5,
            subtitle_style={
                "font_family": _subtitle_font(40).getname()[0],
                "font_size": 72, "position": "middle",
                "primary_color": "#00FF00", "emphasis_color": "#FFD54F",
            },
            subtitle_emphasis=["真实运行"],
        )],
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
    frame_path = tmp_path / "rendered-style-frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.5", "-i", str(output_path), "-frames:v", "1", str(frame_path)],
        check=True, capture_output=True,
    )
    with Image.open(frame_path) as frame:
        center = frame.convert("RGB").crop((100, 800, 980, 1120))
        colors = center.get_flattened_data()
        assert sum(g > 180 and r < 80 and b < 80 for r, g, b in colors) > 100
        assert sum(r > 180 and g > 140 and b < 130 for r, g, b in colors) > 100
        assert frame.getpixel((540, 1500))[2] > 200


def test_creation_task_planner_uses_confirmed_script_and_logical_material_references(tmp_path):
    script = CreationTaskScript(
        id="script_task",
        workspace_id="workspace_a",
        task_id="task_1",
        run_id="run_1",
        topic_id="topic_1",
        title="任务文案",
        language="zh-CN",
        selection_status="confirmed",
        sections=[
            CreationTaskScriptSection(
                id="section_task",
                title="正文",
                content="人工智能工具正在改变团队效率。",
                visual_prompt="人工智能工具界面",
                evidence_reference_ids=["knowledge:1:v1"],
            )
        ],
    )
    asset = _asset(tmp_path)
    plan = MontagePlanner().build_for_creation_task(
        workspace_id="workspace_a",
        task_id="task_1",
        script=script,
        profile=ProductionProfile(workspace_id="workspace_a"),
        assets=[asset],
        media_reference_for_asset=lambda asset_id: f"workspace://{asset_id}.mp4",
        narration_audio_path="narration://artifact/cache_1",
        narration_duration_seconds=6.0,
    )

    assert plan.task_id == "task_1"
    assert plan.script_id == script.id
    assert plan.clips
    assert all(clip.source_path.startswith("workspace://") for clip in plan.clips)
    restored = creation_task_montage_plan_from_payload(
        creation_task_montage_plan_to_payload(plan)
    )
    assert restored == plan


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
@pytest.mark.parametrize("bilingual", [False, True])
def test_creation_task_renderer_writes_task_scoped_mp4_and_srt(tmp_path, bilingual):
    video_path = tmp_path / "source-task.mp4"
    audio_path = tmp_path / "voice-task.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=640x360:d=1.2",
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
            "sine=frequency=440:duration=1.2",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )
    plan = CreationTaskMontagePlan(
        id="task_montage_real",
        workspace_id="workspace_a",
        task_id="task_real",
        script_id="script_real",
        script_title="真实任务渲染",
        status="ready",
        cues=[NarrationCue("cue_001", "section_1", "真实任务字幕", 1.2)],
        clips=[
            MontageClip(
                id="clip_real",
                cue_id="cue_001",
                asset_id="asset_real",
                segment_id="segment_real",
                source_path=str(video_path),
                source_start_seconds=0,
                source_end_seconds=1.2,
                timeline_start_seconds=0,
                timeline_end_seconds=1.2,
                match_score=1,
                match_reasons=["test"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=1.2,
        low_confidence_clip_count=0,
    )

    renderer = MontageRenderer(tmp_path / "legacy-output")
    if bilingual:
        # Explicit artificial translation fixture: exercises the production track-to-render
        # path, never a model-call assertion or a persisted user task.
        chinese = SubtitleTrack(
            id="fixture-zh", workspace_id="workspace_a", task_id="task_real",
            timeline_version_id="fixture-version", language="zh-CN", source_language=None,
            style={"font_family": "PingFang SC" if sys.platform == "darwin" else _subtitle_font(40).getname()[0], "font_size": 40, "position": "bottom"},
            cues=[ProductionSubtitleCue(
                id="cue_001", source_cue_id=None, text="人工测试字幕", start_seconds=0, end_seconds=1.2,
                line_breaks=[2], emphasis=[],
            )],
            generation_method="timeline", model_id=None, artifact_ref=None, transform_id=None, status="valid",
        )
        english = replace(
            chinese, id="fixture-en", language="en-US", source_language="zh-CN",
            cues=[ProductionSubtitleCue(
                id="fixture-en-cue", source_cue_id="cue_001",
                text="MANUAL TEST SUBTITLESNot a model translation",
                start_seconds=0, end_seconds=1.2, line_breaks=[21], emphasis=["MANUAL"],
            )],
        )
        main_module._apply_subtitle_tracks_to_render_plan(plan, [chinese, english])
        assert plan.cues[0].text == "人工\n测试字幕\nMANUAL TEST SUBTITLES\nNot a model translation"
    with pytest.raises(ValueError, match="job-scoped"):
        renderer.render(plan, audio_path)

    job_staging_path = tmp_path / "temp" / "job_render_1"
    rendered = renderer.render(
        plan,
        audio_path,
        job_staging_path=job_staging_path,
    )

    staged_output = job_staging_path / "preview.mp4"
    staged_subtitle = job_staging_path / "subtitles.srt"
    assert staged_output.is_file()
    srt = staged_subtitle.read_text(encoding="utf-8")
    assert ("人工\n测试字幕\nMANUAL TEST SUBTITLES\nNot a model translation" if bilingual else "真实任务字幕") in srt
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height", "-of", "json", str(staged_output)],
        check=True, capture_output=True, text=True,
    ).stdout)
    assert {stream["codec_type"] for stream in probe["streams"]} == {"video", "audio"}
    assert float(probe["format"]["duration"]) == pytest.approx(1.2, abs=0.1)
    assert int(probe["format"]["size"]) > 0
    assert rendered.output_path is None
    assert rendered.subtitle_path is None
    assert not (tmp_path / "legacy-output" / "creation-tasks").exists()
    frame_path = tmp_path / "subtitle-frame.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(staged_output),
            "-frames:v",
            "1",
            str(frame_path),
        ],
        check=True,
        capture_output=True,
    )
    with Image.open(frame_path) as frame:
        subtitle_region = frame.convert("RGB").crop((0, 1300, 1080, 1800))
        white_pixels = sum(
            red > 220 and green > 220 and blue > 220
            for red, green, blue in subtitle_region.get_flattened_data()
        )
    assert white_pixels > 50
    if bilingual:
        assert sum(red > 180 and green > 140 and blue < 130
                   for red, green, blue in subtitle_region.get_flattened_data()) > 50


def test_creation_task_render_helper_restores_logical_media_references(
    tmp_path,
    monkeypatch,
):
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    production._save_asset(asset)
    logical_clip_reference = production.workspace_media_reference_for_asset(
        workspace_id, asset.id
    )
    logical_narration_reference = "narration://artifact/test-reference"
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"wave")
    plan = CreationTaskMontagePlan(
        id="task-montage-logical",
        workspace_id=workspace_id,
        task_id="task-logical",
        script_id="script-logical",
        script_title="逻辑引用",
        status="ready",
        cues=[NarrationCue("cue-logical", "section-logical", "逻辑引用", 4.0)],
        clips=[
            MontageClip(
                id="clip-logical",
                cue_id="cue-logical",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path=logical_clip_reference,
                source_start_seconds=0,
                source_end_seconds=4,
                timeline_start_seconds=0,
                timeline_end_seconds=4,
                match_score=1,
                match_reasons=["已确认"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=4,
        low_confidence_clip_count=0,
        narration_audio_path=logical_narration_reference,
    )

    class MutatingRenderer:
        def render(self, rendered_plan, resolved_audio_path):
            assert Path(rendered_plan.clips[0].source_path).is_absolute()
            rendered_plan.narration_audio_path = str(resolved_audio_path)
            rendered_plan.status = "rendered"
            return rendered_plan

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "montage_renderer", MutatingRenderer())

    rendered = main_module._render_montage_with_resolved_clip_sources(
        workspace_id,
        plan,
        audio_path,
    )

    assert rendered.clips[0].source_path == logical_clip_reference
    assert rendered.narration_audio_path == logical_narration_reference


def test_legacy_rendered_task_cannot_bypass_typed_timeline_edits(tmp_path, monkeypatch):
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    production._save_asset(asset)
    creation_plan = CreationPlan(
        id="creation-plan-review",
        workspace_id=workspace_id,
        goal="检查成片后修改镜头",
        name="成片修订",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=1,
        approved_clip_count=1,
        material_rule_summary="只使用已审核镜头",
        model_snapshot=[],
        confirmation_points=["确认镜头"],
        blockers=[],
        preflight_fingerprint="f" * 64,
        material_matches=[
            MaterialSegmentMatch(
                asset_ref=f"asset:{asset.id}",
                segment_ref=f"segment:{asset.segments[0].id}",
                score=1.0,
                reasons=["测试夹具中的已审核镜头"],
            )
        ],
    )
    task = CreationTask(
        id="task-review-edit",
        workspace_id=workspace_id,
        plan_id=creation_plan.id,
        name=creation_plan.name,
        scenario=creation_plan.goal,
        stage="review",
        stage_status="waiting_for_user",
        status="waiting_for_user",
        progress=90,
        recovery_point="review",
        output_ready=True,
        stage_reason="render_failed",
        stage_impact="未生成成片。",
        stage_repair_action="修复后重试。",
        stage_retryable=True,
    )
    production.confirm_creation_plan(creation_plan, task)
    plan = CreationTaskMontagePlan(
        id="task-montage-review",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id="script-review",
        script_title="成片修订",
        status="rendered",
        cues=[NarrationCue("cue-review", "section-review", "检查镜头", 4.0)],
        clips=[
            MontageClip(
                id="clip-review",
                cue_id="cue-review",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path=production.workspace_media_reference_for_asset(
                    workspace_id, asset.id
                ),
                source_start_seconds=0,
                source_end_seconds=4,
                timeline_start_seconds=0,
                timeline_end_seconds=4,
                match_score=1,
                match_reasons=["已确认"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=4,
        low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/test-review",
        output_path=str(tmp_path / "rendered.mp4"),
        subtitle_path=str(tmp_path / "rendered.srt"),
    )
    production.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        plan.id,
        creation_task_montage_plan_to_payload(plan),
    )
    rendered_task = production.get_creation_task(workspace_id, task.id)
    assert rendered_task.stage_reason is None
    assert rendered_task.stage_impact is None
    assert rendered_task.stage_repair_action is None
    assert rendered_task.stage_retryable is None
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)

    subtitle_response = TestClient(main_module.app).patch(
        f"/api/creation-tasks/{task.id}/montage/cues/{plan.cues[0].id}",
        json={"text": "修改后的画面字幕"},
    )

    assert subtitle_response.status_code == 409
    assert subtitle_response.json()["detail"]["error"] == "edit_base_not_migrated"

    response = TestClient(main_module.app).patch(
        f"/api/creation-tasks/{task.id}/montage/clips/{plan.clips[0].id}",
        json={"source_start_seconds": 1.0},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "edit_base_not_migrated"
    restored_task = production.get_creation_task(workspace_id, task.id)
    assert restored_task.stage == "review"
    assert restored_task.stage_status == "waiting_for_user"
    assert restored_task.output_ready is True
    assert production.list_edit_operations(workspace_id, task.id) == []


@pytest.mark.parametrize("exact_replacement", [False, True])
def test_creation_task_render_route_persists_job_lineage_and_logical_outputs(
    tmp_path, monkeypatch, exact_replacement
):
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    production._save_asset(asset)
    replacement_path = tmp_path / "replacement-outdoor.mp4"
    replacement_path.write_bytes(b"replacement-video")
    replacement_segment = replace(
        asset.segments[0],
        id="segment_replacement",
        description="户外产品补充画面",
        system_tags=["场景:户外"],
    )
    replacement_asset = replace(
        asset,
        id="asset_replacement",
        file_name=replacement_path.name,
        path=str(replacement_path),
        segments=[replacement_segment],
    )
    production._save_asset(replacement_asset)
    creation_plan = CreationPlan(
        id="plan-render-route",
        workspace_id=workspace_id,
        goal="渲染一条可追溯成片",
        name="渲染迁移",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=1,
        approved_clip_count=1,
        material_rule_summary="使用当前任务素材",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="f" * 64,
        material_matches=[
            MaterialSegmentMatch(
                asset_ref=f"asset:{asset.id}",
                segment_ref=f"segment:{asset.segments[0].id}",
                score=1.0,
                reasons=["测试夹具中的已审核镜头"],
            )
        ],
    )
    task = CreationTask(
        id="task-render-route",
        workspace_id=workspace_id,
        plan_id=creation_plan.id,
        name=creation_plan.name,
        scenario=creation_plan.goal,
        stage="montage",
        stage_status="waiting_for_user",
        status="waiting_for_user",
        progress=70,
        recovery_point="montage",
    )
    production.confirm_creation_plan(creation_plan, task)
    plan = CreationTaskMontagePlan(
        id="montage-render-route",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id="script-render-route",
        script_title="渲染迁移",
        status="ready",
        cues=[NarrationCue("cue-route", "section-route", "字幕内容", 1.0)],
        clips=[
            MontageClip(
                id="clip-route",
                cue_id="cue-route",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path="workspace://source.mp4",
                source_start_seconds=0,
                source_end_seconds=1,
                timeline_start_seconds=0,
                timeline_end_seconds=1,
                match_score=1,
                match_reasons=["已确认"],
                needs_confirmation=False,
            ),
            MontageClip(
                id="clip-route-unchanged",
                cue_id="cue-route",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path="workspace://source.mp4",
                source_start_seconds=1,
                source_end_seconds=2,
                timeline_start_seconds=1,
                timeline_end_seconds=2,
                match_score=1,
                match_reasons=["已确认"],
                needs_confirmation=False,
            ),
        ],
        duration_seconds=2,
        low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/route",
    )
    production.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        plan.id,
        creation_task_montage_plan_to_payload(plan),
    )
    output_root = tmp_path / "output"
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")

    def fake_render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path):
        job_staging_path.mkdir(parents=True, exist_ok=True)
        (job_staging_path / "preview.mp4").write_bytes(b"playable-video")
        (job_staging_path / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n字幕内容\n", encoding="utf-8"
        )
        rendered_plan.status = "rendered"
        return rendered_plan

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(output_root))
    monkeypatch.setattr(
        main_module, "settings", SimpleNamespace(video_output_path=str(output_root))
    )
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    monkeypatch.setattr(
        production,
        "resolve_authorized_narration_audio_reference",
        lambda _workspace_id, _reference: audio_path,
    )
    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", fake_render
    )

    client = TestClient(main_module.app)
    original_create_artifact_record = production.create_artifact_record

    class SimulatedProcessExit(BaseException):
        pass

    monkeypatch.setattr(
        production,
        "create_artifact_record",
        lambda _artifact: (_ for _ in ()).throw(SimulatedProcessExit()),
    )
    with pytest.raises(BaseExceptionGroup) as crashed:
        client.post(f"/api/creation-tasks/{task.id}/montage/render", json={})
    assert any(
        isinstance(error, SimulatedProcessExit)
        for error in crashed.value.exceptions
    )

    interrupted_job = production.list_job_runs(workspace_id, task.id)[0]
    assert interrupted_job.status == "running"
    assert production.list_artifact_records(workspace_id, task.id) == []
    assert (output_root / "artifacts" / task.id / interrupted_job.id / "preview.mp4").is_file()
    assert (output_root / "artifacts" / task.id / interrupted_job.id / "subtitles.srt").is_file()

    monkeypatch.setattr(
        production, "create_artifact_record", original_create_artifact_record
    )
    assert main_module.video_job_runner.recover_after_restart() == 1

    def reject_rerender(*_args, **_kwargs):
        pytest.fail("恢复已发布产物时不应再次执行 FFmpeg")

    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", reject_rerender
    )
    response = client.post(
        f"/api/creation-tasks/{task.id}/montage/render", json={}
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rendered"
    jobs = production.list_job_runs(workspace_id, task.id)
    assert len(jobs) == 1 and jobs[0].status == "succeeded"
    artifacts = production.list_artifact_records(workspace_id, task.id)
    assert {artifact.kind for artifact in artifacts} == {
        "rendered_video",
        "subtitle_track",
    }
    transforms = production.list_transform_runs(workspace_id, task.id)
    assert len(transforms) == 1 and transforms[0].status == "succeeded"
    assert transforms[0].output_refs == jobs[0].output_refs
    assert len(production.list_dependency_edges(workspace_id, task.id)) == 6
    versions = production.list_timeline_versions(workspace_id, task.id)
    assert len(versions) == 1
    saved_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert saved_plan.output_path == jobs[0].output_refs[0]
    assert saved_plan.subtitle_path == jobs[0].output_refs[1]
    assert not Path(saved_plan.output_path).is_absolute()
    assert list((output_root / ".job-staging").iterdir()) == []
    output_response = client.get(f"/api/creation-tasks/{task.id}/output")
    subtitle_response = client.get(
        f"/api/creation-tasks/{task.id}/output/subtitles"
    )
    assert output_response.content == b"playable-video"
    assert "字幕内容" in subtitle_response.text

    rendered_narration_reference = saved_plan.narration_audio_path
    saved_plan.output_path = str(tmp_path / "legacy-preview.mp4")
    saved_plan.subtitle_path = str(tmp_path / "legacy-subtitles.srt")
    production.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        saved_plan.id,
        creation_task_montage_plan_to_payload(saved_plan),
    )
    legacy_base_response = client.post(
        f"/api/creation-tasks/{task.id}/edits/preview",
        json={
            "raw_command": "把这个镜头换成另一个镜头",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-route"],
            "playhead_ms": 500,
        },
    )
    assert legacy_base_response.status_code == 409
    assert legacy_base_response.json()["detail"]["error"] == "edit_base_not_migrated"
    assert production.list_edit_intents(workspace_id, task.id) == []
    with production._connect() as conn:
        task_payload = json.loads(
            conn.execute(
                "SELECT payload FROM creation_tasks WHERE workspace_id = ? AND id = ?",
                (workspace_id, task.id),
            ).fetchone()[0]
        )
        task_payload["current_timeline_version_id"] = None
        conn.execute(
            "UPDATE creation_tasks SET payload = ? WHERE workspace_id = ? AND id = ?",
            (json.dumps(task_payload, ensure_ascii=False), workspace_id, task.id),
        )
        conn.execute(
            "DELETE FROM timeline_versions WHERE workspace_id = ? AND task_id = ?",
            (workspace_id, task.id),
        )
    migrated_render = client.post(
        f"/api/creation-tasks/{task.id}/montage/render", json={}
    )
    assert migrated_render.status_code == 200, migrated_render.text
    migrated_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert migrated_plan.output_path == jobs[0].output_refs[0]
    assert migrated_plan.subtitle_path == jobs[0].output_refs[1]
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 1
    assert production.get_creation_task(
        workspace_id, task.id
    ).current_timeline_version_id

    for path_command in (
        "把这个镜头换成 /tmp/other.mp4",
        "把这个镜头换成/Users/test/other.mp4",
        "把这个镜头换成 file:///tmp/other.mp4",
        r"把这个镜头换成C:\素材\other.mp4",
        r"把这个镜头换成\\server\share\other.mp4",
    ):
        arbitrary_path_response = client.post(
            f"/api/creation-tasks/{task.id}/edits/preview",
            json={
                "raw_command": path_command,
                "context_stage": "review",
                "selected_object_refs": ["clip:clip-route"],
                "playhead_ms": 500,
            },
        )
        assert arbitrary_path_response.status_code == 409
        assert (
            arbitrary_path_response.json()["detail"]["error"]
            == "edit_path_input_forbidden"
        )
        assert production.list_edit_intents(workspace_id, task.id) == []

    scope_required_response = client.post(
        f"/api/creation-tasks/{task.id}/edits/preview",
        json={
            "raw_command": "把这个镜头换成另一个镜头",
            "context_stage": "review",
            "selected_object_refs": [],
            "playhead_ms": 500,
        },
    )
    assert scope_required_response.status_code == 409
    assert scope_required_response.json()["detail"]["error"] == "edit_scope_required"

    unchanged = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json={
        "operation_type": "ReplaceClip", "clip_id": "clip-route",
        "asset_id": asset.id, "segment_id": asset.segments[0].id,
    })
    assert unchanged.status_code == 409
    assert unchanged.json()["detail"]["error"] == "edit_no_change"
    assert production.list_edit_intents(workspace_id, task.id) == []

    preview_response = client.post(
        f"/api/creation-tasks/{task.id}/edits/" + ("exact/preview" if exact_replacement else "preview"),
        json={
            "operation_type": "ReplaceClip",
            "clip_id": "clip-route",
            "asset_id": replacement_asset.id,
            "segment_id": replacement_segment.id,
        } if exact_replacement else {
            "raw_command": "把这个镜头换成户外画面",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-route"],
            "playhead_ms": 500,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_payload = preview_response.json()
    assert preview_payload["intent"]["intent_type"] == ("exact_montage_edit" if exact_replacement else "replace_visual")
    unchanged_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert unchanged_plan.clips[0].asset_id == asset.id
    assert unchanged_plan.output_path == migrated_plan.output_path
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 1
    assert production.list_edit_operations(workspace_id, task.id) == []
    assert preview_payload["proposal"]["operation"]["operation_type"] == "ReplaceClip"
    replacement_payload = preview_payload["proposal"]["operation"]["payload"]
    assert (replacement_payload["asset_id"], replacement_payload["segment_id"]) != (
        asset.id,
        asset.segments[0].id,
    )
    assert preview_payload["proposal"]["invalidated_refs"] == jobs[0].output_refs
    assert preview_payload["proposal"]["external_effects"] == [
        "只更新任务版本；重新渲染前不会执行媒体进程。" if exact_replacement
        else "确认后只更新任务版本，不会立即调用模型或渲染。"
    ]

    confirm_response = client.post(
        f"/api/creation-tasks/{task.id}/edits/{preview_payload['proposal']['id']}/confirm"
    )
    assert confirm_response.status_code == 200, confirm_response.text
    assert confirm_response.json()["operation"]["status"] == "applied"
    updated_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert updated_plan.status == "ready"
    assert updated_plan.output_path is None and updated_plan.subtitle_path is None
    assert updated_plan.narration_audio_path == rendered_narration_reference
    assert (updated_plan.clips[0].asset_id, updated_plan.clips[0].segment_id) == (
        replacement_payload["asset_id"],
        replacement_payload["segment_id"],
    )
    assert updated_plan.clips[1].asset_id == asset.id
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 2
    assert len(production.list_edit_intents(workspace_id, task.id)) == 1
    assert len(production.list_edit_proposals(workspace_id, task.id)) == 1
    assert len(production.list_edit_operations(workspace_id, task.id)) == 1
    assert client.post(
        f"/api/creation-tasks/{task.id}/edits/{preview_payload['proposal']['id']}/confirm"
    ).status_code == 409

    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", fake_render
    )
    rerender = client.post(f"/api/creation-tasks/{task.id}/montage/render", json={})
    assert rerender.status_code == 200, rerender.text
    rerendered_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert rerendered_plan.status == "rendered"
    assert rerendered_plan.output_path not in jobs[0].output_refs
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 3
    workspace_after_rerender = client.get(f"/api/creation-tasks/{task.id}").json()
    assert workspace_after_rerender["task"]["timeline_sequence"] == 3
    assert workspace_after_rerender["task"]["can_undo"] is True
    assert client.post(f"/api/creation-tasks/{task.id}/montage/render", json={}).status_code == 200
    assert client.post(f"/api/creation-tasks/{task.id}/montage/render", json={}).status_code == 200
    assert client.get(f"/api/creation-tasks/{task.id}").json()["task"]["can_undo"] is True
    before_trim_start = rerendered_plan.clips[0].source_start_seconds

    exact_trim = client.post(
        f"/api/creation-tasks/{task.id}/edits/exact",
        json={
            "operation_type": "TrimClip",
            "clip_id": "clip-route",
            "source_start_seconds": before_trim_start + 0.5,
        },
    )
    assert exact_trim.status_code == 200, exact_trim.text
    assert exact_trim.json()["operation"]["operation_type"] == "TrimClip"
    trimmed_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert trimmed_plan.clips[0].source_start_seconds == before_trim_start + 0.5
    assert trimmed_plan.clips[1].source_start_seconds == 1.0
    assert trimmed_plan.narration_audio_path == rendered_narration_reference
    assert trimmed_plan.output_path is None and trimmed_plan.subtitle_path is None
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 6

    undo = client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={})
    assert undo.status_code == 200, undo.text
    assert undo.json()["operation"]["operation_type"] == "UndoTimelineVersion"
    restored_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert restored_plan.clips[0].source_start_seconds == before_trim_start
    assert restored_plan.clips[0].asset_id == replacement_payload["asset_id"]
    assert restored_plan.clips[1].source_start_seconds == 1
    assert restored_plan.narration_audio_path == rendered_narration_reference
    assert restored_plan.output_path is None and restored_plan.subtitle_path is None
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 7

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.get_creation_task(
        workspace_id, task.id
    ).current_timeline_version_id == production.list_timeline_versions(
        workspace_id, task.id
    )[-1].id
    assert creation_task_montage_plan_from_payload(
        reloaded.get_creation_task_montage_plan(workspace_id, task.id)
    ).clips[0].source_start_seconds == before_trim_start
    assert client.get(f"/api/creation-tasks/{task.id}").json()["task"]["can_undo"] is False

    final_render = client.post(f"/api/creation-tasks/{task.id}/montage/render", json={})
    assert final_render.status_code == 200, final_render.text
    assert final_render.json()["status"] == "rendered"
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 8
    final_output = client.get(f"/api/creation-tasks/{task.id}/output")
    assert final_output.status_code == 200
    assert final_output.content == b"playable-video"


@pytest.mark.parametrize("visual_only", [False, True])
def test_subtitle_exact_edit_uses_typed_operation_and_keeps_segmented_narration(
    tmp_path, monkeypatch, visual_only
):
    runtime_settings = main_module.settings
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    asset = _asset(tmp_path, workspace_id=workspace_id)
    production._save_asset(asset)
    creation_plan = CreationPlan(
        id="plan-subtitle-edit",
        workspace_id=workspace_id,
        goal="修改一条字幕",
        name="字幕同源编辑",
        target_count=1,
        language="zh-CN",
        aspect_ratio="9:16",
        duration_target_seconds=30,
        knowledge_references=[],
        source_references=[],
        selected_voice=None,
        pronunciation_references=[],
        approved_asset_count=1,
        approved_clip_count=1,
        material_rule_summary="测试素材",
        model_snapshot=[],
        confirmation_points=[],
        blockers=[],
        preflight_fingerprint="s" * 64,
        material_matches=[
            MaterialSegmentMatch(
                asset_ref=f"asset:{asset.id}",
                segment_ref=f"segment:{asset.segments[0].id}",
                score=1,
                reasons=["测试素材"],
            )
        ],
    )
    task = CreationTask(
        id="task-subtitle-edit",
        workspace_id=workspace_id,
        plan_id=creation_plan.id,
        name=creation_plan.name,
        scenario=creation_plan.goal,
    )
    production.confirm_creation_plan(creation_plan, task)
    now = datetime.now(timezone.utc)
    script = CreationTaskScript(
        id="script-subtitle-edit",
        workspace_id=workspace_id,
        task_id=task.id,
        run_id="run-subtitle-edit",
        topic_id="topic-subtitle-edit",
        title="字幕同源编辑",
        language="zh-CN",
        sections=[
            CreationTaskScriptSection(
                id="section-subtitle-edit",
                title="正文",
                content="原始字幕内容。",
                visual_prompt="演示画面",
                evidence_reference_ids=["knowledge:test"],
            )
        ],
        selection_status="confirmed",
        confirmed_at=now,
    )
    task = production.get_creation_task(workspace_id, task.id)
    task.confirmed_script_id = script.id
    task.stage = "narration"
    task.stage_status = "running"
    task.status = "running"
    with production._connect() as conn:
        conn.execute(
            "INSERT INTO creation_task_scripts (id, workspace_id, task_id, run_id, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                script.id,
                workspace_id,
                task.id,
                script.run_id,
                json.dumps(asdict(script), ensure_ascii=False, default=lambda value: value.isoformat()),
                now.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE creation_tasks SET payload = ?, updated_at = ? WHERE workspace_id = ? AND id = ?",
            (
                json.dumps(task.__dict__, ensure_ascii=False, default=lambda value: value.isoformat()),
                now.isoformat(),
                workspace_id,
                task.id,
            ),
        )
    segment_audio = tmp_path / "segment.wav"
    segment_audio.write_bytes(b"segment-audio")
    whole_audio = tmp_path / "whole.wav"
    whole_audio.write_bytes(b"whole-audio")
    production.save_narration_artifact(
        NarrationArtifactRecord(
            id="segment-artifact",
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id="voice-1",
            cache_key="segment-cache",
            audio_path=str(segment_audio),
            duration_seconds=4,
            sample_rate=24000,
            model_id="qwen3-tts",
        )
    )
    segment = NarrationSegment(
        id="narration-segment-subtitle",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        source_section_id=script.sections[0].id,
        text=script.sections[0].content,
        audio_reference="narration://artifact/segment-cache",
        duration_seconds=4,
        sample_rate=24000,
        model_id="qwen3-tts",
        voice_profile_id="voice-1",
        voice_version="v1",
        language="zh",
        pronunciation_version="p1",
        status="ready",
    )
    production.complete_creation_task_narration(
        NarrationArtifactRecord(
            id="whole-artifact",
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id="voice-1",
            cache_key="whole-cache",
            audio_path=str(whole_audio),
            duration_seconds=4,
            sample_rate=24000,
            model_id="qwen3-tts",
        ),
        segments=[segment],
    )
    montage = CreationTaskMontagePlan(
        id="montage-subtitle-edit",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id=script.id,
        script_title=script.title,
        status="rendered",
        cues=[
            NarrationCue(
                id="cue-subtitle-edit",
                source_section_id=script.sections[0].id,
                text="原始字幕\n内容。",
                duration_seconds=4,
                narration_segment_id=segment.id,
            )
        ],
        clips=[
            MontageClip(
                id="clip-subtitle-edit",
                cue_id="cue-subtitle-edit",
                asset_id=asset.id,
                segment_id=asset.segments[0].id,
                source_path=production.workspace_media_reference_for_asset(workspace_id, asset.id),
                source_start_seconds=0,
                source_end_seconds=4,
                timeline_start_seconds=0,
                timeline_end_seconds=4,
                match_score=1,
                match_reasons=["测试"],
                needs_confirmation=False,
            )
        ],
        duration_seconds=4,
        low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/whole-cache",
        output_path="artifacts/task-subtitle-edit/rendered/preview.mp4",
        subtitle_path="artifacts/task-subtitle-edit/rendered/subtitles.srt",
    )
    production.save_creation_task_montage_plan(
        workspace_id, task.id, montage.id, creation_task_montage_plan_to_payload(montage)
    )
    production.create_timeline_version(
        TimelineVersion(
            id="timeline-subtitle-edit-base",
            workspace_id=workspace_id,
            task_id=task.id,
            parent_version_id=None,
            sequence=1,
            snapshot={
                "montage": creation_task_montage_plan_to_payload(montage),
                "transform_run_id": "transform-subtitle-edit-rendered",
                "cover_asset_id": "cover_subtitle_edit_base",
            },
            created_by_operation_id=None,
        )
    )
    cover = production.create_cover_asset(CoverAsset(
        id="cover_subtitle_edit_base", workspace_id=workspace_id, task_id=task.id,
        timeline_version_id="timeline-subtitle-edit-base",
        source_video_ref=montage.output_path, frame_time_seconds=0.5,
        title="保留用户选择", safe_area={"left": 80, "top": 140, "right": 80, "bottom": 180},
        layout={"title_position": "bottom"}, generation_method="video_frame",
        model_or_engine="ffmpeg+Pillow", artifact_ref="artifacts/cover.png",
        transform_id="transform_cover", status="valid",
    ))
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)

    base = production.get_timeline_version(workspace_id, "timeline-subtitle-edit-base")
    chinese = main_module._subtitle_track_from_montage(workspace_id, task, base, montage)
    production.create_subtitle_track(chinese)
    production.create_subtitle_track(replace(chinese, id="fixture-english", language="en-US", source_language="zh-CN", cues=[
        replace(chinese.cues[0], id="english-cue", source_cue_id=chinese.cues[0].id, text="MANUAL TEST", line_breaks=[])
    ]))
    rejected_style = TestClient(main_module.app).post(
        f"/api/creation-tasks/{task.id}/edits/exact", json={
            "operation_type": "UpdateSubtitleCue", "track_id": "fixture-english",
            "cue_id": "english-cue", "text": "MANUAL TEST", "style": {"name": "emphasis"},
        },
    )
    assert rejected_style.status_code == 422
    response = TestClient(main_module.app).post(
        f"/api/creation-tasks/{task.id}/edits/exact",
        json={
            "operation_type": "UpdateSubtitleCue",
            "track_id": "subtitle_timeline-subtitle-edit-base_zh",
            "cue_id": montage.cues[0].id,
            "text": "原始\n字幕内容。" if visual_only else "修改后的字幕\n保留换行",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["operation"]["operation_type"] == "UpdateSubtitleCue"
    assert response.json()["narration"]["segments"][0]["audio_reference"] == segment.audio_reference
    saved = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert saved.cues[0].text == ("原始字幕内容。" if visual_only else "修改后的字幕保留换行")
    edited_track = production.list_subtitle_tracks(
        workspace_id,
        task.id,
        timeline_version_id=production.get_creation_task(
            workspace_id, task.id
        ).current_timeline_version_id,
    )
    assert len(edited_track) == (2 if visual_only else 1)
    edited_track = next(track for track in edited_track if track.language == "zh-CN")
    assert edited_track.cues[0].line_breaks == ([2] if visual_only else [6])
    assert saved.narration_audio_path == montage.narration_audio_path
    assert saved.output_path is None and saved.subtitle_path is None
    assert production.get_creation_task(workspace_id, task.id).narration_cache_key == "whole-cache"
    assert len(production.list_timeline_versions(workspace_id, task.id)) == 2
    assert production.list_edit_operations(workspace_id, task.id)[0].operation_type == "UpdateSubtitleCue"
    reloaded = ProductionRepository(production.database_path, production.video_library_path)
    assert reloaded.list_narration_segments(workspace_id, task.id, script.id)[0].audio_reference == segment.audio_reference

    undo = TestClient(main_module.app).post(
        f"/api/creation-tasks/{task.id}/timeline/undo",
        json={},
    )
    assert undo.status_code == 200, undo.text
    assert undo.json()["operation"]["operation_type"] == "UndoTimelineVersion"
    restored = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert restored.status == "rendered"
    assert restored.output_path == montage.output_path
    assert restored.subtitle_path == montage.subtitle_path
    restored_workspace = TestClient(main_module.app).get(
        f"/api/creation-tasks/{task.id}"
    ).json()
    assert restored_workspace["task"]["stage"] == "review"
    assert restored_workspace["task"]["output_ready"] is True
    assert restored_workspace["task"]["can_undo"] is False
    assert {track["language"] for track in restored_workspace["subtitle_tracks"]} == {"zh-CN", "en-US"}
    assert next(track for track in restored_workspace["subtitle_tracks"] if track["language"] == "zh-CN")["cues"][0]["line_breaks"] == [
        4
    ]
    assert restored_workspace["cover_asset"]["artifact_ref"] == cover.artifact_ref

    render_calls = []
    def render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path):
        render_calls.append(rendered_plan)
        (job_staging_path / "preview.mp4").write_bytes(b"render-fixture")
        (job_staging_path / "subtitles.srt").write_text(_plan_to_srt(rendered_plan))
        rendered_plan.status = "rendered"
        return rendered_plan

    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "output"))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path / "output")))
    monkeypatch.setattr(main_module, "_render_montage_with_resolved_clip_sources", render)
    client = TestClient(main_module.app)
    # New renderer, then cache hit: both must carry the same immutable cover selection.
    for _ in range(2):
        result = client.post(f"/api/creation-tasks/{task.id}/montage/render", json={})
        assert result.status_code == 200, result.text
        current = client.get(f"/api/creation-tasks/{task.id}").json()
        assert current["cover_asset"]["title"] == cover.title
        assert current["cover_asset"]["artifact_ref"] == cover.artifact_ref
        assert current["cover_asset"]["source_video_ref"] == cover.source_video_ref
        assert current["cover_asset"]["timeline_version_id"] == current["task"]["current_timeline_version_id"]
    assert len(render_calls) == 1

    # A rendering-contract change must not reuse an output from the previous engine.
    monkeypatch.setattr(main_module, "CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION", "next-render-contract")
    assert client.post(f"/api/creation-tasks/{task.id}/montage/render", json={}).status_code == 200
    assert len(render_calls) == 2

    from app.subtitle_tracks import SubtitleTranslationProviderError

    class FailedTranslationProvider:
        ready = True
        provider_name = "test-configured"
        model_id = "test-model"
        message = "configured"

        def translate(self, cues):
            raise SubtitleTranslationProviderError(
                code="insufficient_balance", reason="余额不足",
                impact="中文字幕和成片保持不变。", repair_action="充值后重试。", retryable=True,
            )

    monkeypatch.setattr(main_module, "settings", runtime_settings)
    monkeypatch.setattr(main_module, "subtitle_translation_provider_from_settings", lambda _: FailedTranslationProvider())
    current_workspace = client.get(f"/api/creation-tasks/{task.id}").json()
    current_chinese = next(track for track in current_workspace["subtitle_tracks"] if track["language"] == "zh-CN")
    changed_source = client.post(f"/api/creation-tasks/{task.id}/edits/exact", json={
        "operation_type": "UpdateSubtitleCue", "track_id": current_chinese["id"],
        "cue_id": current_chinese["cues"][0]["id"], "text": "测试新的翻译来源",
    })
    assert changed_source.status_code == 200, changed_source.text
    before = client.get(f"/api/creation-tasks/{task.id}").json()
    preview = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json={"operation_type": "ConfigureSubtitleTrack"})
    assert preview.status_code == 200, preview.text
    failed = client.post(f"/api/creation-tasks/{task.id}/edits/{preview.json()['proposal']['id']}/confirm")
    assert failed.status_code == 503, failed.text
    monkeypatch.setattr(main_module, "production_repo", ProductionRepository(production.database_path, production.video_library_path))
    after = client.get(f"/api/creation-tasks/{task.id}").json()
    assert after["task"]["current_timeline_version_id"] == before["task"]["current_timeline_version_id"]
    assert after["subtitle_tracks"] == before["subtitle_tracks"]
    assert after["cover_asset"] == before["cover_asset"]
    assert after["subtitle_translation_capability"]["message"] == "上次翻译未完成：余额不足"
    assert after["subtitle_translation_capability"]["repair_action"] == "充值后重试。"
    assert after["subtitle_translation_capability"]["available"] is True


def test_regenerate_person_render_replaces_only_target_and_undo_restores_old_render(
    person_render_edit_state,
    monkeypatch,
):
    state = person_render_edit_state
    production = state.production
    workspace_response = state.client.get(
        f"/api/creation-tasks/{state.task.id}"
    )
    assert workspace_response.status_code == 200, workspace_response.text
    assert workspace_response.json()["person_render_capability"] == {
        "available": True,
        "model_id": state.provider.model_id,
        "message": state.provider.message,
        "repair_action": None,
    }
    old_output_refs = [
        state.montage.output_path,
        state.montage.subtitle_path,
    ]
    unchanged_clip = asdict(state.montage.clips[1])
    unchanged_cues = [asdict(cue) for cue in state.montage.cues]

    preview = state.client.post(
        f"/api/creation-tasks/{state.task.id}/edits/preview",
        json={
            "raw_command": "这里只重做口型，其他都不要动",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-person-target"],
            "playhead_ms": 500,
        },
    )

    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["intent"]["intent_type"] == "regenerate_person_render"
    assert (
        preview_payload["proposal"]["operation"]["operation_type"]
        == "RegeneratePersonRender"
    )
    assert preview_payload["proposal"]["invalidated_refs"] == old_output_refs
    assert len(production.list_timeline_versions(state.workspace_id, state.task.id)) == 1

    confirm = state.client.post(
        f"/api/creation-tasks/{state.task.id}/edits/"
        f"{preview_payload['proposal']['id']}/confirm"
    )

    assert confirm.status_code == 200, confirm.text
    operation = confirm.json()["operation"]
    assert operation["operation_type"] == "RegeneratePersonRender"
    new_render_id = operation["payload"]["person_render_id"]
    assert new_render_id != state.old_render.id
    renders = production.list_person_renders(state.workspace_id, state.task.id)
    assert {render.id for render in renders} == {
        state.old_render.id,
        new_render_id,
    }
    new_render = next(render for render in renders if render.id == new_render_id)
    expected_cache_key = hashlib.sha256(
        (
            f"{state.old_render.cache_key}\n"
            f"regenerate:{preview_payload['proposal']['id']}"
        ).encode("utf-8")
    ).hexdigest()
    assert new_render.cache_key == expected_cache_key
    assert state.provider.output_cache_keys == [expected_cache_key]
    assert new_render.artifact_ref

    changed_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(
            state.workspace_id, state.task.id
        )
    )
    assert changed_plan.clips[0].person_render_id == new_render_id
    assert asdict(changed_plan.clips[1]) == unchanged_clip
    assert [asdict(cue) for cue in changed_plan.cues] == unchanged_cues
    assert changed_plan.narration_audio_path == state.montage.narration_audio_path
    assert changed_plan.output_path is None
    assert changed_plan.subtitle_path is None
    versions = production.list_timeline_versions(
        state.workspace_id, state.task.id
    )
    assert len(versions) == 2
    assert versions[-1].snapshot["invalidated_refs"] == old_output_refs
    version_plan = creation_task_montage_plan_from_payload(
        versions[-1].snapshot["montage"]
    )
    assert version_plan.clips[0].person_render_id == new_render_id
    assert asdict(version_plan.clips[1]) == unchanged_clip

    undo = state.client.post(
        f"/api/creation-tasks/{state.task.id}/timeline/undo",
        json={},
    )

    assert undo.status_code == 200, undo.text
    assert undo.json()["operation"]["operation_type"] == "UndoTimelineVersion"
    restored_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(
            state.workspace_id, state.task.id
        )
    )
    assert restored_plan.clips[0].person_render_id == state.old_render.id
    assert asdict(restored_plan.clips[1]) == unchanged_clip
    assert {render.id for render in production.list_person_renders(
        state.workspace_id, state.task.id
    )} == {state.old_render.id, new_render_id}

    def fake_render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path):
        job_staging_path.mkdir(parents=True, exist_ok=True)
        (job_staging_path / "preview.mp4").write_bytes(b"rerendered-video")
        (job_staging_path / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:04,000\n人物出镜讲解。\n",
            encoding="utf-8",
        )
        rendered_plan.status = "rendered"
        return rendered_plan

    monkeypatch.setattr(
        main_module,
        "_render_montage_with_resolved_clip_sources",
        fake_render,
    )
    rerender = state.client.post(
        f"/api/creation-tasks/{state.task.id}/montage/render",
        json={},
    )
    assert rerender.status_code == 200, rerender.text
    rerendered_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(
            state.workspace_id, state.task.id
        )
    )
    rerendered_output_refs = [
        rerendered_plan.output_path,
        rerendered_plan.subtitle_path,
    ]
    unchanged_after_undo = asdict(rerendered_plan.clips[1])
    cues_after_undo = [asdict(cue) for cue in rerendered_plan.cues]

    reuse_preview = state.client.post(
        f"/api/creation-tasks/{state.task.id}/edits/preview",
        json={
            "raw_command": "这一段换成当前人物出镜",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-person-target"],
            "playhead_ms": 500,
        },
    )

    assert reuse_preview.status_code == 200, reuse_preview.text
    reuse_payload = reuse_preview.json()
    assert (
        reuse_payload["proposal"]["operation"]["operation_type"]
        == "ChangePersonSegment"
    )
    assert (
        reuse_payload["proposal"]["operation"]["payload"][
            "reuse_person_render_id"
        ]
        == new_render_id
    )
    assert reuse_payload["proposal"]["invalidated_refs"] == rerendered_output_refs

    reuse_confirm = state.client.post(
        f"/api/creation-tasks/{state.task.id}/edits/"
        f"{reuse_payload['proposal']['id']}/confirm"
    )

    assert reuse_confirm.status_code == 200, reuse_confirm.text
    assert reuse_confirm.json()["operation"]["payload"]["person_render_id"] == new_render_id
    assert state.provider.output_cache_keys == [expected_cache_key]
    reused_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(
            state.workspace_id, state.task.id
        )
    )
    assert reused_plan.clips[0].person_render_id == new_render_id
    assert asdict(reused_plan.clips[1]) == unchanged_after_undo
    assert [asdict(cue) for cue in reused_plan.cues] == cues_after_undo
    assert reused_plan.narration_audio_path == rerendered_plan.narration_audio_path
    assert reused_plan.output_path is None
    assert reused_plan.subtitle_path is None
    assert {render.id for render in production.list_person_renders(
        state.workspace_id, state.task.id
    )} == {state.old_render.id, new_render_id}


def test_person_expression_unavailable_creates_no_edit_or_timeline(
    person_render_edit_state,
    monkeypatch,
):
    state = person_render_edit_state
    unavailable_provider = SimpleNamespace(
        ready=False,
        model_id="avatar-lipsync-unconfigured",
        message="人物口型能力尚未配置。",
    )
    monkeypatch.setattr(
        main_module, "avatar_lipsync_provider", unavailable_provider
    )
    workspace_response = state.client.get(
        f"/api/creation-tasks/{state.task.id}"
    )
    assert workspace_response.status_code == 200, workspace_response.text
    assert workspace_response.json()["person_render_capability"] == {
        "available": False,
        "model_id": unavailable_provider.model_id,
        "message": unavailable_provider.message,
        "repair_action": "配置本地人物口型运行环境后重试。",
    }

    response = state.client.post(
        f"/api/creation-tasks/{state.task.id}/edits/preview",
        json={
            "raw_command": "表情更自然",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-person-target"],
            "playhead_ms": 500,
        },
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "person_expression_control_unavailable"
    )
    assert state.production.list_edit_intents(
        state.workspace_id, state.task.id
    ) == []
    assert state.production.list_edit_proposals(
        state.workspace_id, state.task.id
    ) == []
    assert state.production.list_timeline_versions(
        state.workspace_id, state.task.id
    ) == []
