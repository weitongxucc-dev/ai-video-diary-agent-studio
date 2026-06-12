from datetime import date
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

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


def episode_with_approved_topic(tmp_path):
    episode = reset_episode_runtime(tmp_path)
    episode = main_module.workflow.run("generate_topics", episode)
    episode = main_module.workflow.run("approve_topic", episode, topic_id=episode.topics[0].id)
    return main_module.repo.save(episode)


def episode_with_script(tmp_path):
    episode = episode_with_approved_topic(tmp_path)
    episode = main_module.workflow.run("generate_script", episode)
    return main_module.repo.save(episode)


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
    assert len(body["result_episode"]["topics"]) == 5
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


def test_voice_command_does_not_remove_when_delete_is_negated(tmp_path):
    episode = reset_episode_runtime(tmp_path)

    for transcript in ("不要删除 retake-1", "别删除 retake-1", "不要剪掉 retake-1"):
        response = client.post(
            f"/api/episodes/{episode.id}/voice-command/preview",
            json={"transcript": transcript},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["intent"] != "edit_plan_segment_decision" or all(
            operation["params"].get("action") != "remove" for operation in body["operations"]
        )


@pytest.mark.parametrize(
    ("setup_episode", "expected_summary", "expected_next_url", "expected_operation"),
    [
        (
            lambda tmp_path: reset_episode_runtime(tmp_path),
            "继续今天的流程：生成今日选题。",
            "/topics",
            "generate_topics",
        ),
        (
            lambda tmp_path: episode_with_approved_topic(tmp_path),
            "继续今天的流程：生成口播稿。",
            "/script",
            "generate_script",
        ),
        (
            lambda tmp_path: episode_with_script(tmp_path),
            "继续今天的流程：确认口播稿并进入录制。",
            "/video",
            "approve_script",
        ),
    ],
)
def test_voice_command_preview_continue_flow_ready_steps(
    tmp_path,
    setup_episode,
    expected_summary,
    expected_next_url,
    expected_operation,
):
    episode = setup_episode(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/preview",
        json={"transcript": "继续今天的流程"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["intent"] == "continue_flow"
    assert body["summary"] == expected_summary
    assert body["can_execute"] is True
    assert body["next_url"] == expected_next_url
    assert [operation["kind"] for operation in body["operations"]] == [expected_operation]
    assert body["result_episode"] is None


def test_voice_command_execute_continue_flow_blocks_when_topic_needs_approval(tmp_path):
    episode = reset_episode_runtime(tmp_path)
    episode = main_module.workflow.run("generate_topics", episode)
    main_module.repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "继续今天的流程"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["intent"] == "blocked"
    assert body["can_execute"] is False
    assert body["next_url"] == "/topics"
    assert body["operations"] == []
    assert body["result_episode"] is None
    saved = main_module.repo.get(episode.id)
    assert saved.status == EpisodeStatus.TOPICS_READY
    assert saved.selected_topic_id is None


def test_voice_command_execute_generates_script_from_confirmed_topic(tmp_path):
    episode = episode_with_approved_topic(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "生成脚本"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "generate_script"
    assert body["next_url"] == "/script"
    assert body["operations"] == [
        {"kind": "generate_script", "label": "生成口播稿", "params": {}}
    ]
    assert body["result_episode"]["status"] == EpisodeStatus.SCRIPT_READY
    assert body["result_episode"]["script"]["sections"]
    saved = main_module.repo.get(episode.id)
    assert saved.script is not None
    assert saved.script_versions[-1].source == "ai_generated"


def test_voice_command_execute_regenerates_full_script_with_reason(tmp_path):
    episode = episode_with_script(tmp_path)
    original_script_id = episode.script.id

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "重新生成口播稿，语气更松弛"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "regenerate_script"
    assert body["operations"][0]["params"] == {"reason": "重新生成口播稿，语气更松弛"}
    saved = main_module.repo.get(episode.id)
    assert saved.script.id != original_script_id
    assert saved.script_versions[-1].source == "full_regeneration"
    assert saved.script_versions[-1].reason == "重新生成口播稿，语气更松弛"


def test_voice_command_execute_create_draft_from_processed_video(tmp_path):
    episode = reset_episode_runtime(tmp_path)
    processed_video = Path(main_module.settings.video_output_path) / "talk_preview.mp4"
    processed_video.write_bytes(b"processed video")
    episode.video_processed_file_name = processed_video.name
    episode.video_processed_path = str(processed_video)
    main_module.repo.save(episode)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "生成剪映草稿"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["intent"] == "create_draft"
    assert body["next_url"] == "/draft"
    assert body["operations"] == [
        {"kind": "create_draft", "label": "生成剪映草稿", "params": {}}
    ]
    result_episode = body["result_episode"]
    assert result_episode["status"] == EpisodeStatus.DRAFT_READY
    assert result_episode["draft_package_name"].startswith("AI视频日记_")
    manifest = json.loads(
        (Path(result_episode["draft_package_path"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["video"]["file_name"] == "talk_preview.mp4"


def test_voice_command_blocks_create_draft_without_processed_video(tmp_path):
    episode = reset_episode_runtime(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "生成剪映草稿"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["summary"] == "还没有处理后视频，不能生成剪映草稿。"
    assert body["next_url"] == "/video"
    assert body["can_execute"] is False
    assert body["result_episode"] is None
    saved = main_module.repo.get(episode.id)
    assert saved.status == EpisodeStatus.CREATED
    assert saved.draft_package_name is None


def test_voice_command_preview_rewrites_ending_section_by_name(tmp_path):
    episode = episode_with_script(tmp_path)
    ending = episode.script.sections[-1]

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/preview",
        json={"transcript": "把结尾改写得更真实"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["intent"] == "regenerate_script_section"
    assert body["summary"] == f"重写口播段落：{ending.title}。"
    assert body["operations"] == [
        {
            "kind": "regenerate_script_section",
            "label": f"重写段落：{ending.title}",
            "params": {
                "section_id": ending.id,
                "reason": "把结尾改写得更真实",
            },
        }
    ]


def test_voice_command_blocks_section_rewrite_without_script(tmp_path):
    episode = episode_with_approved_topic(tmp_path)

    response = client.post(
        f"/api/episodes/{episode.id}/voice-command/execute",
        json={"transcript": "把开头重写得更犀利"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["summary"] == "还没有口播稿，不能重写段落。"
    assert body["next_url"] == "/script"
    assert body["operations"] == []
    assert body["result_episode"] is None
    assert main_module.repo.get(episode.id).script is None
