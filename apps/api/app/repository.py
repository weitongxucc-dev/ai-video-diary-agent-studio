from __future__ import annotations

import json
import logging
import sqlite3
from contextvars import ContextVar
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from app.models import (
    Episode,
    EpisodeIntakeMode,
    EpisodeStatus,
    FeedbackEvent,
    FeedbackValue,
    ProductionChainStage,
    ProductionChainStep,
    ScriptPackage,
    ScriptSection,
    ScriptVersion,
    ScriptVersionSource,
    SourceFileState,
    SourceRef,
    TopicCandidate,
    VideoJobStatus,
    utc_now,
)

logger = logging.getLogger(__name__)

# The active workspace for the current request/worker scope. Set by the
# auth middleware; background jobs re-set it explicitly so repository
# queries always stay inside the caller's workspace partition.
_WORKSPACE_CONTEXT: ContextVar[str | None] = ContextVar(
    "workspace_context", default=None
)

# Whether Supabase auth is enabled. When enabled, repository queries must
# resolve a workspace from the active context and never silently fall back
# to the reserved 'local' workspace — an authenticated flow that loses its
# context fails loudly instead of reading someone else's data.
_AUTH_ENABLED: bool = False


class WorkspaceContextRequiredError(RuntimeError):
    """Raised when auth is enabled but no workspace context is active."""


def set_auth_enabled(enabled: bool) -> None:
    global _AUTH_ENABLED
    _AUTH_ENABLED = bool(enabled)


def set_workspace_context(workspace_id: str) -> object:
    return _WORKSPACE_CONTEXT.set(workspace_id)


def reset_workspace_context(token: object) -> None:
    _WORKSPACE_CONTEXT.reset(token)


def active_workspace_id() -> str:
    """Return the active workspace, failing safe when auth is enabled.

    With Supabase auth enabled a missing context is a programming bug (the
    middleware/worker must set it), so we raise rather than silently scoping
    the query to the reserved 'local' workspace.
    """
    workspace_id = _WORKSPACE_CONTEXT.get()
    if workspace_id:
        return workspace_id
    if _AUTH_ENABLED:
        raise WorkspaceContextRequiredError(
            "workspace context is required when Supabase auth is enabled"
        )
    return LEGACY_WORKSPACE_ID


