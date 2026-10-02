from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.config import Settings
from app.models import utc_now
from app.production import directory_accepts_writes


def setup_preflight(settings: Settings) -> dict:
    """Run a native setup preflight and return a stable consumable structure.

    Never leaks secrets or absolute sensitive content.  Reuses existing
    health detection where possible and distinguishes *not applicable*
    (e.g. optional Whisper when Tencent ASR is configured) from *missing*.
    """
    generated_at = utc_now()
    checks: list[dict] = [
        _deepseek_check(settings),
        _obsidian_check(settings),
        _video_inbox_check(settings),
        _video_output_check(settings),
        _jianying_check(settings),
        _ffmpeg_check(),
        _python_check(),
        _asr_check(settings),
    ]

    # Text input keeps the workbench usable without ASR, while Obsidian,
    # Jianying and remote model services are optional integrations. Only the
    # local video pipeline prerequisites block first-run completion.
    required = {"video_inbox", "video_output", "ffmpeg"}
    for check in checks:
        check["required"] = check["id"] in required

    required_ready = all(
        check["status"] == "ready"
        for check in checks
        if check["id"] in required
    )

    return {
        "overall": "ready" if required_ready else "blocked",
        "checks": checks,
        "generated_at": generated_at.isoformat(),
    }


def _preflight_item(
    *,
    item_id: str,
    label: str,
    status: str,
    summary: str,
    impact: str,
    repair_action: str,
    settings_target: str | None = None,
) -> dict:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "summary": summary,
        "impact": impact,
        "repair_action": repair_action,
        "settings_target": settings_target,
    }


# ── Individual checks ──────────────────────────────────────────────────


def _deepseek_check(settings: Settings) -> dict:
    if settings.llm_configured:
        return _preflight_item(
            item_id="deepseek",
            label="AI 文案模型",
            status="ready",
            summary=(
                f"配置已提供：{settings.effective_llm_provider} / "
                f"{settings.effective_llm_model}；远端可用性由 /api/runtime/health 验证"
            ),
            impact="AI 选题、脚本生成、语音指令草案均依赖此服务。",
            repair_action="无需处理。",
            settings_target="llm_api_key",
        )
    return _preflight_item(
        item_id="deepseek",
        label="对话与文字模型",
        status="degraded",
        summary="未配置外部文字模型；本地来源摘要不能代替对话",
        impact="0.11 对话和文字创作不可用；后台本地素材分析可独立运行。",
        repair_action="在设置页连接 MiniMax-M3 或主动选择 DeepSeek。",
        settings_target="llm_api_key",
    )


def _obsidian_check(settings: Settings) -> dict:
    if not settings.obsidian_vault_path:
        return _preflight_item(
            item_id="obsidian",
            label="Obsidian 知识库",
            status="degraded",
            summary="未配置 OBSIDIAN_VAULT_PATH",
            impact="无法摄入知识库笔记作为选题上下文。",
            repair_action="在设置页选择 Obsidian Vault 目录。",
            settings_target="obsidian_vault_path",
        )
    path = Path(settings.obsidian_vault_path).expanduser()
    if not path.is_dir():
        return _preflight_item(
            item_id="obsidian",
            label="Obsidian 知识库",
            status="invalid",
            summary="配置的目录不存在",
            impact="无法摄入知识库笔记作为选题上下文。",
            repair_action="在设置页重新选择存在的 Obsidian Vault 目录。",
            settings_target="obsidian_vault_path",
        )
    md_count = len(list(path.rglob("*.md")))
    if md_count == 0:
        return _preflight_item(
            item_id="obsidian",
            label="Obsidian 知识库",
            status="degraded",
            summary="目录可访问但未找到 .md 笔记",
            impact="知识库为空，选题将只依赖外部信息源。",
            repair_action="在 Obsidian 中创建笔记后刷新。",
            settings_target="obsidian_vault_path",
        )
    return _preflight_item(
        item_id="obsidian",
        label="Obsidian 知识库",
        status="ready",
        summary=f"已找到 {md_count} 篇笔记",
        impact="知识库可用，可摄入笔记作为上下文。",
        repair_action="无需处理。",
        settings_target="obsidian_vault_path",
    )


