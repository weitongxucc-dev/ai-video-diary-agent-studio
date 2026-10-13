"""Local, time-anchored speech evidence for the existing material repository."""

import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from app.asr import ASRConfigurationError, ASRTranscriptionError, LocalWhisperTranscriber
from app.media_observations import MaterialSpeechAnalysis, SubtitleSegment
from app.config import Settings


class LocalMaterialSpeechAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings
        # Material analysis never chooses a cloud transcriber from saved credentials.
        self.transcriber = LocalWhisperTranscriber(settings)
        self._model_identity: tuple | None = None
        self._model_id = ""

    def _current_model_id(self) -> str:
        path = Path(self.settings.local_whisper_model_path or "").expanduser()
        stat = path.stat()
        runtime = Path(self.settings.local_whisper_runtime_path or "").expanduser()
        runtime_stat = runtime.stat()
        identity = (str(path), stat.st_size, stat.st_mtime_ns, str(runtime), runtime_stat.st_size, runtime_stat.st_mtime_ns)
        if identity != self._model_identity:
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            with runtime.open("rb") as source:
                runtime_digest = hashlib.file_digest(source, "sha256").hexdigest()
            self._model_id = f"{self.settings.local_whisper_backend}:{digest}:{runtime_digest}:{self.settings.local_whisper_language}:v1"
            self._model_identity = identity
        return self._model_id

    def analyze(self, path: Path, duration: float, previous: MaterialSpeechAnalysis | None, source_fingerprint: str | None = None) -> MaterialSpeechAnalysis:
        fingerprint = ""
        model_id = ""
        try:
            if source_fingerprint is None:
                with path.open("rb") as source:
                    source_fingerprint = hashlib.file_digest(source, "sha256").hexdigest()
            fingerprint = source_fingerprint
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, check=True, timeout=15,
            )
            if not probe.stdout.strip():
                return MaterialSpeechAnalysis("ready", fingerprint, "ffprobe:no-audio:v1", message="原片没有音轨。")
            if not self.transcriber.configured:
                return MaterialSpeechAnalysis("blocked", fingerprint, message="本地转写未配置，未分析原片声音。")
            model_id = self._current_model_id()
            if previous and previous.status == "ready" and previous.source_fingerprint == fingerprint and previous.model_id == model_id:
                return previous
            temporary_root = self.settings.effective_temporary_root_path
            temporary_root.mkdir(exist_ok=True)
            with TemporaryDirectory(prefix="material-speech-", dir=temporary_root) as directory:
                audio = Path(directory) / "speech.wav"
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(audio)],
                    capture_output=True, check=True, timeout=120,
                )
                transcript = self.transcriber.transcribe(audio)
            duration_ms = round(duration * 1000)
            observations = [
                SubtitleSegment(max(0, item.start_ms), min(duration_ms, item.end_ms), item.text)
                for item in transcript.segments
                if min(duration_ms, item.end_ms) > max(0, item.start_ms)
            ]
            return MaterialSpeechAnalysis("ready", fingerprint, model_id, observations,
                                          "本地语音转写完成；时间为语音片段范围，文字可纠正。")
        except (OSError, subprocess.SubprocessError, ASRConfigurationError, ASRTranscriptionError):
            return MaterialSpeechAnalysis("failed", fingerprint, model_id,
                                          message="原片声音分析失败，请检查本地转写程序和音频；画面分析仍可继续。")


def speech_text_for_range(analysis: MaterialSpeechAnalysis | None, start: float, end: float) -> str:
    if analysis is None or analysis.status != "ready":
        return ""
    return " ".join(item.text for item in analysis.observations
                    if item.end_ms > start * 1000 and item.start_ms < end * 1000)