class EpisodeRepository:
    def __init__(
        self,
        today_provider: object | None = None,
        database_path: str | Path | None = None,
    ) -> None:
        del today_provider
        self._episodes: dict[str, Episode] = {}
        self._database_path = Path(database_path).expanduser() if database_path else None
        if self._database_path is not None and self._database_path.is_file():
            self._load_database()

    def get(self, episode_id: str, workspace_id: str | None = None) -> Episode:
        ws = workspace_id or active_workspace_id()
        episode = self._episodes[episode_id]
        if episode.workspace_id != ws:
            raise KeyError(episode_id)
        return episode

    def all(self, workspace_id: str | None = None) -> list[Episode]:
        ws = workspace_id or active_workspace_id()
        episodes = [
            episode
            for episode in self._episodes.values()
            if episode.workspace_id == ws
        ]
        return sorted(
            episodes,
            key=lambda episode: episode.created_at,
            reverse=True,
        )

    def _load_database(self) -> None:
        assert self._database_path is not None
        try:
            with sqlite3.connect(self._database_path) as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(episodes)").fetchall()
                }
                rows = (
                    conn.execute("SELECT payload, workspace_id FROM episodes").fetchall()
                    if "workspace_id" in columns
                    else [
                        (payload, LEGACY_WORKSPACE_ID)
                        for (payload,) in conn.execute(
                            "SELECT payload FROM episodes"
                        ).fetchall()
                    ]
                )
        except sqlite3.Error:
            logger.warning(
                "unable to read historical episodes from %s",
                self._database_path,
                exc_info=True,
            )
            return
        for payload, workspace_id in rows:
            try:
                episode = self._episode_from_dict(json.loads(payload))
                episode.workspace_id = str(workspace_id or "local")
                self._episodes[episode.id] = episode
            except Exception:
                logger.warning(
                    'skipping unparseable episode row in %s',
                    self._database_path,
                    exc_info=True,
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
            "video_production_chain": [
                _production_chain_step_to_dict(step)
                for step in episode.video_production_chain
            ],
            "source_file_states": {
                k: v.value for k, v in episode.source_file_states.items()
            },
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
            "platform": episode.platform,
            "play_count": episode.play_count,
            "like_count": episode.like_count,
            "comment_count": episode.comment_count,
            "notes": episode.notes,
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
            day_number=_safe_int(data["day_number"], context="day_number"),
            episode_date=_safe_date(data.get("episode_date"), context="episode_date"),
            status=_safe_enum(EpisodeStatus, data.get("status"), EpisodeStatus.CREATED, context="status"),
            intake_mode=_intake_mode_from_raw(
                str(data.get("intake_mode") or "planned")
            ),
            content_template_id=str(data.get("content_template_id") or "video_diary"),
            topics=[
                TopicCandidate(
                    id=str(topic["id"]),
                    title=str(topic["title"]),
                    core_viewpoint=str(topic["core_viewpoint"]),
                    reason=str(topic["reason"]),
                    audience=str(topic["audience"]),
                    outline=[str(item) for item in topic["outline"]],
                    score=_safe_int(topic.get("score"), context="topic score"),
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
            video_production_chain=[
                _production_chain_step_from_dict(step)
                for step in data.get("video_production_chain", [])
                if isinstance(step, dict)
            ],
            source_file_states={
                str(k): SourceFileState(str(v))
                for k, v in data.get("source_file_states", {}).items()
            },
            video_job_status=_safe_enum(
                VideoJobStatus, data.get("video_job_status"), VideoJobStatus.IDLE, context="video_job_status"
            ),
            video_job_message=str(data["video_job_message"])
            if data.get("video_job_message")
            else None,
            video_job_error=str(data["video_job_error"])
            if data.get("video_job_error")
            else None,
            video_job_started_at=_safe_datetime(
                data.get("video_job_started_at"), context="video_job_started_at"
            ),
            video_job_finished_at=_safe_datetime(
                data.get("video_job_finished_at"), context="video_job_finished_at"
            ),
            draft_package_name=str(data["draft_package_name"])
            if data.get("draft_package_name")
            else None,
            draft_package_path=str(data["draft_package_path"])
            if data.get("draft_package_path")
            else None,
            publish_url=str(data["publish_url"]) if data.get("publish_url") else None,
            platform=str(data["platform"]) if data.get("platform") else None,
            play_count=_safe_int(data["play_count"], default=0, context="play_count") if data.get("play_count") is not None else None,
            like_count=_safe_int(data["like_count"], default=0, context="like_count") if data.get("like_count") is not None else None,
            comment_count=_safe_int(data["comment_count"], default=0, context="comment_count")
            if data.get("comment_count") is not None
            else None,
            notes=str(data["notes"]) if data.get("notes") else None,
            published_at=_safe_datetime(
                data.get("published_at"), context="published_at"
            ),
            feedback_events=[
                FeedbackEvent(
                    target_type=str(event["target_type"]),
                    target_id=str(event["target_id"]),
                    value=_safe_enum(FeedbackValue, event.get("value"), FeedbackValue.UP, context="feedback value"),
                    reason=str(event["reason"]) if event.get("reason") else None,
                    id=str(event.get("id") or f"fb_{uuid4().hex[:8]}"),
                    active=bool(event.get("active", True)),
                    before_snapshot=event.get("before_snapshot")
                    if isinstance(event.get("before_snapshot"), dict)
                    else None,
                    after_snapshot=event.get("after_snapshot")
                    if isinstance(event.get("after_snapshot"), dict)
                    else None,
                    reverted_at=_safe_datetime(
                        event.get("reverted_at"), context="reverted_at"
                    ),
                    revert_reason=str(event["revert_reason"])
                    if event.get("revert_reason")
                    else None,
                    created_at=_safe_datetime(
                        event.get("created_at"), context="feedback created_at"
                    ) or utc_now(),
                )
                for event in data.get("feedback_events", [])
            ],
            created_at=_safe_datetime(data.get("created_at"), context="created_at") or utc_now(),
            updated_at=_safe_datetime(data.get("updated_at"), context="updated_at") or utc_now(),
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
            raise TypeError("script version must contain script payload")
        return ScriptVersion(
            id=str(data["id"]),
            source=_safe_enum(ScriptVersionSource, data.get("source"), ScriptVersionSource.AI_GENERATED, context="script version source"),
            script=self._script_from_dict(script_data),
            changed_section_ids=[
                str(item) for item in data.get("changed_section_ids", [])
            ],
            reason=str(data["reason"]) if data.get("reason") else None,
            active=bool(data.get("active", True)),
            created_at=_safe_datetime(data.get("created_at"), context="script version created_at") or utc_now(),
        )

    def _script_from_dict(self, data: dict[str, object]) -> ScriptPackage:
        return ScriptPackage(
            id=str(data["id"]),
            hook=str(data["hook"]),
            sections=[
                ScriptSection(
                    id=str(section["id"]),
                    title=str(section["title"]),
                    estimated_seconds=_safe_int(section.get("estimated_seconds"), context="estimated_seconds"),
                    content=str(section["content"]),
                    feedback=_safe_enum(FeedbackValue, section.get("feedback"), None, context="section feedback")
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

def _safe_int(value: object, default: int = 0, context: str = "") -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(
            "repository: unparseable int value=%r (%s), defaulting to %d",
            value, context, default,
        )
        return default


def _safe_date(value: object, context: str = "") -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        logger.warning(
            "repository: unparseable date value=%r (%s), defaulting to today",
            value, context,
        )
        return datetime.now(UTC).date()


def _safe_datetime(value: object, context: str = "") -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        logger.warning(
            "repository: unparseable datetime value=%r (%s), defaulting to None",
            value, context,
        )
        return None


def _safe_enum(enum_cls, value: object, default, context: str = ""):
    try:
        return enum_cls(str(value))
    except (ValueError, TypeError):
        logger.warning(
            "repository: unparseable enum value=%r for %s (%s), using default %s",
            value, enum_cls.__name__, context, default,
        )
        return default



def _intake_mode_from_raw(raw: str) -> EpisodeIntakeMode:
    mode_map: dict[str, str] = {
        "planned": "planned_single",
        "ad_hoc_video": "ad_hoc_single",
        "ad_hoc_video_batch": "ad_hoc_batch",
    }
    return EpisodeIntakeMode(mode_map.get(raw, raw))


def _production_chain_step_to_dict(step: ProductionChainStep) -> dict[str, object]:
    return {
        "stage": step.stage.value,
        "status": step.status,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
        "detail": step.detail,
    }


def _production_chain_step_from_dict(data: dict[str, object]) -> ProductionChainStep:
    return ProductionChainStep(
        stage=ProductionChainStage(str(data["stage"])),
        status=str(data.get("status") or "pending"),
        started_at=datetime.fromisoformat(str(data["started_at"]))
        if data.get("started_at")
        else None,
        finished_at=datetime.fromisoformat(str(data["finished_at"]))
        if data.get("finished_at")
        else None,
        detail=str(data["detail"]) if data.get("detail") else None,
    )


def _enum_value(value: object) -> object:
    return value.value if hasattr(value, "value") else value


# ── Global state (source sync & preference state) ────────────────

_GLOBAL_STATE_FILE = Path("data/global_state.json")

# Reserved workspace id for pre-auth local data. Legacy rows and any data
# written while Supabase auth is disabled live here and are never merged
# into an authenticated account's workspace.
LEGACY_WORKSPACE_ID = "local"


def load_global_state() -> dict[str, object]:
    if not _GLOBAL_STATE_FILE.exists():
        return {}
    try:
        state = json.loads(_GLOBAL_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(state, dict):
        return {}
    # Migrate legacy flat format into the reserved local workspace so it is
    # never merged into an authenticated account's data.
    if not isinstance(state.get("workspaces"), dict):
        legacy: dict[str, object] = {}
        for key in ("source_sync_statuses", "preference_state"):
            if isinstance(state.get(key), dict):
                legacy[key] = state[key]
        if legacy:
            return {"workspaces": {LEGACY_WORKSPACE_ID: legacy}}
    return state


def save_global_state(state: dict[str, object]) -> None:
    _GLOBAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GLOBAL_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _workspace_state_slot(
    state: dict[str, object],
    workspace_id: str,
) -> dict[str, object]:
    """Return a copy of the mutable sub-state for a workspace.

    Legacy flat state files are migrated by load_global_state into the
    reserved 'local' workspace, so this only ever sees the partitioned
    'workspaces' format.
    """
    workspaces = state.get("workspaces")
    if isinstance(workspaces, dict):
        slot = workspaces.get(workspace_id)
        return dict(slot) if isinstance(slot, dict) else {}
    return {}


def _write_workspace_state_slot(
    workspace_id: str,
    slot: dict[str, object],
) -> None:
    state = load_global_state()
    workspaces = state.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = {}
    workspaces[workspace_id] = slot
    state["workspaces"] = workspaces
    save_global_state(state)


def load_source_sync_statuses(
    workspace_id: str | None = None,
) -> dict[str, dict[str, object]]:
    ws = workspace_id or active_workspace_id()
    state = load_global_state()
    raw = _workspace_state_slot(state, ws).get("source_sync_statuses", {})
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return {}


def save_source_sync_statuses(
    statuses: dict[str, dict[str, object]],
    workspace_id: str | None = None,
) -> None:
    ws = workspace_id or active_workspace_id()
    state = load_global_state()
    slot = _workspace_state_slot(state, ws)
    slot["source_sync_statuses"] = statuses
    _write_workspace_state_slot(ws, slot)


def load_preference_state_payload(
    workspace_id: str | None = None,
) -> dict[str, object] | None:
    ws = workspace_id or active_workspace_id()
    state = load_global_state()
    pref = _workspace_state_slot(state, ws).get("preference_state")
    return pref if isinstance(pref, dict) else None


def save_preference_state_payload(
    payload: dict[str, object],
    workspace_id: str | None = None,
) -> None:
    ws = workspace_id or active_workspace_id()
    state = load_global_state()
    slot = _workspace_state_slot(state, ws)
    slot["preference_state"] = payload
    _write_workspace_state_slot(ws, slot)
