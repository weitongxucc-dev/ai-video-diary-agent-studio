"""Local silence candidates with speech-edge padding; never auto-apply cuts."""
import math
import re
import subprocess


def detect_silence_cuts(path, duration_seconds, offset_seconds=0):
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "silencedetect=noise=-40dB:d=0.45", "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=180)
    spans = []
    start = None
    for kind, value in re.findall(r"silence_(start|end):\s*([0-9.]+)", result.stderr):
        if kind == "start":
            start = float(value)
        elif start is not None:
            spans.append((start, min(float(value), duration_seconds)))
            start = None
    if start is not None:
        spans.append((start, duration_seconds))
    if sum(end-start for start,end in spans) >= duration_seconds-.1:
        raise ValueError("所选范围几乎全是静音，无法判断应保留的讲话；请手动指定剪辑范围。")
    cuts = []
    for start, end in spans:
        left = round(math.ceil((offset_seconds+start+.12)*30)/30*1000)
        right = round(math.floor((offset_seconds+end-.12)*30)/30*1000)
        if right-left >= 200:
            cuts.append([left,right])
    if not cuts:
        raise ValueError("没有检测到可安全缩短的静音候选；未改变时间线。")
    return cuts
