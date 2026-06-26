from __future__ import annotations

from pathlib import Path
import shutil
from importlib import util as importlib_util
from collections.abc import Callable

from app.config import Settings


def same_style_workflow_status(settings: Settings) -> dict[str, object]:
    public_status = settings.public_status()
    video_status = public_status["video"]
    asr_status = public_status["asr"]
    llm_status = public_status["llm"]
    ffmpeg_ready = bool(video_status["ffmpeg_available"])
    asr_ready = bool(asr_status["configured"] or asr_status["local_whisper_configured"])
    llm_ready = bool(llm_status["configured"])
    yt_dlp_ready = yt_dlp_available()
    seedance_ready = bool(settings.seedance_api_key and settings.seedance_base_url)
    cosyvoice_ready = bool(settings.cosyvoice_base_url)
    heygem_ready = bool(settings.heygem_base_url)
    bgm_library_ready = Path(settings.bgm_library_path).expanduser().is_dir()
    auto_upload_ready = bool(settings.social_auto_upload_base_url)
    draft_path = Path(settings.draft_output_path).expanduser()
    draft_ready = draft_path.exists() and draft_path.is_dir()

    sections = [
        {
            "id": "script_generation",
            "label": "文案生成",
            "status": _status(all_ready=ffmpeg_ready and asr_ready and llm_ready, partial=ffmpeg_ready or asr_ready or llm_ready),
            "engine": "FFmpeg + ASR + DeepSeek",
            "module_name": "reference_video_importer",
            "description": "导入对标视频，抽音频、转写口播，并由大模型生成仿写文案。",
            "inputs": ["对标视频链接", "自有产品信息", "仿写模式"],
            "outputs": ["对标文案", "改写口播稿", "参考帧", "宫格参考图", "标题标签候选"],
            "primary_action": "提取对标文案",
        },
        {
            "id": "audio_generation",
            "label": "音频生成",
            "status": "ready" if cosyvoice_ready else "missing",
            "engine": "CosyVoice",
            "module_name": "cosyvoice_adapter",
            "description": "把改写后的口播稿合成为指定音色的 voice.wav，并保存音频任务 manifest。",
            "inputs": ["口播稿", "音色", "语速"],
            "outputs": ["voice.wav", "音频时长", "任务ID"],
            "primary_action": "生成音频",
        },
        {
            "id": "digital_human_video",
            "label": "视频生成",
            "status": "partial" if seedance_ready or heygem_ready else "missing",
            "engine": "HeyGem / Duix Avatar / Seedance",
            "module_name": "heygem_digital_human_job",
            "description": "可用数字人模型生成口播视频，也可用 Seedance 读取宫格参考图生成同款镜头视频。",
            "inputs": ["数字人模型或宫格参考图", "voice.wav 或分镜提示词", "输出比例"],
            "outputs": ["talking_head.mp4 或 seedance_video.mp4", "生成任务ID"],
            "primary_action": "生成视频",
        },
        {
            "id": "subtitle_generation",
            "label": "字幕生成",
            "status": _status(all_ready=ffmpeg_ready and asr_ready, partial=ffmpeg_ready or asr_ready),
            "engine": "ASR + SRT + FFmpeg",
            "module_name": "subtitle_artifact_writer",
            "description": "复用现有 ASR 和字幕产物链路生成 SRT/ASS 字幕。",
            "inputs": ["成片视频", "字幕样式"],
            "outputs": ["subtitle.srt", "字幕样式参数"],
            "primary_action": "生成字幕",
        },
        {
            "id": "bgm_generation",
            "label": "背景音乐生成",
            "status": "ready" if ffmpeg_ready and bgm_library_ready else ("partial" if ffmpeg_ready else "missing"),
            "engine": "BGM library + FFmpeg",
            "module_name": "bgm_mix_job",
            "description": "选择或生成 BGM，并按音量参数混入成片。",
            "inputs": ["成片视频", "BGM 策略", "音量"],
            "outputs": ["mixed_video.mp4", "BGM 使用记录"],
            "primary_action": "混入背景音乐",
        },
        {
            "id": "title_generation",
            "label": "标题生成",
            "status": "ready" if llm_ready else "missing",
            "engine": "DeepSeek",
            "module_name": "publish_copy_generator",
            "description": "根据成片内容生成标题、话题标签和平台发布文案。",
            "inputs": ["改写口播稿", "平台", "目标人群"],
            "outputs": ["标题候选", "标签", "发布文案"],
            "primary_action": "生成标题标签",
        },
        {
            "id": "cover_generation",
            "label": "封面制作",
            "status": "ready",
            "engine": "Pillow pixel template",
            "module_name": "cover_artifact_writer",
            "description": "复用现有封面模板生成可进入草稿包的封面图。",
            "inputs": ["标题", "副标题", "封面模板"],
            "outputs": ["cover.png", "封面文案"],
            "primary_action": "生成封面",
        },
        {
            "id": "publisher",
            "label": "视频发布",
            "status": "ready" if auto_upload_ready else "partial",
            "engine": "social-auto-upload",
            "module_name": "social_auto_upload_adapter",
            "description": "汇总视频、封面、标题和标签生成发布包；自动上传前保留人工确认。",
            "inputs": ["最终视频", "封面", "标题标签", "平台账号"],
            "outputs": ["发布包 manifest", "人工确认状态"],
            "primary_action": "生成发布包",
        },
    ]

    return {
        "title": "一键追爆智能体",
        "summary": "从对标视频链接生成仿写文案、音频、数字人口播视频、字幕、封面和发布素材。",
        "sections": sections,
        "capabilities": [
            {
                "id": "ffmpeg",
                "label": "FFmpeg",
                "status": "ready" if ffmpeg_ready else "missing",
                "detail": "用于抽音频、转码、字幕烧录和 BGM 混音。",
            },
            {
                "id": "yt_dlp",
                "label": "yt-dlp",
                "status": "ready" if yt_dlp_ready else "missing",
                "detail": "用于把抖音等外部视频链接下载到参考任务 workspace。",
            },
            {
                "id": "asr",
                "label": "ASR",
                "status": "ready" if asr_ready else "missing",
                "detail": "腾讯云 ASR 或本地 Whisper 用于提取对标口播和生成字幕。",
            },
            {
                "id": "llm",
                "label": "LLM",
                "status": "ready" if llm_ready else "missing",
                "detail": "DeepSeek 用于爆款结构拆解、仿写、标题和标签生成。",
            },
            {
                "id": "seedance",
                "label": "Seedance",
                "status": "ready" if seedance_ready else "missing",
                "detail": "用于读取宫格参考图并提交图生视频任务。",
            },
            {
                "id": "heygem",
                "label": "HeyGem / Duix",
                "status": "ready" if heygem_ready else "missing",
                "detail": "用于把 voice.wav 和数字人模型提交为口播视频任务。",
            },
            {
                "id": "cosyvoice",
                "label": "CosyVoice",
                "status": "ready" if cosyvoice_ready else "missing",
                "detail": "用于把同款脚本草案合成为 voice.wav。",
            },
            {
                "id": "draft_package",
                "label": "剪映草稿包",
                "status": "ready" if draft_ready else "partial",
                "detail": f"草稿素材目录：{draft_path}",
            },
            {
                "id": "bgm_library",
                "label": "BGM 素材库",
                "status": "ready" if bgm_library_ready else "missing",
                "detail": f"素材库目录：{Path(settings.bgm_library_path).expanduser()}",
            },
            {
                "id": "social_auto_upload",
                "label": "自动上传",
                "status": "ready" if auto_upload_ready else "missing",
                "detail": "配置 SOCIAL_AUTO_UPLOAD_BASE_URL 后可在人工确认时提交自动上传任务。",
            },
        ],
        "safety_rules": [
            "复刻镜头结构和表达节奏，不复制原视频原文案。",
            "默认替换为自有产品、自己的人设和授权素材。",
            "外部平台发布前保留人工确认。",
        ],
    }


def _status(*, all_ready: bool, partial: bool) -> str:
    if all_ready:
        return "ready"
    if partial:
        return "partial"
    return "missing"


def yt_dlp_available(
    *,
    executable_lookup: Callable[[str], str | None] = shutil.which,
    module_lookup: Callable[[str], bool] | None = None,
) -> bool:
    active_module_lookup = module_lookup or _python_module_available
    return executable_lookup("yt-dlp") is not None or active_module_lookup("yt_dlp")


def _python_module_available(module_name: str) -> bool:
    return importlib_util.find_spec(module_name) is not None
