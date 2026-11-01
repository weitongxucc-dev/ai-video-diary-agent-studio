from __future__ import annotations

from app.subtitle_schedule import subtitle_schedule

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.creation_execution import CpuRenderGrantContext
from app.host_resource_probes import HostCapacity, HostProcessProbe
from app.avatar_lipsync import AvatarAndLipSyncResult, avatar_lipsync_cache_key
from app.models import (
    Episode,
    EpisodeStatus,
    ScriptPackage,
    ScriptSection,
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
    _score_candidate,
    _split_narration,
    _split_text_evenly,
    _subtitle_font,
    _subtitle_font_and_lines,
    _tokens,
    _validate_rendered_media,
    _write_subtitle_overlays,
    creation_task_montage_plan_from_payload,
    creation_task_montage_plan_to_payload,
    select_presenter_sections,
)
from app.creation.contracts import (
    CoverAsset,
    CreationPlanPersonReference,
    CreationPlanVoiceReference,
    CreationTask,
    NarrationSegment,
    SubtitleCue as ProductionSubtitleCue,
    SubtitleTrack,
    TimelineVersion,
)
from app.creation.narration_artifacts import NarrationArtifactRecord
from app.creation.person_renders import PersonRender
from app.production import MaterialAsset, MaterialSegment, MaterialSegmentMatch, ProductionBatch, ProductionProfile, ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.script_contracts import CreationTaskScript, CreationTaskScriptSection
from app.video_jobs import VideoJobRunner
from _media_preview_test_fixtures import register_synthetic_profiles, synthetic_wav_bytes, write_synthetic_video


pytestmark = pytest.mark.usefixtures("granted_montage_renderer")


def _assert_retired_montage_endpoint(client, production, workspace_id, task_id, stage):
    """Public retirement must reject before any job/version/artifact mutation."""
    with production._connect() as conn:
        before = tuple(conn.iterdump())
    response = client.post(f"/api/creation-tasks/{task_id}/montage/{stage}", json={})
    assert response.status_code == 409, response.text
    expected = "故事板必须进入设备队列" if stage == "plan" else "成片必须进入设备队列"
    assert expected in response.json()["detail"]
    with production._connect() as conn:
        assert tuple(conn.iterdump()) == before


def test_subtitle_export_uses_selected_current_tracks_and_fixed_chinese_first(monkeypatch):
    task = SimpleNamespace(id="task_export", current_timeline_version_id="version_2")
    source = SubtitleTrack(
        id="subtitle_zh", workspace_id="workspace_export", task_id=task.id,
        timeline_version_id="version_2", language="zh-CN", source_language=None,
        style={"bilingual_order": "translation_then_source"},
        cues=[ProductionSubtitleCue(id="cue_zh", source_cue_id=None, text="新中文",
                                    start_seconds=0.4, end_seconds=2.8)],
        generation_method="timeline", model_id=None, artifact_ref=None,
        transform_id=None, status="valid",
    )
    translation = replace(
        source, id="subtitle_en", language="en-US", source_language="zh-CN",
        cues=[ProductionSubtitleCue(id="cue_en", source_cue_id="cue_zh",
                                    text="New English", start_seconds=0.4, end_seconds=2.8)],
        generation_method="translation",
    )
    tracks = [source, translation]
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: "workspace_export")
    monkeypatch.setattr(main_module, "_creation_task_or_404", lambda _workspace, _task: task)
    monkeypatch.setattr(main_module, "_current_subtitle_tracks", lambda _workspace, _task: tracks)
    client = TestClient(main_module.app)
    url = f"/api/creation-tasks/{task.id}/output/selected-subtitles"
    params = {
        "expected_version_id": "version_2", "mode": "zh-en",
        "zh_track_id": source.id, "en_track_id": translation.id,
    }
    result = client.get(url, params=params)
    assert result.status_code == 200, result.text
    assert result.headers["content-type"].startswith("application/x-subrip")
    assert "00:00:00,400 --> 00:00:02,800\n新中文\nNew English" in result.text
    assert client.get(url, params={**params, "mode": "zh", "en_track_id": None}).status_code == 200
    assert client.get(url, params={**params, "expected_version_id": "version_1"}).status_code == 409
    assert client.get(url, params={**params, "zh_track_id": "another_task_track"}).status_code == 409
    tracks[1] = replace(translation, status="unavailable")
    assert client.get(url, params=params).status_code == 409
    tracks[1] = replace(translation, cues=[replace(translation.cues[0], source_cue_id="missing")])
    assert client.get(url, params=params).status_code == 409
    tracks.clear()
    assert client.get(url, params=params).status_code == 409


@pytest.mark.parametrize("text", [
    "Check subtitle text and timing. Listen to the audio mix. Preview changes before confirming, then export your video.",
    "Don't split English words or version 0.11 when creating captions for a video.",
    "检查 English subtitles 的显示时间，再 preview changes，然后导出 video。" * 3,
])
def test_automatic_narration_cues_keep_words_and_text(text):
    expected_words = re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", text)
    for parts in (_split_narration(text), _split_text_evenly(text, 7)):
        assert re.sub(r"\s", "", "".join(parts)) == re.sub(r"\s", "", text)
        assert [word for part in parts for word in re.findall(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*", part)] == expected_words
    assert _split_text_evenly("Extraordinary", 3) == ["Extraordinary"]
    assert _split_text_evenly("Hello, world", 1) == ["Hello, world"]
    assert _split_text_evenly("First, second, third", 2) == ["First, second,", "third"]


def test_matching_preserves_english_words_and_chinese_phrase_boundaries() -> None:
    assert _tokens("USB port 特写，桌面 操作") == {"usb", "port", "特写", "桌面", "操作"}
    cue = NarrationCue(
        id="cue-port", source_section_id="section-port", narration_segment_id="voice-port",
        text="Show the USB port", duration_seconds=4.0,
    )
    asset = SimpleNamespace(file_name="001.mp4", speech_analysis=None, user_tags=[])
    relevant = MaterialSegment(
        id="port", asset_id="asset", start_seconds=0, end_seconds=4,
        description="A close-up of a USB port",
    )
    unrelated = replace(relevant, id="desk", description="A wooden desk near a window")
    port_score = _score_candidate(cue, asset, relevant, 0, 0)
    desk_score = _score_candidate(cue, asset, unrelated, 0, 0)
    assert port_score[1] == 0.5
    assert desk_score[1] == 0.0
    assert port_score[0] > desk_score[0]


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

    captions = subtitle_schedule([track], plan.duration_seconds)

    assert captions[0].text == "本机页面记录来源，\n每一步都能追溯"
    assert captions[0].subtitle_emphasis_ranges == [(0, 4)]
    assert plan.cues[0].text == "旧字幕"
    assert captions[0].subtitle_style["emphasis_color"] == "#FFD54F"
    english = replace(track, id="english", language="en-US", source_language="zh-CN", cues=[
        replace(track.cues[0], id="english-cue", source_cue_id=track.cues[0].id,
                text="本机页面 English", line_breaks=[], emphasis=["English"])
    ])
    captions = subtitle_schedule([track, english], plan.duration_seconds)
    assert captions[0].subtitle_emphasis_ranges == [(0, 4), (len(track.cues[0].text) + 4, len(track.cues[0].text) + 11)]


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
    owner_user_id: str | None = None,
) -> MaterialAsset:
    path = tmp_path / "人工智能工具演示.mp4"
    write_synthetic_video(path)
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
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
        modified_at=modified_at,
        duration_seconds=24,
        media_type="video",
        owner_user_id=owner_user_id,
        usage_role="b_roll",
        review_status="approved",
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
    write_synthetic_video(path)
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return MaterialAsset(
        id="asset_a_roll",
        workspace_id="workspace_a",
        file_name=path.name,
        path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=modified_at,
        duration_seconds=24,
        media_type="video",
        usage_role="a_roll",
        review_status="approved",
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


def _person_render_edit_state(tmp_path, monkeypatch, workspace_id):
    from app.local_members import LocalMemberService

    output_root = tmp_path / "output"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    members = LocalMemberService(production.database_path)
    member, member_token = members.join(members.create_invite_code(), "合成测试成员")
    monkeypatch.setattr(main_module, "local_members_service", members)
    person, voice, _avatar_authorization, _voice_path, _image_path = register_synthetic_profiles(
        production, workspace_id=workspace_id, member_id=member.member_id,
        person_id="person-a", voice_id="voice-person", language="zh",
    )
    asset = _asset(tmp_path, workspace_id=workspace_id, owner_user_id=member.member_id)
    production._save_asset(asset)
    visual_reference = production.workspace_media_reference_for_asset(
        workspace_id, asset.id
    )
    with production._connect() as conn:
        production._claim_workspace_media_reference(
            conn, workspace_id=workspace_id, reference=visual_reference
        )
    selected_voice = CreationPlanVoiceReference(
        profile_id=voice.id,
        version=voice.version,
        person_profile_id=person.id,
        person_version=person.version,
        person_authorization_id=person.authorization.id,
        person_authorization_version=person.authorization.version,
        authorization_id=voice.authorization.id,
        authorization_version=voice.authorization.version,
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
        selected_person=CreationPlanPersonReference(person.id, person.version),
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
        owner_user_id=member.member_id,
    )
    production.plan_confirmation.confirm_creation_plan(creation_plan, task, verified_owner_user_id=member.member_id)
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
    task = production.task_records.get_creation_task(workspace_id, task.id)
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
    narration_path.write_bytes(synthetic_wav_bytes(4))
    narration_cache_key = "person-segment-cache"
    narration_reference = production.narration_artifacts.narration_artifact_reference(
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
        sample_rate=8000,
        model_id="qwen3-tts",
        voice_profile_id=voice.id,
        voice_version=voice.version,
        language="zh",
        pronunciation_version="p1",
        status="ready",
    )
    production.narration_completion.complete_creation_task_narration(
        NarrationArtifactRecord(
            id="narration-artifact-person-edit",
            workspace_id=workspace_id,
            task_id=task.id,
            script_id=script.id,
            voice_profile_id=voice.id,
            cache_key=narration_cache_key,
            audio_path=str(narration_path),
            duration_seconds=4,
            sample_rate=8000,
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
    render_request = production.profile_inputs.resolve_avatar_lipsync_request(
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
        authorization_id=person.authorization.id,
        authorization_version=person.authorization.version,
        model_id=provider.model_id,
        viewer_user_id=member.member_id,
    )
    base_cache_key = avatar_lipsync_cache_key(render_request)
    old_render = production.person_renders.create_person_render(
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
    production.person_renders.claim_person_render(workspace_id, task.id, old_render.id)
    old_render_path = output_root / "legacy" / "old-person-render.mp4"
    old_render_path.parent.mkdir(parents=True, exist_ok=True)
    old_render_path.write_bytes(b"old-person-render")
    old_render = production.person_renders.finish_person_render(
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
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        montage.id,
        creation_task_montage_plan_to_payload(montage),
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production.job_records))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(output_root))
    monkeypatch.setattr(
        main_module,
        "settings",
        replace(main_module.settings, video_output_path=str(output_root), video_inbox_path=str(production.video_library_path),
            native_portrait_worker_path=None, temporary_root_path=str(tmp_path / "temp")),
    )
    monkeypatch.setattr(main_module, "avatar_lipsync_provider", provider)
    monkeypatch.setattr(
        main_module, "current_workspace_id", lambda _request: workspace_id
    )
    return SimpleNamespace(
        workspace_id=workspace_id,
        production=production,
        members=members,
        member=member,
        member_token=member_token,
        task=task,
        montage=montage,
        old_render=old_render,
        provider=provider,
        client=TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}),
        request=Request({
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"x-local-member-token", member_token.encode("ascii"))],
        }),
    )


def synthetic_clip_preview_budget():
    from app.host_resource_probes import CpuRenderBudget
    # Finite metadata/synthetic-media test profile, never a host calibration or default.
    return CpuRenderBudget(max_duration_seconds=5, max_pixel_seconds=1920*1920*5*2,
        max_width_px=1920, max_height_px=1920, max_input_bytes=32*1024**2, max_clip_count=1,
        max_overlay_count=0, max_output_bytes=32*1024**2, max_threads=1, max_timeout_seconds=30,
        additional_memory_bytes=512*1024**2, additional_disk_bytes=64*1024**2, max_process_threads=16)


def _granted_domain_preview_adapter(state, monkeypatch, capture=None, *, facts_override=None):
    """Use real membership/version/JobRun/permit/checkpoint for domain assertions.

    This fixture's old-person file is an intentionally non-decodable lineage
    receipt. Only media measurement/rendering is a seam here. Real decoding,
    silence/size/duration checks remain in the separate real-media regressions.
    """
    from app.creation_execution import CreationExecutionActor, CreationJobOwnerVerifier, CreationJobUnitExecutor, CpuRenderLimits
    from app.device_runtime import OwnerRegistration, build_device_runtime
    from app.member_permits import MemberDeviceAccess
    from app.task_audio import TaskAudio, TaskMediaJobs, TASK_MEDIA_CONTEXT
    from app.media.clip_preview import ClipPreviewExecutor
    from app.media.picture_preview import PicturePreviewRenderer
    from app.host_resource_probes import HostProcessProbe
    from _media_preview_test_fixtures import ControlledResources
    actor = CreationExecutionActor(state.workspace_id,state.member.member_id)
    monkeypatch.setattr(main_module, "LEGACY_WORKSPACE_ID", state.workspace_id)
    def live_identity(candidate):
        return 'ready' if candidate==actor and any(member.member_id==actor.member_id for member in state.members.list_members()) else 'unauthorized'
    access = MemberDeviceAccess(state.members,identity_verifier=live_identity)
    monkeypatch.setattr(main_module,'settings',replace(main_module.settings,native_ocr_worker_path=None))
    clip_profile = synthetic_clip_preview_budget()
    selected_budget_for = main_module._device_operation_budget
    monkeypatch.setattr(main_module, '_device_operation_budget',
        lambda name: clip_profile if name == 'clip_preview' else selected_budget_for(name))
    def facts(workspace,task_id,operation,arguments,original):
        assert original==actor
        if '_clip_preview' in arguments:
            return main_module._task_media_facts(workspace, task_id, operation, arguments, original)

        current=state.production.task_records.get_creation_task(workspace,task_id)
        assert current.owner_user_id==state.member.member_id
        payload=state.production.get_creation_task_montage_plan(workspace,task_id)
        stable={key:value for key,value in payload.items() if key not in {'status','message','updated_at','output_path','subtitle_path'}}
        return {'base_version_id':current.current_timeline_version_id,'montage':stable}, {
            'kind':'creation_render','execution_mode':'cpu_ffmpeg','threads':1,'timeout_seconds':10,'max_output_bytes':1024**2}
    original_picture_render = PicturePreviewRenderer.render_picture_input
    def render(workspace,task_id,inputs,path,context):
        assert TASK_MEDIA_CONTEXT.get() is None
        assert context.grant.workspace_id==state.workspace_id and context.grant.owner_type=='creation_job'
        context.check()
        state.preview_inputs.append(inputs)
        if capture is not None:
            capture(inputs)
        if facts_override is not None:
            renderer = main_module._picture_preview_components(state.production)[2]
            original_picture_render(renderer, workspace, task_id, inputs, path, context)
        else:
            path.write_bytes(b'domain-render-receipt')
    def clip_input(inputs, path, context):
        # Metadata-lineage seam. Real FFmpeg media is verified in the permitted_media_state path.
        service = main_module._task_audio()
        return service.picture_renderer(state.workspace_id, state.task.id, inputs, path, context)
    if facts_override is None:
        monkeypatch.setattr('app.media.clip_preview.render_clip_input', clip_input)
        monkeypatch.setattr(PicturePreviewRenderer, 'render_picture_input',
            lambda self,workspace,task_id,inputs,path,context:render(workspace,task_id,inputs,path,context))
        def measured_video(path, context, *, expected_size, expected_duration_seconds, silent=False):
            context.check()
            assert path.read_bytes() == b'domain-render-receipt'
            return {'duration_seconds':expected_duration_seconds,'sample_rate':None,'channels':None}
        monkeypatch.setattr('app.montage.probe_preview_video', measured_video)
    original_measure = TaskAudio._measure_publication
    def measure(self,path,operation,inputs,context):
        if facts_override is not None or operation not in {"clip_preview", "picture_preview"}:
            return original_measure(self, path, operation, inputs, context)
        context.check()
        duration=(min(5,inputs['clip']['timeline_end_seconds']-inputs['clip']['timeline_start_seconds'])
                  if 'clip' in inputs else inputs['plan']['duration_seconds'])
        return {'duration_seconds':duration,'sample_rate':None,'channels':None}
    state.preview_inputs=[]
    media=TaskMediaJobs(state.production,facts=facts_override or facts,verify_identity=access.verify_identity,
        audio_ingress=main_module._audio_ingress_use_case(state.production),
        audio_edit=main_module._audio_edit_use_case(state.production),
        output_observation=main_module._output_observation_use_case(state.production),
        media_script=main_module._media_script_use_case(state.production),
        visual_preview=main_module._visual_preview_use_case(state.production),
        compound_selection=main_module._compound_edit_use_case(state.production),
        exact_preview=main_module._exact_preview_use_case(state.production),
        command_preview=main_module._command_preview_use_case(state.production),
        clip_preview=ClipPreviewExecutor(main_module._clip_preview_use_case(state.production),
            TaskAudio(state.production,main_module.artifact_store,main_module.settings.effective_temporary_root_path)))
    resources = ControlledResources()
    if facts_override is not None:
        from app.host_resource_probes import HostBudget, HostResourceProbe
        from tests.host_memory_boundary import IsolatedMemoryMonitor
        from test_host_resource_probes import _render_budget
        presenter_cpu = main_module._device_operation_budget("presenter_cpu_render")
        resources = HostResourceProbe(state.production.database_path, disk_paths=(state.production.database_path.parent,),
            budget=HostBudget(75, 1, 1, 1, 32*1024**2, 32*1024**2,
                cpu_render=replace(main_module._device_operation_budget("cpu_render"),running_cpu_policy=_render_budget().running_cpu_policy),
                clip_preview=clip_profile,presenter_cpu_render=presenter_cpu,cpu_audio=main_module._device_operation_budget("cpu_audio")),
            memory_monitor=IsolatedMemoryMonitor())
    runtime=build_device_runtime(database_path=state.production.database_path,
        device_lock_path=state.production.database_path.with_name('domain-preview.lock'),device_access=access,
        registrations={'creation_job':OwnerRegistration(CreationJobOwnerVerifier(state.production,
            verify_identity=access.verify_identity,task_media=media,
            cpu_render_limits=CpuRenderLimits(32*1024**2,1,30)),CreationJobUnitExecutor(state.production,
            render_action=None,render_is_valid=None,task_media=media))},resources=resources,
        processes=HostProcessProbe())
    state.preview_runtime=runtime
    monkeypatch.setattr(main_module,'_creation_runtime',lambda:runtime)
    monkeypatch.setattr(main_module, '_media_result_read_components',
        lambda: (runtime.registrations['creation_job'].owner.task_media, runtime.scheduler))
    def permit(request,workspace):
        member=state.members.resolve_member(request.headers.get('x-local-member-token'))
        assert member.member_id==actor.member_id and workspace==actor.workspace_id
        return actor
    monkeypatch.setattr(main_module,'_require_permit_actor',permit)
    monkeypatch.setattr(main_module,'_task_audio',lambda:TaskAudio(state.production,main_module.artifact_store,
        main_module.settings.effective_temporary_root_path,picture_renderer=render))
    monkeypatch.setattr(TaskAudio,'_measure_publication',measure)
    # The domain tests use persisted receipts, not claims of playable video.
    if facts_override is None:
        from app.creation.output_observation_use_case import OutputObservationUseCase
        monkeypatch.setattr(OutputObservationUseCase, 'published_outputs_current', lambda self, job: True)
    original_post=getattr(state, "domain_original_post", state.client.post)
    state.domain_original_post=original_post
    def post(*args,**kwargs):
        response=original_post(*args,**kwargs)
        if response.status_code!=202:
            return response
        receipt=response.json()
        assert receipt['result'] is None and receipt['device_request_id']
        assert runtime.run_once()
        completed=state.client.get(receipt['result_url'])
        with state.production._connect() as connection:
            pending=connection.execute('SELECT state,wait_reason FROM device_work_requests WHERE id=?',
                (receipt['device_request_id'],)).fetchone()
        assert completed.status_code==200 and completed.json()['status']=='succeeded',(completed.text,tuple(pending),getattr(state,'preview_error',None))
        response.status_code=200
        response._content=json.dumps(completed.json()['result']).encode()
        return response
    state.client.post=post
    return runtime


@pytest.fixture
def person_render_edit_state(tmp_path, monkeypatch):
    return _person_render_edit_state(tmp_path, monkeypatch, "workspace_a")


