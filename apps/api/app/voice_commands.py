from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from app.models import Episode, EpisodeStatus, ScriptSection


VoiceCommandStatus = Literal["ready", "blocked", "unknown"]


@dataclass
class VoiceCommandOperation:
    kind: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceCommandPlan:
    status: VoiceCommandStatus
    intent: str
    summary: str
    can_execute: bool
    next_url: str | None = None
    operations: list[VoiceCommandOperation] = field(default_factory=list)


def parse_voice_command(episode: Episode, transcript: str) -> VoiceCommandPlan:
    text = _normalize(transcript)
    if not text:
        return _unknown("请先输入一条指令。")

    edit_plan = _parse_edit_plan_decision(text)
    if edit_plan is not None:
        return edit_plan

    script_section = _parse_script_section_regeneration(episode, text)
    if script_section is not None:
        return script_section

    if _matches(text, "重新生成选题", "换一批选题", "重做选题"):
        return VoiceCommandPlan(
            status="ready",
            intent="regenerate_topics",
            summary="重新生成候选选题。",
            can_execute=True,
            next_url="/topics",
            operations=[VoiceCommandOperation("regenerate_topics", "重新生成候选选题")],
        )

    if _matches(text, "生成选题", "做选题", "出选题"):
        return VoiceCommandPlan(
            status="ready",
            intent="generate_topics",
            summary="生成今日候选选题。",
            can_execute=True,
            next_url="/topics",
            operations=[VoiceCommandOperation("generate_topics", "生成今日选题")],
        )

    if _matches(text, "重新生成口播稿", "重写口播稿", "重做脚本", "重新生成脚本"):
        if episode.selected_topic_id is None:
            return _blocked("还没有确认选题，不能重新生成口播稿。", "/topics")
        return VoiceCommandPlan(
            status="ready",
            intent="regenerate_script",
            summary="基于当前选题重新生成整篇口播稿。",
            can_execute=True,
            next_url="/script",
            operations=[
                VoiceCommandOperation(
                    "regenerate_script",
                    "重新生成口播稿",
                    {"reason": transcript.strip()},
                )
            ],
        )

    if _matches(text, "生成口播稿", "生成脚本", "写口播稿"):
        if episode.selected_topic_id is None:
            return _blocked("还没有确认选题，不能生成口播稿。", "/topics")
        return VoiceCommandPlan(
            status="ready",
            intent="generate_script",
            summary="基于已确认选题生成口播稿。",
            can_execute=True,
            next_url="/script",
            operations=[VoiceCommandOperation("generate_script", "生成口播稿")],
        )

    if _matches(text, "确认脚本", "确认口播稿", "脚本通过", "等待录制"):
        if episode.script is None:
            return _blocked("还没有口播稿，不能确认脚本。", "/script")
        return VoiceCommandPlan(
            status="ready",
            intent="approve_script",
            summary="确认口播稿，进入等待录制。",
            can_execute=True,
            next_url="/video",
            operations=[VoiceCommandOperation("approve_script", "确认口播稿")],
        )

    if _matches(text, "生成剪映草稿", "导入剪映", "生成草稿"):
        if not episode.video_processed_file_name:
            return _blocked("还没有处理后视频，不能生成剪映草稿。", "/video")
        return VoiceCommandPlan(
            status="ready",
            intent="create_draft",
            summary="确认剪辑计划，并生成/导入剪映草稿。",
            can_execute=True,
            next_url="/draft",
            operations=[VoiceCommandOperation("create_draft", "生成剪映草稿")],
        )

    if _matches(text, "继续", "下一步", "今天的流程", "往下走"):
        return _continue_flow_plan(episode)

    return _unknown("暂时没有识别出可执行指令。你可以说：继续今天的流程、重新生成选题、重写开头、把 retake-1 保留。")