def _video_inbox_check(settings: Settings) -> dict:
    path = Path(settings.video_inbox_path).expanduser()
    if path.is_dir():
        # Listing a directory proves nothing about accepting the files the local
        # video pipeline writes into it, and a first run that reports ready here
        # only fails later, at the first import.
        if not directory_accepts_writes(path):
            return _preflight_item(
                item_id="video_inbox",
                label="视频待处理目录",
                status="invalid",
                summary="目录存在但当前不可写",
                impact="待处理视频无法进入该目录，扫描到也不会被处理。",
                repair_action=(
                    "检查该磁盘是否以只读方式挂载或目录权限是否被改动，"
                    "恢复可写后重新检查；或在设置页改选一个可写目录。"
                ),
                settings_target="video_inbox_path",
            )
        video_count = sum(
            1
            for f in path.iterdir()
            if f.is_file() and f.suffix.lower() in {
                ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
            }
        )
        return _preflight_item(
            item_id="video_inbox",
            label="视频待处理目录",
            status="ready",
            summary=f"目录存在且可写，当前 {video_count} 个待处理视频",
            impact="支持扫描和导入待录制/待处理视频。",
            repair_action="无需处理。" if video_count else "放置视频文件到该目录后自动识别。",
            settings_target="video_inbox_path",
        )
    return _preflight_item(
        item_id="video_inbox",
        label="视频待处理目录",
        status="invalid",
        summary="配置的目录不存在",
        impact="无法扫描待处理视频。",
        repair_action="在设置页选择或创建视频待处理目录。",
        settings_target="video_inbox_path",
    )


def _video_output_check(settings: Settings) -> dict:
    path = Path(settings.video_output_path).expanduser()
    if path.is_dir():
        # The render pipeline writes finished video, subtitles and material packs
        # here, so a directory that cannot be written to is not a usable output
        # directory even when it lists normally.
        if not directory_accepts_writes(path):
            return _preflight_item(
                item_id="video_output",
                label="视频输出目录",
                status="invalid",
                summary="目录存在但当前不可写",
                impact="处理后视频、字幕和素材无法写入此目录。",
                repair_action=(
                    "检查该磁盘是否以只读方式挂载或目录权限是否被改动，"
                    "恢复可写后重新检查；或在设置页改选一个可写目录。"
                ),
                settings_target="video_output_path",
            )
        return _preflight_item(
            item_id="video_output",
            label="视频输出目录",
            status="ready",
            summary="目录可写",
            impact="处理后视频、字幕和素材将写入此目录。",
            repair_action="无需处理。",
            settings_target="video_output_path",
        )
    return _preflight_item(
        item_id="video_output",
        label="视频输出目录",
        status="invalid",
        summary="配置的目录不存在",
        impact="处理后视频、字幕和材料无法写入。",
        repair_action="在设置页选择或创建视频输出目录。",
        settings_target="video_output_path",
    )


def _jianying_check(settings: Settings) -> dict:
    root = Path(settings.jianying_draft_root_path).expanduser()
    if not root.is_dir():
        return _preflight_item(
            item_id="jianying",
            label="剪映草稿目录",
            status="invalid",
            summary="配置的目录不存在",
            impact="无法将素材包导入剪映草稿箱。",
            repair_action="在设置页选择剪映草稿根目录（通常为 com.lveditor.draft）。",
            settings_target="jianying_draft_root_path",
        )
    encrypted = list(root.glob("*/crypto_key_store.dat"))
    if encrypted:
        return _preflight_item(
            item_id="jianying",
            label="剪映草稿目录",
            status="degraded",
            summary="检测到剪映 10.7+ 加密草稿，导入时以原生 draft_content.json 写入",
            impact="加密草稿无法直接拷贝；将以原生草稿格式写入，剪映会自动发现新草稿，已打开的剪映需刷新草稿箱。",
            repair_action="导入后在剪映中刷新草稿箱或人工验证。",
            settings_target="jianying_draft_root_path",
        )
    return _preflight_item(
        item_id="jianying",
        label="剪映草稿目录",
        status="ready",
        summary="目录可访问，草稿可直写",
        impact="素材包可直接写入剪映草稿目录。",
        repair_action="无需处理。",
        settings_target="jianying_draft_root_path",
    )


