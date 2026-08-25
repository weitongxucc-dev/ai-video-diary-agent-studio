#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

MODEL_ID = "chinese-clip-vit-base-patch16"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).expanduser().resolve()
    response_path = Path(args.response).expanduser().resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    _validate_request(request)

    import torch
    from PIL import Image
    from transformers import ChineseCLIPModel, ChineseCLIPProcessor

    model_path = Path(request["model_path"]).expanduser().resolve()
    if not model_path.is_dir():
        raise FileNotFoundError("model directory does not exist")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = ChineseCLIPProcessor.from_pretrained(
        str(model_path),
        local_files_only=True,
    )
    model = ChineseCLIPModel.from_pretrained(
        str(model_path),
        local_files_only=True,
    ).to(device)
    model.eval()

    if request["mode"] == "image":
        video_path = Path(request["video_path"]).expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError("video file does not exist")
        embeddings: list[dict[str, object]] = []
        with TemporaryDirectory(prefix="ai_video_frames_") as temporary_dir:
            frame_root = Path(temporary_dir)
            for segment in request["segments"]:
                timestamps = _sample_timestamps(
                    float(segment["start_seconds"]),
                    float(segment["end_seconds"]),
                )
                images = []
                for index, timestamp in enumerate(timestamps):
                    frame_path = frame_root / f"{segment['segment_id']}_{index}.jpg"
                    _extract_frame(video_path, timestamp, frame_path)
                    with Image.open(frame_path) as frame:
                        images.append(frame.convert("RGB"))
                inputs = processor(images=images, return_tensors="pt")
                pixel_values = inputs["pixel_values"].to(device)
                with torch.inference_mode():
                    frame_features = _feature_tensor(
                        model.get_image_features(pixel_values=pixel_values)
                    )
                    frame_features = frame_features / frame_features.norm(
                        dim=-1,
                        keepdim=True,
                    ).clamp_min(1e-12)
                    vector = frame_features.mean(dim=0)
                    vector = vector / vector.norm().clamp_min(1e-12)
                embeddings.append(
                    {
                        "segment_id": segment["segment_id"],
                        "vector": vector.detach().cpu().float().tolist(),
                        "sample_count": len(images),
                    }
                )
        response = {
            "schema_version": 1,
            "model_id": MODEL_ID,
            "embeddings": embeddings,
        }
    else:
        inputs = processor(
            text=request["texts"],
            padding=True,
            return_tensors="pt",
        )
        input_ids = inputs["input_ids"].to(device)
        attention_mask = inputs["attention_mask"].to(device)
        with torch.inference_mode():
            text_features = _feature_tensor(
                model.get_text_features(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                )
            )
            text_features = text_features / text_features.norm(
                dim=-1,
                keepdim=True,
            ).clamp_min(1e-12)
        response = {
            "schema_version": 1,
            "model_id": MODEL_ID,
            "text_embeddings": text_features.detach().cpu().float().tolist(),
        }
    response_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = response_path.with_suffix(response_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(response, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, response_path)


def _feature_tensor(output):
    """Accept both Transformers 4 tensor and Transformers 5 model output APIs."""
    projected = getattr(output, "pooler_output", None)
    return projected if projected is not None else output


def _validate_request(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported request schema")
    if payload.get("model_id") != MODEL_ID:
        raise ValueError("unexpected model id")
    if not isinstance(payload.get("model_path"), str):
        raise ValueError("model path is required")  # noqa: TRY004 - request validation
    mode = payload.get("mode")
    if mode == "text":
        texts = payload.get("texts")
        if not isinstance(texts, list) or not texts:
            raise ValueError("texts are required")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("invalid text")
        return
    if mode != "image":
        raise ValueError("unsupported mode")
    if not isinstance(payload.get("video_path"), str):
        raise ValueError("video path is required")  # noqa: TRY004 - request validation
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments are required")
    seen: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("invalid segment")  # noqa: TRY004 - request validation
        segment_id = segment.get("segment_id")
        start = segment.get("start_seconds")
        end = segment.get("end_seconds")
        if not isinstance(segment_id, str) or not segment_id or segment_id in seen:
            raise ValueError("invalid segment id")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError("invalid segment timestamps")  # noqa: TRY004
        if start < 0 or end <= start:
            raise ValueError("invalid segment range")
        seen.add(segment_id)


def _sample_timestamps(start: float, end: float) -> list[float]:
    duration = end - start
    if duration < 1.5:
        return [round(start + duration * 0.5, 3)]
    return [
        round(start + duration * fraction, 3)
        for fraction in (0.25, 0.5, 0.75)
    ]


def _extract_frame(video_path: Path, timestamp: float, output_path: Path) -> None:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 or not output_path.is_file():
        detail = completed.stderr.strip() or "ffmpeg did not generate a frame"
        raise RuntimeError(detail)


if __name__ == "__main__":
    main()
