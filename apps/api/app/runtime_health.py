from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import utc_now


def _feature_gates(settings: Settings) -> list[dict[str, Any]]:
    ffmpeg_available = shutil.which("ffmpeg") is not None
    return [
        {
            "feature": "generate-topics",
            "available": True,
            "reason": "",
        },
        {
            "feature": "generate-script",
            "available": True,
            "reason": "",
        },
        {
            "feature": "regenerate-section",
            "available": True,
            "reason": "",
        },
        {
            "feature": "regenerate-script",
            "available": True,
            "reason": "",
        },
        {
            "feature": "process-video",
            "available": ffmpeg_available,
            "reason": "ffmpeg 未安装，请运行 brew install ffmpeg"
            if not ffmpeg_available
            else "",
        },
    ]


def runtime_health(settings: Settings) -> dict[str, Any]:
    checked_at = utc_now()
    items = [
        _deepseek_item(settings, checked_at),
        _obsidian_item(settings, checked_at),
        _ffmpeg_item(checked_at),
        _local_whisper_item(settings, checked_at),
        _video_directory_item(
            "video_inbox",
            "视频待处理目录",
            settings.video_inbox_path,
            "视频处理页无法扫描待处理视频。",
            "在设置页选择一个存在的待处理视频目录。",
            checked_at,
            fix_command=f"mkdir -p {settings.video_inbox_path}",
        ),
        _video_directory_item(
            "video_output",
            "视频输出目录",
            settings.video_output_path,
            "处理后视频、剪辑计划和字幕无法写入。",
            "在设置页选择一个存在且可写的输出目录。",
            checked_at,
            fix_command=f"mkdir -p {settings.video_output_path}",
        ),
        _video_directory_item(
            "draft_output",
            "剪映素材包目录",
            settings.draft_output_path,
            "无法生成剪映素材包和 manifest。",
            "在设置页选择一个存在且可写的素材包目录。",
            checked_at,
            fix_command=f"mkdir -p {settings.draft_output_path}",
        ),
        _jianying_item(settings, checked_at),
        _external_sources_item(settings, checked_at),
    ]
    counts = {"ready": 0, "missing": 0, "degraded": 0, "invalid": 0}
    for item in items:
        counts[str(item["status"])] += 1
    gates = _feature_gates(settings)
    return {
        "summary": {
            "total": len(items),
            "ready": counts["ready"],
            "missing": counts["missing"],
            "degraded": counts["degraded"],
            "invalid": counts["invalid"],
        },
        "items": items,
        "feature_gates": gates,
    }


def _health_item(
    *,
    item_id: str,
    label: str,
    status: str,
    reason: str,
    impact: str,
    repair_action: str,
    checked_at,
    fix_command: str | None = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "reason": reason,
        "impact": impact,
        "repair_action": repair_action,
        "fix_command": fix_command,
        "last_checked_at": checked_at,
    }


def _deepseek_item(settings: Settings, checked_at) -> dict[str, Any]:
    if settings.deepseek_api_key:
        return _health_item(
            item_id="deepseek",
            label="DeepSeek",
            status="ready",
            reason=f"已配置模型 {settings.deepseek_model}",
            impact="可生成选题、脚本和语音指令草案。",
            repair_action="无需处理。",
            checked_at=checked_at,
        )
    return _health_item(
        item_id="deepseek",
        label="云端文案增强",
        status="degraded",
        reason="未配置 DeepSeek，当前使用本地来源摘要生成器。",
        impact="本地闭环可用，但文案的表达丰富度较低。",
        repair_action="可选：在设置页填写自有 DeepSeek API Key 开启云端增强。",
        fix_command=None,
        checked_at=checked_at,
    )


def _obsidian_item(settings: Settings, checked_at) -> dict[str, Any]:
    path = Path(settings.obsidian_vault_path).expanduser() if settings.obsidian_vault_path else None
    fix_command = None
    if path and path.is_dir():
        markdown_count = len(list(path.rglob("*.md")))
        status = "ready" if markdown_count else "degraded"
        reason = f"已找到 {markdown_count} 篇 Markdown 笔记。"
        repair_action = "无需处理。" if markdown_count else "放入笔记或重新选择知识库目录。"
    else:
        status = "missing"
        reason = "Obsidian Vault 路径不存在。"
        repair_action = "在设置页选择真实存在的 Obsidian Vault。"
        fix_command = "在 Obsidian 中打开你的知识库查看路径"
    return _health_item(
        item_id="obsidian",
        label="Obsidian",
        status=status,
        reason=reason,
        impact="影响知识库摄入、选题素材和偏好上下文。",
        repair_action=repair_action,
        fix_command=fix_command,
        checked_at=checked_at,
    )


