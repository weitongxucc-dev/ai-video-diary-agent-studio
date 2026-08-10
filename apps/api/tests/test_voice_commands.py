import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.models import EpisodeStatus
from app.repository import EpisodeRepository
from app.services import EpisodeService
from app.workflow import LangGraphEpisodeWorkflow

client = TestClient(main_module.app)


def reset_episode_runtime(tmp_path, today=date(2026, 6, 7)):
    main_module.settings = Settings(
        video_output_path=str(tmp_path / "processed"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "agent.sqlite3"),
    )
    Path(main_module.settings.video_output_path).mkdir(parents=True)
    Path(main_module.settings.draft_output_path).mkdir(parents=True)
    main_module.repo = EpisodeRepository(today_provider=lambda: today)
    main_module.service = EpisodeService()
    main_module.workflow = LangGraphEpisodeWorkflow(main_module.service)
    main_module.draft_workspace = main_module.DraftWorkspace(main_module.settings)
    return main_module.repo.today()


def test_voice_command_preview_does_not_mutate_episode(tmp_path):
    episode = reset_episode_runtime(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/preview",
        json={"transcript": "继续今天的流程"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["intent"] == "continue_flow"
    assert body["can_execute"] is True
    assert body["operations"] == [
        {
            "kind": "generate_topics",
            "label": "生成今日选题",
            "params": {},
        }
    ]
    assert body["result_episode"] is None
    assert main_module.repo.get(episode.id).status == EpisodeStatus.CREATED


def test_voice_command_execute_continue_flow_generates_topics(tmp_path):
    episode = reset_episode_runtime(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "继续今天的流程"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "continue_flow"
    assert body["next_url"] == "/topics"
    assert body["result_episode"]["status"] == "topics_ready"
    assert len(body["result_episode"]["topics"]) == 4
    assert main_module.repo.get(episode.id).status == EpisodeStatus.TOPICS_READY


def test_voice_command_execute_regenerates_named_script_section(tmp_path):
    episode = reset_episode_runtime(tmp_path)
    episode = main_module.workflow.run("generate_topics", episode)
    episode = main_module.workflow.run("approve_topic", episode, topic_id=episode.topics[0].id)
    episode = main_module.workflow.run("generate_script", episode)
    main_module.repo.save(episode)
    section = episode.script.sections[0]

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "把开头重写得更像真实日记"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "regenerate_script_section"
    assert body["operations"][0]["params"]["section_id"] == section.id
    updated_episode = main_module.repo.get(episode.id)
    assert updated_episode.script.sections[0].content != section.content
    assert updated_episode.script_versions[-1].changed_section_ids == [section.id]
    assert updated_episode.script_versions[-1].reason == "把开头重写得更像真实日记"


def test_voice_command_execute_updates_edit_plan_segment_decision(tmp_path):
    episode = reset_episode_runtime(tmp_path)
    video_output = Path(main_module.settings.video_output_path)
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
    episode.status = EpisodeStatus.DRAFT_READY
    episode.video_processed_file_name = output_video.name
    episode.video_processed_path = str(output_video)
    episode.video_edit_plan_path = str(edit_plan_path)
    main_module.repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "把 retake-1 保留，因为这不是重复录制"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "edit_plan_segment_decision"
    assert body["operations"][0]["params"] == {
        "segment_id": "retake-1",
        "action": "keep",
        "reason": "这不是重复录制",
    }
    retake_segment = next(
        segment for segment in body["result_edit_plan"]["segments"] if segment["id"] == "retake-1"
    )
    assert retake_segment["user_decision"]["action"] == "keep"
    saved_payload = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    assert saved_payload["user_decisions"]["retake-1"]["reason"] == "这不是重复录制"
