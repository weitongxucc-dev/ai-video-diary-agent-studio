"""WIL-95：素材元数据复核失败时，证据措辞必须与实际返回的字段一致。

背景：find_material_candidates 会为命中的候选按 asset ID 再取一次素材元数据
（首页没有的、或需要归属校验的）。这一步失败时，旧文案有两个问题：

1. 断言失败原因「不属于当前工作区或已被删除」——但一次读取失败完全可能是
   5xx、超时或网络不可达，工具没有任何依据断定原因。
2. 断言「这些候选的 file_name/tags/duration 为空」——但 file_name 与 tags
   会从检索响应兜底，实际可能非空；只有 duration_seconds 与 source_reference
   确实只来自复核后的素材元数据，取不到才是空。

这里只验证措辞与实际字段一致，不改任何返回字段，也不验证检索算法。
全部使用合成数据，不出现任何真实用户内容。
"""
from __future__ import annotations

import pytest

from test_agent_recall_memory import AGENT_TOOLS, call

pytestmark = pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)

# 旧文案的两处断言——它们本身就是本次要修掉的回归点。
OLD_CAUSE_CLAIM = "（不属于当前工作区或已被删除）"
OLD_ALL_EMPTY_CLAIM = "file_name/tags/duration 为空"

EMPTY_LIBRARY = {"assets": [], "total_count": 0, "limit": 100, "offset": 0, "has_more": False}


def _match(**overrides) -> dict:
    base = {
        "asset_ref": "asset:a999",
        "segment_ref": "segment:s999",
        "score": 0.9,
        "reasons": ["自然语言匹配片段描述"],
        "rank": 1,
        "match_tier": "text",
        "time_range": "00:01.0 – 00:07.5",
    }
    base.update(overrides)
    return base


def _routes(match: dict, asset_route: dict) -> dict:
    # 具体素材路由必须排在库列表前面：桩按插入顺序做子串匹配。
    return {
        "/api/production/materials/search": [match],
        "/api/production/materials/a999": asset_route,
        "/api/production/materials": EMPTY_LIBRARY,
    }


NOT_FOUND = {"__status": 404, "detail": "material asset not found"}
SERVER_ERROR = {"__status": 500, "detail": "internal error"}


def _call(routes: dict) -> dict:
    return call("find_material_candidates", {"description": "城市夜景"}, routes)


class TestSearchFallbackIsNotReportedAsEmpty:
    """检索响应带了 name/tags 时，文案不得说这些字段为空。"""

    ROUTES = _routes(
        _match(asset_file_name="city_night_001.mp4", tags=["城市", "夜"]),
        NOT_FOUND,
    )

    def test_search_name_and_tags_survive_and_are_reported_as_search_sourced(self):
        result = _call(self.ROUTES)
        first = result["candidates"][0]
        # 字段行为保持原样：兜底值照旧返回，本次只修文案。
        assert first["file_name"] == "city_night_001.mp4", first
        assert "城市" in first["tags"], first
        assert result["evidence_complete"] is False

        note = result["evidence_note"]
        assert OLD_ALL_EMPTY_CLAIM not in note, f"文案仍说三个字段全空，与实际返回不符：{note}"
        assert "未经本工作区元数据复核" in note, f"未说明 name/tags 来自检索、未复核：{note}"

    def test_note_stays_consistent_with_actually_empty_fields(self):
        result = _call(self.ROUTES)
        first = result["candidates"][0]
        # duration_seconds / source_reference 只来自复核后的元数据，确实取不到。
        assert first["duration_seconds"] is None, first
        assert first["source_reference"] is None, first
        assert "duration_seconds" in result["evidence_note"]

    def test_note_names_the_affected_asset(self):
        note = _call(self.ROUTES)["evidence_note"]
        assert "a999" in note, f"未用缺失 asset ID 指明受影响素材：{note}"


class TestFailureCauseIsNotInvented:
    """404 与非 404 都不得被断言成「已删除」或「跨工作区」。"""

    def test_404_does_not_claim_deletion_or_cross_workspace(self):
        result = _call(_routes(_match(), NOT_FOUND))
        note = result["evidence_note"]
        assert result["evidence_complete"] is False
        assert OLD_CAUSE_CLAIM not in note, f"404 被断言成了确定原因：{note}"
        assert "原因未确定" in note, f"未说明失败原因未确定：{note}"

    def test_server_error_does_not_claim_deletion_or_cross_workspace(self):
        """非 404（此处 500）同样不得被断言成已删除/跨工作区——旧文案两种都中招。"""
        result = _call(_routes(_match(), SERVER_ERROR))
        first = result["candidates"][0]
        note = result["evidence_note"]
        # 读取确实失败：不该凭空长出元数据。
        assert first["file_name"] is None, first
        assert first["duration_seconds"] is None, first
        assert first["source_reference"] is None, first
        assert result["evidence_complete"] is False
        assert OLD_CAUSE_CLAIM not in note, f"5xx 被断言成了已删除/跨工作区：{note}"
        assert "原因未确定" in note, f"未说明失败原因未确定：{note}"


