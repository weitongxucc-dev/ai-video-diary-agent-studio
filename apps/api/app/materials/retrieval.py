"""Authorized material retrieval and grant-bound query encoding on one device queue.

Search never calls a model. It reads a completed private query cache or submits
one idempotent device unit. The queue is the only execution-state owner.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re

from app.device_runtime import OwnerRegistration
from app.device_scheduler import RecoveryBlocked, SchedulePolicy, SubmissionIntent, VerifiedWork
from app.granted_subprocess import GrantedCommandCancelled
from app.host_resource_probes import HostResourceRefusal, material_visual_query_resources
from app.materials.analysis import visual_cosine_similarity
from app.materials.contracts import (
    MaterialAsset, MaterialSegment, MaterialSearchResult, MaterialSegmentMatch,
    material_allows_automation, material_allows_footage, selectable_segment_usage_role,
)
from app.material_speech import speech_text_for_range
from app.visual_understanding import searchable_ocr_text
from app.visual_semantics import VisualSemanticError, evidence_timestamps, visual_feature_version


OWNER = "material_retrieval"
logger = logging.getLogger(__name__)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class MaterialRetrievalService:
    def __init__(self, *, assets, analysis, provider, database_path, verify_identity,
                 actor_from_identity, query_budget=None):
        self.assets, self.analysis, self.provider = assets, analysis, provider
        self.database_path = Path(database_path)
        self.verify_identity, self.actor_from_identity = verify_identity, actor_from_identity
        self.query_budget = query_budget
        assets.initialize_semantic_queries()

    @property
    def index_version(self):
        return visual_feature_version(self.provider.model_id)

    @property
    def execution_limits(self):
        return self.query_budget.execution_limits if self.query_budget is not None else None

    def registration(self):
        return OwnerRegistration(self, MaterialQueryExecutor(self))

    def _actor(self, workspace_id, actor):
        if actor is None or actor.workspace_id != workspace_id or self.verify_identity(actor) != "ready":
            raise PermissionError("素材检索需要当前已验证身份")
        return actor

    def _query_id(self, workspace_id, identity, query):
        return _digest([workspace_id, identity, query, self.provider.model_id, self.index_version])

    def _query_facts(self, row):
        identity = json.loads(row["identity_json"])
        actor = self.actor_from_identity(row["workspace_id"], identity)
        if self.verify_identity(actor) != "ready":
            raise PermissionError("视觉查询成员已失效")
        if row["model_id"] != self.provider.model_id or row["index_version"] != self.index_version:
            raise ValueError("视觉查询模型版本已失效")
        if row["id"] != self._query_id(row["workspace_id"], identity, row["query_text"]):
            raise ValueError("视觉查询输入身份已变化")
        if self.execution_limits is None or not self.provider.ready:
            raise VisualSemanticError("视觉查询模型或本机执行范围未就绪")
        resources = material_visual_query_resources(self.query_budget, query_chars=len(row["query_text"]))
        if (resources["model_id"], resources["model_version"]) != (self.provider.model_id, self.index_version):
            raise VisualSemanticError("视觉查询标定与当前编码器版本不一致")
        digest = _digest([row["id"], resources])
        return actor, digest, f"visual-query:v1:{digest}", resources

    def verify_submission(self, intent, actor):
        self._actor(intent.workspace_id, actor)
        row = self.assets.read_semantic_query(intent.workspace_id, intent.owner_id)
        original, digest, unit, resources = self._query_facts(row)
        if intent.owner_type != OWNER or actor != original or (intent.input_digest, intent.unit_key) != (digest, unit):
            raise PermissionError("视觉查询许可输入不一致")
        return VerifiedWork(intent.workspace_id, OWNER, row["id"], digest, unit,
            actor.member_id, "preview_dependency", resources)

    def check_current(self, request, conn):
        try:
            row = self.assets.read_semantic_query(request.workspace_id, request.owner_id, connection=conn)
            actor, digest, unit, resources = self._query_facts(row)
        except PermissionError:
            return "unauthorized"
        except (KeyError, ValueError):
            return "stale"
        except VisualSemanticError:
            return "dependency"
        if (request.owner_type != OWNER or request.member_id != actor.member_id
                or request.input_digest != digest or request.unit_key != unit
                or request.work_class != "preview_dependency" or request.source_refs
                or request.resources != resources):
            return "stale"
        return "ready"

    def capture_local(self, request, conn):
        def read(current):
            row = current.execute(
                """SELECT member_id,identity_json,query_text,model_id,index_version
                   FROM material_semantic_queries WHERE workspace_id=? AND id=?""",
                (request.workspace_id, request.owner_id),
            ).fetchone()
            resources = (material_visual_query_resources(self.query_budget, query_chars=len(row[2]))
                         if row is not None and self.execution_limits is not None else None)
            return tuple(row) if row else None, self.provider.model_id, self.index_version, resources
        expected = read(conn)
        return lambda current: "ready" if read(current) == expected else "stale"

    def check_local(self, request, conn, observed):
        return observed(conn)

    def can_manage(self, request, actor):
        try:
            self._actor(request.workspace_id, actor)
            row = self.assets.read_semantic_query(request.workspace_id, request.owner_id)
        except (PermissionError, KeyError):
            return False
        return request.owner_type == OWNER and json.loads(row["identity_json"]) == actor.identity_payload()

    can_view_detail = can_manage
    can_wake = can_manage

    def verify_retry(self, request, actor, reason, conn):
        from app.device_scheduler import VerifiedRetry
        if not self.can_manage(request, actor):
            raise PermissionError("不能重试其他成员的视觉查询")
        return VerifiedRetry(actor.member_id)

    def search(self, workspace_id, *, query="", asset_ids=None, eligible_segment_refs=None,
               usage_roles=None, tags=None, min_duration_seconds=None, max_duration_seconds=None,
               limit=20, viewer_user_id=None, local_diagnostic=False, actor=None, scheduler=None,
               automatic_selection=False):
        normalized = query.strip().casefold()
        roles = set(usage_roles or [])
        required_tags = {value.strip().casefold() for value in (tags or [])}
        if (len(normalized) > 120 or not 1 <= limit <= 100
                or not roles <= {"unassigned", "a_roll", "b_roll", "exclude"}
                or "" in required_tags):
            raise ValueError("素材查询、用途、标签或分页无效")
        for duration in (min_duration_seconds, max_duration_seconds):
            if duration is not None and (not math.isfinite(duration) or duration < 0):
                raise ValueError("镜头时长范围无效")
        if min_duration_seconds is not None and max_duration_seconds is not None and min_duration_seconds > max_duration_seconds:
            raise ValueError("镜头时长范围无效")
        if actor is not None:
            self._actor(workspace_id, actor)
            if viewer_user_id != (actor.user_id or actor.member_id):
                raise PermissionError("素材读取身份与设备查询身份不一致")

        def candidates():
            selected = set(asset_ids) if asset_ids is not None else None
            available = self.assets.list_assets(workspace_id, include_private=False,
                viewer_user_id=viewer_user_id, local_diagnostic=local_diagnostic)
            result = []
            for asset in available:
                if selected is not None and asset.id not in selected:
                    continue
                allowed = material_allows_automation(asset) if automatic_selection else material_allows_footage(asset)
                if asset.analysis_status != "ready" or asset.review_status != "approved" or not allowed or asset.usage_role == "exclude":
                    continue
                for segment in asset.segments:
                    role = selectable_segment_usage_role(asset, segment)
                    if (segment.review_status != "approved" or role == "exclude"
                            or (roles and role not in roles)
                            or (eligible_segment_refs is not None and (asset.id, segment.id) not in eligible_segment_refs)):
                        continue
                    duration = segment.end_seconds - segment.start_seconds
                    if duration <= 0 or (min_duration_seconds is not None and duration < min_duration_seconds) or (max_duration_seconds is not None and duration > max_duration_seconds):
                        continue
                    if required_tags - {value.casefold() for value in (*asset.user_tags, *segment.system_tags, *segment.user_tags)}:
                        continue
                    result.append((asset, segment))
            return result

        eligible = candidates()  # permission, approval and use-state precede even cache reads
        status, message, request_id, vector, features = "not_requested", "未请求画面语义", None, None, {}
        if normalized and not eligible:
            message = "当前没有满足权限、审核和筛选条件的片段，未提交画面推理"
        if normalized and eligible:
            status, message = "unavailable", "画面语义不可用；当前仅按文字与结构条件检索"
            try:
                features = self.analysis.read_visual_features(workspace_id, eligible, self.provider.model_id)
                if not features:
                    status, message = "index_missing", "当前可用片段缺少本模型版本的有效视觉特征"
                elif actor is not None:
                    identity = actor.identity_payload()
                    query_id = self._query_id(workspace_id, identity, normalized)
                    self.assets.save_semantic_query(query_id, workspace_id, identity, normalized,
                                                   self.provider.model_id, self.index_version)
                    row = self.assets.read_semantic_query(workspace_id, query_id)
                    if row["vector_json"] is not None:
                        vector = _valid_vector(json.loads(row["vector_json"]))
                        status, message = "ready", "已使用当前模型的查询向量和有效片段特征；相似度不是准确概率"
                    elif scheduler is not None:
                        _, digest, unit, _ = self._query_facts(row)
                        request = scheduler.submit(SubmissionIntent(workspace_id, OWNER, query_id,
                            digest, unit, SchedulePolicy()), actor)
                        request_id = request.id
                        status, message = "pending", "画面查询已进入设备队列，当前结果仅含文字或结构候选"
                        if request.wait_reason:
                            message += f"；设备等待原因：{request.wait_reason}"
                        if request.state in {"failed", "cancelled", "done"}:
                            status, message = "unavailable", "视觉查询没有可用结果；请查看设备请求并显式重试"
            except (OSError, ValueError, VisualSemanticError) as exc:
                status, message = "unavailable", f"画面语义不可用：{exc}；当前仅按文字与结构条件检索"

        # A concurrent withdrawal/edit cannot survive through the initially qualified snapshot.
        current = candidates()
        structured = bool(roles or required_tags or min_duration_seconds is not None or max_duration_seconds is not None)
        matches = []
        for asset, segment in current:
            match = _lexical_match(asset, segment, normalized, structured, roles, tags or [], min_duration_seconds, max_duration_seconds)
            feature = features.get((asset.id, segment.id))
            if feature is not None and vector is not None and len(feature.vector) != len(vector):
                status, message = "partial_index", "部分片段向量维度与查询不符，已跳过这些语义候选"
                feature = None
            if feature is not None and vector is not None:
                # Re-read the exact current source/segment fingerprint after ranking inputs changed.
                current_feature = self.analysis.read_visual_features(workspace_id, [(asset, segment)], self.provider.model_id).get((asset.id, segment.id))
                if current_feature is not None and current_feature.fingerprint == feature.fingerprint:
                    similarity = visual_cosine_similarity(vector, _valid_vector(feature.vector))
                    if similarity > 0:
                        if match is None:
                            match = _base_match(asset, segment, 0.0, [], "semantic")
                        match = replace(match,
                            visual_model_id=self.provider.model_id, visual_model_version=self.index_version,
                            visual_similarity=round(similarity, 6), visual_sample_count=feature.sample_count,
                            visual_sample_timestamps=evidence_timestamps(segment.start_seconds, segment.end_seconds),
                            reasons=[*match.reasons, "画面相似仅辅助排序；不证明型号或动作事实"])
            if match is not None and not (automatic_selection and match.needs_confirmation):
                matches.append(match)
        # Reciprocal ranks keep cosine and lexical scores in separate scales.
        visual = sorted((item for item in matches if item.visual_similarity is not None), key=lambda item: (-item.visual_similarity, item.asset_ref, item.segment_ref))
        lexical = sorted((item for item in matches if item.match_tier != "semantic"), key=lambda item: (-item.score, item.asset_ref, item.segment_ref))
        ranks = {}
        for ranked in (lexical, visual):
            for index, item in enumerate(ranked, 1):
                key = item.asset_ref, item.segment_ref
                ranks[key] = ranks.get(key, 0) + 1 / (60 + index)
        matches.sort(key=lambda item: (-ranks[(item.asset_ref, item.segment_ref)], item.asset_ref, item.segment_ref))
        selected = [replace(item, score=round(ranks[(item.asset_ref, item.segment_ref)], 6), rank=index)
                    for index, item in enumerate(matches[:limit], 1)]
        return MaterialSearchResult(selected, status, message, request_id)


class MaterialQueryExecutor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, grant, scheduler):
        owner = self.owner
        try:
            scheduler.assert_grant(grant)
            row = owner.assets.read_semantic_query(grant.workspace_id, grant.owner_id)
            actor, digest, unit, _ = owner._query_facts(row)
            if (grant.owner_type, grant.input_digest, grant.unit_key) != (OWNER, digest, unit):
                raise PermissionError("视觉查询执行许可与输入不一致")
            provider = owner.provider.for_query_grant(scheduler, grant, owner.execution_limits)
            vector = _valid_vector(provider.embed_texts([row["query_text"]])[0])
            scheduler.assert_grant(grant)
            scheduler.complete_unit(grant, checkpoint_database_path=owner.database_path,
                checkpoint_write=lambda conn, _: owner.assets.commit_semantic_query(
                    conn, grant.workspace_id, grant.owner_id, vector))
        except HostResourceRefusal as exc:
            if exc.recoverable_pressure:
                scheduler.defer_unit(grant, reason="resource_pressure",
                    retry_not_before_utc=datetime.now(timezone.utc) + timedelta(seconds=30))
            else:
                scheduler.fail_unit(grant, reason="visual_query_resource_unavailable", failure_write=lambda *_: None)
        except RecoveryBlocked:
            raise
        except GrantedCommandCancelled:
            scheduler.complete_unit(grant, checkpoint_database_path=owner.database_path,
                                    checkpoint_write=lambda *_: None, cancelled=True)
        except Exception:
            logger.exception("Visual query failed (device request %s)", grant.request_id)
            scheduler.fail_unit(grant, reason="visual_query_failed", failure_write=lambda *_: None)


def _valid_vector(vector):
    if (not isinstance(vector, list) or not vector
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector)
            or sum(value * value for value in vector) <= 1e-12):
        raise ValueError("视觉向量为空、非有限或零长度")
    return vector


def _material_contains_text(text: str, phrase: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase in text
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def _base_match(asset, segment, score, reasons, tier, *, terms=None, conditions=None):
    return MaterialSegmentMatch(asset_ref=f"asset:{asset.id}", segment_ref=f"segment:{segment.id}",
        score=score, reasons=reasons, match_tier=tier, matched_terms=terms or [], matched_conditions=conditions or [],
        asset_file_name=asset.file_name, start_seconds=segment.start_seconds, end_seconds=segment.end_seconds,
        duration_seconds=round(segment.end_seconds - segment.start_seconds, 3),
        time_range=f"{_material_timecode(segment.start_seconds)} – {_material_timecode(segment.end_seconds)}",
        tags=list(dict.fromkeys([*segment.system_tags, *segment.user_tags, *asset.user_tags])),
        evidence_excerpt=_material_searchable_corpus(segment, asset)[:900], needs_confirmation=tier in {"partial", "structural", "semantic"})


def _lexical_match(asset, segment, query, structured, roles, tags, lower, upper):
    score, reasons, conditions = 0.1, ["当前工作区已审核素材"], []
    if roles:
        role = selectable_segment_usage_role(asset, segment)
        score += .25
        reasons.append(f"用途匹配 {role}")
        conditions.append(f"用途 {role}")
    if tags:
        score += .15 * len(tags)
        reasons.append("标签匹配 " + "、".join(tags))
        conditions.append("标签 " + "、".join(tags))
    duration = segment.end_seconds - segment.start_seconds
    if lower is not None or upper is not None:
        score += .1
        reasons.append(f"时长 {duration:.1f} 秒在指定范围内")
        conditions.append(f"时长 {duration:.1f} 秒")
    if lower is not None and duration > lower:
        score += min(.15, .02 * (duration - lower))
    tier, terms = "structural", []
    if query:
        text_reasons = _material_query_reasons(query, segment, asset)
        terms = _material_partial_terms(query, segment, asset)
        if text_reasons:
            tier, score = "text", score + .45 + .1 * len(text_reasons)
            reasons.extend(text_reasons)
        elif terms:
            tier, score = "partial", score + .25 + .03 * min(len(terms), 5)
            reasons.append("文字未整段匹配，分词命中 " + "、".join(terms))
        elif structured:
            reasons.append("文字未匹配，按结构化条件返回弱候选")
        else:
            return None
    return _base_match(asset, segment, score, reasons, tier, terms=terms, conditions=conditions)


_MATERIAL_QUERY_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "to", "in", "on", "at", "is", "it", "and", "or", "for"}
)
# 中文虚字：切分前先剔掉，它们不携带检索意图。
# 「更」「长」「太」「吵」都不在表内，所以「更长」「太吵」能切出来。
_MATERIAL_QUERY_FUNCTION_CHARS = frozenset("的了着过和与或是在这那不也就都而之其为以于被把从对向")


def _material_query_units(normalized_query: str) -> list[str]:
    """把一句查询切成可核对的最小单元：英文/数字词 + 中文二元组。"""
    units: list[str] = []
    for token in re.findall(r"[^\W_]+", normalized_query):
        chunks = (
            re.findall(r"[\u4e00-\u9fff]+", token)
            if re.search(r"[\u4e00-\u9fff]", token)
            else [token]
        )
        for chunk in chunks:
            if re.search(r"[\u4e00-\u9fff]", chunk):
                # 先剔虚字再切二元组：「更长一点的」要切出「更长」而不是「点的」。
                meaningful = "".join(
                    char for char in chunk if char not in _MATERIAL_QUERY_FUNCTION_CHARS
                )
                units.extend(
                    meaningful[index : index + 2] for index in range(len(meaningful) - 1)
                )
            elif len(chunk) >= 2 and chunk not in _MATERIAL_QUERY_STOPWORDS:
                units.append(chunk)
    # 去重且保持出现顺序，让「为什么被选中」稳定可复现。
    return list(dict.fromkeys(units))


def _material_searchable_corpus(segment: MaterialSegment, asset: MaterialAsset) -> str:
    """一个片段所有能被人读出来的文字，作为「依据」的取样范围。"""
    return " ".join(
        value.casefold()
        for value in (
            segment.description,
            searchable_ocr_text(segment.understanding),
            segment.transcript,
            *segment.system_tags,
            *segment.user_tags,
            *(asset.user_tags if isinstance(asset, MaterialAsset) else ()),
        )
        if value
    )


def _material_partial_terms(
    normalized_query: str, segment: MaterialSegment, asset: MaterialAsset
) -> list[str]:
    """字面没整段命中时，看查询里还有哪些词真的出现在这条素材上。"""
    corpus = _material_searchable_corpus(segment, asset)
    if not corpus:
        return []
    return [unit for unit in _material_query_units(normalized_query) if _material_contains_text(corpus, unit)]


def _material_timecode(seconds: float) -> str:
    """把秒数写成 00:12 这种人能读的时间点。"""
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _material_query_reasons(
    normalized_query: str, segment: MaterialSegment, asset: MaterialAsset
) -> list[str]:
    reasons: list[str] = []
    text_fields = (
        ("片段描述", segment.description),
        ("本地画面观察", segment.understanding.summary if not segment.description and segment.understanding and segment.understanding.status == "ready" else ""),
        ("原片画面文字", searchable_ocr_text(segment.understanding)),
        ("片段转录", segment.transcript or speech_text_for_range(asset.speech_analysis, segment.start_seconds, segment.end_seconds)),
    )
    query_terms = re.findall(r"[^\W_]+", normalized_query)

    def contains_text(text: str, phrase: str) -> bool:
        return _material_contains_text(text, phrase)
    for label, raw_value in text_fields:
        value = raw_value.strip().casefold()
        if not value:
            continue
        if (
            contains_text(value, normalized_query)
            or contains_text(normalized_query, value)
            or any(contains_text(value, term) for term in query_terms)
        ):
            reasons.append(f"自然语言匹配{label}")
    for source, values in (
        ("系统标签", segment.system_tags),
        ("用户标签", segment.user_tags),
        ("整条素材标签", asset.user_tags if isinstance(asset, MaterialAsset) else []),
    ):
        matched = []
        for raw_value in values:
            value = raw_value.casefold()
            suffix = value.split(":", 1)[-1]
            if value in normalized_query or suffix in normalized_query:
                matched.append(raw_value)
        reasons.extend(
            f"自然语言匹配{source} {value}" for value in matched
        )
    role_aliases = {
        "a_roll": ("人物主镜头", "主镜头", "口播画面", "人物画面"),
        "b_roll": ("补充画面", "素材画面", "辅助镜头", "产品画面"),
    }
    role = segment.usage_role
    if role == "unassigned":
        role = next(
            (
                value.split(":", 1)[1]
                for value in segment.system_tags
                if value.startswith("用途:")
                and value.split(":", 1)[1] in {"a_roll", "b_roll"}
            ),
            "unassigned",
        )
    if role in role_aliases and any(
        alias in normalized_query for alias in role_aliases[role]
    ):
        reasons.append(f"自然语言匹配用途 {role}")
    return reasons
