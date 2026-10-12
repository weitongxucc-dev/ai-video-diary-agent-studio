"""「不要自动选用」只挡机器选片，不挡人工选片。

用户把素材标成 not_for_automation 之后，混剪计划、出镜占比自动补位、AI 替换意图
都不该再挑它；同时人工检索、手动插入/替换必须照旧能选它。这些都走真实路由和真实
仓储（隔离的临时 SQLite 与素材目录），不调模型。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import app.main as main_module
from app.montage import MontagePlanner
from app.production import (
    ContentUseState,
    MaterialAsset,
    MaterialSegment,
    ProductionProfile,
    ProductionRepository,
    material_allows_automation,
    material_allows_footage,
    utc_now,
)
from fastapi.testclient import TestClient

WORKSPACE = "workspace-gate"


def _asset(library: Path, name: str, *, use_state: ContentUseState = "available") -> MaterialAsset:
    """一条已审核、已分析的 B-roll 素材，带两个可用镜头。"""
    path = library / name
    path.write_bytes(b"video")
    now = utc_now()
    return MaterialAsset(
        id=f"asset_{name}",
        workspace_id=WORKSPACE,
        file_name=name,
        path=str(path),
        size_bytes=path.stat().st_size,
        modified_at=now,
        duration_seconds=24,
        usage_role="b_roll",
        review_status="approved",
        analysis_status="ready",
        use_state=use_state,
        visibility="shared",
        segments=[
            MaterialSegment(
                id=f"{name}_segment_{index}",
                asset_id=f"asset_{name}",
                start_seconds=float(index * 8),
                end_seconds=float((index + 1) * 8),
                description=f"演示镜头{index}",
                review_status="approved",
                usage_role="b_roll",
            )
            for index in range(3)
        ],
    )


def _task_plan(assets: list[MaterialAsset]):
    """走真实计划构建：机器从候选池里自己挑镜头。"""
    return MontagePlanner().build_for_creation_task(duration_target_seconds=90,
        workspace_id=WORKSPACE,
        task_id="task-gate",
        script=_script(),
        profile=ProductionProfile(workspace_id=WORKSPACE),
        assets=assets,
        media_reference_for_asset=lambda _asset: "workspace://source.mp4",
        narration_audio_path="narration://artifact/test",
        narration_duration_seconds=4,
    )


def _script():
    from app.production import CreationTaskScript, CreationTaskScriptSection

    return CreationTaskScript(
        id="script-gate",
        workspace_id=WORKSPACE,
        task_id="task-gate",
        run_id="run",
        topic_id="topic",
        title="自动选片闸门",
        language="zh-CN",
        selection_status="confirmed",
        sections=[
            CreationTaskScriptSection(
                id="section",
                title="演示",
                content="演示电脑操作。",
                visual_prompt="电脑打字画面",
                evidence_reference_ids=[],
            )
        ],
    )


def test_the_two_gates_differ_only_where_the_domain_says_they_should(tmp_path: Path) -> None:
    """自动判据是人工判据的真子集，且只在 not_for_automation 这一态上不同。"""
    library = tmp_path / "library"
    library.mkdir()
    expectations = {
        "available": True,
        "not_for_publish": True,
        "not_for_automation": False,
        "reference_only": False,
        "excluded": False,
    }
    for use_state, expected in expectations.items():
        asset = _asset(library, f"{use_state}.mp4", use_state=use_state)
        assert material_allows_automation(asset) is expected, use_state
        # 人工判据完全没动：只有「不使用」和「仅参考」挡住人工。
        assert material_allows_footage(asset) is (use_state not in {"excluded", "reference_only"}), use_state


def test_the_plan_never_picks_material_marked_not_for_automation(tmp_path: Path) -> None:
    """只剩这一条素材时，机器没有可用候选，必须报错而不是偷偷选它。"""
    library = tmp_path / "library"
    library.mkdir()
    blocked = _asset(library, "blocked.mp4", use_state="not_for_automation")

    with pytest.raises(ValueError, match="可用素材库"):
        _task_plan([blocked])

    # 恢复 available 后同一条素材立刻回到候选池，不需要别处再改一次。
    restored = _asset(library, "blocked.mp4", use_state="available")
    plan = _task_plan([restored])
    assert plan.clips
    assert {clip.asset_id for clip in plan.clips} == {restored.id}


def test_the_plan_prefers_the_machine_usable_material_over_the_excluded_one(tmp_path: Path) -> None:
    """两条都在库里时，计划只该用可自动选用的那条。"""
    library = tmp_path / "library"
    library.mkdir()
    usable = _asset(library, "usable.mp4", use_state="available")
    manual_only = _asset(library, "manual.mp4", use_state="not_for_automation")

    plan = _task_plan([usable, manual_only])

    assert {clip.asset_id for clip in plan.clips} == {usable.id}


def test_marking_the_state_does_not_remove_an_existing_timeline_reference(tmp_path: Path) -> None:
    """已经排进计划的镜头不被偷偷摘掉：判据只在选片时生效，不回写已有方案。"""
    library = tmp_path / "library"
    library.mkdir()
    asset = _asset(library, "kept.mp4")
    plan = _task_plan([asset])
    used_ids = {clip.asset_id for clip in plan.clips}

    after = replace(asset, use_state="not_for_automation")

    assert {clip.asset_id for clip in plan.clips} == used_ids
    # 同一条素材重新进自动候选池时已被排除，但人工判据仍认它。
    assert material_allows_footage(after) is True
    assert material_allows_automation(after) is False


@pytest.fixture
def gate_repo(tmp_path: Path):
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )
    asset = _asset(library, "manual.mp4", use_state="not_for_automation")
    repo._save_asset(asset)
    return repo, asset


def test_manual_search_still_finds_material_the_machine_may_not_pick(gate_repo) -> None:
    """人工检索照旧能搜到：这是「人工可用」与「自动不用」的分界线。"""
    repo, asset = gate_repo

    matches = repo.search_material_segments(WORKSPACE, usage_roles=["a_roll", "b_roll"])

    assert {match.asset_ref for match in matches} == {f"asset:{asset.id}"}


def test_manual_candidate_lists_keep_showing_it_while_machine_picks_do_not(tmp_path: Path, monkeypatch) -> None:
    """共享候选函数只在机器调用处收窄，手选列表原样。"""
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )
    manual_only = repo._save_asset(_asset(library, "manual.mp4", use_state="not_for_automation"))
    usable = repo._save_asset(_asset(library, "usable.mp4"))
    monkeypatch.setattr(main_module, "production_repo", repo)

    # 用真实计划构建产出的 plan/clip，不再另造一套夹具。
    plan = _task_plan([usable])
    clip = plan.clips[0]

    manual = main_module._creation_task_montage_candidates(WORKSPACE, plan, clip)
    automatic = main_module._creation_task_montage_candidates(WORKSPACE, plan, clip, automation_only=True)

    assert manual_only.id in {candidate.asset_id for candidate in manual}
    assert manual_only.id not in {candidate.asset_id for candidate in automatic}


def test_the_use_state_api_persists_and_the_gate_follows_it(tmp_path: Path, monkeypatch) -> None:
    """真实路由：改状态 → 落库 → 自动判据立刻生效，人工检索不受影响。"""
    library = tmp_path / "曜核AI IP一体机"
    library.mkdir()
    repo = ProductionRepository(
        tmp_path / "production.sqlite",
        library,
        duration_reader=lambda path: 1.0,
        scene_reader=lambda path, duration: [],
    )
    asset = repo._save_asset(_asset(library, "api.mp4"))
    monkeypatch.setattr(main_module, "production_repo", repo)
    monkeypatch.setattr(
        main_module,
        "current_workspace_id",
        lambda request: WORKSPACE,
    )
    client = TestClient(main_module.app)

    base = f"/api/production/materials/{asset.id}"
    assert material_allows_automation(repo.get_asset(WORKSPACE, asset.id)) is True

    response = client.put(base + "/use-state", json={"use_state": "not_for_automation"})
    assert response.status_code == 200, response.text
    assert response.json()["use_state"] == "not_for_automation"

    stored = repo.get_asset(WORKSPACE, asset.id)
    assert material_allows_automation(stored) is False
    assert material_allows_footage(stored) is True
    assert {match.asset_ref for match in repo.search_material_segments(WORKSPACE, usage_roles=["a_roll", "b_roll"])} == {f"asset:{asset.id}"}

    restored = client.put(base + "/use-state", json={"use_state": "available"})
    assert restored.status_code == 200, restored.text
    assert material_allows_automation(repo.get_asset(WORKSPACE, asset.id)) is True
