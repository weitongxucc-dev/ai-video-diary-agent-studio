from fastapi.testclient import TestClient

import app.main as main_module
from app.same_style_workflow import yt_dlp_available


def test_yt_dlp_available_accepts_python_module_fallback():
    assert yt_dlp_available(
        executable_lookup=lambda name: None,
        module_lookup=lambda name: True,
    )


def test_yt_dlp_available_is_false_when_executable_and_module_are_missing():
    assert not yt_dlp_available(
        executable_lookup=lambda name: None,
        module_lookup=lambda name: False,
    )


def test_same_style_workflow_exposes_chase_hot_sections():
    client = TestClient(main_module.app)

    response = client.get("/api/same-style/workflow")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "一键追爆智能体"
    assert [section["label"] for section in body["sections"]] == [
        "文案生成",
        "音频生成",
        "视频生成",
        "字幕生成",
        "背景音乐生成",
        "标题生成",
        "封面制作",
        "视频发布",
    ]
    assert body["sections"][0]["module_name"] == "reference_video_importer"
    assert "参考帧" in body["sections"][0]["outputs"]
    assert "宫格参考图" in body["sections"][0]["outputs"]
    assert body["sections"][1]["module_name"] == "cosyvoice_adapter"
    assert body["sections"][2]["module_name"] == "heygem_digital_human_job"
    assert "Seedance" in body["sections"][2]["engine"]
    assert body["sections"][-1]["module_name"] == "social_auto_upload_adapter"
    assert any(
        capability["id"] == "ffmpeg" and capability["status"] in {"ready", "missing"}
        for capability in body["capabilities"]
    )
    assert any(
        capability["id"] == "yt_dlp" and capability["status"] in {"ready", "missing"}
        for capability in body["capabilities"]
    )
    assert any(
        capability["id"] == "seedance" and capability["status"] in {"ready", "missing"}
        for capability in body["capabilities"]
    )
    assert any(
        capability["id"] == "cosyvoice" and capability["status"] in {"ready", "missing"}
        for capability in body["capabilities"]
    )
    assert any(
        capability["id"] == "heygem" and capability["status"] in {"ready", "missing"}
        for capability in body["capabilities"]
    )
    assert "复刻镜头结构和表达节奏，不复制原视频原文案。" in body["safety_rules"]
