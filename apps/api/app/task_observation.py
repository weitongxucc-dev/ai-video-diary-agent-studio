"""Bounded, local observations of registered task outputs, never source guesses.

The API owns artifact/identity/version resolution. This module only receives a
trusted file and a command runner holding the existing device grant. Its result
contains measurements and local text, not image bytes or host file paths.
"""
from __future__ import annotations

import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageStat

MAX_SAMPLES = 3
MAX_SAMPLE_EDGE = 1280
MAX_OBSERVATION_BYTES = 64 * 1024


def _sample(path: Path, timestamp: float, index: int) -> dict:
    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        stats = ImageStat.Stat(rgb)
        return {"id": f"frame-{index}", "timestamp_seconds": timestamp,
                "width": image.width, "height": image.height,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "pixel_mean_rgb": [round(value, 3) for value in stats.mean],
                "pixel_extrema_rgb": [list(value) for value in stats.extrema],
                "object_refs": [], "ocr_status": "unavailable", "ocr_text": []}


def observe_output(path: Path, *, sample_times: list[float], temporary_root: Path,
                   run_command, max_output_bytes: int, expected_size: tuple[int, int], ocr_worker: Path | None = None) -> dict:
    """Measure at most three actual frames under caller-supplied admission.

    ``run_command(command)`` must execute through the original device grant.
    OCR is only passed after a separately calibrated local execution envelope.
    """
    if len(sample_times) > MAX_SAMPLES or any(not math.isfinite(value) or value < 0 for value in sample_times):
        raise ValueError("观察采样须为最多三个非负有限时间")
    if len(set(sample_times)) != len(sample_times):
        raise ValueError("观察采样时间不得重复")
    temporary_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="task-observation-", dir=temporary_root) as directory:
        root = Path(directory)
        frames = []
        if path.suffix.lower() == ".png":
            if sample_times and sample_times != [0.0]:
                raise ValueError("PNG 来源仅能观察时间 0")
            with Image.open(path) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("登记图片不是单帧 PNG")
                if image.size != expected_size:
                    raise ValueError("PNG 实际尺寸与登记输出不一致")
                image.load()
                metadata = {"width": image.width, "height": image.height,
                            "duration_seconds": None, "video_codec": "png", "audio_stream_count": 0}
                image.thumbnail((MAX_SAMPLE_EDGE, MAX_SAMPLE_EDGE))
                frame_path = root / "frame-0.png"
                encoded = BytesIO()
                image.save(encoded, format="PNG")
                if encoded.tell() > max_output_bytes:
                    raise ValueError("PNG 有限采样超过本次设备许可输出上限")
                frame_path.write_bytes(encoded.getvalue())
            frames.append((frame_path, 0.0))
        elif path.suffix.lower() == ".mp4":
            probe = run_command(["ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,duration", "-of", "json", str(path)])
            probe = json.loads(probe.stdout)
            streams = probe.get("streams", [])
            videos = [item for item in streams if item.get("codec_type") == "video"]
            if len(videos) != 1:
                raise ValueError("观察输出必须有一条实际视频流")
            video, = videos
            if (int(video["width"]), int(video["height"])) != expected_size:
                raise ValueError("实际视频尺寸与登记输出不一致")
            duration = float(video.get("duration") or probe.get("format", {}).get("duration", 0))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("实际视频时长不可验证")
            metadata = {"width": int(video["width"]), "height": int(video["height"]),
                        "duration_seconds": duration, "video_codec": video["codec_name"],
                        "audio_stream_count": sum(item.get("codec_type") == "audio" for item in streams)}
            times = sample_times or [round(duration * fraction, 6) for fraction in (.15, .5, .85)]
            if any(timestamp >= duration for timestamp in times):
                raise ValueError("采样时间必须位于实际输出时长内")
            for index, timestamp in enumerate(times):
                frame_path = root / f"frame-{index}.png"
                run_command(["ffmpeg", "-v", "error", "-threads", "1", "-ss", str(timestamp),
                    "-i", str(path), "-frames:v", "1", "-vf",
                    f"scale=w='min({MAX_SAMPLE_EDGE},iw)':h='min({MAX_SAMPLE_EDGE},ih)':force_original_aspect_ratio=decrease",
                    "-threads", "1", str(frame_path)], output_paths=[frame for frame, _ in frames] + [frame_path])
                frames.append((frame_path, timestamp))
        else:
            raise ValueError("本轮实际画面观察只支持登记 MP4 或 PNG")
        if sum(frame.stat().st_size for frame, _ in frames) > max_output_bytes:
            raise ValueError("有限采样帧超过本次设备许可输出上限")
        samples = [_sample(frame, timestamp, index) for index, (frame, timestamp) in enumerate(frames)]
        unavailable = [{"scope": "visual_semantics", "reason": "本地合成画面的语义理解尚无受许可执行能力；像素测量不代表看懂内容。"},
                       {"scope": "speech_and_listening", "reason": "本次只观察实际画面，没有转写或听感检查。"}]
        if ocr_worker is None:
            unavailable.append({"scope": "ocr", "reason": "本地 OCR 未配置或输出观察执行范围尚未标定；未提取文字。"})
            ocr_model_id = None
        else:
            from app.visual_understanding import validate_ocr_frames
            requested = [{"id": sample["id"], "path": str(frame), "timestamp_seconds": timestamp}
                         for sample, (frame, timestamp) in zip(samples, frames, strict=True)]
            request_path, response_path = root / "ocr-request.json", root / "ocr-response.json"
            request_path.write_text(json.dumps({"frames": requested}), encoding="utf-8")
            run_command([str(ocr_worker), "--request", str(request_path), "--response", str(response_path)],
                        output_paths=[frame for frame, _ in frames] + [response_path])
            if not response_path.is_file() or response_path.stat().st_size > MAX_OBSERVATION_BYTES:
                raise ValueError("本地 OCR 响应缺失或超过有界大小")
            ocr_model_id, ocr_frames = validate_ocr_frames(json.loads(response_path.read_bytes()), requested)
            for sample, frame in zip(samples, ocr_frames, strict=True):
                observations = frame["observations"]
                sample.update(ocr_status="ready", ocr_text=[{**item, "text": item["text"][:200]}
                    for item in observations[:50]], ocr_truncated_count=max(0, len(observations) - 50))
        result = {"measurement_scope": "actual_output_bounded_frames", "media": metadata,
                  "samples": samples, "ocr_model_id": ocr_model_id, "unavailable": unavailable}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OBSERVATION_BYTES:
            raise ValueError("观察文字结果超过有界大小")
        return result
