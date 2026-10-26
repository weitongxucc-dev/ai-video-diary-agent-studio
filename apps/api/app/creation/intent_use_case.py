"""Creation intake rules shared by protocol adapters.

Plans remain the authoritative delivery requirements. A window may inherit
defaults, but revalidation must use each frozen Plan rather than the parent.
"""
from __future__ import annotations

import hashlib
import re


class CreationIntentUseCase:
    DELIVERY_FIELDS = ("language", "aspect_ratio", "duration_target_seconds", "duration_mode", "duration_range_seconds",
                       "presenter_share_target", "lipsync_quality")

    @staticmethod
    def require_duration_authority(mode, *, delegated):
        if mode == "approximate" and delegated:
            raise ValueError("对话尚未冻结可信的近似时长范围，请先由成员明确确认结构化时长约定。")

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
    def adopted_for_execution(plan, script, *, verified_body_digest: str | None = None) -> bool:
        intent = plan.creation_intent
        return bool(intent and intent["kind"] in {"goal_delegation", "final_script"} and script
            and script.selection_status == "confirmed" and script.adoption
            and script.adoption["kind"] == "delegated_system"
            and script.adoption["authorization_source"] == intent["source"]
            and script.adoption["body_digest"] == (verified_body_digest if verified_body_digest is not None
                else hashlib.sha256("".join(part.content for part in script.sections).encode()).hexdigest()))

    @staticmethod
    def script_goal(repository, plan, task, *, viewer_user_id: str | None) -> str:
        """Resolve user requirements without promoting a delegated draft to fixed copy."""
        intent, adoption = plan.creation_intent or {}, plan.adoption or {}
        if (intent.get("kind") != "goal_delegation" or adoption.get("kind") != "delegated_system"
                or not any(key in adoption for key in ("request_id", "window_id"))):
            return plan.goal
        if (not adoption.get("request_id") or not adoption.get("window_id")
                or task.workspace_id != plan.workspace_id or task.plan_id != plan.id
                or plan.creation_task_id != task.id or adoption.get("plan_id") != plan.id):
            raise ValueError("文案目标的原请求、方案和任务采用关联不一致，请重新读取当前任务。")

        # Authorization belongs to the current reader, never the original owner
        # supplied by the Plan. This read also preserves the repository's 404.
        original = repository.get_creation_request(plan.workspace_id, adoption["request_id"],
            viewer_user_id=viewer_user_id)
        saved_intent = original["requirements"].get("creation_intent") or {}
        source = intent.get("source") or {}
        if (saved_intent.get("kind") != "goal_delegation" or saved_intent.get("source") != source
                or adoption.get("authorization_source") != source
                or source.get("kind") not in {"conversation_input", "user_action"}
                or source.get("request_id") != original["request_key"]
                or source.get("member_id") != original["owner_user_id"]
                or adoption.get("member_id") != source.get("member_id")):
            raise ValueError("文案目标的原始用户授权来源不一致，请核对已采用请求。")
        # Conversation input has an exact host-computed body digest. Direct
        # user actions have no conversation body; their saved request owns it.
        if (source["kind"] == "conversation_input"
                and source.get("message_digest") != hashlib.sha256(original["goal"].encode()).hexdigest()):
            raise ValueError("文案目标与原始用户输入摘要不一致，没有沿用方案中的自拟文案。")

        window = original["current_window"]
        if window and window["id"] == adoption["window_id"]:
            if (window["status"] != "confirmed"
                    or window["input_fingerprint"] != adoption.get("window_input_fingerprint")):
                raise ValueError("文案目标的规划窗口未采用或输入摘要不一致，请读取实际采用版本。")
            entry = next((item for item in window["entries"]
                if item["plan_id"] == plan.id and item["task_id"] == task.id), None)
        else:
            # Adopted Plan + Task + window receipt were committed atomically.
            # Re-read that exact persisted proof, never scan historical items.
            persisted = repository.get_creation_plan(plan.workspace_id, plan.id)
            if persisted.adoption == adoption and persisted.creation_task_id == task.id:
                return original["goal"]
            raise ValueError("文案目标不属于该请求已采用窗口中的当前任务，请重新核对采用关联。")
        if (entry is None or not entry["plan"] or not entry["task"] or entry["plan"].get("adoption") != adoption
                or entry["plan"].get("creation_task_id") != task.id or entry["task"]["plan_id"] != plan.id):
            raise ValueError("文案目标不属于该请求已采用窗口中的当前任务，请重新核对采用关联。")
        return original["goal"]

    @classmethod
    def frozen_delivery_inputs(cls, requirements: dict, plan: dict) -> dict:
        defaults = {"duration_mode": "exact", "duration_range_seconds": None}
        values = {key: plan.get(key, defaults[key]) if key in defaults else plan[key]
                  for key in cls.DELIVERY_FIELDS}
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
