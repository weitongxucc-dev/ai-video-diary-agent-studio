"""Rebuildable waveform peaks derived from an authorized audio artifact."""

from __future__ import annotations

import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryFile


class AudioWaveformError(RuntimeError):
    pass


def audio_waveform(path: Path, cache_root: Path) -> dict:
    # Include the source identity so regenerating audio cannot reuse stale peaks.
    try:
        stat = path.stat()
        identity = f"v1:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        cache_root.mkdir(parents=True, exist_ok=True)
        cache = cache_root / f"{key}.json"
        if cache.is_file():
            return json.loads(cache.read_text())
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=channels", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        if not streams or not 1 <= streams[0].get("channels", 0) <= 8:
            raise AudioWaveformError("音频缺少可读取的声道。")
        channels = streams[0]["channels"]
        sample_rate = 8000
        # Disk-backed PCM keeps long recordings out of application memory.
        with TemporaryFile(dir=cache_root) as pcm:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                 "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
                stdout=pcm, stderr=subprocess.PIPE, check=True, timeout=120,
            )
            frame_count = pcm.tell() // (2 * channels)
            if not frame_count:
                raise AudioWaveformError("音频没有可读取的采样。")
            frames_per_peak = max(80, math.ceil(frame_count / 4000))
            pcm.seek(0)
            peaks = []
            while chunk := pcm.read(frames_per_peak * channels * 2):
                samples = array.array("h", chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                # Preserve both channels: opposite phases must not become silence.
                peaks.append(round(max(abs(sample) for sample in samples) / 32768, 5))
        result = {
            "source_id": key,
            "duration_seconds": frame_count / sample_rate,
            "seconds_per_peak": frames_per_peak / sample_rate,
            "peaks": peaks,
        }
        with NamedTemporaryFile(mode="w", dir=cache_root, suffix=".json", delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(result, output)
                output.flush()
                temporary.replace(cache)
            finally:
                temporary.unlink(missing_ok=True)
        return result
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise AudioWaveformError("无法生成音频波形，请检查音频文件与本地 FFmpeg。") from exc