@pytest.fixture
def permitted_person_edit_state(tmp_path, monkeypatch):
    """Synthetic media, but a real roster, persisted request, OS lock and Grant."""
    from contextlib import contextmanager
    import fcntl
    import psutil
    import app.avatar_lipsync as avatar
    from app.creation_execution import (CreationExecution, CreationJobOwnerVerifier,
                                       CreationStageGrantContext, CpuRenderLimits, revision_digest)
    from app.device_scheduler import DeviceScheduler, SchedulePolicy, SubmissionIntent
    from app.host_resource_probes import HostBudget, HostResourceDecision, HostResourceProbe, MontageBudget, PresenterBudget
    from app.member_permits import MemberDeviceAccess, permit_actor
    from app.repository import LEGACY_WORKSPACE_ID

    state = _person_render_edit_state(tmp_path, monkeypatch, LEGACY_WORKSPACE_ID)
    plan = state.production.plan_records.get_creation_plan(state.workspace_id, state.task.plan_id)
    plan.presenter_share_target = 100
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    members = state.members
    actor = permit_actor(state.member, state.workspace_id)
    access = MemberDeviceAccess(members)
    budget = PresenterBudget(1, 1024**2, 30, 4096, 1, 5, 64*1024**2, 32, .1, 64*1024**2, 4096)
    owner = CreationJobOwnerVerifier(state.production, presenter_budget=budget,
        presenter_cpu_render_limits=CpuRenderLimits(4096, 1, 5), montage_budget=MontageBudget(1, 4, 100000, 100000, 5, 1),
        presenter_models={"fast": state.provider.model_id}, resolve_presenter_output=main_module._person_render_media_path,
        verify_identity=access.verify_identity)
    resources = HostResourceProbe(state.production.database_path, disk_paths=(tmp_path,),
        budget=HostBudget(75, 1, 1, 1, 1024**2, 1024**2, presenter=budget, montage=owner.montage_budget))
    # Explicit resource/timing fixtures; never sample this host or decode placeholder media.
    resources._sample = lambda declared, **options: HostResourceDecision(True, declared, {"fixture": {}}, None)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=1024**3))
    monkeypatch.setattr(psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(avatar, "_probe_avatar_output", lambda *args, **kwargs: None)
    def fake_person_render(*, request, output_path, grant_context):
        grant_context.check()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        grant_context.run([sys.executable, "-c", "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_bytes(b'new-person-render'); time.sleep(.25)", str(output)],
                          output_paths=(output,))
        state.provider.output_cache_keys.append(output.stem)
        state.provider.languages.append(request.language)
        from app.creation_execution import presenter_input_facts
        state.provider.base_cache_keys.append(avatar_lipsync_cache_key(request, input_facts=presenter_input_facts(request)))
        return AvatarAndLipSyncResult("succeeded", output.stem, str(output.resolve()), 4,
                                     '{"engine":"synthetic-provider; no media decoding"}', None, None, None)
    state.provider.render = fake_person_render
    state.provider.languages = []
    state.provider.base_cache_keys = []
    state.releases = []

    @contextmanager
    def granted(stage="person_render"):
        current = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
        plan = state.production.plan_records.get_creation_plan(state.workspace_id, current.plan_id)
        script = state.production.script_records.get_creation_task_script(state.workspace_id, current.confirmed_script_id)
        def forbidden(*args):
            pytest.fail("diagnostic must not run ungranted creation actions")
        execution = CreationExecution(state.production, run_narration=forbidden, run_montage_plan=forbidden,
                                      run_render=forbidden, render_is_valid=forbidden)
        job = execution.start(state.workspace_id, current.id, script.id, revision_digest(script, plan), actor=actor)
        key, _ = owner.stage_facts(current, script, plan, stage)
        scheduler = DeviceScheduler(state.production.database_path, tmp_path / "device.lock",
                                    owners=owner, resources=resources, processes=HostProcessProbe())
        grant = None
        succeeded = False
        try:
            scheduler.submit(SubmissionIntent(state.workspace_id, "creation_job", job.id, job.input_digest, key, SchedulePolicy()), actor)
            grant = scheduler.pick_and_grant()
            assert grant is not None and grant.unit_key == key
            yield CreationStageGrantContext(scheduler, grant, time.monotonic()+5, 4096, 1)
            succeeded = True
        finally:
            if grant is not None:
                with state.production._connect() as conn:
                    children = json.loads(conn.execute("SELECT children_json FROM device_permit WHERE id=1").fetchone()[0])
                if succeeded:
                    scheduler.complete_unit(grant, checkpoint_database_path=state.production.database_path,
                                            checkpoint_write=lambda conn, current: None)
                else:
                    scheduler.fail_unit(grant, reason="diagnostic_failed", failure_write=lambda conn, current: None)
                scheduler.assert_released(grant)
                with (tmp_path / "device.lock").open("r") as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                with state.production._connect() as conn:
                    permit = dict(conn.execute("SELECT state,generation,request_id FROM device_permit WHERE id=1").fetchone())
                ledger = json.loads(scheduler.ledger_path.read_text())
                assert permit["state"] == ledger["state"] == "free"
                state.releases.append({"children": children, "lock": "EX|NB reacquired", "permit": permit, "ledger": ledger})
            scheduler.close()
    from app.device_runtime import DeviceRuntime, OwnerRegistration
    from app.creation_execution import CreationJobUnitExecutor
    def forbidden(*args):
        pytest.fail("local person edit must not start full creation")
    executor = CreationJobUnitExecutor(state.production, render_action=forbidden,
        render_is_valid=forbidden, stage_actions={"person_edit": main_module._person_edit_stage_with_grant})
    scheduler = DeviceScheduler(state.production.database_path, tmp_path / "device.lock",
        owners=owner, resources=resources, processes=HostProcessProbe())
    state.runtime = DeviceRuntime(scheduler, {"creation_job": OwnerRegistration(owner, executor)}, poll_interval_seconds=1)
    state.owner, state.actor, state.members = owner, actor, members
    monkeypatch.setattr(main_module, "local_members_service", members)
    monkeypatch.setattr(main_module, "_creation_runtime", lambda: state.runtime)
    monkeypatch.setattr(main_module, '_media_result_read_components',
        lambda: (state.runtime.registrations['creation_job'].owner.task_media, state.runtime.scheduler))
    state.granted = granted
    yield state
    assert state.runtime.stop(1)
    (tmp_path / "release-evidence.json").write_text(json.dumps(state.releases, indent=2))


def prepare_current_presenter_media(state, monkeypatch):
    from _media_preview_test_fixtures import configure_synthetic_media_budgets
    from _media_preview_test_fixtures import synthetic_wav_bytes
    state.original_person_clip = replace(state.montage.clips[0])
    narration = state.production.narration_artifacts.resolve_authorized_narration_audio_reference(state.workspace_id, state.montage.narration_audio_path)
    narration.write_bytes(synthetic_wav_bytes(state.montage.duration_seconds))
    _save_fixture_narration_duration(state, state.montage.duration_seconds)
    # Real decoding tests use a synthetic four-second presenter source.
    path = main_module._person_render_media_path(state.old_render)
    write_synthetic_video(path, seconds=4)
    from app.creation_execution import presenter_inputs, presenter_input_facts
    from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
    from app.avatar_lipsync import avatar_lipsync_cache_key
    monkeypatch.setattr(main_module, "_presenter_model_ids", lambda: {"high": state.provider.model_id, "fast": state.provider.model_id})
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    script = state.production.script_records.get_creation_task_script(state.workspace_id, task.confirmed_script_id)
    plan = state.production.plan_records.get_creation_plan(state.workspace_id, task.plan_id)
    plan.presenter_share_target = 100
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    segment, request = presenter_inputs(state.production, task, script, plan, main_module._presenter_model_ids())[0]
    inputs = presenter_input_facts(request)
    key = avatar_lipsync_cache_key(request, input_facts=inputs)
    render = state.production.person_renders.create_person_render(replace(state.old_render, id="synthetic-current-presenter", cache_key=key,
        status="pending", output_path=None, artifact_ref=None, duration_seconds=None, alignment_evidence=None))
    state.production.person_renders.claim_person_render(state.workspace_id, task.id, render.id)
    published = main_module.artifact_store.publish_file(f"artifacts/{task.id}/synthetic-presenter.mp4", path)
    job = state.production.job_records.create_job_run(JobRun(id="synthetic-presenter-job", workspace_id=state.workspace_id,
        task_id=task.id, kind="person_render", idempotency_key=key, input_digest=key))
    state.production.execution_records.create_artifact_record(ArtifactRecord(id="synthetic-presenter-artifact", workspace_id=state.workspace_id,
        task_id=task.id, kind="person_render", logical_ref=published.logical_ref, sha256=published.sha256,
        size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=[segment.audio_reference]))
    state.production.lineage_records.create_transform_run(TransformRun(id="synthetic-presenter-transform", workspace_id=state.workspace_id,
        task_id=task.id, job_id=job.id, transform_type="person_render", input_digest=key, input_refs=[segment.audio_reference],
        output_refs=[published.logical_ref], engine="synthetic-presenter", parameters={"language":segment.language,
        "presenter_input_facts":inputs}, status="succeeded", started_at=job.created_at, finished_at=job.created_at))
    state.old_render = state.production.person_renders.finish_person_render(state.workspace_id, task.id, render.id, status="ready",
        output_path=str(path), duration_seconds=4, alignment_evidence='{"engine":"synthetic-presenter"}')
    state.old_render = state.production.person_renders.bind_person_render_artifact(state.workspace_id, task.id, render.id, published.logical_ref)
    state.montage = replace(state.montage, clips=[replace(clip, person_render_id=render.id,
        asset_id=f"person:{render.id}", source_path=f"person-render://{render.id}") if clip.person_render_id else clip for clip in state.montage.clips])
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id, task.id, state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    configure_synthetic_media_budgets(monkeypatch, main_module)
    return state


@pytest.fixture
def permitted_media_state(person_render_edit_state, monkeypatch):
    state = prepare_current_presenter_media(person_render_edit_state, monkeypatch)
    runtime = _granted_domain_preview_adapter(state, monkeypatch,
        facts_override=main_module._task_media_facts)
    yield state
    assert runtime.stop(1)


def _assert_unpermitted_person_confirm(state, proposal):
    with state.production._connect() as conn:
        before = tuple(conn.iterdump())
    root = Path(main_module.settings.video_output_path)
    outputs = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    calls = list(state.provider.output_cache_keys)
    budget = state.owner.presenter_budget
    state.owner.presenter_budget = None
    try:
        response = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/{proposal['id']}/confirm")
        assert response.status_code == 409 and "预算尚未标定" in response.text, response.text
    finally:
        state.owner.presenter_budget = budget
    assert state.provider.output_cache_keys == calls
    with state.production._connect() as conn:
        assert tuple(conn.iterdump()) == before
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == outputs


def test_trim_person_preview_accepts_legacy_and_synthetic_sources_but_checks_render_owner(person_render_edit_state):
    state = person_render_edit_state
    clip = state.montage.clips[0]
    cue = state.montage.cues[0]
    expected = f"/api/creation-tasks/{state.task.id}/person-renders/{state.old_render.id}/media#t=0.000,4.000"
    assert main_module._trim_clip_source_url(state.workspace_id, state.task.id, clip, cue) == expected
    synthetic = replace(clip, asset_id=f"person:{state.old_render.id}", source_path=f"person-render://{state.old_render.id}")
    assert main_module._trim_clip_source_url(state.workspace_id, state.task.id, synthetic, cue) == expected
    with pytest.raises(ValueError, match="配音段落不匹配"):
        main_module._trim_clip_source_url(state.workspace_id, state.task.id, clip, replace(cue, narration_segment_id="foreign"))
    with pytest.raises(KeyError):
        main_module._trim_clip_source_url(state.workspace_id, f"{state.task.id}-other", synthetic, cue)


def test_planner_matches_approved_segments_with_traceable_reasons(tmp_path):
    episode = _episode()
    episode.script.sections[0].content = "先识别重复工作，再看火箭发射，最后保留人工审核。"
    plan = MontagePlanner().build(
        workspace_id="workspace_a",
        batch=_batch(),
        episode=episode,
        profile=_profile(),
        assets=[_asset(tmp_path)],
    )

    assert plan.status == "waiting_for_user"
    assert plan.clips
    pending = [clip for clip in plan.clips if clip.needs_confirmation]
    assert plan.low_confidence_clip_count == len(pending)
    assert any("火箭发射" in next(cue.text for cue in plan.cues if cue.id == clip.cue_id) for clip in pending)
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


def test_planner_keeps_duration_or_filename_only_matches_waiting_for_confirmation(tmp_path):
    asset = _asset(tmp_path, with_metadata=False)
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

    with pytest.raises(ValueError, match="没有可自动选用的补充镜头"):
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
    assert any("画面语义排序分" in reason for reason in plan.clips[0].match_reasons)
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
    plan.status = "ready"
    plan.low_confidence_clip_count = 0
    for clip in plan.clips:
        clip.needs_confirmation = False
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
    plan.status = "ready"
    plan.low_confidence_clip_count = 0
    for clip in plan.clips:
        clip.needs_confirmation = False

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
@pytest.mark.parametrize("fit_mode,crop_x", [("fill", 0), ("fill", 1), ("fit", 0.5)])
def test_real_ffmpeg_renders_playable_vertical_preview(tmp_path, fit_mode, crop_x):
    video_path = tmp_path / "source.mp4"
    audio_path = tmp_path / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:d=1.5,drawbox=x=320:y=0:w=320:h=360:color=red:t=fill",
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
                fit_mode=fit_mode,
                crop_x=crop_x,
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
        pixel = frame.convert("RGB").getpixel((540, 1500))
        if fit_mode == "fit":
            assert max(pixel) < 15  # The whole landscape frame is letterboxed.
            assert frame.convert("RGB").getpixel((100, 700))[2] > 200
            assert frame.convert("RGB").getpixel((980, 700))[0] > 200
        elif crop_x == 0:
            assert pixel[2] > 200 and pixel[0] < 30
        else:
            assert pixel[0] > 200 and pixel[2] < 30


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
    plan = MontagePlanner().build_for_creation_task(duration_target_seconds=90,
        workspace_id="workspace_a",
        task_id="task_1",
        script=script,
        profile=ProductionProfile(workspace_id="workspace_a"),
        assets=[asset, _a_roll_asset(tmp_path)],
        media_reference_for_asset=lambda asset_id: f"workspace://{asset_id}.mp4",
        narration_audio_path="narration://artifact/cache_1",
        narration_duration_seconds=6.0,
    )

    assert plan.task_id == "task_1"
    assert plan.script_id == script.id
    assert plan.clips
    assert all(clip.asset_id == asset.id and clip.role == "b_roll" for clip in plan.clips)
    assert all(clip.source_path.startswith("workspace://") for clip in plan.clips)
    restored = creation_task_montage_plan_from_payload(
        creation_task_montage_plan_to_payload(plan)
    )
    assert restored == plan


def test_semantic_shot_query_is_independent_of_english_caption_rows(tmp_path):
    # Metadata boundary only: no video fixture generation, decoder or model.
    source = tmp_path / "metadata-only.mp4"
    source.write_bytes(b"metadata-only source")
    script = CreationTaskScript(id="script", workspace_id="workspace_a", task_id="task",
        run_id="run", topic_id="topic", title="One semantic scene", language="en-US",
        selection_status="confirmed", sections=[CreationTaskScriptSection(id="section", title="Machine",
            content="The footage shows an outdoor maize shelling site, with people working beside a green cylindrical machine.",
            visual_prompt="Green maize shelling machine", evidence_reference_ids=[])])
    asset = MaterialAsset(id="asset", workspace_id="workspace_a", file_name=source.name,
        path=str(source), size_bytes=source.stat().st_size,
        modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc),
        duration_seconds=12, usage_role="b_roll", review_status="approved", analysis_status="ready",
        segments=[MaterialSegment(id="source", asset_id="asset", start_seconds=0, end_seconds=12,
            description="Green maize shelling machine", usage_role="b_roll", review_status="approved")])
    queries = []
    provider = SimpleNamespace(ready=True, model_id="synthetic-semantic-port",
        embed_texts=lambda values: queries.extend(values) or [[1., 0.] for _ in values])
    plan = MontagePlanner(semantic_provider=provider, visual_feature_loader=lambda *_: [1., 0.]).build_for_creation_task(
        duration_target_seconds=30, workspace_id="workspace_a", task_id="task", script=script,
        profile=ProductionProfile(workspace_id="workspace_a"), assets=[asset],
        media_reference_for_asset=lambda _: "workspace://metadata-only.mp4",
        narration_audio_path="narration://artifact/frozen", narration_duration_seconds=7.68,
        narration_segment_ids_by_section={"section": "fixed-audio"},
        narration_segment_durations_by_section={"section": 7.68})
    assert queries == ["Green maize shelling machine"]
    assert len(plan.cues) == 4 and len(plan.clips) == 1
    assert plan.clips[0].timeline_end_seconds == 7.68 and plan.clips[0].cue_id == plan.cues[0].id
    assert creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(plan)) == plan


def test_presenter_share_selects_only_needed_narration_sections(tmp_path):
    sections = [(f"section_{index}", 4.0) for index in range(5)]
    assert select_presenter_sections(sections, 0) == []
    assert select_presenter_sections(sections, 20) == ["section_0"]
    assert len(select_presenter_sections(sections, 40)) == 2
    assert select_presenter_sections(sections, 100) == [item[0] for item in sections]

    script = CreationTaskScript(
        id="script_mix", workspace_id="workspace_a", task_id="task_mix",
        run_id="run_mix", topic_id="topic_mix", title="混剪", language="zh-CN",
        selection_status="confirmed",
        sections=[CreationTaskScriptSection(
            id=section_id, title=f"第 {index + 1} 段", content="介绍产品。",
            visual_prompt="产品画面", evidence_reference_ids=[],
        ) for index, (section_id, _) in enumerate(sections)],
    )
    chosen = select_presenter_sections(sections, 20)
    plan = MontagePlanner().build_for_creation_task(duration_target_seconds=90,
        workspace_id="workspace_a", task_id="task_mix", script=script,
        profile=ProductionProfile(workspace_id="workspace_a"),
        assets=[_asset(tmp_path)],
        media_reference_for_asset=lambda asset_id: f"workspace://{asset_id}.mp4",
        narration_audio_path="narration://artifact/mix", narration_duration_seconds=20,
        narration_segment_ids_by_section={section_id: f"narration_{index}" for index, (section_id, _) in enumerate(sections)},
        narration_segment_durations_by_section=dict(sections),
        person_render_by_section={section_id: ("render_0", 4.0) for section_id in chosen},
        presenter_share_target=20,
    )
    cues = {cue.id: cue for cue in plan.cues}
    face_clips = [clip for clip in plan.clips if clip.person_render_id]
    material_clips = [clip for clip in plan.clips if not clip.person_render_id]
    assert face_clips and material_clips
    assert {cues[clip.cue_id].source_section_id for clip in face_clips} == set(chosen)
    assert all(clip.role == "b_roll" and clip.source_path.startswith("workspace://") for clip in material_clips)
    assert sum(clip.timeline_end_seconds - clip.timeline_start_seconds for clip in face_clips) == pytest.approx(4.0)
    assert plan.duration_seconds == pytest.approx(20.0)
    assert plan.presenter_share_target == 20

    all_face = MontagePlanner().build_for_creation_task(duration_target_seconds=90,
        workspace_id="workspace_a", task_id="task_mix", script=script,
        profile=ProductionProfile(workspace_id="workspace_a"), assets=[],
        media_reference_for_asset=lambda asset_id: f"workspace://{asset_id}.mp4",
        narration_audio_path="narration://artifact/mix", narration_duration_seconds=20,
        narration_segment_ids_by_section={section_id: f"narration_{index}" for index, (section_id, _) in enumerate(sections)},
        narration_segment_durations_by_section=dict(sections),
        person_render_by_section={section_id: (f"render_{index}", 4.0) for index, (section_id, _) in enumerate(sections)},
        presenter_share_target=100,
    )
    assert all(clip.person_render_id for clip in all_face.clips)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed")
def test_mixed_person_and_material_clips_render_with_continuous_narration(tmp_path):
    face = tmp_path / "face.mp4"
    material = tmp_path / "material.mp4"
    audio = tmp_path / "narration.wav"
    for color, path in (("red", face), ("blue", material)):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=25:d=1",
                        "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(audio)], check=True, capture_output=True)
    plan = CreationTaskMontagePlan(
        id="mixed-render", workspace_id="workspace_a", task_id="task-mixed",
        script_id="script-mixed", script_title="混剪", status="ready",
        cues=[NarrationCue("cue-face", "section-face", "真人出镜。", 1.0, "narration-face"),
              NarrationCue("cue-material", "section-material", "产品画面。", 1.0, "narration-material")],
        clips=[
            MontageClip("clip-face", "cue-face", "person:render", "narration-face", str(face),
                        0.0, 1.0, 0.0, 1.0, 1.0, ["当前配音口型"], False,
                        role="a_roll", person_render_id="render"),
            MontageClip("clip-material", "cue-material", "asset", "segment", str(material),
                        0.0, 1.0, 1.0, 2.0, 1.0, ["产品画面"], False),
        ],
        duration_seconds=2.0, low_confidence_clip_count=0,
        presenter_share_target=40, narration_audio_path="narration://artifact/test",
    )
    MontageRenderer(tmp_path).render(plan, audio, job_staging_path=tmp_path / "job")
    output = tmp_path / "job" / "preview.mp4"
    assert output.is_file() and output.stat().st_size > 0
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
                                      check=True, capture_output=True, text=True).stdout)
    assert {item["codec_type"] for item in probe["streams"]} >= {"video", "audio"}
    assert float(next(item["duration"] for item in probe["streams"] if item["codec_type"] == "video")) == pytest.approx(2, abs=0.12)
    def sample_color(at: float) -> bytes:
        return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(output),
                               "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              check=True, capture_output=True).stdout
    red, blue = sample_color(0.4), sample_color(1.4)
    assert red[0] > red[2] * 2
    assert blue[2] > blue[0] * 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed")
def test_creation_montage_runs_all_media_commands_under_one_grant(tmp_path, monkeypatch):
    # 准入门禁的请求与上限校验保持真实执行，只把环境采样钉死，避免机器繁忙与否决定本用例成败。
    pinned_capacity = HostCapacity(cpu_count=8, cpu_threads_ceiling=7,
                                   available_memory_bytes=8 * 1024 ** 3, load_avg_1m=0.5)
    monkeypatch.setattr("app.montage.sample_host_capacity", lambda **_kwargs: pinned_capacity)
    video = tmp_path / "source.mp4"
    audio = tmp_path / "narration.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x240:r=25:d=1", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(audio)], check=True, capture_output=True)
    plan = CreationTaskMontagePlan(
        id="granted-render", workspace_id="workspace_a", task_id="task-granted",
        script_id="script-granted", script_title="短片", status="ready",
        cues=[NarrationCue("cue", "section", "短片。", 1.0, "segment")],
        clips=[MontageClip("clip", "cue", "asset", "segment", str(video),
                           0.0, 1.0, 0.0, 1.0, 1.0, ["蓝色画面"], False)],
        duration_seconds=1.0, low_confidence_clip_count=0,
        presenter_share_target=0, narration_audio_path="narration://artifact/test",
    )

    class RecordingGrant:
        processes = HostProcessProbe()
        # Isolated synthetic media profile; no real machine-budget claim.
        resources = SimpleNamespace(_grant_resources=lambda _: {"kind":"creation_render"},
            budget=SimpleNamespace(cpu_render=SimpleNamespace(additional_disk_bytes=64 * 1024**2)))

        def __init__(self):
            self.children = []

        def assert_grant(self, grant):
            assert grant == "one-render-grant"

        def register_child(self, grant, child):
            self.assert_grant(grant)
            self.children.append(child)

    commands = []
    import app.montage as montage_module
    actual_runner = montage_module._run_granted_media_command
    def record_runner(scheduler, grant, command, **kwargs):
        commands.append(command.copy())
        return actual_runner(scheduler, grant, command, **kwargs)
    monkeypatch.setattr(montage_module, "_run_granted_media_command", record_runner)
    scheduler = RecordingGrant()
    rendered = MontageRenderer(tmp_path).render(
        plan, audio, job_staging_path=tmp_path / "job", scheduler=scheduler,
        grant="one-render-grant", deadline_monotonic=time.monotonic() + 90,
        max_output_bytes=8 * 1024 * 1024, threads=2,
    )
    encode = next(command for command in commands if command[0] == "ffmpeg")
    inputs = [index for index, value in enumerate(encode) if value == "-i"]
    assert len(inputs) == 3  # video, authorized narration, current subtitle image
    assert all(encode[index - 2:index] == ["-threads", "2"] for index in inputs)
    assert encode[encode.index("-threads:v") + 1] == "2"
    assert encode[encode.index("-filter_threads") + 1] == "2"
    assert encode[encode.index("-filter_complex_threads") + 1] == "2"
    assert encode[encode.index("-fs") + 1] == str(8 * 1024 * 1024)
    assert rendered.status == "rendered"
    assert (tmp_path / "job" / "preview.mp4").stat().st_size <= 8 * 1024 * 1024
    assert len(scheduler.children) == 3  # duration probe, encoder, output probe
    with pytest.raises(OSError, match="时长不足"):
        _validate_rendered_media(tmp_path / "job" / "preview.mp4", expected_size=(1080, 1920),
                                 expected_duration_seconds=5)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg is not installed",
)
@pytest.mark.parametrize("subtitle_mode", ["source", "bilingual", "hidden"])
def test_creation_task_renderer_writes_task_scoped_mp4_and_srt(tmp_path, subtitle_mode):
    bilingual = subtitle_mode != "source"
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
    for mismatched_duration in (0.6, 2.0):
        mismatched = replace(plan, duration_seconds=mismatched_duration)
        with pytest.raises(ValueError, match="配音与剪辑时长不一致"):
            renderer.render(mismatched, audio_path, job_staging_path=tmp_path / "rejected")
        assert not (tmp_path / "rejected" / "preview.mp4").exists()
    if bilingual:
        # Explicit artificial translation fixture: exercises the production track-to-render
        # path, never a model-call assertion or a persisted user task.
        chinese = SubtitleTrack(
            id="fixture-zh", workspace_id="workspace_a", task_id="task_real",
            timeline_version_id="fixture-version", language="zh-CN", source_language=None,
            style={"font_family": "PingFang SC" if sys.platform == "darwin" else _subtitle_font(40).getname()[0], "font_size": 40, "position": "bottom"},
            cues=[ProductionSubtitleCue(
                id="cue_001", source_cue_id=None, text="人工测试字幕", start_seconds=.3, end_seconds=.8,
                line_breaks=[2], emphasis=[],
            )],
            generation_method="timeline", model_id=None, artifact_ref=None, transform_id=None, status="valid",
        )
        english = replace(
            chinese, id="fixture-en", language="en-US", source_language="zh-CN",
            cues=[ProductionSubtitleCue(
                id="fixture-en-cue", source_cue_id="cue_001",
                text="MANUAL TEST SUBTITLESNot a model translation",
                start_seconds=.3, end_seconds=.8, line_breaks=[21], emphasis=["MANUAL"],
            )],
        )
        captions = subtitle_schedule([chinese, english], plan.duration_seconds)
        assert captions[0].text == "人工\n测试字幕\nMANUAL TEST SUBTITLES\nNot a model translation"
    with pytest.raises(ValueError, match="job-scoped"):
        renderer.render(plan, audio_path)

    if subtitle_mode == "hidden":
        chinese = replace(chinese, style={**chinese.style, "enabled": False})
        english = replace(english, style={**english.style, "enabled": False})
    job_staging_path = tmp_path / "temp" / "job_render_1"
    rendered = renderer.render(
        plan,
        audio_path,
        job_staging_path=job_staging_path,
        subtitle_tracks=[chinese, english] if bilingual else None,
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
    assert white_pixels == 0 if subtitle_mode == "hidden" else white_pixels > 50
    if bilingual:
        assert "00:00:00,300 --> 00:00:00,800" in srt
        for moment in (0.1, 1.0):
            outside = tmp_path / f"outside-{moment}.png"
            subprocess.run(["ffmpeg", "-y", "-ss", str(moment), "-i", str(staged_output),
                            "-frames:v", "1", str(outside)], check=True, capture_output=True)
            with Image.open(outside) as frame:
                region = frame.convert("RGB").crop((0, 1300, 1080, 1800))
                assert sum(r > 220 and g > 220 and b > 220 for r,g,b in region.get_flattened_data()) == 0
        if subtitle_mode != "hidden":
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
        def __init__(self):
            self.options = []

        def render(self, rendered_plan, resolved_audio_path, **options):
            assert Path(rendered_plan.clips[0].source_path).is_absolute()
            rendered_plan.narration_audio_path = str(resolved_audio_path)
            rendered_plan.status = "rendered"
            self.options.append(options)
            return rendered_plan

    renderer = MutatingRenderer()
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "montage_renderer", renderer)

    rendered = main_module._render_montage_with_resolved_clip_sources(
        workspace_id,
        plan,
        audio_path,
    )

    assert rendered.clips[0].source_path == logical_clip_reference
    context = CpuRenderGrantContext(object(), object(), time.monotonic() + 30, 1_000_000, 1)
    main_module._render_montage_with_resolved_clip_sources(
        workspace_id, plan, audio_path, job_staging_path=tmp_path / "staging",
        render_context=context,
    )
    assert renderer.options[-1]["scheduler"] is context.scheduler
    assert renderer.options[-1]["grant"] is context.grant
    assert renderer.options[-1]["max_output_bytes"] == 1_000_000
    assert plan.clips[0].source_path == logical_clip_reference
    prior_calls = len(renderer.options)
    plan.clips[0].visual_effects = {"beauty_strength": 0.3}
    with pytest.raises(ValueError, match="人像效果"):
        main_module._render_montage_with_resolved_clip_sources(
            workspace_id, plan, audio_path, job_staging_path=tmp_path / "staging",
            render_context=context,
        )
    assert len(renderer.options) == prior_calls
    assert plan.clips[0].source_path == logical_clip_reference
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
    production.plan_confirmation.confirm_creation_plan(creation_plan, task)
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
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        plan.id,
        creation_task_montage_plan_to_payload(plan),
    )
    rendered_task = production.task_records.get_creation_task(workspace_id, task.id)
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
    restored_task = production.task_records.get_creation_task(workspace_id, task.id)
    assert restored_task.stage == "review"
    assert restored_task.stage_status == "waiting_for_user"
    assert restored_task.output_ready is True
    assert production.edit_records.list_edit_operations(workspace_id, task.id) == []


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("exact_replacement", [False, True])
@pytest.mark.parametrize("mixed_color", [False, True])
def test_creation_task_render_route_persists_job_lineage_and_logical_outputs(
    tmp_path, monkeypatch, exact_replacement, language, mixed_color
):
    from app.local_members import LocalMemberService

    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    members = LocalMemberService(production.database_path)
    member, member_token = members.join(members.create_invite_code(), "合成测试成员")
    monkeypatch.setattr(main_module, "local_members_service", members)
    asset = _asset(tmp_path, workspace_id=workspace_id, owner_user_id=member.member_id)
    production._save_asset(asset)
    replacement_path = tmp_path / "replacement-outdoor.mp4"
    write_synthetic_video(replacement_path)
    replacement_segment = replace(
        asset.segments[0],
        asset_id="asset_replacement",
        id="segment_replacement",
        description="户外画面，产品补充素材",
        system_tags=["场景:户外"],
    )
    replacement_asset = replace(
        asset,
        id="asset_replacement",
        file_name=replacement_path.name,
        path=str(replacement_path),
        size_bytes=replacement_path.stat().st_size,
        modified_at=datetime.fromtimestamp(replacement_path.stat().st_mtime, timezone.utc),
        segments=[replacement_segment],
        owner_user_id=member.member_id,
    )
    production._save_asset(replacement_asset)
    for source_asset in (asset, replacement_asset):
        source_reference = production.workspace_media_reference_for_asset(
            workspace_id, source_asset.id
        )
        with production._connect() as conn:
            production._claim_workspace_media_reference(
                conn, workspace_id=workspace_id, reference=source_reference
            )
    assert (replacement_asset.id, replacement_segment.id) in {
        (candidate_asset.id, candidate_segment.id)
        for candidate_asset, candidate_segment in production.automatic_material_segments(
            workspace_id, viewer_user_id=member.member_id
        )
    }
    creation_plan = CreationPlan(
        id="plan-render-route",
        workspace_id=workspace_id,
        goal="渲染一条可追溯成片",
        name="渲染迁移",
        target_count=1,
        language=language,
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
        owner_user_id=member.member_id,
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
        owner_user_id=member.member_id,
    )
    production.plan_confirmation.confirm_creation_plan(creation_plan, task, verified_owner_user_id=member.member_id)
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
    plan.clips[0].visual_effects = {"exposure": 0.5, "saturation": 0.8}
    plan.clips[1].visual_effects = {"exposure": 1 if mixed_color else 0.5, "saturation": 0.8}
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        plan.id,
        creation_task_montage_plan_to_payload(plan),
    )
    output_root = tmp_path / "output"
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"audio")

    def fake_render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path, subtitle_tracks=None):
        job_staging_path.mkdir(parents=True, exist_ok=True)
        (job_staging_path / "preview.mp4").write_bytes(b"playable-video")
        (job_staging_path / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n字幕内容\n", encoding="utf-8"
        )
        rendered_plan.status = "rendered"
        return rendered_plan

    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production.job_records))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(output_root))
    monkeypatch.setattr(
        main_module, "settings", SimpleNamespace(video_output_path=str(output_root), video_inbox_path=str(tmp_path), native_portrait_worker_path=None, effective_temporary_root_path=tmp_path / "temp")
    )
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    monkeypatch.setattr(
        production.narration_artifacts,
        "resolve_authorized_narration_audio_reference",
        lambda _workspace_id, _reference: audio_path,
    )
    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", fake_render
    )

    client = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token})
    _assert_retired_montage_endpoint(client, production, workspace_id, task.id, "render")
    # Existing helper remains diagnostic-only: no public execution fallback or real FFmpeg here.
    original_create_artifact_record = production.execution_records.create_artifact_record

    class SimulatedProcessExit(BaseException):
        pass

    monkeypatch.setattr(
        production.execution_records,
        "create_artifact_record",
        lambda _artifact: (_ for _ in ()).throw(SimulatedProcessExit()),
    )
    with pytest.raises(SimulatedProcessExit):
        main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)

    interrupted_job = production.job_records.list_job_runs(workspace_id, task.id)[0]
    assert interrupted_job.status == "running"
    assert production.execution_records.list_artifact_records(workspace_id, task.id) == []
    assert (output_root / "artifacts" / task.id / interrupted_job.id / "preview.mp4").is_file()
    assert (output_root / "artifacts" / task.id / interrupted_job.id / "subtitles.srt").is_file()

    monkeypatch.setattr(
        production.execution_records, "create_artifact_record", original_create_artifact_record
    )
    assert main_module.video_job_runner.recover_after_restart() == 1

    def reject_rerender(*_args, **_kwargs):
        pytest.fail("恢复已发布产物时不应再次执行 FFmpeg")

    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", reject_rerender
    )
    response = main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)

    assert response.status == "rendered"
    jobs = production.job_records.list_job_runs(workspace_id, task.id)
    assert len(jobs) == 1 and jobs[0].status == "succeeded"
    artifacts = production.execution_records.list_artifact_records(workspace_id, task.id)
    assert {artifact.kind for artifact in artifacts} == {
        "rendered_video",
        "subtitle_track",
    }
    transforms = production.lineage_records.list_transform_runs(workspace_id, task.id)
    assert len(transforms) == 1 and transforms[0].status == "succeeded"
    assert transforms[0].output_refs == jobs[0].output_refs
    assert len(production.lineage_records.list_dependency_edges(workspace_id, task.id)) == 6
    versions = production.timeline_records.list_timeline_versions(workspace_id, task.id)
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

    original_task = asdict(production.task_records.get_creation_task(workspace_id, task.id))
    saved_template = client.post(f"/api/creation-tasks/{task.id}/templates", json={"name": "成片设置", "style_instruction": "简洁说明"})
    assert saved_template.status_code == 200, saved_template.text
    template = saved_template.json()
    assert template["source_timeline_version_id"] == original_task["current_timeline_version_id"]
    assert template["settings"]["fit_mode"] == "fill"
    assert template["settings"]["color_style"] == (None if mixed_color else {
        "exposure": 0.5, "saturation": 0.8, "contrast": 1, "temperature": 0,
    })
    assert "字幕内容" not in json.dumps(template, ensure_ascii=False)
    new_version = client.post(f"/api/creation-tasks/{task.id}/templates", json={"name": "成片设置", "template_id": template["id"], "expected_version": 1})
    assert new_version.status_code == 200 and new_version.json()["version"] == 2
    conflict = client.post(f"/api/creation-tasks/{task.id}/templates", json={"name": "冲突", "template_id": template["id"], "expected_version": 1})
    assert conflict.status_code == 409
    assert asdict(production.task_records.get_creation_task(workspace_id, task.id)) == original_task
    assert client.get(f"/api/creation-tasks/{task.id}/output").content == output_response.content

    rendered_narration_reference = saved_plan.narration_audio_path
    saved_plan.output_path = str(tmp_path / "legacy-preview.mp4")
    saved_plan.subtitle_path = str(tmp_path / "legacy-subtitles.srt")
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        saved_plan.id,
        creation_task_montage_plan_to_payload(saved_plan),
    )
    legacy_base_response = client.post(
        f"/api/creation-tasks/{task.id}/edits/preview",
        json={
            "raw_command": "把这个镜头换成另一个镜头，保留当前配音",
            "context_stage": "review",
            "selected_object_refs": ["clip:clip-route"],
            "playhead_ms": 500,
        },
    )
    assert legacy_base_response.status_code == 409
    assert legacy_base_response.json()["detail"]["error"] == "edit_base_not_migrated"
    assert production.edit_records.list_edit_intents(workspace_id, task.id) == []
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
    migrated_render = main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)
    assert migrated_render.status == "rendered"
    migrated_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert migrated_plan.output_path == jobs[0].output_refs[0]
    assert migrated_plan.subtitle_path == jobs[0].output_refs[1]
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 1
    assert production.task_records.get_creation_task(
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
        assert production.edit_records.list_edit_intents(workspace_id, task.id) == []

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
    assert production.edit_records.list_edit_intents(workspace_id, task.id) == []

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
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 1
    assert production.edit_records.list_edit_operations(workspace_id, task.id) == []
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
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 2
    assert len(production.edit_records.list_edit_intents(workspace_id, task.id)) == 1
    assert len(production.edit_records.list_edit_proposals(workspace_id, task.id)) == 1
    assert len(production.edit_records.list_edit_operations(workspace_id, task.id)) == 1
    assert client.post(
        f"/api/creation-tasks/{task.id}/edits/{preview_payload['proposal']['id']}/confirm"
    ).status_code == 409

    monkeypatch.setattr(
        main_module, "_render_montage_with_resolved_clip_sources", fake_render
    )
    rerender = main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)
    assert rerender.status == "rendered"
    rerendered_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert rerendered_plan.status == "rendered"
    assert rerendered_plan.output_path not in jobs[0].output_refs
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 3
    workspace_after_rerender = client.get(f"/api/creation-tasks/{task.id}").json()
    assert workspace_after_rerender["task"]["timeline_sequence"] == 3
    assert workspace_after_rerender["task"]["can_undo"] is True
    assert main_module._render_creation_task_montage_for_workspace(workspace_id, task.id).status == "rendered"
    assert main_module._render_creation_task_montage_for_workspace(workspace_id, task.id).status == "rendered"
    assert client.get(f"/api/creation-tasks/{task.id}").json()["task"]["can_undo"] is True
    before_trim_start = rerendered_plan.clips[0].source_start_seconds
    before_trim_version = production.task_records.get_creation_task(workspace_id, task.id).current_timeline_version_id
    proposal_count = len(production.edit_records.list_edit_proposals(workspace_id, task.id))
    original_resolve = production.resolve_clip_media_reference
    monkeypatch.setattr(
        production, "resolve_clip_media_reference",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("源素材已断开")),
    )
    unavailable_preview = client.post(
        f"/api/creation-tasks/{task.id}/edits/exact/preview",
        json={"operation_type": "TrimClip", "clip_id": "clip-route", "source_start_seconds": before_trim_start + 0.5},
    )
    assert unavailable_preview.status_code == 422
    assert production.task_records.get_creation_task(workspace_id, task.id).current_timeline_version_id == before_trim_version
    assert len(production.edit_records.list_edit_proposals(workspace_id, task.id)) == proposal_count
    monkeypatch.setattr(production, "resolve_clip_media_reference", original_resolve)

    trim_preview = client.post(
        f"/api/creation-tasks/{task.id}/edits/exact/preview",
        json={
            "operation_type": "TrimClip",
            "clip_id": "clip-route",
            "source_start_seconds": before_trim_start + 0.5,
        },
    )
    assert trim_preview.status_code == 200, trim_preview.text
    before_url = trim_preview.json()["proposal"]["preview"]["before"]["video_url"]
    after_url = trim_preview.json()["proposal"]["preview"]["after"]["video_url"]
    assert before_url.endswith(f"#t={before_trim_start:.3f},{rerendered_plan.clips[0].source_end_seconds:.3f}")
    assert after_url.endswith(f"#t={before_trim_start + 0.5:.3f},{rerendered_plan.clips[0].source_end_seconds + 0.5:.3f}")
    assert before_url.split("#")[0] == after_url.split("#")[0]
    assert production.task_records.get_creation_task(workspace_id, task.id).current_timeline_version_id == before_trim_version
    exact_trim = client.post(f"/api/creation-tasks/{task.id}/edits/{trim_preview.json()['proposal']['id']}/confirm")
    assert exact_trim.status_code == 200, exact_trim.text
    assert exact_trim.json()["operation"]["operation_type"] == "TrimClip"
    trimmed_plan = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    assert trimmed_plan.clips[0].source_start_seconds == before_trim_start + 0.5
    assert trimmed_plan.clips[1].source_start_seconds == 1.0
    assert trimmed_plan.narration_audio_path == rendered_narration_reference
    assert trimmed_plan.output_path is None and trimmed_plan.subtitle_path is None
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 6

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
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 7

    reloaded = ProductionRepository(
        database_path=production.database_path,
        video_library_path=production.video_library_path,
    )
    assert reloaded.task_records.get_creation_task(
        workspace_id, task.id
    ).current_timeline_version_id == production.timeline_records.list_timeline_versions(
        workspace_id, task.id
    )[-1].id
    assert creation_task_montage_plan_from_payload(
        reloaded.get_creation_task_montage_plan(workspace_id, task.id)
    ).clips[0].source_start_seconds == before_trim_start
    assert client.get(f"/api/creation-tasks/{task.id}").json()["task"]["can_undo"] is True

    final_render = main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)
    assert final_render.status == "rendered"
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 8
    final_output = client.get(f"/api/creation-tasks/{task.id}/output")
    assert final_output.status_code == 200
    assert final_output.content == b"playable-video"
    current_task = production.task_records.get_creation_task(workspace_id, task.id)
    current_tracks = production.subtitle_records.list_subtitle_tracks(workspace_id, task.id, timeline_version_id=current_task.current_timeline_version_id)
    assert len(current_tracks) == 1 and current_tracks[0].language == language
    assert current_tracks[0].artifact_ref == production.get_creation_task_montage_plan(workspace_id, task.id)["subtitle_path"]
    assert current_tracks[0].transform_id is not None

    before_crop = client.get(f"/api/creation-tasks/{task.id}").json()
    crop_state=SimpleNamespace(workspace_id=workspace_id,production=production,members=members,
        member=member,client=client)
    crop_runtime=_granted_domain_preview_adapter(crop_state,monkeypatch)
    crop_payload = {"operation_type": "ChangeCrop", "clip_id": "clip-route", "fit_mode": "fit", "crop_x": 0, "crop_y": 1}
    crop_preview = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json=crop_payload)
    assert crop_preview.status_code == 200, crop_preview.text
    proposal = crop_preview.json()["proposal"]
    assert proposal["preview"]["before"]["detail"] != proposal["preview"]["after"]["detail"]
    assert client.get(f"/api/creation-tasks/{task.id}").json()["montage"]["clips"] == before_crop["montage"]["clips"]
    crop_confirm = client.post(f"/api/creation-tasks/{task.id}/edits/{proposal['id']}/confirm")
    assert crop_confirm.status_code == 200, crop_confirm.text
    assert crop_confirm.json()["operation"]["operation_type"] == "ChangeCrop"
    cropped = client.get(f"/api/creation-tasks/{task.id}").json()
    assert cropped["montage"]["clips"][0]["fit_mode"] == "fit"
    assert cropped["montage"]["clips"][0]["crop_x"] == 0
    assert cropped["montage"]["clips"][0]["crop_y"] == 1
    assert cropped["montage"]["clips"][1:] == before_crop["montage"]["clips"][1:]
    assert cropped["narration"] == before_crop["narration"]
    assert client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={}).status_code == 200
    restored = client.get(f"/api/creation-tasks/{task.id}").json()
    assert restored["montage"]["clips"] == before_crop["montage"]["clips"]
    assert restored["task"]["can_redo"] is True
    redo = client.post(f"/api/creation-tasks/{task.id}/timeline/redo", json={})
    assert redo.status_code == 200, redo.text
    assert redo.json()["operation"]["operation_type"] == "RedoTimelineVersion"
    redone = client.get(f"/api/creation-tasks/{task.id}").json()
    assert redone["montage"]["clips"] == cropped["montage"]["clips"]
    assert redone["task"]["can_redo"] is False
    assert redone["task"]["can_undo"] is True
    assert client.post(f"/api/creation-tasks/{task.id}/timeline/redo", json={}).status_code == 409
    assert client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={}).status_code == 200

    for change in ({"operation_type": "ReorderClip", "position": 1}, {"operation_type": "SetClipLock", "locked": True}):
        before_change = client.get(f"/api/creation-tasks/{task.id}").json()
        preview = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json={**change, "clip_id": "clip-route"})
        assert preview.status_code == 200, preview.text
        proposal = preview.json()["proposal"]
        assert proposal["preview"]["before"]["detail"] != proposal["preview"]["after"]["detail"]
        assert client.get(f"/api/creation-tasks/{task.id}").json()["montage"]["clips"] == before_change["montage"]["clips"]
        assert client.post(f"/api/creation-tasks/{task.id}/edits/{proposal['id']}/confirm").status_code == 200
        assert client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={}).status_code == 200
        assert client.get(f"/api/creation-tasks/{task.id}").json()["montage"]["clips"] == before_change["montage"]["clips"]

    assert client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json={**crop_payload, "crop_x": 2}).status_code == 422
    assert client.patch(f"/api/creation-tasks/{task.id}/montage/clips/clip-route", json={"fit_mode": "fit", "confirmed": True}).status_code == 409
    assert client.post(f"/api/creation-tasks/{task.id}/edits/exact", json={"operation_type": "SetClipLock", "clip_id": "clip-route", "locked": True}).status_code == 200
    assert client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json=crop_payload).status_code == 409
    locked_state = production.get_creation_task_montage_plan(workspace_id, task.id)
    for edit in (
        {"operation_type":"TrimClip", "clip_id":"clip-route", "source_start_seconds":0},
        {"operation_type":"ReorderClip", "clip_id":"clip-route", "position":1},
        {"operation_type":"ReorderClip", "clip_id":"clip-route-unchanged", "position":0},
        {"operation_type":"ReplaceClip", "clip_id":"clip-route", "asset_id":asset.id,
         "segment_id":asset.segments[0].id,"source_start_seconds":0},
    ):
        rejected = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json=edit)
        assert rejected.status_code == 409, rejected.text
        assert production.get_creation_task_montage_plan(workspace_id, task.id) == locked_state
    assert client.get(f"/api/creation-tasks/{task.id}").json()["task"]["can_redo"] is False
    assert client.post(f"/api/creation-tasks/{task.id}/timeline/redo", json={}).status_code == 409

    assert crop_runtime.stop(1)

