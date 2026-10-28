"""agent 接缝端到端回归（常驻）。

把上一包留在 /tmp/seam-sim 的 `test_evidence_fields_survive_the_real_seam`
搬进仓库并扩成三层：真实 ProductionRepository → asdict → MaterialSegmentMatchResponse。

这条回归走的是**真实**的 `search_material_segments`（不是桩），所以它能守住
「Pkg2 产出的证据字段真的穿过接缝到了 schema」这条链——桩测不到这一段。
全部使用合成数据与临时目录，不碰任何真实用户库。
"""
from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

from app.production import MaterialAsset, ProductionRepository
from app.schemas import MaterialSegmentMatchResponse

# Pkg2 在 MaterialSegmentMatch 上新增的证据字段（原有四字段之外的全部）。
EVIDENCE_FIELDS = [
    "rank",
    "match_tier",
    "matched_terms",
    "matched_conditions",
    "asset_file_name",
    "start_seconds",
    "end_seconds",
    "duration_seconds",
    "time_range",
    "tags",
]


def _repository(tmp_path: Path) -> ProductionRepository:
    library = tmp_path / "library"
    library.mkdir()
    return ProductionRepository(
        database_path=tmp_path / "studio.sqlite3",
        video_library_path=library,
        duration_reader=lambda _path: 25.0,
        scene_reader=lambda _path, _duration: [5.0, 17.0],
    )


def _approved_asset(
    production: ProductionRepository,
    workspace_id: str,
    file_name: str,
    *,
    segments: list[dict[str, object]],
    asset_tags: list[str] | None = None,
) -> MaterialAsset:
    (production.video_library_path / file_name).write_bytes(b"fixture")
    production.bind_material_ingestion(workspace_id, expected_directory=str(production.video_library_path))
    production.confirm_material_copy_complete(
        workspace_id, relative_path=file_name,
        expected_revision=production.material_copy_revision((production.video_library_path / file_name).stat()),
        confirmation_source="manual",
    )
    production.scan_library(workspace_id)
    asset = next(item for item in production.list_assets(workspace_id) if item.file_name == file_name)
    asset = production.analyze_asset(workspace_id, asset.id)
    asset = production.update_asset_review(workspace_id, asset.id, review_status="approved", usage_role="b_roll")
    for patch in segments:
        patch = dict(patch)
        start = patch.pop("_start", None)
        segment = next(item for item in asset.segments if item.start_seconds == start)
        asset = production.update_segment(
            workspace_id, asset.id, segment.id,
            review_status="approved", usage_role="b_roll", **patch,  # type: ignore[arg-type]
        )
    for tag in asset_tags or []:
        asset = production.update_asset_tags(workspace_id, asset.id, [tag])
    asset = production.get_asset(workspace_id, asset.id)
    # The seam contract is exercised with an explicitly shared fixture, not an
    # anonymous read of a private upload.
    return production.set_asset_visibility(workspace_id, asset.id, "shared")


def _workspace(tmp_path: Path) -> tuple[ProductionRepository, MaterialAsset]:
    """一条三段素材，各段有可检索的中文描述与标签。"""
    production = _repository(tmp_path)
    asset = _approved_asset(
        production,
        "workspace_a",
        "office.mp4",
        segments=[
            {"_start": 0.0, "description": "电脑打字演示", "transcript": "演示如何编辑文档", "user_tags": ["动作:打字"]},
            {"_start": 5.0, "description": "窗边的城市夜景，灯光渐次亮起", "transcript": "", "user_tags": ["场景:夜景"]},
            {"_start": 17.0, "description": "街道上很吵的市声，车流不断", "transcript": "", "user_tags": ["场景:街道"]},
        ],
        asset_tags=["项目:教程"],
    )
    return production, asset


# --------------------------------------------------------------------------
# 一、真实 search_material_segments 产出的证据字段
# --------------------------------------------------------------------------


def test_real_search_produces_all_evidence_fields(tmp_path):
    """前提校验：真实检索结果 asdict 后，10 个证据字段确实都存在。"""
    production, _asset = _workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a", query="城市夜景", usage_roles=["b_roll"],
    )
    assert results, "真实检索必须返回候选"
    for match in results:
        dumped = asdict(match)
        for name in EVIDENCE_FIELDS:
            assert name in dumped, f"真实检索结果缺证据字段 {name}：{sorted(dumped)}"


def test_evidence_fields_survive_the_real_seam(tmp_path):
    """接缝本体：production → asdict → MaterialSegmentMatchResponse，10 个字段一个不丢、值不被改。"""
    production, _asset = _workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a", query="城市夜景", usage_roles=["b_roll"],
    )
    assert results, "真实检索必须返回候选"
    for match in results:
        dumped = asdict(match)
        validated = MaterialSegmentMatchResponse.model_validate(dumped)
        for name in EVIDENCE_FIELDS:
            assert hasattr(validated, name), f"过 schema 后丢了证据字段 {name}"
            # 值不得被改写
            assert getattr(validated, name) == dumped[name], (
                f"证据字段 {name} 过 schema 后被改：{getattr(validated, name)!r} != {dumped[name]!r}"
            )


def test_evidence_has_real_values_on_a_text_hit(tmp_path):
    """文字命中档的证据不是空壳：rank / tier / time_range / 命中词都要有内容。"""
    production, _asset = _workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a", query="城市夜景", usage_roles=["b_roll"],
    )
    assert results, "文字查询应命中"
    hit = results[0]
    dumped = asdict(hit)
    assert dumped["rank"] == 1, f"rank 应为 1：{dumped}"
    assert dumped["match_tier"] in {"text", "partial"}, f"文字查询应落在 text/partial 档：{dumped}"
    assert dumped["time_range"], f"time_range 不该为空：{dumped}"
    assert dumped["start_seconds"] != dumped["end_seconds"], f"起止时间应有跨度：{dumped}"
    assert dumped["asset_file_name"] == "office.mp4", f"asset_file_name 应为素材文件名：{dumped}"
    assert dumped["tags"], f"tags 不该为空：{dumped}"


# --------------------------------------------------------------------------
# 二、降级路径：证据变少也不许 422
# --------------------------------------------------------------------------


def test_structural_only_query_does_not_422(tmp_path):
    """只给结构化条件（无文字命中）→ structural 档，证据更少，但必须正常返回。"""
    production, _asset = _workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a", usage_roles=["b_roll"],  # 没有 query
    )
    assert results, "结构化条件也应返回候选"
    for match in results:
        dumped = asdict(match)
        validated = MaterialSegmentMatchResponse.model_validate(dumped)  # 降级路径不许 422
        assert hasattr(validated, "match_tier")
        assert validated.match_tier == "structural", f"无文字命中应为 structural 档：{dumped}"
        # 降级到 structural 时，命中词本就为空——这是设计，不是丢字段
        assert validated.matched_terms == [], f"structural 档不应有命中词：{dumped}"


def test_evidence_field_names_match_production_dataclass(tmp_path):
    """守住接缝两侧的字段名不漂移：schema 声明的证据字段必须与 production 实际字段同名。"""
    from app.production import MaterialSegmentMatch

    dataclass_fields = {f.name for f in fields(MaterialSegmentMatch)}
    schema_fields = set(MaterialSegmentMatchResponse.model_fields)
    for name in EVIDENCE_FIELDS:
        assert name in dataclass_fields, f"production 侧没有 {name}"
        assert name in schema_fields, f"schema 侧没有 {name}"
