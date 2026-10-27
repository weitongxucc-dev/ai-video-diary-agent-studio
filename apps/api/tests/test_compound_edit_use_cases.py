"""Compound candidates preserve cross-object constraints before any persistence."""
from dataclasses import asdict, replace

import pytest

from app.audio_timeline import AudioTimeline, TimelineAudioClip, TimelineAudioTrack
from app.creation.contracts import EditCommand, SubtitleCue, SubtitleTrack
from app.creation.editing.compound import calculate_compound_edit, apply_compound_proposal
from app.creation.editing.picture import EditConflict
from app.montage import CreationTaskMontagePlan, MontageClip
from app.creation.contracts import CreationTask
from test_linked_edit_use_cases import linked_state as linked_state


@pytest.fixture
def compound_values():
    audio = AudioTimeline(tracks=[TimelineAudioTrack(
        id="voice", name="配音", role="narration", clips=[TimelineAudioClip(
            id="voice-clip", name="配音", source_ref="workspace://fixture/voice.wav",
            source_start_ms=0, source_end_ms=8000, timeline_start_ms=0,
        )],
    )])
    clips = [MontageClip(name, name, "asset", name, "workspace://fixture/source.mp4",
        start, end, start, end, 1, [], False) for name, start, end in (("first", 0, 4), ("last", 4, 8))]
    plan = CreationTaskMontagePlan("plan", "workspace", "task", "script", "pure candidate", "ready",
        [], clips, 8, 0, audio_timeline=audio)
    zh = SubtitleTrack("zh", "workspace", "task", "timeline", "zh-CN", None, {"font_size": 40},
        [SubtitleCue("sentence", None, "原文", 0, 4)], "timeline", None,
        "artifacts/original.srt", "fixture-original", "valid")
    task = CreationTask(id="task", workspace_id="workspace", plan_id="plan", name="pure candidate",
        scenario="knowledge_explanation", owner_user_id="member", current_timeline_version_id="timeline")
    return task, plan, zh, audio


def calculate(values, text, included=None, tracks=None):
    task, plan, zh, audio = values
    return calculate_compound_edit(plan, [zh] if tracks is None else tracks, audio,
        raw_command=text, selected_refs=["clip:first"], base_version_id="timeline", playhead_ms=None,
        included_step_indices=included)


def test_compound_candidate_and_adoption_compute_same_three_lanes_without_mutation(compound_values):
    task, plan, zh, audio = compound_values
    before, captions = asdict(plan), asdict(zh)
    candidate = calculate(compound_values, "当前镜头亮一点，同时字幕字号改为64，配音小一点")
    assert asdict(plan) == before and asdict(zh) == captions
    assert candidate.plan.clips[0].visual_effects["exposure"] == .25
    assert candidate.plan.clips[1] == plan.clips[1]
    assert candidate.plan.audio_timeline.tracks[0].gain_db == -3
    assert candidate.subtitle_tracks[0].style["font_size"] == 64
    assert candidate.plan.duration_seconds == 8
    command = EditCommand("workspace", "task", {"member_id": "member"}, "timeline", "EditCompoundTimeline", {
        "clips": [asdict(clip) for clip in candidate.plan.clips],
        "audio_timeline": candidate.plan.audio_timeline.model_dump(), "steps": candidate.steps,
    })
    checked = []
    adopted, tracks = apply_compound_proposal(task, plan, [zh], command,
        validate_audio=lambda graph, duration: checked.append((graph, duration)))
    assert asdict(adopted) == asdict(candidate.plan)
    assert tracks == candidate.subtitle_tracks and checked == [(adopted.audio_timeline, 8)]


def test_compound_selection_recompiles_against_original_objects(compound_values):
    task, plan, zh, audio = compound_values
    selected = calculate(compound_values, "当前镜头亮一点，同时字幕字号改为64，配音小一点", [1])
    assert [step["enabled"] for step in selected.steps] == [False, True, False]
    assert selected.plan.clips == plan.clips and selected.plan.audio_timeline == audio
    assert selected.subtitle_tracks[0].style["font_size"] == 64
    assert selected.subtitle_tracks[0].cues == zh.cues