@pytest.mark.parametrize("visual_only", [False, True])


def test_subtitle_exact_edit_uses_typed_operation_and_keeps_segmented_narration(
    tmp_path, monkeypatch, visual_only
):
    # This existing test uses a marker-byte renderer, not the quality/media path.
    monkeypatch.setattr(main_module, "_start_creation_task_quality_check", lambda *_: None)
    runtime_settings = main_module.settings
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    from app.local_members import LocalMemberService

    members = LocalMemberService(production.database_path)
    member, member_token = members.join(members.create_invite_code(), "字幕编辑测试成员")
    monkeypatch.setattr(main_module, "local_members_service", members)
    asset = _asset(tmp_path, workspace_id=workspace_id, owner_user_id=member.member_id)
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
    production.plan_confirmation.confirm_creation_plan(creation_plan, task)
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
    task = production.task_records.get_creation_task(workspace_id, task.id)
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
    production.narration_artifacts.save_narration_artifact(
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
    production.narration_completion.complete_creation_task_narration(
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
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id, task.id, montage.id, creation_task_montage_plan_to_payload(montage)
    )
    production.timeline_records.create_timeline_version(
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
    cover = production.cover_records.create_cover_asset(CoverAsset(
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

    base = production.timeline_records.get_timeline_version(workspace_id, "timeline-subtitle-edit-base")
    chinese = main_module._subtitle_track_from_montage(workspace_id, task, base, montage)
    production.subtitle_records.create_subtitle_track(chinese)
    production.subtitle_records.create_subtitle_track(replace(chinese, id="fixture-english", language="en-US", source_language="zh-CN", cues=[
        replace(chinese.cues[0], id="english-cue", source_cue_id=chinese.cues[0].id, text="MANUAL TEST", line_breaks=[])
    ]))
    display_client = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token})
    for mode, languages in [("en", {"en-US"}), ("zh", {"zh-CN"}), ("none", set())]:
        preview = display_client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json={
            "operation_type":"ConfigureSubtitleTrack", "subtitle_display":mode})
        assert preview.status_code == 200, preview.text
        result = display_client.post(f"/api/creation-tasks/{task.id}/edits/{preview.json()['proposal']['id']}/confirm")
        assert result.status_code == 200, result.text
        visible = display_client.get(f"/api/creation-tasks/{task.id}").json()
        assert {track["language"] for track in visible["subtitle_tracks"] if track["style"].get("enabled",True)} == languages
        assert display_client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={}).status_code == 200
    # Undo creates new versioned IDs; assertions below use the current tracks.
    current = display_client.get(f"/api/creation-tasks/{task.id}").json()
    current_zh = next(track["id"] for track in current["subtitle_tracks"] if track["language"]=="zh-CN")
    current_en = next(track["id"] for track in current["subtitle_tracks"] if track["language"]=="en-US")
    rejected_style = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}).post(
        f"/api/creation-tasks/{task.id}/edits/exact", json={
            "operation_type": "UpdateSubtitleCue", "track_id": current_en,
            "cue_id": "english-cue", "text": "MANUAL TEST", "style": {"name": "emphasis"},
        },
    )
    assert rejected_style.status_code == 422
    english_preview = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}).post(
        f"/api/creation-tasks/{task.id}/edits/preview", json={
            "context_stage":"subtitle_cover", "selected_object_refs":["cue:english-cue"],
            "raw_command":"把这句字幕改成 Manual edited English"})
    assert english_preview.status_code == 200, english_preview.text
    assert english_preview.json()["proposal"]["operation"]["payload"]["track_id"] == current_en
    response = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}).post(
        f"/api/creation-tasks/{task.id}/edits/exact",
        json={
            "operation_type": "UpdateSubtitleCue",
            "track_id": current_zh,
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
    edited_track = production.subtitle_records.list_subtitle_tracks(
        workspace_id,
        task.id,
        timeline_version_id=production.task_records.get_creation_task(
            workspace_id, task.id
        ).current_timeline_version_id,
    )
    assert len(edited_track) == (2 if visual_only else 1)
    edited_track = next(track for track in edited_track if track.language == "zh-CN")
    assert edited_track.cues[0].line_breaks == ([2] if visual_only else [6])
    assert saved.narration_audio_path == montage.narration_audio_path
    assert saved.output_path is None and saved.subtitle_path is None
    assert production.task_records.get_creation_task(workspace_id, task.id).narration_cache_key == "whole-cache"
    assert len(production.timeline_records.list_timeline_versions(workspace_id, task.id)) == 8
    assert any(operation.id == response.json()["operation"]["id"] and operation.operation_type == "UpdateSubtitleCue" for operation in production.edit_records.list_edit_operations(workspace_id, task.id))
    reloaded = ProductionRepository(production.database_path, production.video_library_path)
    assert reloaded.narration_records.list_narration_segments(workspace_id, task.id, script.id)[0].audio_reference == segment.audio_reference

    undo = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}).post(
        f"/api/creation-tasks/{task.id}/timeline/undo",
        json={},
    )
    assert undo.status_code == 200, undo.text
    assert undo.json()["operation"]["operation_type"] == "UndoTimelineVersion"
    restored = creation_task_montage_plan_from_payload(
        production.get_creation_task_montage_plan(workspace_id, task.id)
    )
    # No registered video, successful producer or sealed input exists in this
    # fixture. Undo restores the draft and tracks, not a fabricated output.
    assert restored.status == "ready"
    assert restored.output_path is None
    assert restored.subtitle_path is None
    restored_workspace = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token}).get(
        f"/api/creation-tasks/{task.id}"
    ).json()
    assert restored_workspace["task"]["stage"] == "montage"
    assert restored_workspace["task"]["output_ready"] is False
    assert restored_workspace["task"]["can_undo"] is False
    assert {track["language"] for track in restored_workspace["subtitle_tracks"]} == {"zh-CN", "en-US"}
    assert next(track for track in restored_workspace["subtitle_tracks"] if track["language"] == "zh-CN")["cues"][0]["line_breaks"] == [
        4
    ]
    assert restored_workspace["cover_asset"]["artifact_ref"] == cover.artifact_ref

    render_calls = []
    def render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path, subtitle_tracks=None):
        render_calls.append(rendered_plan)
        (job_staging_path / "preview.mp4").write_bytes(b"render-fixture")
        (job_staging_path / "subtitles.srt").write_text(_plan_to_srt(rendered_plan))
        rendered_plan.status = "rendered"
        return rendered_plan

    monkeypatch.setattr(main_module, "video_job_runner", VideoJobRunner(production.job_records))
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "output"))
    monkeypatch.setattr(main_module, "settings", SimpleNamespace(video_output_path=str(tmp_path / "output"), native_portrait_worker_path=None, effective_temporary_root_path=tmp_path / "temp"))
    monkeypatch.setattr(main_module, "_render_montage_with_resolved_clip_sources", render)
    client = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token})
    _assert_retired_montage_endpoint(client, production, workspace_id, task.id, "render")
    # Diagnostic helper, then cache hit: both must carry the same immutable cover selection.
    for _ in range(2):
        result = main_module._render_creation_task_montage_for_workspace(workspace_id, task.id)
        assert result.status == "rendered"
        current = client.get(f"/api/creation-tasks/{task.id}").json()
        assert current["cover_asset"]["title"] == cover.title
        assert current["cover_asset"]["artifact_ref"] == cover.artifact_ref
        assert current["cover_asset"]["source_video_ref"] == cover.source_video_ref
        assert current["cover_asset"]["timeline_version_id"] == current["task"]["current_timeline_version_id"]
    assert len(render_calls) == 1

    # A rendering-contract change must not reuse an output from the previous engine.
    monkeypatch.setattr(main_module, "CREATION_TASK_MONTAGE_RENDER_CONTRACT_VERSION", "next-render-contract")
    assert main_module._render_creation_task_montage_for_workspace(workspace_id, task.id).status == "rendered"
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

    selected=after["subtitle_tracks"][0]
    selected_cue=selected["cues"][0]
    timing_preview=client.post(f"/api/creation-tasks/{task.id}/edits/preview", json={
        "context_stage":"subtitle_cover", "selected_object_refs":[f"cue:{selected_cue['id']}"],
        "raw_command":"这条字幕从0.4秒到3.6秒"})
    assert timing_preview.status_code==200, timing_preview.text
    assert client.get(f"/api/creation-tasks/{task.id}").json()["subtitle_tracks"]==after["subtitle_tracks"]
    timing_result=client.post(f"/api/creation-tasks/{task.id}/edits/{timing_preview.json()['proposal']['id']}/confirm")
    assert timing_result.status_code==200, timing_result.text
    timed=client.get(f"/api/creation-tasks/{task.id}").json()
    assert (timed["subtitle_tracks"][0]["cues"][0]["start_seconds"],timed["subtitle_tracks"][0]["cues"][0]["end_seconds"])==(.4,3.6)
    assert timed["montage"]["clips"]==after["montage"]["clips"]
    invalid=client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview",json={
        "operation_type":"UpdateSubtitleCue","cue_id":selected_cue["id"],"end_seconds":5})
    assert invalid.status_code==422
    undone=client.post(f"/api/creation-tasks/{task.id}/timeline/undo",json={})
    assert undone.status_code==200, undone.text
    restored=client.get(f"/api/creation-tasks/{task.id}").json()["subtitle_tracks"][0]["cues"][0]
    assert (restored["start_seconds"],restored["end_seconds"])==(selected_cue["start_seconds"],selected_cue["end_seconds"])
    before_lock=client.get(f"/api/creation-tasks/{task.id}").json()
    clip_id=before_lock["montage"]["clips"][0]["id"]
    result=client.post(f"/api/creation-tasks/{task.id}/edits/exact",json={"operation_type":"SetClipLock","clip_id":clip_id,"locked":True})
    assert result.status_code==200, result.text
    for endpoint in (None,"/timeline/undo","/timeline/redo"):
        if endpoint:
            result=client.post(f"/api/creation-tasks/{task.id}"+endpoint,json={})
            assert result.status_code==200, result.text
        state=client.get(f"/api/creation-tasks/{task.id}").json()
        assert [track["cues"] for track in state["subtitle_tracks"]]==[track["cues"] for track in before_lock["subtitle_tracks"]]
        assert all(track["timeline_version_id"]==state["task"]["current_timeline_version_id"] for track in state["subtitle_tracks"])
    visibility_before = client.get(f"/api/creation-tasks/{task.id}").json()
    hidden_preview = client.post(f"/api/creation-tasks/{task.id}/edits/preview", json={
        "context_stage":"subtitle_cover", "raw_command":"关闭字幕", "selected_object_refs":[]})
    assert hidden_preview.status_code == 200, hidden_preview.text
    assert client.get(f"/api/creation-tasks/{task.id}").json()["subtitle_tracks"] == visibility_before["subtitle_tracks"]
    result = client.post(f"/api/creation-tasks/{task.id}/edits/{hidden_preview.json()['proposal']['id']}/confirm")
    assert result.status_code == 200, result.text
    hidden = client.get(f"/api/creation-tasks/{task.id}").json()
    assert all(track["style"]["enabled"] is False for track in hidden["subtitle_tracks"])
    assert [track["cues"] for track in hidden["subtitle_tracks"]] == [track["cues"] for track in visibility_before["subtitle_tracks"]]
    assert client.post(f"/api/creation-tasks/{task.id}/timeline/undo",json={}).status_code == 200
    restored = client.get(f"/api/creation-tasks/{task.id}").json()
    assert all(track["style"].get("enabled",True) for track in restored["subtitle_tracks"])
    absent_english = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview",json={"operation_type":"ConfigureSubtitleTrack","subtitle_display":"en"})
    assert absent_english.status_code == 409


