#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

MODEL_ID = "qwen3-tts-12hz-0.6b-base-8bit"
LANGUAGE_CODES = {"zh": "chinese", "en": "english", "zh-en": "auto"}


def _log_mlx_memory(mx, phase: str) -> None:
    print(f"QWEN_MEMORY phase={phase} active_bytes={mx.get_active_memory()} "
          f"cache_bytes={mx.get_cache_memory()} peak_bytes={mx.get_peak_memory()}",
          file=sys.stderr, flush=True)


def main() -> None:
    print("QWEN_PHASE worker_started", file=sys.stderr, flush=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    _validate_request(request)

    print("QWEN_PHASE importing_runtime", file=sys.stderr, flush=True)
    import numpy as np
    import mlx.core as mx

    # Bound retained free buffers; active MLX memory still needs host admission.
    mx.set_cache_limit(0)
    _log_mlx_memory(mx, "cache_disabled")
    from mlx_audio.audio_io import write as audio_write
    from mlx_audio.tts.utils import load_model

    model_path = Path(request["model_path"]).expanduser().resolve()
    reference_path = Path(request["reference_audio_path"]).expanduser().resolve()
    output_path = Path(request["output_path"]).expanduser().resolve()
    if not model_path.is_dir():
        raise FileNotFoundError("model directory does not exist")
    if not reference_path.is_file() or reference_path.suffix.lower() != ".wav":
        raise FileNotFoundError("reference WAV does not exist")

    print("QWEN_PHASE loading_weights", file=sys.stderr, flush=True)
    model = load_model(str(model_path))
    print("QWEN_PHASE weights_loaded", file=sys.stderr, flush=True)
    _log_mlx_memory(mx, "weights_loaded")
    print("QWEN_PHASE inference_started", file=sys.stderr, flush=True)
    _log_mlx_memory(mx, "inference_started")
    results = _generate_with_pronunciation(model, request, reference_path)
    if not results:
        raise RuntimeError("model generated no audio")
    print("QWEN_PHASE inference_returned", file=sys.stderr, flush=True)
    _log_mlx_memory(mx, "inference_returned")
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
    print("QWEN_PHASE audio_written", file=sys.stderr, flush=True)
    _log_mlx_memory(mx, "audio_written")


def _validate_request(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported request schema")
    if not isinstance(payload.get("language"), str) or payload["language"] not in LANGUAGE_CODES:
        raise ValueError("language must be zh, en or zh-en")
    dictionary = payload.get("pronunciation_dictionary", [])
    if not isinstance(dictionary, list):
        raise ValueError("pronunciation_dictionary must be a list")
    for entry in dictionary:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(field), str) or not entry[field].strip()
            for field in ("id", "version", "term", "language", "target_pronunciation", "scope")
        ):
            raise ValueError("pronunciation dictionary entry is invalid")
        if entry["language"] not in LANGUAGE_CODES:
            raise ValueError("pronunciation dictionary language is invalid")
        if entry["scope"] not in {"workspace", "person", "task"}:
            raise ValueError("pronunciation dictionary scope is invalid")
        if not isinstance(entry.get("scope_reference", ""), str):
            raise ValueError("pronunciation dictionary scope is invalid")
    _resolved_pronunciation_rules(dictionary, payload["language"])
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


def _resolved_pronunciation_rules(dictionary: list[dict], language: str) -> dict[str, str]:
    scope_priority = {"workspace": 0, "person": 1, "task": 2}
    by_term: dict[str, list[dict]] = {}
    for entry in dictionary:
        if entry["language"] == language or entry["language"] == "zh-en":
            by_term.setdefault(entry["term"], []).append(entry)

    resolved: dict[str, str] = {}
    for term, candidates in by_term.items():
        targets_by_level: dict[tuple[str, str], set[str]] = {}
        for entry in candidates:
            level = (entry["language"], entry["scope"])
            targets_by_level.setdefault(level, set()).add(entry["target_pronunciation"])
        for (rule_language, scope), targets in targets_by_level.items():
            if len(targets) > 1:
                raise ValueError(
                    f"conflicting pronunciation rules for {term!r} ({rule_language}, {scope})"
                )
        strongest_scope = max(scope_priority[entry["scope"]] for entry in candidates)
        candidates = [entry for entry in candidates if scope_priority[entry["scope"]] == strongest_scope]
        exact_language = [entry for entry in candidates if entry["language"] == language]
        if exact_language:
            candidates = exact_language
        targets = {entry["target_pronunciation"] for entry in candidates}
        resolved[term] = next(iter(targets))
    return resolved


def _apply_pronunciation_dictionary(text: str, dictionary: list[dict], language: str) -> str:
    rules = _resolved_pronunciation_rules(dictionary, language)
    if not rules:
        return text
    alternatives = sorted(rules, key=lambda term: (-len(term), term))
    pattern = re.compile("|".join(re.escape(term) for term in alternatives))
    return pattern.sub(lambda match: rules[match.group(0)], text)


def _generate_with_pronunciation(model, request: dict, reference_path: Path) -> list:
    spoken_text = _apply_pronunciation_dictionary(
        request["text"], request["pronunciation_dictionary"], request["language"]
    )
    return list(
        model.generate(
            text=spoken_text,
            lang_code=LANGUAGE_CODES[request["language"]],
            ref_audio=str(reference_path),
            ref_text=request["reference_text"],
        )
    )


if __name__ == "__main__":
    main()
