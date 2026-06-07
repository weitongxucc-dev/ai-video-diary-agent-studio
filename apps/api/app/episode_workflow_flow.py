from __future__ import annotations

from typing import Any

from app import episode_media_flow
from app.edit_plan import update_edit_plan_segment_decision
from app.models import (
    Episode,
    FeedbackEvent,
    FeedbackValue,
    ScriptPackage,
    ScriptSection,
)
from app.preference_memory import long_term_preference_events
from app.routers.context import AppContext
from app.schemas import VoiceCommandResponse
from app.voice_commands import VoiceCommandPlan


def feedback_history_for(context: AppContext, episode_id: str) -> list[FeedbackEvent]:
    blocked_preference_keys = {
        (preference.kind, preference.content)
        for preference in context.preference_repo.all()
        if preference.deleted or not preference.enabled
    }
    episode_feedback = [
        event
        for episode in context.repo.all()
        if episode.id != episode_id
        for event in episode.feedback_events
        if event.active
        and _feedback_preference_key(event) not in blocked_preference_keys
    ][-30:]
    return [
        *episode_feedback,
        *long_term_preference_events(context.preference_repo.active()),
    ]


def script_package_from_response(script: Any) -> ScriptPackage:
    return ScriptPackage(
        id=script.id,
        hook=script.hook,
        sections=[
            ScriptSection(
                id=section.id,
                title=section.title,
                estimated_seconds=section.estimated_seconds,
                content=section.content,
                feedback=section.feedback,
            )
            for section in script.sections
        ],
        title_options=[*script.title_options],
        cover_copy=script.cover_copy,
        publish_copy=script.publish_copy,
        comment_prompt=script.comment_prompt,
    )


def voice_command_response(
    context: AppContext,
    plan: VoiceCommandPlan,
    *,
    status: str | None = None,
    result_episode: Episode | None = None,
    result_edit_plan: dict[str, object] | None = None,
) -> VoiceCommandResponse:
    return VoiceCommandResponse.model_validate(
        {
            "status": status or plan.status,
            "intent": plan.intent,
            "summary": plan.summary,
            "can_execute": plan.can_execute,
            "next_url": plan.next_url,
            "operations": [
                {
                    "kind": operation.kind,
                    "label": operation.label,
                    "params": operation.params,
                }
                for operation in plan.operations
            ],
            "result_episode": (
                episode_media_flow.episode_response(context, result_episode)
                if result_episode is not None
                else None
            ),
            "result_edit_plan": result_edit_plan,
        }
    )


def execute_voice_command_plan(
    context: AppContext,
    episode: Episode,
    plan: VoiceCommandPlan,
) -> tuple[Episode, dict[str, object] | None]:
    updated_episode = episode
    edit_plan_workspace: dict[str, object] | None = None
    for operation in plan.operations:
        if operation.kind == "generate_topics":
            updated_episode = context.workflow.run(
                "generate_topics",
                updated_episode,
                feedback_history=feedback_history_for(context, updated_episode.id),
                creator_profile=context.creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_topics":
            updated_episode = context.workflow.run(
                "regenerate_topics",
                updated_episode,
                feedback_history=feedback_history_for(context, updated_episode.id),
                creator_profile=context.creator_profile_repo.load(),
            )
        elif operation.kind == "generate_script":
            updated_episode = context.workflow.run(
                "generate_script",
                updated_episode,
                feedback_history=feedback_history_for(context, updated_episode.id),
                creator_profile=context.creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_script":
            updated_episode = context.workflow.run(
                "regenerate_script",
                updated_episode,
                feedback_history=feedback_history_for(context, updated_episode.id),
                reason=string_param(operation.params, "reason"),
                creator_profile=context.creator_profile_repo.load(),
            )
        elif operation.kind == "regenerate_script_section":
            updated_episode = context.workflow.run(
                "regenerate_script_section",
                updated_episode,
                section_id=required_string_param(operation.params, "section_id"),
                feedback_history=feedback_history_for(context, updated_episode.id),
                reason=string_param(operation.params, "reason"),
                creator_profile=context.creator_profile_repo.load(),
            )
        elif operation.kind == "approve_script":
            updated_episode = context.workflow.run("approve_script", updated_episode)
        elif operation.kind == "create_draft":
            updated_episode = episode_media_flow.create_draft_for_episode(
                context,
                updated_episode,
            )
        elif operation.kind == "edit_plan_segment_decision":
            edit_plan_workspace = update_edit_plan_segment_decision(
                updated_episode,
                context.settings.video_output_path,
                segment_id=required_string_param(operation.params, "segment_id"),
                action=required_string_param(operation.params, "action"),
                reason=string_param(operation.params, "reason"),
            )
        else:
            raise ValueError(f"unsupported voice command operation: {operation.kind}")
    return context.repo.save(updated_episode), edit_plan_workspace


def has_comment_insight_event(
    episode: Episode,
    content: str,
    source_comment: str,
) -> bool:
    for event in episode.feedback_events:
        if event.target_type != "published_comment_insight" or event.reason != content:
            continue
        if not isinstance(event.before_snapshot, dict):
            continue
        if event.before_snapshot.get("source_comment") == source_comment:
            return True
    return False


def required_string_param(params: dict[str, object], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"voice command operation missing param: {key}")
    return value


def string_param(params: dict[str, object], key: str) -> str | None:
    value = params.get(key)
    return value if isinstance(value, str) and value else None


def _feedback_preference_key(event: FeedbackEvent) -> tuple[str, str] | None:
    if not event.reason:
        return None
    kind = "prefer" if event.value == FeedbackValue.UP else "avoid"
    return (kind, event.reason.strip())
