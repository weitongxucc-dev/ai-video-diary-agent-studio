import json
import sqlite3
from datetime import date

from app.models import (
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    ScriptVersion,
    ScriptVersionSource,
    SourceRef,
    TopicCandidate,
)
from app.repository import EpisodeRepository


def _script_package(text: str) -> ScriptPackage:
    return ScriptPackage(
        id="script_1",
        hook=f"hook {text}",
        sections=[ScriptSection("s1", "第一段", 12, f"content {text}")],
        title_options=[f"title {text}"],
        cover_copy=f"cover {text}",
        publish_copy=f"publish {text}",
        comment_prompt=f"comment {text}",
    )


def test_today_creates_new_episode_when_calendar_date_changes():
    current_date = date(2026, 5, 27)
    repo = EpisodeRepository(today_provider=lambda: current_date)

    first = repo.today()
    same_day = repo.today()

    assert same_day.id == first.id
    assert same_day.episode_date == date(2026, 5, 27)

    current_date = date(2026, 5, 28)
    next_day = repo.today()

    assert next_day.id != first.id
    assert next_day.day_number == 2
    assert next_day.episode_date == date(2026, 5, 28)


def test_sqlite_repository_persists_episode_state(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(
        today_provider=lambda: date(2026, 5, 28),
        database_path=db_path,
    )
    episode = repo.today()
    episode.topics.append(
        TopicCandidate(
            id="topic_1",
            title="AI 视频日记为什么需要闭环",
            core_viewpoint="闭环比单点工具更重要",
            reason="验证持久化能保存真实状态",
            audience="AI 创作者",
            outline=["选题", "录制", "复盘"],
            score=92,
            source_refs=[SourceRef("Obsidian", "闭环笔记", "notes/loop.md")],
        )
    )
    episode.selected_topic_id = "topic_1"
    episode.feedback_events.append(
        FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "方向对")
    )
    episode.video_dedup_report_path = "/tmp/raw_preview_dedup_report.json"
    repo.save(episode)

    reloaded = EpisodeRepository(
        today_provider=lambda: date(2026, 5, 28),
        database_path=db_path,
    )

    today = reloaded.today()
    assert today.id == episode.id
    assert today.selected_topic_id == "topic_1"
    assert today.topics[0].source_refs[0].ref == "notes/loop.md"
    assert today.feedback_events[0].value == FeedbackValue.UP
    assert today.video_dedup_report_path == "/tmp/raw_preview_dedup_report.json"


def test_sqlite_repository_restores_missing_video_dedup_report_path_as_none(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(database_path=db_path)
    episode = repo.create()

    with sqlite3.connect(db_path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload FROM episodes WHERE id = ?",
                (episode.id,),
            ).fetchone()[0]
        )
        payload.pop("video_dedup_report_path", None)
        conn.execute(
            "UPDATE episodes SET payload = ? WHERE id = ?",
            (json.dumps(payload), episode.id),
        )

    reloaded = EpisodeRepository(database_path=db_path)

    assert reloaded.get(episode.id).video_dedup_report_path is None


def test_repository_persists_script_versions_and_feedback_lifecycle(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 4),
        database_path=db_path,
    )
    episode = repo.today()
    version = ScriptVersion(
        id="sv_1",
        source=ScriptVersionSource.MANUAL_EDIT,
        script=_script_package("edited"),
        changed_section_ids=["s1"],
        reason="我改成自己的口吻",
    )
    event = FeedbackEvent(
        target_type="script_section",
        target_id="s1",
        value=FeedbackValue.DOWN,
        reason="太像营销文案",
        id="fb_1",
        active=False,
        revert_reason="误点",
    )
    episode.script = version.script
    episode.script_versions.append(version)
    episode.feedback_events.append(event)
    repo.save(episode)

    reloaded = EpisodeRepository(
        today_provider=lambda: date(2026, 6, 4),
        database_path=db_path,
    )
    restored = reloaded.today()

    assert restored.script_versions[0].id == "sv_1"
    assert restored.script_versions[0].source == ScriptVersionSource.MANUAL_EDIT
    assert restored.script_versions[0].script.sections[0].content == "content edited"
    assert restored.script_versions[0].changed_section_ids == ["s1"]
    assert restored.script_versions[0].reason == "我改成自己的口吻"
    assert restored.feedback_events[0].id == "fb_1"
    assert restored.feedback_events[0].active is False
    assert restored.feedback_events[0].revert_reason == "误点"


def test_repository_restores_old_feedback_events_as_active(tmp_path):
    db_path = tmp_path / "episodes.sqlite3"
    repo = EpisodeRepository(database_path=db_path)
    episode = repo.create()
    episode.feedback_events.append(
        FeedbackEvent("topic", "topic_1", FeedbackValue.UP, "方向对")
    )
    repo.save(episode)

    reloaded = EpisodeRepository(database_path=db_path)
    restored = reloaded.get(episode.id)

    assert restored.feedback_events[0].id.startswith("fb_")
    assert restored.feedback_events[0].active is True
    assert restored.feedback_events[0].reverted_at is None
    assert restored.feedback_events[0].revert_reason is None
    assert restored.script_versions == []
