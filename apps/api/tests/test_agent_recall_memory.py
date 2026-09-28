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
