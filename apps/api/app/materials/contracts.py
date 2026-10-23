"""Material value contracts and pure media-type, use-state and role rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.database_migrations import CONTENT_VISIBILITY_DEFAULT
from app.models import utc_now
from app.source_types import ContentUseState
if TYPE_CHECKING:
    from app.visual_understanding import SegmentUnderstanding
    from app.material_speech import MaterialSpeechAnalysis


AssetReviewStatus = Literal["raw", "approved", "rejected"]


ContentVisibility = Literal["private", "shared"]


AssetUsageRole = Literal["unassigned", "a_roll", "b_roll", "exclude"]


SuggestedUsageRole = Literal["unassigned", "a_roll", "b_roll"]


AnalysisStatus = Literal["pending", "ready", "blocked", "failed"]


MaterialMediaType = Literal["video", "audio", "image"]


MATERIAL_MEDIA_LABEL = {"video": "视频", "audio": "音频", "image": "图片"}


MATERIAL_MEDIA_BY_SUFFIX: dict[str, MaterialMediaType] = {
    ".mp4": "video", ".mov": "video", ".m4v": "video", ".webm": "video",
    ".mp3": "audio", ".m4a": "audio", ".aac": "audio", ".wav": "audio",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".gif": "image",
}


BUSINESS_OBJECT_KINDS = ("product", "dish", "person")


@dataclass
class MaterialSegment:
    id: str
    asset_id: str
    start_seconds: float
    end_seconds: float
    description: str = ""
    transcript: str = ""
    visual_status: AnalysisStatus = "pending"
    visual_model_id: str = ""
    visual_sample_count: int = 0
    understanding: SegmentUnderstanding | None = None
    usage_role: AssetUsageRole = "unassigned"
    suggested_usage_role: SuggestedUsageRole = "unassigned"
    usage_role_confidence: float = 0.0
    usage_role_reason: str = ""
    review_status: AssetReviewStatus = "raw"
    system_tags: list[str] = field(default_factory=list)
    user_tags: list[str] = field(default_factory=list)
    collection_ids: list[str] = field(default_factory=list)
    usage_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class MaterialAsset:
    id: str
    workspace_id: str
    file_name: str
    path: str
    size_bytes: int
    modified_at: datetime
    # Assets stored before direct-link import carry no media type and no provenance:
    # they default to the only type the scan whitelist ever admitted.
    media_type: MaterialMediaType = "video"
    source_url: str = ""
    duration_seconds: float | None = None
    usage_role: AssetUsageRole = "unassigned"
    suggested_usage_role: SuggestedUsageRole = "unassigned"
    usage_role_confidence: float = 0.0
    usage_role_reason: str = ""
    review_status: AssetReviewStatus = "raw"
    use_state: ContentUseState = "available"
    user_tags: list[str] = field(default_factory=list)
    # What the user typed about this one material, and the batch background it
    # arrived with. Both are the user's own words, so they ride the JSON payload
    # untouched: a later scan or re-analysis must not rewrite them, and a note
    # saved for one material must never become a fact about its neighbours.
    user_description: str = ""
    upload_context: str = ""
    analysis_status: AnalysisStatus = "pending"
    analysis_message: str = "待分析镜头和画面内容。"
    speech_analysis: MaterialSpeechAnalysis | None = None
    segments: list[MaterialSegment] = field(default_factory=list)
    # 新建素材默认私有：只有本人显式共享才对成员可见。存量行迁移时回填 shared。
    visibility: ContentVisibility = CONTENT_VISIBILITY_DEFAULT
    # 上传者：来源是已验证身份（Bearer user_id 或本地成员 member_id），不采信客户端自报。
    owner_user_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialAssetNote:
    """One material's own text and the batch background it was uploaded with.

    An empty string is a real value: it means the user left that box blank, and
    saving it must clear an earlier note rather than be rejected or ignored.
    """

    asset_id: str
    user_description: str = ""
    upload_context: str = ""


@dataclass(frozen=True)
class MaterialBusinessObject:
    """一条工作区自有的名称条目，按类型标注素材。

    名称只是用户自己维护的条目：它记录用户怎么称呼这个东西，不是系统识别出
    的画面事实。它与出镜形象／声音档案（PersonProfile）没有关系，关联到素材
    也不会创建形象或声音档案。
    """

    id: str
    workspace_id: str
    kind: str
    name: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class MaterialCollection:
    id: str
    workspace_id: str
    name: str
    parent_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class MaterialSegmentMatch:
    """一条候选素材及其可核对的依据。

    前四个字段是既有响应形状（schemas.MaterialSegmentMatchResponse）；
    其余字段是证据，必须能让人不打开代码就判断这条对不对。
    """

    asset_ref: str
    segment_ref: str
    score: float
    reasons: list[str]
    rank: int = 0
    match_tier: str = "structural"
    matched_terms: list[str] = field(default_factory=list)
    matched_conditions: list[str] = field(default_factory=list)
    asset_file_name: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0
    time_range: str = ""
    tags: list[str] = field(default_factory=list)
    visual_model_id: str | None = None
    visual_model_version: str | None = None
    visual_similarity: float | None = None
    visual_sample_count: int = 0
    visual_sample_timestamps: list[float] = field(default_factory=list)
    evidence_excerpt: str = ""
    needs_confirmation: bool = False


@dataclass(frozen=True)
class MaterialVisualFeature:
    vector: list[float]
    sample_count: int
    fingerprint: str


@dataclass(frozen=True)
class MaterialSearchResult:
    matches: list[MaterialSegmentMatch]
    semantic_status: str
    semantic_message: str
    device_request_id: str | None = None


def material_media_type_for(path: Path) -> MaterialMediaType | None:
    return MATERIAL_MEDIA_BY_SUFFIX.get(path.suffix.lower())


def material_media_type_label(media_type: MaterialMediaType) -> str:
    return MATERIAL_MEDIA_LABEL[media_type]


def material_allows_footage(asset: MaterialAsset) -> bool:
    return asset.use_state in {"available", "not_for_automation", "not_for_publish"}


def material_allows_automation(asset: MaterialAsset) -> bool:
    """Can a machine pick this material on its own?

    Stricter than `material_allows_footage` in exactly one way: a material the
    user marked "不要自动选用" stays usable by hand — searchable, insertable,
    replaceable — but must never reach a candidate pool the machine itself draws
    from, including scheduled runs. Everything the manual rule allows stays
    allowed, so restoring `available` needs no second edit anywhere else.
    """
    return asset.use_state in {"available", "not_for_publish"}


MaterialUsagePurpose = Literal["reference", "manual", "automatic", "publish"]


@dataclass(frozen=True)
class MaterialUsageEligibility:
    status: Literal["eligible", "not_found", "unreadable", "processing", "restricted", "input_changed"]
    reason: str
    repair_action: str | None = None

    @property
    def eligible(self) -> bool:
        return self.status == "eligible"


def material_usage_eligibility(asset: MaterialAsset | None, segment: MaterialSegment | None = None, *,
    purpose: MaterialUsagePurpose, readable: bool = True, expected_asset_version: datetime | None = None,
    expected_segment_version: datetime | None = None) -> MaterialUsageEligibility:
    """Qualify authorized object versions for an explicit use, apart from ranking.

    Callers resolve identity and actual file facts. No review flag is changed:
    raw means unreviewed, while explicit rejection and use restrictions still apply.
    """
    if purpose not in {"reference", "manual", "automatic", "publish"}:
        raise ValueError("素材用途必须明确为参考、人工、自动或发布")
    if not readable:
        return MaterialUsageEligibility("unreadable", "当前身份不可读取此素材", "使用当前身份可读取的素材")
    if asset is None or segment is not None and segment.asset_id != asset.id:
        return MaterialUsageEligibility("not_found", "未找到所引用的素材或片段", "沿原素材引用重新查询")
    if (expected_asset_version is not None and asset.updated_at != expected_asset_version or
            expected_segment_version is not None and (segment is None or segment.updated_at != expected_segment_version)):
        return MaterialUsageEligibility("input_changed", "素材或片段已不再是读取时的版本", "重新读取原对象并核对用途，不能静默改绑新版")
    if asset.review_status == "rejected" or segment is not None and segment.review_status == "rejected":
        return MaterialUsageEligibility("restricted", "素材或片段已被明确拒绝使用", "由用户核对并修改原对象的使用限制")
    if asset.usage_role == "exclude" or segment is not None and effective_segment_usage_role(asset, segment) == "exclude":
        return MaterialUsageEligibility("restricted", "素材或片段已被明确排除", "由用户核对原对象的用途")
    restricted = (asset.use_state == "excluded" or asset.use_state == "reference_only" and purpose != "reference"
        or asset.use_state == "not_for_automation" and purpose == "automatic"
        or asset.use_state == "not_for_publish" and purpose == "publish")
    if restricted:
        return MaterialUsageEligibility("restricted", "当前用途不符合素材的使用限制", "选择符合用途的素材，或由用户调整原使用限制")
    if purpose != "reference" and asset.analysis_status != "ready":
        return MaterialUsageEligibility("processing", "素材分析尚未就绪", "查询原分析状态；按具体失败原因恢复后再选用")
    return MaterialUsageEligibility("eligible", "当前对象版本可用于所请求的用途")


def effective_segment_usage_role(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> AssetUsageRole:
    return segment.usage_role if segment.usage_role != "unassigned" else asset.usage_role


def selectable_segment_usage_role(asset: MaterialAsset, segment: MaterialSegment) -> AssetUsageRole:
    explicit = effective_segment_usage_role(asset, segment)
    if explicit != "unassigned":
        return explicit
    if segment.usage_role_confidence >= 0.7:
        return segment.suggested_usage_role
    return "unassigned"


class MediaAuthorityError(ValueError):
    """A fail-closed media identity or authorization error for API callers."""

    def __init__(
        self,
        *,
        reason: str,
        impact: str,
        repair_action: str,
    ) -> None:
        self.reason = reason
        self.impact = impact
        self.repair_action = repair_action
        super().__init__(repair_action)
