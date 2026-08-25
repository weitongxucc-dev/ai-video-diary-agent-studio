from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.models import utc_now
from app.video import SUPPORTED_VIDEO_EXTENSIONS
from app.visual_semantics import (
    SegmentWindow,
    UnavailableVisualSemanticProvider,
    VisualSemanticError,
    VisualSemanticProvider,
)

AssetReviewStatus = Literal["raw", "approved", "rejected"]
AssetUsageRole = Literal["unassigned", "a_roll", "b_roll", "exclude"]
SuggestedUsageRole = Literal["unassigned", "a_roll", "b_roll"]
AnalysisStatus = Literal["pending", "ready", "blocked", "failed"]
BatchStatus = Literal[
    "created",
    "blocked",
    "running",
    "waiting_for_user",
    "interrupted",
    "ready",
    "reviewed",
    "failed",
]
BatchItemStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "rendered",
    "failed",
    "interrupted",
]

_A_ROLL_PROMPTS = (
    "一个人面对摄像头说话的自拍视频",
    "人物面部特写口播",
    "主播面对镜头讲话",
)
_B_ROLL_PROMPTS = (
    "没有人脸的风景环境补充镜头",
    "产品特写或软件界面操作演示",
    "城市街景、办公环境或物品的空镜头",
)
_ROLE_SUGGESTION_MARGIN = 0.02
DEFAULT_MATERIAL_TAXONOMY = (
    "场景:办公",
    "场景:户外",
    "物体:电脑",
    "动作:打字",
    "内容:软件界面",
    "主体:人物",
    "镜头:特写",
)


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
    duration_seconds: float | None = None
    usage_role: AssetUsageRole = "unassigned"
    suggested_usage_role: SuggestedUsageRole = "unassigned"
    usage_role_confidence: float = 0.0
    usage_role_reason: str = ""
    review_status: AssetReviewStatus = "raw"
    rights_confirmed: bool = False
    brand_safe: bool = False
    analysis_status: AnalysisStatus = "pending"
    analysis_message: str = "待分析镜头和画面内容。"
    segments: list[MaterialSegment] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionProfile:
    workspace_id: str
    workspace_name: str = "AI 视频日记"
    brand_name: str = ""
    primary_platform: str = "douyin"
    aspect_ratio: str = "9:16"
    target_duration_min_seconds: int = 30
    target_duration_max_seconds: int = 90
    brand_template_name: str = ""
    brand_colors: list[str] = field(default_factory=list)
    subtitle_style: str = ""
    intro_text: str = ""
    outro_text: str = ""
    background_music_path: str | None = None
    voice_name: str = ""
    voice_reference_path: str | None = None
    voice_reference_text: str = ""
    voice_consent_confirmed: bool = False
    voice_model_status: AnalysisStatus = "blocked"
    voice_model_message: str = "请先下载本地声音模型并确认声音授权。"
    source_urls: list[str] = field(default_factory=list)
    use_obsidian: bool = True
    use_hermes: bool = True
    factuality_gate_enabled: bool = True
    cloud_enhancement_enabled: bool = False
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class VoiceProfile:
    id: str
    workspace_id: str
    name: str
    reference_audio_path: str
    reference_text: str
    consent_confirmed: bool
    is_default: bool = False
    model_id: str = "qwen3-tts-12hz-0.6b-base-8bit"
    status: AnalysisStatus = "pending"
    message: str = "等待声音模型检查。"
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class NarrationArtifactRecord:
    id: str
    workspace_id: str
    episode_id: str
    script_id: str
    voice_profile_id: str | None
    cache_key: str
    audio_path: str
    duration_seconds: float
    sample_rate: int
    model_id: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class MaterialCollection:
    id: str
    workspace_id: str
    name: str
    parent_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionBatchItem:
    episode_id: str
    status: BatchItemStatus = "queued"
    plan_id: str | None = None
    narration_audio_path: str | None = None
    output_path: str | None = None
    message: str = "等待串行处理。"
    attempts: int = 0
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ProductionBatch:
    id: str
    workspace_id: str
    name: str
    target_count: int
    status: BatchStatus
    episode_ids: list[str] = field(default_factory=list)
    items: list[ProductionBatchItem] = field(default_factory=list)
    blocker_codes: list[str] = field(default_factory=list)
    message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def rendered_count(self) -> int:
        return sum(item.status == "rendered" for item in self.items)

    @property
    def waiting_review_count(self) -> int:
        return sum(item.status == "waiting_for_user" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status in {"failed", "interrupted"} for item in self.items)


