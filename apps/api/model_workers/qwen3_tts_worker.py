#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


MODEL_ID = "qwen3-tts-12hz-0.6b-base-8bit"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    _validate_request(request)

    import numpy as np
    from mlx_audio.audio_io import write as audio_write
    from mlx_audio.tts.utils import load_model

    model_path = Path(request["model_path"]).expanduser().resolve()
    reference_path = Path(request["reference_audio_path"]).expanduser().resolve()
    output_path = Path(request["output_path"]).expanduser().resolve()
    if not model_path.is_dir():
        raise FileNotFoundError("model directory does not exist")
    if not reference_path.is_file() or reference_path.suffix.lower() != ".wav":
        raise FileNotFoundError("reference WAV does not exist")

    model = load_model(str(model_path))
    results = list(
        model.generate(
            text=request["text"],
            ref_audio=str(reference_path),
            ref_text=request["reference_text"],
        )
    )
    if not results:
        raise RuntimeError("model generated no audio")
    sample_rates = {int(result.sample_rate) for result in results}
    if len(sample_rates) != 1:
        raise RuntimeError("model returned inconsistent sample rates")
    sample_rate = sample_rates.pop()
    audio = np.concatenate([np.asarray(result.audio) for result in results])
    if audio.size == 0:
        raise RuntimeError("model generated empty audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_audio = output_path.with_suffix(".tmp.wav")
    audio_write(str(temporary_audio), audio, sample_rate)
    os.replace(temporary_audio, output_path)

    response_path = Path(args.response).expanduser().resolve()
    response_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "audio_path": str(output_path),
        "duration_seconds": round(float(audio.size) / sample_rate, 3),
        "sample_rate": sample_rate,
    }
    temporary_response = response_path.with_suffix(response_path.suffix + ".tmp")
    temporary_response.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary_response, response_path)


def _validate_request(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported request schema")
    if payload.get("model_id") != MODEL_ID:
        raise ValueError("unexpected model id")
    for key in (
        "model_path",
        "text",
        "reference_audio_path",
        "reference_text",
        "output_path",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required")
    if Path(payload["output_path"]).suffix.lower() != ".wav":
        raise ValueError("output must be WAV")


if __name__ == "__main__":
    main()
