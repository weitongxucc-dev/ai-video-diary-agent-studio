"""Rebuildable waveform peaks derived from an authorized audio artifact."""

from __future__ import annotations

import array
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory


class AudioWaveformError(RuntimeError):
    pass


def audio_waveform(path: Path, cache_root: Path, *, context=None) -> dict:
    if context is None:
        raise PermissionError("波形解码必须提供设备许可")
    context.check()
    # Include the source identity so regenerating audio cannot reuse stale peaks.
    try:
        stat = path.stat()
        identity = f"v1:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        cache_root.mkdir(parents=True, exist_ok=True)
        cache = cache_root / f"{key}.json"
        from app.audio_timeline import probe_audio_metadata
        measured = probe_audio_metadata(path, scheduler=context.scheduler, grant=context.grant,
            cancel_event=context.cancel_event, deadline_monotonic=context.deadline_monotonic)
        if cache.is_file():
            result = json.loads(cache.read_text())
            if abs(result["duration_seconds"] - measured["duration_seconds"]) > .25:
                raise AudioWaveformError("已保存波形与当前音频规格不符。")
            return result
        channels = measured["channels"]
        sample_rate = 8000
        with context.scheduler._connect() as conn:
            resources = json.loads(conn.execute("SELECT resources_json FROM device_work_requests WHERE id=?",
                                                (context.grant.request_id,)).fetchone()["resources_json"])
        if (measured["duration_seconds"] > resources["output_duration_limit_seconds"]
                or sample_rate * channels * 2 * measured["duration_seconds"] > context.max_output_bytes):
            raise AudioWaveformError("实际波形解码规格超过本次输出预算，未启动解码。")
        # Disk-backed PCM keeps long recordings out of application memory.
        with TemporaryDirectory(dir=cache_root) as directory:
            pcm_path = Path(directory) / "waveform.pcm"
            command = ["ffmpeg", "-v", "error", "-filter_threads", str(context.threads), "-threads", str(context.threads), "-i", str(path),
                "-map", "0:a:0", "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-threads:a", str(context.threads),
                "-f", "s16le", "-fs", str(context.max_output_bytes), str(pcm_path)]
            decoded = context.run(command, output_paths=(pcm_path,))
            if decoded.returncode:
                raise subprocess.CalledProcessError(decoded.returncode, command, stderr=decoded.stderr)
            pcm = pcm_path.open("rb")
            pcm.seek(0, 2)
            frame_count = pcm.tell() // (2 * channels)
            if not frame_count or abs(frame_count / sample_rate - measured["duration_seconds"]) > .25:
                pcm.close()
                raise AudioWaveformError("波形解码未覆盖完整音频，未发布截断结果。")
            frames_per_peak = max(80, math.ceil(frame_count / 4000))
            pcm.seek(0)
            peaks = []
            while chunk := pcm.read(frames_per_peak * channels * 2):
                samples = array.array("h", chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                # Preserve both channels: opposite phases must not become silence.
                peaks.append(round(max(abs(sample) for sample in samples) / 32768, 5))
            pcm.close()
        context.check()
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
