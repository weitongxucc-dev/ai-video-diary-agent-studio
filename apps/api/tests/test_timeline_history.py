"""查看历史时间线版本：只读旧版本自己的快照与产物，不动当前版本。"""

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.artifact_store import ArtifactStore
from app.montage import (CreationTaskMontagePlan, MontageClip, NarrationCue,
                         creation_task_montage_plan_to_payload)
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan
from app.creation.contracts import (
    CreationPlanVoiceReference,
    CreationTask,
    SubtitleCue,
    SubtitleTrack,
    TimelineVersion,
)

WORKSPACE_ID = "local"


def make_plan(plan_id: str, name: str) -> CreationPlan:
    return CreationPlan(
        id=plan_id, workspace_id=WORKSPACE_ID, goal="一条视频", name=name, target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=30,
        knowledge_references=[], source_references=[],
        selected_voice=CreationPlanVoiceReference(
            profile_id="voice_1", version="v1", person_profile_id="person_1", person_version="v1",
            person_authorization_id="person_auth_1", person_authorization_version="v1",
            authorization_id="voice_auth_1", authorization_version="v1",
            language="zh-CN", model_id="test"),
        pronunciation_references=[], approved_asset_count=0, approved_clip_count=0,
        material_rule_summary="测试", model_snapshot=[], confirmation_points=[], blockers=[],
        preflight_fingerprint="test",
    )


def montage_for(task_id: str, script_id: str, plan_id: str, *, cue_text: str,
                output_path: str | None, subtitle_path: str | None) -> CreationTaskMontagePlan:
    return CreationTaskMontagePlan(
        id=plan_id, workspace_id=WORKSPACE_ID, task_id=task_id,
        script_id=script_id, script_title="历史版本文案", status="rendered" if output_path else "ready",
        cues=[NarrationCue("cue_1", "section_1", cue_text, 1.0),
              NarrationCue("cue_2", "section_2", f"{cue_text}·第二句", 1.0)],
        clips=[MontageClip("clip_1", "cue_1", "asset_1", "segment_1", "workspace://source.mp4",
                           0, 1, 0, 1, 1, [f"{cue_text}对应镜头"], False),
               MontageClip("clip_2", "cue_2", "asset_2", "segment_2", "workspace://source.mp4",
                           1, 2, 1, 2, 1, [], False)],
        duration_seconds=2, low_confidence_clip_count=0,
        narration_audio_path="narration://artifact/voice_cache",
        output_path=output_path, subtitle_path=subtitle_path,
    )