def test_compound_source_text_invalidates_translation_and_reports_only_new_gaps(compound_values):
    task, plan, zh, audio = compound_values
    en = replace(zh, id="en", language="en-US", source_language="zh-CN", generation_method="translation",
        cues=[replace(zh.cues[0], id="translation", source_cue_id="sentence", text="original")])
    changed = calculate(compound_values, "中文字幕1改成“新中文”，中文字幕1显示0.5到3.5秒", tracks=[zh, en])
    assert [track.language for track in changed.subtitle_tracks] == ["zh-CN"]
    assert changed.subtitle_tracks[0].cues[0].text == "新中文"
    assert changed.subtitle_tracks[0].artifact_ref is None
    assert changed.plan.audio_timeline == audio
    assert any("旧英文翻译将失效" in effect for effect in changed.external_effects)
    assert [effect for effect in changed.external_effects if "新增无字幕时段" in effect] == [
        "中文字幕新增无字幕时段：0.00–0.50 秒。", "中文字幕新增无字幕时段：3.50–4.00 秒。",
    ]


def test_compound_conflicting_preservation_rejects_whole_candidate(compound_values):
    task, plan, zh, audio = compound_values
    before = asdict(plan)
    with pytest.raises(ValueError, match="音频修改冲突"):
        calculate(compound_values, "当前镜头亮一点，同时配音小一点，配音不变")
    assert asdict(plan) == before


def test_compound_adoption_rejects_stale_version_before_reading_candidate_payload(compound_values):
    task, plan, zh, audio = compound_values
    command = EditCommand("workspace", "task", {"member_id": "member"}, "old", "EditCompoundTimeline", {})
    with pytest.raises(EditConflict, match="时间线已经变化"):
        apply_compound_proposal(task, plan, [zh], command,
            validate_audio=lambda *_: pytest.fail("stale edit must not validate or execute audio"))


def test_compound_proposal_seals_actual_source_without_writing_and_rechecks_revocation(linked_state):
    import app.main as main
    from app.creation.editing.sources import EditSourceVerifier
    from app.creation_execution import CreationExecutionActor
    from app.creation.editing.compound import prepare_compound_proposal

    repo, task, plan, track, identity, verifier = linked_state
    verifier = EditSourceVerifier(repo, identity=identity, audio_service=main._task_audio(),
        audio_actor=CreationExecutionActor("local", identity["member_id"]))
    text = "当前镜头亮一点，同时配音小一点"
    selected = ["clip:clip_2"]
    with repo._connect() as conn:
        before = tuple(conn.iterdump())
    candidate = calculate_compound_edit(plan, [track], plan.audio_timeline, raw_command=text,
        selected_refs=selected, base_version_id="linked-base", playhead_ms=None)
    original = EditCommand("local", "task", identity, "linked-base", "EditCompoundTimeline", {})
    command, intent, proposal = prepare_compound_proposal(task, plan, original, candidate,
        raw_command=text, context_stage="montage", selected_refs=selected, playhead_ms=None,
        selection_command=None, verifier=verifier, output_refs=[])
    assert command.affected_clip_ids == ("clip_2",)
    assert command.input_source_facts[0]["reference"] == plan.audio_timeline.tracks[0].clips[0].source_ref
    assert intent.constraints["affected_source_scope"] == verifier.scope_payload(command)
    assert proposal.operation["payload"]["base_version_id"] == "linked-base"
    assert proposal.preview["after"]["ref"] == "timeline:linked-base"
    with repo._connect() as conn:
        assert tuple(conn.iterdump()) == before
        verifier.verify(command, conn)
    repo._save_asset(replace(repo.get_asset("local", "neighbour"),
        owner_user_id="another-member", visibility="private"))
    with repo._connect() as conn, pytest.raises(PermissionError):
        verifier.verify(command, conn)