def _ffmpeg_item(checked_at) -> dict[str, Any]:
    available = shutil.which("ffmpeg") is not None
    return _health_item(
        item_id="ffmpeg",
        label="ffmpeg",
        status="ready" if available else "missing",
        reason="命令可用。" if available else "未在 PATH 中找到 ffmpeg。",
        impact="影响视频预处理、音频抽取、静音检测、字幕和封面生成。",
        repair_action="无需处理。" if available else "安装 ffmpeg 并确认终端 PATH 可访问。",
        fix_command=None if available else "brew install ffmpeg",
        checked_at=checked_at,
    )


def _local_whisper_item(settings: Settings, checked_at) -> dict[str, Any]:
    python_ok = bool(
        settings.local_whisper_python_path
        and Path(settings.local_whisper_python_path).expanduser().is_file()
    )
    model_ok = bool(
        settings.local_whisper_model_path
        and Path(settings.local_whisper_model_path).expanduser().is_file()
    )
    fix_command = None
    if python_ok and model_ok:
        status = "ready"
        reason = "本地 Whisper Python 和模型均可访问。"
        repair_action = "无需处理。"
    elif python_ok or model_ok:
        status = "invalid"
        reason = "本地 Whisper Python 或模型路径不完整。"
        repair_action = "在设置页同时配置 Python 路径和模型文件路径。"
    else:
        status = "missing"
        reason = "未配置本地 Whisper。"
        repair_action = "配置本地 Whisper，或改用腾讯云 ASR。"
        fix_command = "pip install openai-whisper"
    return _health_item(
        item_id="local_whisper",
        label="本地 Whisper",
        status=status,
        reason=reason,
        impact="影响本地转写、重复录制检测和字幕对齐。",
        repair_action=repair_action,
        fix_command=fix_command,
        checked_at=checked_at,
    )


def _video_directory_item(
    item_id: str,
    label: str,
    path_value: str,
    impact: str,
    repair_action: str,
    checked_at,
    fix_command: str | None = None,
) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    ready = path.exists() and path.is_dir()
    return _health_item(
        item_id=item_id,
        label=label,
        status="ready" if ready else "missing",
        reason=f"目录存在：{path}" if ready else f"目录不存在：{path}",
        impact=impact,
        repair_action="无需处理。" if ready else repair_action,
        fix_command=None if ready else fix_command,
        checked_at=checked_at,
    )


def _jianying_item(settings: Settings, checked_at) -> dict[str, Any]:
    root = Path(settings.jianying_draft_root_path).expanduser()
    fix_command = None
    if not root.exists() or not root.is_dir():
        status = "missing"
        reason = f"剪映草稿目录不存在：{root}"
        repair_action = "在设置页选择剪映草稿根目录。"
        fix_command = "在剪映中创建至少一个草稿后使用"
    else:
        encrypted_markers = list(root.glob("*/crypto_key_store.dat"))
        status = "degraded" if encrypted_markers else "ready"
        reason = "检测到剪映 10.7 加密草稿，使用兼容桥导入。" if encrypted_markers else "草稿目录可访问。"
        repair_action = "导入后打开剪映人工验证。" if encrypted_markers else "无需处理。"
    return _health_item(
        item_id="jianying",
        label="剪映草稿目录",
        status=status,
        reason=reason,
        impact="影响自动导入剪映草稿箱。",
        repair_action=repair_action,
        fix_command=fix_command,
        checked_at=checked_at,
    )


def _external_sources_item(settings: Settings, checked_at) -> dict[str, Any]:
    has_sources = bool(settings.topic_source_urls or settings.product_hunt_token or settings.hermes_radar_path)
    return _health_item(
        item_id="external_sources",
        label="外部信息源",
        status="ready" if has_sources else "missing",
        reason="已配置外部信息源。" if has_sources else "没有配置 Product Hunt、Hermes 或手动 URL。",
        impact="影响 AI 产品趋势素材和工具测评选题。",
        repair_action="无需处理。" if has_sources else "在设置页配置 Product Hunt Token、Hermes 目录或外链 URL。",
        fix_command=None if has_sources else "在 https://www.producthunt.com 注册获取 API Token",
        checked_at=checked_at,
    )