def persist_task(repo: ProductionRepository, task: CreationTask, script_id: str,
                 montage: CreationTaskMontagePlan, *, insert_script: bool = True) -> None:
    now = datetime.now(timezone.utc).isoformat()
    script = {"id": script_id, "workspace_id": WORKSPACE_ID, "task_id": task.id,
              "run_id": f"run_{task.id}", "topic_id": "topic_1", "title": "历史版本文案",
              "language": "zh-CN",
              "sections": [{"id": "section_1", "title": "正文", "content": "第一段文案",
                            "visual_prompt": "现场", "evidence_reference_ids": ["source_1"]},
                           {"id": "section_2", "title": "正文", "content": "第二段文案",
                            "visual_prompt": "特写", "evidence_reference_ids": ["source_1"]}],
              "selection_status": "confirmed", "created_at": now, "updated_at": now,
              "confirmed_at": now}
    with repo._connect() as conn:
        if insert_script:
            conn.execute(
                "INSERT INTO creation_task_scripts (id,workspace_id,task_id,run_id,payload,updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (script_id, WORKSPACE_ID, task.id, script["run_id"], json.dumps(script), now))
        conn.execute("UPDATE creation_tasks SET payload=? WHERE workspace_id=? AND id=?",
                     (json.dumps(asdict(task), default=str), WORKSPACE_ID, task.id))
        conn.execute(
            "INSERT INTO creation_task_montage_plans (id,workspace_id,task_id,payload,updated_at)"
            " VALUES (?,?,?,?,?)",
            (montage.id, WORKSPACE_ID, task.id,
             json.dumps(creation_task_montage_plan_to_payload(montage)), now))


@pytest.fixture
def history_task(tmp_path, monkeypatch):
    from test_production_foundation import edit_adoption_no_execution
    edit_adoption_no_execution(monkeypatch)
    repo = ProductionRepository(database_path=tmp_path / "production.sqlite3",
                                video_library_path=tmp_path / "library")
    store = ArtifactStore(tmp_path / "artifacts")

    # 任务一：v1 是旧版本（有成片 + 字幕轨），v2 是当前版本（产物未生成）。
    plan = make_plan("plan_history", "历史版本测试")
    task = CreationTask(id="task_history", workspace_id=WORKSPACE_ID, plan_id=plan.id,
                        name="历史版本测试", scenario="knowledge_explanation")
    repo.plan_confirmation.confirm_creation_plan(plan, task)
    task = repo.task_records.get_creation_task(WORKSPACE_ID, task.id)
    task.confirmed_script_id = "script_history"
    task.montage_plan_id = "montage_history"
    task.stage = "montage"
    current_montage = montage_for(task.id, "script_history", "montage_history",
                                  cue_text="当前版本文案",
                                  output_path="artifacts/task_history/v2.mp4",
                                  subtitle_path=None)
    persist_task(repo, task, "script_history", current_montage)

    # 旧版本只存在于版本快照里：混剪计划每任务只有一份，版本快照是不可变副本。
    old_montage = montage_for(task.id, "script_history", "montage_history",
                              cue_text="旧版本文案",
                              output_path="artifacts/task_history/v1.mp4",
                              subtitle_path=None)
    old_payload = creation_task_montage_plan_to_payload(old_montage)
    repo.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline_history_v1", workspace_id=WORKSPACE_ID, task_id=task.id,
        parent_version_id=None, sequence=1,
        snapshot={"montage": old_payload,
                  "subtitle_track_ids": ["subtitle_history_v1"],
                  "narration_segments": [{"id": "segment_1", "text": "旧版本文案",
                                          "duration_seconds": 1.0, "status": "ready"}]},
        created_by_operation_id=None))
    repo.subtitle_records.create_subtitle_track(SubtitleTrack(
        id="subtitle_history_v1", workspace_id=WORKSPACE_ID, task_id=task.id,
        timeline_version_id="timeline_history_v1", language="zh-CN", source_language="zh-CN",
        style={}, cues=[SubtitleCue("subtitle_cue_1", "cue_1", "旧版本文案", 0, 1)],
        generation_method="timeline", model_id=None,
        artifact_ref="artifacts/task_history/v1.srt", transform_id="transform_history_v1",
        status="valid"))
    store.publish_bytes("artifacts/task_history/v1.mp4", b"old-version-video-bytes")
    store.publish_bytes("artifacts/task_history/v1.srt", "旧版本字幕\n".encode())

    repo.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline_history_v2", workspace_id=WORKSPACE_ID, task_id=task.id,
        parent_version_id="timeline_history_v1", sequence=2,
        snapshot={"montage": creation_task_montage_plan_to_payload(current_montage),
                  "narration_segments": [{"id": "segment_1", "text": "当前版本文案",
                                          "duration_seconds": 1.0, "status": "ready"}]},
        created_by_operation_id=None))

    # 任务二：只有它自己的版本，用来验证跨任务读取被拒。
    other_plan = make_plan("plan_other", "另一个任务")
    other_task = CreationTask(id="task_other", workspace_id=WORKSPACE_ID, plan_id=other_plan.id,
                              name="另一个任务", scenario="knowledge_explanation")
    repo.plan_confirmation.confirm_creation_plan(other_plan, other_task)
    other_task = repo.task_records.get_creation_task(WORKSPACE_ID, other_task.id)
    other_task.confirmed_script_id = "script_other"
    other_task.montage_plan_id = "montage_other"
    other_montage = montage_for(other_task.id, "script_other", "montage_other",
                                cue_text="别的任务文案",
                                output_path=None, subtitle_path=None)
    persist_task(repo, other_task, "script_other", other_montage)
    repo.timeline_records.create_timeline_version(TimelineVersion(
        id="timeline_other_v1", workspace_id=WORKSPACE_ID, task_id=other_task.id,
        parent_version_id=None, sequence=1,
        snapshot={"montage": creation_task_montage_plan_to_payload(other_montage)},
        created_by_operation_id=None))

    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(main_module, "artifact_store", store)
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings,
        video_output_path=str(tmp_path / 'outputs'), temporary_root_path=str(tmp_path / 'temp')))
    monkeypatch.setattr(main_module, "creation_execution_mode", "legacy")
    return repo, task.id, TestClient(main_module.app)


