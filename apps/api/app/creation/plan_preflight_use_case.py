"""Concrete read-only Plan preflight from validated values and canonical owners."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Literal

from app.avatar_lipsync import AvatarAndLipSyncProvider
from app.materials.contracts import MaterialSegmentMatch, effective_segment_usage_role
from app.model_runtime import ModelCatalog
from app.creation.video_template_records import VideoTemplate
from app.production import ProductionRepository
from app.creation.plan_contracts import CreationPlan, CreationPlanBlocker, CreationPlanKnowledgeReference, CreationPlanPronunciationReference, CreationPlanSourceReference
from app.source_policy import content_allows_use
from app.source_repository import SourceRepository
from app.source_types import source_item_identity


@dataclass(frozen=True)
class CreationPlanPreflightInput:
    goal: str
    name: str | None
    target_count: int
    language: str
    aspect_ratio: str
    duration_target_seconds: int
    presenter_share_target: int
    lipsync_quality: str
    template_id: str | None
    template_version: int | None
    selected_knowledge: tuple[str, str] | None
    selected_voice: tuple[str, str] | None
    selected_person: tuple[str, str] | None
    provided_fields: frozenset[str]
    duration_mode: str = "exact"
    duration_range_seconds: tuple[float, float] | None = None


PreflightRejectionCode = Literal["template_version_required", "template_id_required",
    "knowledge_outside_automation_scope", "knowledge_version_unreadable",
    "knowledge_automation_forbidden", "knowledge_reference_forbidden"]


class CreationPlanPreflightRejected(RuntimeError):
    def __init__(self, code: PreflightRejectionCode, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class CreationPlanPreflightUseCase:
    def __init__(self, production: ProductionRepository, sources: SourceRepository, catalog: ModelCatalog,
                 *, lipsync_provider_for: Callable[[str], AvatarAndLipSyncProvider]):
        self.production, self.sources, self.catalog = production, sources, catalog
        self.lipsync_provider_for = lipsync_provider_for

    @staticmethod
    def check_template_selection(payload: CreationPlanPreflightInput) -> None:
        if payload.template_id is not None:
            if payload.template_version is None:
                raise CreationPlanPreflightRejected("template_version_required", "请选择明确的模板版本。")
        elif payload.template_version is not None:
            raise CreationPlanPreflightRejected("template_id_required", "模板版本缺少对应模板。")

    @staticmethod
    def inherit_template(payload: CreationPlanPreflightInput, template: VideoTemplate | None) -> CreationPlanPreflightInput:
        if payload.template_id is not None:
            if template.settings.get('kind') == 'visual_component':
                raise CreationPlanPreflightRejected('visual_component_requires_task', '这是视觉组件，请在作品的图文与动画中使用；新建作品请选择制作结构或整片组合。')
            inherited = {key: template.settings[key] for key in ("language", "aspect_ratio", "duration_target_seconds") if key not in payload.provided_fields and key in template.settings}
            if "presenter_share_target" not in payload.provided_fields and "presenter_share_target" in template.settings:
                inherited["presenter_share_target"] = template.settings["presenter_share_target"]
            if "lipsync_quality" not in payload.provided_fields and "lipsync_quality" in template.settings:
                inherited["lipsync_quality"] = template.settings["lipsync_quality"]
            elif "lipsync_quality" not in payload.provided_fields:
                inherited["lipsync_quality"] = "fast"
            payload = replace(payload, **inherited, provided_fields=payload.provided_fields | frozenset(inherited))
        return payload

    def prepare(self, workspace_id: str, payload: CreationPlanPreflightInput, *, template: VideoTemplate | None,
                reader_user_id: str | None, local_diagnostic: bool,
                automation_scope: dict[str, list[str]] | None = None, frozen_plan: dict | None = None) -> CreationPlan:
        _plan_user_id, _plan_local_diagnostic = reader_user_id, local_diagnostic
        knowledge_references = [
            CreationPlanKnowledgeReference(
                id=item.id,
                version=item.version,
                title=item.title,
                authorization=item.authorization,
                ownership=item.ownership,
            )
            for item in self.sources.list_knowledge_items_for_use(
                workspace_id, purpose="reference", user_id=_plan_user_id,
                local_diagnostic=_plan_local_diagnostic,
            ) if item.use_state != "not_for_automation"
        ]
        if payload.selected_knowledge is not None:
            pin = payload.selected_knowledge
            if automation_scope is not None and pin[0] not in automation_scope["knowledge_ids"]:
                raise CreationPlanPreflightRejected("knowledge_outside_automation_scope", "固定知识不在本次自动化明确范围内。")
            try:
                item = self.sources.get_knowledge_reference_readable_by(
                    workspace_id, pin[0], pin[1],
                    user_id=_plan_user_id, local_diagnostic=_plan_local_diagnostic,
                )
            except KeyError as exc:
                raise CreationPlanPreflightRejected("knowledge_version_unreadable", "所选资料的固定版本不存在或不可读取，请重新选择。") from exc
            if automation_scope is not None and item.use_state == "not_for_automation":
                raise CreationPlanPreflightRejected("knowledge_automation_forbidden", "固定知识当前不允许自动化使用。")
            if not content_allows_use(item.use_state, "reference"):
                raise CreationPlanPreflightRejected("knowledge_reference_forbidden", "所选资料当前不允许引用，本次未创建任务。")
            knowledge_references = [reference for reference in knowledge_references if reference.id != item.id]
            knowledge_references.append(CreationPlanKnowledgeReference(
                id=item.id, version=item.version, title=item.title,
                authorization=item.authorization, ownership=item.ownership,
            ))
        source_references = [
            CreationPlanSourceReference(
                id=item.id,
                connector_id=item.connector_id,
                source_label=item.source_label,
                title=item.title,
                license=item.license,
                identity=source_item_identity(item),
            )
            for item in self.sources.list_source_items_for_use(
                workspace_id, purpose="reference"
            ) if item.use_state != "not_for_automation"
        ]
        if automation_scope is not None:
            knowledge_references = [item for item in knowledge_references if item.id in automation_scope["knowledge_ids"]]
            source_references = [item for item in source_references if item.connector_id in automation_scope["connector_ids"]]
        if frozen_plan is not None:
            source_ids = {item["id"] for item in frozen_plan["source_references"]}
            knowledge_references = []
            for reference in frozen_plan["knowledge_references"]:
                try:
                    item = self.sources.get_knowledge_reference_readable_by(workspace_id, reference["id"], reference["version"],
                        user_id=_plan_user_id, local_diagnostic=_plan_local_diagnostic)
                except KeyError:
                    continue  # The missing dependency changes the digest; it never authorizes the old Plan.
                if item.use_state == "not_for_automation" or not content_allows_use(item.use_state, "reference"):
                    continue
                knowledge_references.append(CreationPlanKnowledgeReference(id=item.id, version=item.version,
                    title=item.title, authorization=item.authorization, ownership=item.ownership))
            source_references = [item for item in source_references if item.id in source_ids]
        usable = self.production.automatic_material_segments(workspace_id, viewer_user_id=_plan_user_id,
                                                              local_diagnostic=_plan_local_diagnostic)
        if automation_scope is not None:
            usable = [(asset, segment) for asset, segment in usable if asset.id in automation_scope["material_asset_ids"]]
        frozen_material_matches: list[MaterialSegmentMatch] | None = None
        if frozen_plan is not None:
            chosen_pairs = {(item["asset_ref"].removeprefix("asset:"), item["segment_ref"].removeprefix("segment:"))
                for item in frozen_plan["material_matches"]}
            usable = [(asset, segment) for asset, segment in usable if (asset.id, segment.id) in chosen_pairs]
            # A request window is an Agent/user adoption boundary. Reuse the
            # exact candidate snapshot that was shown in that window; do not
            # run a second ranking pass that could silently change its reasons
            # or evidence.
            frozen_material_matches = [
                MaterialSegmentMatch(**item)
                for item in frozen_plan.get("material_matches", [])
                if isinstance(item, dict)
            ]
        approved_assets = list({asset.id: asset for asset, _ in usable}.values())
        approved_clips = [segment for asset, segment in usable if effective_segment_usage_role(asset, segment) == "b_roll"]
        eligible_material_pairs = {(asset.id, segment.id) for asset, segment in usable}
        eligible_material_ids = sorted({asset_id for asset_id, _ in eligible_material_pairs})
        if frozen_material_matches is not None:
            material_matches = [
                match for match in frozen_material_matches
                if (match.asset_ref.removeprefix("asset:"), match.segment_ref.removeprefix("segment:"))
                in eligible_material_pairs
            ]
        else:
            material_matches = self.production.material_search.search_material_segments(
                workspace_id,
                query=payload.goal,
                asset_ids=eligible_material_ids,
                eligible_segment_refs=eligible_material_pairs,
                usage_roles=["a_roll", "b_roll"],
                limit=8,
                viewer_user_id=_plan_user_id,
                local_diagnostic=_plan_local_diagnostic,
            ) if eligible_material_ids else []
            material_matches = [match for match in material_matches if (match.asset_ref.removeprefix("asset:"), match.segment_ref.removeprefix("segment:")) in eligible_material_pairs]
            if not material_matches and template is None:
                material_matches = self.production.material_search.search_material_segments(
                    workspace_id,
                    asset_ids=eligible_material_ids,
                    eligible_segment_refs=eligible_material_pairs,
                    usage_roles=["a_roll", "b_roll"],
                    limit=8,
                    viewer_user_id=_plan_user_id,
                    local_diagnostic=_plan_local_diagnostic,
                ) if eligible_material_ids else []
                material_matches = [match for match in material_matches if (match.asset_ref.removeprefix("asset:"), match.segment_ref.removeprefix("segment:")) in eligible_material_pairs]
        if automation_scope is not None:
            material_matches = [match for match in material_matches if match.asset_ref.removeprefix("asset:") in automation_scope["material_asset_ids"]]
        language_to_voice_language = {
            "zh-CN": "zh",
            "en-US": "en",
            "zh-CN/en-US": "zh-en",
        }
        requested_voice_language = language_to_voice_language[payload.language]
        active_voices = []
        required_voice = template.settings.get("voice") if template else None
        explicit = payload.selected_voice
        default_voice = self.production.get_default_versioned_voice(workspace_id) if explicit is None and required_voice is None else None
        voice_media_failure = None
        try:
            voices = ([self.production.profile_records.get_versioned_voice_profile(workspace_id, explicit[0], explicit[1])] if explicit else
                      [self.production.profile_records.get_versioned_voice_profile(workspace_id, required_voice["profile_id"], required_voice["version"])] if required_voice else
                      [default_voice] if default_voice else [])
        except KeyError:
            voices = []
            voice_media_failure = "voice_reference_missing"
        selected_voice = None
        selected_person = None
        person_authorization_failure = None
        pronunciation_references = []
        for voice in voices:
            try:
                reference = self.production.creation_plan_voice_reference(workspace_id, voice.id, voice.version,
                    viewer_user_id=_plan_user_id, local_diagnostic=_plan_local_diagnostic)
                if voice.language not in {requested_voice_language, "zh-en"}:
                    raise ValueError("voice_language_unavailable")
                selected_voice = reference
                active_voices.append(voice)
                pronunciation_references = [CreationPlanPronunciationReference(id=entry.id, version=entry.version,
                    scope=entry.scope, scope_reference=entry.scope_reference, language=entry.language)
                    for entry in self.production.profile_records.list_applicable_pronunciation_entries(workspace_id=workspace_id,
                        person_profile_id=voice.person_profile_id, episode_id="")]
            except (PermissionError, ValueError, OSError, KeyError) as exc:
                voice_media_failure = str(exc)
        if payload.selected_person is not None:
            try:
                selected_person = self.production.creation_plan_person_reference(workspace_id,
                    payload.selected_person[0], payload.selected_person[1],
                    viewer_user_id=_plan_user_id, local_diagnostic=_plan_local_diagnostic)
            except (PermissionError, ValueError, OSError, KeyError) as exc:
                person_authorization_failure = str(exc)
        elif payload.presenter_share_target > 0:
            person_authorization_failure = "person_reference_missing"
        model_snapshot = [
            {
                "id": status.model.id,
                "capability": status.model.capability,
                "state": status.state,
                "ready": status.ready,
                "message": status.message,
            }
            for status in self.catalog.list_statuses()
        ]
        selected_lipsync_provider = None
        if payload.presenter_share_target > 0:
            selected_lipsync_provider = self.lipsync_provider_for(payload.lipsync_quality)
            model_snapshot.append({
                "id": selected_lipsync_provider.model_id,
                "capability": "avatar_lipsync",
                "state": "ready" if selected_lipsync_provider.ready else "unavailable",
                "ready": selected_lipsync_provider.ready,
                "message": selected_lipsync_provider.message,
            })
        model_by_id = {str(item["id"]): item for item in model_snapshot}
        blockers: list[CreationPlanBlocker] = []
        if not knowledge_references and not source_references:
            blockers.append(
                CreationPlanBlocker(
                    reason="content_sources_missing",
                    impact="没有已保存的知识库内容或信息源条目，不能可靠进入选题阶段。",
                    repair_action="先保存用户知识或完成一个可用信息源同步，再重新预检。",
                    stage="topic",
                    blocks_confirmation=True,
                )
            )
        required_template_voice = template.settings.get("voice") if template else None
        if selected_voice is None and required_template_voice:
            blockers.append(CreationPlanBlocker(
                reason="template_voice_unavailable",
                impact=f"模板指定声音 {required_template_voice['profile_id']} · {required_template_voice['version']} 不可用，未替换为其他声音。",
                repair_action="在人物与声音中恢复这个声音版本及授权，或返回选择其他模板版本。",
                stage="narration", blocks_confirmation=True,
            ))
        elif selected_voice is None:
            reason = (
                "voice_language_unavailable"
                if active_voices
                else voice_media_failure or "voice_reference_missing"
            )
            blockers.append(
                CreationPlanBlocker(
                    reason=reason,
                    impact="当前方案缺少可用的固定声音版本，配音阶段尚不可执行。",
                    repair_action="登记并选择当前工作区可用语言的声音版本，或调整方案语言。",
                    stage="narration",
                    blocks_confirmation=False,
                )
            )
        else:
            model = model_by_id.get(selected_voice.model_id)
            if model is None or not bool(model["ready"]):
                blockers.append(
                    CreationPlanBlocker(
                        reason="voice_model_unavailable",
                        impact="当前方案不能进入配音阶段，但可先创建任务并进行选题。",
                        repair_action="安装或修复所选声音版本要求的模型后，再从配音阶段继续。",
                        stage="narration",
                        blocks_confirmation=False,
                    )
                )
            if voice_media_failure is not None:
                blockers.append(
                    CreationPlanBlocker(
                        reason=voice_media_failure,
                        impact="当前声音版本没有可读取的已授权 WAV，配音阶段会停止。",
                        repair_action="在人物与声音中重新选择当前工作区已批准的 WAV 参考素材。",
                        stage="narration",
                        blocks_confirmation=False,
                    )
                )
        if person_authorization_failure is not None and payload.presenter_share_target > 0:
            blockers.append(
                CreationPlanBlocker(
                    reason=person_authorization_failure,
                    impact="当前方案不能进入人物口型阶段，但可先创建任务并进行选题。",
                    repair_action="选择可用人物参考，检查来源及明确使用限制后继续。",
                    stage="avatar_lipsync",
                    blocks_confirmation=False,
                )
            )
        if payload.presenter_share_target > 0 and not selected_lipsync_provider.ready:
            blockers.append(CreationPlanBlocker(
                reason="person_render_unavailable",
                impact=f"所选{('高精度' if payload.lipsync_quality == 'high' else '快速')}口型尚不可用，当前露脸比例无法进入成片。",
                repair_action="检查所选档位的本地口型环境，或主动选择另一档／将露脸占比调到 0%。",
                stage="avatar_lipsync", blocks_confirmation=False,
            ))
        if not approved_clips and payload.presenter_share_target < 100:
            blockers.append(
                CreationPlanBlocker(
                    reason="approved_material_clips_missing",
                    impact="当前方案不能进入自动选片和视频剪辑阶段。",
                    repair_action="导入并分析可用的补充镜头；检查来源、范围和主动使用限制后继续。",
                    stage="video_editing",
                    blocks_confirmation=False,
                )
            )
        if template is not None and not material_matches and payload.presenter_share_target < 100:
            blockers.append(CreationPlanBlocker(
                reason="template_material_matches_missing",
                impact="已有素材没有匹配本次新主题，未沿用原任务镜头。",
                repair_action="前往素材库补充或审核与新主题相关的镜头，再重新检查。",
                stage="video_editing", blocks_confirmation=True,
            ))
        name = payload.name or payload.goal[:48]
        required_model_ids = ({selected_voice.model_id} if selected_voice else set())
        if selected_lipsync_provider is not None:
            required_model_ids.add(selected_lipsync_provider.model_id)
        fingerprint_payload = {
            "template_id": template.id if template else None,
            "template_version": template.version if template else None,
            "template_settings": template.settings if template else {},
            "goal": payload.goal,
            "name": name,
            "target_count": payload.target_count,
            "language": payload.language,
            "aspect_ratio": payload.aspect_ratio,
            "duration_target_seconds": payload.duration_target_seconds,
            "presenter_share_target": payload.presenter_share_target,
            "lipsync_quality": payload.lipsync_quality,
            "knowledge_references": [item.__dict__ for item in knowledge_references],
            "source_references": [item.__dict__ for item in source_references],
            "selected_voice": selected_voice.__dict__ if selected_voice else None,
            "selected_person": asdict(selected_person) if selected_person else None,
            "pronunciation_references": [
                item.__dict__ for item in pronunciation_references
            ],
            "material_rule_summary": "使用当前可自动选用的有效镜头；保留主动限制、人工疑点与匹配依据。",
            # Keep old preflight fingerprints stable when a historical
            # candidate had no governed evidence yet. New evidence IDs are
            # included and therefore become part of the adoption contract as
            # soon as they exist.
            "material_matches": [
                {
                    key: value for key, value in asdict(item).items()
                    if item.evidence_ref_ids or key != "evidence_ref_ids"
                }
                for item in material_matches
            ],
            "model_snapshot": [{key: model[key] for key in ("id", "capability", "state", "ready")}
                for model in model_snapshot if model["id"] in required_model_ids],
            "confirmation_points": ["确认资料与来源事实", "确认选题", "审核成片与发布"],
        }
        if payload.duration_mode != "exact":
            fingerprint_payload.update(duration_mode=payload.duration_mode,
                duration_range_seconds=payload.duration_range_seconds)
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return CreationPlan(
            id="",
            workspace_id=workspace_id,
            goal=payload.goal,
            name=name,
            target_count=payload.target_count,
            language=payload.language,
            aspect_ratio=payload.aspect_ratio,
            duration_target_seconds=payload.duration_target_seconds,
            duration_mode=payload.duration_mode,
            duration_range_seconds=payload.duration_range_seconds,
            presenter_share_target=payload.presenter_share_target,
            lipsync_quality=payload.lipsync_quality,
            knowledge_references=knowledge_references,
            source_references=source_references,
            selected_voice=selected_voice,
            selected_person=selected_person,
            pronunciation_references=pronunciation_references,
            approved_asset_count=len(approved_assets),
            approved_clip_count=len(approved_clips),
            material_rule_summary=str(fingerprint_payload["material_rule_summary"]),
            material_matches=material_matches,
            model_snapshot=model_snapshot,
            confirmation_points=list(fingerprint_payload["confirmation_points"]),
            blockers=blockers,
            preflight_fingerprint=fingerprint,
            template_id=template.id if template else None,
            template_version=template.version if template else None,
            template_settings=template.settings if template else {},
        )
