"""Template metadata only; rendered task/output qualification below is declared."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from fastapi import Request

from app import main
from app.content_templates import list_content_templates
from app.creation.video_template_use_case import VideoTemplateUseCase
from app.creation.video_template_records import VideoTemplate
from app.production import ProductionRepository
from app.schemas import SaveVideoTemplateRequest, VideoTemplateVersionRequest
from test_content_visibility_routes import material_library_no_execution


def test_metadata_template_append_task_settings_and_builtin_projection(tmp_path, monkeypatch):
    material_library_no_execution(monkeypatch)
    workspace, owner = "template-metadata", "declared-owner"
    database = tmp_path / "templates.sqlite3"
    repository = ProductionRepository(database, tmp_path / "unused-library")
    original = repository.video_templates.save_video_template(VideoTemplate(id="template-existing", workspace_id=workspace,
        version=1, name="原模板", origin="user", owner_user_id="untrusted-input",
        visibility="shared", source_task_id="original-task", source_timeline_version_id="original-timeline",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), settings={"aspect_ratio": "1:1",
            "style_instruction": "原风格", "voice": {"profile_id": "original-voice", "version": "v1"},
            "custom_existing": "preserve"}), verified_owner_user_id=owner)
    assert original.owner_user_id == owner
    original_snapshot = asdict(original)
    trace, reads = [], []
    reader_facts = iter([(owner, False), ("declared-shared-viewer", False), (owner, False), (owner, False)])

    def workspace_for(request):
        trace.append("workspace")
        return workspace

    def trusted_reader(request, ws):
        assert ws == workspace
        user, diagnostic = next(reader_facts)
        reads.append(user)
        trace.append("reader:" + user)
        return user, diagnostic

    get_template, save_template = repository.video_templates.get_video_template, repository.video_templates.save_video_template

    def observe_template(*args):
        trace.append("template.read")
        return get_template(*args)

    def observe_save(template, **kwargs):
        trace.append("template.save")
        assert kwargs == {"verified_owner_user_id": owner, "local_diagnostic": False}
        return save_template(template, **kwargs)

    monkeypatch.setattr(main, "production_repo", repository)
    monkeypatch.setattr(main, "current_workspace_id", workspace_for)
    monkeypatch.setattr(main, "_content_reader", trusted_reader)
    monkeypatch.setattr(repository.video_templates, "get_video_template", observe_template)
    monkeypatch.setattr(repository.video_templates, "save_video_template", observe_save)
    request = Request({"type": "http", "method": "POST", "path": "/metadata", "headers": []})
    appended = main._video_template_http_adapter().create_video_template_version(original.id, request,
        VideoTemplateVersionRequest(expected_version=1, name=" 新名称 ", style_instruction=" 新风格 "))
    append_trace = list(trace)
    assert append_trace == ["workspace", "reader:" + owner, "template.read",
        "reader:declared-shared-viewer", "template.save"]
    assert appended["can_edit"] is True
    fresh = ProductionRepository(database, tmp_path / "unused-library")
    assert asdict(fresh.video_templates.get_video_template(workspace, original.id, 1)) == original_snapshot
    expected_v2 = {**original_snapshot, "version": 2, "name": "新名称", "created_at": appended["created_at"],
        "settings": {**original.settings, "style_instruction": "新风格"}}
    assert asdict(fresh.video_templates.get_video_template(workspace, original.id, 2)) == expected_v2
    assert {k: v for k, v in appended.items() if k != "can_edit"} == expected_v2

    # No video was produced. This tiny file tests nonempty stat only; the output port
    # and qualified source records below explicitly supply metadata facts.
    output = tmp_path / "NONMEDIA-output-fixture"
    output.write_bytes(b"metadata fixture, not a playable video")
    task_id, timeline_id = "declared-task", "declared-timeline"
    task = SimpleNamespace(id=task_id, current_timeline_version_id=timeline_id, confirmed_script_id="declared-script")
    plan = SimpleNamespace(language="en-US", duration_target_seconds=45, presenter_share_target=20,
        lipsync_quality="high", selected_voice=SimpleNamespace(profile_id="voice-fixed", version="v8"))
    effects = {"exposure": 0.25, "contrast": 1.1, "saturation": 0.9, "temperature": 0.1}
    clips = [{"id": "old-clip-" + str(index), "cue_id": "old-cue", "asset_id": "old-asset",
        "segment_id": "old-segment", "source_path": "declared-old-source", "source_start_seconds": 0,
        "source_end_seconds": 1, "timeline_start_seconds": index, "timeline_end_seconds": index + 1,
        "match_score": 1.0, "match_reasons": ["declared metadata"], "needs_confirmation": False,
        "fit_mode": "fit", "crop_x": 0.2, "visual_effects": {**effects, "zoom": index + 1,
            "fade_in_seconds": index, "beauty_strength": index * 0.2}} for index in range(2)]
    montage_payload = {"id": "declared-montage", "workspace_id": workspace, "task_id": task_id,
        "script_id": "declared-script", "script_title": "OLD TEXT MUST NOT COPY", "status": "rendered",
        "cues": [], "clips": clips, "duration_seconds": 2, "low_confidence_clip_count": 0,
        "aspect_ratio": "16:9", "output_path": "declared-old-output-ref"}
    source_style = {"position": "top", "font_size": 38}

    def task_fact(ws, identifier):
        trace.append("task")
        assert (ws, identifier) == (workspace, task_id)
        return task

    def plan_fact(ws, current_task):
        trace.append("plan")
        assert ws == workspace and current_task is task
        return plan

    def montage_fact(ws, identifier):
        trace.append("montage")
        assert (ws, identifier) == (workspace, task_id)
        return montage_payload

    def output_fact(ws, identifier, *, subtitle):
        trace.append("qualified-output-port")
        assert (ws, identifier, subtitle) == (workspace, task_id, False)
        return output

    nonempty = VideoTemplateUseCase.require_nonempty_output

    def observe_nonempty(path):
        trace.append("nonempty-stat")
        assert path is output
        return nonempty(path)

    def track_facts(ws, current_task, *, montage):
        trace.append("subtitles")
        assert ws == workspace and current_task is task and montage.status == "rendered"
        return [SimpleNamespace(source_language="en-US", style={"position": "bottom"}),
            SimpleNamespace(source_language=None, style=source_style),
            SimpleNamespace(source_language=None, style={"position": "ignored-later-source"})]

    def script_facts(ws, identifier):
        trace.append("scripts")
        assert (ws, identifier) == (workspace, task_id)
        return [SimpleNamespace(id="unconfirmed-script", sections=["OLD TEXT"]),
            SimpleNamespace(id="declared-script", sections=["OLD ONE", "OLD TWO"])]

    monkeypatch.setattr(main, "_creation_task_or_404", task_fact)
    monkeypatch.setattr(main, "_creation_plan_for_task_or_404", plan_fact)
    monkeypatch.setattr(repository, "get_creation_task_montage_plan", montage_fact)
    monkeypatch.setattr(main, "_creation_task_output_path", output_fact)
    monkeypatch.setattr(VideoTemplateUseCase, "require_nonempty_output", staticmethod(observe_nonempty))
    monkeypatch.setattr(main, "_current_subtitle_tracks", track_facts)
    monkeypatch.setattr(repository.script_records, "list_creation_task_scripts", script_facts)
    trace.clear()
    saved = main._video_template_http_adapter().save_creation_task_template(task_id, request,
        SaveVideoTemplateRequest(name=" 新模板 ", style_instruction=" 新任务风格 "))
    task_trace = list(trace)
    assert task_trace == ["workspace", "task", "plan", "montage", "qualified-output-port", "nonempty-stat",
        "subtitles", "scripts", "workspace", "reader:" + owner, "workspace", "reader:" + owner, "template.save"]
    expected_settings = {"language": "en-US", "aspect_ratio": "16:9", "duration_target_seconds": 45,
        "presenter_share_target": 20, "lipsync_quality": "high",
        "voice": {"profile_id": "voice-fixed", "version": "v8"}, "script_section_count": 2,
        "style_instruction": "新任务风格", "fit_mode": "fit", "color_style": effects, "subtitle_style": source_style}
    assert saved["settings"] == expected_settings and saved["name"] == "新模板"
    assert re.fullmatch(r"template_[0-9a-f]{12}", saved["id"])
    assert (saved["version"], saved["source_task_id"], saved["source_timeline_version_id"], saved["owner_user_id"]) == (
        1, task_id, timeline_id, owner)
    assert asdict(fresh.video_templates.get_video_template(workspace, saved["id"], 1)) == saved
    serialized = json.dumps(saved["settings"], ensure_ascii=False)
    assert all(value not in serialized for value in ("old-clip", "old-asset", "old-segment", "old-output", "OLD TEXT", "OLD ONE"))
    trace.clear()
    builtins = VideoTemplateUseCase(main.production_repo).builtin_templates(workspace)
    catalog = list_content_templates()
    assert trace == [] and len(builtins) == len(catalog) and builtins
    first = builtins[0]
    assert (first.id, first.name, first.version, first.workspace_id, first.origin) == (
        "builtin_" + catalog[0].template_id, catalog[0].name, 1, workspace, "built_in")
    assert first.settings == {"language": "zh-CN", "aspect_ratio": "9:16", "duration_target_seconds": 60,
        "script_structure": catalog[0].script_structure, "style_instruction": catalog[0].scenario,
        "fit_mode": "fill", "voice": None, "subtitle_style": {"name": "default", "font_family": "PingFang SC",
            "font_size": 40, "primary_color": "#FFFFFF", "emphasis_color": "#FFD54F", "position": "bottom",
            "bilingual_order": "source_then_translation"}}
    assert reads == [owner, "declared-shared-viewer", owner, owner]
    with sqlite3.connect(database) as conn:
        counts = {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("video_templates", "creation_plans", "creation_tasks", "job_runs")}
    assert counts == {"video_templates": 3, "creation_plans": 0, "creation_tasks": 0, "job_runs": 0}
    evidence = os.environ.get("VIDEO_TEMPLATE_EVIDENCE_DIR")
    if evidence:
        (Path(evidence) / "NORMAL_FACTS.json").write_text(json.dumps({"database": str(database),
            "invocations": [{"kind": "field_append", "trace": append_trace},
                {"kind": "task_save", "trace": task_trace}, {"kind": "builtin_projection", "trace": []}],
            "reader_values": reads, "counts": counts, "template_settings": expected_settings,
            "saved_template_id": saved["id"], "nonmedia_fixture_bytes": output.stat().st_size,
            "real_template_repository_and_fresh_reads": True, "source_output_identity_facts": "declared fixtures",
            "physical_video_model_production_auth_acceptance": False}, ensure_ascii=False, indent=2) + "\n")
