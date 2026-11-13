"""Qualified persisted-material search and explainable candidate ranking.

The asset store owns readable rows; file qualification remains an explicit port.
This use case never submits semantic inference or device execution.
"""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Literal, Protocol

from app.materials.contracts import (
    AssetUsageRole, MaterialAsset, MaterialSegment, MaterialSegmentMatch,
    material_usage_eligibility, selectable_segment_usage_role,
)
from app.materials.evidence_text import segment_text_evidence, segment_searchable_text


class ReadableAssets(Protocol):
    def __call__(self, workspace_id: str, *, include_private: bool,
                 viewer_user_id: str | None, local_diagnostic: bool) -> list[MaterialAsset]: ...


class SegmentEligibility(Protocol):
    def __call__(self, asset: MaterialAsset, segment: MaterialSegment,
                 workspace_id: str, *, automation_only: bool = False) -> bool: ...


class IndexStatusReader(Protocol):
    def __call__(self, workspace_id: str, asset_id: str, index_type: str) -> str | None: ...


class EvidenceRefReader(Protocol):
    def __call__(self, workspace_id: str, asset_id: str, segment_id: str) -> list[str]: ...


class MaterialSegmentSearchUseCase:
    def __init__(self, *, list_assets: ReadableAssets,
                 segment_eligible: SegmentEligibility,
                 index_status_for: IndexStatusReader | None = None,
                 evidence_refs_for: EvidenceRefReader | None = None) -> None:
        self._list_assets = list_assets
        self._segment_eligible = segment_eligible
        self._index_status_for = index_status_for
        self._evidence_refs_for = evidence_refs_for

    def search_material_segments(
        self,
        workspace_id: str,
        *,
        query: str = "",
        asset_ids: list[str] | None = None,
        eligible_segment_refs: set[tuple[str, str]] | None = None,
        usage_roles: list[AssetUsageRole] | None = None,
        tags: list[str] | None = None,
        min_duration_seconds: float | None = None,
        max_duration_seconds: float | None = None,
        limit: int = 20,
        viewer_user_id: str | None = None,
        local_diagnostic: bool = False,
        purpose: Literal['manual','automatic'] = 'manual',
        include_eligibility: bool = False,
    ) -> list[MaterialSegmentMatch] | dict:
        """Search one workspace's qualified, persisted segments and explain every candidate.

        硬过滤（workspace 隔离、用途资格、已持久化、调用方给的结构化条件）保持严格，
        任何放宽都只发生在排序上：文字匹配不上时依次降级到「分词包含」和
        「仅按结构化条件返回」，并且每条候选都带上可核对的依据。
        """
        roles = set(usage_roles or [])
        if purpose not in {'manual','automatic'}:
            raise ValueError('成片素材检索用途必须明确为人工或自动')
        if not roles <= {"unassigned", "a_roll", "b_roll", "exclude"}:
            raise ValueError("素材用途条件无效")
        requested_tags = [value.strip().casefold() for value in (tags or [])]
        if any(not value for value in requested_tags):
            raise ValueError("素材标签条件不能为空")
        if min_duration_seconds is not None and min_duration_seconds < 0:
            raise ValueError("最小镜头时长不能为负数")
        if max_duration_seconds is not None and max_duration_seconds < 0:
            raise ValueError("最大镜头时长不能为负数")
        if (
            min_duration_seconds is not None
            and max_duration_seconds is not None
            and min_duration_seconds > max_duration_seconds
        ):
            raise ValueError("镜头时长范围无效")
        if not 1 <= limit <= 100:
            raise ValueError("素材检索 limit 必须在 1 到 100 之间")

        normalized_query = query.strip().casefold()
        # 「仅按结构化条件返回」只在调用方真的给了结构化条件时才有意义。
        has_structured_condition = bool(roles or requested_tags) or (
            min_duration_seconds is not None or max_duration_seconds is not None
        )
        matches: list[MaterialSegmentMatch] = []
        qualification_counts = {'processing':0,'restricted':0,'input_changed':0,'source_unavailable':0}
        selected_assets = set(asset_ids) if asset_ids is not None else None
        for asset in self._list_assets(
            workspace_id,
            include_private=viewer_user_id is not None or local_diagnostic,
            viewer_user_id=viewer_user_id,
            local_diagnostic=local_diagnostic,
        ):
            if selected_assets is not None and asset.id not in selected_assets:
                continue
            index_stale = (
                self._index_status_for is not None
                and self._index_status_for(workspace_id, asset.id, "material_search") == "stale"
            )
            qualification=material_usage_eligibility(asset,purpose=purpose)
            if not qualification.eligible:
                qualification_counts[qualification.status]+=1
                continue
            for segment in asset.segments:
                if eligible_segment_refs is not None and (asset.id, segment.id) not in eligible_segment_refs:
                    continue
                qualification=material_usage_eligibility(asset,segment,purpose=purpose)
                if not qualification.eligible:
                    qualification_counts[qualification.status]+=1
                    continue
                if not self._segment_eligible(asset,segment,workspace_id,automation_only=purpose=='automatic'):
                    qualification_counts['source_unavailable']+=1
                    continue
                duration = max(0.0, segment.end_seconds - segment.start_seconds)
                if (
                    min_duration_seconds is not None
                    and duration < min_duration_seconds
                ):
                    continue
                if (
                    max_duration_seconds is not None
                    and duration > max_duration_seconds
                ):
                    continue
                role = selectable_segment_usage_role(asset, segment)
                if roles and role not in roles:
                    continue
                segment_tags = [
                    value.strip()
                    for value in (*segment.system_tags, *segment.user_tags, *asset.user_tags)
                    if value.strip()
                ]
                if requested_tags and not set(requested_tags) <= {
                    value.casefold() for value in segment_tags
                }:
                    continue

                score = 0.1
                reasons = ["当前工作区已分析素材"]
                conditions: list[str] = []
                if roles:
                    score += 0.25
                    reasons.append(f"用途匹配 {role}")
                    conditions.append(f"用途 {role}")
                if requested_tags:
                    score += 0.15 * len(requested_tags)
                    reasons.append("标签匹配 " + "、".join(tags or []))
                    conditions.append("标签 " + "、".join(tags or []))
                if min_duration_seconds is not None or max_duration_seconds is not None:
                    score += 0.1
                    lower = "不限" if min_duration_seconds is None else f"{min_duration_seconds:.1f}"
                    upper = "不限" if max_duration_seconds is None else f"{max_duration_seconds:.1f}"
                    reasons.append(f"时长 {duration:.1f} 秒在 {lower}–{upper} 秒内")
                    conditions.append(f"时长 {duration:.1f} 秒")
                if min_duration_seconds is not None and duration > min_duration_seconds:
                    surplus = min(0.15, 0.02 * (duration - min_duration_seconds))
                    score += surplus
                    reasons.append(
                        f"比要求的最短 {min_duration_seconds:.1f} 秒多"
                        f" {duration - min_duration_seconds:.1f} 秒"
                    )
                    conditions.append(f"比最短要求多 {duration - min_duration_seconds:.1f} 秒")

                tier = "structural"
                matched_terms: list[str] = []
                if normalized_query:
                    text_reasons = _material_query_reasons(
                        normalized_query, segment, asset
                    )
                    if text_reasons:
                        tier = "text"
                        score += 0.45 + 0.1 * len(text_reasons)
                        reasons.extend(text_reasons)
                        matched_terms = _material_partial_terms(
                            normalized_query, segment, asset
                        )
                    else:
                        matched_terms = _material_partial_terms(
                            normalized_query, segment, asset
                        )
                        if matched_terms:
                            tier = "partial"
                            score += 0.25 + 0.03 * min(len(matched_terms), 5)
                            reasons.append(
                                "文字未整段匹配，分词命中 "
                                + "、".join(f"「{term}」" for term in matched_terms)
                            )
                        elif has_structured_condition:
                            tier = "structural"
                            score += 0.05
                            reasons.append("文字未匹配，按结构化条件返回")
                        else:
                            # 既没有文字重合，也没有结构化条件可退：没有东西可返回。
                            continue

                matches.append(
                    MaterialSegmentMatch(
                        asset_ref=f"asset:{asset.id}",
                        segment_ref=f"segment:{segment.id}",
                        score=round(score, 4),
                        reasons=[*reasons, "素材搜索索引已过期，结果仅供核对" ] if index_stale else reasons,
                        match_tier=tier,
                        matched_terms=matched_terms,
                        matched_conditions=conditions,
                        asset_file_name=asset.file_name,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        duration_seconds=round(duration, 3),
                        time_range=(
                            f"{_material_timecode(segment.start_seconds)} – "
                            f"{_material_timecode(segment.end_seconds)}"
                        ),
                        tags=segment_tags,
                        evidence_excerpt=_material_searchable_corpus(segment, asset)[:900],
                        evidence_ref_ids=(
                            self._evidence_refs_for(workspace_id, asset.id, segment.id)
                            if self._evidence_refs_for is not None else []
                        ),
                        needs_confirmation=index_stale or tier in {"partial", "structural", "semantic"},
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.asset_ref, item.segment_ref))
        selected = matches[:limit]
        total = len(selected)
        ranked = [
            replace(
                match,
                rank=rank,
                reasons=[*match.reasons, f"综合排第 {rank} 名（本次候选 {total} 条）"],
            )
            for rank, match in enumerate(selected, start=1)
        ]
        if not include_eligibility:
            return ranked
        limitations = [f'{qualification_counts[status]}项{label}' for status,label in (
            ('processing','分析尚未就绪'),('restricted','受本次用途限制'),('input_changed','输入版本已变化'),
            ('source_unavailable','文件、媒体类型或源区间不可用')) if qualification_counts[status]]
        message = f'在当前身份可读取范围内，按{"自动" if purpose=="automatic" else "人工"}用途返回{len(ranked)}条候选。'
        if limitations:
            message += '当前可读素材还有'+ '、'.join(limitations)+'，未作为候选。'
        if not ranked:
            message += '没有匹配候选不代表当前范围外对象不存在；可调整筛选或沿原素材查看处理状态。'
        message += '本次只按已有文字与结构条件检索，未提交画面语义推理。'
        return {'matches':ranked,'qualification_message':message,'total_count':len(matches),'truncated':len(matches)>len(ranked)}



def _material_contains_text(text: str, phrase: str) -> bool:
    """中文按子串算包含，英文按词边界算——避免 "usb" 命中 "bus"。"""
    if re.search(r"[\u4e00-\u9fff]", phrase):
        return phrase in text
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


# 英文虚词不作为匹配依据：命中它们不构成「这条更好」的证据。
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
    return segment_searchable_text(asset, segment)


def _material_partial_terms(
    normalized_query: str, segment: MaterialSegment, asset: MaterialAsset
) -> list[str]:
    """字面没整段命中时，看查询里还有哪些词真的出现在这条素材上。"""
    corpus = _material_searchable_corpus(segment, asset)
    if not corpus:
        return []
    return [unit for unit in _material_query_units(normalized_query) if unit in corpus]


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
    text_fields = segment_text_evidence(asset, segment)
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