def test_regenerate_person_render_replaces_only_target_and_undo_restores_old_render(
    permitted_person_edit_state,
    monkeypatch,
):
    state = permitted_person_edit_state
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
    assert len(production.timeline_records.list_timeline_versions(state.workspace_id, state.task.id)) == 1

    # An uncalibrated dedicated budget still rejects before queue or generation writes.
    _assert_unpermitted_person_confirm(state, preview_payload['proposal'])
    # Internal generation consumes an actual original-owner Grant. It is not public edit completion.
    with state.granted() as context:
        generated = main_module._generate_creation_task_person_render_record(
            state.workspace_id, state.task.id, main_module.CreationTaskPersonRenderRequest(
                narration_segment_id=state.montage.cues[0].narration_segment_id,
                person_profile_id=state.old_render.person_profile_id, person_version=state.old_render.person_version),
            regeneration_token=preview_payload['proposal']['id'], grant_context=context)
    new_render_id = generated.id
    assert new_render_id != state.old_render.id
    # The existing same-audio reuse path retains target-only replacement and undo coverage.
    reuse = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/preview", json={
        "raw_command": "这一段换成当前人物出镜", "context_stage": "review",
        "selected_object_refs": ["clip:clip-person-target"], "playhead_ms": 500})
    assert reuse.status_code == 200, reuse.text
    assert reuse.json()['proposal']['operation']['payload']['reuse_person_render_id'] == new_render_id
    confirm = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/{reuse.json()['proposal']['id']}/confirm")
    assert confirm.status_code == 200, confirm.text
    operation = confirm.json()['operation']
    assert operation['operation_type'] == 'ChangePersonSegment'
    assert operation['payload']['person_render_id'] == new_render_id
    renders = production.person_renders.list_person_renders(state.workspace_id, state.task.id)
    assert {render.id for render in renders} == {state.old_render.id, new_render_id}
    new_render = next(render for render in renders if render.id == new_render_id)
    expected_cache_key = hashlib.sha256(
        (
            f"{state.provider.base_cache_keys[0]}\n"
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
    versions = production.timeline_records.list_timeline_versions(
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
    assert {render.id for render in production.person_renders.list_person_renders(
        state.workspace_id, state.task.id
    )} == {state.old_render.id, new_render_id}

    # Retain the existing fake-renderer diagnostic; it proves cache/edit lineage only,
    # never a permitted device render, media output, or public re-output capability.
    def fake_render(_workspace_id, rendered_plan, _audio_path, *, job_staging_path, subtitle_tracks=None):
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
    _assert_retired_montage_endpoint(state.client, production, state.workspace_id, state.task.id, "render")
    # Diagnostic fixture rendering does not prove a permitted person/edit re-output path.
    rerender = main_module._render_creation_task_montage_for_workspace(state.workspace_id, state.task.id)
    assert rerender.status == "rendered"
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
    assert {render.id for render in production.person_renders.list_person_renders(
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
        "model_id": "",
        "message": "0%人物，本任务不需要口型能力。",
        "repair_action": None,
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
    assert state.production.edit_records.list_edit_intents(
        state.workspace_id, state.task.id
    ) == []
    assert state.production.edit_records.list_edit_proposals(
        state.workspace_id, state.task.id
    ) == []
    assert state.production.timeline_records.list_timeline_versions(
        state.workspace_id, state.task.id
    ) == []


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("color_mode", ["legacy", "mixed", "uniform"])
def test_template_settings_reach_initial_montage_and_subtitle_projection(person_render_edit_state, language, color_mode):
    state = person_render_edit_state
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    task.stage = "montage"
    task.stage_status = "not_started"
    task.current_timeline_version_id = None
    plan = state.production.plan_records.get_creation_plan(state.workspace_id, task.plan_id)
    plan.language = language
    plan.template_settings = {"fit_mode": "fit", "subtitle_style": {"position": "top", "font_size": 48}}
    if color_mode != "legacy":
        plan.template_settings["color_style"] = ({"exposure": 0.5, "contrast": 1.1, "saturation": 0.8, "temperature": 0.2}
                                                  if color_mode == "uniform" else None)
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    _assert_retired_montage_endpoint(state.client, state.production, state.workspace_id, task.id, "plan")
    # Existing planning diagnostic with fixture providers, not the public/device path.
    response = main_module._create_creation_task_montage_plan_for_workspace(state.workspace_id, task.id)
    assert response.task_id == task.id
    versioned_task = state.production.task_records.get_creation_task(state.workspace_id, task.id)
    assert versioned_task.current_timeline_version_id is not None
    assert len(state.production.timeline_records.list_timeline_versions(state.workspace_id, task.id)) == 1
    montage = creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id, task.id))
    assert montage.clips and all(clip.fit_mode == "fit" for clip in montage.clips)
    assert all(clip.visual_effects == (plan.template_settings.get("color_style") or {}) for clip in montage.clips)
    assert all(cue.subtitle_style == plan.template_settings["subtitle_style"] for cue in montage.cues)
    montage.status = "rendered"
    montage.subtitle_path = "test/subtitle.srt"
    version = SimpleNamespace(id="test-version", snapshot={"transform_run_id": "test-transform"})
    track = main_module._subtitle_track_from_montage(state.workspace_id, task, version, montage)
    assert track.language == language
    assert track.style == plan.template_settings["subtitle_style"]


def test_first_subtitle_projection_spans_two_shots_but_keeps_explicit_short_track(monkeypatch):
    from app.subtitle_schedule import narration_schedule, schedule_srt
    task = SimpleNamespace(id="task", plan_id="plan", current_timeline_version_id="version")
    version = SimpleNamespace(id="version", snapshot={"transform_run_id":"transform"})
    montage = CreationTaskMontagePlan(
        id="montage", workspace_id="workspace", task_id=task.id, script_id="script",
        script_title="初次字幕", status="rendered",
        cues=[NarrationCue("cue", "section", "旁白跨越两个镜头。", 5)],
        clips=[MontageClip(f"clip-{index}", "cue", "asset", "segment", "workspace://video.mp4",
            0, 2.5, index * 2.5, (index + 1) * 2.5, 1, [], False) for index in range(2)],
        duration_seconds=5, low_confidence_clip_count=0, subtitle_path="artifacts/subtitles.srt")
    persisted = []
    monkeypatch.setattr(main_module, "production_repo", SimpleNamespace(
        plan_records=SimpleNamespace(get_creation_plan=lambda *_: SimpleNamespace(language="zh-CN")),
        subtitle_records=SimpleNamespace(list_subtitle_tracks=lambda *_, **__: persisted)))
    projected = main_module._current_subtitle_tracks("workspace", task,
        current_version=version, montage=montage)
    assert [(cue.id, cue.start_seconds, cue.end_seconds) for cue in projected[0].cues] == [("cue", 0., 5.)]
    assert schedule_srt(subtitle_schedule(projected, 5)) == schedule_srt(narration_schedule(montage.cues, 5))
    explicit = replace(projected[0], cues=[replace(projected[0].cues[0], end_seconds=2.5)])
    persisted.append(explicit)
    current = main_module._current_subtitle_tracks("workspace", task, current_version=version, montage=montage)
    assert current == [explicit]
    assert current[0].cues[0].end_seconds == 2.5


@pytest.mark.parametrize("target,expected_calls", [(0, 0), (100, 1)])
def test_initial_montage_generates_face_only_for_selected_share(permitted_person_edit_state, monkeypatch, target, expected_calls):
    state = permitted_person_edit_state
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    task.stage = "montage"
    task.stage_status = "not_started"
    task.current_timeline_version_id = None
    plan = state.production.plan_records.get_creation_plan(state.workspace_id, task.plan_id)
    plan.presenter_share_target = target
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id=?", (json.dumps(asdict(task), default=str), task.id))
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    _assert_retired_montage_endpoint(state.client, state.production, state.workspace_id, task.id, "plan")
    if target:
        with state.granted() as context:
            main_module._person_stage_with_grant(state.workspace_id, task.id, context)
        with state.granted("montage_plan") as context:
            response = main_module._create_creation_task_montage_plan_for_workspace(state.workspace_id, task.id, grant_context=context)
    else:
        # Pure metadata diagnostic remains available; no person provider is involved.
        response = main_module._create_creation_task_montage_plan_for_workspace(state.workspace_id, task.id)
    assert response.task_id == task.id
    body = response.model_dump()
    assert len(state.provider.output_cache_keys) == expected_calls
    assert body["presenter_share_target"] == target
    assert body["presenter_share_actual"] == target
    assert all(bool(clip["person_render_id"]) == bool(target) for clip in body["clips"])


def test_confirm_pending_clip_preview_preserves_state_and_can_undo(tmp_path, monkeypatch):
    workspace_id = "workspace_a"
    production = ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=tmp_path,
    )
    from app.local_members import LocalMemberService

    members = LocalMemberService(production.database_path)
    member, member_token = members.join(members.create_invite_code(), "待确认镜头测试成员")
    monkeypatch.setattr(main_module, "local_members_service", members)
    asset = _asset(tmp_path, workspace_id=workspace_id, owner_user_id=member.member_id)
    production._save_asset(asset)
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
    production.plan_confirmation.confirm_creation_plan(creation_plan, task)
    plan = CreationTaskMontagePlan(
        id="montage-render-route",
        workspace_id=workspace_id,
        task_id=task.id,
        script_id="script-render-route",
        script_title="渲染迁移",
        status="waiting_for_user",
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
                needs_confirmation=True,
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
        low_confidence_clip_count=1,
        narration_audio_path="narration://artifact/route",
    )
    production.montage_plan_adoption.save_creation_task_montage_plan(
        workspace_id,
        task.id,
        plan.id,
        creation_task_montage_plan_to_payload(plan),
    )
    monkeypatch.setattr(main_module, "production_repo", production)
    monkeypatch.setattr(main_module, "artifact_store", ArtifactStore(tmp_path / "output"))
    monkeypatch.setattr(main_module, "current_workspace_id", lambda _request: workspace_id)
    client = TestClient(main_module.app, headers={"X-Local-Member-Token": member_token})
    changed, _ = main_module._prepare_changed_montage(plan, message="字幕已修改。")
    assert changed.status == "waiting_for_user"
    assert changed.low_confidence_clip_count == 1
    # Old persisted versions may incorrectly say rendered; per-clip review remains authoritative.
    inconsistent = creation_task_montage_plan_to_payload(plan)
    inconsistent["status"] = "rendered"
    production.montage_plan_adoption.save_creation_task_montage_plan(workspace_id, task.id, plan.id, inconsistent)
    assert client.post(f"/api/creation-tasks/{task.id}/montage/render", json={}).status_code == 409
    request = {"operation_type": "ConfirmClip", "clip_id": "clip-route"}
    preview = client.post(f"/api/creation-tasks/{task.id}/edits/exact/preview", json=request)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert proposal["preview"]["before"]["detail"] == "待确认"
    assert proposal["preview"]["after"]["detail"] == "已确认"
    persisted = production.get_creation_task_montage_plan(workspace_id, task.id)
    assert persisted["clips"][0]["needs_confirmation"] is True
    assert production.edit_records.list_edit_operations(workspace_id, task.id) == []
    confirmed = client.post(f"/api/creation-tasks/{task.id}/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    persisted = production.get_creation_task_montage_plan(workspace_id, task.id)
    assert persisted["clips"][0]["needs_confirmation"] is False
    assert persisted["clips"][1] == creation_task_montage_plan_to_payload(plan)["clips"][1]
    confirmed_task = production.task_records.get_creation_task(workspace_id, task.id)
    confirmed_version = production.timeline_records.get_timeline_version(workspace_id, confirmed_task.current_timeline_version_id)
    assert "subtitle_track_ids" not in confirmed_version.snapshot
    undo = client.post(f"/api/creation-tasks/{task.id}/timeline/undo", json={})
    assert undo.status_code == 200, undo.text
    assert production.get_creation_task_montage_plan(workspace_id, task.id)["clips"][0]["needs_confirmation"] is True


@pytest.mark.parametrize("command,expected", [
    ("把这句字幕改成：Keep your files on this computer.", "Keep your files on this computer."),
    ('Replace with "Ready?"', "Ready?"),
    ("把字幕改成：‘Hello!’", "Hello!"),
    ("把字幕改成：保留标点。", "保留标点。"),
    ("把字幕改成：It's ready.", "It's ready."),
])
def test_subtitle_command_preserves_authored_punctuation(command, expected):
    assert main_module._subtitle_text_from_command(command) == expected


@pytest.mark.parametrize("visual_prompt,content", [
    ("USB port closeup", "Save your time."),
    ("接口特写", "提高工作效率。"),
])
@pytest.mark.parametrize("action_times", [[0.2, 0.7], [3.0, 9.0]])
def test_creation_task_retrieval_uses_visual_requirement_without_changing_narration(tmp_path, visual_prompt, content, action_times):
    from app.visual_understanding import SegmentUnderstanding
    script = CreationTaskScript(id="script", workspace_id="workspace_a", task_id="task",
        run_id="run", topic_id="topic", title="Visual requirement", language="en-US",
        selection_status="confirmed", sections=[CreationTaskScriptSection(
            id="section", title="Intro", content=content, visual_prompt=visual_prompt,
            evidence_reference_ids=[])])
    asset = _asset(tmp_path)
    relevant = replace(asset.segments[0], id="relevant", description=visual_prompt,
        end_seconds=20, transcript="", understanding=SegmentUnderstanding(status="ready",
            facts=[{"kind":"action", "text":"手指移动", "sample_times":action_times}],
            unknowns=["软件内容不可见"]))
    spoken_only = replace(asset.segments[0], id="spoken-only", description="A sunny garden",
        end_seconds=4, transcript=visual_prompt + " " + content)
    observed_queries = []
    provider = SimpleNamespace(ready=True, model_id="test-local",
        embed_texts=lambda texts: observed_queries.extend(texts) or [[1.0, 0.0] for _ in texts])
    planner = MontagePlanner(semantic_provider=provider,
        visual_feature_loader=lambda _workspace, segment_id, _model: [1.0, 0.0] if segment_id == "relevant" else [0.0, 1.0])
    plan = planner.build_for_creation_task(duration_target_seconds=90, workspace_id="workspace_a", task_id="task", script=script,
        profile=ProductionProfile(workspace_id="workspace_a"), assets=[replace(asset, segments=[spoken_only, relevant])],
        media_reference_for_asset=lambda _: "workspace://source.mp4",
        narration_audio_path="narration://artifact/test", narration_duration_seconds=4)
    assert observed_queries == [visual_prompt]
    assert plan.clips[0].segment_id == relevant.id
    assert plan.cues[0].text == content
    assert script.sections[0].content == content
    assert "镜头时长适配 100%" in plan.clips[0].match_reasons
    assert plan.clips[0].source_end_seconds == 4
    reasons = plan.clips[0].match_reasons
    assert any("局部动作观察" in reason for reason in reasons) == (action_times == [0.2, 0.7])
    assert "画面未确认：软件内容不可见" in reasons
    if action_times == [3.0, 9.0]:
        assert "选取区间缺少多帧动作证据；物体相似不代表动作符合" in reasons


def test_creation_task_audio_claim_cannot_clear_missing_visual_evidence(tmp_path):
    asset = _asset(tmp_path)
    segment = replace(asset.segments[0], description="A sunny garden", transcript="USB port closeup", end_seconds=20)
    cue = NarrationCue(id="cue", source_section_id="section", text="USB port closeup", duration_seconds=4)
    result = _score_candidate(cue, asset, segment, 0, 0, visual_only=True)
    assert result[1] == 0
    assert "文字词重合 0%" in result[-1]
    # Extra source duration does not reduce the ability to supply the requested shot.
    exact = _score_candidate(cue, asset, replace(segment, end_seconds=4), 0, 0, visual_only=True)
    assert result[0] == exact[0]


@pytest.mark.parametrize("segmented", [False, True])
def test_short_creation_subtitles_do_not_extend_real_narration(tmp_path, segmented):
    from app.montage import _build_creation_task_cues
    script = CreationTaskScript(id="s", workspace_id="w", task_id="t", run_id="r", topic_id="p",
        title="Short cues", language="zh-CN", selection_status="confirmed", sections=[
            CreationTaskScriptSection(id="a", title="A", content="好。开始。完成。", visual_prompt="", evidence_reference_ids=[]),
            CreationTaskScriptSection(id="b", title="B", content="再见。", visual_prompt="", evidence_reference_ids=[])])
    cues = _build_creation_task_cues(script, min_seconds=30, max_seconds=90,
        narration_duration_seconds=1.321,
        narration_segment_durations_by_section={"a":0.901,"b":0.420} if segmented else None)
    assert sum(c.duration_seconds for c in cues) == pytest.approx(1.321, abs=0.000001)
    assert all(c.duration_seconds > 0 for c in cues)
    assert "".join(c.text for c in cues) == "好。开始。完成。再见。"
    if segmented:
        assert sum(c.duration_seconds for c in cues if c.source_section_id == "a") == pytest.approx(0.901)


@pytest.mark.parametrize("durations", [{}, {"a":float("nan")}, {"a":3.0}])
def test_creation_subtitles_reject_inconsistent_segment_clock(durations):
    from app.montage import _build_creation_task_cues
    script = CreationTaskScript(id="s", workspace_id="w", task_id="t", run_id="r", topic_id="p",
        title="Invalid clock", language="zh-CN", selection_status="confirmed", sections=[
            CreationTaskScriptSection(id="a", title="A", content="开始。", visual_prompt="", evidence_reference_ids=[])])
    with pytest.raises(ValueError, match="配音时长"):
        _build_creation_task_cues(script, min_seconds=30, max_seconds=90,
            narration_duration_seconds=1.0, narration_segment_durations_by_section=durations)


@pytest.mark.parametrize("natural", [False, True])
def test_split_clip_preserves_clock_narration_and_roundtrips_undo(person_render_edit_state, natural):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    target = before["montage"]["clips"][0]
    split_at = target["timeline_start_seconds"] + 1.25
    if natural:
        response = state.client.post(base + "/edits/preview", json={
            "raw_command":"从这里分割镜头", "context_stage":"montage",
            "selected_object_refs":[f"clip:{target['id']}"], "playhead_ms":round(split_at * 1000)})
    else:
        response = state.client.post(base + "/edits/exact/preview", json={
            "operation_type":"SplitClip", "clip_id":target["id"], "split_time_seconds":split_at})
    assert response.status_code == 200, response.text
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    proposal = response.json()["proposal"]
    confirmed = state.client.post(base + f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    clips = after["montage"]["clips"]
    assert len(clips) == len(before["montage"]["clips"]) + 1
    assert clips[0]["timeline_end_seconds"] == clips[1]["timeline_start_seconds"] == split_at
    assert clips[0]["source_end_seconds"] == clips[1]["source_start_seconds"] == target["source_start_seconds"] + 1.25
    assert clips[1]["source_end_seconds"] == target["source_end_seconds"]
    assert clips[1]["person_render_id"] == target["person_render_id"]
    assert clips[2:] == before["montage"]["clips"][1:]
    assert after["montage"]["duration_seconds"] == before["montage"]["duration_seconds"]
    assert after["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]
    assert after["montage"]["cues"] == before["montage"]["cues"]
    assert state.client.post(base + f"/edits/{proposal['id']}/confirm").status_code == 409
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == clips


def test_split_clip_rejects_endpoints_and_locked_source(person_render_edit_state):
    state=person_render_edit_state
    base=f"/api/creation-tasks/{state.task.id}"
    clip=state.montage.clips[0]
    mixed=state.client.patch(base+f"/montage/clips/{clip.id}",json={"split_time_seconds":1,"confirmed":True})
    assert mixed.status_code == 409
    for at in (clip.timeline_start_seconds, clip.timeline_end_seconds, clip.timeline_start_seconds + .01):
        r=state.client.post(base+"/edits/exact/preview", json={"operation_type":"SplitClip", "clip_id":clip.id, "split_time_seconds":at})
        assert r.status_code == 422, r.text
    r=state.client.post(base+"/edits/exact", json={"operation_type":"SetClipLock", "clip_id":clip.id, "locked":True})
    assert r.status_code == 200, r.text
    r=state.client.post(base+"/edits/exact/preview", json={"operation_type":"SplitClip", "clip_id":clip.id, "split_time_seconds":1})
    assert r.status_code == 409, r.text


def test_split_person_render_keeps_source_offset_in_render_helper(person_render_edit_state, monkeypatch, tmp_path):
    state=person_render_edit_state
    montage=creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(state.montage))
    first=montage.clips[0]
    second=replace(first,id="split-second",source_start_seconds=1.25,timeline_start_seconds=1.25)
    first.source_end_seconds=1.25
    first.timeline_end_seconds=1.25
    montage.clips=[first,second]
    observed=[]
    def render(plan,*args,**kwargs):
        observed.extend((c.source_start_seconds,c.source_end_seconds) for c in plan.clips)
        return plan
    monkeypatch.setattr(main_module,"montage_renderer",SimpleNamespace(render=render))
    main_module._render_montage_with_resolved_clip_sources(state.workspace_id,montage,tmp_path/"voice.wav")
    assert observed == [(0,1.25),(1.25,4)]


def test_regenerated_narration_is_not_duplicated_by_split_clips(person_render_edit_state):
    state=person_render_edit_state
    plan=creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(state.montage))
    # Two adjacent portions of one cue must share its replacement duration.
    plan.clips=plan.clips[:1]
    first=plan.clips[0]
    first.person_render_id=None
    first.source_path=person_render_edit_state.production.workspace_media_reference_for_asset(
        state.workspace_id, first.asset_id)
    second=replace(first,id="split-second",source_start_seconds=1,timeline_start_seconds=1)
    first.source_end_seconds=1
    first.timeline_end_seconds=1
    plan.clips.append(second)
    plan.duration_seconds=4
    segment=SimpleNamespace(id=plan.cues[0].narration_segment_id,duration_seconds=6,status="ready")
    result=main_module._retime_montage_for_narration_segments(state.workspace_id,plan,[segment],"narration://artifact/new")
    assert result.duration_seconds == 6
    assert [(c.timeline_start_seconds,c.timeline_end_seconds) for c in result.clips] == [(0,1.5),(1.5,6)]
    assert [(c.source_start_seconds,c.source_end_seconds) for c in result.clips] == [(0,1.5),(1.5,6)]
    assert plan.duration_seconds == 4
    plan.clips[0].locked=True
    with pytest.raises(ValueError,match="锁定"):
        main_module._retime_montage_for_narration_segments(state.workspace_id,plan,[segment],"narration://artifact/new")


def test_regenerated_narration_cannot_exceed_existing_person_render(person_render_edit_state):
    state = person_render_edit_state
    plan = creation_task_montage_plan_from_payload(creation_task_montage_plan_to_payload(state.montage))
    plan.clips = plan.clips[:1]
    plan.duration_seconds = 4
    segment = SimpleNamespace(id=plan.cues[0].narration_segment_id, duration_seconds=6, status="ready")
    with pytest.raises(ValueError, match="超过当前镜头可用范围"):
        main_module._retime_montage_for_narration_segments(state.workspace_id, plan, [segment], "narration://artifact/new")


def test_person_clip_crop_and_replacement_keep_editable_timeline(permitted_media_state):
    state = permitted_media_state
    base = f"/api/creation-tasks/{state.task.id}"
    face = state.montage.clips[0]
    crop = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ChangeCrop", "clip_id": face.id,
        "fit_mode": "fit", "crop_x": .5, "crop_y": .5,
    })
    assert crop.status_code == 200, crop.text
    assert crop.json()["proposal"]["preview"]["before"]["label"] == "人物口播"
    confirmed = state.client.post(base + f"/edits/{crop.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert state.client.get(base).json()["montage"]["clips"][0]["person_render_id"] == face.person_render_id
    replacement = state.client.post(base + "/edits/exact/preview", json={
        "operation_type": "ReplaceClip", "clip_id": face.id,
        "asset_id": state.montage.clips[1].asset_id,
        "segment_id": state.montage.clips[1].segment_id,
    })
    assert replacement.status_code == 200, replacement.text
    confirmed = state.client.post(base + f"/edits/{replacement.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert state.client.get(base).json()["montage"]["clips"][0]["person_render_id"] is None
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"][0]["person_render_id"] == face.person_render_id



def test_natural_language_presenter_share_previews_confirms_and_undoes(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()["montage"]
    prior_renders = list(state.provider.output_cache_keys)
    preview = state.client.post(base + "/edits/preview", json={
        "raw_command": "露脸占比调到0%", "context_stage": "montage",
        "selected_object_refs": [],
    })
    assert preview.status_code == 200, preview.text
    assert "目标 0%" in preview.json()["proposal"]["preview"]["after"]["label"]
    assert state.client.get(base).json()["montage"] == before
    confirmed = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()["montage"]
    assert after["presenter_share_target"] == 0
    assert after["clips"] == before["clips"]
    assert after["presenter_share_actual"] == before["presenter_share_actual"]
    assert after["cues"] == before["cues"]
    assert after["audio_timeline"] == before["audio_timeline"]
    assert state.provider.output_cache_keys == prior_renders
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == before["clips"]


def test_diagnostic_narration_retime_snapshot_marks_subtitles_unavailable_and_roundtrips_history(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    segment_id = state.montage.cues[0].narration_segment_id
    preview = state.client.post(base + "/edits/preview", json={
        "raw_command": "这句重新配音", "context_stage": "narration",
        "selected_object_refs": [f"narration:{segment_id}"],
    })
    assert preview.status_code == 200, preview.text
    base_version_id = preview.json()["proposal"]["operation"]["payload"]["base_version_id"]
    state.production.subtitle_records.create_subtitle_track(SubtitleTrack(
        id="retime-source", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=base_version_id,
        language="zh-CN", source_language=None, style={"font_size": 40},
        cues=[ProductionSubtitleCue(id="caption", source_cue_id=None, text="手动改过的字幕", start_seconds=0, end_seconds=4)],
        generation_method="timeline", model_id=None, artifact_ref="artifacts/original.srt",
        transform_id="original-transform", status="valid"))
    before = state.client.get(base).json()["subtitle_tracks"]
    changed = main_module._retime_montage_for_narration_segments(state.workspace_id, state.montage,
        [SimpleNamespace(id=segment_id, duration_seconds=6, status="ready")], "narration://artifact/diagnostic-new")
    # Direct diagnostic snapshot only: synthetic duration facts exercise persisted history, not licensed generation.
    regenerated = [replace(segment, duration_seconds=6, updated_at=main_module.utc_now())
        for segment in state.production.narration_records.list_narration_segments(
            state.workspace_id, state.task.id, state.task.confirmed_script_id)]
    proposal = state.production.edit_records.get_edit_proposal(state.workspace_id, preview.json()["proposal"]["id"])
    current_task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    base_version = state.production.timeline_records.get_timeline_version(state.workspace_id, base_version_id)
    main_module._persist_typed_montage_edit(
        workspace_id=state.workspace_id, task=current_task, proposal=proposal,
        base_version=base_version, updated_plan=changed,
        operation_type="RegenerateNarrationSegment", operation_payload=proposal.operation["payload"],
        narration_segments=regenerated)
    after = state.client.get(base).json()["subtitle_tracks"]
    assert after[0]["cues"][0]["end_seconds"] == 6
    assert after[0]["cues"][0]["text"] == before[0]["cues"][0]["text"]
    assert after[0]["status"] == "unavailable"
    assert after[0]["artifact_ref"] is None and after[0]["transform_id"] is None
    assert after[0]["failure_reason"] and after[0]["impact"] and after[0]["repair_action"]
    assert state.production.narration_records.get_narration_segment(state.workspace_id, state.task.id, segment_id).duration_seconds == 6
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    restored = state.client.get(base).json()["subtitle_tracks"]
    assert restored[0]["cues"][0]["end_seconds"] == 4
    assert restored[0]["cues"][0]["text"] == before[0]["cues"][0]["text"]
    assert restored[0]["status"] == "valid"
    assert restored[0]["artifact_ref"] == "artifacts/original.srt"
    assert restored[0]["transform_id"] == "original-transform"
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    redone = state.client.get(base).json()["subtitle_tracks"]
    assert redone[0]["cues"] == after[0]["cues"]
    assert redone[0]["status"] == "unavailable"
    assert redone[0]["artifact_ref"] is None and redone[0]["transform_id"] is None
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.production.narration_records.get_narration_segment(state.workspace_id, state.task.id, segment_id).duration_seconds == 4

@pytest.mark.parametrize("action,expected", [("DeleteClip", 4), ("DuplicateClip", 12)])
def test_linked_clip_edits_preview_audio_and_undo(person_render_edit_state, action, expected):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    preview = state.client.post(base + "/edits/exact/preview", json={"operation_type": action, "clip_id": state.montage.clips[0].id})
    assert preview.status_code == 200, preview.text
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    confirmed = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    edited = state.client.get(base).json()
    assert edited["montage"]["duration_seconds"] == expected
    clips = edited["montage"]["audio_timeline"]["tracks"][0]["clips"]
    assert sum(c["source_end_ms"] - c["source_start_ms"] for c in clips) == expected * 1000
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["audio_timeline"] == edited["montage"]["audio_timeline"]


def test_linked_delete_removes_last_caption_without_reviving_projection(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    state.production.subtitle_records.create_subtitle_track(SubtitleTrack(id="only-caption", workspace_id=state.workspace_id,
        task_id=state.task.id, timeline_version_id=version.id, language="zh-CN", source_language=None,
        style={}, cues=[ProductionSubtitleCue(id="caption", source_cue_id=None, text="被删除的字幕", start_seconds=0, end_seconds=4)],
        generation_method="timeline", model_id=None, artifact_ref="artifacts/old.srt", transform_id="old", status="valid"))
    preview = state.client.post(base + "/edits/exact/preview", json={"operation_type":"DeleteClip", "clip_id":state.montage.clips[0].id})
    assert preview.status_code == 200, preview.text
    result = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert result.status_code == 200, result.text
    assert state.client.get(base).json()["subtitle_tracks"] == []
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["subtitle_tracks"][0]["cues"][0]["text"] == "被删除的字幕"


def test_linked_delete_rejects_shifting_locked_later_shot(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    assert state.client.post(base + "/timeline/initialize").status_code == 200
    result = state.client.post(base + "/edits/exact", json={"operation_type":"SetClipLock", "clip_id":state.montage.clips[1].id, "locked":True})
    assert result.status_code == 200, result.text
    result = state.client.post(base + "/edits/exact/preview", json={"operation_type":"DeleteClip", "clip_id":state.montage.clips[0].id})
    assert result.status_code == 409 and "锁定" in result.text


def test_insert_clip_inherits_source_span_and_shifts_every_linked_lane(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    anchor, later = state.montage.clips[0], state.montage.clips[1]
    before = state.client.get(base).json()
    payload = {"operation_type": "InsertClip", "clip_id": anchor.id, "asset_id": "asset_1",
               "segment_id": "segment_1", "source_start_seconds": 10}
    preview = state.client.post(base + "/edits/exact/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    confirmed = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    # 白名单真的生效：操作确实存进了 edit_operations，而不是被冻结集合挡在门外。
    recorded = state.production.edit_records.list_edit_operations(state.workspace_id, state.task.id)
    assert [operation.operation_type for operation in recorded] == ["InsertClip"]
    assert recorded[0].status == "applied"
    edited = state.client.get(base).json()["montage"]
    # 没有时长参数：插入的镜头就是素材从10秒到片段末尾16秒的那6秒。
    assert edited["duration_seconds"] == 14
    added = next(clip for clip in edited["clips"] if clip["id"] not in {c["id"] for c in before["montage"]["clips"]})
    assert (added["timeline_start_seconds"], added["timeline_end_seconds"]) == (4, 10)
    assert (added["source_start_seconds"], added["source_end_seconds"]) == (10, 16)
    assert added["manually_selected"] is True
    kept = next(clip for clip in edited["clips"] if clip["id"] == anchor.id)
    assert (kept["timeline_start_seconds"], kept["timeline_end_seconds"]) == (0, 4)
    moved = next(clip for clip in edited["clips"] if clip["id"] == later.id)
    assert (moved["timeline_start_seconds"], moved["timeline_end_seconds"]) == (10, 14)
    # 插入画面不等于复制声音：配音不重播，插入的6秒没有配音，原声整体后移。
    clips = edited["audio_timeline"]["tracks"][0]["clips"]
    assert [(c["timeline_start_ms"], c["source_end_ms"] - c["source_start_ms"]) for c in clips] == [
        (0, 4000), (10000, 4000)]
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["audio_timeline"] == edited["audio_timeline"]


def test_insert_clip_rejects_shifting_locked_later_shot(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    assert state.client.post(base + "/timeline/initialize").status_code == 200
    result = state.client.post(base + "/edits/exact", json={"operation_type":"SetClipLock", "clip_id":state.montage.clips[1].id, "locked":True})
    assert result.status_code == 200, result.text
    result = state.client.post(base + "/edits/exact/preview", json={"operation_type":"InsertClip",
        "clip_id":state.montage.clips[0].id, "asset_id":"asset_1", "segment_id":"segment_1"})
    assert result.status_code == 409 and "锁定" in result.text


def test_insert_clip_refuses_to_move_anything_without_usable_material(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    def preview(**extra):
        return state.client.post(base + "/edits/exact/preview", json={
            "operation_type": "InsertClip", "clip_id": state.montage.clips[0].id, **extra})
    assert preview().status_code == 422 and "素材片段" in preview().text
    assert preview(asset_id="asset_1", segment_id="segment_9").status_code == 404
    # 起点落在片段末尾之外，就没有可插入的长度。
    assert preview(asset_id="asset_1", segment_id="segment_1", source_start_seconds=16).status_code == 400
    assert state.client.get(base).json()["montage"] == before["montage"]


def test_insert_clip_command_says_which_material_is_missing(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    result = state.client.post(base + "/edits/preview", json={"raw_command": "插入镜头",
        "context_stage": "montage", "selected_object_refs": [f"clip:{state.montage.clips[0].id}"]})
    assert result.status_code == 422 and "素材片段" in result.text


def test_picture_speed_preserves_voice_and_clock_and_survives_split(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    clip = state.montage.clips[1]
    seen_rates = []
    def capture(inputs):
        seen_rates.append(main_module.clip_playback_rate(MontageClip(**inputs['clip'])))
    runtime=_granted_domain_preview_adapter(state,monkeypatch,capture)
    before = state.client.get(base).json()
    preview = state.client.post(base + "/edits/preview", json={"raw_command":"当前镜头改为0.5倍速，保留配音", "context_stage":"montage", "selected_object_refs":[f"clip:{clip.id}"]})
    assert preview.status_code == 200, preview.text
    assert seen_rates == [1, .5]
    assert preview.json()["proposal"]["preview"]["after"]["video_url"].startswith(base+"/visual/artifacts/")
    response = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert response.status_code == 200, response.text
    after = state.client.get(base).json()
    slowed = after["montage"]["clips"][1]
    assert slowed["source_end_seconds"] - slowed["source_start_seconds"] == 2
    assert after["montage"]["duration_seconds"] == 8
    assert after["montage"]["cues"] == before["montage"]["cues"]
    assert after["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]
    # A later non-speed edit must not silently restore 1x.
    response = state.client.post(base + "/edits/exact", json={"operation_type":"ChangeCrop", "clip_id":clip.id, "fit_mode":"fit", "crop_x":.5,"crop_y":.5})
    assert response.status_code == 200, response.text
    assert state.client.get(base).json()["montage"]["clips"][1]["source_end_seconds"] == slowed["source_end_seconds"]
    response = state.client.post(base + "/edits/exact", json={"operation_type":"SplitClip", "clip_id":clip.id, "split_time_seconds":6})
    assert response.status_code == 200, response.text
    pieces = state.client.get(base).json()["montage"]["clips"][1:]
    assert [(c["source_start_seconds"], c["source_end_seconds"]) for c in pieces] == [(slowed["source_start_seconds"],slowed["source_start_seconds"]+1),(slowed["source_start_seconds"]+1,slowed["source_end_seconds"])]
    response = state.client.post(base + "/edits/exact/preview", json={"operation_type":"ChangeClipSpeed", "clip_id":state.montage.clips[0].id,"playback_rate":.5})
    assert response.status_code == 409 and "口型" in response.text
    response = state.client.post(base + "/edits/preview", json={"raw_command":"当前镜头改为9倍速，保留配音", "context_stage":"montage", "selected_object_refs":[f"clip:{clip.id}"]})
    assert response.status_code == 422
    assert runtime.stop(1)


def test_linked_speed_preview_confirm_uses_same_derived_audio_and_rejects_stale(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    published = []
    def publish(*args, **kwargs):
        published.append(kwargs["kind"])
        return SimpleNamespace(id=f"preview-{len(published)}", logical_ref=f"artifacts/audio-{len(published)}.wav")
    service = SimpleNamespace(source=lambda *args: main_module.Path("fixture.wav"), publish=publish, validate=lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "_task_audio", lambda: service)
    before = state.client.get(base).json()
    preview = state.client.post(base + "/edits/exact/preview", json={"operation_type":"ChangeLinkedSpeed", "clip_id":state.montage.clips[1].id,"playback_rate":2})
    assert preview.status_code == 200, preview.text
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]
    assert published == []  # Logical draft speed never materializes audio.
    count = len(published)
    response = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert response.status_code == 200, response.text
    assert len(published) == count  # Confirmation uses the audio the user previewed.
    after = state.client.get(base).json()
    assert after["montage"]["duration_seconds"] == 6
    assert after["montage"]["audio_timeline"]["tracks"][0]["clips"][-1]["source_ref"] == state.montage.narration_audio_path
    assert after["montage"]["audio_timeline"]["tracks"][0]["clips"][-1]["playback_rate"] == 2
    assert state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 409
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["montage"]["clips"] == before["montage"]["clips"]


def test_full_subtitle_replace_respects_language_preserves_voice_and_undo(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    zh = SubtitleTrack(id="batch-zh", workspace_id=state.workspace_id, task_id=state.task.id, timeline_version_id=version.id,
        language="zh-CN", source_language=None, style={},
        cues=[ProductionSubtitleCue(id="shared", source_cue_id=None, text="AI工具 AI记录", start_seconds=0,end_seconds=4)],
        generation_method="timeline",model_id=None,artifact_ref="artifacts/zh.srt",transform_id="zh",status="valid")
    state.production.subtitle_records.create_subtitle_track(zh)
    state.production.subtitle_records.create_subtitle_track(replace(zh,id="batch-en",language="en-US",source_language="zh-CN",
        cues=[replace(zh.cues[0],text="AI tools and AI notes")],generation_method="translation"))
    before = state.client.get(base).json()
    single = state.client.post(base + "/edits/preview", json={"raw_command":"把这句字幕改成Local tools", "context_stage":"subtitle_cover",
        "selected_object_refs":["cue:shared","subtitle-track:batch-en"]})
    assert single.status_code == 200, single.text
    assert single.json()["proposal"]["operation"]["payload"]["track_id"] == "batch-en"
    preview = state.client.post(base + "/edits/preview", json={"raw_command":"全文把“AI”替换成“Local AI”", "context_stage":"subtitle_cover",
        "selected_object_refs":["cue:shared","subtitle-track:batch-en"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["proposal"]["preview"]["text_changes"][0]["occurrences"] == 2
    assert state.client.get(base).json()["subtitle_tracks"] == before["subtitle_tracks"]
    confirmed = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    after = state.client.get(base).json()
    assert {t["language"]:t["cues"][0]["text"] for t in after["subtitle_tracks"]} == {"zh-CN":"AI工具 AI记录","en-US":"Local AI tools and Local AI notes"}
    assert after["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]
    assert after["montage"]["clips"] == before["montage"]["clips"]
    assert after["narration"]["audio_reference"] == before["narration"]["audio_reference"]
    assert state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 409
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    current = state.client.get(base).json()
    assert [t["cues"] for t in current["subtitle_tracks"]] == [t["cues"] for t in before["subtitle_tracks"]]
    zh = next(t for t in current["subtitle_tracks"] if t["language"] == "zh-CN")
    preview = state.client.post(base + "/edits/exact/preview", json={"operation_type":"ReplaceSubtitleText","track_id":zh["id"],"find_text":"AI","replacement_text":"本地AI"})
    assert preview.status_code == 200, preview.text
    assert any("译文将失效" in x for x in preview.json()["proposal"]["external_effects"])
    assert state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 200
    assert [t["language"] for t in state.client.get(base).json()["subtitle_tracks"]] == ["zh-CN"]
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert len(state.client.get(base).json()["subtitle_tracks"]) == 2


def test_subtitle_structure_preview_export_facts_and_restore_empty_track(person_render_edit_state):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    track = SubtitleTrack(id="structure-en", workspace_id=state.workspace_id, task_id=state.task.id, timeline_version_id=version.id,
        language="en-US", source_language=None, style={},
        cues=[ProductionSubtitleCue(id="structure-cue", source_cue_id=None, text="Local video. Good sound.", start_seconds=0,end_seconds=4)],
        generation_method="timeline",model_id=None,artifact_ref="artifacts/structure.srt",transform_id="structure-transform",status="valid")
    state.production.subtitle_records.create_subtitle_track(track)
    before = state.client.get(base).json()
    def preview_confirm(command, current):
        selected = current["subtitle_tracks"][0]
        preview = state.client.post(base + "/edits/preview", json={"raw_command":command, "context_stage":"subtitle_cover",
            "selected_object_refs":[f"cue:{selected['cues'][0]['id']}", f"subtitle-track:{selected['id']}"]})
        assert preview.status_code == 200, preview.text
        assert state.client.get(base).json()["subtitle_tracks"] == current["subtitle_tracks"]
        confirmed = state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert state.client.post(base + f"/edits/{preview.json()['proposal']['id']}/confirm").status_code == 409
        result = state.client.get(base).json()
        assert result["montage"]["clips"] == before["montage"]["clips"]
        assert result["montage"]["audio_timeline"] == before["montage"]["audio_timeline"]
        assert result["narration"]["audio_reference"] == before["narration"]["audio_reference"]
        return result
    split = preview_confirm("当前字幕在第13字后2秒拆分", before)
    assert [cue["text"] for cue in split["subtitle_tracks"][0]["cues"]] == ["Local video. ", "Good sound."]
    merged = preview_confirm("当前字幕与下一条合并", split)
    assert merged["subtitle_tracks"][0]["cues"][0]["text"] == track.cues[0].text
    deleted = preview_confirm("删除当前字幕", merged)
    assert deleted["subtitle_tracks"] == []
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert state.client.get(base).json()["subtitle_tracks"][0]["cues"] == merged["subtitle_tracks"][0]["cues"]
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert state.client.get(base).json()["subtitle_tracks"] == []

    # Repeated undo/redo follows restored snapshots, including after a render.
    for expected in (1, 2, 1):
        response = state.client.post(base + "/timeline/undo", json={})
        assert response.status_code == 200, response.text
        assert len(state.client.get(base).json()["subtitle_tracks"][0]["cues"]) == expected
    assert state.client.get(base).json()["task"]["can_undo"] is False
    for expected in (2, 1, 0):
        response = state.client.post(base + "/timeline/redo", json={})
        assert response.status_code == 200, response.text
        tracks = state.client.get(base).json()["subtitle_tracks"]
        assert (len(tracks[0]["cues"]) if tracks else 0) == expected
    assert state.client.get(base).json()["task"]["can_redo"] is False


def test_subtitle_style_preview_and_confirm_preserve_voice_and_language_visibility(permitted_media_state, monkeypatch):
    state=permitted_media_state
    base=f"/api/creation-tasks/{state.task.id}"
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    track=SubtitleTrack(id="style-english",workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language="en-US",source_language=None,style={"enabled":False},cues=[ProductionSubtitleCue(id="caption",source_cue_id=None,
        text="Real local subtitles",start_seconds=0,end_seconds=4)],generation_method="timeline",model_id=None,
        artifact_ref="artifacts/style.srt",transform_id="style-transform",status="valid")
    state.production.subtitle_records.create_subtitle_track(track)
    before=state.client.get(base).json()
    missing=state.client.post(base+'/edits/exact/preview',json={"operation_type":"ConfigureSubtitleTrack","style":{"font_family":"Missing Subtitle Family 123456"}})
    assert missing.status_code==422,missing.text
    preview=state.client.post(base+'/edits/preview',json={"context_stage":"subtitle_cover","raw_command":"字幕字号改成64","selected_object_refs":[]})
    assert preview.status_code==200,preview.text
    assert state.client.get(base).json()['subtitle_tracks']==before['subtitle_tracks']
    image_url=preview.json()['proposal']['preview']['after']['video_url']
    image=state.client.get(image_url)
    assert image.status_code==200 and image.headers['content-type']=='video/mp4'
    assert b'ftyp' in image.content[:32]
    confirmed=state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=state.client.get(base).json()
    assert after['subtitle_tracks'][0]['style']=={"enabled":False,"font_size":64}
    assert after['subtitle_tracks'][0]['cues']==before['subtitle_tracks'][0]['cues']
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['subtitle_tracks'][0]['style']==track.style



@pytest.mark.parametrize('ratio', ['16:9','1:1'])
def test_canvas_preview_confirmation_and_undo_keep_time_and_voice(person_render_edit_state,monkeypatch,ratio):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    seen=[]
    runtime=_granted_domain_preview_adapter(state,monkeypatch,lambda inputs:seen.append(inputs['plan']['aspect_ratio']))
    from copy import deepcopy
    locked=deepcopy(state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id))
    locked['clips'][0]['locked']=True
    with monkeypatch.context() as local:
        local.setattr(state.production,'get_creation_task_montage_plan',lambda *args:locked)
        rejected=state.client.post(base+'/edits/exact/preview',json={'operation_type':'ChangeCanvas','aspect_ratio':ratio})
        assert rejected.status_code==422 and '解锁' in rejected.text
        assert seen==[]
    preview=state.client.post(base+'/edits/exact/preview',json={'operation_type':'ChangeCanvas','aspect_ratio':ratio,'fit_mode':'fit'})
    assert preview.status_code==200,preview.text
    with state.production._connect() as conn:
        saved = conn.execute("SELECT e.payload_json FROM task_events e JOIN job_runs j ON j.workspace_id=e.workspace_id AND j.id=e.job_id "
            "WHERE e.event_type='task_media_submitted' AND j.workspace_id=? AND j.task_id=? ORDER BY j.created_at DESC LIMIT 1",
            (state.workspace_id,state.task.id)).fetchone()
    saved_payload=json.loads(saved['payload_json'])['arguments']['payload']
    assert set(saved_payload)=={'operation_type','aspect_ratio','fit_mode'}
    restored=main_module.CreationTaskExactEditRequest.model_validate(saved_payload)
    assert restored.model_fields_set=={'operation_type','aspect_ratio','fit_mode'}
    assert seen==['9:16',ratio]
    assert state.client.get(base).json()['montage']['aspect_ratio']=='9:16'
    confirmed=state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=state.client.get(base).json()
    assert after['montage']['aspect_ratio']==ratio
    assert all(clip['fit_mode']=='fit' for clip in after['montage']['clips'])
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['duration_seconds']==before['montage']['duration_seconds']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['aspect_ratio']=='9:16'
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['aspect_ratio']==ratio
    assert runtime.stop(1)


@pytest.mark.parametrize("status", ["unavailable", "failed"])
def test_canvas_edit_preserves_nonvalid_subtitle_state_through_persisted_history(person_render_edit_state, monkeypatch, status):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    base_version = main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    cue = ProductionSubtitleCue(id="canvas-caption", source_cue_id=None, text="保留的字幕", start_seconds=0, end_seconds=2)
    unavailable = SubtitleTrack(
        id="canvas-unavailable", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=base_version.id, language="zh-CN", source_language=None,
        style={"enabled": True}, cues=[cue], generation_method="timeline", model_id=None,
        artifact_ref=None, transform_id=None, status=status,
        failure_reason="字幕服务尚未就绪" if status == "unavailable" else "字幕产物校验失败",
        impact="字幕文字保留，但不能作为有效输出。", repair_action="检查字幕并重新生成。",
    )
    valid = replace(
        unavailable, id="canvas-valid", language="en-US", source_language="zh-CN",
        generation_method="translation", artifact_ref="artifacts/valid-caption.srt",
        transform_id="transform-valid-caption", status="valid", failure_reason=None,
        impact=None, repair_action=None,
    )
    original_tracks = main_module._current_subtitle_tracks

    def tracks_with_unavailable(workspace_id, task, *, current_version=None, montage=None):
        tracks = original_tracks(workspace_id, task, current_version=current_version, montage=montage)
        if current_version and current_version.id == base_version.id:
            return [unavailable, valid]
        return tracks

    monkeypatch.setattr(main_module, "_current_subtitle_tracks", tracks_with_unavailable)
    before = state.client.get(base).json()
    assert [(track["status"], track["failure_reason"], track["impact"], track["repair_action"])
            for track in before["subtitle_tracks"]] == [
                (status, unavailable.failure_reason, unavailable.impact, unavailable.repair_action),
                ("valid", None, None, None),
            ]
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *args, **kwargs:
        pytest.fail("ordinary canvas draft must not encode media"))

    changed = state.client.post(base + "/edits/exact", json={"operation_type": "ChangeCanvas", "aspect_ratio": "1:1"})
    assert changed.status_code == 200, changed.text
    after = state.client.get(base).json()
    assert after["task"]["current_timeline_version_id"] != before["task"]["current_timeline_version_id"]
    assert after["montage"]["aspect_ratio"] == "1:1"
    assert [(track["status"], track["failure_reason"], track["impact"], track["repair_action"])
            for track in after["subtitle_tracks"]] == [
                (status, unavailable.failure_reason, unavailable.impact, unavailable.repair_action),
                ("valid", None, None, None),
            ]

    undone = state.client.post(base + "/timeline/undo", json={})
    assert undone.status_code == 200, undone.text
    restored = state.client.get(base).json()
    assert restored["montage"]["aspect_ratio"] == before["montage"]["aspect_ratio"]
    assert [(track["status"], track["failure_reason"], track["impact"], track["repair_action"])
            for track in restored["subtitle_tracks"]] == [
                (status, unavailable.failure_reason, unavailable.impact, unavailable.repair_action),
                ("valid", None, None, None),
            ]
    redone = state.client.post(base + "/timeline/redo", json={})
    assert redone.status_code == 200, redone.text
    redone_workspace = state.client.get(base).json()
    assert redone_workspace["montage"]["aspect_ratio"] == "1:1"
    assert [(track["status"], track["failure_reason"], track["impact"], track["repair_action"])
            for track in redone_workspace["subtitle_tracks"]] == [
                (status, unavailable.failure_reason, unavailable.impact, unavailable.repair_action),
                ("valid", None, None, None),
            ]

    newly_generated = main_module._clone_subtitle_track(
        unavailable, timeline_version_id="timeline_generated-caption",
        artifact_ref="artifacts/regenerated-caption.srt", transform_id="transform-regenerated-caption",
    )
    assert newly_generated.status == "valid"
    assert newly_generated.failure_reason is newly_generated.impact is newly_generated.repair_action is None


def test_geometry_edit_uses_existing_version_and_preserves_audio(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    selected=before['montage']['clips'][0]
    runtime=_granted_domain_preview_adapter(state,monkeypatch)
    preview=state.client.post(base+'/edits/preview',json={'context_stage':'montage','raw_command':'当前镜头缩小到0.8倍','selected_object_refs':[f"clip:{selected['id']}"]})
    assert preview.status_code==200,preview.text
    assert preview.json()['proposal']['operation']['payload']['effects']['zoom']==.8
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    confirm=state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm')
    assert confirm.status_code==200,confirm.text
    after=state.client.get(base).json()
    assert after['montage']['clips'][0]['visual_effects']['zoom']==.8
    assert after['montage']['clips'][1:]==before['montage']['clips'][1:]
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['duration_seconds']==before['montage']['duration_seconds']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert runtime.stop(1)


def test_visual_preview_keeps_trimmed_person_source_interval(person_render_edit_state,monkeypatch):
    from copy import deepcopy
    state=person_render_edit_state
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    plan=deepcopy(state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id))
    plan['clips'][0]['source_start_seconds']=1
    plan['clips'][0]['source_end_seconds']=3
    monkeypatch.setattr(state.production,'get_creation_task_montage_plan',lambda *args:plan)
    ranges=[]
    def capture(inputs):
        target=inputs['plan']['clips'][0]
        ranges.append((target['source_start_seconds'],target['source_end_seconds']))
    runtime=_granted_domain_preview_adapter(state,monkeypatch,capture)
    response=state.client.post(f'/api/creation-tasks/{state.task.id}/visual/preview',json={
        'base_version_id':version.id,'clip_ids':[plan['clips'][0]['id']],'effects':{'zoom':.8}})
    assert response.status_code==200,response.text
    assert ranges==[(1,3),(1,3)]
    assert runtime.stop(1)


def test_fade_preview_validates_all_targets_and_roundtrips(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    limits=[]
    runtime=_granted_domain_preview_adapter(state,monkeypatch,lambda inputs:limits.append(None))
    invalid=state.client.post(base+'/visual/preview',json={'base_version_id':before['task']['current_timeline_version_id'],
        'clip_ids':[c['id'] for c in before['montage']['clips']],'effects':{'fade_in_seconds':5,'fade_out_seconds':5}})
    assert invalid.status_code==422 and not limits
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'当前镜头淡出0.4秒','context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    assert preview.status_code==200,preview.text
    assert limits==[None,None]
    assert '完整区间' in preview.json()['proposal']['preview']['after']['detail']
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    confirmed=state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=state.client.get(base).json()
    assert after['montage']['clips'][0]['visual_effects']['fade_out_seconds']==.4
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['duration_seconds']==before['montage']['duration_seconds']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==after['montage']['clips']
    assert runtime.stop(1)


def test_split_inside_fade_preserves_envelope_clock(person_render_edit_state):
    state=person_render_edit_state
    clip=state.montage.clips[0]
    clip.visual_effects={'fade_in_seconds':.4,'fade_out_seconds':.4,'exposure':.5}
    frames=round(clip.timeline_end_seconds*30)-round(clip.timeline_start_seconds*30)
    from app.schemas import CreationTaskMontageClipUpdateRequest
    plan,_tracks,_linkage=main_module._apply_creation_task_montage_clip_update(
        state.workspace_id,
        state.task.id,
        state.montage,
        clip.id,
        CreationTaskMontageClipUpdateRequest(split_time_seconds=clip.timeline_start_seconds+.2),
        request=state.request,
    )
    assert plan.clips[0].visual_effects==plan.clips[1].visual_effects=={'fade_in_seconds':.4,'fade_out_seconds':.4,'exposure':.5}
    assert plan.clips[0].fade_envelope_duration_frames==plan.clips[1].fade_envelope_duration_frames==frames
    assert plan.clips[0].fade_envelope_offset_frames==0
    assert plan.clips[1].fade_envelope_offset_frames==6


def test_dissolve_exact_and_natural_share_version_preview_confirm_undo(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    target=before['montage']['clips'][1]
    calls=[]
    def publish(*args,**kwargs):
        calls.append(kwargs['parameters'])
        return SimpleNamespace(id='transition',logical_ref='artifacts/transition.mp4')
    monkeypatch.setattr(main_module,'_task_audio',lambda:SimpleNamespace(
        publish=publish,validate_material_use=lambda *args:None))
    result=state.client.post(base+'/edits/preview',json={'raw_command':'当前镜头叠化0.4秒','context_stage':'montage','selected_object_refs':[f"clip:{target['id']}"]})
    assert result.status_code==200,result.text
    assert len(calls)==2
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    result=state.client.post(base+'/edits/'+result.json()['proposal']['id']+'/confirm')
    assert result.status_code==200,result.text
    after=state.client.get(base).json()
    assert after['montage']['clips'][1]['transition_in_seconds']==.4
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['duration_seconds']==before['montage']['duration_seconds']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==after['montage']['clips']
    result=state.client.post(base+'/edits/exact/preview',json={'operation_type':'SetClipTransition','clip_id':target['id'],'transition_seconds':0})
    assert result.status_code==200,result.text
    assert state.client.post(base+'/edits/'+result.json()['proposal']['id']+'/confirm').status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    state.client.post(base+'/edits/exact',json={'operation_type':'SetClipLock','clip_id':before['montage']['clips'][0]['id'],'locked':True})
    count=len(calls)
    result=state.client.post(base+'/edits/exact/preview',json={'operation_type':'SetClipTransition','clip_id':target['id'],'transition_seconds':.4})
    assert result.status_code==409 and len(calls)==count


def test_titles_share_preview_version_and_undo_without_changing_caption_or_voice(permitted_media_state,monkeypatch):
    state=permitted_media_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='title',logical_ref='artifacts/title.mp4'))
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'前3秒加标题“本地 / Local”','context_stage':'montage','selected_object_refs':[]})
    assert preview.status_code==200,preview.text
    assert state.client.get(base).json()['montage']['graphic_overlays']==[]
    assert state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    assert after['montage']['graphic_overlays'][0]['text']=='本地 / Local'
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['clips']==before['montage']['clips']
    assert [t['cues'] for t in after['subtitle_tracks']]==[t['cues'] for t in before['subtitle_tracks']]
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['graphic_overlays']==[]
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['graphic_overlays']==after['montage']['graphic_overlays']
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'标题1删除','context_stage':'montage','selected_object_refs':[]})
    assert preview.status_code==200,preview.text
    assert state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm').status_code==200
    assert state.client.get(base).json()['montage']['graphic_overlays']==[]



def test_pure_arrow_nl_command_reaches_overlay_compiler(person_render_edit_state,monkeypatch):
    from app.graphic_overlays import ArrowOverlay
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    arrow=ArrowOverlay(x=.46,y=.35,name='进料口',start_seconds=.2,end_seconds=1.2).model_dump()
    state.montage.graphic_overlays.append(arrow)
    state.production.montage_plan_adoption.save_creation_task_montage_plan(
        state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage),
    )
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='arrow',logical_ref='artifacts/arrow.mp4'))
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'箭头1删除','context_stage':'montage','selected_object_refs':[]})
    assert preview.status_code==200,preview.text
    assert preview.json()['proposal']['target_refs']==[f"overlay:{arrow['id']}"]
    assert state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm').status_code==200
    assert state.client.get(base).json()['montage']['graphic_overlays']==[]


def test_graphic_import_ownership_and_image_layer_roundtrip(person_render_edit_state,monkeypatch,tmp_path):
    import io
    from PIL import Image
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    monkeypatch.setattr(main_module.settings,'effective_temporary_root_path',tmp_path,raising=False)
    content=io.BytesIO()
    Image.new('RGBA',(80,40),(255,0,0,128)).save(content,format='PNG')
    imported=state.client.post(base+'/graphics/import',files={'image':('logo.png',content.getvalue(),'image/png')})
    assert imported.status_code==200,imported.text
    source=imported.json()
    assert source['width']==80 and source['height']==40
    records=state.client.get(base+'/graphics/sources').json()
    assert records[0]['source_ref']==source['source_ref']
    read=state.client.get(base+'/graphics/source/'+source['artifact_id'])
    assert read.status_code==200
    assert Image.open(io.BytesIO(read.content)).getpixel((0,0))==(255,0,0,128)
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='image',logical_ref='artifacts/image.mp4'))
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'前3秒加图片“logo.png”','context_stage':'montage','selected_object_refs':[]})
    assert preview.status_code==200,preview.text
    assert state.client.post(base+'/edits/'+preview.json()['proposal']['id']+'/confirm').status_code==200
    result=state.client.get(base).json()['montage']['graphic_overlays'][0]
    assert result['kind']=='image' and result['source_ref']==source['source_ref']
    contextual=state.client.post(base+'/edits/preview',json={'raw_command':'当前图片不透明度改为50%','context_stage':'montage','selected_object_refs':[f"overlay:{result['id']}"]})
    assert contextual.status_code==200,contextual.text
    assert contextual.json()['proposal']['target_refs']==[f"overlay:{result['id']}"]
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['graphic_overlays']==[]
    invalid=state.client.post(base+'/edits/exact/preview',json={'operation_type':'EditGraphicOverlay','overlay_action':'add',
        'graphic_overlay':{**result,'source_ref':'/etc/passwd'}})
    assert invalid.status_code==422 and '当前任务' in invalid.text
    foreign=state.client.post(base+'/edits/exact/preview',json={'operation_type':'EditGraphicOverlay','overlay_action':'add',
        'graphic_overlay':{**result,'source_ref':'artifacts/other-task/private.png'}})
    assert foreign.status_code==422
    from app.creation.contracts import CreationTask
    from dataclasses import replace
    other_plan=replace(state.production.plan_records.get_creation_plan(state.workspace_id,state.task.plan_id),id='other-image-plan')
    other=CreationTask(id='other-image-task',workspace_id=state.workspace_id,
        plan_id=other_plan.id,name='Other task',scenario=state.task.scenario)
    state.production.plan_confirmation.confirm_creation_plan(other_plan,other)
    other_import=state.client.post(f'/api/creation-tasks/{other.id}/graphics/import',files={'image':('other.png',content.getvalue(),'image/png')})
    assert other_import.status_code==200,other_import.text
    other_source=other_import.json()
    assert state.client.get(base+'/graphics/source/'+other_source['artifact_id']).status_code==404
    foreign=state.client.post(base+'/edits/exact/preview',json={'operation_type':'EditGraphicOverlay','overlay_action':'add',
        'graphic_overlay':{**result,'source_ref':other_source['source_ref']}})
    assert foreign.status_code==422
    assert state.client.post(base+'/graphics/import',files={'image':('bad.svg',b'<svg/>','image/svg+xml')}).status_code==422


def test_graphic_import_corrects_photo_orientation_and_drops_metadata(person_render_edit_state,monkeypatch,tmp_path):
    import io
    from PIL import Image
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    monkeypatch.setattr(main_module.settings,'effective_temporary_root_path',tmp_path,raising=False)
    image=Image.new('RGB',(20,10),(0,120,200))
    exif=Image.Exif()
    exif[274]=6
    data=io.BytesIO()
    image.save(data,format='JPEG',exif=exif)
    response=state.client.post(base+'/graphics/import',files={'image':('portrait.jpg',data.getvalue(),'image/jpeg')})
    assert response.status_code==200,response.text
    source=response.json()
    assert (source['width'],source['height'])==(10,20)
    loaded=Image.open(io.BytesIO(state.client.get(base+'/graphics/source/'+source['artifact_id']).content))
    assert loaded.size==(10,20) and not loaded.getexif()


def test_compound_visual_edit_is_one_version_and_rejects_unknown_before_render(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    clip_id=before['montage']['clips'][0]['id']
    calls=[]
    runtime=_granted_domain_preview_adapter(state,monkeypatch,lambda inputs:calls.append(True))
    def preview(command):return state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'montage','selected_object_refs':[f'clip:{clip_id}']})
    assert preview('亮一点，加上闪电特效').status_code==422
    assert calls==[] and state.client.get(base).json()['task']['current_timeline_version_id']==before['task']['current_timeline_version_id']
    result=preview('当前镜头缩小到0.8倍，向右移动5%，曝光改为0.5，配音不变')
    assert result.status_code==200,result.text
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/edits/'+result.json()['proposal']['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    assert after['task']['timeline_sequence']==before['task']['timeline_sequence']+1
    effect=after['montage']['clips'][0]['visual_effects']
    assert effect['zoom']==.8 and effect['offset_x']==.05 and effect['exposure']==.5
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert after['montage']['clips'][1:]==before['montage']['clips'][1:]
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert runtime.stop(1)


def test_cross_object_proposal_is_atomic_and_stale_safe(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    calls=[]
    source_service = main_module._task_audio()
    monkeypatch.setattr(source_service, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, '_task_audio', lambda: source_service)
    def publish(*args,**kwargs):
        calls.append(True)
        return SimpleNamespace(id='compound',logical_ref='artifacts/compound.mp4')
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',publish)
    def preview(command):return state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    bad=preview('当前镜头亮一点，同时配音小一点，然后增加闪电特效')
    assert bad.status_code==422,bad.text
    assert calls==[] and state.client.get(base).json()['task']['current_timeline_version_id']==before['task']['current_timeline_version_id']
    result=preview('当前镜头亮一点，同时配音小一点')
    assert result.status_code==200,result.text
    proposal=result.json()['proposal']
    assert proposal['operation']['operation_type']=='EditCompoundTimeline'
    assert len(proposal['target_refs'])==2
    stale=preview('当前镜头暗一点，同时配音大一点').json()['proposal']
    assert state.client.post(base+'/edits/'+proposal['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    assert after['task']['timeline_sequence']==before['task']['timeline_sequence']+1
    assert after['montage']['clips'][0]['visual_effects']['exposure']==.25
    assert after['montage']['audio_timeline']['tracks'][0]['gain_db']==-3
    assert after['montage']['clips'][1:]==before['montage']['clips'][1:]
    assert state.client.post(base+'/edits/'+stale['id']+'/confirm').status_code==409
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    assert restored['montage']['clips']==before['montage']['clips']
    assert restored['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['audio_timeline']==after['montage']['audio_timeline']


def test_cross_object_compiler_keeps_context_and_lock_boundaries(person_render_edit_state):
    from copy import deepcopy
    from app.compound_edits import compile_compound_edit
    state=person_render_edit_state
    plan=deepcopy(state.montage)
    audio=main_module._effective_audio_timeline(plan)
    refs=[f'clip:{plan.clips[0].id}']
    changed,steps=compile_compound_edit('当前镜头亮一点，同时配音片段1音量降低3分贝，然后淡出0.4秒',plan,audio,refs,None,None)
    assert changed.audio_timeline.tracks[0].clips[0].fade_out_ms==400
    assert changed.clips[0].visual_effects.get('fade_out_seconds',0)==0
    assert len(steps)==3 and steps[-1]['target_ref'].startswith('audio:')
    assert compile_compound_edit('当前配音音量降低3分贝，然后淡出0.4秒',plan,audio,['audio:'+audio.tracks[0].clips[0].id],None,None) is None
    with pytest.raises(ValueError,match='冲突'):
        compile_compound_edit('当前镜头亮一点，同时配音小一点，配音不变',plan,audio,refs,None,None)
    with pytest.raises(ValueError,match='没有音频'):
        compile_compound_edit('当前镜头亮一点，同时音乐小一点',plan,audio,refs,None,None)
    plan.clips[0].locked=True
    with pytest.raises(ValueError,match='锁定'):
        compile_compound_edit('配音小一点，同时当前镜头亮一点',plan,audio,refs,None,None)
    assert audio.tracks[0].gain_db==0


def test_compound_partial_selection_recompiles_from_base_and_preserves_context(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    source_service = main_module._task_audio()
    monkeypatch.setattr(source_service, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, '_task_audio', lambda: source_service)
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='partial',logical_ref='artifacts/partial.mp4'))
    result=state.client.post(base+'/edits/preview',json={'raw_command':'当前镜头亮一点，同时配音片段1音量降低3分贝，然后淡出0.4秒','context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    assert result.status_code==200,result.text
    first=result.json()['proposal']
    route=base+'/edits/'+first['id']+'/selection/preview'
    for invalid in ([],[99],[-1]):
        assert state.client.post(route,json={'included_step_indices':invalid}).status_code==422
    partial=state.client.post(route,json={'included_step_indices':[0,2]})
    assert partial.status_code==200,partial.text
    proposal=partial.json()['proposal']
    data=proposal['operation']['payload']
    assert [s['enabled'] for s in data['steps']]==[True,False,True]
    assert data['audio_timeline']['tracks'][0]['clips'][0]['gain_db']==0
    assert data['audio_timeline']['tracks'][0]['clips'][0]['fade_out_ms']==400
    assert state.client.get(base).json()['task']['current_timeline_version_id']==before['task']['current_timeline_version_id']
    restored=state.client.post(base+'/edits/'+proposal['id']+'/selection/preview',json={'raw_command':'恢复第2项'})
    assert restored.status_code==200
    assert restored.json()['proposal']['operation']['payload']['audio_timeline']['tracks'][0]['clips'][0]['gain_db']==-3
    assert state.client.post(base+'/edits/'+proposal['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    assert after['montage']['audio_timeline']['tracks'][0]['clips'][0]['gain_db']==0
    assert state.client.post(route,json={'included_step_indices':[0]}).status_code==409
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['audio_timeline']==before['montage']['audio_timeline']


@pytest.mark.parametrize("bilingual", [False, True])
def test_local_subtitle_alignment_preview_confirm_undo(person_render_edit_state, bilingual):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    main_module._ensure_montage_base_version(state.workspace_id, state.task, state.montage)
    before = state.client.get(base).json()
    command = "按配音校准双语字幕" if bilingual else "按配音校准中文字幕"
    response = state.client.post(base + "/edits/preview", json={"raw_command": command,
        "context_stage": "subtitle_cover", "selected_object_refs": []})
    assert response.status_code == 503
    assert "独立模型资源预算" in response.text
    assert state.client.get(base).json() == before
    assert not state.production.edit_records.list_edit_proposals(state.workspace_id, state.task.id)


def test_subtitle_style_joins_compound_preview_selection_and_undo(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    state.production.subtitle_records.create_subtitle_track(SubtitleTrack(id="compound-caption",workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language="en-US",source_language=None,style={"font_size":40},cues=[ProductionSubtitleCue(id="compound-cue",source_cue_id=None,text="Local video",start_seconds=0,end_seconds=4)],
        generation_method="timeline",model_id=None,artifact_ref="artifacts/compound.srt",transform_id="compound-transform",status="valid"))
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    captured=[]
    source_service = main_module._task_audio()
    monkeypatch.setattr(source_service, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, '_task_audio', lambda: source_service)
    def publish(workspace,task,version,plan,tracks,**kwargs):
        captured.append([track.style for track in tracks])
        return SimpleNamespace(id='subtitle-compound',logical_ref='artifacts/compound.mp4')
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',publish)
    result=state.client.post(base+'/edits/preview',json={'raw_command':'当前镜头亮一点，同时字幕字号改为64，配音小一点','context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    assert result.status_code==200,result.text
    proposal=result.json()['proposal']
    assert captured[-1][0]['font_size']==64
    assert state.client.get(base).json()['subtitle_tracks']==before['subtitle_tracks']
    selected=state.client.post(base+'/edits/'+proposal['id']+'/selection/preview',json={'raw_command':'只保留字幕修改'})
    assert selected.status_code==200,selected.text
    partial=selected.json()['proposal']
    assert partial['target_refs']==['subtitle:all']
    adopted = state.client.post(base+'/edits/'+partial['id']+'/confirm')
    assert adopted.status_code == 200, adopted.text
    after=state.client.get(base).json()
    assert after['subtitle_tracks'][0]['style']['font_size']==64
    assert [track['cues'] for track in after['subtitle_tracks']]==[track['cues'] for track in before['subtitle_tracks']]
    assert after['montage']['clips']==before['montage']['clips']
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    assert [track['style'] for track in restored['subtitle_tracks']]==[track['style'] for track in before['subtitle_tracks']]
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['subtitle_tracks'][0]['style']['font_size']==64


def test_compound_caption_text_time_and_translation_boundary(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    zh=SubtitleTrack(id='compound-zh',workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language='zh-CN',source_language=None,style={},cues=[ProductionSubtitleCue(id='compound-source',source_cue_id=None,text='本地视频',start_seconds=0,end_seconds=4)],
        generation_method='timeline',model_id=None,artifact_ref='artifacts/source.srt',transform_id='source',status='valid')
    state.production.subtitle_records.create_subtitle_track(zh)
    state.production.subtitle_records.create_subtitle_track(replace(zh,id='compound-en',language='en-US',source_language='zh-CN',
        cues=[replace(zh.cues[0],id='compound-translation',source_cue_id=zh.cues[0].id,text='Local video')],generation_method='translation'))
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    source_service = main_module._task_audio()
    monkeypatch.setattr(source_service, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, '_task_audio', lambda: source_service)
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='text-preview',logical_ref='artifacts/text-preview.mp4'))
    def preview(command):return state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    assert preview('当前镜头亮一点，同时字幕1改成“歧义字幕”').status_code==422
    assert preview('当前镜头亮一点，同时英文字幕1显示0到99秒').status_code==422
    response=preview('当前镜头亮一点，同时英文字幕1改成“Local, 然后更清楚”，英文字幕1显示0.2到3.5秒')
    assert response.status_code==200,response.text
    assert state.client.post(base+'/edits/'+response.json()['proposal']['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    track=next(t for t in after['subtitle_tracks'] if t['language']=='en-US')
    assert track['cues'][0]['text']=='Local, 然后更清楚'
    assert track['cues'][0]['start_seconds']==.2 and track['cues'][0]['end_seconds']==3.5
    assert track['artifact_ref']!='artifacts/source.srt'
    assert after['montage']['audio_timeline']==before['montage']['audio_timeline']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert [t['cues'] for t in state.client.get(base).json()['subtitle_tracks']]==[t['cues'] for t in before['subtitle_tracks']]
    response=preview('当前镜头亮一点，同时中文字幕1改成“真实修改”')
    assert response.status_code==200,response.text
    assert any('英文翻译' in effect for effect in response.json()['proposal']['external_effects'])
    assert state.client.post(base+'/edits/'+response.json()['proposal']['id']+'/confirm').status_code==200
    assert [t['language'] for t in state.client.get(base).json()['subtitle_tracks']]==['zh-CN']


@pytest.mark.parametrize('command', ['不要重新配音', '读慢一点，同时字幕变黄', '重新配音，开心一点', '读快一点，同时读慢一点'])
def test_narration_preview_rejects_unexecutable_whole_request(person_render_edit_state, command):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    before = state.client.get(base).json()
    response = state.client.post(base + '/edits/preview', json={
        'raw_command': command, 'context_stage': 'narration',
        'selected_object_refs': [f'narration:{state.montage.cues[0].narration_segment_id}'],
    })
    assert response.status_code in (409, 422), response.text
    assert state.client.get(base).json() == before


def test_narration_preview_explains_actual_tempo(person_render_edit_state):
    state = person_render_edit_state
    response = state.client.post(f'/api/creation-tasks/{state.task.id}/edits/preview', json={
        'raw_command': '这句重新配音，同时读慢一点', 'context_stage': 'narration',
        'selected_object_refs': [f'narration:{state.montage.cues[0].narration_segment_id}'],
    })
    assert response.status_code == 200, response.text
    assert '0.9 倍速' in response.json()['proposal']['summary']


@pytest.mark.parametrize('command', ['不要重做口型', '人物更自然', '重做口型，同时字幕变黄'])
def test_person_preview_rejects_partial_or_negated_request(person_render_edit_state, command):
    state = person_render_edit_state
    response = state.client.post(f'/api/creation-tasks/{state.task.id}/edits/preview', json={
        'raw_command': command, 'context_stage': 'review',
        'selected_object_refs': ['clip:clip-person-target'],
    })
    assert response.status_code in (409, 422), response.text
    assert state.production.edit_records.list_edit_proposals(state.workspace_id, state.task.id) == []
    assert state.provider.output_cache_keys == []


def test_person_generation_reads_narration_language(permitted_person_edit_state, monkeypatch):
    state = permitted_person_edit_state
    segment = state.production.narration_records.get_narration_segment(state.workspace_id, state.task.id, state.montage.cues[0].narration_segment_id)
    voice = state.production.get_versioned_voice_profile(state.workspace_id, segment.voice_profile_id, segment.voice_version)
    state.production.save_versioned_voice_profile(
        replace(voice, version='v2', language='en'),
        verified_owner_user_id=state.member.member_id,
    )
    state.production.narration_records.save_narration_segments(state.workspace_id, state.task.id, segment.script_id,
                                            [replace(segment, voice_version='v2', language='en')])
    plan = state.production.plan_records.get_creation_plan(state.workspace_id, state.task.plan_id)
    plan.selected_voice = replace(plan.selected_voice, version='v2', language='en')
    with state.production._connect() as conn:
        conn.execute("UPDATE creation_plans SET payload=? WHERE id=?", (json.dumps(asdict(plan), default=str), plan.id))
    state.provider.supported_languages = ('zh', 'en')
    with state.granted() as context:
        main_module._generate_creation_task_person_render_record(state.workspace_id, state.task.id,
            main_module.CreationTaskPersonRenderRequest(
                narration_segment_id=state.montage.cues[0].narration_segment_id,
                person_profile_id=state.old_render.person_profile_id,
                person_version=state.old_render.person_version, motion='natural', expression='neutral'), grant_context=context)
    assert state.provider.languages == ['en']
    assert len(state.provider.output_cache_keys) == 1


def test_person_preview_rejects_unsupported_narration_language(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    original_get = state.production.narration_records.get_narration_segment
    monkeypatch.setattr(state.production.narration_records, 'get_narration_segment', lambda *args: replace(original_get(*args), language='en'))
    response = state.client.post(f'/api/creation-tasks/{state.task.id}/edits/preview', json={
        'raw_command': '这里只重做口型', 'context_stage': 'review',
        'selected_object_refs': ['clip:clip-person-target'],
    })
    assert response.status_code == 503, response.text
    assert response.json()['detail']['error'] == 'language_unavailable'
    assert state.production.edit_records.list_edit_proposals(state.workspace_id, state.task.id) == []
    assert state.provider.output_cache_keys == []


def test_person_reuse_requires_current_audio_lineage(permitted_person_edit_state, monkeypatch):
    state = permitted_person_edit_state
    base = f'/api/creation-tasks/{state.task.id}'
    command = {'raw_command': '这一段换成当前人物出镜', 'context_stage': 'review',
               'selected_object_refs': ['clip:clip-person-target']}
    # A legacy output without generation lineage is not proof of current audio.
    legacy = state.client.post(base + '/edits/preview', json=command)
    assert legacy.status_code == 200, legacy.text
    assert 'reuse_person_render_id' not in legacy.json()['proposal']['operation']['payload']
    with state.granted() as context:
        generated = main_module._generate_creation_task_person_render_record(
            state.workspace_id, state.task.id, main_module.CreationTaskPersonRenderRequest(
                narration_segment_id=state.montage.cues[0].narration_segment_id,
                person_profile_id=state.old_render.person_profile_id,
                person_version=state.old_render.person_version, motion='natural', expression='neutral'),
            regeneration_token='lineage-test', grant_context=context)
    preview = state.client.post(base + '/edits/preview', json=command)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()['proposal']
    assert proposal['operation']['payload']['reuse_person_render_id'] == generated.id
    original_get = state.production.narration_records.get_narration_segment
    before = state.client.get(base).json()
    with monkeypatch.context() as scoped:
        scoped.setattr(state.production.narration_records, 'get_narration_segment', lambda *args:
                       replace(original_get(*args), audio_reference='narration://artifact/replacement'))
        changed = state.client.post(base + '/edits/preview', json=command)
        assert changed.status_code == 200, changed.text
        assert 'reuse_person_render_id' not in changed.json()['proposal']['operation']['payload']
        rejected = state.client.post(base + f"/edits/{proposal['id']}/confirm")
        assert rejected.status_code == 409, rejected.text
        assert '配音已改变' in rejected.text
    assert state.client.get(base).json()['montage'] == before['montage']
    assert len(state.provider.output_cache_keys) == 1


@pytest.mark.parametrize("role", ["audio", "visual"])
def test_person_cache_rejects_same_path_mutation(permitted_person_edit_state, role):
    state = permitted_person_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    command = {"raw_command": "这一段换成当前人物出镜", "context_stage": "review",
               "selected_object_refs": ["clip:clip-person-target"]}
    with state.granted() as context:
        generated = main_module._generate_creation_task_person_render_record(
            state.workspace_id, state.task.id, main_module.CreationTaskPersonRenderRequest(
                narration_segment_id=state.montage.cues[0].narration_segment_id,
                person_profile_id=state.old_render.person_profile_id,
                person_version=state.old_render.person_version), grant_context=context)
    preview = state.client.post(base + "/edits/preview", json=command)
    assert preview.status_code == 200, preview.text
    proposal = preview.json()["proposal"]
    assert proposal["operation"]["payload"]["reuse_person_render_id"] == generated.id
    transform = next(t for t in state.production.lineage_records.list_transform_runs(state.workspace_id, state.task.id)
                     if t.input_digest == generated.cache_key and t.status == "succeeded")
    fact = next(f for f in transform.parameters["presenter_input_facts"] if f["role"] == role)
    path = Path(fact["path"])
    stat = path.stat()
    path.write_bytes(bytes(b ^ 1 for b in path.read_bytes()))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size and path.stat().st_mtime_ns == stat.st_mtime_ns
    assert path.stat().st_ctime_ns != stat.st_ctime_ns
    with state.production._connect() as conn:
        before = tuple(conn.iterdump())
    root = Path(main_module.settings.video_output_path)
    outputs = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    calls = list(state.provider.output_cache_keys)
    rejected = state.client.post(base + f"/edits/{proposal['id']}/confirm")
    assert rejected.status_code == 409, rejected.text
    assert "reuse target has changed" in rejected.text
    with state.production._connect() as conn:
        assert tuple(conn.iterdump()) == before
    assert state.provider.output_cache_keys == calls
    assert {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()} == outputs
    changed = state.client.post(base + "/edits/preview", json=command)
    assert changed.status_code == 200, changed.text
    assert "reuse_person_render_id" not in changed.json()["proposal"]["operation"]["payload"]


def test_person_lineage_requires_matching_language_and_success(person_render_edit_state):
    state = person_render_edit_state
    segment = state.production.narration_records.get_narration_segment(state.workspace_id, state.task.id,
                                                      state.montage.cues[0].narration_segment_id)
    render = replace(state.old_render, artifact_ref='artifacts/test/person.mp4')
    transform = SimpleNamespace(transform_type='person_render', status='succeeded',
        input_digest=render.cache_key, output_refs=[render.artifact_ref],
        input_refs=[segment.audio_reference], parameters={'language': segment.language})
    assert not main_module._person_render_matches_narration(render, segment, [transform])
    audio = state.production.narration_artifacts.resolve_authorized_narration_audio_reference(state.workspace_id, segment.audio_reference)
    person = state.production.get_person_profile(state.workspace_id, render.person_profile_id, render.person_version)
    visual = state.production.resolve_workspace_media_reference(
        state.workspace_id, person.avatar_reference, require_registered=True)
    facts = main_module.presenter_input_facts(SimpleNamespace(audio_path=str(audio), visual_path=str(visual)))
    transform.parameters['presenter_input_facts'] = facts
    assert main_module._person_render_matches_narration(render, segment, [transform])
    transform.parameters = {'language': 'en', 'presenter_input_facts': facts}
    assert not main_module._person_render_matches_narration(render, segment, [transform])
    transform.parameters = {'language': segment.language, 'presenter_input_facts': facts}
    transform.status = 'failed'
    assert not main_module._person_render_matches_narration(render, segment, [transform])
    transform.status = 'succeeded'
    voice = state.production.get_versioned_voice_profile(state.workspace_id, segment.voice_profile_id,
                                                          segment.voice_version)
    state.production.save_authorization(replace(voice.authorization, version='v2', status='revoked'))
    assert not main_module._person_render_matches_narration(render, segment, [transform])
    state.production.save_authorization(replace(voice.authorization, version='v3'))
    audio.unlink()
    assert not main_module._person_render_matches_narration(render, segment, [transform])


@pytest.mark.parametrize('start', [0,1])
def test_person_replacement_maps_narration_instead_of_broll_source(permitted_person_edit_state, start):
    state=permitted_person_edit_state
    target=state.montage.clips[0]
    target.person_render_id=None
    target.role='b_roll'
    target.source_start_seconds=4+start
    target.source_end_seconds=8
    target.timeline_start_seconds=start
    if start:
        state.montage.clips.insert(0,replace(target,id='earlier-split',
            source_start_seconds=4,source_end_seconds=5,timeline_start_seconds=0,timeline_end_seconds=1))
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    base=f'/api/creation-tasks/{state.task.id}'
    state.client.get(base).json()
    preview=state.client.post(base+'/edits/preview',json={
        'raw_command':'这一段换成当前人物出镜','context_stage':'review',
        'selected_object_refs':['clip:clip-person-target']})
    assert preview.status_code==200,preview.text
    proposal=preview.json()['proposal']
    assert proposal['operation']['payload']['source_start_seconds']==start
    assert proposal['operation']['payload']['source_end_seconds']==4
    _assert_unpermitted_person_confirm(state, proposal)
    with state.granted() as context:
        generated = main_module._generate_creation_task_person_render_record(
            state.workspace_id, state.task.id, main_module.CreationTaskPersonRenderRequest(
                narration_segment_id=state.montage.cues[0].narration_segment_id,
                person_profile_id=state.old_render.person_profile_id,
                person_version=state.old_render.person_version), grant_context=context)
    reuse=state.client.post(base+'/edits/preview',json={
        'raw_command':'这一段换成当前人物出镜','context_stage':'review',
        'selected_object_refs':['clip:clip-person-target']})
    assert reuse.status_code==200,reuse.text
    proposal=reuse.json()['proposal']
    assert proposal['operation']['payload']['reuse_person_render_id']==generated.id
    confirmed=state.client.post(base+f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code==200,confirmed.text
    after=state.client.get(base).json()
    clip=next(c for c in after['montage']['clips'] if c['id']==target.id)
    assert (clip['source_start_seconds'],clip['source_end_seconds'])==(start,4)
    assert (clip['timeline_start_seconds'],clip['timeline_end_seconds'])==(start,4)
    stored=state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id)
    assert creation_task_montage_plan_from_payload(stored).narration_audio_path==state.montage.narration_audio_path
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    clip=next(c for c in restored['montage']['clips'] if c['id']==target.id)
    assert (clip['source_start_seconds'],clip['source_end_seconds'])==(4+start,8)
    assert clip['person_render_id'] is None


def test_person_preview_respects_locked_clip_before_generation(person_render_edit_state):
    state=person_render_edit_state
    state.montage.clips[0].locked=True
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    response=state.client.post(f'/api/creation-tasks/{state.task.id}/edits/preview',json={
        'raw_command':'这里只重做口型','context_stage':'review',
        'selected_object_refs':['clip:clip-person-target']})
    assert response.status_code==409,response.text
    assert '锁定' in response.text
    assert state.provider.output_cache_keys==[]
    assert state.production.edit_records.list_edit_proposals(state.workspace_id,state.task.id)==[]


def _save_fixture_narration_duration(state, duration_seconds):
    """Match the persisted source fact to the media authored by this test."""
    reference = state.montage.narration_audio_path
    record = state.production.narration_artifacts.get_narration_artifact(
        state.workspace_id, reference.removeprefix("narration://artifact/"))
    import wave
    with wave.open(record.audio_path) as audio:
        sample_rate = audio.getframerate()
    state.production.narration_artifacts.save_narration_artifact(replace(record, duration_seconds=duration_seconds, sample_rate=sample_rate))


def test_audio_draft_uses_registered_duration_without_probe(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    service = main_module._task_audio()
    timeline = main_module._effective_audio_timeline(state.montage)
    monkeypatch.setattr("app.task_audio.probe_audio_source", lambda *_: pytest.fail("draft must not probe media"))
    with pytest.raises(ValueError, match="音频裁剪区间超出"):
        service.validate(state.workspace_id, state.task.id, timeline, 8, draft_only=True)
    _save_fixture_narration_duration(state, 8)
    service.validate(state.workspace_id, state.task.id, timeline, 8, draft_only=True)
    _save_fixture_narration_duration(state, 4)
    with pytest.raises(ValueError, match="音频裁剪区间超出"):
        service.validate(state.workspace_id, state.task.id, timeline, 8, draft_only=True)


@pytest.mark.parametrize("source_end", [4, 6])
def test_extract_original_audio_real_media_preview_confirm_undo(permitted_media_state, source_end):
    import subprocess
    import io
    import wave
    import array
    state=permitted_media_state
    clip=state.montage.clips[0]
    clip.asset_id=state.original_person_clip.asset_id
    clip.source_path=state.original_person_clip.source_path
    clip.person_render_id=None
    clip.role='b_roll'
    clip.source_start_seconds=2
    clip.source_end_seconds=source_end
    source=state.production.resolve_clip_media_reference(state.workspace_id,clip.asset_id,clip.source_path)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:size=32x32:rate=30:duration=8',
        '-f','lavfi','-i','aevalsrc=if(lt(t\\,2)\\,0\\,0.2*sin(2*PI*660*t)):s=48000:d=8',
        '-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True)
    asset=state.production.get_asset(state.workspace_id,clip.asset_id)
    asset.duration_seconds=8
    asset.size_bytes=source.stat().st_size
    asset.modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc)
    state.production._save_asset(asset)
    narration=state.production.narration_artifacts.resolve_authorized_narration_audio_reference(state.workspace_id,state.montage.narration_audio_path)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','8',str(narration)],check=True)
    _save_fixture_narration_duration(state, 8)
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    base=f'/api/creation-tasks/{state.task.id}'
    command={'raw_command':'提取当前镜头原声','context_stage':'montage','selected_object_refs':[f'clip:{clip.id}']}
    preview=state.client.post(base+'/edits/preview',json=command)
    assert preview.status_code==200,preview.text
    proposal=preview.json()['proposal']
    assert '混合' in proposal['summary']
    response=state.client.get(proposal['preview']['after']['audio_url'])
    assert response.status_code==200
    with wave.open(io.BytesIO(response.content)) as audio:
        audio.setpos(round(.25*audio.getframerate()))
        values=array.array('h',audio.readframes(round(.5*audio.getframerate())))
        assert max(abs(v) for v in values)>1000  # cropped first 2 seconds of silence away
    before=state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id)
    assert creation_task_montage_plan_from_payload(before).audio_timeline is None
    confirmed=state.client.post(base+f"/edits/{proposal['id']}/confirm")
    assert confirmed.status_code==200,confirmed.text
    updated=creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id))
    original=next(t for t in updated.audio_timeline.tracks if t.role=='original')
    assert len(original.clips)==1
    assert (original.clips[0].timeline_start_ms,original.clips[0].source_end_ms)==(0,4000)
    assert updated.narration_audio_path==state.montage.narration_audio_path
    duplicate=state.client.post(base+'/edits/preview',json=command)
    assert duplicate.status_code==409 and '已有原声' in duplicate.text
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id,state.task.id))
    assert not any(t.clips for t in main_module._effective_audio_timeline(restored).tracks if t.role=='original')



def test_extract_original_audio_rejects_silent_video_without_timeline_change(permitted_media_state):
    import subprocess
    state=permitted_media_state
    clip=state.montage.clips[0]
    clip.asset_id=state.original_person_clip.asset_id
    clip.source_path=state.original_person_clip.source_path
    clip.person_render_id=None
    source=state.production.resolve_clip_media_reference(state.workspace_id,clip.asset_id,clip.source_path)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:size=32x32:rate=30:duration=8',
        '-an','-c:v','libx264',str(source)],check=True)
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    base=f'/api/creation-tasks/{state.task.id}'
    before=state.client.get(base).json()
    response=state.client.request('POST',base+'/edits/preview',json={'raw_command':'提取原声',
        'context_stage':'montage','selected_object_refs':[f'clip:{clip.id}']})
    assert response.status_code==202
    assert state.preview_runtime.run_once()
    assert state.production.job_records.get_job_run(state.workspace_id,response.json()['job_id']).status=='failed'
    assert state.client.get(base).json()['montage']==before['montage']
    assert state.production.edit_records.list_edit_proposals(state.workspace_id,state.task.id)==[]



@pytest.mark.parametrize('person,locked', [(True,False),(False,True)])
def test_extract_original_audio_rejects_generated_or_locked_clip(person_render_edit_state,person,locked):
    state=person_render_edit_state
    clip=state.montage.clips[0]
    if not person:
        clip.person_render_id=None
    clip.locked=locked
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    response=state.client.post(f'/api/creation-tasks/{state.task.id}/edits/preview',json={
        'raw_command':'保留这个镜头的原声','context_stage':'review','selected_object_refs':[f'clip:{clip.id}']})
    assert response.status_code==409,response.text
    assert ('生成口型' if person else '锁定') in response.text
    assert state.production.edit_records.list_edit_proposals(state.workspace_id,state.task.id)==[]


def test_silence_edit_real_preview_confirm_and_undo(permitted_media_state, granted_media):
    import subprocess
    state=permitted_media_state
    clip=state.montage.clips[0]
    clip.asset_id=state.original_person_clip.asset_id
    clip.source_path=state.original_person_clip.source_path
    clip.person_render_id=None
    clip.role='b_roll'
    source=state.production.resolve_clip_media_reference(state.workspace_id,clip.asset_id,clip.source_path)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:size=64x64:rate=30:duration=8',
        '-an','-c:v','libx264',str(source)],check=True)
    asset=state.production.get_asset(state.workspace_id,clip.asset_id)
    asset.duration_seconds=8
    asset.size_bytes=source.stat().st_size
    asset.modified_at=datetime.fromtimestamp(source.stat().st_mtime,timezone.utc)
    state.production._save_asset(asset)
    narration=state.production.narration_artifacts.resolve_authorized_narration_audio_reference(state.workspace_id,state.montage.narration_audio_path)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'aevalsrc=if(between(t\\,1\\,2)\\,0\\,0.2*sin(2*PI*440*t)):s=48000:d=8',str(narration)],check=True)
    _save_fixture_narration_duration(state, 8)
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    base=f'/api/creation-tasks/{state.task.id}'
    response=state.client.post(base+'/edits/preview',json={'raw_command':'剪掉当前镜头停顿',
        'context_stage':'montage','selected_object_refs':[f'clip:{clip.id}']})
    assert response.status_code==200,response.text
    proposal=response.json()['proposal']
    assert proposal['operation']['operation_type']=='RemoveSilence'
    cuts=proposal['operation']['payload']['removed_ranges_ms']
    assert len(cuts)==1
    for side in ('before','after'):
        media=state.client.get(proposal['preview'][side]['video_url'])
        assert media.status_code==200 and len(media.content)>1000
        if side=='after':
            preview_after=source.parent/'silence-preview.mp4'
            preview_after.write_bytes(media.content)
    before=state.client.get(base).json()
    assert before['montage']['duration_seconds']==8
    result=state.client.post(base+f"/edits/{proposal['id']}/confirm")
    assert result.status_code==200,result.text
    after=state.client.get(base).json()
    expected=8-(cuts[0][1]-cuts[0][0])/1000
    assert after['montage']['duration_seconds']==expected
    assert len(after['montage']['clips'])==3
    assert after['subtitle_tracks']==before['subtitle_tracks']==[]
    _assert_retired_montage_endpoint(state.client, state.production, state.workspace_id, state.task.id, "render")
    # Preserve this pre-existing real-media diagnostic, never restore the public fallback.
    with granted_media([source,narration],kind="creation_render") as context:
        rendered=main_module._render_creation_task_montage_for_workspace(state.workspace_id,state.task.id,render_context=context)
    assert rendered.status=="rendered"
    media=state.client.get(base+'/output')
    assert media.status_code==200 and len(media.content)>1000
    output=source.parent/'silence-result.mp4'
    output.write_bytes(media.content)
    import json
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
        '-show_entries','stream=nb_read_frames','-of','json',str(output)],capture_output=True,text=True,check=True).stdout)
    assert int(probe['streams'][0]['nb_read_frames'])==round(expected*30)
    def pixels(path):
        return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vf','scale=12:20:flags=area',
            '-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True).stdout
    assert pixels(preview_after)==pixels(output)
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    assert restored['montage']['duration_seconds']==8
    assert restored['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['duration_seconds']==expected
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200



@pytest.mark.parametrize('command,expected', [('这句字幕提前0.2秒', (.8,2.8)), ('当前字幕延后300毫秒', (1.3,3.3))])
def test_relative_subtitle_shift_previews_confirms_and_undoes(person_render_edit_state, command, expected):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    track=SubtitleTrack(id='shift-zh',workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language='zh-CN',source_language=None,style={},
        cues=[ProductionSubtitleCue(id='shift-cue',source_cue_id=None,text='移动字幕',start_seconds=1,end_seconds=3)],
        generation_method='timeline',model_id=None,artifact_ref='artifacts/shift.srt',transform_id='shift-fixture',status='valid')
    state.production.subtitle_records.create_subtitle_track(track)
    before=state.client.get(base).json()
    response=state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'subtitle_cover',
        'selected_object_refs':['cue:shift-cue','subtitle-track:shift-zh']})
    assert response.status_code==200,response.text
    assert state.client.get(base).json()['subtitle_tracks']==before['subtitle_tracks']
    confirmed=state.client.post(base+'/edits/'+response.json()['proposal']['id']+'/confirm')
    assert confirmed.status_code==200,confirmed.text
    after=state.client.get(base).json()
    cue=after['subtitle_tracks'][0]['cues'][0]
    assert (cue['start_seconds'],cue['end_seconds'])==expected
    assert cue['text']=='移动字幕'
    assert {k:v for k,v in after['narration'].items() if k != 'task'} == {k:v for k,v in before['narration'].items() if k != 'task'}
    assert after['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    assert restored['subtitle_tracks'][0]['cues']==before['subtitle_tracks'][0]['cues']
    current_track=restored['subtitle_tracks'][0]
    for invalid in ['这句字幕提前2秒','这句字幕延后99秒']:
        rejected=state.client.post(base+'/edits/preview',json={'raw_command':invalid,'context_stage':'subtitle_cover',
            'selected_object_refs':[f"cue:{current_track['cues'][0]['id']}",f"subtitle-track:{current_track['id']}"]})
        assert rejected.status_code==422,rejected.text
    assert state.client.get(base).json()['task']['current_timeline_version_id']==restored['task']['current_timeline_version_id']
    state.production.subtitle_records.create_subtitle_track(replace(track,id='shift-en',language='en-US',source_language='zh-CN',
        timeline_version_id=restored['task']['current_timeline_version_id'],generation_method='translation'))
    ambiguous=state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'subtitle_cover',
        'selected_object_refs':['cue:shift-cue']})
    assert ambiguous.status_code==409,ambiguous.text
    assert '语言轨' in ambiguous.text


