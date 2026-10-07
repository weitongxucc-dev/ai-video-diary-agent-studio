from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.assistant_pi import pi_runtime_checks
from app.config import Settings
from app.model_runtime import (
    CHINESE_CLIP_VIT_B16,
    QWEN3_VL_4B_4BIT,
    ModelCatalog,
    ModelReadiness,
    ModelSpec,
)
from app.models import utc_now


def model_catalog_from_settings(settings: Settings) -> ModelCatalog:
    """Same wiring as the application's catalog, so health and runtime agree.

    ``main.py`` builds its own catalog from these same settings; it cannot pass
    that instance in without a change to a file outside this task's scope, so
    the wiring is repeated here and pinned by a test.
    """
    return ModelCatalog(
        settings.model_root_path,
        settings.model_runtime_python_path,
        settings.visual_model_worker_path,
        settings.voice_model_worker_path,
        understanding_runtime_python_path=settings.understanding_runtime_python_path,
        understanding_worker_path=settings.understanding_worker_path,
    )


# (feature key, spec, health item id, health label, impact when unavailable)
_LOCAL_MODEL_FEATURES: tuple[tuple[str, ModelSpec, str, str, str], ...] = (
    (
        "material-visual-understanding",
        QWEN3_VL_4B_4BIT,
        "local_vlm",
        "本地画面理解模型",
        "影响逐镜头画面描述、事实与时间证据、画面文字（OCR）检索；没有它时相关字段留空并标注未就绪，不会用推测内容填充。",
    ),
    (
        "material-visual-semantics",
        CHINESE_CLIP_VIT_B16,
        "local_visual_semantics",
        "本地画面语义模型",
        "影响按画面内容找候选镜头；没有它时只按素材文字与时长推荐，并在结果里写明语义未参与。",
    ),
)


def _feature_gates(
    settings: Settings,
    catalog: ModelCatalog,
    pi_checks: list[dict],
) -> list[dict[str, Any]]:
    ffmpeg_available = shutil.which("ffmpeg") is not None
    blockers = [check for check in pi_checks if check["status"] != "ready"]
    conversation_reason = " ".join(f"{c['reason']} {c['repair_action']}" for c in blockers)
    if not settings.llm_configured:
        conversation_reason += " 在设置页连接 MiniMax，或主动选择 DeepSeek。"
    gates = [
        {"feature": "assistant-conversation", "available": not blockers and settings.llm_configured,
         "reason": conversation_reason.strip()},
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
    for _feature, spec, _item_id, _label, _impact in _LOCAL_MODEL_FEATURES:
        readiness = catalog.readiness(spec)
        gates.append(
            {
                "feature": _feature,
                "available": readiness.available,
                "reason": "" if readiness.available else readiness.message,
            }
        )
    return gates


def runtime_health(settings: Settings) -> dict[str, Any]:
    checked_at = utc_now()
    catalog = model_catalog_from_settings(settings)
    pi_checks = pi_runtime_checks()
    items = [
        *(_health_item(
            item_id=c["id"], label=c["label"], status=c["status"],
            reason=c["reason"], repair_action=c["repair_action"],
            fix_command=c["fix_command"], checked_at=checked_at,
            impact="仅影响对话助手；不阻断首启、精确编辑或直接文字模型调用。",
        ) for c in pi_checks),
        _deepseek_item(settings, checked_at),
        _obsidian_item(settings, checked_at),
        _ffmpeg_item(checked_at),
        _local_whisper_item(settings, checked_at),
        *(
            _local_model_item(catalog, spec, item_id, label, impact, checked_at)
            for _feature, spec, item_id, label, impact in _LOCAL_MODEL_FEATURES
        ),
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
    gates = _feature_gates(settings, catalog, pi_checks)
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
    if settings.llm_configured:
        endpoint = urlsplit(settings.effective_llm_base_url)
        connection_note = "连接与实际生成尚未验证。"
        if endpoint.hostname in {"localhost", "127.0.0.1", "::1"}:
            try:
                port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
                with socket.create_connection((endpoint.hostname, port), timeout=0.5):
                    pass
            except (OSError, ValueError):
                return _health_item(
                    item_id="deepseek",
                    label="AI 文案模型",
                    status="degraded",
                    reason="已配置本地模型，但无法连接本机模型服务。",
                    impact="当前不能通过该模型生成选题或文案。",
                    repair_action="启动本地模型服务，并核对设置中的服务地址与端口。",
                    checked_at=checked_at,
                )
            connection_note = "本机服务端口可连接；模型加载与实际生成尚未验证。"
        return _health_item(
            item_id="deepseek",
            label="AI 文案模型",
            status="ready",
            reason=(
                f"已配置 {settings.effective_llm_provider} / "
                f"{settings.effective_llm_model}。{connection_note}"
            ),
            impact="用于生成选题、脚本和语音指令草案；配置检查不代表生成成功。",
            repair_action="如生成失败，请检查模型服务及返回的具体错误。",
            checked_at=checked_at,
        )
    return _health_item(
        item_id="deepseek",
        label="对话与文字模型",
        status="degraded",
        reason="未配置外部文字模型；本地来源摘要不能代替对话 Agent。",
        impact="0.11 对话和文字创作不可用；后台本地素材分析可独立运行。",
        repair_action="在设置页连接 MiniMax-M3 或主动选择 DeepSeek。",
        fix_command=None,
        checked_at=checked_at,
    )


def _obsidian_item(settings: Settings, checked_at) -> dict[str, Any]:
    path = Path(settings.obsidian_vault_path).expanduser() if settings.obsidian_vault_path else None
    fix_command = None
    if path and path.is_dir():
        # Desktop readiness must not walk a user's entire vault: a nested
        # cloud/permission-gated directory can block startup indefinitely.
        status = "ready"
        reason = "资料目录存在；具体内容在读取资料时检查。"
        repair_action = "无需处理。"
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
        settings.local_whisper_runtime_path
        and Path(settings.local_whisper_runtime_path).expanduser().is_file()
    )
    model_ok = bool(
        settings.local_whisper_model_path
        and Path(settings.local_whisper_model_path).expanduser().is_file()
    )
    fix_command = None
    if python_ok and model_ok:
        status = "ready"
        reason = "所选本地 Whisper 程序和模型可访问，尚需实际转写验证。"
        repair_action = "无需处理。"
    elif python_ok or model_ok:
        status = "invalid"
        reason = "所选本地 Whisper 程序或模型路径不完整。"
        repair_action = "在设置页配置所选运行方式的程序和对应模型文件。"
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


def _local_model_item(
    catalog: ModelCatalog,
    spec: ModelSpec,
    item_id: str,
    label: str,
    impact: str,
    checked_at,
) -> dict[str, Any]:
    """Map a model's readiness onto the four statuses this module already uses.

    The severity comes from the *weight* state, because that is the decision the
    user has to make: no weights is a download, a broken or half-present install
    is a reinstall, and weights that are present but unusable means the
    environment around them is at fault — repairable without spending another
    multi-gigabyte download. It is read as a field rather than matched against
    the Chinese label so that rewording a message can never silently drop a model
    into ``degraded``.
    """
    readiness: ModelReadiness = catalog.readiness(spec)
    status = readiness.severity
    return _health_item(
        item_id=item_id,
        label=f"{label}（{spec.display_name}）",
        status=status,
        reason=readiness.message,
        impact="无影响。" if readiness.available else impact,
        repair_action=readiness.repair_action,
        fix_command="在应用内「设置 → 模型管理」中下载" if status == "missing" else None,
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
