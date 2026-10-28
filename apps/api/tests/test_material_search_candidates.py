"""素材检索要能挑出「更好的」镜头，并给得出可核对的依据。

覆盖 B09 / WIL-25 验收标准那半句「03 镜头换更好素材可定位且有候选证据」：
只做「可定位 + 有候选证据」，不做自动替换。
"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.production import MaterialAsset, MaterialSegment, ProductionRepository


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
    review_status: str = "approved",
    usage_role: str = "b_roll",
) -> MaterialAsset:
    """建一条已分析的素材，并把每个片段写成给定的描述/标签/转录。"""
    (production.video_library_path / file_name).write_bytes(b"fixture")
    production.bind_material_ingestion(workspace_id, expected_directory=str(production.video_library_path))
    production.confirm_material_copy_complete(
        workspace_id, relative_path=file_name,
        expected_revision=production.material_copy_revision((production.video_library_path / file_name).stat()),
        confirmation_source="manual",
    )
    production.scan_library(workspace_id)
    asset = next(
        item
        for item in production.list_assets(workspace_id)
        if item.file_name == file_name
    )
    asset = production.analyze_asset(workspace_id, asset.id)
    # 先把整条素材定审核状态（update_segment 要求素材已进入可用库才能批片段）。
    asset = production.update_asset_review(
        workspace_id,
        asset.id,
        review_status=review_status,
        usage_role="exclude" if review_status == "rejected" else usage_role,
    )
    for patch in segments:
        patch = dict(patch)
        start = patch.pop("_start", None)
        segment = next(item for item in asset.segments if item.start_seconds == start)
        asset = production.update_segment(
            workspace_id,
            asset.id,
            segment.id,
            review_status=review_status,
            usage_role="exclude" if review_status == "rejected" else usage_role,
            **patch,  # type: ignore[arg-type]
        )
    for tag in asset_tags or []:
        asset = production.update_asset_tags(workspace_id, asset.id, [tag])
    asset = production.get_asset(workspace_id, asset.id)
    # These repository-level search cases model material explicitly shared to
    # the workspace reader; they do not bypass the private-owner filter.
    return production.set_asset_visibility(workspace_id, asset.id, "shared")


# 三个片段的时长分别是 5 秒 / 12 秒 / 8 秒（scene_reader 把 25 秒切成这三段）。
def _three_segment_workspace(tmp_path: Path) -> tuple[ProductionRepository, MaterialAsset]:
    production = _repository(tmp_path)
    asset = _approved_asset(
        production,
        "workspace_a",
        "office.mp4",
        segments=[
            {
                "_start": 0.0,
                "description": "电脑打字演示",
                "transcript": "演示如何编辑文档",
                "user_tags": ["动作:打字"],
            },
            {
                "_start": 5.0,
                "description": "窗边的城市夜景，灯光渐次亮起",
                "transcript": "",
                "user_tags": ["场景:夜景"],
            },
            {
                "_start": 17.0,
                "description": "街道上很吵的市声，车流不断",
                "transcript": "",
                "user_tags": ["场景:街道"],
            },
        ],
        asset_tags=["项目:教程"],
    )
    return production, asset


# 红线测试用的片段：两条素材共用同一份文字与标签，差别只在状态上。
def _same_text_segment() -> dict[str, object]:
    return {
        "_start": 0.0,
        "description": "同文同标签的镜头",
        "transcript": "",
        "user_tags": ["标签:对照"],
    }


# --------------------------------------------------------------------------
# 一、修复前必须失败：抽象查询现在返回空
# --------------------------------------------------------------------------


def test_abstract_longer_query_returns_candidates_instead_of_empty(tmp_path):
    """B09 的 bug 本体：问「更长一点的」拿回空列表。"""
    production, asset = _three_segment_workspace(tmp_path)
    # 用户说「这个片段太短了」——手上那个就是最短的那条。
    current = min(asset.segments, key=lambda item: item.end_seconds - item.start_seconds)
    slot = current.end_seconds - current.start_seconds
    longest = max(asset.segments, key=lambda item: item.end_seconds - item.start_seconds)

    # 用户原话「增加 20%」→ agent 把它翻译成 min_duration_seconds。
    results = production.material_search.search_material_segments(
        "workspace_a",
        query="更长一点的",
        usage_roles=["b_roll"],
        min_duration_seconds=slot * 1.2,
    )

    assert results, "抽象查询必须返回可核对的候选，不能空转"
    assert results[0].segment_ref == f"segment:{longest.id}"
    assert results[0].rank == 1


def test_abstract_query_with_structured_conditions_still_returns(tmp_path):
    production, _asset = _three_segment_workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a",
        query="不那么吵的",
        usage_roles=["b_roll"],
        tags=["场景:街道"],
    )
    assert results, "带结构化条件的抽象查询不能空转"
    assert any("场景:街道" in reason for match in results for reason in match.reasons)


def test_partial_overlap_query_is_not_rejected(tmp_path):
    """字面不等但有实质重合：换词、插词之后仍应能被找到。"""
    production, _asset = _three_segment_workspace(tmp_path)
    night = production.material_search.search_material_segments(
        "workspace_a", query="灯光夜景"
    )
    assert night, "词序不同但字符重合的查询应能命中"
    assert any(
        "夜景" in term or "灯光" in term
        for match in night
        for term in match.matched_terms
    )


# --------------------------------------------------------------------------
# 二、不许回归：精确查询和两条既有负向不变量
# --------------------------------------------------------------------------


def test_exact_query_still_ranks_the_exact_segment_first(tmp_path):
    production, asset = _three_segment_workspace(tmp_path)
    typing = next(item for item in asset.segments if item.description == "电脑打字演示")

    results = production.material_search.search_material_segments("workspace_a", query="电脑打字演示")

    assert results[0].segment_ref == f"segment:{typing.id}"
    assert results[0].match_tier == "text"
    assert results[0].matched_terms


def test_text_only_query_with_no_overlap_still_returns_nothing(tmp_path):
    """既有不变量：没有结构化条件时，匹配不上就是匹配不上。

    这条对应 test_material_speech 里「旧转录词不得复活」的断言。
    """
    production, _asset = _three_segment_workspace(tmp_path)
    assert production.material_search.search_material_segments("workspace_a", query="USB") == []
    assert production.material_search.search_material_segments("workspace_a", query="季度财报") == []


def test_query_units_strip_function_chars_before_bigrams():
    """切分规则本身就是「依据」的一部分，规则变了依据就说不清了。"""
    from app.materials.segment_search_use_case import _material_query_units

    # 「的」是虚字，不该切出「点的」；「更」「长」是实字，必须留下。
    assert _material_query_units("更长一点的") == ["更长", "长一", "一点"]
    assert _material_query_units("电脑打字演示") == ["电脑", "脑打", "打字", "字演", "演示"]
    # 英文虚词不进依据，英文实词进。
    assert _material_query_units("a quiet office") == ["quiet", "office"]
    # 纯虚字查询不该造出噪声依据。
    assert _material_query_units("的") == []


# --------------------------------------------------------------------------
# 三、红线：三条硬过滤，一条都不许松
# --------------------------------------------------------------------------


def test_cross_workspace_material_is_never_returned(tmp_path):
    """红线 1：workspace 隔离。"""
    production = _repository(tmp_path)
    _approved_asset(
        production,
        "workspace_a",
        "secret-a.mp4",
        segments=[
            {
                "_start": 0.0,
                "description": "机密客户现场拍摄",
                "transcript": "",
                "user_tags": ["客户:机密"],
            },
        ],
    )
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign_production = _repository(foreign_root)
    foreign_asset = _approved_asset(
        foreign_production,
        "workspace_b",
        "secret-b.mp4",
        segments=[
            {
                "_start": 0.0,
                "description": "机密客户现场拍摄",
                "transcript": "",
                "user_tags": ["客户:机密"],
            },
        ],
    )

    # Seed already-confirmed foreign facts without rebinding the primary inbox.
    production._save_asset(foreign_asset)

    probes = [
        {"query": "机密客户现场拍摄"},
        {"query": "机密", "tags": ["客户:机密"]},
        {"query": "更长一点的", "min_duration_seconds": 0.1},
        {"query": "更长一点的", "usage_roles": ["b_roll"]},
        {"query": "", "usage_roles": ["b_roll", "a_roll"]},
        {"usage_roles": ["b_roll"]},
    ]
    for probe in probes:
        found = production.material_search.search_material_segments("workspace_a", **probe)  # type: ignore[arg-type]
        assert found, f"对照：workspace_a 自己的素材应当能被检索到 {probe}"
        assert all(
            not match.asset_ref.startswith(f"asset:{asset_id}")
            for match in found
            for asset_id in [
                item.id
                for item in production.list_assets("workspace_b")
            ]
        ), f"跨 workspace 泄漏：{probe}"


def test_unapproved_material_is_never_returned(tmp_path):
    """红线 2：只搜已审核。rejected 一律搜不到。

    两条素材的**文字、标签、用途完全相同**，唯一差别是审核状态——
    这样「搜不到」只可能来自审核状态，不可能来自「它长得不一样」。
    """
    production = _repository(tmp_path)
    _approved_asset(
        production,
        "workspace_a",
        "rejected.mp4",
        segments=[_same_text_segment()],
        review_status="rejected",
    )
    _approved_asset(
        production,
        "workspace_a",
        "approved.mp4",
        segments=[_same_text_segment()],
    )
    rejected = next(
        item
        for item in production.list_assets("workspace_a")
        if item.file_name == "rejected.mp4"
    )
    rejected_segments = {segment.id for segment in rejected.segments}

    for probe in (
        {"query": "同文同标签的镜头"},
        {"query": "对照", "tags": ["标签:对照"]},
        {"query": "更长一点的", "min_duration_seconds": 0.1},
        {"query": "更长一点的", "usage_roles": ["b_roll"]},
        {"query": "", "usage_roles": ["b_roll"]},
        {"query": "", "limit": 100},
    ):
        found = production.material_search.search_material_segments("workspace_a", **probe)  # type: ignore[arg-type]
        assert found, f"对照：已审核素材应当能被检索到 {probe}"
        assert all(
            match.asset_ref != f"asset:{rejected.id}" for match in found
        ), f"未审核素材泄漏：{probe}"
        assert all(
            match.segment_ref.removeprefix("segment:") not in rejected_segments
            for match in found
        ), f"未审核片段泄漏：{probe}"


def test_unpersisted_material_is_never_returned(tmp_path):
    """红线 3：只搜已持久化。只扫描未分析的素材搜不到。

    正常流程下未分析的素材不会有片段，所以这里**手工**往数据库里塞一个
    99 秒的片段——如果硬过滤真的松了，它会在「更长一点的」里直接冲到第一。
    """
    production = _repository(tmp_path)
    _approved_asset(
        production,
        "workspace_a",
        "ready.mp4",
        segments=[_same_text_segment()],
    )

    # 只扫描、未分析：正常流程下不会有片段，但手工塞一段进去也不该被搜到。
    (production.video_library_path / "pending.mp4").write_bytes(b"fixture")
    production.confirm_material_copy_complete(
        "workspace_a", relative_path="pending.mp4",
        expected_revision=production.material_copy_revision((production.video_library_path / "pending.mp4").stat()),
        confirmation_source="manual",
    )
    production.scan_library("workspace_a")
    pending = next(
        item
        for item in production.list_assets("workspace_a")
        if item.file_name == "pending.mp4"
    )
    assert pending.analysis_status == "pending"
    production._save_asset(
        replace(
            pending,
            segments=[
                MaterialSegment(
                    id="seg_smuggled",
                    asset_id=pending.id,
                    start_seconds=0.0,
                    end_seconds=99.0,
                    description="同文同标签的镜头",
                    review_status="approved",
                    user_tags=["标签:对照"],
                )
            ],
        )
    )

    for probe in (
        {"query": "同文同标签的镜头"},
        {"query": "对照", "tags": ["标签:对照"]},
        {"query": "更长一点的", "min_duration_seconds": 0.1},
        {"query": "更长一点的", "usage_roles": ["b_roll"]},
        {"query": "", "usage_roles": ["b_roll"]},
        {"query": "", "limit": 100},
    ):
        found = production.material_search.search_material_segments("workspace_a", **probe)  # type: ignore[arg-type]
        assert found, f"对照：已持久化素材应当能被检索到 {probe}"
        assert all(
            match.asset_ref != f"asset:{pending.id}" for match in found
        ), f"未持久化素材泄漏：{probe}"
        assert all(
            match.segment_ref != "segment:seg_smuggled" for match in found
        ), f"未持久化片段泄漏：{probe}"


def test_excluded_use_state_material_is_never_returned(tmp_path):
    """已有的可用性边界不被放宽：reference_only / excluded 仍然搜不到。"""
    production, asset = _three_segment_workspace(tmp_path)
    for use_state in ("reference_only", "excluded"):
        production.update_asset_use_state("workspace_a", asset.id, use_state)
        found = production.material_search.search_material_segments(
            "workspace_a", query="更长一点的", usage_roles=["b_roll"], min_duration_seconds=0.1
        )
        assert not found, f"{use_state} 的素材不该被检索出来"
    production.update_asset_use_state("workspace_a", asset.id, "available")
    assert production.material_search.search_material_segments(
        "workspace_a", query="更长一点的", usage_roles=["b_roll"], min_duration_seconds=0.1
    )


# --------------------------------------------------------------------------
# 四、每条候选都要带可核对的依据
# --------------------------------------------------------------------------


def test_every_candidate_carries_checkable_evidence(tmp_path):
    production, asset = _three_segment_workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a",
        query="更长一点的",
        usage_roles=["b_roll"],
        min_duration_seconds=1.0,
    )
    assert results
    by_ref = {
        f"segment:{item.id}": item for item in asset.segments
    }
    for rank, match in enumerate(results, start=1):
        assert match.asset_ref.startswith("asset:")
        assert match.segment_ref.startswith("segment:")
        assert match.rank == rank
        assert match.match_tier in {"text", "partial", "structural"}
        # 来源
        assert match.asset_file_name == "office.mp4"
        # 时间点
        segment = by_ref[match.segment_ref]
        assert match.start_seconds == pytest.approx(segment.start_seconds)
        assert match.end_seconds == pytest.approx(segment.end_seconds)
        assert match.time_range
        # 时长
        assert match.duration_seconds == pytest.approx(
            segment.end_seconds - segment.start_seconds
        )
        # 标签
        assert match.tags, f"{match.segment_ref} 应当带标签"
        # 为什么被选中
        assert match.matched_terms or match.matched_conditions
        assert match.reasons
        assert any("排第" in reason for reason in match.reasons)


def test_structural_fallback_says_why_text_did_not_match(tmp_path):
    production, _asset = _three_segment_workspace(tmp_path)
    results = production.material_search.search_material_segments(
        "workspace_a",
        query="不那么吵的",
        usage_roles=["b_roll"],
        min_duration_seconds=0.1,
    )
    assert results
    assert all(match.match_tier in {"partial", "structural"} for match in results)
    for match in results:
        if match.match_tier == "structural":
            assert any("文字未匹配" in reason for reason in match.reasons)
            assert match.matched_conditions


def test_serialized_match_shape_keeps_existing_fields(tmp_path):
    """响应形状的既有字段不能消失（schemas.py 与 main.py 不在本包可写范围）。"""
    production, _asset = _three_segment_workspace(tmp_path)
    match = production.material_search.search_material_segments("workspace_a", query="电脑打字演示")[0]
    payload = asdict(match)
    for field in ("asset_ref", "segment_ref", "score", "reasons"):
        assert field in payload
