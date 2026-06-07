from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.models import (
    Episode,
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
    ScriptVersion,
    ScriptVersionSource,
    SourceRef,
    TopicCandidate,
    VideoJobStatus,
    utc_now,
)


class EpisodeRepository:
    def __init__(
        self,
        today_provider: Callable[[], date] = date.today,
        database_path: str | Path | None = None,
    ) -> None:
        self._today_provider = today_provider
        self._episodes: dict[str, Episode] = {}
        self._episode_ids_by_date: dict[date, str] = {}
        self._database_path = Path(database_path).expanduser() if database_path else None
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_database()
            self._load_database()

    def create(self, episode_date: date | None = None) -> Episode:
        episode_date = episode_date or self._today_provider()
        episode = Episode(
            id=f"ep_{uuid4().hex[:8]}",
            day_number=len(self._episodes) + 1,
            episode_date=episode_date,
            status=EpisodeStatus.CREATED,
        )
        self._episodes[episode.id] = episode
        self._episode_ids_by_date[episode_date] = episode.id
        self._persist_episode(episode)
        return episode

    def today(self) -> Episode:
        today = self._today_provider()
        episode_id = self._episode_ids_by_date.get(today)
        if episode_id is None:
            return self.create(today)
        return self._episodes[episode_id]

    def get(self, episode_id: str) -> Episode:
        return self._episodes[episode_id]

    def all(self) -> list[Episode]:
        return sorted(
            self._episodes.values(),
            key=lambda episode: episode.created_at,
            reverse=True,
        )

    def save(self, episode: Episode) -> Episode:
        episode.updated_at = utc_now()
        self._episodes[episode.id] = episode
        self._episode_ids_by_date[episode.episode_date] = episode.id
        self._persist_episode(episode)
        return episode

    def _init_database(self) -> None:
        assert self._database_path is not None
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    episode_date TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _load_database(self) -> None:
        assert self._database_path is not None
        with sqlite3.connect(self._database_path) as conn:
            rows = conn.execute("SELECT payload FROM episodes").fetchall()
        for (payload,) in rows:
            episode = self._episode_from_dict(json.loads(payload))
            self._episodes[episode.id] = episode
            self._episode_ids_by_date[episode.episode_date] = episode.id

    def _persist_episode(self, episode: Episode) -> None:
        if self._database_path is None:
            return
        payload = json.dumps(self._episode_to_dict(episode), ensure_ascii=False)
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                    (id, episode_date, payload, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    episode.id,
                    episode.episode_date.isoformat(),
                    payload,
                    episode.updated_at.isoformat(),
                ),
            )

    def _episode_to_dict(self, episode: Episode) -> dict[str, object]:
        return {
            "id": episode.id,
            "day_number": episode.day_number,
            "episode_date": episode.episode_date.isoformat(),
            "status": episode.status.value,
            "intake_mode": episode.intake_mode.value,
            "content_template_id": episode.content_template_id,
            "topics": [
                {
                    "id": topic.id,
                    "title": topic.title,
                    "core_viewpoint": topic.core_viewpoint,
                    "reason": topic.reason,
                    "audience": topic.audience,
                    "outline": topic.outline,
                    "score": topic.score,
                    "source_refs": [
                        {
                            "source_type": source.source_type,
                            "title": source.title,
                            "ref": source.ref,
                        }
                        for source in topic.source_refs
                    ],
                    "feedback": topic.feedback.value if topic.feedback else None,
                }
                for topic in episode.topics
            ],
            "selected_topic_id": episode.selected_topic_id,
            "script": self._script_to_dict(episode.script) if episode.script else None,
            "script_versions": [
                self._script_version_to_dict(version)
                for version in episode.script_versions
            ],
            "video_source_file_name": episode.video_source_file_name,
            "video_source_file_names": episode.video_source_file_names,
            "video_processed_file_name": episode.video_processed_file_name,
            "video_processed_path": episode.video_processed_path,
            "video_edit_plan_path": episode.video_edit_plan_path,
            "video_dedup_report_path": episode.video_dedup_report_path,
            "video_job_status": _enum_value(episode.video_job_status),
            "video_job_message": episode.video_job_message,
            "video_job_error": episode.video_job_error,
            "video_job_started_at": episode.video_job_started_at.isoformat()
            if episode.video_job_started_at
            else None,
            "video_job_finished_at": episode.video_job_finished_at.isoformat()
            if episode.video_job_finished_at
            else None,
            "draft_package_name": episode.draft_package_name,
            "draft_package_path": episode.draft_package_path,
            "publish_url": episode.publish_url,
            "play_count": episode.play_count,
            "like_count": episode.like_count,
            "comment_count": episode.comment_count,
            "published_at": episode.published_at.isoformat()
            if episode.published_at
            else None,
            "feedback_events": [
                {
                    "id": event.id,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "value": event.value.value,
                    "reason": event.reason,
                    "active": event.active,
                    "before_snapshot": event.before_snapshot,
                    "after_snapshot": event.after_snapshot,
                    "reverted_at": event.reverted_at.isoformat()
                    if event.reverted_at
                    else None,
                    "revert_reason": event.revert_reason,
                    "created_at": event.created_at.isoformat(),
                }
                for event in episode.feedback_events
            ],
            "created_at": episode.created_at.isoformat(),
            "updated_at": episode.updated_at.isoformat(),
        }

    def _script_to_dict(self, script: ScriptPackage) -> dict[str, object]:
        return {
            "id": script.id,
            "hook": script.hook,
            "sections": [
                {
                    "id": section.id,
                    "title": section.title,
                    "estimated_seconds": section.estimated_seconds,
                    "content": section.content,
                    "feedback": section.feedback.value if section.feedback else None,
                }
                for section in script.sections
            ],
            "title_options": script.title_options,
            "cover_copy": script.cover_copy,
            "publish_copy": script.publish_copy,
            "comment_prompt": script.comment_prompt,
        }

    def _episode_from_dict(self, data: dict[str, object]) -> Episode:
        script_data = data.get("script")
        return Episode(
            id=str(data["id"]),
            day_number=int(data["day_number"]),
            episode_date=date.fromisoformat(str(data["episode_date"])),
            status=EpisodeStatus(str(data["status"])),
            intake_mode=EpisodeIntakeMode(str(data.get("intake_mode") or "planned")),
            content_template_id=str(data.get("content_template_id") or "video_diary"),
            topics=[
                TopicCandidate(
                    id=str(topic["id"]),
                    title=str(topic["title"]),
                    core_viewpoint=str(topic["core_viewpoint"]),
                    reason=str(topic["reason"]),
                    audience=str(topic["audience"]),
                    outline=[str(item) for item in topic["outline"]],
                    score=int(topic["score"]),
                    source_refs=[
                        SourceRef(
                            str(source["source_type"]),
                            str(source["title"]),
                            str(source["ref"]),
                        )
                        for source in topic["source_refs"]
                    ],
                    feedback=FeedbackValue(str(topic["feedback"]))
                    if topic.get("feedback")
                    else None,
                )
                for topic in data["topics"]
            ],
            selected_topic_id=str(data["selected_topic_id"])
            if data.get("selected_topic_id")
            else None,
            script=self._script_from_dict(script_data)
            if isinstance(script_data, dict)
            else None,
            script_versions=[
                self._script_version_from_dict(version)
                for version in data.get("script_versions", [])
                if isinstance(version, dict)
            ],
            video_source_file_name=str(data["video_source_file_name"])
            if data.get("video_source_file_name")
            else None,
            video_source_file_names=[
                str(file_name)
                for file_name in (
                    data.get("video_source_file_names")
                    or (
                        [data["video_source_file_name"]]
                        if data.get("video_source_file_name")
                        else []
                    )
                )
            ],
            video_processed_file_name=str(data["video_processed_file_name"])
            if data.get("video_processed_file_name")
            else None,
            video_processed_path=str(data["video_processed_path"])
            if data.get("video_processed_path")
            else None,
            video_edit_plan_path=str(data["video_edit_plan_path"])
            if data.get("video_edit_plan_path")
            else None,
            video_dedup_report_path=str(data["video_dedup_report_path"])
            if data.get("video_dedup_report_path")
            else None,
            video_job_status=VideoJobStatus(
                str(data.get("video_job_status") or "idle")
            ),
            video_job_message=str(data["video_job_message"])
            if data.get("video_job_message")
            else None,
            video_job_error=str(data["video_job_error"])
            if data.get("video_job_error")
            else None,
            video_job_started_at=datetime.fromisoformat(
                str(data["video_job_started_at"])
            )
            if data.get("video_job_started_at")
            else None,
            video_job_finished_at=datetime.fromisoformat(
                str(data["video_job_finished_at"])
            )
            if data.get("video_job_finished_at")
            else None,
            draft_package_name=str(data["draft_package_name"])
            if data.get("draft_package_name")
            else None,
            draft_package_path=str(data["draft_package_path"])
            if data.get("draft_package_path")
            else None,
            publish_url=str(data["publish_url"]) if data.get("publish_url") else None,
            play_count=int(data["play_count"]) if data.get("play_count") is not None else None,
            like_count=int(data["like_count"]) if data.get("like_count") is not None else None,
            comment_count=int(data["comment_count"])
            if data.get("comment_count") is not None
            else None,
            published_at=datetime.fromisoformat(str(data["published_at"]))
            if data.get("published_at")
            else None,
            feedback_events=[
                FeedbackEvent(
                    target_type=str(event["target_type"]),
                    target_id=str(event["target_id"]),
                    value=FeedbackValue(str(event["value"])),
                    reason=str(event["reason"]) if event.get("reason") else None,
                    id=str(event.get("id") or f"fb_{uuid4().hex[:8]}"),
                    active=bool(event.get("active", True)),
                    before_snapshot=event.get("before_snapshot")
                    if isinstance(event.get("before_snapshot"), dict)
                    else None,
                    after_snapshot=event.get("after_snapshot")
                    if isinstance(event.get("after_snapshot"), dict)
                    else None,
                    reverted_at=datetime.fromisoformat(str(event["reverted_at"]))
                    if event.get("reverted_at")
                    else None,
                    revert_reason=str(event["revert_reason"])
                    if event.get("revert_reason")
                    else None,
                    created_at=(
                        utc_now()
                        if not event.get("created_at")
                        else datetime.fromisoformat(str(event["created_at"]))
                    ),
                )
                for event in data.get("feedback_events", [])
            ],
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
        )

    def _script_version_to_dict(self, version: ScriptVersion) -> dict[str, object]:
        return {
            "id": version.id,
            "source": version.source.value,
            "script": self._script_to_dict(version.script),
            "changed_section_ids": version.changed_section_ids,
            "reason": version.reason,
            "active": version.active,
            "created_at": version.created_at.isoformat(),
        }

    def _script_version_from_dict(self, data: dict[str, object]) -> ScriptVersion:
        script_data = data.get("script")
        if not isinstance(script_data, dict):
            raise ValueError("script version must contain script payload")
        return ScriptVersion(
            id=str(data["id"]),
            source=ScriptVersionSource(str(data["source"])),
            script=self._script_from_dict(script_data),
            changed_section_ids=[
                str(item) for item in data.get("changed_section_ids", [])
            ],
            reason=str(data["reason"]) if data.get("reason") else None,
            active=bool(data.get("active", True)),
            created_at=datetime.fromisoformat(str(data["created_at"]))
            if data.get("created_at")
            else utc_now(),
        )

    def _script_from_dict(self, data: dict[str, object]) -> ScriptPackage:
        return ScriptPackage(
            id=str(data["id"]),
            hook=str(data["hook"]),
            sections=[
                ScriptSection(
                    id=str(section["id"]),
                    title=str(section["title"]),
                    estimated_seconds=int(section["estimated_seconds"]),
                    content=str(section["content"]),
                    feedback=FeedbackValue(str(section["feedback"]))
                    if section.get("feedback")
                    else None,
                )
                for section in data["sections"]
            ],
            title_options=[str(item) for item in data["title_options"]],
            cover_copy=str(data["cover_copy"]),
            publish_copy=str(data["publish_copy"]),
            comment_prompt=str(data["comment_prompt"]),
        )


def _enum_value(value: object) -> object:
    return value.value if hasattr(value, "value") else value
