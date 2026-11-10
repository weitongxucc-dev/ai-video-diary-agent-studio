"""Read actual Whisper alignment; never interpolate sentence time by text length."""
import json
import math

from app.media_observations import SubtitleSegment


def read_whisper_alignment(path, backend):
    data = json.loads(path.read_text(encoding="utf-8"))
    cpp = backend == "whisper_cpp"
    segments = data["transcription" if cpp else "segments"]
    if not isinstance(segments, list):
        raise ValueError("本地转写未返回实际分段")
    words = []
    for segment in segments:
        for unit in segment.get("tokens" if cpp else "words", []):
            text = unit["text" if cpp else "word"].strip()
            if not text or text.startswith("[_"):
                continue  # whisper.cpp control tokens are not spoken words.
            if cpp:
                offsets = unit.get("offsets")
                if offsets is None:
                    continue  # Explicitly unaligned token; do not invent timing.
                start, end = offsets["from"], offsets["to"]
            else:
                start, end = unit["start"] * 1000, unit["end"] * 1000
            if (type(start) not in {float, int} or type(end) not in {float, int}
                    or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start):
                raise ValueError("本地转写返回无效对齐时间")
            if end > start:
                words.append(SubtitleSegment(round(start), round(end), text))
    return words, ("token" if cpp else "word") if words else "segment"