def test_relative_subtitle_shift_in_compound_selection_and_undo(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    zh=SubtitleTrack(id='shift-compound-zh',workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language='zh-CN',source_language=None,style={},
        cues=[ProductionSubtitleCue(id='shift-source',source_cue_id=None,text='本地视频',start_seconds=1,end_seconds=3)],
        generation_method='timeline',model_id=None,artifact_ref='artifacts/source.srt',transform_id='source',status='valid')
    state.production.subtitle_records.create_subtitle_track(zh)
    state.production.subtitle_records.create_subtitle_track(replace(zh,id='shift-compound-en',language='en-US',source_language='zh-CN',
        cues=[replace(zh.cues[0],id='shift-translation',source_cue_id=zh.cues[0].id,text='Local video')],generation_method='translation'))
    before=state.client.get(base).json()
    clip=before['montage']['clips'][0]
    source_service = main_module._task_audio()
    monkeypatch.setattr(source_service, 'validate', lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, '_task_audio', lambda: source_service)
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',lambda *args,**kwargs:SimpleNamespace(id='shift-preview',logical_ref='artifacts/shift-preview.mp4'))
    def preview(command):return state.client.post(base+'/edits/preview',json={'raw_command':command,'context_stage':'montage','selected_object_refs':[f"clip:{clip['id']}"]})
    for invalid in ['当前镜头亮一点，同时字幕1提前0.2秒','当前镜头亮一点，同时英文字幕1提前2秒']:
        response=preview(invalid)
        assert response.status_code==422,response.text
        assert state.client.get(base).json()['task']['current_timeline_version_id']==before['task']['current_timeline_version_id']
    response=preview('当前镜头亮一点，同时英文字幕1提前0.2秒，然后英文字幕1延后100毫秒')
    assert response.status_code==200,response.text
    assert state.client.get(base).json()['subtitle_tracks']==before['subtitle_tracks']
    proposal=response.json()['proposal']
    selected=state.client.post(base+'/edits/'+proposal['id']+'/selection/preview',json={'included_step_indices':[0,2]})
    assert selected.status_code==200,selected.text
    assert state.client.post(base+'/edits/'+selected.json()['proposal']['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    english=next(t for t in after['subtitle_tracks'] if t['language']=='en-US')
    assert (english['cues'][0]['start_seconds'],english['cues'][0]['end_seconds'])==(1.1,3.1)
    chinese=next(t for t in after['subtitle_tracks'] if t['language']=='zh-CN')
    assert chinese['cues']==next(t['cues'] for t in before['subtitle_tracks'] if t['language']=='zh-CN')
    assert after['montage']['clips'][0]['visual_effects']!=before['montage']['clips'][0]['visual_effects']
    assert after['narration']['audio_reference']==before['narration']['audio_reference']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    assert [t['cues'] for t in restored['subtitle_tracks']]==[t['cues'] for t in before['subtitle_tracks']]
    assert restored['montage']['clips']==before['montage']['clips']


@pytest.mark.parametrize('stage,command,expected',[
    ('montage','英文字幕2延后300毫秒',(4.3,5.3,'Second cue')),
    ('subtitle_cover','英文字幕 2 延后 300 毫秒',(4.3,5.3,'Second cue')),
    ('review','英文字幕2改成“Keep spaces, and punctuation.”',(4,5,'Keep spaces, and punctuation.')),
])
def test_indexed_subtitle_without_selection_previews_confirms_and_undoes(person_render_edit_state,monkeypatch,stage,command,expected):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    version=main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    zh=SubtitleTrack(id='indexed-zh',workspace_id=state.workspace_id,task_id=state.task.id,timeline_version_id=version.id,
        language='zh-CN',source_language=None,style={},
        cues=[ProductionSubtitleCue(id='indexed-first',source_cue_id=None,text='第一句',start_seconds=1,end_seconds=3),
              ProductionSubtitleCue(id='indexed-second',source_cue_id=None,text='第二句',start_seconds=4,end_seconds=5)],
        generation_method='timeline',model_id=None,artifact_ref='artifacts/source.srt',transform_id='source',status='valid')
    state.production.subtitle_records.create_subtitle_track(zh)
    state.production.subtitle_records.create_subtitle_track(replace(zh,id='indexed-en',language='en-US',source_language='zh-CN',
        cues=[replace(cue,id='en-'+cue.id,source_cue_id=cue.id,text=text) for cue,text in zip(zh.cues,['First cue','Second cue'])],generation_method='translation'))
    before=state.client.get(base).json()
    monkeypatch.setattr(main_module,'_task_audio',lambda:SimpleNamespace(validate=lambda *args, **kwargs:None))
    previews=[]
    def publish(*args,**kwargs):
        previews.append(args[4])
        return SimpleNamespace(id='indexed-preview',logical_ref='artifacts/indexed-preview.mp4')
    monkeypatch.setattr(main_module,'_publish_task_picture_preview',publish)
    def preview(raw):return state.client.post(base+'/edits/preview',json={'raw_command':raw,'context_stage':stage,'selected_object_refs':[]})
    for invalid in ['字幕2延后300毫秒','英文字幕99延后300毫秒','英文字幕2提前9秒','英文字幕2提前2秒','英文字幕2延后300毫秒，字幕不变']:
        rejected=preview(invalid)
        assert rejected.status_code==422,rejected.text
        assert state.client.get(base).json()['task']['current_timeline_version_id']==before['task']['current_timeline_version_id']
    response=preview(command)
    assert response.status_code==200,response.text
    assert len(previews)==2
    assert state.client.get(base).json()['subtitle_tracks']==before['subtitle_tracks']
    preview_cue=next(t for t in previews[-1] if t.language=='en-US').cues[1]
    assert (preview_cue.start_seconds,preview_cue.end_seconds,preview_cue.text)==expected
    assert state.client.post(base+'/edits/'+response.json()['proposal']['id']+'/confirm').status_code==200
    after=state.client.get(base).json()
    english=next(t for t in after['subtitle_tracks'] if t['language']=='en-US')
    cue=english['cues'][1]
    assert (cue['start_seconds'],cue['end_seconds'],cue['text'])==expected
    assert after['montage']['clips']==before['montage']['clips']
    assert after['narration']['audio_reference']==before['narration']['audio_reference']
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    restored=state.client.get(base).json()
    for language in ['zh-CN','en-US']:
        assert next(t['cues'] for t in restored['subtitle_tracks'] if t['language']==language)==next(t['cues'] for t in before['subtitle_tracks'] if t['language']==language)


@pytest.mark.parametrize('mode,notice',[('unavailable','未就绪'),('failed','计算失败'),('invalid_count','数量不符'),('missing_index','该片段缺少'),('ready',None)])
def test_creation_task_explains_visual_retrieval_degradation(tmp_path,mode,notice):
    from app.visual_semantics import VisualSemanticError
    script=CreationTaskScript(id='diagnostic-script',workspace_id='workspace_a',task_id='diagnostic-task',run_id='run',topic_id='topic',
        title='Retrieval diagnostic',language='en-US',selection_status='confirmed',sections=[CreationTaskScriptSection(
            id='section',title='Intro',content='This is a narration.',visual_prompt='An underwater coral reef',evidence_reference_ids=[])])
    asset=_asset(tmp_path)
    segment=replace(asset.segments[0],description='A computer keyboard',transcript='',end_seconds=20)
    def embed(texts):
        if mode=='failed':
            raise VisualSemanticError('worker failed')
        return [] if mode=='invalid_count' else [[1.0,0.0] for _ in texts]
    provider=SimpleNamespace(ready=mode!='unavailable',model_id='test-local',embed_texts=embed)
    planner=MontagePlanner(semantic_provider=provider,visual_feature_loader=lambda *_:None if mode=='missing_index' else [1.0,0.0])
    plan=planner.build_for_creation_task(duration_target_seconds=90, workspace_id='workspace_a',task_id='diagnostic-task',script=script,
        profile=ProductionProfile(workspace_id='workspace_a'),assets=[replace(asset,segments=[segment])],
        media_reference_for_asset=lambda _:'workspace://source.mp4',narration_audio_path='narration://artifact/test',narration_duration_seconds=4)
    assert plan.clips[0].needs_confirmation is True
    reasons=plan.clips[0].match_reasons
    if notice:
        assert any(notice in reason and '仅按素材文字与时长推荐' in reason for reason in reasons)
    else:
        assert not any('仅按素材文字与时长推荐' in reason for reason in reasons)


@pytest.mark.parametrize('rights,brand',[(False,False),(False,True),(True,False)])
def test_confirmed_user_material_does_not_require_legacy_rights_flags(tmp_path,rights,brand):
    script=CreationTaskScript(id='user-material-script',workspace_id='workspace_a',task_id='user-material-task',run_id='run',topic_id='topic',
        title='User material',language='zh-CN',selection_status='confirmed',sections=[CreationTaskScriptSection(
            id='section',title='演示',content='演示电脑操作。',visual_prompt='电脑打字画面',evidence_reference_ids=[])])
    asset=_asset(tmp_path)
    asset=replace(asset,segments=[replace(asset.segments[0],end_seconds=20)])
    asset=ProductionRepository._asset_from_payload({**asdict(asset),"rights_confirmed":rights,"brand_safe":brand})
    plan=MontagePlanner().build_for_creation_task(duration_target_seconds=90, workspace_id='workspace_a',task_id=script.task_id,script=script,
        profile=ProductionProfile(workspace_id='workspace_a'),assets=[asset],
        media_reference_for_asset=lambda _:'workspace://source.mp4',narration_audio_path='narration://artifact/test',narration_duration_seconds=4)
    assert plan.clips[0].asset_id==asset.id
    # Removing obsolete proof flags must not remove explicit exclusion or review.
    for excluded in [replace(asset,review_status='rejected'),replace(asset,usage_role='exclude',segments=[replace(asset.segments[0],usage_role='exclude')])]:
        with pytest.raises(ValueError,match='可用素材库'):
            MontagePlanner().build_for_creation_task(duration_target_seconds=90, workspace_id='workspace_a',task_id=script.task_id,script=script,
                profile=ProductionProfile(workspace_id='workspace_a'),assets=[excluded],
                media_reference_for_asset=lambda _:'workspace://source.mp4',narration_audio_path='narration://artifact/test',narration_duration_seconds=4)


def test_material_use_state_api_persists_and_rejects_obsolete_writer(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    monkeypatch.setattr(main_module.settings,'video_inbox_path',str(state.production.video_library_path),raising=False)
    asset=state.production.list_assets(state.workspace_id)[0]
    base=f'/api/production/materials/{asset.id}'
    response=state.client.put(base+'/use-state',json={'use_state':'reference_only'})
    assert response.status_code==200,response.text
    assert response.json()['use_state']=='reference_only'
    assert 'rights_confirmed' not in response.json() and 'brand_safe' not in response.json()
    assert state.production.get_asset(state.workspace_id,asset.id).use_state=='reference_only'
    state.montage.clips[0].person_render_id=None
    state.production.montage_plan_adoption.save_creation_task_montage_plan(state.workspace_id,state.task.id,state.montage.id,
        creation_task_montage_plan_to_payload(state.montage))
    _assert_retired_montage_endpoint(state.client, state.production, state.workspace_id, state.task.id, "render")
    # The internal diagnostic still enforces the material's authoritative use state.
    with pytest.raises(main_module.HTTPException) as blocked:
        main_module._render_creation_task_montage_for_workspace(state.workspace_id, state.task.id)
    assert blocked.value.status_code == 409
    assert '使用状态' in str(blocked.value.detail)
    assert state.client.put(base+'/use-state',json={'use_state':'unknown'}).status_code==422
    assert state.client.put(base+'/review',json={'review_status':'approved','usage_role':'b_roll','rights_confirmed':True}).status_code==422
    assert state.client.put(base+'/use-state',json={'use_state':'available'}).status_code==200


def test_crop_dialogue_reuses_the_exact_chain_with_preview_confirm_undo_lock_and_expiry(person_render_edit_state,monkeypatch):
    state=person_render_edit_state
    base=f'/api/creation-tasks/{state.task.id}'
    main_module._ensure_montage_base_version(state.workspace_id,state.task,state.montage)
    before=state.client.get(base).json()
    target=before['montage']['clips'][1]
    original_fit,original_y=target['fit_mode'],target['crop_y']
    publishes=[]
    runtime=_granted_domain_preview_adapter(state,monkeypatch,lambda inputs:publishes.append(inputs))
    # 明确单轴指令：画面预览不写时轴、不派生声音、不动另一轴和原 fit_mode
    preview=state.client.post(base+'/edits/preview',json={'raw_command':'水平裁剪到 20%','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert preview.status_code==200,preview.text
    proposal=preview.json()['proposal']
    assert proposal['operation']['operation_type']=='ChangeCrop'
    assert proposal['operation']['payload']['crop_x']==.2
    assert proposal['operation']['payload']['crop_y']==original_y
    assert proposal['operation']['payload']['fit_mode']==original_fit
    assert '水平 20%' in proposal['preview']['after']['detail']
    assert len(publishes)==2
    assert [item["clip"]["crop_x"] for item in publishes]==[before["montage"]["clips"][1]["crop_x"],.2]
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']

    confirmed=state.client.post(base+'/edits/'+proposal['id']+'/confirm')
    assert confirmed.status_code==200,confirmed.text
    cropped=state.client.get(base).json()
    assert cropped['montage']['clips'][1]['crop_x']==.2
    assert cropped['montage']['clips'][1]['crop_y']==original_y
    assert cropped['montage']['clips'][1]['fit_mode']==original_fit
    assert cropped['montage']['clips'][0]==before['montage']['clips'][0]
    assert cropped['montage']['clips'][2:]==before['montage']['clips'][2:]
    assert cropped['montage']['audio_timeline']==before['montage']['audio_timeline']

    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==before['montage']['clips']
    assert state.client.post(base+'/timeline/redo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['clips']==cropped['montage']['clips']

    # 只说模式：沿用当前两轴，不把已裁好的位置重置
    mode=state.client.post(base+'/edits/preview',json={'raw_command':'完整显示','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert mode.status_code==200,mode.text
    mode_proposal=mode.json()['proposal']
    assert mode_proposal['operation']['payload']=={**mode_proposal['operation']['payload'],'fit_mode':'fit','crop_x':.2,'crop_y':original_y}
    assert state.client.post(base+'/edits/'+mode_proposal['id']+'/confirm').status_code==200
    assert state.client.get(base).json()['montage']['clips'][1]['fit_mode']=='fit'

    # 含糊数量不猜步长：返回需补参数的提示，不产生任何修改
    vague=state.client.post(base+'/edits/preview',json={'raw_command':'裁剪一下','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert vague.status_code==422,vague.text
    assert '缺少可执行参数' in vague.json()['detail']

    # 改画幅沿当前持久 Job/grant 回读，不被编成单轴裁剪。
    wide=state.client.post(base+'/edits/preview',json={'raw_command':'改成横版','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert wide.status_code==200,wide.text
    assert wide.json()['proposal']['operation']['operation_type']=='ChangeCanvas',wide.text
    assert state.client.post(base+'/edits/'+wide.json()['proposal']['id']+'/confirm').status_code==200
    assert state.client.get(base).json()['montage']['aspect_ratio']=='16:9'

    canvas=state.client.post(base+'/edits/preview',json={'raw_command':'改成竖版','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert canvas.status_code==200,canvas.text
    assert canvas.json()['proposal']['operation']['operation_type']=='ChangeCanvas',canvas.text
    assert canvas.json()['proposal']['operation']['payload']['aspect_ratio']=='9:16'
    # 画幅预览同样不写库
    assert state.client.get(base).json()['montage']['aspect_ratio']=='16:9'
    assert state.client.post(base+'/timeline/undo',json={}).status_code==200
    assert state.client.get(base).json()['montage']['aspect_ratio']=='9:16'

    assert len(publishes)==8
    publishes.clear()

    # 时间线在预览后变化：旧提案确认被拒
    stale=state.client.post(base+'/edits/preview',json={'raw_command':'水平裁剪到 40%','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert stale.status_code==200,stale.text
    stale_proposal=stale.json()['proposal']
    assert state.client.post(base+'/edits/exact',json={'operation_type':'SetClipLock',
        'clip_id':before['montage']['clips'][0]['id'],'locked':True}).status_code==200
    expired=state.client.post(base+'/edits/'+stale_proposal['id']+'/confirm')
    assert expired.status_code==409,expired.text
    assert state.client.get(base).json()['montage']['clips'][1]['crop_x']==.2

    # 锁定镜头仍要求先解锁
    assert state.client.post(base+'/edits/exact',json={'operation_type':'SetClipLock',
        'clip_id':target['id'],'locked':True}).status_code==200
    publishes.clear()
    locked=state.client.post(base+'/edits/preview',json={'raw_command':'水平裁剪到 30%','context_stage':'montage',
        'selected_object_refs':[f"clip:{target['id']}"]})
    assert locked.status_code==409,locked.text
    assert '请先解锁' in locked.json()['detail']
    assert publishes==[]
    assert runtime.stop(1)


def test_v3_exact_draft_persists_and_restores_only_registered_output(person_render_edit_state, monkeypatch, tmp_path):
    from app.creation.contracts import ArtifactRecord, JobRun, TimelineVersion, TransformRun
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    # Seal this fixture's existing person clip with the current, authorized
    # lineage path; no portrait renderer or media decoding is invoked.
    person_segment = state.production.narration_records.get_narration_segment(
        state.workspace_id, state.task.id, state.montage.cues[0].narration_segment_id,
    )
    person_profile = state.production.get_person_profile(
        state.workspace_id, state.old_render.person_profile_id, state.old_render.person_version,
    )
    person_audio = state.production.narration_artifacts.resolve_authorized_narration_audio_reference(
        state.workspace_id, person_segment.audio_reference,
    )
    person_visual = state.production.resolve_workspace_media_reference(
        state.workspace_id, person_profile.avatar_reference, require_registered=True,
    )
    person_input_facts = main_module.presenter_input_facts(
        SimpleNamespace(audio_path=str(person_audio), visual_path=str(person_visual)),
    )
    person_input_refs = list(dict.fromkeys([
        f"person-profile://{person_profile.id}/{person_profile.version}",
        f"voice-profile://{person_segment.voice_profile_id}/{person_segment.voice_version}",
        f"narration-segment://{person_segment.id}", person_segment.audio_reference,
        person_profile.avatar_reference,
    ]))
    person_artifact_ref = f"artifacts/{state.task.id}/person-lineage/person-render.mp4"
    published_person = main_module.artifact_store.publish_file(
        person_artifact_ref, Path(state.old_render.output_path),
    )
    person_job = state.production.job_records.create_job_run(JobRun(
        id="job_v3_person_lineage", workspace_id=state.workspace_id, task_id=state.task.id,
        kind="person_render", idempotency_key="v3-person-lineage", input_digest=state.old_render.cache_key,
    ))
    state.production.job_records.claim_job_run(state.workspace_id, person_job.id)
    state.production.execution_records.create_artifact_record(ArtifactRecord(
        id="artifact_v3_person_lineage", workspace_id=state.workspace_id, task_id=state.task.id,
        kind="person_render", logical_ref=person_artifact_ref, sha256=published_person.sha256,
        size_bytes=published_person.size_bytes, producer_job_id=person_job.id, input_refs=person_input_refs,
    ))
    state.production.job_records.finish_job_run(state.workspace_id, person_job.id, status="succeeded", output_refs=[person_artifact_ref])
    state.production.person_renders.bind_person_render_artifact(state.workspace_id, state.task.id, state.old_render.id, person_artifact_ref)
    state.old_render = state.production.person_renders.get_person_render(state.workspace_id, state.task.id, state.old_render.id)
    state.production.lineage_records.create_transform_run(TransformRun(
        id="transform_job_v3_person_lineage", workspace_id=state.workspace_id, task_id=state.task.id,
        job_id=person_job.id, transform_type="person_render", input_digest=state.old_render.cache_key,
        input_refs=person_input_refs, output_refs=[person_artifact_ref], engine=state.old_render.model_id,
        parameters={"motion": state.old_render.motion, "expression": state.old_render.expression,
                    "language": person_segment.language, "presenter_input_facts": person_input_facts},
        status="succeeded", started_at=main_module.utc_now(), finished_at=main_module.utc_now(),
    ))
    # Establish a real versioned input before registering the synthetic output.
    input_edit = state.client.post(base + "/edits/exact", json={"operation_type":"ChangeCanvas", "aspect_ratio":"16:9"})
    assert input_edit.status_code == 200, input_edit.text
    input_task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    input_version = state.production.timeline_records.get_timeline_version(state.workspace_id, input_task.current_timeline_version_id)
    input_plan = creation_task_montage_plan_from_payload(input_version.snapshot["montage"])
    input_refs = main_module._creation_task_montage_render_input_refs(input_task, input_plan)
    input_subtitle_tracks = main_module._current_subtitle_tracks(
        state.workspace_id, input_task, current_version=input_version, montage=input_plan,
    )
    explicit_subtitles = "subtitle_track_ids" in input_version.snapshot
    subtitles_visible = (
        any(track.status == "valid" and track.style.get("enabled", True) for track in input_subtitle_tracks)
        if input_subtitle_tracks or explicit_subtitles else bool(input_plan.cues)
    )
    sealed_subtitle_tracks = [
        {"language": track.language, "style": track.style, "cues": [asdict(cue) for cue in track.cues]}
        for track in input_subtitle_tracks if track.status == "valid"
    ]
    render_input_payload = main_module._creation_task_montage_render_input_payload(
        input_plan, subtitles_visible=subtitles_visible, subtitle_tracks=sealed_subtitle_tracks,
        timeline_version_id=input_version.id,
        bind_timeline_version=True,
    )
    render_input_digest = main_module._creation_task_montage_render_input_digest(render_input_payload)
    # Synthetic registered bytes prove version/hash behavior, not rendered media.
    source = tmp_path / "synthetic-output.bin"
    source.write_bytes(b"synthetic-registered-output")
    published = main_module.artifact_store.publish_file(state.montage.output_path, source)
    job = state.production.job_records.create_job_run(JobRun(id="job_v3_fixture", workspace_id=state.workspace_id,
        task_id=state.task.id, kind="creation_task_montage_render", idempotency_key="v3-fixture",
        input_digest=render_input_digest))
    state.production.job_records.claim_job_run(state.workspace_id, job.id)
    state.production.execution_records.create_artifact_record(ArtifactRecord(id="artifact_v3_fixture", workspace_id=state.workspace_id,
        task_id=state.task.id, kind="rendered_video", logical_ref=published.logical_ref,
        sha256=published.sha256, size_bytes=published.size_bytes, producer_job_id=job.id, input_refs=input_refs))
    state.production.job_records.finish_job_run(state.workspace_id, job.id, status="succeeded", output_refs=[published.logical_ref])
    transform_id = "transform_job_v3_fixture"
    now = main_module.utc_now()
    state.production.lineage_records.create_transform_run(TransformRun(id=transform_id, workspace_id=state.workspace_id,
        task_id=state.task.id, job_id=job.id, transform_type="montage_render", input_digest=render_input_digest,
        input_refs=input_refs, output_refs=[published.logical_ref], engine="fixture", parameters={}, status="succeeded",
        started_at=now, finished_at=now))
    rendered_plan = replace(input_plan, status="rendered", output_path=published.logical_ref)
    original = state.production.timeline_records.create_timeline_version(TimelineVersion(id="timeline_job_v3_fixture",
        workspace_id=state.workspace_id, task_id=state.task.id, parent_version_id=input_version.id,
        sequence=input_version.sequence + 1,
        snapshot={"montage":creation_task_montage_plan_to_payload(rendered_plan),
                  "output_refs":[published.logical_ref], "transform_run_id":transform_id,
                  "render_input_digest":render_input_digest,
                  "render_input_timeline_version_id":input_version.id,
                  "render_input_subtitle_tracks":sealed_subtitle_tracks,
                  "render_input_subtitles_visible":subtitles_visible,
                  "creation_render_request_id":"request_v3_fixture",
                  **main_module._current_narration_snapshot(state.workspace_id, state.task)},
        created_by_operation_id=None))
    assert main_module._creation_render_is_valid(
        state.workspace_id, state.task.id, verify_media=False, timeline_version=original,
    )
    person_authorization = state.production.get_person_profile(
        state.workspace_id, person_profile.id, person_profile.version,
    ).authorization
    state.production.save_authorization(replace(person_authorization, version="v2-revoked", status="revoked"))
    assert not main_module._creation_render_is_valid(
        state.workspace_id, state.task.id, verify_media=False, timeline_version=original,
    )
    state.production.save_authorization(replace(person_authorization, version="v3-active"))
    # Missing sealed input facts must never restore a ready output.
    unsealed_snapshot = {key: value for key, value in original.snapshot.items() if not key.startswith("render_input_")
                         and key != "creation_render_request_id"}
    unsealed = replace(original, snapshot=unsealed_snapshot)
    assert not main_module._creation_render_is_valid(
        state.workspace_id, state.task.id, verify_media=False, timeline_version=unsealed,
    )
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *a, **kw: pytest.fail("ordinary draft must not encode media"))
    jobs_before = state.production.job_records.list_job_runs(state.workspace_id, state.task.id)
    changed = state.client.post(base + "/edits/exact", json={"operation_type":"ChangeCanvas", "aspect_ratio":"1:1"})
    assert changed.status_code == 200, changed.text
    task = state.production.task_records.get_creation_task(state.workspace_id, state.task.id)
    assert task.current_timeline_version_id != original.id and not task.output_ready
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["aspect_ratio"] == "1:1"
    assert state.production.job_records.list_job_runs(state.workspace_id, state.task.id) == jobs_before
    changed_input_version = state.production.timeline_records.get_timeline_version(state.workspace_id, task.current_timeline_version_id)
    changed_seal_snapshot = dict(original.snapshot)
    changed_seal_snapshot["render_input_timeline_version_id"] = changed_input_version.id
    changed_dependency = replace(original, parent_version_id=changed_input_version.id, snapshot=changed_seal_snapshot)
    assert not main_module._creation_render_is_valid(
        state.workspace_id, state.task.id, verify_media=False, timeline_version=changed_dependency,
    )
    undo = state.client.post(base + "/timeline/undo", json={})
    assert undo.status_code == 200, undo.text
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).output_ready
    assert state.client.post(base + "/timeline/redo", json={}).status_code == 200
    assert not state.production.task_records.get_creation_task(state.workspace_id, state.task.id).output_ready
    # Same length tampering must not restore a false ready output.
    main_module.artifact_store.resolve_file(published.logical_ref).write_bytes(b"x" * published.size_bytes)
    assert state.client.post(base + "/timeline/undo", json={}).status_code == 200
    assert not state.production.task_records.get_creation_task(state.workspace_id, state.task.id).output_ready
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)["output_path"] is None


def test_v3_natural_proposal_applies_once_without_media(person_render_edit_state, monkeypatch):
    state = person_render_edit_state
    base = f"/api/creation-tasks/{state.task.id}"
    monkeypatch.setattr(main_module, "_publish_task_picture_preview", lambda *a, **kw: pytest.fail("draft proposal must not encode media"))
    before = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    proposal = state.client.post(base + "/edits/preview?draft=true", json={
        "raw_command":"露脸占比调到0%", "context_stage":"montage", "selected_object_refs":[]})
    assert proposal.status_code == 200, proposal.text
    assert state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id) == before
    url = base + f"/edits/{proposal.json()['proposal']['id']}/confirm"
    applied = state.client.post(url)
    assert applied.status_code == 200, applied.text
    after = state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id)
    assert after["presenter_share_target"] == 0 and after["clips"] == before["clips"]
    assert state.provider.output_cache_keys == []
    version = state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id
    assert state.client.post(url).status_code == 409
    assert state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id == version


def _queued_person_edit(state, *, exact=False, change=False):
    base = f"/api/creation-tasks/{state.task.id}"
    assert state.client.get(base).status_code == 200
    if exact:
        response = state.client.post(base + "/edits/exact", json={
            "operation_type": "ChangePersonSegment" if change else "RegeneratePersonRender",
            "clip_id": "clip-person-target"})
        proposal = state.production.execution_records.get_task_event(state.workspace_id,
            f"event_{response.json()['job_id']}_start").payload["edit_proposal_id"] if response.status_code == 202 else None
    else:
        preview = state.client.post(base + "/edits/preview", json={
            "raw_command": "这一段换成当前人物出镜" if change else "这里只重做口型，其他都不要动",
            "context_stage": "review", "selected_object_refs": ["clip:clip-person-target"]})
        assert preview.status_code == 200, preview.text
        proposal = preview.json()["proposal"]["id"]
        response = state.client.post(base + f"/edits/{proposal}/confirm")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert set(response.json()) == {"status", "job_id", "request_id"}
    assert state.provider.output_cache_keys == []
    return response.json(), proposal


def _revoke_person_edit_authorization(state):
    person = state.production.get_person_profile(state.workspace_id,
        state.old_render.person_profile_id, state.old_render.person_version)
    state.production.save_authorization(replace(person.authorization, version="v2-revoked", status="revoked"))


def _person_edit_versions(state):
    return state.production.timeline_records.list_timeline_versions(state.workspace_id, state.task.id)


@pytest.mark.parametrize("exact,change", [(False, False), (True, False), (True, True)])
def test_public_person_edit_queue_grant_commits_only_target(permitted_person_edit_state, exact, change):
    state = permitted_person_edit_state
    assert state.client.get(f"/api/creation-tasks/{state.task.id}").status_code == 200
    base_version = main_module._ensure_montage_base_version(state.workspace_id,
        state.production.task_records.get_creation_task(state.workspace_id, state.task.id), state.montage).id
    track = state.production.subtitle_records.create_subtitle_track(SubtitleTrack(
        id="retained-person-caption", workspace_id=state.workspace_id, task_id=state.task.id,
        timeline_version_id=base_version, language="zh-CN", source_language=None,
        style={"font_size": 40}, cues=[ProductionSubtitleCue(id="caption", source_cue_id=None,
            text="保留当前字幕", start_seconds=0, end_seconds=4)],
        generation_method="timeline", model_id=None, artifact_ref=None, transform_id=None, status="unavailable",
        failure_reason="合成字幕尚无产物", impact="保留字幕文字与时间", repair_action="后续核验字幕产物"))
    narration_before = state.production.narration_records.list_narration_segments(state.workspace_id, state.task.id)
    queued, proposal_id = _queued_person_edit(state, exact=exact, change=change)
    assert len(_person_edit_versions(state)) == 1
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"
    duplicate = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/{proposal_id}/confirm")
    assert duplicate.status_code == 202 and duplicate.json() == queued
    assert state.runtime.run_once()
    job = state.production.job_records.get_job_run(state.workspace_id, queued["job_id"])
    assert job.status == "succeeded"
    assert len(state.provider.output_cache_keys) == 1
    assert len(_person_edit_versions(state)) == 2
    plan = creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    assert plan.clips[0].person_render_id != state.old_render.id
    assert asdict(plan.clips[1]) == asdict(state.montage.clips[1])
    assert [asdict(cue) for cue in plan.cues] == [asdict(cue) for cue in state.montage.cues]
    assert plan.narration_audio_path == state.montage.narration_audio_path
    assert state.production.narration_records.list_narration_segments(state.workspace_id, state.task.id) == narration_before
    tracks = state.production.subtitle_records.list_subtitle_tracks(state.workspace_id, state.task.id,
        timeline_version_id=state.production.task_records.get_creation_task(state.workspace_id, state.task.id).current_timeline_version_id)
    assert len(tracks) == 1
    assert tracks[0].cues == track.cues and tracks[0].style == track.style
    assert (tracks[0].status, tracks[0].artifact_ref, tracks[0].transform_id) == (track.status, track.artifact_ref, track.transform_id)
    assert (tracks[0].failure_reason, tracks[0].impact, tracks[0].repair_action) == (track.failure_reason, track.impact, track.repair_action)
    assert Path(main_module._person_render_media_path(state.old_render)).read_bytes() == b"old-person-render"
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "confirmed"
    duplicate = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/{proposal_id}/confirm")
    assert duplicate.status_code == 409
    assert not state.runtime.run_once()
    assert len(state.provider.output_cache_keys) == 1
    with state.production._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued["request_id"],)).fetchone()[0] == "done"


@pytest.mark.parametrize("block", ["stale", "revoke", "cancel"])
def test_person_edit_blocked_before_grant_never_calls_provider(permitted_person_edit_state, block):
    state = permitted_person_edit_state
    queued, proposal_id = _queued_person_edit(state)
    if block == "stale":
        result = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/exact",
            json={"operation_type": "SetClipLock", "clip_id": state.montage.clips[1].id, "locked": True})
        assert result.status_code == 200, result.text
    elif block == "revoke":
        _revoke_person_edit_authorization(state)
    else:
        result = state.client.post(f"/api/device/queue/{queued['request_id']}/cancel")
        assert result.status_code == 200, result.text
    before = [asdict(version) for version in _person_edit_versions(state)]
    assert not state.runtime.run_once()
    assert state.provider.output_cache_keys == []
    assert [asdict(version) for version in _person_edit_versions(state)] == before
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"
    assert state.production.job_records.get_job_run(state.workspace_id, queued["job_id"]).status != "succeeded"
    with state.production._connect() as conn:
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued["request_id"],)).fetchone()[0] in {"blocked", "cancelled"}


@pytest.mark.parametrize("block", ["failed", "stale", "revoke", "cancel"])
def test_person_edit_loses_permission_or_fails_without_committing_version(permitted_person_edit_state, monkeypatch, block):
    state = permitted_person_edit_state
    queued, proposal_id = _queued_person_edit(state)
    before = [asdict(version) for version in _person_edit_versions(state)]
    original = state.provider.render
    def interrupted(**kwargs):
        kwargs["grant_context"].check()
        if block == "failed":
            raise RuntimeError("synthetic provider failed")
        if block == "stale":
            result = state.client.post(f"/api/creation-tasks/{state.task.id}/edits/exact",
                json={"operation_type": "SetClipLock", "clip_id": state.montage.clips[1].id, "locked": True})
            assert result.status_code == 200
        elif block == "revoke":
            _revoke_person_edit_authorization(state)
        else:
            result = state.client.post(f"/api/device/queue/{queued['request_id']}/cancel")
            assert result.status_code == 200, result.text
        return original(**kwargs)
    monkeypatch.setattr(state.provider, "render", interrupted)
    assert state.runtime.run_once()
    job = state.production.job_records.get_job_run(state.workspace_id, queued["job_id"])
    assert job.status == ("cancelled" if block == "cancel" else "failed")
    assert job.failure_reason
    versions = _person_edit_versions(state)
    assert len(versions) == len(before) + (1 if block == "stale" else 0)
    plan = creation_task_montage_plan_from_payload(state.production.get_creation_task_montage_plan(state.workspace_id, state.task.id))
    assert plan.clips[0].person_render_id == state.old_render.id
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"
    with state.production._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued["request_id"],)).fetchone()[0] in {"failed", "cancelled"}


def test_person_edit_publication_rolls_back_with_failed_job(permitted_person_edit_state, monkeypatch):
    state = permitted_person_edit_state
    queued, proposal_id = _queued_person_edit(state)
    before = [asdict(version) for version in _person_edit_versions(state)]
    original = state.production.edit_adoption.confirm
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        assert kwargs["connection"] is not None
        raise RuntimeError("synthetic checkpoint publication failed")
    monkeypatch.setattr(state.production.edit_adoption, "confirm", fail_after_write)
    assert state.runtime.run_once()
    assert len(state.provider.output_cache_keys) == 1
    assert [asdict(version) for version in _person_edit_versions(state)] == before
    assert state.production.edit_records.get_edit_proposal(state.workspace_id, proposal_id).status == "proposed"
    assert state.production.job_records.get_job_run(state.workspace_id, queued["job_id"]).status == "failed"
    with state.production._connect() as conn:
        assert conn.execute("SELECT state FROM device_permit WHERE id=1").fetchone()[0] == "free"
        assert conn.execute("SELECT state FROM device_work_requests WHERE id=?", (queued["request_id"],)).fetchone()[0] == "failed"


def test_failed_person_edit_requires_new_preview_and_reuses_concurrent_active_request(permitted_person_edit_state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    state = permitted_person_edit_state
    queued, proposal_id = _queued_person_edit(state)
    original = state.provider.render
    def failed(**kwargs):
        kwargs["grant_context"].check()
        raise RuntimeError("synthetic provider failed once")
    monkeypatch.setattr(state.provider, "render", failed)
    assert state.runtime.run_once()
    assert state.production.job_records.get_job_run(state.workspace_id, queued["job_id"]).status == "failed"
    assert len(_person_edit_versions(state)) == 1
    base = f"/api/creation-tasks/{state.task.id}"
    assert state.client.post(base + f"/edits/{proposal_id}/confirm").status_code == 409
    monkeypatch.setattr(state.provider, "render", original)
    # Two explicit fresh exact previews, released simultaneously, still create
    # only one active job/event/request under the existing SQLite transaction.
    def retry():
        return state.client.post(base + "/edits/exact", json={
            "operation_type": "RegeneratePersonRender", "clip_id": "clip-person-target"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: retry(), range(2)))
    assert all(item.status_code == 202 for item in responses), [item.text for item in responses]
    assert responses[0].json() == responses[1].json()
    retried = responses[0].json()
    assert retried["job_id"] != queued["job_id"]
    assert state.provider.output_cache_keys == []
    # Repeating the failed old proposal cannot join or revive the new attempt.
    assert state.client.post(base + f"/edits/{proposal_id}/confirm").status_code == 409
    with state.production._connect() as conn:
        assert conn.execute("SELECT count(*) FROM job_runs WHERE kind='creation_task_person_edit'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM task_events WHERE event_type='person_edit_requested'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM device_work_requests").fetchone()[0] == 2
    assert state.runtime.run_once()
    assert state.production.job_records.get_job_run(state.workspace_id, retried["job_id"]).status == "succeeded"
    assert state.production.job_records.get_job_run(state.workspace_id, queued["job_id"]).status == "failed"
    assert len(_person_edit_versions(state)) == 2
    assert len(state.provider.output_cache_keys) == 1
    assert not state.runtime.run_once()


def test_tag_and_observation_relevance_precedes_semantic_similarity_and_repeat_preference(tmp_path):
    from app.visual_understanding import SegmentUnderstanding
    asset = _asset(tmp_path)
    cue = NarrationCue(id="cue", source_section_id="section", text="人物走路", duration_seconds=2)
    irrelevant = replace(asset.segments[0], description="电脑键盘特写", user_tags=[], system_tags=[], usage_count=0)
    tagged = replace(irrelevant, description="街道画面", user_tags=["人物走路"], usage_count=100)
    observed = replace(irrelevant, description="", understanding=SegmentUnderstanding("ready", summary="街道",
        facts=[{"kind": "action", "label": "人物走路", "text": "人物走路穿过街道", "sample_times": [.3, 1.7]}]))
    unrelated = _score_candidate(cue, asset, irrelevant, 0, 0, visual_score=1.0, visual_only=True)
    by_tag = _score_candidate(cue, asset, tagged, 10, 10, visual_score=0.0, visual_only=True)
    by_observation = _score_candidate(cue, asset, observed, 0, 0, visual_score=0.0, visual_only=True)
    assert by_tag[0] > unrelated[0]
    assert by_observation[0] > unrelated[0]
    assert "标签依据：人物走路" in by_tag[-1]
    assert any("主体、动作或场景命中" in reason for reason in by_observation[-1])
    # A manual correction continues to shadow contradictory model observations.
    corrected = replace(observed, description="这是电脑键盘特写", system_tags=[])
    assert _score_candidate(cue, asset, corrected, 0, 0, visual_only=True)[0] < by_observation[0]


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,reason='ffmpeg unavailable')
def test_serial_lossless_hard_cut_uses_real_synthetic_media_and_original_frames(tmp_path,monkeypatch,granted_media):
    # Real media + persisted isolated grant; synthetic grant fixture is not host calibration.
    import app.montage as montage
    blue,green,audio=tmp_path/'blue.mp4',tmp_path/'green.mp4',tmp_path/'voice.wav'
    for path,color in ((blue,'blue'),(green,'green')):
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=c={color}:s=160x120:d=1.6:r=30',
            '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1.6',str(audio)],check=True,capture_output=True)
    clips=[MontageClip('one','cue','blue','segment',str(blue),0,.8,0,.8,1,[],False),
        MontageClip('two','cue','green','segment',str(green),.4,1.2,.8,1.6,1,[],False,
            visual_effects={'fade_out_seconds':.2})]
    plan=CreationTaskMontagePlan(id='serial-test',workspace_id='workspace',task_id='task',script_id='script',
        script_title='合成硬切测试',status='ready',cues=[],clips=clips,duration_seconds=1.6,low_confidence_clip_count=0)
    commands=[]
    runner=montage._run_granted_media_command
    def recorded(scheduler,grant,command,**kwargs):
        commands.append((command.copy(),dict(kwargs)))
        return runner(scheduler,grant,command,**kwargs)
    monkeypatch.setattr(montage,'_run_granted_media_command',recorded)
    with granted_media([blue,green,audio],kind='creation_render') as context:
        rendered=montage.MontageRenderer(tmp_path).render(plan,audio,job_staging_path=tmp_path/'job',subtitle_tracks=[],
            scheduler=context.scheduler,grant=context.grant,max_output_bytes=context.max_output_bytes,threads=context.threads,
            deadline_monotonic=context.deadline_monotonic)
    assert rendered.status=='rendered'
    encodes=[command for command,_ in commands if command[0]=='ffmpeg']
    assert len(encodes)==3
    assert [command.count('-i') for command in encodes]==[1,1,2]
    assert all(command[command.index('-c:v')+1]=='ffv1' and command[command.index('-f')+1]=='nut'
        and '-crf' not in command for command in encodes[:2])
    assert encodes[-1][encodes[-1].index('-crf')+1]=='20'
    assert all(options['deadline_monotonic']==context.deadline_monotonic for _,options in commands)
    assert not list((tmp_path/'job').glob('.subtitle-overlays-*'))
    output=tmp_path/'job'/'preview.mp4'
    info=json.loads(subprocess.run(['ffprobe','-v','error','-count_frames','-show_entries',
        'stream=codec_type,codec_name,width,height,nb_read_frames:format=duration','-of','json',str(output)],
        check=True,capture_output=True,text=True).stdout)
    video=next(item for item in info['streams'] if item['codec_type']=='video')
    assert (video['width'],video['height'],int(video['nb_read_frames']))==(1080,1920,48)
    def rgb_at(seconds):
        raw=subprocess.run(['ffmpeg','-v','error','-ss',str(seconds),'-i',str(output),'-frames:v','1','-vf','scale=1:1',
            '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
        return tuple(raw[:3])
    first,second,tail=rgb_at(.2),rgb_at(1),rgb_at(47/30)
    assert first[2]>first[0]+100 and second[1]>second[2]+50
    assert max(tail)<10  # Original final fade clock was retained across serial encoding.


def _ffv1_picture_probe(frames=115):
    return {'format': {'format_name': 'nut', 'duration': str((frames-1)/30)}, 'streams': [{
        'codec_type': 'video', 'codec_name': 'ffv1', 'width': 1920, 'height': 1080,
        'pix_fmt': 'yuv420p', 'r_frame_rate': '30/1', 'nb_read_frames': str(frames)}]}


@pytest.mark.parametrize('disk_case', ['enough', 'below_raw_payload', 'existing_mix', 'write_excess'])
def test_serial_picture_contract_keeps_900_frames_and_one_temporary_budget(tmp_path, monkeypatch, disk_case):
    """Command/probe contract only; byte placeholders are not media acceptance."""
    import app.montage as montage
    source = tmp_path/'source.mp4'
    source.write_bytes(b'source-placeholder')
    output = tmp_path/'job'/'preview.mp4'
    output.parent.mkdir()
    overhead = output.parent/'timeline-mix.wav'
    overhead.write_bytes(b'mix-placeholder')
    boundaries = [0, 3.84, 9.36, 14.4, 19.84, 25.2, 30]
    clips = [MontageClip(str(i), 'cue', 'asset', 'segment', str(source), 12, 12+end-start,
        start, end, 1, [], False) for i, (start, end) in enumerate(zip(boundaries, boundaries[1:]))]
    plan = CreationTaskMontagePlan('plan', 'workspace', 'task', 'script', 'title', 'ready', [], clips, 30, 0,
        aspect_ratio='16:9')
    grant, cancelled, calls = object(), threading.Event(), []
    scheduler = SimpleNamespace(assert_grant=lambda current: current is grant or pytest.fail('grant changed'))
    deadline, budget = time.monotonic()+90, 3*1024**3
    if disk_case == 'below_raw_payload':
        budget = 1024**2  # Finite byte-placeholder contract; not a measured FFV1 compression bound.
    elif disk_case in {'existing_mix', 'write_excess'}:
        budget = overhead.stat().st_size + (10 if disk_case == 'write_excess' else 0)
    frames_by_path = {}
    def command(current_scheduler, current_grant, argv, **options):
        assert current_scheduler is scheduler and current_grant is grant
        assert options['deadline_monotonic'] == deadline and options['cancel_event'] is cancelled
        calls.append((argv, options))
        if argv[0] == 'ffmpeg':
            target = Path(argv[-1])
            previous = tuple(path for path in output.parent.rglob('*') if path.is_file())
            assert set(options['output_paths']) == {*previous, target}
            assert int(argv[argv.index('-fs')+1]) == budget-sum(path.stat().st_size for path in previous)
            frames_by_path[str(target)] = int(argv[argv.index('-frames:v')+1])
            target.write_bytes(b'x'*(int(argv[argv.index('-fs')+1])+1) if disk_case == 'write_excess' else b'ffv1-placeholder')
            assert options['max_output_file_bytes'] == budget
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        assert '-count_frames' in argv
        assert argv[argv.index('-threads')+1] == '2'
        return SimpleNamespace(stdout=json.dumps(_ffv1_picture_probe(frames_by_path[argv[-1]])).encode())
    monkeypatch.setattr(montage, '_run_granted_media_command', command)
    if disk_case in {'existing_mix', 'write_excess'}:
        with pytest.raises(ValueError, match='同一许可磁盘上限'):
            montage.MontageRenderer(tmp_path)._render_serial_picture_input(plan, output.parent/'pictures',
                output, budget, scheduler=scheduler, grant=grant, cancel_event=cancelled, threads=2, deadline_monotonic=deadline)
        assert len(calls) == (1 if disk_case == 'write_excess' else 0)
        return
    manifest = montage.MontageRenderer(tmp_path)._render_serial_picture_input(plan, output.parent/'pictures',
        output, budget, scheduler=scheduler, grant=grant, cancel_event=cancelled, threads=2, deadline_monotonic=deadline)
    encodes = [argv for argv, _ in calls if argv[0] == 'ffmpeg']
    assert list(frames_by_path.values()) == [115, 166, 151, 163, 161, 144]
    assert sum(frames_by_path.values()) == 900
    durations = [float(line.split()[1]) for line in manifest.read_text().splitlines() if line.startswith('duration ')]
    assert durations == pytest.approx([frames/30 for frames in frames_by_path.values()])
    assert sum(durations) == pytest.approx(30)
    assert all(argv[-1].endswith('.nut') and argv[argv.index('-c:v')+1] == 'ffv1'
        and argv[argv.index('-f')+1] == 'nut' and argv[argv.index('-pix_fmt')+1] == 'yuv420p'
        and argv[argv.index('-threads:v')+1] == '2' and 'fps=30' in argv[argv.index('-vf')+1]
        and '-crf' not in argv and '-movflags' not in argv for argv in encodes)
    assert all(all(argv[argv.index(key)+1] == value for key, value in {
        '-level':'3', '-coder':'0', '-context':'0', '-g':'1', '-slices':'4', '-slicecrc':'1'}.items()) for argv in encodes)


@pytest.mark.parametrize('invalid', ['format', 'codec', 'raw', 'size', 'pixels', 'fps', 'missing_fps', 'short', 'long', 'audio'])
def test_serial_picture_probe_rejects_wrong_format_or_incomplete_frame_clock(tmp_path, monkeypatch, invalid):
    import app.montage as montage
    path = tmp_path/'internal.nut'
    path.write_bytes(b'probe-placeholder')
    payload = _ffv1_picture_probe()
    video = payload['streams'][0]
    if invalid == 'format':
        payload['format']['format_name'] = 'mov,mp4,m4a,3gp,3g2,mj2'
    elif invalid == 'audio':
        payload['streams'].append({'codec_type': 'audio', 'codec_name': 'aac'})
    else:
        key, value = {'codec': ('codec_name', 'h264'), 'raw': ('codec_name', 'rawvideo'),
            'size': ('width', 1280), 'pixels': ('pix_fmt', 'yuv422p'),
            'fps': ('r_frame_rate', '30000/1001'), 'missing_fps': ('r_frame_rate', None),
            'short': ('nb_read_frames', '114'), 'long': ('nb_read_frames', '116')}[invalid]
        video[key] = value
    context = SimpleNamespace(check=lambda: None, scheduler=object(), grant=object(), cancel_event=None,
        deadline_monotonic=time.monotonic()+90, max_output_bytes=1024, threads=2)
    monkeypatch.setattr(montage, '_run_granted_media_command', lambda *args, **kwargs:
        SimpleNamespace(stdout=json.dumps(payload).encode()))
    with pytest.raises(ValueError, match='中间片'):
        montage._probe_serial_picture_input(path, context, expected_size=(1920,1080), expected_frames=115)


@pytest.mark.parametrize('size', [0, 1025])
def test_serial_picture_probe_keeps_file_budget(tmp_path, monkeypatch, size):
    import app.montage as montage
    path = tmp_path/'internal.nut'
    path.write_bytes(b'x'*size)
    context = SimpleNamespace(check=lambda: None, scheduler=object(), grant=object(), cancel_event=None,
        deadline_monotonic=time.monotonic()+90, max_output_bytes=1024, threads=2)
    monkeypatch.setattr(montage, '_run_granted_media_command', lambda *args, **kwargs:
        SimpleNamespace(stdout=json.dumps(_ffv1_picture_probe()).encode()))
    with pytest.raises(ValueError, match='许可输出上限'):
        montage._probe_serial_picture_input(path, context, expected_size=(1920,1080), expected_frames=115)


def test_ffv1_intermediate_does_not_widen_h264_preview_or_final_gate(tmp_path, monkeypatch):
    import app.montage as montage
    path = tmp_path/'ffv1.nut'
    path.write_bytes(b'probe-placeholder')
    context = SimpleNamespace(check=lambda: None, scheduler=object(), grant=object(), cancel_event=None,
        deadline_monotonic=time.monotonic()+90, max_output_bytes=1024)
    monkeypatch.setattr(montage, '_run_granted_media_command', lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout=json.dumps(_ffv1_picture_probe()).encode(), stderr=b''))
    with pytest.raises(ValueError, match='编码'):
        montage.probe_preview_video(path, context, expected_size=(1920,1080), expected_duration_seconds=115/30, silent=True)
    with pytest.raises(OSError, match='H.264'):
        montage._validate_rendered_media(path, scheduler=context.scheduler, grant=context.grant,
            expected_size=(1920,1080), deadline_monotonic=context.deadline_monotonic)