@pytest.mark.parametrize("source_kind", ["clip", "audio"])
def test_real_compound_adoption_rechecks_all_original_sources_at_commit(linked_state, monkeypatch, source_kind):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    client = TestClient(main.app)
    result = client.post("/api/creation-tasks/task/edits/preview?draft=true", json={
        "raw_command": "当前镜头亮一点，同时配音小一点", "context_stage": "montage",
        "selected_object_refs": ["clip:clip_2"],
    })
    assert result.status_code == 200, result.text
    proposal = result.json()["proposal"]
    frozen = repo.get_edit_intent("local", proposal["intent_id"]).constraints["affected_source_scope"]
    assert frozen["clip_ids"] == ["clip_2"]
    assert frozen["input_source_facts"][0]["reference"] == plan.audio_timeline.tracks[0].clips[0].source_ref
    commit = repo.confirm_edit_proposal

    def revoke(*args, **kwargs):
        assert kwargs["verify_sources"] is not None
        asset_id = "neighbour" if source_kind == "clip" else "voice"
        repo._save_asset(replace(repo.get_asset("local", asset_id),
            visibility="private", owner_user_id="another-member"))
        return commit(*args, **kwargs)

    monkeypatch.setattr(repo, "confirm_edit_proposal", revoke)
    rejected = client.post(f"/api/creation-tasks/task/edits/{proposal['id']}/confirm")
    assert rejected.status_code == 403, rejected.text
    assert repo.get_creation_task("local", "task").current_timeline_version_id == "linked-base"
    assert repo.get_edit_proposal("local", proposal["id"]).status == "proposed"
    assert repo.list_edit_operations("local", "task") == []
    assert repo.get_creation_task_montage_plan("local", "task")["audio_timeline"] == plan.audio_timeline.model_dump()


def test_real_compound_preview_sql_failure_keeps_both_objects_uncommitted(linked_state):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    with repo._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_compound_proposal BEFORE INSERT ON edit_proposals
            BEGIN SELECT RAISE(ABORT, 'qa_compound_proposal_refusal'); END""")
        before = tuple(conn.iterdump())
    result = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/creation-tasks/task/edits/preview?draft=true", json={
            "raw_command": "当前镜头亮一点，同时配音小一点", "context_stage": "montage",
            "selected_object_refs": ["clip:clip_2"],
        })
    assert result.status_code == 500, result.text
    with repo._connect() as conn:
        assert tuple(conn.iterdump()) == before


def test_injected_wrong_empty_source_scope_cannot_adopt_actual_picture_change(linked_state):
    import app.main as main
    from fastapi.testclient import TestClient

    repo, task, plan, track, identity, verifier = linked_state
    client = TestClient(main.app)
    ready = client.post("/api/creation-tasks/task/edits/preview?draft=true", json={
        "raw_command": "当前镜头亮一点，同时配音小一点", "context_stage": "montage",
        "selected_object_refs": ["clip:clip_2"],
    })
    assert ready.status_code == 200, ready.text
    original = repo.get_edit_proposal("local", ready.json()["proposal"]["id"])
    intent = repo.get_edit_intent("local", original.intent_id)
    sealed = {**intent.constraints["affected_source_scope"], "clip_ids": [], "source_facts": [], "clip_facts": []}
    bad_intent = repo.create_edit_intent(replace(intent, id="wrong-empty-intent",
        constraints={**intent.constraints, "affected_source_scope": sealed}), source=None)
    bad_proposal = repo.create_edit_proposal(replace(original, id="wrong-empty-proposal", intent_id=bad_intent.id))
    repo._save_asset(replace(repo.get_asset("local", "neighbour"), owner_user_id="another-member", visibility="private"))
    rejected = client.post(f"/api/creation-tasks/task/edits/{bad_proposal.id}/confirm")
    assert rejected.status_code == 409, rejected.text
    assert "实际影响的镜头不一致" in rejected.text
    assert repo.get_creation_task("local", "task").current_timeline_version_id == "linked-base"
    assert repo.get_edit_proposal("local", bad_proposal.id).status == "proposed"
    assert repo.list_edit_operations("local", "task") == []
    assert repo.get_creation_task_montage_plan("local", "task")["clips"] == [asdict(clip) for clip in plan.clips]
