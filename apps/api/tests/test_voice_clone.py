import json
from datetime import date
from pathlib import Path

import pytest

from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection
from app.narration import NarrationGenerator
from app.production import ProductionProfile
from app.voice_clone import (
    Qwen3TTSSubprocessProvider,
    VoiceCloneError,
    VoiceCloneResult,
)


def _provider(tmp_path: Path, responder):
    runtime_python = tmp_path / "python"
    worker = tmp_path / "worker.py"
    model = tmp_path / "model"
    runtime_python.write_text("python", encoding="utf-8")
    worker.write_text("worker", encoding="utf-8")
    model.mkdir()
    (model / "manifest.json").write_text("{}", encoding="utf-8")

    def runner(command):
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        responder(json.loads(request_path.read_text(encoding="utf-8")), response_path)

    return Qwen3TTSSubprocessProvider(
        runtime_python_path=runtime_python,
        worker_path=worker,
        model_path=model,
        runner=runner,
    )


def test_voice_provider_requires_wav_and_validates_worker_result(tmp_path):
    def responder(request, response_path):
        output = Path(request["output_path"])
        output.write_bytes(b"wav")
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "qwen3-tts-12hz-0.6b-base-8bit",
                    "audio_path": str(output),
                    "duration_seconds": 12.5,
                    "sample_rate": 24000,
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"wav")
    result = provider.generate(
        text="这是一段自动配音。",
        reference_audio_path=reference,
        reference_text="这是参考文字。",
        output_path=tmp_path / "output" / "narration.wav",
    )

    assert result.duration_seconds == 12.5
    assert Path(result.audio_path).read_bytes() == b"wav"

    invalid_reference = tmp_path / "reference.mp3"
    invalid_reference.write_bytes(b"mp3")
    with pytest.raises(VoiceCloneError, match="WAV"):
        provider.generate(
            text="文案",
            reference_audio_path=invalid_reference,
            reference_text="参考文字",
            output_path=tmp_path / "invalid.wav",
        )


def test_narration_generator_enforces_consent_and_uses_safe_output_path(tmp_path):
    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, *, text, reference_audio_path, reference_text, output_path):
            assert "自动配音" in text
            assert reference_text == "本人授权的参考文字"
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"audio")
            return VoiceCloneResult(str(output), 8.0, 24000)

    episode = Episode(
        id="episode/unsafe",
        day_number=1,
        episode_date=date(2026, 8, 24),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage(
            id="script_1",
            hook="自动配音开始。",
            sections=[
                ScriptSection(
                    id="section_1",
                    title="正文",
                    estimated_seconds=8,
                    content="这是正文。",
                )
            ],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"wav")
    profile = ProductionProfile(
        workspace_id="workspace_a",
        voice_reference_path=str(reference),
        voice_reference_text="本人授权的参考文字",
        voice_consent_confirmed=True,
    )

    artifact = NarrationGenerator(tmp_path / "outputs", Provider()).generate(
        workspace_id="workspace_a",
        episode=episode,
        profile=profile,
    )

    assert artifact.audio_path.endswith("workspace_a/episode_unsafe.wav")
    assert artifact.duration_seconds == 8.0

    profile.voice_consent_confirmed = False
    with pytest.raises(ValueError, match="授权"):
        NarrationGenerator(tmp_path / "outputs", Provider()).generate(
            workspace_id="workspace_a",
            episode=episode,
            profile=profile,
        )
