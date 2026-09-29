"""agent 扩展（diary_tools.ts）专项测试。

通过 node 加载真实的 TS 扩展并驱动其工具，验证 B08 中文检索修复、
B10 记忆接线、以及按描述找候选素材工具。全部使用合成数据，
不出现任何真实用户内容。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

AGENT_TOOLS = Path(__file__).resolve().parents[1] / "agent_tools"
RUNNER = AGENT_TOOLS / "_test_runner.mjs"
EXTENSION = AGENT_TOOLS / "diary_tools.ts"


def run_ops(*ops: dict, env_extra: dict | None = None) -> list[dict]:
    """把若干 op 喂给 node 运行器，返回解析后的结果列表。"""
    env = {
        **os.environ,
        "AI_VIDEO_DIARY_AGENT_API_BASE": "http://stub",
        "DIARY_TOOLS_PATH": str(EXTENSION),
    }
    if env_extra:
        env.update(env_extra)
    payload = "\n".join(json.dumps(op) for op in ops) + "\n"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(RUNNER)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, f"node runner failed: {proc.stderr}"
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def call(tool: str, params: dict, routes: dict, env_extra: dict | None = None) -> dict:
    [result] = run_ops({"op": "call", "tool": tool, "params": params, "routes": routes}, env_extra=env_extra)
    assert result.get("ok"), f"tool {tool} raised: {result.get('error')}"
    return result["result"]


def call_with_requests(tool: str, params: dict, routes: dict) -> tuple[dict, list[str]]:
    """同 call()，但额外返回工具实际发出的请求 URL（用于断言分页 limit 等）。"""
    [result] = run_ops({"op": "call", "tool": tool, "params": params, "routes": routes})
    assert result.get("ok"), f"tool {tool} raised: {result.get('error')}"
    return result["result"], result.get("requests", [])


def probe(url: str, routes: dict | None = None, options: dict | None = None) -> dict:
    """不经工具、直接拿桩去请求一个 URL。

    返回原始 runner 结果：{ok, status, body} 或 {ok:False, error}。
    故意不 assert ok —— 「用例声明的参数不匹配时桩报错」本身就是被测行为之一。
    """
    [result] = run_ops({"op": "probe", "url": url, "routes": routes or {}, "options": options or {}})
    return result


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestChineseRetrieval:
    """B08：中文查询曾经必然零命中。"""

    ROUTES = {
        "/api/knowledge-items": {
            "items": [
                {"id": "k1", "title": "剪辑效率提升", "text": "通过自动识别字幕来提高剪辑效率，减少手动打轴时间。", "version": 1},
                {"id": "k2", "title": "拍摄清单", "text": "拍摄前先列好分镜清单，避免返工。", "version": 1},
                {"id": "k3", "title": "English notes", "text": "Use tripod for outdoor shots and stable framing.", "version": 1},
            ]
        }
    }

    def test_chinese_query_hits_matching_item(self):
        result = call("find_knowledge", {"query": "怎么提高剪辑效率"}, self.ROUTES)
        ids = [m["id"] for m in result["matches"]]
        assert "k1" in ids, f"中文查询未命中任何条目，matches={result['matches']}"

    def test_another_chinese_query(self):
        result = call("find_knowledge", {"query": "如何避免返工"}, self.ROUTES)
        ids = [m["id"] for m in result["matches"]]
        assert "k2" in ids, f"中文查询未命中，matches={result['matches']}"

    def test_english_tags_do_not_regress(self):
        result = call("find_knowledge", {"query": "tripod"}, self.ROUTES)
        ids = [m["id"] for m in result["matches"]]
        assert "k3" in ids, f"英文检索回归，matches={result['matches']}"

    def test_mixed_chinese_and_english_query(self):
        result = call("find_knowledge", {"query": "outdoor 分镜"}, self.ROUTES)
        ids = [m["id"] for m in result["matches"]]
        # 「分镜」在 k2，outdoor 在 k3
        assert "k2" in ids, f"混合查询未命中中文词，matches={result['matches']}"

    def test_no_match_returns_guidance_not_silent_empty(self):
        # 匹配不上时必须给出「换个说法」的提示，而不是干瘪的空
        result = call("find_knowledge", {"query": "量子计算"}, self.ROUTES)
        assert "matched" in result or "matches" in result
        # 允许 matches 为空，但 details 里必须带 guidance 之类的可行动提示
        assert result.get("guidance") or result.get("hint") or result.get("instruction"), (
            f"未匹配时缺少引导语，result={result}"
        )


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestPreferenceMemory:
    """B10：agent 能读到用户偏好记忆。"""

    ROUTES = {
        "/api/preferences": {
            "total_feedback_count": 5,
            "preferences": [
                {"label": "偏好", "signal": "节奏快", "evidence_count": 3, "confidence": 0.8, "enabled": True},
                {"label": "避开", "signal": "拖沓开头", "evidence_count": 2, "confidence": 0.65, "enabled": True},
            ],
        }
    }

    def test_reads_preference_memory(self):
        result = call("read_preference_memory", {}, self.ROUTES)
        # 问答例子：「这个用户以前说过什么偏好？」→ prefer/avoid 两栏
        assert "prefer" in result, f"记忆结果缺少 prefer，result={result}"
        assert "avoid" in result, f"记忆结果缺少 avoid，result={result}"
        assert any(p["signal"] == "节奏快" for p in result["prefer"]), result
        assert any(a["signal"] == "拖沓开头" for a in result["avoid"]), result

    def test_preference_evidence_counts_present(self):
        result = call("read_preference_memory", {}, self.ROUTES)
        for item in result["prefer"] + result["avoid"]:
            assert "evidence_count" in item, f"记忆项缺依据字段：{item}"
            assert "confidence" in item, f"记忆项缺置信度：{item}"


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestFindMaterialCandidates:
    """按描述找候选素材：必须带可核对依据，且不自动替换。"""

    ROUTES = {
        "/api/production/materials/search": [
            {"asset_ref": "asset:a1", "segment_ref": "segment:s1", "score": 0.9, "reasons": ["标签匹配 城市,夜"]},
        ],
        "/api/production/materials": {
            "assets": [
                {
                    "id": "a1",
                    "file_name": "city_night_001.mp4",
                    "duration_seconds": 6.5,
                    "user_tags": ["城市", "夜"],
                    "source_reference": "asset-source-001",
                }
            ]
        },
    }

    def test_returns_candidates_with_evidence(self):
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES)
        candidates = result["candidates"]
        assert candidates, "没有返回候选"
        first = candidates[0]
        # 依据字段：让用户一眼能判断对错
        assert "asset_ref" in first
        assert "file_name" in first, f"候选缺文件名依据：{first}"
        assert "tags" in first, f"候选缺标签依据：{first}"
        assert "duration_seconds" in first, f"候选缺时长依据：{first}"
        assert "reasons" in first and first["reasons"], f"候选缺匹配原因：{first}"

    def test_does_not_auto_replace(self):
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES)
        # 明确不允许自动执行：结果里不应出现任何时间线修改动作
        assert "proposal" not in result, "不应产出剪辑提案"
        assert "applies" not in result and "applied" not in result
        text = json.dumps(result, ensure_ascii=False)
        assert "已替换" not in text and "已执行" not in text


# ── 能力接缝收口（B08/B09/B10 收尾 + WIL-25 候选证据）────────────────────────
#
# 两处接缝：
# 1) apps/api/app/schemas.py 的 MaterialSegmentMatchResponse 只有 4 个字段，
#    Pydantic v2 对未声明字段 extra='ignore' 静默丢弃，Pkg2 造出的证据字段
#    （rank/match_tier/matched_terms/time_range/...）到不了 HTTP 客户端。
# 2) find_material_candidates 自己再拉一次素材库补 asset 级元数据，但只取第
#    一页（且历史上写了 limit=200，而路由上限是 100 → 422）。素材多于一页时，
#    命中候选若不在首页，file_name/tags/duration 会安静地变成 null。
#
# 下列断言在修复前必须失败（见报告「修复前失败输出」）。


def _pkg2_match(**overrides) -> dict:
    """照抄 Pkg2 production.py MaterialSegmentMatch 的真实产出形状。"""
    base = {
        "asset_ref": "asset:a1",
        "segment_ref": "segment:s1",
        "score": 1.2345,
        "reasons": ["自然语言匹配片段描述", "综合排第 1 名（本次候选 3 条）"],
        "rank": 1,
        "match_tier": "text",
        "matched_terms": ["城市", "夜景"],
        "matched_conditions": ["用途 b_roll"],
        "asset_file_name": "city_night_001.mp4",
        "start_seconds": 1.0,
        "end_seconds": 7.5,
        "duration_seconds": 6.5,
        "time_range": "00:01.0 – 00:07.5",
        "tags": ["城市", "夜"],
    }
    base.update(overrides)
    return base


class TestSegmentMatchSchemaCarriesEvidence:
    """证据字段必须真的活到 schema 结果里（B08/B09 的根因）。"""

    def test_evidence_fields_survive_model_validate(self):
        from app.schemas import MaterialSegmentMatchResponse

        match = _pkg2_match()
        result = MaterialSegmentMatchResponse.model_validate(match)
        dumped = result.model_dump()
        for field in (
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
        ):
            assert field in dumped, (
                f"证据字段 {field} 被 schema 静默丢弃了："
                f"产出={sorted(match)} 结果={sorted(dumped)}"
            )

    def test_evidence_values_are_preserved(self):
        from app.schemas import MaterialSegmentMatchResponse

        dumped = MaterialSegmentMatchResponse.model_validate(_pkg2_match()).model_dump()
        assert dumped["rank"] == 1
        assert dumped["match_tier"] == "text"
        assert dumped["matched_terms"] == ["城市", "夜景"]
        assert dumped["time_range"] == "00:01.0 – 00:07.5"
        assert dumped["duration_seconds"] == 6.5
        assert dumped["tags"] == ["城市", "夜"]

    def test_structural_degraded_match_does_not_422(self):
        """降级路径（structural 层）证据不全也必须正常返回，不许 422。"""
        from app.schemas import MaterialSegmentMatchResponse

        degraded = {
            "asset_ref": "asset:a9",
            "segment_ref": "segment:s9",
            "score": 0.25,
            "reasons": ["文字未匹配，按结构化条件返回"],
            "match_tier": "structural",
        }
        result = MaterialSegmentMatchResponse.model_validate(degraded)
        assert result.match_tier == "structural"
        assert result.rank == 0
        assert result.matched_terms == []
        assert result.tags == []
        assert result.time_range == ""

    def test_score_upper_bound_is_not_invented(self):
        """Pkg2 不对 score 做上限截断（多标签 + 多文字命中可 >1.0），
        所以 schema 不得加 le=1.0 把合法值挡成 422。"""
        from app.schemas import MaterialSegmentMatchResponse

        high = MaterialSegmentMatchResponse.model_validate(_pkg2_match(score=2.4))
        assert high.score == pytest.approx(2.4)


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
def _library_with_250(total: int = 250, page_size: int = 100) -> dict:
    """首页只含 asset a1..a100；命中候选是第 210 条（不在首页）。"""
    assets = [
        {
            "id": f"a{i}",
            "file_name": f"clip_{i:03d}.mp4",
            "duration_seconds": 5.0 + i,
            "user_tags": [f"标签{i}"],
            "source_reference": f"src-{i}",
        }
        for i in range(1, page_size + 1)
    ]
    return {
        "assets": assets,
        "total_count": total,
        "limit": page_size,
        "offset": 0,
        "has_more": True,
    }


# 路由键按插入顺序匹配（子串先到先得），所以具体素材路由必须排在库列表前面。
ROUTES_210 = {
    "/api/production/materials/search": [
        _pkg2_match(asset_ref="asset:a210", segment_ref="segment:s210", rank=1),
    ],
    "/api/production/materials/a210": {
        "id": "a210",
        "file_name": "hidden_gem_210.mp4",
        "duration_seconds": 42.5,
        "user_tags": ["城市", "夜"],
        "system_tags": ["夜景"],
        "source_reference": "src-210",
    },
    "/api/production/materials": _library_with_250(),
}


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestEvidenceSurvivesLargeLibrary:
    """素材库超过一页时，命中候选的依据不许被截断成 null。"""

    ROUTES_210 = ROUTES_210

    def test_candidate_beyond_first_page_keeps_evidence(self):
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES_210)
        first = result["candidates"][0]
        assert first["file_name"] == "hidden_gem_210.mp4", f"第 210 条的依据被截断了：{first}"
        assert first["duration_seconds"] == 42.5, f"第 210 条缺时长：{first}"
        assert first["tags"], f"第 210 条标签为空：{first}"
        assert "城市" in first["tags"]

    def test_library_request_stays_within_route_limit(self):
        """路由 limit 上限是 100（main.py 越界即 422）。上一包写了 200，
        真实服务上整条工具会直接抛错；桩不校验查询参数所以当时测试全绿。"""
        _, requests = call_with_requests("find_material_candidates", {"description": "城市夜景"}, self.ROUTES_210)
        library_calls = [u for u in requests if "/api/production/materials?" in u]
        assert library_calls, f"没有发起素材库列表请求：{requests}"
        for url in library_calls:
            assert "limit=200" not in url, f"请求了越界的 limit（真实服务会 422）：{url}"
            assert "limit=100" in url or "limit=50" in url or "limit=24" in url, f"分页参数异常：{url}"

    def test_segment_level_evidence_reaches_the_tool(self):
        """片段级证据（时间点/命中词/排第几）必须进到工具输出里。"""
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES_210)
        first = result["candidates"][0]
        for field in ("time_range", "start_seconds", "end_seconds", "matched_terms", "rank", "match_tier"):
            assert field in first, f"候选缺片段级依据 {field}：{first}"
        assert first["time_range"] == "00:01.0 – 00:07.5"
        assert first["matched_terms"] == ["城市", "夜景"]
        assert first["match_tier"] == "text"
        assert first["rank"] == 1


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestStubValidatesQueryParams:
    """桩必须像真后端一样校验查询参数。

    上一包的洞：工具写 ?limit=200，真后端会 422，桩却回 200，于是 bug 在全绿
    测试下溜过去。堵上之后，任何越界/非法枚举的入参都必须被桩抓出来。
    """

    LIST_ROUTE = {
        "/api/production/materials": {
            "__expect_query": {"limit": "100"},
            "assets": [], "total_count": 0, "has_more": False,
        },
    }

    def test_stub_rejects_limit_over_100(self):
        """上一包真实踩到的洞：limit>100 真后端 422，桩也必须 422。"""
        result = probe("http://stub/api/production/materials?limit=200", self.LIST_ROUTE)
        assert result.get("status") == 422, f"limit=200 应被桩拒掉（真后端会 422），桩却回了 {result}"

    def test_stub_accepts_limit_within_range(self):
        result = probe("http://stub/api/production/materials?limit=100", self.LIST_ROUTE)
        assert result.get("status") == 200, f"limit=100 是合法的，桩不该拒：{result}"

    def test_stub_rejects_invalid_usage_role_enum(self):
        result = probe(
            "http://stub/api/production/materials/search?q=x&usage_role=bogus",
            {"/api/production/materials/search": []},
        )
        assert result.get("status") == 422, f"非法 usage_role 应被桩拒掉，桩却回了 {result}"

    def test_stub_accepts_valid_usage_role(self):
        result = probe(
            "http://stub/api/production/materials/search?q=x&usage_role=b_roll",
            {"/api/production/materials/search": []},
        )
        assert result.get("status") == 200, f"合法 usage_role 不该被拒：{result}"

    def test_case_declared_query_expectation_is_enforced(self):
        """用例声明期望参数后，不匹配就报错——不再静默通过。"""
        result = probe(
            "http://stub/api/production/materials?limit=50",
            self.LIST_ROUTE,
            options={"strictQuery": True},
        )
        assert result.get("ok") is False, f"声明的参数不匹配时桩应报错，实际：{result}"
        assert "mismatch" in str(result.get("error", "")), f"报错信息应指明参数不匹配：{result}"

    def test_case_declared_query_expectation_passes_when_matched(self):
        result = probe(
            "http://stub/api/production/materials?limit=100",
            self.LIST_ROUTE,
            options={"strictQuery": True},
        )
        assert result.get("status") == 200, f"参数匹配时不该报错：{result}"


@pytest.mark.skipif(
    not (AGENT_TOOLS / "_test_runner.mjs").exists(),
    reason="node runner not available",
)
class TestCrossWorkspaceIsolation:
    """跨 workspace 隔离是硬红线：别的空间的素材元数据一条都不许漏出来。

    真实威胁模型：检索端点是 workspace 作用域的，正常不会返回别的空间的候选。
    但一旦 asset_ref 指向一个本工作区取不到的素材（已删除、或归属校验拦下），
    逐个取回会拿到 404，工具必须把它当成「拿不到依据」而不是把别处的元数据
    漏出去，更不能编一个像样的文件名填上。
    """

    # a999 取不到：后端 get_asset(workspace_id, asset_id) 抛 404
    # （main.py get_production_material；归属不符时同为 404）。
    # 检索响应里也不带 asset_file_name —— 真实情况下这条匹配本就属于本工作区。
    ROUTES_FOREIGN = {
        "/api/production/materials/search": [
            {
                "asset_ref": "asset:a999",
                "segment_ref": "segment:s999",
                "score": 0.9,
                "reasons": ["自然语言匹配片段描述"],
                "rank": 1,
                "match_tier": "text",
                "time_range": "00:01.0 – 00:07.5",
            },
        ],
        "/api/production/materials/a999": {
            "__status": 404,
            "detail": "material asset not found",
        },
        "/api/production/materials": {
            "assets": [
                {
                    "id": "a1",
                    "file_name": "mine.mp4",
                    "duration_seconds": 3.0,
                    "user_tags": ["本地"],
                    "source_reference": "src-1",
                }
            ],
            "total_count": 1,
            "limit": 100,
            "offset": 0,
            "has_more": False,
        },
    }

    def test_foreign_asset_metadata_is_not_leaked(self):
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES_FOREIGN)
        first = result["candidates"][0]
        # 别的 workspace 的文件名 / 标签 / 时长都不许出现，也不许编一个填上
        assert first["file_name"] is None, f"取不到依据时却给出了文件名：{first}"
        assert not first["tags"], f"取不到依据时却给出了标签：{first}"
        assert first["duration_seconds"] is None, f"取不到依据时却给出了时长：{first}"
        assert first["source_reference"] is None, f"取不到依据时却给出了来源：{first}"
        text = json.dumps(result, ensure_ascii=False)
        assert "mine.mp4" not in text, f"把本工作区其它素材的名字串了进来：{text}"

    def test_evidence_gap_is_stated_not_hidden(self):
        """拿不到依据时必须明说，不许拿一堆 null 冒充完整依据。"""
        result = call("find_material_candidates", {"description": "城市夜景"}, self.ROUTES_FOREIGN)
        assert result["evidence_complete"] is False
        assert "取不到" in result["evidence_note"], f"未说明依据不全：{result}"
        assert "必须向用户说明" in result["instruction"], f"未提示 agent 告知用户：{result}"

    def test_isolation_holds_when_assets_are_present(self):
        """素材在库里时正常返回（正向对照，证明上面的 null 不是工具坏了）。"""
        routes = {
            "/api/production/materials/search": [
                {"asset_ref": "asset:a1", "segment_ref": "segment:s1", "score": 0.9, "reasons": ["x"]},
            ],
            "/api/production/materials": self.ROUTES_FOREIGN["/api/production/materials"],
        }
        result = call("find_material_candidates", {"description": "城市夜景"}, routes)
        first = result["candidates"][0]
        assert first["file_name"] == "mine.mp4"
        assert result["evidence_complete"] is True
