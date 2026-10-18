"""Concrete material analysis with explicit execution ports and staged persistence."""
from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
from typing import TYPE_CHECKING

from app.materials.assets import MaterialAssetStore, _infer_system_tags, _scene_ranges, _segment_id
from app.materials.contracts import MaterialAsset, MaterialSegment, MaterialVisualFeature
from app.materials.source_files import MaterialSourceFileAccess
from app.models import utc_now
from app.visual_semantics import SegmentWindow, VisualSemanticError, VisualSemanticProvider, evidence_timestamps, visual_feature_version

if TYPE_CHECKING:
    from app.material_speech import LocalMaterialSpeechAnalyzer
    from app.visual_understanding import LocalVisualUnderstanding

# Keep the existing analysis error log namespace while moving its business owner.
logger = logging.getLogger("app.production")

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


def _material_analysis_public_error(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "素材文件没有读取权限"
    if isinstance(error, FileNotFoundError):
        return "素材文件已移动或外接盘暂不可用"
    if isinstance(error, OSError):
        return "素材文件无法读取"
    if isinstance(error, subprocess.SubprocessError):
        return "媒体探测或解码失败，请检查文件格式"
    if isinstance(error, VisualSemanticError):
        return "画面分析模型不可用或执行失败"
    if isinstance(error, ValueError):
        return "媒体格式或内容无效"
    return "分析服务未能完成，请查看本机日志"


def visual_cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("画面与提示向量维度不一致")
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        raise ValueError("素材用途向量无效")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _visual_fingerprint(
    path: Path,
    segment: MaterialSegment,
    model_id: str,
) -> str:
    stat = path.stat()
    raw = (
        f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_ino}:"
        f"{segment.start_seconds:.3f}:{segment.end_seconds:.3f}:{model_id}:"
        f"{visual_feature_version(model_id)}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MaterialAnalysisUseCase:
    def __init__(
        self, assets: MaterialAssetStore, source_files: MaterialSourceFileAccess, *,
        connection_factory: Callable[[], sqlite3.Connection],
        duration_reader: Callable[[Path], float], scene_reader: Callable[[Path, float], list[float]],
        visual_provider: VisualSemanticProvider,
        speech_analyzer: LocalMaterialSpeechAnalyzer | None,
        visual_understanding: LocalVisualUnderstanding | None,
    ) -> None:
        self.assets = assets
        self.source_files = source_files
        self._connect = connection_factory
        self.duration_reader = duration_reader
        self.scene_reader = scene_reader
        self.visual_provider = visual_provider
        self.speech_analyzer = speech_analyzer
        self.visual_understanding = visual_understanding
        self._usage_role_prompt_vectors: list[list[float]] | None = None

    def analyze_asset(self, workspace_id: str, asset_id: str, *, expected_source_digest: str | None = None) -> MaterialAsset:
        asset = self.assets.get_asset(workspace_id, asset_id)
        if asset.use_state == "excluded":
            raise ValueError("素材已设为不使用，请先修改使用状态再分析")
        path = self.source_files.validated_library_file(asset.path)
        self.source_files.require_material_copy_confirmation(workspace_id, path)
        source_stat = path.stat()
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
            source_fingerprint = None
            if self.visual_understanding is not None:
                with path.open("rb") as source:
                    source_fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
            if self.speech_analyzer is not None:
                asset.speech_analysis = self.speech_analyzer.analyze(path, duration, asset.speech_analysis, source_fingerprint)
            if self.visual_understanding is not None:
                observations = self.visual_understanding.analyze(path, segments, source_fingerprint)
                for segment in segments:
                    segment.understanding = observations[segment.id]
            visual_message = self._analyze_visual_semantics(asset, path)
            role_message = self._suggest_usage_roles(asset)
            for segment in asset.segments:
                segment.system_tags = _infer_system_tags(asset, segment)
            asset.analysis_message = (
                f"已拆分 {len(segments)} 个镜头；{visual_message} {role_message} "
                f"{asset.speech_analysis.message if asset.speech_analysis else '未分析原片声音。'} "
                f"画面观察 {sum(bool(item.understanding and item.understanding.status == 'ready') for item in segments)}/{len(segments)} 个片段。"
            ).strip()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.exception("Material analysis failed for asset %s", asset_id)
            asset.analysis_status = "failed"
            asset.analysis_message = f"镜头分析失败：{_material_analysis_public_error(exc)}。检查后可手动重试。"
        final_stat = path.stat()
        if self.source_files.material_copy_revision(source_stat) != self.source_files.material_copy_revision(final_stat):
            raise ValueError("分析期间原片发生变化，未保存本次结果，请重新分析。")
        if expected_source_digest is not None:
            with path.open("rb") as source:
                final_digest = hashlib.file_digest(source, "sha256").hexdigest()
            if final_digest != expected_source_digest:
                raise ValueError("分析期间原片内容发生变化，未保存本次结果，请重新分析。")
        return self.assets.save_asset(asset, analysis_only=True)

    def get_visual_feature(
        self,
        workspace_id: str,
        segment_id: str,
        model_id: str,
    ) -> list[float] | None:
        with self._connect() as conn:
            row = self.assets._visual_feature_asset_payload_row(
                conn, workspace_id, segment_id, model_id,
            )
        if row is None:
            return None
        asset = self.assets._asset_from_payload(json.loads(str(row["payload"])))
        segment = next((item for item in asset.segments if item.id == segment_id), None)
        if segment is None:
            return None
        return self._visual_feature_for_segment(asset, segment, model_id)

    def read_visual_features(
        self, workspace_id: str, candidates: Sequence[tuple[MaterialAsset, MaterialSegment]], model_id: str,
    ) -> dict[tuple[str, str], MaterialVisualFeature]:
        """Read only caller-qualified persisted segments, never enumerate private vectors."""
        features = {}
        with self._connect() as conn:
            for asset, segment in candidates:
                if asset.workspace_id != workspace_id or segment.asset_id != asset.id:
                    raise ValueError("视觉候选归属不一致")
                try:
                    path = self.source_files.validated_library_file(asset.path)
                    fingerprint = _visual_fingerprint(path, segment, model_id)
                except (OSError, ValueError):
                    continue
                feature = self.assets.read_visual_segment_feature(conn, workspace_id,
                    asset.id, segment.id, model_id, fingerprint)
                if feature is not None:
                    if feature.sample_count != len(evidence_timestamps(segment.start_seconds, segment.end_seconds)):
                        raise ValueError("片段特征抽帧数量与原片时间范围不一致")
                    features[(asset.id, segment.id)] = feature
        return features

    def _visual_feature_for_segment(
        self, asset: MaterialAsset, segment: MaterialSegment, model_id: str
    ) -> list[float] | None:
        # Analysis also calls this before newly detected segments are persisted.
        try:
            path = self.source_files.validated_library_file(asset.path)
            fingerprint = _visual_fingerprint(path, segment, model_id)
        except (OSError, ValueError):
            return None
        with self._connect() as conn:
            row = self.assets._visual_feature_vector_row(
                conn, asset.workspace_id, asset.id, segment.id, model_id, fingerprint,
            )
        if row is None:
            return None
        payload = json.loads(str(row["vector_json"]))
        if not isinstance(payload, list):
            return None
        return [float(value) for value in payload]

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
                    self.assets.write_visual_segment_feature(
                        conn,
                        asset.workspace_id,
                        asset.id,
                        segment.id,
                        provider.model_id,
                        fingerprints[segment.id],
                        json.dumps(embedding.vector),
                        embedding.sample_count,
                        utc_now().isoformat(),
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
                vector = self._visual_feature_for_segment(asset, segment, provider.model_id)
                if not vector:
                    continue
                scores = [
                    visual_cosine_similarity(vector, prompt_vector)
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
            rows = self.assets._visual_feature_cache_rows(
                conn, workspace_id, model_id,
            )
        return {
            str(row["segment_id"])
            for row in rows
            if fingerprints.get(str(row["segment_id"])) == str(row["fingerprint"])
        }
