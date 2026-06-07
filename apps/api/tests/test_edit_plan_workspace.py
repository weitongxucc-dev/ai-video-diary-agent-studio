from datetime import date
import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.models import EpisodeStatus
from app.repository import EpisodeRepository


client = TestClient(main_module.app)


def test_edit_plan_endpoint_returns_real_workspace_from_episode_plan(tmp_path):
    video_output = tmp_path / "processed"
    video_output.mkdir()
    source_video = video_output / "talk_preview.mp4"
    output_video = video_output / "talk_preview_dedup.mp4"
    edit_plan_path = video_output / "talk_preview_dedup.edit_plan.json"
    report_path = video_output / "talk_preview_dedup_report.json"
    source_video.write_bytes(b"source video")
    output_video.write_bytes(b"processed video")
    edit_plan_path.write_text(
        json.dumps(
            {
                "strategy": "whisper_retake_dedup",
                "removed_retakes": [
                    {
                        "remove": {"start": 1.0, "end": 2.0},
                        "keep": {"start": 3.0, "end": 4.0},
                        "confidence": 0.95,
                        "reason": "repeated_take",
                    }
                ],
                "keep_ranges": [
                    {"start": 0.0, "end": 1.0, "duration": 1.0},
                    {"start": 2.0, "end": 6.0, "duration": 4.0},
                ],
                "source_video": str(source_video),
                "output_video": str(output_video),
                "report_path": str(report_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "status": "applied",
                "source_video": str(source_video),
                "output_video": str(output_video),
                "auto_removed": [
                    {
                        "remove": {"start": 1.0, "end": 2.0},
                        "keep": {"start": 3.0, "end": 4.0},
                        "confidence": 0.95,
                        "reason": "repeated_take",
                    }
                ],
                "review_only": [
                    {
                        "remove": {"start": 4.5, "end": 5.0},
                        "keep": {"start": 5.0, "end": 5.5},
                        "confidence": 0.62,
                        "reason": "possible_retake",
                    }
                ],
                "message": "High-confidence repeated takes were removed",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    main_module.settings = Settings(video_output_path=str(video_output))
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 6))
    episode = main_module.repo.create()
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_processed_file_name = output_video.name
    episode.video_processed_path = str(output_video)
    episode.video_edit_plan_path = str(edit_plan_path)
    episode.video_dedup_report_path = str(report_path)
    episode.draft_package_name = "AI视频日记_20260606_talk"
    episode.draft_package_path = str(tmp_path / "drafts" / episode.draft_package_name)
    main_module.repo.save(episode)

    response = client.get(f"/api/episodes/{episode.id}/edit-plan")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["status"] == "ready"
    assert body["strategy"] == "whisper_retake_dedup"
    assert body["output_video"] == {
        "file_name": "talk_preview_dedup.mp4",
        "path": str(output_video),
        "url": "/api/videos/processed/talk_preview_dedup.mp4",
    }
    assert body["duration_seconds"] == 6.0
    assert body["jianying"]["status"] == "draft_ready"
    assert body["jianying"]["package_name"] == "AI视频日记_20260606_talk"
    assert {segment["status"] for segment in body["segments"]} == {
        "kept",
        "auto_removed",
        "manual_review",
    }
    assert body["decisions"][0]["command"] == "remove_retake"
    assert body["decisions"][0]["confidence"] == 0.95
    assert body["markers"][0]["label"] == "保留片段 1"


def test_edit_plan_segment_decision_is_persisted_and_returned(tmp_path):
    video_output = tmp_path / "processed"
    video_output.mkdir()
    source_video = video_output / "talk_preview.mp4"
    output_video = video_output / "talk_preview_dedup.mp4"
    edit_plan_path = video_output / "talk_preview_dedup.edit_plan.json"
    source_video.write_bytes(b"source video")
    output_video.write_bytes(b"processed video")
    edit_plan_path.write_text(
        json.dumps(
            {
                "strategy": "whisper_retake_dedup",
                "removed_retakes": [
                    {
                        "remove": {"start": 1.0, "end": 2.0},
                        "keep": {"start": 3.0, "end": 4.0},
                        "confidence": 0.91,
                        "reason": "repeated_take",
                    }
                ],
                "keep_ranges": [{"start": 0.0, "end": 5.0, "duration": 5.0}],
                "source_video": str(source_video),
                "output_video": str(output_video),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    main_module.settings = Settings(video_output_path=str(video_output))
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 6))
    episode = main_module.repo.create()
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_processed_file_name = output_video.name
    episode.video_processed_path = str(output_video)
    episode.video_edit_plan_path = str(edit_plan_path)
    main_module.repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/edit-plan/segments/retake-1/decision",
        json={"action": "keep", "reason": "这次虽然像重录，但我要保留上下文"},
    )

    assert response.status_code == 200
    body = response.json()
    retake_segment = next(segment for segment in body["segments"] if segment["id"] == "retake-1")
    assert retake_segment["user_decision"] == {
        "action": "keep",
        "reason": "这次虽然像重录，但我要保留上下文",
        "created_at": retake_segment["user_decision"]["created_at"],
    }
    saved_payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assert saved_payload["user_decisions"]["retake-1"]["action"] == "keep"


def test_edit_plan_segment_decision_can_be_reverted(tmp_path):
    video_output = tmp_path / "processed"
    video_output.mkdir()
    source_video = video_output / "talk_preview.mp4"
    output_video = video_output / "talk_preview_dedup.mp4"
    edit_plan_path = video_output / "talk_preview_dedup.edit_plan.json"
    source_video.write_bytes(b"source video")
    output_video.write_bytes(b"processed video")
    edit_plan_path.write_text(
        json.dumps(
            {
                "strategy": "whisper_retake_dedup",
                "removed_retakes": [
                    {
                        "remove": {"start": 1.0, "end": 2.0},
                        "keep": {"start": 3.0, "end": 4.0},
                        "confidence": 0.91,
                        "reason": "repeated_take",
                    }
                ],
                "source_video": str(source_video),
                "output_video": str(output_video),
                "user_decisions": {
                    "retake-1": {
                        "action": "keep",
                        "reason": "临时保留",
                        "created_at": "2026-06-06T09:00:00+00:00",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    main_module.settings = Settings(video_output_path=str(video_output))
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 6))
    episode = main_module.repo.create()
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_processed_file_name = output_video.name
    episode.video_processed_path = str(output_video)
    episode.video_edit_plan_path = str(edit_plan_path)
    main_module.repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/edit-plan/segments/retake-1/revert"
    )

    assert response.status_code == 200
    body = response.json()
    retake_segment = next(segment for segment in body["segments"] if segment["id"] == "retake-1")
    assert retake_segment["user_decision"] is None
    saved_payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assert saved_payload["user_decisions"] == {}


def test_edit_plan_endpoint_returns_missing_state_without_processed_plan():
    main_module.settings = Settings()
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 6))
    episode = main_module.repo.create()

    response = client.get(f"/api/episodes/{episode.id}/edit-plan")

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["status"] == "missing"
    assert body["segments"] == []
    assert body["message"] == "还没有可视化剪辑计划，请先处理视频。"


def test_processed_video_endpoint_serves_only_files_from_output_dir(tmp_path):
    video_output = tmp_path / "processed"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    main_module.settings = Settings(video_output_path=str(video_output))

    response = client.get("/api/videos/processed/talk_preview.mp4")

    assert response.status_code == 200
    assert response.content == b"processed video"

    traversal = client.get("/api/videos/processed/../secret.mp4")
    assert traversal.status_code == 404


def test_create_draft_applies_edit_plan_user_decisions_to_confirmed_video(
    tmp_path,
    monkeypatch,
):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    draft_output.mkdir()
    source_video = video_output / "talk_preview.mp4"
    processed_video = video_output / "talk_preview_dedup.mp4"
    edit_plan_path = video_output / "talk_preview_dedup.edit_plan.json"
    source_video.write_bytes(b"source video")
    processed_video.write_bytes(b"processed video")
    edit_plan_path.write_text(
        json.dumps(
            {
                "strategy": "whisper_retake_dedup",
                "removed_retakes": [
                    {
                        "remove": {"start": 1.0, "end": 2.0},
                        "keep": {"start": 3.0, "end": 4.0},
                        "confidence": 0.91,
                        "reason": "repeated_take",
                    }
                ],
                "keep_ranges": [
                    {"start": 0.0, "end": 1.0, "duration": 1.0},
                    {"start": 2.0, "end": 6.0, "duration": 4.0},
                ],
                "source_video": str(source_video),
                "output_video": str(processed_video),
                "user_decisions": {
                    "retake-1": {
                        "action": "keep",
                        "reason": "这次不是废片，要保留上下文",
                        "created_at": "2026-06-06T09:00:00+00:00",
                    },
                    "keep-2": {
                        "action": "remove",
                        "reason": "后半段偏题，剪掉",
                        "created_at": "2026-06-06T09:01:00+00:00",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cut_commands: list[list[str]] = []

    def runner(command: list[str]) -> None:
        cut_commands.append(command)
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"confirmed video")

    settings = Settings(
        video_output_path=str(video_output),
        draft_output_path=str(draft_output),
        jianying_draft_root_path=str(tmp_path / "jianying"),
    )
    main_module.settings = settings
    main_module.repo = EpisodeRepository(today_provider=lambda: date(2026, 6, 6))
    main_module.draft_workspace = main_module.DraftWorkspace(settings)
    monkeypatch.setattr(main_module, "edit_plan_apply_runner", runner, raising=False)
    episode = main_module.repo.create()
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_processed_file_name = processed_video.name
    episode.video_processed_path = str(processed_video)
    episode.video_edit_plan_path = str(edit_plan_path)
    main_module.repo.save(episode)

    response = client.post(f"/api/episodes/{episode.id}/create-draft")

    assert response.status_code == 200
    body = response.json()
    assert body["video_processed_file_name"] == "talk_preview_confirmed.mp4"
    assert Path(body["video_processed_path"]).read_bytes() == b"confirmed video"
    assert cut_commands
    assert "trim=start=0.0:end=2.0" in " ".join(cut_commands[0])
    updated_payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assert updated_payload["confirmed_output_video"] == body["video_processed_path"]
    assert updated_payload["confirmed_keep_ranges"] == [
        {"start": 0.0, "end": 2.0, "duration": 2.0}
    ]
    manifest = json.loads(
        (Path(body["draft_package_path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["video"]["file_name"] == "talk_preview_confirmed.mp4"
