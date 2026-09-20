"""Local recognition timing mapped onto unchanged, confirmed subtitle text."""
import json
import math
import subprocess
from dataclasses import replace
from pathlib import Path
from app.subtitle_schedule import subtitle_schedule


def _spoken_text(text):
    return "".join(char.casefold() for char in text if char.isalnum())


def align_subtitle_track(track, result, duration_seconds):
    tokens = []
    segments = result.get("transcription")
    if not isinstance(segments, list):
        raise ValueError("本地字幕识别缺少时间数据")
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("tokens"), list):
            raise ValueError("本地字幕识别缺少词时间数据")
        for token in segment["tokens"]:
            if not isinstance(token, dict) or not isinstance(token.get("text"), str) or not isinstance(token.get("offsets"), dict):
                raise ValueError("本地字幕识别返回了无效词数据")
            if token.get("text", "").startswith("[_"):
                continue
            text = _spoken_text(token.get("text", ""))
            if not text:
                continue
            offsets = token.get("offsets", {})
            start, end = offsets.get("from"), offsets.get("to")
            probability = token.get("p")
            if (any(type(value) not in (int, float) or not math.isfinite(value)
                    for value in (start, end, probability))
                    or not 0 <= start < end <= duration_seconds * 1000 + 20
                    or probability < .5 or probability > 1):
                raise ValueError("语音识别时间或置信度不足，请手动检查字幕；原时间未改动")
            if tokens and start < tokens[-1][2]:
                raise ValueError("语音识别时间存在重叠，不能自动校准")
            tokens.append((text, start, end))
    cues = sorted(track.cues, key=lambda cue: cue.start_seconds)
    if not tokens or "".join(token[0] for token in tokens) != "".join(_spoken_text(cue.text) for cue in cues):
        raise ValueError("识别内容与当前字幕不一致，请先检查文字或配音；不会用识别结果覆盖字幕")
    starts, ends = {}, {}
    position = 0
    for text, start, end in tokens:
        starts[position] = start / 1000
        position += len(text)
        ends[position] = min(end / 1000, duration_seconds)
    aligned = []
    position = 0
    for cue in cues:
        end_position = position + len(_spoken_text(cue.text))
        if position not in starts or end_position not in ends:
            raise ValueError("字幕在识别词内部断开，请先合并断词字幕后再校准")
        aligned.append(replace(cue, start_seconds=starts[position], end_seconds=ends[end_position]))
        position = end_position
    return replace(track, cues=aligned, artifact_ref=None, transform_id=None)


def apply_aligned_timings(track, timings, duration_seconds):
    if [item["id"] for item in timings] != [cue.id for cue in sorted(track.cues, key=lambda cue: cue.start_seconds)]:
        raise ValueError("字幕条目已变化，请重新校准")
    changed = replace(track, cues=[replace(cue, start_seconds=item["start_seconds"], end_seconds=item["end_seconds"])
        for cue, item in zip(sorted(track.cues, key=lambda cue: cue.start_seconds), timings, strict=True)],
        artifact_ref=None, transform_id=None)
    subtitle_schedule([replace(changed, style={**changed.style, "enabled":True})], duration_seconds)
    return changed


def recognize_subtitle_timing(settings, audio_path, language, output_root):
    # This path intentionally never dispatches to the cloud ASR provider.
    if settings.local_whisper_backend != "whisper_cpp" or not settings.local_whisper_configured:
        raise ValueError("字幕校准需要已安装的本地 whisper.cpp 和模型，请在设置中配置")
    target = Path(output_root) / "recognition"
    command = [str(settings.local_whisper_cli_path), "-m", str(settings.local_whisper_model_path),
               "-f", str(audio_path), "-l", "en" if language == "en-US" else "zh",
               "-ojf", "-of", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=1800)
        result = json.loads(target.with_suffix(".json").read_text())
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise ValueError("本地字幕识别失败，请检查 whisper.cpp 程序与模型；未修改字幕") from exc
    if not isinstance(result, dict):
        raise ValueError("本地字幕识别返回了无效结果")
    return result