class ProductionRepository:
    """Workspace-scoped persistence for the batch-production foundation.

    The production domain deliberately uses separate tables from legacy Episode
    payloads. That keeps the migration reversible while allowing the existing
    single-Episode flow to continue during the product transition.
    """

    def __init__(
        self,
        database_path: str | Path,
        video_library_path: str | Path,
        duration_reader: Callable[[Path], float] | None = None,
        scene_reader: Callable[[Path, float], list[float]] | None = None,
        visual_provider: VisualSemanticProvider | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self.video_library_path = Path(video_library_path).expanduser()
        self.duration_reader = duration_reader or self._probe_duration
        self.scene_reader = scene_reader or self._detect_scene_boundaries
        self.visual_provider = visual_provider or UnavailableVisualSemanticProvider()
        self._usage_role_prompt_vectors: list[list[float]] | None = None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_profiles (
                    workspace_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS material_assets (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, file_path)
                );
                CREATE INDEX IF NOT EXISTS idx_material_assets_workspace
                    ON material_assets(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS material_segment_features (
                    workspace_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, segment_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_material_features_asset
                    ON material_segment_features(workspace_id, asset_id);
                CREATE TABLE IF NOT EXISTS production_batches (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_production_batches_workspace
                    ON production_batches(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS voice_profiles (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    is_default INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_profiles_workspace
                    ON voice_profiles(workspace_id, is_default DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS episode_voice_selections (
                    workspace_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    voice_profile_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, episode_id)
                );
                CREATE TABLE IF NOT EXISTS narration_artifacts (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, cache_key)
                );
                CREATE INDEX IF NOT EXISTS idx_narration_artifacts_episode
                    ON narration_artifacts(workspace_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS material_collections (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_material_collections_workspace
                    ON material_collections(workspace_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS material_segment_usage (
                    workspace_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    clip_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    segment_id TEXT NOT NULL,
                    used_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, plan_id, clip_id)
                );
                CREATE INDEX IF NOT EXISTS idx_material_usage_segment
                    ON material_segment_usage(workspace_id, segment_id, used_at DESC);
                """
            )

    def get_profile(self, workspace_id: str) -> ProductionProfile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM production_profiles WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return ProductionProfile(workspace_id=workspace_id)
        return self._profile_from_payload(json.loads(str(row["payload"])))

    def save_profile(self, profile: ProductionProfile) -> ProductionProfile:
        profile.updated_at = utc_now()
        payload = _json_payload(profile)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO production_profiles (workspace_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (profile.workspace_id, payload, profile.updated_at.isoformat()),
            )
        return profile

    def list_voice_profiles(self, workspace_id: str) -> list[VoiceProfile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload, is_default FROM voice_profiles
                WHERE workspace_id = ? ORDER BY is_default DESC, updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        profiles: list[VoiceProfile] = []
        for row in rows:
            data = json.loads(str(row["payload"]))
            data["is_default"] = bool(row["is_default"])
            profiles.append(self._voice_profile_from_payload(data))
        return profiles

    def get_voice_profile(self, workspace_id: str, voice_profile_id: str) -> VoiceProfile:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, is_default FROM voice_profiles
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, voice_profile_id),
            ).fetchone()
        if row is None:
            raise KeyError(voice_profile_id)
        data = json.loads(str(row["payload"]))
        data["is_default"] = bool(row["is_default"])
        return self._voice_profile_from_payload(data)

    def save_voice_profile(self, voice: VoiceProfile) -> VoiceProfile:
        voice.updated_at = utc_now()
        with self._connect() as conn:
            if voice.is_default:
                conn.execute(
                    "UPDATE voice_profiles SET is_default = 0 WHERE workspace_id = ?",
                    (voice.workspace_id,),
                )
            conn.execute(
                """
                INSERT INTO voice_profiles (id, workspace_id, is_default, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    is_default = excluded.is_default,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    voice.id,
                    voice.workspace_id,
                    int(voice.is_default),
                    _json_payload(voice),
                    voice.updated_at.isoformat(),
                ),
            )
        return voice

    def select_episode_voice(
        self,
        workspace_id: str,
        episode_id: str,
        voice_profile_id: str | None,
    ) -> VoiceProfile | None:
        with self._connect() as conn:
            if voice_profile_id is None:
                conn.execute(
                    "DELETE FROM episode_voice_selections WHERE workspace_id = ? AND episode_id = ?",
                    (workspace_id, episode_id),
                )
                return None
            voice = self.get_voice_profile(workspace_id, voice_profile_id)
            conn.execute(
                """
                INSERT INTO episode_voice_selections
                    (workspace_id, episode_id, voice_profile_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(workspace_id, episode_id) DO UPDATE SET
                    voice_profile_id = excluded.voice_profile_id,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, episode_id, voice.id, utc_now().isoformat()),
            )
        return voice

    def selected_episode_voice_id(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT voice_profile_id FROM episode_voice_selections
                WHERE workspace_id = ? AND episode_id = ?
                """,
                (workspace_id, episode_id),
            ).fetchone()
        return str(row["voice_profile_id"]) if row is not None else None

    def resolve_voice_profile(
        self,
        workspace_id: str,
        episode_id: str,
    ) -> VoiceProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT voice_profile_id FROM episode_voice_selections
                WHERE workspace_id = ? AND episode_id = ?
                """,
                (workspace_id, episode_id),
            ).fetchone()
        if row is not None:
            return self.get_voice_profile(workspace_id, str(row["voice_profile_id"]))
        profiles = self.list_voice_profiles(workspace_id)
        return next((voice for voice in profiles if voice.is_default), None)

    def get_narration_artifact(
        self,
        workspace_id: str,
        cache_key: str,
    ) -> NarrationArtifactRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM narration_artifacts
                WHERE workspace_id = ? AND cache_key = ?
                """,
                (workspace_id, cache_key),
            ).fetchone()
        if row is None:
            return None
        record = self._narration_artifact_from_payload(json.loads(str(row["payload"])))
        if not Path(record.audio_path).is_file():
            return None
        return record

    def save_narration_artifact(
        self,
        artifact: NarrationArtifactRecord,
    ) -> NarrationArtifactRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO narration_artifacts (id, workspace_id, cache_key, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, cache_key) DO UPDATE SET
                    id = excluded.id,
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    artifact.id,
                    artifact.workspace_id,
                    artifact.cache_key,
                    _json_payload(artifact),
                    artifact.created_at.isoformat(),
                ),
            )
        return artifact

    def list_collections(self, workspace_id: str) -> list[MaterialCollection]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_collections
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [
            self._collection_from_payload(json.loads(str(row["payload"])))
            for row in rows
        ]

    def save_collection(self, collection: MaterialCollection) -> MaterialCollection:
        if collection.parent_id is not None:
            parent = next(
                (
                    item
                    for item in self.list_collections(collection.workspace_id)
                    if item.id == collection.parent_id
                ),
                None,
            )
            if parent is None:
                raise KeyError(collection.parent_id)
        collection.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_collections (id, workspace_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    collection.id,
                    collection.workspace_id,
                    _json_payload(collection),
                    collection.updated_at.isoformat(),
                ),
            )
        return collection

    def scan_library(self, workspace_id: str) -> list[MaterialAsset]:
        if not self.video_library_path.exists() or not self.video_library_path.is_dir():
            return []
        discovered: list[MaterialAsset] = []
        for path in sorted(self.video_library_path.rglob("*")):
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS
                or "_已处理" in path.parts
            ):
                continue
            discovered.append(self._upsert_discovered_asset(workspace_id, path))
        return discovered

    def list_assets(self, workspace_id: str) -> list[MaterialAsset]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM material_assets
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._asset_from_payload(json.loads(str(row["payload"]))) for row in rows]

    def get_asset(self, workspace_id: str, asset_id: str) -> MaterialAsset:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM material_assets
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._asset_from_payload(json.loads(str(row["payload"])))

    def analyze_asset(self, workspace_id: str, asset_id: str) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        path = self._validated_library_file(asset.path)
        try:
            duration = float(self.duration_reader(path))
            if duration <= 0:
                raise ValueError("视频时长无效")
            boundaries = self.scene_reader(path, duration)
            ranges = _scene_ranges(boundaries, duration)
            old_segments = {segment.id: segment for segment in asset.segments}
            segments: list[MaterialSegment] = []
            for start, end in ranges:
                segment_id = _segment_id(asset.id, start, end)
                previous = old_segments.get(segment_id)
                segments.append(
                    previous
                    or MaterialSegment(
                        id=segment_id,
                        asset_id=asset.id,
                        start_seconds=start,
                        end_seconds=end,
                    )
                )
            asset.duration_seconds = round(duration, 3)
            asset.segments = segments
            asset.analysis_status = "ready"
            visual_message = self._analyze_visual_semantics(asset, path)
            role_message = self._suggest_usage_roles(asset)
            for segment in asset.segments:
                segment.system_tags = _infer_system_tags(asset, segment)
            asset.analysis_message = (
                f"已拆分 {len(segments)} 个镜头；{visual_message} {role_message}"
            ).strip()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            asset.analysis_status = "failed"
            asset.analysis_message = f"镜头分析失败：{exc}"
        return self._save_asset(asset)

    def get_visual_feature(
        self,
        workspace_id: str,
        segment_id: str,
        model_id: str,
    ) -> list[float] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT vector_json FROM material_segment_features
                WHERE workspace_id = ? AND segment_id = ? AND model_id = ?
                """,
                (workspace_id, segment_id, model_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["vector_json"]))
        if not isinstance(payload, list):
            return None
        return [float(value) for value in payload]

    def update_asset_review(
        self,
        workspace_id: str,
        asset_id: str,
        *,
        review_status: AssetReviewStatus,
        rights_confirmed: bool,
        brand_safe: bool,
        usage_role: AssetUsageRole = "unassigned",
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        if usage_role == "exclude":
            review_status = "rejected"
        if review_status == "approved":
            if usage_role not in {"a_roll", "b_roll"}:
                raise ValueError("先选择人物主镜头或 B-roll，再加入可用库")
            if not rights_confirmed:
                raise ValueError("确认版权后才能将素材加入可用库")
            if not brand_safe:
                raise ValueError("确认品牌安全后才能将素材加入可用库")
            if asset.analysis_status != "ready" or not asset.segments:
                raise ValueError("请先完成镜头分析再审核素材")
        asset.review_status = review_status
        asset.usage_role = usage_role
        asset.rights_confirmed = rights_confirmed
        asset.brand_safe = brand_safe
        if review_status == "approved":
            for segment in asset.segments:
                if segment.usage_role == "unassigned":
                    segment.usage_role = usage_role
                if segment.review_status == "raw":
                    segment.review_status = "approved"
                    segment.updated_at = utc_now()
        elif review_status == "rejected":
            for segment in asset.segments:
                if usage_role == "exclude":
                    segment.usage_role = "exclude"
                segment.review_status = "rejected"
                segment.updated_at = utc_now()
        return self._save_asset(asset)

    def update_segment(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        description: str,
        transcript: str,
        review_status: AssetReviewStatus,
        usage_role: AssetUsageRole | None = None,
        user_tags: list[str] | None = None,
        collection_ids: list[str] | None = None,
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        if review_status == "approved" and asset.review_status != "approved":
            raise ValueError("素材文件尚未进入可用库")
        segment.description = description.strip()
        segment.transcript = transcript.strip()
        segment.system_tags = _infer_system_tags(asset, segment)
        if usage_role is not None:
            segment.usage_role = usage_role
            if usage_role == "exclude":
                review_status = "rejected"
        if user_tags is not None:
            segment.user_tags = _normalized_labels(user_tags)
        if collection_ids is not None:
            known_collections = {
                item.id for item in self.list_collections(workspace_id)
            }
            if not set(collection_ids) <= known_collections:
                raise ValueError("标签夹不存在或不属于当前工作区")
            segment.collection_ids = list(dict.fromkeys(collection_ids))
        segment.review_status = review_status
        segment.updated_at = utc_now()
        return self._save_asset(asset)

    def record_segment_usage(
        self,
        workspace_id: str,
        asset_id: str,
        segment_id: str,
        *,
        plan_id: str | None = None,
        clip_id: str | None = None,
    ) -> MaterialAsset:
        asset = self.get_asset(workspace_id, asset_id)
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        usage_plan_id = plan_id or f"manual_{uuid4().hex}"
        usage_clip_id = clip_id or f"clip_{uuid4().hex}"
        used_at = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO material_segment_usage
                    (workspace_id, plan_id, clip_id, asset_id, segment_id, used_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    usage_plan_id,
                    usage_clip_id,
                    asset_id,
                    segment_id,
                    used_at.isoformat(),
                ),
            )
        if cursor.rowcount == 0:
            return asset
        segment.usage_count += 1
        segment.last_used_at = used_at
        segment.updated_at = used_at
        return self._save_asset(asset)

    def library_insights(self, workspace_id: str) -> dict[str, object]:
        assets = self.list_assets(workspace_id)
        segments = [segment for asset in assets for segment in asset.segments]
        stats: dict[str, dict[str, object]] = {}
        for segment in segments:
            duration = max(0.0, segment.end_seconds - segment.start_seconds)
            for tag in {*segment.system_tags, *segment.user_tags}:
                item = stats.setdefault(
                    tag,
                    {"tag": tag, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0},
                )
                item["segment_count"] = int(item["segment_count"]) + 1
                item["duration_seconds"] = float(item["duration_seconds"]) + duration
                item["usage_count"] = int(item["usage_count"]) + segment.usage_count
        for tag in DEFAULT_MATERIAL_TAXONOMY:
            stats.setdefault(
                tag,
                {"tag": tag, "segment_count": 0, "duration_seconds": 0.0, "usage_count": 0},
            )
        ordered = sorted(
            stats.values(),
            key=lambda item: (-int(item["segment_count"]), str(item["tag"])),
        )
        return {
            "asset_count": len(assets),
            "segment_count": len(segments),
            "tags": [
                {**item, "duration_seconds": round(float(item["duration_seconds"]), 3)}
                for item in ordered
            ],
            "recommended_gaps": [
                str(item["tag"])
                for item in ordered
                if int(item["segment_count"]) == 0
                and str(item["tag"]) in DEFAULT_MATERIAL_TAXONOMY
            ],
        }

    def create_batch(
        self,
        workspace_id: str,
        *,
        name: str,
        target_count: int,
        profile: ProductionProfile | None = None,
    ) -> ProductionBatch:
        if not 1 <= target_count <= 50:
            raise ValueError("单个批次的成片数必须在 1 到 50 之间")
        readiness = self.readiness(workspace_id, profile=profile)
        blockers = [item["code"] for item in readiness["blockers"]]
        batch = ProductionBatch(
            id=f"batch_{uuid4().hex[:8]}",
            workspace_id=workspace_id,
            name=name.strip() or f"生产批次 {utc_now().date().isoformat()}",
            target_count=target_count,
            status="blocked" if blockers else "created",
            blocker_codes=blockers,
            message=(
                "生产基础尚未就绪，修复阻断后再启动。"
                if blockers
                else "批次已创建，等待自动选题。"
            ),
        )
        return self.save_batch(batch)

    def save_batch(self, batch: ProductionBatch) -> ProductionBatch:
        batch.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO production_batches (id, workspace_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    batch.id,
                    batch.workspace_id,
                    _json_payload(batch),
                    batch.updated_at.isoformat(),
                ),
            )
        return batch

    def list_batches(self, workspace_id: str) -> list[ProductionBatch]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM production_batches
                WHERE workspace_id = ? ORDER BY updated_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._batch_from_payload(json.loads(str(row["payload"]))) for row in rows]

    def get_batch(self, workspace_id: str, batch_id: str) -> ProductionBatch:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM production_batches
                WHERE workspace_id = ? AND id = ?
                """,
                (workspace_id, batch_id),
            ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return self._batch_from_payload(json.loads(str(row["payload"])))

    def readiness(
        self,
        workspace_id: str,
        *,
        profile: ProductionProfile | None = None,
    ) -> dict[str, object]:
        profile = profile or self.get_profile(workspace_id)
        assets = self.list_assets(workspace_id)
        approved_assets = [
            asset
            for asset in assets
            if asset.review_status == "approved"
            and asset.usage_role in {"a_roll", "b_roll"}
        ]
        approved_segments = [
            segment
            for asset in approved_assets
            for segment in asset.segments
            if segment.review_status == "approved"
            and effective_segment_usage_role(asset, segment) == "b_roll"
        ]
        blockers: list[dict[str, str]] = []
        default_voice = next(
            (
                voice
                for voice in self.list_voice_profiles(workspace_id)
                if voice.is_default
            ),
            None,
        )
        if not profile.brand_name or not profile.brand_template_name:
            blockers.append(
                {
                    "code": "brand_profile_missing",
                    "message": "尚未配置品牌名称和固定视觉模板。",
                }
            )
        voice_consent_confirmed = (
            default_voice.consent_confirmed
            if default_voice is not None
            else profile.voice_consent_confirmed
        )
        voice_reference_path = (
            default_voice.reference_audio_path
            if default_voice is not None
            else profile.voice_reference_path
        )
        voice_reference_text = (
            default_voice.reference_text
            if default_voice is not None
            else profile.voice_reference_text
        )
        if not voice_consent_confirmed:
            blockers.append(
                {
                    "code": "voice_consent_missing",
                    "message": "授权声音尚未确认授权。",
                }
            )
        elif not voice_reference_path or not voice_reference_text.strip():
            blockers.append(
                {
                    "code": "voice_reference_missing",
                    "message": "授权声音缺少 WAV 参考文件或对应文字。",
                }
            )
        if profile.voice_model_status != "ready":
            blockers.append(
                {
                    "code": "voice_model_not_ready",
                    "message": profile.voice_model_message,
                }
            )
        if not (profile.source_urls or profile.use_obsidian or profile.use_hermes):
            blockers.append(
                {
                    "code": "source_pool_empty",
                    "message": "自动选题来源池为空。",
                }
            )
        if not approved_segments:
            blockers.append(
                {
                    "code": "approved_materials_empty",
                    "message": "可用素材库中还没有已审核的 B-roll 镜头。",
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "asset_count": len(assets),
            "approved_asset_count": len(approved_assets),
            "approved_segment_count": len(approved_segments),
            "total_duration_seconds": round(
                sum(asset.duration_seconds or 0 for asset in assets), 3
            ),
        }

    def _upsert_discovered_asset(
        self,
        workspace_id: str,
        path: Path,
    ) -> MaterialAsset:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM material_assets
                WHERE workspace_id = ? AND file_path = ?
                """,
                (workspace_id, str(resolved)),
            ).fetchone()
        existing = (
            self._asset_from_payload(json.loads(str(row["payload"])))
            if row is not None
            else None
        )
        stat = resolved.stat()
        if existing is None:
            digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
            existing = MaterialAsset(
                id=f"asset_{digest}",
                workspace_id=workspace_id,
                file_name=resolved.name,
                path=str(resolved),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo),
            )
        else:
            existing.file_name = resolved.name
            existing.size_bytes = stat.st_size
            existing.modified_at = datetime.fromtimestamp(stat.st_mtime, tz=utc_now().tzinfo)
        return self._save_asset(existing)

    def _save_asset(self, asset: MaterialAsset) -> MaterialAsset:
        asset.updated_at = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO material_assets
                    (id, workspace_id, file_path, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, file_path) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    asset.id,
                    asset.workspace_id,
                    asset.path,
                    _json_payload(asset),
                    asset.updated_at.isoformat(),
                ),
            )
        return asset

    def _analyze_visual_semantics(self, asset: MaterialAsset, path: Path) -> str:
        provider = self.visual_provider
        if not provider.ready:
            for segment in asset.segments:
                if not segment.visual_model_id:
                    segment.visual_status = "blocked"
            return f"{provider.message} 可先人工补充画面描述并审核。"

        fingerprints = {
            segment.id: _visual_fingerprint(path, segment, provider.model_id)
            for segment in asset.segments
        }
        cached_ids = self._cached_visual_segment_ids(
            asset.workspace_id,
            provider.model_id,
            fingerprints,
        )
        pending = [segment for segment in asset.segments if segment.id not in cached_ids]
        for segment in asset.segments:
            if segment.id in cached_ids:
                segment.visual_status = "ready"
                segment.visual_model_id = provider.model_id
        if not pending:
            return f"已复用 {len(cached_ids)} 个镜头的画面语义缓存，等待审核。"

        try:
            embeddings = provider.embed_segments(
                path,
                [
                    SegmentWindow(
                        segment_id=segment.id,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                    )
                    for segment in pending
                ],
            )
            embedding_by_id = {item.segment_id: item for item in embeddings}
            with self._connect() as conn:
                for segment in pending:
                    embedding = embedding_by_id[segment.id]
                    conn.execute(
                        """
                        INSERT INTO material_segment_features
                            (workspace_id, asset_id, segment_id, model_id, fingerprint,
                             vector_json, sample_count, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(workspace_id, segment_id, model_id) DO UPDATE SET
                            asset_id = excluded.asset_id,
                            fingerprint = excluded.fingerprint,
                            vector_json = excluded.vector_json,
                            sample_count = excluded.sample_count,
                            updated_at = excluded.updated_at
                        """,
                        (
                            asset.workspace_id,
                            asset.id,
                            segment.id,
                            provider.model_id,
                            fingerprints[segment.id],
                            json.dumps(embedding.vector),
                            embedding.sample_count,
                            utc_now().isoformat(),
                        ),
                    )
                    segment.visual_status = "ready"
                    segment.visual_model_id = provider.model_id
                    segment.visual_sample_count = embedding.sample_count
        except (KeyError, OSError, ValueError, VisualSemanticError) as exc:
            for segment in pending:
                segment.visual_status = "failed"
                segment.visual_model_id = provider.model_id
                segment.visual_sample_count = 0
            return f"画面语义分析失败：{exc}；可重试或人工补充描述。"
        return (
            f"已生成 {len(pending)} 个镜头的画面语义向量"
            f"并复用 {len(cached_ids)} 个缓存，等待审核。"
        )

    def _suggest_usage_roles(self, asset: MaterialAsset) -> str:
        asset.suggested_usage_role = "unassigned"
        asset.usage_role_confidence = 0.0
        asset.usage_role_reason = ""
        for segment in asset.segments:
            segment.suggested_usage_role = "unassigned"
            segment.usage_role_confidence = 0.0
            segment.usage_role_reason = ""

        provider = self.visual_provider
        if not provider.ready:
            return "画面模型未就绪，未生成素材用途建议。"
        try:
            if self._usage_role_prompt_vectors is None:
                self._usage_role_prompt_vectors = provider.embed_texts(
                    [*_A_ROLL_PROMPTS, *_B_ROLL_PROMPTS]
                )
            prompt_vectors = self._usage_role_prompt_vectors
            if len(prompt_vectors) != len(_A_ROLL_PROMPTS) + len(_B_ROLL_PROMPTS):
                raise ValueError("素材用途提示向量数量不正确")

            scored_segments: list[MaterialSegment] = []
            for segment in asset.segments:
                vector = self.get_visual_feature(
                    asset.workspace_id,
                    segment.id,
                    provider.model_id,
                )
                if not vector:
                    continue
                scores = [
                    _cosine_similarity(vector, prompt_vector)
                    for prompt_vector in prompt_vectors
                ]
                a_roll_score = max(scores[: len(_A_ROLL_PROMPTS)])
                b_roll_score = max(scores[len(_A_ROLL_PROMPTS) :])
                margin = abs(a_roll_score - b_roll_score)
                if margin < _ROLE_SUGGESTION_MARGIN:
                    segment.usage_role_reason = (
                        "人物口播与补充画面得分接近，保留人工判断"
                        f"（A {a_roll_score:.2f} / B {b_roll_score:.2f}）"
                    )
                else:
                    segment.suggested_usage_role = (
                        "a_roll" if a_roll_score > b_roll_score else "b_roll"
                    )
                    segment.usage_role_confidence = round(
                        min(0.99, 0.5 + margin * 5),
                        3,
                    )
                    label = (
                        "人物面对镜头口播"
                        if segment.suggested_usage_role == "a_roll"
                        else "补充画面或环境空镜"
                    )
                    segment.usage_role_reason = (
                        f"画面更接近{label}"
                        f"（A {a_roll_score:.2f} / B {b_roll_score:.2f}）"
                    )
                scored_segments.append(segment)
        except (OSError, ValueError, VisualSemanticError) as exc:
            return f"素材用途建议失败：{exc}；请人工判断。"

        if not scored_segments:
            return "没有可用于判断素材用途的画面向量。"
        coverage = len(scored_segments) / max(1, len(asset.segments))
        if coverage < 0.75:
            asset.usage_role_reason = (
                f"仅完成 {len(scored_segments)}/{len(asset.segments)} 个镜头的用途判断"
            )
            return "画面分析覆盖不足，请重试后再确认素材用途。"
        counts = {
            role: sum(segment.suggested_usage_role == role for segment in scored_segments)
            for role in ("a_roll", "b_roll")
        }
        dominant_role = max(counts, key=counts.get)
        dominant_segments = [
            segment
            for segment in scored_segments
            if segment.suggested_usage_role == dominant_role
        ]
        dominant_ratio = len(dominant_segments) / len(scored_segments)
        if dominant_ratio >= 0.75 and dominant_segments:
            asset.suggested_usage_role = dominant_role
            asset.usage_role_confidence = round(
                dominant_ratio
                * sum(segment.usage_role_confidence for segment in dominant_segments)
                / len(dominant_segments),
                3,
            )
            label = "人物主镜头" if dominant_role == "a_roll" else "B-roll"
            asset.usage_role_reason = (
                f"{len(dominant_segments)}/{len(scored_segments)} 个镜头建议为{label}"
            )
            return (
                f"用途建议：{label}"
                f"（置信度 {round(asset.usage_role_confidence * 100)}%）。"
            )

        asset.usage_role_reason = (
            f"镜头用途混合或不确定：人物主镜头 {counts['a_roll']} 个，"
            f"B-roll {counts['b_roll']} 个"
        )
        return "素材包含混合或不确定镜头，请逐段确认用途。"

    def _cached_visual_segment_ids(
        self,
        workspace_id: str,
        model_id: str,
        fingerprints: dict[str, str],
    ) -> set[str]:
        if not fingerprints:
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT segment_id, fingerprint FROM material_segment_features
                WHERE workspace_id = ? AND model_id = ?
                """,
                (workspace_id, model_id),
            ).fetchall()
        return {
            str(row["segment_id"])
            for row in rows
            if fingerprints.get(str(row["segment_id"])) == str(row["fingerprint"])
        }

    def _validated_library_file(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser().resolve()
        try:
            path.relative_to(self.video_library_path.resolve())
        except ValueError as exc:
            raise ValueError("素材文件不在配置的素材库中") from exc
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError("素材文件不存在或格式不支持")
        return path

    def _probe_duration(self, path: Path) -> float:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(completed.stdout.strip())

    def _detect_scene_boundaries(self, path: Path, duration: float) -> list[float]:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-vf",
                "select='gt(scene,0.35)',showinfo",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise subprocess.CalledProcessError(
                completed.returncode,
                completed.args,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        boundaries = [
            float(match.group(1))
            for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", completed.stderr)
        ]
        return [value for value in boundaries if 0 < value < duration]

    @staticmethod
    def _profile_from_payload(data: dict[str, object]) -> ProductionProfile:
        data = _with_datetime(data, "updated_at")
        return ProductionProfile(**data)

    @staticmethod
    def _voice_profile_from_payload(data: dict[str, object]) -> VoiceProfile:
        data = _with_datetime(data, "created_at", "updated_at")
        return VoiceProfile(**data)

    @staticmethod
    def _narration_artifact_from_payload(
        data: dict[str, object],
    ) -> NarrationArtifactRecord:
        data = _with_datetime(data, "created_at")
        return NarrationArtifactRecord(**data)

    @staticmethod
    def _collection_from_payload(data: dict[str, object]) -> MaterialCollection:
        data = _with_datetime(data, "created_at", "updated_at")
        return MaterialCollection(**data)

    @staticmethod
    def _asset_from_payload(data: dict[str, object]) -> MaterialAsset:
        segment_payloads = data.pop("segments", [])
        data = _with_datetime(data, "modified_at", "created_at", "updated_at")
        segments = [
            MaterialSegment(
                **_with_datetime(segment, "last_used_at", "created_at", "updated_at")
            )
            for segment in segment_payloads
            if isinstance(segment, dict)
        ]
        asset = MaterialAsset(**data, segments=segments)
        for segment in asset.segments:
            if not segment.system_tags:
                segment.system_tags = _infer_system_tags(asset, segment)
        return asset

    @staticmethod
    def _batch_from_payload(data: dict[str, object]) -> ProductionBatch:
        item_payloads = data.pop("items", [])
        data = _with_datetime(
            data,
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )
        items = [
            ProductionBatchItem(**_with_datetime(item, "updated_at"))
            for item in item_payloads
            if isinstance(item, dict)
        ]
        return ProductionBatch(**data, items=items)


def _json_payload(value: object) -> str:
    return json.dumps(asdict(value), ensure_ascii=False, default=_json_default)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("画面与提示向量维度不一致")
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        raise ValueError("素材用途向量无效")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def effective_segment_usage_role(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> AssetUsageRole:
    return segment.usage_role if segment.usage_role != "unassigned" else asset.usage_role


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _with_datetime(data: dict[str, object], *keys: str) -> dict[str, object]:
    converted = dict(data)
    for key in keys:
        value = converted.get(key)
        if isinstance(value, str):
            converted[key] = datetime.fromisoformat(value)
    return converted


def _normalized_labels(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            value.strip()[:80]
            for value in values
            if value.strip()
        )
    )


def _infer_system_tags(
    asset: MaterialAsset,
    segment: MaterialSegment,
) -> list[str]:
    text = f"{asset.file_name} {segment.description} {segment.transcript}".lower()
    role = effective_segment_usage_role(asset, segment)
    if role == "unassigned":
        role = segment.suggested_usage_role
    tags = [f"用途:{role}"] if role != "unassigned" else []
    rules = {
        "场景:办公": ("办公", "office", "workspace", "desk"),
        "物体:电脑": ("电脑", "笔记本", "laptop", "computer", "keyboard"),
        "动作:打字": ("打字", "键盘", "typing", "keyboard"),
        "内容:软件界面": ("软件", "界面", "screen", "app", "website"),
        "场景:户外": ("户外", "街道", "风景", "outdoor", "street", "nature"),
        "主体:人物": ("人物", "人脸", "口播", "person", "face", "talking"),
        "镜头:特写": ("特写", "close-up", "closeup"),
    }
    for tag, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return _normalized_labels(tags)


def _segment_id(asset_id: str, start: float, end: float) -> str:
    raw = f"{asset_id}:{start:.3f}:{end:.3f}"
    return f"seg_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _visual_fingerprint(
    path: Path,
    segment: MaterialSegment,
    model_id: str,
) -> str:
    stat = path.stat()
    raw = (
        f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:"
        f"{segment.start_seconds:.3f}:{segment.end_seconds:.3f}:{model_id}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scene_ranges(boundaries: list[float], duration: float) -> list[tuple[float, float]]:
    clean = sorted({round(value, 3) for value in boundaries if 0 < value < duration})
    points = [0.0, *clean, round(duration, 3)]
    ranges: list[tuple[float, float]] = []
    for start, end in pairwise(points):
        cursor = start
        while end - cursor > 12:
            ranges.append((round(cursor, 3), round(cursor + 12, 3)))
            cursor += 12
        if end - cursor >= 0.75:
            ranges.append((round(cursor, 3), round(end, 3)))
        elif ranges:
            previous_start, _ = ranges[-1]
            ranges[-1] = (previous_start, round(end, 3))
    return ranges or [(0.0, round(duration, 3))]
