from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Literal

import PIL
from PIL import Image, ImageDraw, ImageFont

from app.artifact_store import ArtifactStore
from app.canvas import AspectRatio, canvas_size
from app.montage import _subtitle_font
from app.video import SUPPORTED_VIDEO_EXTENSIONS

CoverImageFormat = Literal["png", "jpeg"]

COVER_WIDTH = 1080
COVER_HEIGHT = 1920


class CoverGenerationError(OSError):
    """A failed ffmpeg/ffprobe boundary with preserved process evidence."""

    def __init__(self, *, tool: str, exit_code: int, stderr: str) -> None:
        self.tool = tool
        self.exit_code = exit_code
        self.stderr = stderr.strip() or "(no stderr produced)"
        super().__init__(f"{tool} exited with code {exit_code}: {self.stderr}")


@dataclass(frozen=True)
class CoverSafeArea:
    """Pixel margins inside the fixed 1080x1920 vertical cover canvas."""

    left: int = 72
    top: int = 144
    right: int = 72
    bottom: int = 180

    def validate(self, size=(COVER_WIDTH,COVER_HEIGHT)) -> None:
        values = (self.left, self.top, self.right, self.bottom)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("封面安全区边距必须是非负整数")
        if self.left + self.right > size[0] - 320:
            raise ValueError("封面安全区宽度不足")
        if self.top + self.bottom > size[1] - 320:
            raise ValueError("封面安全区高度不足")


@dataclass(frozen=True)
class CoverGenerationRequest:
    source_video_path: str | Path
    output_ref: str
    frame_time_seconds: float
    title: str
    safe_area: CoverSafeArea = field(default_factory=CoverSafeArea)
    aspect_ratio: AspectRatio = "9:16"


@dataclass(frozen=True)
class CoverGenerationEvidence:
    input_ref: str
    input_digest: str
    input_size_bytes: int
    input_duration_seconds: float
    input_width: int
    input_height: int
    engine: str
    engine_version: str
    layout_engine: str
    layout_engine_version: str
    font_family: str
    font_path: str
    parameters: dict[str, object]
    output_ref: str
    output_sha256: str
    output_size_bytes: int
    output_width: int
    output_height: int
    output_format: CoverImageFormat

    def as_dict(self) -> dict[str, object]:
        """Return JSON-compatible evidence for TransformRun persistence."""
        return asdict(self)


@dataclass(frozen=True)
class GeneratedCover:
    output_path: str
    evidence: CoverGenerationEvidence


@dataclass(frozen=True)
class ImageGenerationCapability:
    capability_state: Literal["unavailable"] = "unavailable"
    failure_reason: str = "ImageGenerationProvider 未配置"
    impact: str = "当前只能从成片画面生成确定性封面，不提供 AI 生图。"
    remediation: str = (
        "如需 AI 生图，先由产品集成并验真 ImageGenerationProvider、凭据、"
        "调用确认与产物校验。"
    )


def unconfigured_image_generation_capability() -> ImageGenerationCapability:
    """Describe the honest capability boundary without making a paid call."""
    return ImageGenerationCapability()


