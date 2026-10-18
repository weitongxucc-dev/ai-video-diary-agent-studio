"""Creation intake rules shared by protocol adapters.

Plans remain the authoritative delivery requirements. A window may inherit
defaults, but revalidation must use each frozen Plan rather than the parent.
"""
from __future__ import annotations

import hashlib
import re


class CreationIntentUseCase:
    DELIVERY_FIELDS = ("language", "aspect_ratio", "duration_target_seconds",
                       "presenter_share_target", "lipsync_quality")

    @staticmethod
    def is_bilingual_variant_goal(goal: str) -> bool:
        return bool(re.search(r"中(?:文)?(?:和|与|、|/)?英(?:文)?(?:版本)?各\s*(?:一|1)\s*条", goal))

    @staticmethod
    def delivery_inputs(requirements: dict, *, goal: str, name: str | None, overrides: dict) -> dict:
        return {**{key: value for key, value in requirements.items() if key != "creation_intent"},
                **overrides, "goal": goal, "name": name, "target_count": 1}

    @staticmethod
    def receive_intent(intent: dict, *, source: dict, target_count: int, original_message: str | None = None) -> dict:
        kind, final = intent["kind"], intent.get("final_script")
        if kind == "final_script":
            if target_count != 1 or final is None or not final["text"].strip() or not final["title"].strip():
                raise ValueError("按定稿制作需要单条目标及准确的标题和正文；不同稿件分别保存。")
        elif final is not None:
            raise ValueError("固定正文只属于按定稿制作，不能混入讨论或自主改写范围。")
        if original_message is not None and kind != "discussion":
            # The typed intent is persisted against the actual input. Do not
            # build another intent classifier from verb/word-distance guesses.
            # Explicit discussion-only/stop wording still limits that scope.
            CreationIntentUseCase.reject_discussion_only_input(original_message)
            if final and not final.get("reply_origin") and final["text"] not in original_message:
                raise ValueError("定稿正文与实际用户输入不一致，没有固定模型改写的版本。")
        saved = {**intent, "source": source}
        if final:
            saved["final_script"] = {**final, "body_digest": hashlib.sha256(final["text"].encode()).hexdigest()}
        return saved

    @staticmethod
    def reject_discussion_only_input(message: str) -> None:
        if re.search(r"先.{0,8}(?:讨论|聊聊|看看方案)|只(?:讨论|写文案|润色)|(?:不要|先不|先别|暂不)(?:开始|制作|生成|执行)(?=$|[，。！？；\s]|视频|成片)|\b(?:just|only)\s+(?:discuss|draft)\b|\b(?:do not|don't)\s+(?:start|(?:make|create|produce|render)\s+(?:a\s+|the\s+)?video)\b", message, re.IGNORECASE):
            raise ValueError("本轮只授权讨论，没有采用或制作授权。")

    @staticmethod
    def adoption(intent: dict | None, *, kind: str, member_id: str | None, version: str) -> dict:
        if kind not in {"manual", "delegated_system"}:
            raise ValueError("采用来源必须为人工动作或委托范围内系统采用。")
        if intent and intent["kind"] == "discussion":
            raise ValueError("讨论没有制作授权，不能采用方案或文案。")
        if kind == "delegated_system" and (not intent or intent["kind"] not in {"goal_delegation", "final_script"}):
            raise ValueError("系统采用需要已保存的明确制作委托。")
        if intent and intent["source"]["member_id"] != member_id:
            raise ValueError("创作授权不属于当前可信成员。")
        return {"kind": kind, "operator": "system" if kind == "delegated_system" else "member",
                "member_id": member_id, "version": version,
                "authorization_source": intent["source"] if intent else {"kind": "manual_action", "member_id": member_id}}

    @staticmethod
    def use_provided_copy(plan, intent: dict | None):
        if intent and intent["kind"] == "final_script":
            from app.speech_timing import estimate_speech_seconds
            final = intent["final_script"]
            if hashlib.sha256(final["text"].encode()).hexdigest() != final["body_digest"]:
                raise ValueError("定稿正文摘要不一致，没有采用变化后的正文。")
            estimate = estimate_speech_seconds(final["text"])
            if estimate > plan.duration_target_seconds:
                raise ValueError(f"定稿预计约{estimate:.1f}秒，超过方案{plan.duration_target_seconds}秒；请调整时长或明确采用新的定稿，不会自动删词。")
            # A supplied fixed copy is a real content source. This replaces
            # only the missing-topic-source gate, never media/auth/model gates.
            plan.blockers = [item for item in plan.blockers if item.reason != "content_sources_missing"]
        return plan

    @staticmethod
    def adopted_for_execution(plan, script) -> bool:
        intent = plan.creation_intent
        return bool(intent and intent["kind"] in {"goal_delegation", "final_script"} and script
            and script.selection_status == "confirmed" and script.adoption
            and script.adoption["kind"] == "delegated_system"
            and script.adoption["authorization_source"] == intent["source"]
            and script.adoption["body_digest"] == hashlib.sha256("".join(part.content for part in script.sections).encode()).hexdigest())

    @classmethod
    def frozen_delivery_inputs(cls, requirements: dict, plan: dict) -> dict:
        values = {key: plan[key] for key in cls.DELIVERY_FIELDS}
        for key in ("selected_voice", "selected_person"):
            reference = plan.get(key)
            values[key] = ({"profile_id": reference["profile_id"], "version": reference["version"]}
                           if reference else None)
        return cls.delivery_inputs(requirements, goal=plan["goal"], name=plan["name"], overrides=values)

    @staticmethod
    def check_distinct_deliveries(inputs: list[dict]) -> None:
        # The same topic in two languages is two deliveries. Merely renaming a
        # duplicate does not make it a distinct video.
        seen = []
        for item in inputs:
            delivery = {key: value for key, value in item.items() if key != "name"}
            if delivery in seen:
                raise ValueError("每条视频需要不同目标或交付要求，不能复制相同方案凑数。")
            seen.append(delivery)