class TestInstructionDoesNotOverclaim:
    """instruction 不得宣称取到元数据就等于内容已核实。"""

    def test_incomplete_instruction_keeps_disclosure_and_marks_search_values_unverified(self):
        result = _call(_routes(_match(asset_file_name="city_night_001.mp4", tags=["城市"]), NOT_FOUND))
        instruction = result["instruction"]
        assert "必须向用户说明" in instruction, instruction
        assert "不等于归属或内容已核实" in instruction, (
            f"instruction 未把检索兜底值与已核实区分开：{instruction}"
        )

    def test_complete_branch_is_untouched(self):
        """正向对照：元数据取到时走完整分支，不加未核实警告，也不改文案。"""
        routes = {
            "/api/production/materials/search": [_match(asset_ref="asset:a1")],
            "/api/production/materials": {
                "assets": [
                    {
                        "id": "a1",
                        "file_name": "mine.mp4",
                        "duration_seconds": 3.0,
                        "user_tags": ["城市"],
                        "source_reference": "src-1",
                    }
                ],
                "total_count": 1,
                "limit": 100,
                "offset": 0,
                "has_more": False,
            },
        }
        result = _call(routes)
        first = result["candidates"][0]
        assert first["file_name"] == "mine.mp4", first
        assert first["duration_seconds"] == 3.0, first
        assert result["evidence_complete"] is True
        assert result["evidence_note"] == "每条候选的依据都已取回。"
        assert "必须向用户说明" not in result["instruction"], result["instruction"]


class TestInstructionIsScopedToMissingCandidatesOnly:
    """「依据不全」只应指向元数据取不到的那些候选，不能牵连同一次调用里取回成功的。"""

    ROUTES = {
        "/api/production/materials/search": [
            _match(asset_ref="asset:a1", segment_ref="segment:s1", score=0.95, rank=1),
            _match(asset_file_name="city_night_001.mp4", tags=["城市"], score=0.8, rank=2),
        ],
        "/api/production/materials/a999": NOT_FOUND,
        "/api/production/materials": {
            "assets": [
                {
                    "id": "a1",
                    "file_name": "mine.mp4",
                    "duration_seconds": 3.0,
                    "user_tags": ["城市"],
                    "source_reference": "src-1",
                }
            ],
            "total_count": 1,
            "limit": 100,
            "offset": 0,
            "has_more": False,
        },
    }

    def test_note_and_instruction_name_only_the_missing_asset(self):
        result = _call(self.ROUTES)
        note, instruction = result["evidence_note"], result["instruction"]
        assert "a999" in note, note
        assert "素材 a1、" not in note, f"把复核成功的 a1 也算成缺失：{note}"
        assert "a999" in instruction, instruction
        assert "只针对这些候选说依据不全" in instruction, (
            f"instruction 未把「依据不全」限定到缺失候选：{instruction}"
        )

    def test_fetched_candidate_keeps_its_complete_evidence(self):
        result = _call(self.ROUTES)
        ok = next(c for c in result["candidates"] if c["asset_ref"] == "asset:a1")
        assert ok["file_name"] == "mine.mp4", ok
        assert ok["duration_seconds"] == 3.0, ok
        assert ok["source_reference"] == "src-1", ok

    def test_two_segments_of_one_missing_asset_do_not_imply_others_were_fetched(self):
        """同一素材的两个片段都缺元数据：缺失 asset 去重后只有 1 个，候选却有 2 行。

        任何按这两个数量推断「还有别的取回了」的说法在这里都是假的——本次
        一条 asset 级元数据都没取回来。
        """
        routes = {
            "/api/production/materials/search": [
                _match(segment_ref="segment:s999a", score=0.9, rank=1),
                _match(segment_ref="segment:s999b", score=0.85, rank=2),
            ],
            "/api/production/materials/a999": NOT_FOUND,
            "/api/production/materials": EMPTY_LIBRARY,
        }
        result = _call(routes)
        assert len(result["candidates"]) == 2, result["candidates"]
        note = result["evidence_note"]
        assert "其余候选的依据都已按 asset 取回" not in note, (
            f"同素材多片段时误称其余候选已取回：{note}"
        )
        assert "a999" in note, note
        for candidate in result["candidates"]:
            assert candidate["duration_seconds"] is None, candidate
            assert candidate["source_reference"] is None, candidate



def test_returned_fields_are_unchanged_by_this_fix():
    """回归护栏：本次只改文案，返回字段集合不得增减。"""
    result = _call(_routes(_match(asset_file_name="city_night_001.mp4", tags=["城市"]), NOT_FOUND))
    first = result["candidates"][0]
    assert set(first) == {
        "asset_ref", "segment_ref", "score", "rank", "match_tier", "matched_terms",
        "matched_conditions", "time_range", "start_seconds", "end_seconds",
        "segment_duration_seconds", "file_name", "duration_seconds", "tags",
        "source_reference", "reasons",
    }, f"返回字段集合被改动了：{sorted(first)}"
    assert set(result) == {
        "candidates", "evidence_complete", "evidence_note",
        "library_total", "library_has_more", "instruction",
    }, f"details 字段集合被改动了：{sorted(result)}"