def _ffmpeg_check() -> dict:
    available = shutil.which("ffmpeg") is not None
    if available:
        return _preflight_item(
            item_id="ffmpeg",
            label="ffmpeg",
            status="ready",
            summary="ffmpeg 命令可用。",
            impact="视频预处理、音频抽取、静音检测、字幕和封面生成均需 ffmpeg。",
            repair_action="无需处理。",
        )
    return _preflight_item(
        item_id="ffmpeg",
        label="ffmpeg",
        status="missing",
        summary="未检测到 ffmpeg。",
        impact="视频预处理、音频抽取和封面生成无法进行。",
        repair_action="安装 ffmpeg（brew install ffmpeg）并确认可在终端中执行 ffmpeg。",
    )


def _python_check() -> dict:
    """Report system Python availability — always ready since the backend
    is running on it, but verify for completeness."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return _preflight_item(
        item_id="python",
        label="Python 运行时",
        status="ready",
        summary=f"Python {version} 可用",
        impact="后端进程、脚本执行和本地 Whisper 依赖此 Python。",
        repair_action="无需处理。",
    )


def _asr_check(settings: Settings) -> dict:
    whisper_python = bool(
        settings.local_whisper_runtime_path
        and Path(settings.local_whisper_runtime_path).expanduser().is_file()
    )
    whisper_model = bool(
        settings.local_whisper_model_path
        and Path(settings.local_whisper_model_path).expanduser().is_file()
    )
    whisper_ok = whisper_python and whisper_model
    whisper_partial = whisper_python or whisper_model

    if settings.asr_provider == "tencentcloud":
        return _preflight_item(
            item_id="asr", label="语音转写（ASR）",
            status="ready" if settings.asr_configured else "missing",
            summary="已主动选择腾讯云转写；凭据已配置，未验证远端调用。" if settings.asr_configured else "已选择腾讯云转写，但凭据不完整。",
            impact="转写时音频将发送到腾讯云；失败不会切换供应商。",
            repair_action="无需处理。" if settings.asr_configured else "补齐腾讯云凭据，或选择本地 Whisper。",
            settings_target="asr_provider",
        )

    if whisper_ok:
        return _preflight_item(
            item_id="asr",
            label="语音转写（ASR）",
            status="ready",
            summary="本地 Whisper 路径可访问，模型运行尚需实际转写验证",
            impact="语音日记转写将使用本地 Whisper 处理。",
            repair_action="无需处理。",
            settings_target="local_whisper_model_path",
        )

    if whisper_partial:
        return _preflight_item(
            item_id="asr",
            label="语音转写（ASR）",
            status="degraded",
            summary="本地 Whisper 配置不完整，无法使用转写功能",
            impact="语音日记无法转写，需完善本地 Whisper，不会自动外发音频。",
            repair_action="完善本地 Whisper 的运行程序和对应格式的模型路径。",
            settings_target="local_whisper_backend",
        )

    return _preflight_item(
        item_id="asr",
        label="语音转写（ASR）",
        status="missing",
        summary="当前选择本地 Whisper，但尚未配置。",
        impact="语音日记转写完全不可用。",
        repair_action="在设置页配置本地 Whisper 运行程序和模型。",
        settings_target="local_whisper_backend",
    )