class CoverGenerationService:
    """Generate immutable frame-based covers below one controlled output root."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.artifact_store = ArtifactStore(self.output_root)

    def generate(self, request: CoverGenerationRequest) -> GeneratedCover:
        source = self._validated_source(request.source_video_path)
        output_format = self._validated_request(request)
        title = _normalized_title(request.title)
        size = canvas_size(request.aspect_ratio)
        request.safe_area.validate(size)
        source_summary = self._probe_source(source)
        timestamp = float(request.frame_time_seconds)
        if timestamp >= source_summary[0]:
            raise ValueError("封面选帧时间必须早于成片结束时间")

        input_sha256 = _sha256(source)
        input_size_bytes = source.stat().st_size
        engine_version = _tool_version("ffmpeg")

        with TemporaryDirectory(
            prefix=".cover-generation-",
            dir=self.output_root,
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            selected_frame = temporary_root / "selected-frame.png"
            composed_cover = temporary_root / f"cover.{output_format}"
            self._extract_frame(source, timestamp, selected_frame, size=size)
            font, font_path = _layout_cover(
                selected_frame,
                composed_cover,
                title=title,
                safe_area=request.safe_area,
                output_format=output_format,
                size=size,
            )
            _validate_cover_image(composed_cover, output_format, size=size)
            if _sha256(source) != input_sha256:
                raise OSError("封面生成期间源成片发生变化，未发布产物")
            publication = self.artifact_store.publish_file(
                request.output_ref,
                composed_cover,
            )

        output_path = self.artifact_store.resolve_file(request.output_ref)
        duration, input_width, input_height = source_summary
        font_family = " ".join(part for part in font.getname() if part).strip()
        evidence = CoverGenerationEvidence(
            input_ref=source.relative_to(self.output_root).as_posix(),
            input_digest=input_sha256,
            input_size_bytes=input_size_bytes,
            input_duration_seconds=round(duration, 6),
            input_width=input_width,
            input_height=input_height,
            engine="ffmpeg",
            engine_version=engine_version,
            layout_engine="Pillow",
            layout_engine_version=PIL.__version__,
            font_family=font_family,
            font_path=font_path,
            parameters={
                "frame_time_seconds": timestamp,
                "title": title,
                "safe_area": asdict(request.safe_area),
                "canvas": {"width": size[0], "height": size[1]},
                "scale_mode": "cover_center_crop",
                "title_layout": "bottom_center_dark_panel",
            },
            output_ref=publication.logical_ref,
            output_sha256=publication.sha256,
            output_size_bytes=publication.size_bytes,
            output_width=size[0],
            output_height=size[1],
            output_format=output_format,
        )
        return GeneratedCover(output_path=str(output_path), evidence=evidence)

    def _validated_source(self, raw_path: str | Path) -> Path:
        unresolved = Path(raw_path).expanduser()
        if unresolved.is_symlink():
            raise ValueError("封面源成片不能是符号链接")
        source = unresolved.resolve()
        try:
            source.relative_to(self.output_root)
        except ValueError as exc:
            raise ValueError("封面源成片必须位于受控任务输出目录内") from exc
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            raise ValueError("封面源成片不存在或格式不支持")
        return source

    @staticmethod
    def _validated_request(request: CoverGenerationRequest) -> CoverImageFormat:
        if (
            not isinstance(request.frame_time_seconds, (int, float))
            or isinstance(request.frame_time_seconds, bool)
            or not math.isfinite(request.frame_time_seconds)
            or request.frame_time_seconds < 0
        ):
            raise ValueError("封面选帧时间必须是非负有限数")
        if not request.output_ref or "\\" in request.output_ref:
            raise ValueError("封面产物引用必须是非空 POSIX 相对路径")
        relative = PurePosixPath(request.output_ref)
        if (
            relative.is_absolute()
            or relative.as_posix() != request.output_ref
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("封面产物引用必须位于受控输出目录内")
        suffix = relative.suffix.lower()
        if suffix == ".png":
            return "png"
        if suffix in {".jpg", ".jpeg"}:
            return "jpeg"
        raise ValueError("封面产物只支持 PNG 或 JPEG")

    @staticmethod
    def _probe_source(source: Path) -> tuple[float, int, int]:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "json",
            str(source),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise CoverGenerationError(
                tool="ffprobe",
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )
        try:
            payload = json.loads(completed.stdout)
            duration = float(payload["format"]["duration"])
            stream = payload["streams"][0]
            width = int(stream["width"])
            height = int(stream["height"])
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("ffprobe 未返回有效的视频流、时长或尺寸") from exc
        if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
            raise ValueError("ffprobe 返回的视频流、时长或尺寸无效")
        return duration, width, height

    @staticmethod
    def _extract_frame(source: Path, timestamp: float, output_path: Path, *, size=(COVER_WIDTH,COVER_HEIGHT)) -> None:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ss",
            f"{timestamp:.6f}",
            "-frames:v",
            "1",
            "-vf",
            (
                f"scale={size[0]}:{size[1]}:"
                "force_original_aspect_ratio=increase,"
                f"crop={size[0]}:{size[1]},setsar=1"
            ),
            str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            raise CoverGenerationError(
                tool="ffmpeg",
                exit_code=completed.returncode,
                stderr=completed.stderr,
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise OSError("ffmpeg 未产生非空封面选帧")


def _layout_cover(
    selected_frame: Path,
    output_path: Path,
    *,
    title: str,
    safe_area: CoverSafeArea,
    output_format: CoverImageFormat,
    size=(COVER_WIDTH,COVER_HEIGHT),
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, str]:
    with Image.open(selected_frame) as frame:
        canvas = frame.convert("RGBA")
    if canvas.size != size:
        raise ValueError("ffmpeg 选帧尺寸不符合当前画幅")

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    safe_left = safe_area.left
    safe_top = safe_area.top
    safe_right = size[0] - safe_area.right
    safe_bottom = size[1] - safe_area.bottom
    horizontal_padding = 44
    vertical_padding = 32
    max_text_width = safe_right - safe_left - 2 * horizontal_padding
    max_text_height = safe_bottom - safe_top - 2 * vertical_padding
    font, lines = _fit_title(title, max_text_width, max_text_height)
    raw_font_path = getattr(font, "path", None)
    if not isinstance(raw_font_path, (str, Path)):
        raise OSError("未找到可记录来源的中文字体，未生成封面")
    font_path = str(Path(raw_font_path).resolve())

    spacing = max(12, font.size // 5)
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
    widths = [box[2] - box[0] for box in boxes]
    heights = [box[3] - box[1] for box in boxes]
    text_height = sum(heights) + spacing * max(0, len(lines) - 1)
    panel_width = safe_right - safe_left
    panel_height = max(text_height + 2 * vertical_padding, min(260, safe_bottom - safe_top))
    panel_left = safe_left + (safe_right - safe_left - panel_width) / 2
    panel_top = safe_bottom - panel_height
    panel_right = panel_left + panel_width
    panel_bottom = safe_bottom
    if panel_top < safe_top:
        raise ValueError("封面标题无法放入指定安全区")
    draw.rounded_rectangle(
        (panel_left, panel_top, panel_right, panel_bottom),
        radius=28,
        fill=(24, 25, 23, 255),
    )
    cursor_y = panel_top + (panel_height - text_height) / 2
    for line, box, line_width, line_height in zip(
        lines,
        boxes,
        widths,
        heights,
        strict=True,
    ):
        draw.text(
            ((size[0] - line_width) / 2 - box[0], cursor_y - box[1]),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 230),
        )
        cursor_y += line_height + spacing
    composed = Image.alpha_composite(canvas, overlay)
    if output_format == "png":
        composed.save(output_path, format="PNG", optimize=False)
    else:
        composed.convert("RGB").save(
            output_path,
            format="JPEG",
            quality=92,
            subsampling=0,
        )
    return font, font_path


def _fit_title(
    title: str,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    measuring_image = Image.new("L", (1, 1))
    draw = ImageDraw.Draw(measuring_image)
    for size in range(96, 47, -4):
        font = _subtitle_font(size, family="PingFang SC" if sys.platform == "darwin" else None)
        lines = _wrap_title(draw, title, font, max_width)
        spacing = max(12, size // 5)
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in lines]
        height = sum(box[3] - box[1] for box in boxes) + spacing * max(0, len(lines) - 1)
        if len(lines) <= 3 and height <= max_height:
            return font, lines
    raise ValueError("封面标题过长，无法在安全区内排版")


def _wrap_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in title.split("\n"):
        box = draw.textbbox((0, 0), paragraph, font=font, stroke_width=2)
        paragraph_width = box[2] - box[0]
        line_count = max(1, math.ceil(paragraph_width / max_width))
        # Balance wrapped headings instead of leaving one or two characters orphaned.
        wrap_width = min(max_width, paragraph_width / line_count + font.size / 2)
        current = ""
        for character in paragraph:
            candidate = current + character
            box = draw.textbbox((0, 0), candidate, font=font, stroke_width=2)
            if current and box[2] - box[0] > wrap_width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def _normalized_title(raw_title: str) -> str:
    if not isinstance(raw_title, str):
        raise ValueError("封面标题必须是文本")
    lines = [" ".join(line.split()) for line in raw_title.splitlines()]
    title = "\n".join(line for line in lines if line)
    if not title:
        raise ValueError("封面标题不能为空")
    if len(title) > 120:
        raise ValueError("封面标题不能超过 120 个字符")
    return title


def _validate_cover_image(path: Path, expected_format: CoverImageFormat, *, size=(COVER_WIDTH,COVER_HEIGHT)) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise OSError("封面图片为空")
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        actual_format = (image.format or "").lower()
        if image.size != size:
            raise OSError("封面图片尺寸校验失败")
        if actual_format != expected_format:
            raise OSError("封面图片格式校验失败")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_version(tool: str) -> str:
    completed = subprocess.run(
        [tool, "-version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()[0].strip()
