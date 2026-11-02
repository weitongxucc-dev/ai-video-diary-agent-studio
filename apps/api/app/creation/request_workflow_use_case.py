"""Creation goal clarification, bounded planning and explicit adoption.

The workflow yields only named input/DTO/template read stages. HTTP adapters
answer them at the original checkpoints; durable writes stay with their owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple
from uuid import uuid4
import hashlib
import json
import re

from app.assistant.inputs import active_input, latest_consumed_input
from app.creation.contracts import CreationTask
from app.creation.plan_preflight_use_case import CreationPlanPreflightInput, CreationPlanPreflightRejected


class CreationRequestWorkflowRejected(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def conflict(error, reason, repair_action, **facts):
    return CreationRequestWorkflowRejected(409, {"error": error, "reason": reason,
        "repair_action": repair_action, **facts})


@dataclass(frozen=True)
class Reader:
    workspace_id: str
    user_id: str | None
    local_diagnostic: bool


@dataclass(frozen=True)
class ReadWorkspace:
    pass


@dataclass(frozen=True)
class ReadReader:
    pass


@dataclass(frozen=True)
class ReadOwnedRequest:
    request_id: str


@dataclass(frozen=True)
class ReadInput:
    turn_id: str


class CurrentInput(NamedTuple):
    turn: dict
    message: str
    active: dict | None


@dataclass(frozen=True)
class ReadReply:
    conversation_id: str
    turn_id: str


@dataclass(frozen=True)
class ReadTemplate:
    workspace_id: str
    template_id: str
    version: int


@dataclass(frozen=True)
class NormalizeDelivery:
    requirements: dict


@dataclass(frozen=True)
class Delivery:
    values: CreationPlanPreflightInput
    serialized: dict


@dataclass(frozen=True)
class DeliveryGoal:
    goal: str
    name: str | None
    overrides: dict
    fingerprint_payload: dict


@dataclass(frozen=True)
class WindowInput:
    expected_revision: int
    goals: tuple[DeliveryGoal, ...]


@dataclass(frozen=True)
class NormalizeWindow:
    expected_revision: int
    goals: list[dict]


@dataclass(frozen=True)
class CreateGoal:
    goal: str
    request_key: str
    target_count: int
    duration_mode: str
    duration_target_seconds: int
    material_scope: str
    output_relation: str | None
    assistant_turn_id: str | None
    requirements: dict
    intent: dict | None


@dataclass(frozen=True)
class ReviseGoal:
    expected_revision: int
    output_relation: str | None
    material_scope: str | None
    requirements: dict | None
    tool_turn_id: str | None


@dataclass(frozen=True)
class ConfirmWindow:
    expected_revision: int
    input_fingerprint: str
    adoption_kind: str
    assistant_turn_id: str | None


def clarification_questions(target_count: int, relation: str | None, scope: str) -> list[str]:
    questions = []
    if target_count > 1 and relation not in {"different_topics", "variants", "series"}:
        questions.append("这些视频是不同主题、同一主题的不同版本，还是系列分集？")
    if scope == "today_received":
        questions.append("请明确本次要用的今日接收素材，或选择使用现有素材库。")
    return questions


def creation_number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = section = number = 0
    for character in value:
        if character.isascii() and character.isdigit():
            number = number * 10 + int(character)
        elif character in digits:
            number = number * 10 + digits[character]
        elif character == "万":
            total += (section + number) * 10000
            section = number = 0
        else:
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[character]
            number = 0
    return total + section + number


def explicit_creation_constraints(goal: str) -> tuple[int | None, int | None]:
    from app.creation.intent_use_case import CreationIntentUseCase
    number = r"[0-9零〇一二两三四五六七八九十百千万]+"
    count = re.search(rf"({number})\s*(?:条|个|支)(?:短)?视频|(?:做|制作|生成|剪成)\s*({number})\s*(?:条|个|支)(?!镜头)", goal)
    duration = re.search(rf"(?:每条|每个|每支)(?:视频)?(?:为|做成|时长|要|控制在|长|约|大约|是|的|\s)*({number})\s*(分钟|秒钟|秒)", goal)
    if duration is None:
        duration = re.search(rf"(?:做成|制作|剪成)(?:一(?:个|条))?\s*({number})\s*(分钟|秒钟|秒)(?:的)?(?:短)?视频", goal)
    count_value = creation_number(next(value for value in count.groups() if value)) if count else None
    if count_value is None and CreationIntentUseCase.is_bilingual_variant_goal(goal):
        count_value = 2
    duration_value = creation_number(duration[1]) * (60 if duration[2] == "分钟" else 1) if duration else None
    return count_value, duration_value


def check_revision(current: dict, expected_revision: int) -> None:
    if current["revision"] != expected_revision:
        raise conflict("creation_request_revision_conflict", "制作目标的修订已变化，未应用旧要求。",
            "先读取当前制作目标，再按实际修订提交新要求。", request_id=current["id"],
            expected_revision=expected_revision, current_revision=current["revision"])


def require_running_turn(turn):
    if turn is None or turn["status"] != "running":
        raise CreationRequestWorkflowRejected(409, "当前创作回合已变化，没有保存或采用制作目标。")


def current_input(turn, input_id):
    latest = latest_consumed_input(turn["events"])
    if latest and latest["status"] == "interrupted":
        raise CreationRequestWorkflowRejected(409, "当前输入已中断，不能恢复先前制作授权。")
    active = active_input(turn["events"])
    if input_id != (active["input_id"] if active else None):
        raise CreationRequestWorkflowRejected(409, "制作目标不属于当前已消费输入，没有沿旧输入保存或采用。")
    return CurrentInput(turn, active["message"] if active else turn["user_message"], active)


class CreationRequestWorkflowUseCase:
    def __init__(self, *, records, adoption, preflight):
        self.records, self.adoption, self.preflight = records, adoption, preflight

    def prepare_delivery(self, workspace_id, delivery, *, frozen_plan=None):
        values = delivery.values
        try:
            self.preflight.check_template_selection(values)
            template = ((yield ReadTemplate(workspace_id, values.template_id, values.template_version))
                if values.template_id is not None else None)
            values = self.preflight.inherit_template(values, template)
            reader = yield ReadReader()
            return self.preflight.prepare(workspace_id, values, template=template,
                reader_user_id=reader.user_id, local_diagnostic=reader.local_diagnostic,
                **({"frozen_plan": frozen_plan} if frozen_plan is not None else {}))
        except CreationPlanPreflightRejected as exc:
            status = {"template_version_required": 422, "template_id_required": 422,
                "knowledge_outside_automation_scope": 409, "knowledge_version_unreadable": 404,
                "knowledge_automation_forbidden": 409, "knowledge_reference_forbidden": 409}[exc.code]
            failure = CreationRequestWorkflowRejected(status, exc.message)
            if exc.__cause__ is not None:
                raise failure from exc.__cause__
            raise failure from None

    def create(self, payload: CreateGoal):
        reader = yield ReadReader()
        workspace_id, owner, diagnostic = reader.workspace_id, reader.user_id, reader.local_diagnostic
        if not owner:
            raise CreationRequestWorkflowRejected(status_code=403, detail="请先确认当前成员，再保存制作目标。")
        goal = payload.goal
        source_input = None
        if payload.assistant_turn_id is not None:
            turn, goal, source_input = (yield ReadInput(payload.assistant_turn_id))
            if str(payload.request_key) != (source_input["input_id"] if source_input else turn["id"]):
                raise CreationRequestWorkflowRejected(status_code=409, detail="制作目标必须沿本轮固定身份保存，不能重复建立。")
        from app.creation.intent_use_case import CreationIntentUseCase
        try:
            CreationIntentUseCase.require_duration_authority(payload.duration_mode,
                delegated=payload.assistant_turn_id is not None)
        except ValueError as exc:
            raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        stated_count, stated_duration = explicit_creation_constraints(goal)
        if stated_count is not None and stated_count != payload.target_count:
            raise CreationRequestWorkflowRejected(status_code=409, detail="总数量与用户原句不一致，没有保存错误的制作目标。")
        if stated_duration is not None and stated_duration != payload.duration_target_seconds:
            raise CreationRequestWorkflowRejected(status_code=409, detail="目标时长与用户原句不一致，没有保存错误的制作要求。")
        # A model enum cannot impose a date restriction absent from the user's goal.
        scope = "today_received" if re.search(r"(?:今天|今日)(?:我)?(?:接收|收到|发给|传给|导入|上传|发来|传来)", goal) else ("workspace_library" if payload.assistant_turn_id else payload.material_scope)
        if re.search(r"(?:不限制|不限|不按|无需).{0,4}(?:今天|今日)", goal):
            scope = "workspace_library"
        relation = "single" if payload.target_count == 1 else payload.output_relation
        if payload.assistant_turn_id is not None and payload.target_count > 1:
            from app.creation.intent_use_case import CreationIntentUseCase
            explicit_relation = ("series" if re.search(r"系列|分集", goal) else
                "variants" if CreationIntentUseCase.is_bilingual_variant_goal(goal) or re.search(r"同(?:一|一个)?主题.{0,12}(?:版本|风格)|不同版本", goal) else
                "different_topics" if re.search(r"不同主题|各自主题|多个主题", goal) else None)
            if explicit_relation is not None and relation not in {None, explicit_relation}:
                raise CreationRequestWorkflowRejected(status_code=409, detail="多条视频的关系与用户原句不一致，没有保存错误的方案。")
            relation = explicit_relation
        values = dict(payload.requirements)
        values.update(goal=goal, target_count=1)
        if payload.intent is not None:
            from app.creation.intent_use_case import CreationIntentUseCase
            intent = payload.intent
            final = intent.get("final_script")
            if final and final.get("reply_origin"):
                origin = final["reply_origin"]
                reference, reply_text, reply_owner = (yield ReadReply(origin["conversation_id"], origin["turn_id"]))
                if reply_owner != owner or final["text"] != reply_text:
                    raise CreationRequestWorkflowRejected(status_code=409, detail="定稿与指定回复的实际正文不一致，没有采用改写的版本。")
                final["source_reply_reference"] = reference
            source = ({"kind": "conversation_input", "member_id": owner, "request_id": str(payload.request_key),
                "conversation_id": turn["conversation_id"], "turn_id": turn["id"],
                "message_digest": hashlib.sha256(goal.encode()).hexdigest(),
                **({"input_id": source_input["input_id"], "input_turn_id": turn["id"]} if source_input else {})} if payload.assistant_turn_id is not None else
                {"kind": "user_action", "member_id": owner, "request_id": str(payload.request_key)})
            try:
                values["creation_intent"] = CreationIntentUseCase.receive_intent(intent, source=source,
                    target_count=payload.target_count, original_message=goal if payload.assistant_turn_id else None)
            except ValueError as exc:
                raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        fingerprint = hashlib.sha256(json.dumps({
            "goal": goal, "target_count": payload.target_count, "output_relation": relation,
            "requirements": values, "material_scope": scope,
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            data = self.records.create_creation_request(
                workspace_id, request_key=str(payload.request_key), input_fingerprint=fingerprint,
                goal=goal, target_count=payload.target_count, output_relation=relation,
                requirements=values, material_scope={"kind": scope},
                clarification_questions=clarification_questions(payload.target_count, relation, scope),
                verified_owner_user_id=owner, local_diagnostic=diagnostic,
            )
        except ValueError as exc:
            raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        return data

    def revise(self, request_id: str, payload: ReviseGoal):
        if payload.tool_turn_id is not None:
            yield ReadInput(payload.tool_turn_id)
        current = (yield ReadOwnedRequest(request_id))
        check_revision(current, payload.expected_revision)
        reader = yield ReadReader()
        workspace_id, owner, diagnostic = reader.workspace_id, reader.user_id, reader.local_diagnostic
        relation = payload.output_relation or current["output_relation"]
        scope = payload.material_scope or current["material_scope"]["kind"]
        requirements = payload.requirements if payload.requirements else current["requirements"]
        if payload.requirements and current["requirements"].get("creation_intent"):
            requirements["creation_intent"] = current["requirements"]["creation_intent"]
        if requirements["target_count"] != 1:
            raise CreationRequestWorkflowRejected(status_code=422, detail="每条方案只制作一条视频；总数量保存在制作目标中。")
        try:
            data = self.records.revise_creation_request(
                workspace_id, request_id, expected_revision=payload.expected_revision,
                output_relation=relation, requirements=requirements, material_scope={"kind": scope},
                clarification_questions=clarification_questions(current["target_count"], relation, scope),
                verified_owner_user_id=owner, local_diagnostic=diagnostic,
            )
        except ValueError as exc:
            raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        return data

    def prepare_window(self, request_id: str, payload: WindowInput,
        *, tool_turn_id: str | None = None, replace_window_id: str | None = None):
        from app.creation.intent_use_case import CreationIntentUseCase
        if tool_turn_id is not None:
            yield ReadInput(tool_turn_id)
        current = (yield ReadOwnedRequest(request_id))
        check_revision(current, payload.expected_revision)
        if current["clarification_questions"]:
            raise CreationRequestWorkflowRejected(status_code=409, detail="先确认制作目标中尚未明确的条件，再准备逐条方案。")
        reader = yield ReadReader()
        workspace_id, owner, diagnostic = reader.workspace_id, reader.user_id, reader.local_diagnostic
        entries = []
        deliveries = []
        for item in payload.goals:
            deliveries.append((yield NormalizeDelivery(CreationIntentUseCase.delivery_inputs(
                current["requirements"], goal=item.goal, name=item.name,
                overrides=item.overrides))))
        try:
            CreationIntentUseCase.check_distinct_deliveries([item.serialized for item in deliveries])
        except ValueError as exc:
            raise CreationRequestWorkflowRejected(status_code=422, detail=str(exc)) from exc
        for inputs in deliveries:
            plan = (yield from self.prepare_delivery(workspace_id, inputs))
            try:
                plan = CreationIntentUseCase.use_provided_copy(plan, current["requirements"].get("creation_intent"))
            except ValueError as exc:
                raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
            plan.id = f"plan_{uuid4().hex[:12]}"
            task = CreationTask(id=f"task_{uuid4().hex[:12]}", workspace_id=workspace_id,
                plan_id=plan.id, name=plan.name, scenario=plan.goal)
            entries.append((plan, task))
        fingerprint = hashlib.sha256(json.dumps({
            "revision": payload.expected_revision, "goals": [item.fingerprint_payload for item in payload.goals],
            "preflights": [plan.preflight_fingerprint for plan, _task in entries],
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if tool_turn_id is not None:
            yield ReadInput(tool_turn_id)
        try:
            data = self.records.save_creation_request_window(
                workspace_id, request_id, expected_revision=payload.expected_revision,
                input_fingerprint=fingerprint, entries=entries, verified_owner_user_id=owner, local_diagnostic=diagnostic,
                replace_window_id=replace_window_id,
            )
        except ValueError as exc:
            raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        return data

    def replan(self, request_id: str, window_id: str, expected_revision: int,
        goals: tuple[DeliveryGoal, ...] | None, *, tool_turn_id: str | None = None):
        from app.creation.intent_use_case import CreationIntentUseCase
        current = (yield ReadOwnedRequest(request_id))
        check_revision(current, expected_revision)
        window = current["current_window"]
        if window is None or window["id"] != window_id or window["status"] != "proposed":
            raise conflict("creation_window_not_current", "这组候选已变化或已经采用，未改写原窗口。",
                "读取当前制作目标；只对尚未采用的当前窗口重新规划。", request_id=request_id,
                window_id=window_id, current_window_id=window["id"] if window else None,
                expected_revision=expected_revision, current_revision=current["revision"])
        if goals is not None:
            inputs = WindowInput(expected_revision, goals)
        else:
            inputs = yield NormalizeWindow(expected_revision, [{"goal": entry["plan"]["goal"], "name": entry["plan"]["name"],
                "requirements": {key: value for key, value in CreationIntentUseCase.frozen_delivery_inputs(
                    current["requirements"], entry["plan"]).items()
                    if key in {*CreationIntentUseCase.DELIVERY_FIELDS, "selected_voice", "selected_person"}}}
                for entry in window["entries"]])
        return (yield from self.prepare_window(request_id, inputs, tool_turn_id=tool_turn_id, replace_window_id=window_id))

    def confirm(self, request_id: str, window_id: str, payload: ConfirmWindow):
        from app.creation.intent_use_case import CreationIntentUseCase
        if payload.adoption_kind == "delegated_system" and payload.assistant_turn_id is not None:
            _turn, message, _input = (yield ReadInput(payload.assistant_turn_id))
            try:
                CreationIntentUseCase.reject_discussion_only_input(message)
            except ValueError as exc:
                raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
        current = (yield ReadOwnedRequest(request_id))
        window = current["current_window"]
        if current["requirements"].get("creation_intent", {}).get("kind") == "discussion":
            raise conflict("creation_window_not_authorized", "讨论没有制作授权，不能采用方案或文案。",
                "保留讨论方案；只有取得实际制作委托后才能采用。", request_id=request_id, window_id=window_id)
        if window is None or window["id"] != window_id:
            raise conflict("creation_window_not_current", "当前候选窗口已变化，未采用旧窗口。",
                "读取当前制作目标，核对新要求后重新规划或采用当前窗口。", request_id=request_id,
                window_id=window_id, current_window_id=window["id"] if window else None,
                expected_revision=payload.expected_revision, current_revision=current["revision"])
        if payload.input_fingerprint != window["input_fingerprint"]:
            raise conflict("creation_window_input_conflict", "窗口输入摘要不一致，未采用其他方案。",
                "读取当前窗口并核对实际逐条要求；不要重复旧摘要。", request_id=request_id, window_id=window_id,
                expected_input_fingerprint=payload.input_fingerprint, current_input_fingerprint=window["input_fingerprint"])
        if window["status"] == "proposed":
            check_revision(current, payload.expected_revision)
            for entry in window["entries"]:
                frozen = entry["plan"]
                inputs = yield NormalizeDelivery(
                    CreationIntentUseCase.frozen_delivery_inputs(current["requirements"], frozen))
                try:
                    live = CreationIntentUseCase.use_provided_copy((yield from self.prepare_delivery((yield ReadWorkspace()), inputs, frozen_plan=frozen)),
                        current["requirements"].get("creation_intent"))
                except ValueError as exc:
                    raise CreationRequestWorkflowRejected(status_code=409, detail=str(exc)) from exc
                blocking = next((item for item in live.blockers if item.blocks_confirmation), None)
                if blocking is not None:
                    reason = blocking.reason if blocking.reason in {"content_sources_missing", "template_voice_unavailable",
                        "template_material_matches_missing"} else "preflight_confirmation_blocked"
                    raise conflict("creation_window_blocked", reason,
                        "读取当前制作目标，修复所选资料或档案后重新规划；不要重复采用旧候选。",
                        request_id=request_id, window_id=window_id, plan_id=frozen["id"], stage=blocking.stage)
                if live.preflight_fingerprint != frozen["preflight_fingerprint"]:
                    raise conflict("creation_window_dependencies_changed", "实际所选素材、资料、档案或制作条件已变化，未采用旧方案。",
                        "读取当前制作目标和可用资源，用prepare_creation_window保存修订后的逐条要求再采用。",
                        request_id=request_id, window_id=window_id, plan_id=frozen["id"],
                        expected_input_fingerprint=frozen["preflight_fingerprint"], current_input_fingerprint=live.preflight_fingerprint)
        if payload.adoption_kind == "delegated_system" and payload.assistant_turn_id is not None:
            (yield ReadInput(payload.assistant_turn_id))
        reader = yield ReadReader()
        owner, diagnostic = reader.user_id, reader.local_diagnostic
        try:
            data = self.adoption.confirm_creation_request_window(
                reader.workspace_id, request_id, window_id, expected_revision=payload.expected_revision,
                input_fingerprint=payload.input_fingerprint, verified_owner_user_id=owner, local_diagnostic=diagnostic,
                adoption_kind=payload.adoption_kind,
            )
        except (PermissionError, KeyError, OSError) as exc:
            raise conflict("creation_window_resource_unavailable", "所选资源已变化、不可读取或当前不允许采用。",
                "读取当前所选资料和档案，修复来源或权限后重新规划。", request_id=request_id, window_id=window_id) from exc
        except ValueError as exc:
            latest = (yield ReadOwnedRequest(request_id))
            check_revision(latest, payload.expected_revision)
            raise conflict("creation_window_validation_rejected", "窗口当前授权、所选输入或任务状态未通过业务校验。",
                "读取当前制作目标，核对原委托与所选资源后修订并重新规划。", request_id=request_id, window_id=window_id,
                expected_revision=payload.expected_revision, current_revision=latest["revision"]) from exc
        return data