def test_version_list_shows_sequence_time_and_current_marker(history_task):
    _, task_id, client = history_task
    listed = client.get(f"/api/creation-tasks/{task_id}/timeline/versions")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["task_id"] == task_id
    assert body["current_version_id"] == "timeline_history_v2"
    assert [item["sequence"] for item in body["items"]] == [2, 1]
    assert [item["is_current"] for item in body["items"]] == [True, False]
    assert body["items"][0]["parent_version_id"] == "timeline_history_v1"
    assert body["items"][1]["parent_version_id"] is None
    old = body["items"][1]
    assert datetime.fromisoformat(old["created_at"]).tzinfo is not None
    assert old["shot_count"] == 2 and old["subtitle_track_count"] == 1
    assert old["narration_segment_count"] == 1


def test_old_version_detail_reads_its_own_snapshot_without_touching_current(history_task):
    repo, task_id, client = history_task
    detail = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["version"]["sequence"] == 1 and body["version"]["is_current"] is False
    assert [shot["narration_text"] for shot in body["shots"]] == ["旧版本文案", "旧版本文案·第二句"]
    assert [shot["description"] for shot in body["shots"]] == ["旧版本文案对应镜头", None]
    assert (body["shots"][0]["start_seconds"], body["shots"][0]["end_seconds"]) == (0.0, 1.0)
    assert [segment["text"] for segment in body["narration_segments"]] == ["旧版本文案"]
    assert [track["id"] for track in body["subtitle_tracks"]] == ["subtitle_history_v1"]
    assert [cue["text"] for cue in body["subtitle_tracks"][0]["cues"]] == ["旧版本文案"]

    # 当前版本与指针都不得因为查看旧版本而变化。
    assert repo.task_records.get_creation_task(WORKSPACE_ID, task_id).current_timeline_version_id == "timeline_history_v2"
    listed = client.get(f"/api/creation-tasks/{task_id}/timeline/versions").json()
    assert listed["current_version_id"] == "timeline_history_v2"
    assert repo.task_records.get_creation_task(WORKSPACE_ID, task_id).montage_plan_id == "montage_history"
    assert repo.get_creation_task_montage_plan(WORKSPACE_ID, task_id)["cues"][0]["text"] == "当前版本文案"
    assert repo.get_creation_task_montage_plan(WORKSPACE_ID, task_id)["output_path"] == "artifacts/task_history/v2.mp4"


def test_history_artifacts_report_available_not_generated_and_missing(history_task):
    _, task_id, client = history_task
    old = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v1").json()
    states = {item["kind"]: item["status"] for item in old["artifacts"]}
    assert states == {"video": "available", "subtitles": "available", "cover": "not_generated"}
    assert all(item["reason"] is None for item in old["artifacts"] if item["status"] == "available")
    cover = next(item for item in old["artifacts"] if item["kind"] == "cover")
    assert cover["url"] is None and "没有生成封面" in cover["reason"]
    for item in old["artifacts"]:
        if item["status"] == "available":
            assert item["url"].startswith("/api/creation-tasks/")
            assert "task_history/v1.mp4" not in item["url"]

    current = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v2").json()
    current_states = {item["kind"]: item["status"] for item in current["artifacts"]}
    assert current_states == {"video": "missing", "subtitles": "not_generated", "cover": "not_generated"}
    video = next(item for item in current["artifacts"] if item["kind"] == "video")
    assert video["url"] is None and "受控产物目录" in video["reason"]

    assert client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v1/artifacts/video"
    ).content == b"old-version-video-bytes"
    unavailable = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v1/artifacts/cover")
    assert unavailable.status_code == 404 and "没有生成封面" in unavailable.json()["detail"]