def _continue_flow_plan(episode: Episode) -> VoiceCommandPlan:
    if not episode.topics:
        return VoiceCommandPlan(
            status="ready",
            intent="continue_flow",
            summary="继续今天的流程：生成今日选题。",
            can_execute=True,
            next_url="/topics",
            operations=[VoiceCommandOperation("generate_topics", "生成今日选题")],
        )
    if episode.selected_topic_id is None:
        return _blocked("候选选题已生成，需要先人工确认一个选题。", "/topics")
    if episode.script is None:
        return VoiceCommandPlan(
            status="ready",
            intent="continue_flow",
            summary="继续今天的流程：生成口播稿。",
            can_execute=True,
            next_url="/script",
            operations=[VoiceCommandOperation("generate_script", "生成口播稿")],
        )
    if episode.status in {EpisodeStatus.SCRIPT_READY, EpisodeStatus.TOPIC_APPROVED}:
        return VoiceCommandPlan(
            status="ready",
            intent="continue_flow",
            summary="继续今天的流程：确认口播稿并进入录制。",
            can_execute=True,
            next_url="/video",
            operations=[VoiceCommandOperation("approve_script", "确认口播稿")],
        )
    if episode.video_processed_file_name and episode.draft_package_name is None:
        return VoiceCommandPlan(
            status="ready",
            intent="continue_flow",
            summary="继续今天的流程：生成剪映草稿。",
            can_execute=True,
            next_url="/draft",
            operations=[VoiceCommandOperation("create_draft", "生成剪映草稿")],
        )
    return _blocked("当前步骤需要人工录制、上传视频或检查剪映草稿。", "/video")


def _parse_script_section_regeneration(
    episode: Episode,
    text: str,
) -> VoiceCommandPlan | None:
    if not _matches(text, "重写", "改写", "换个说法", "更像", "更犀利", "更真实"):
        return None
    if episode.script is None:
        return _blocked("还没有口播稿，不能重写段落。", "/script")
    section = _match_script_section(episode.script.sections, text)
    if section is None:
        return _blocked("没有找到要重写的口播段落。", "/script")
    return VoiceCommandPlan(
        status="ready",
        intent="regenerate_script_section",
        summary=f"重写口播段落：{section.title}。",
        can_execute=True,
        next_url="/script",
        operations=[
            VoiceCommandOperation(
                "regenerate_script_section",
                f"重写段落：{section.title}",
                {"section_id": section.id, "reason": text},
            )
        ],
    )


def _match_script_section(
    sections: list[ScriptSection],
    text: str,
) -> ScriptSection | None:
    if not sections:
        return None
    if _matches(text, "开头", "开场", "hook"):
        return next(
            (
                section
                for section in sections
                if "开头" in section.title or section.id in {"hook", "intro"}
            ),
            sections[0],
        )
    if _matches(text, "结尾", "收尾", "结束"):
        return next(
            (
                section
                for section in sections
                if "结尾" in section.title or section.id in {"ending", "outro"}
            ),
            sections[-1],
        )
    for section in sections:
        if section.id.lower() in text.lower() or section.title in text:
            return section
    return sections[0]


def _parse_edit_plan_decision(text: str) -> VoiceCommandPlan | None:
    match = re.search(r"\b(?P<segment>(?:retake|keep|silence|review)-\d+)\b", text)
    if match is None:
        return None
    action = None
    if _matches(text, "保留", "留下", "不要删"):
        action = "keep"
    elif _matches(text, "删除", "剪掉", "去掉", "删掉"):
        action = "remove"
    elif _matches(text, "复核", "待确认", "人工看"):
        action = "review"
    if action is None:
        return _blocked("找到了片段编号，但没有识别出保留、删除或复核动作。", "/edit-plan")
    segment_id = match.group("segment")
    reason = _reason_after_because(text)
    return VoiceCommandPlan(
        status="ready",
        intent="edit_plan_segment_decision",
        summary=f"把剪辑片段 {segment_id} 标记为{_decision_action_label(action)}。",
        can_execute=True,
        next_url="/edit-plan",
        operations=[
            VoiceCommandOperation(
                "edit_plan_segment_decision",
                f"更新剪辑片段：{segment_id}",
                {"segment_id": segment_id, "action": action, "reason": reason},
            )
        ],
    )


def _reason_after_because(text: str) -> str | None:
    parts = re.split(r"因为|原因是|理由是", text, maxsplit=1)
    if len(parts) < 2:
        return None
    reason = parts[1].strip(" ，。,.")
    return reason or None


def _decision_action_label(action: str) -> str:
    return {"keep": "保留", "remove": "删除", "review": "复核"}[action]


def _unknown(summary: str) -> VoiceCommandPlan:
    return VoiceCommandPlan(
        status="unknown",
        intent="unknown",
        summary=summary,
        can_execute=False,
    )


def _blocked(summary: str, next_url: str | None = None) -> VoiceCommandPlan:
    return VoiceCommandPlan(
        status="blocked",
        intent="blocked",
        summary=summary,
        can_execute=False,
        next_url=next_url,
    )


def _matches(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _normalize(transcript: str) -> str:
    return transcript.strip()