def test_history_rejects_another_task_version_and_unknown_version(history_task):
    _, task_id, client = history_task
    cross_task = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_other_v1")
    assert cross_task.status_code == 404
    cross_media = client.get(
        f"/api/creation-tasks/{task_id}/timeline/versions/timeline_other_v1/artifacts/video")
    assert cross_media.status_code == 404
    unknown = client.get(f"/api/creation-tasks/{task_id}/timeline/versions/timeline_nope")
    assert unknown.status_code == 404
    assert client.get("/api/creation-tasks/task_nope/timeline/versions").status_code == 404


def test_history_detail_preserves_original_operation_from_list(history_task):
    from app.creation.contracts import EditIntent,EditProposal,EditOperation
    from app.graphic_overlays import TextOverlay
    repo,task_id,client=history_task
    base=repo.timeline_records.get_timeline_version(WORKSPACE_ID,'timeline_history_v2')
    overlay=TextOverlay(id='history-overlay',text='公开演示',start_seconds=0,end_seconds=1,x=.48,font_size=24).model_dump()
    snapshot=json.loads(json.dumps(base.snapshot))
    snapshot['montage'].update(graphic_overlays=[overlay],status='ready',output_path=None,subtitle_path=None)
    payload={'base_version_id':base.id,'graphic_overlays':[overlay]}
    intent=repo.edit_records.create_edit_intent(EditIntent('history-overlay-intent',WORKSPACE_ID,task_id,'添加图文标题','montage',
        ['overlay:history-overlay'],None,'edit_graphic_overlay',{}, {},1,'parsed'))
    proposal=repo.edit_records.create_edit_proposal(EditProposal('history-overlay-proposal',WORKSPACE_ID,task_id,intent.id,
        '添加图文标题',['overlay:history-overlay'],{'operation_type':'EditGraphicOverlay','payload':payload},[],{},[]))
    now=datetime.now(timezone.utc)
    operation=EditOperation('history-overlay-operation',WORKSPACE_ID,task_id,proposal.id,'EditGraphicOverlay',payload,
        base.id,'timeline_history_overlay','applied',created_at=now,applied_at=now)
    version=TimelineVersion(operation.result_version_id,WORKSPACE_ID,task_id,base.id,3,snapshot,operation.id)
    repo.edit_adoption.confirm(WORKSPACE_ID,task_id,proposal.id,operation=operation,result_version=version,
        montage_plan_id=snapshot['montage']['id'],montage_payload=snapshot['montage'])
    later=replace(version,id='timeline_history_after_overlay',sequence=4,parent_version_id=version.id,created_by_operation_id=None)
    repo.timeline_records.create_timeline_version(later)
    listed=client.get(f'/api/creation-tasks/{task_id}/timeline/versions')
    detail=client.get(f'/api/creation-tasks/{task_id}/timeline/versions/{version.id}')
    assert listed.status_code==200 and detail.status_code==200
    summary=next(item for item in listed.json()['items'] if item['id']==version.id)
    for key,expected in [('operation_id',operation.id),('operation_type','EditGraphicOverlay')]:
        assert detail.json()['version'][key]==summary[key]==expected
    assert repo.task_records.get_creation_task(WORKSPACE_ID,task_id).current_timeline_version_id==later.id


def test_history_detail_without_source_operation_keeps_null(history_task):
    _,task_id,client=history_task
    response=client.get(f'/api/creation-tasks/{task_id}/timeline/versions/timeline_history_v1')
    assert response.status_code==200
    assert response.json()['version']['operation_id'] is None
    assert response.json()['version']['operation_type'] is None
