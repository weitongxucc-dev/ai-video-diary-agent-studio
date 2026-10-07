import json
import wave
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection
from app.avatar_lipsync import Authorization, PronunciationEntry, VoiceProfile as VersionedVoiceProfile
from app.narration import NarrationGenerator
from app.production import ProductionProfile, VoiceProfile
from app.voice_clone import (
    Qwen3TTSSubprocessProvider,
    VoiceCloneError,
    VoiceCloneResult,
    text_to_speech_preflight,
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


def _write_wav(path: Path, *, sample_rate: int = 24000, frames: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frames)


def _active_authorization(*, content_type: str, scope: str = "narration") -> Authorization:
    return Authorization(
        id=f"auth-{content_type}",
        version="auth-v1",
        rights_holder="本人",
        content_types=(content_type,),
        usage_scopes=(scope,),
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        evidence_reference="consent://evidence/1",
        status="active",
        workspace_id="workspace_a",
    )


def test_voice_provider_requires_wav_and_validates_worker_result(tmp_path):
    def responder(request, response_path):
        output = Path(request["output_path"])
        _write_wav(output)
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
    _write_wav(reference)
    result = provider.generate(
        text="这是一段自动配音。",
        reference_audio_path=reference,
        reference_text="这是参考文字。",
        language="zh",
        pronunciation_dictionary=(),
        output_path=tmp_path / "output" / "narration.wav",
    )

    assert result.duration_seconds == 12.5
    assert Path(result.audio_path).is_file()

    invalid_reference = tmp_path / "reference.mp3"
    invalid_reference.write_bytes(b"mp3")
    with pytest.raises(VoiceCloneError, match="WAV"):
        provider.generate(
            text="文案",
            reference_audio_path=invalid_reference,
            reference_text="参考文字",
            language="zh",
            pronunciation_dictionary=(),
            output_path=tmp_path / "invalid.wav",
        )


def test_qwen_tts_declares_language_and_pronunciation_limits_before_worker(tmp_path):
    worker_calls = 0

    def responder(request, response_path):
        nonlocal worker_calls
        worker_calls += 1
        output = Path(request["output_path"])
        _write_wav(output)
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "qwen3-tts-12hz-0.6b-base-8bit",
                    "audio_path": str(output),
                    "duration_seconds": 1.0,
                    "sample_rate": 24000,
                }
            ),
            encoding="utf-8",
        )

    provider = _provider(tmp_path, responder)
    reference = tmp_path / "reference.wav"
    _write_wav(reference)
    dictionary = (
        {
            "id": "term-1",
            "version": "v1",
            "term": "Codex",
            "language": "en",
            "target_pronunciation": "KO-deks",
            "scope": "workspace",
            "scope_reference": "",
        },
    )

    assert text_to_speech_preflight(
        provider, language="zh", pronunciation_dictionary=()
    ) is None
    assert text_to_speech_preflight(provider, language="en", pronunciation_dictionary=()) is None
    for language in ("zh-en",):
        failure = text_to_speech_preflight(
            provider, language=language, pronunciation_dictionary=()
        )
        assert failure is not None
        assert (failure.reason, failure.retryable) == ("language_unavailable", False)
    dictionary_failure = text_to_speech_preflight(
        provider, language="zh", pronunciation_dictionary=dictionary
    )
    assert dictionary_failure is not None
    assert dictionary_failure.reason == "pronunciation_dictionary_unavailable"

    for language, entries, reason in (
        ("zh-en", (), "language_unavailable"),
        ("zh", dictionary, "pronunciation_dictionary_unavailable"),
    ):
        with pytest.raises(VoiceCloneError) as exc_info:
            provider.generate(
                text="预检不能进入 worker。",
                reference_audio_path=reference,
                reference_text="参考文字。",
                language=language,
                pronunciation_dictionary=entries,
                output_path=tmp_path / f"{language}.wav",
            )
        assert exc_info.value.failure.reason == reason
    assert worker_calls == 0


def test_narration_generator_enforces_consent_and_uses_safe_output_path(tmp_path):
    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, *, text, reference_audio_path, reference_text, language, pronunciation_dictionary, output_path):
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


def test_narration_generator_rejects_overlong_script_before_voice_generation(tmp_path):
    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            raise AssertionError("overlong script must be rejected before synthesis")

    episode = Episode(
        id="episode_long",
        day_number=1,
        episode_date=date(2026, 8, 25),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage(
            id="script_long",
            hook="开头。",
            sections=[ScriptSection("body", "正文", 100, "过长正文。")],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )
    profile = ProductionProfile(
        workspace_id="workspace_a",
        target_duration_max_seconds=90,
        voice_reference_path=str(tmp_path / "reference.wav"),
        voice_reference_text="参考文字",
        voice_consent_confirmed=True,
    )

    with pytest.raises(ValueError, match="文案预计时长.*超过生产档案上限"):
        NarrationGenerator(tmp_path / "outputs", Provider()).generate(
            workspace_id="workspace_a",
            episode=episode,
            profile=profile,
        )


def test_narration_generator_rejects_overlong_generated_audio(tmp_path):
    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, *, output_path, **_kwargs):
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"audio")
            return VoiceCloneResult(str(output), 95.0, 24000)

    episode = Episode(
        id="episode_audio_long",
        day_number=1,
        episode_date=date(2026, 8, 25),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage(
            id="script_short",
            hook="开头。",
            sections=[ScriptSection("body", "正文", 20, "正文。")],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )
    profile = ProductionProfile(
        workspace_id="workspace_a",
        target_duration_max_seconds=90,
        voice_reference_path=str(tmp_path / "reference.wav"),
        voice_reference_text="参考文字",
        voice_consent_confirmed=True,
    )

    with pytest.raises(ValueError, match="实际配音时长.*超过生产档案上限"):
        NarrationGenerator(tmp_path / "outputs", Provider()).generate(
            workspace_id="workspace_a",
            episode=episode,
            profile=profile,
        )


def test_narration_generator_uses_selected_voice_and_versioned_output(tmp_path):
    captured = []

    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, *, reference_audio_path, reference_text, output_path, **_kwargs):
            captured.append((reference_audio_path, reference_text))
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"audio")
            return VoiceCloneResult(str(output), 8.0, 24000)

    episode = Episode(
        id="episode_voice",
        day_number=1,
        episode_date=date(2026, 8, 25),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage(
            id="script_voice",
            hook="开头。",
            sections=[ScriptSection("body", "正文", 8, "正文。")],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )
    reference = tmp_path / "person-b.wav"
    reference.write_bytes(b"wav")
    voice = VoiceProfile(
        id="person_b",
        workspace_id="workspace_a",
        name="B",
        reference_audio_path=str(reference),
        reference_text="B 的参考文字",
        consent_confirmed=True,
    )
    generator = NarrationGenerator(tmp_path / "outputs", Provider())
    artifact = generator.generate(
        workspace_id="workspace_a",
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_profile=voice,
    )

    assert captured == [(reference.resolve(), "B 的参考文字")]
    assert artifact.voice_profile_id == "person_b"
    assert artifact.cache_key
    assert "episode_voice_person_b_" in artifact.audio_path


def test_narration_cache_key_binds_voice_person_model_language_and_pronunciation_versions(tmp_path):
    class Provider:
        model_id = "runtime-v1"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            raise AssertionError("cache-key test must not synthesize audio")

    episode = Episode(
        id="episode_cache",
        day_number=1,
        episode_date=date(2026, 8, 26),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage(
            id="script_cache",
            hook="欢迎使用 Codex。",
            sections=[ScriptSection("body", "正文", 8, "这是中文 English 混合文案。")],
            title_options=[],
            cover_copy="",
            publish_copy="",
            comment_prompt="",
        ),
    )
    voice = VersionedVoiceProfile(
        id="voice_a",
        workspace_id="workspace_a",
        person_profile_id="person_a",
        person_version="person-v1",
        version="voice-v1",
        language="zh-en",
        reference_audio_path=str(tmp_path / "reference.wav"),
        reference_text="参考文字",
        model_id="voice-model-v1",
        authorization=_active_authorization(content_type="voice"),
    )
    entry = PronunciationEntry(
        id="pronunciation_1",
        workspace_id="workspace_a",
        term="Codex",
        language="en",
        target_pronunciation="KO-deks",
        scope="person",
        scope_reference="person_a",
        version="pronunciation-v1",
    )
    generator = NarrationGenerator(tmp_path / "outputs", Provider())
    baseline = generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=voice,
        pronunciation_entries=(entry,),
        language="zh-en",
    )

    assert baseline != generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=replace(voice, version="voice-v2"),
        pronunciation_entries=(entry,),
        language="zh-en",
    )
    assert baseline != generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=replace(voice, person_version="person-v2"),
        pronunciation_entries=(entry,),
        language="zh-en",
    )
    assert baseline != generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=replace(voice, model_id="voice-model-v2"),
        pronunciation_entries=(entry,),
        language="zh-en",
    )
    assert baseline != generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=voice,
        pronunciation_entries=(replace(entry, version="pronunciation-v2"),),
        language="zh-en",
    )
    assert baseline != generator.cache_key(
        episode=episode,
        profile=ProductionProfile(workspace_id="workspace_a"),
        voice_identity=voice,
        pronunciation_entries=(entry,),
        language="en",
    )


def test_narration_rejects_expired_versioned_voice_authorization(tmp_path):
    class Provider:
        model_id = "voice-model"
        ready = True
        message = "ready"

        def generate(self, **_kwargs):
            raise AssertionError("expired authorization must stop before synthesis")

    episode = Episode(
        id="episode_expired",
        day_number=1,
        episode_date=date(2026, 8, 26),
        status=EpisodeStatus.SCRIPT_APPROVED,
        workspace_id="workspace_a",
        script=ScriptPackage("script", "开头。", [ScriptSection("body", "正文", 8, "正文。")], [], "", "", ""),
    )
    expired = replace(
        _active_authorization(content_type="voice"),
        expires_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    voice = VersionedVoiceProfile(
        id="voice_a",
        workspace_id="workspace_a",
        person_profile_id="person_a",
        person_version="person-v1",
        version="voice-v1",
        language="zh",
        reference_audio_path=str(tmp_path / "reference.wav"),
        reference_text="参考文字",
        model_id="voice-model",
        authorization=expired,
    )

    with pytest.raises(ValueError, match="authorization_expired"):
        NarrationGenerator(tmp_path / "outputs", Provider()).generate(
            workspace_id="workspace_a",
            episode=episode,
            profile=ProductionProfile(workspace_id="workspace_a"),
            voice_identity=voice,
        )


def test_creation_task_english_uses_target_language_and_separate_cache(tmp_path):
    calls=[]
    class Provider:
        model_id="voice-test"
        ready=True
        message="ready"
        supported_languages=("zh","en")
        supports_pronunciation_dictionary=False
        def generate(self, **kwargs):
            calls.append(kwargs)
            _write_wav(Path(kwargs["output_path"]))
            return VoiceCloneResult(str(kwargs["output_path"]),1,24000)
    voice=VersionedVoiceProfile(id="voice",workspace_id="workspace_a",person_profile_id="person",person_version="v1",
        version="v1",language="zh-en",reference_audio_path="workspace://ref.wav",reference_text="参考文字",
        model_id="voice-test",authorization=_active_authorization(content_type="voice"))
    reference=tmp_path/"ref.wav"
    _write_wav(reference)
    generator=NarrationGenerator(output_root=tmp_path/"output",provider=Provider())
    args=dict(workspace_id="workspace_a",task_id="task",script_id="script",narration_text="Preview the result.",
        max_duration_seconds=30,voice_identity=voice,pronunciation_entries=(),resolved_reference_audio_path=reference)
    en=generator.generate_for_creation_task(**args,language="en")
    zh=generator.generate_for_creation_task(**args,language="zh")
    assert en.language=="en" and calls[0]["language"]=="en"
    assert zh.language=="zh" and en.cache_key!=zh.cache_key
    with pytest.raises(ValueError,match="声音版本"):
        generator.generate_for_creation_task(**{**args,"voice_identity":replace(voice,language="zh")},language="en")
    assert len(calls)==2
    with pytest.raises(ValueError,match="语言不一致"):
        generator.combine_creation_task_segments(workspace_id="workspace_a",task_id="task",script_id="script",
            segments=[en,zh],max_duration_seconds=30)


@pytest.mark.parametrize("language", ["zh", "en", "zh-en"])
def test_creation_task_narration_response_preserves_artifact_and_segment_language(monkeypatch, language):
    from types import SimpleNamespace
    from app import main
    from app.production import CreationTask, NarrationArtifactRecord, NarrationSegment

    task = CreationTask(id="task-language", workspace_id="local", plan_id="plan-language",
        name="Language response", scenario="test")
    artifact = NarrationArtifactRecord(id="audio-language", workspace_id="local",
        task_id=task.id, script_id="script-language", voice_profile_id="voice-language",
        cache_key="language-cache", audio_path="narration://artifact/language-cache",
        duration_seconds=2, sample_rate=24000, model_id="local", language=language)
    segment = NarrationSegment(id="segment-language", workspace_id="local", task_id=task.id,
        script_id=artifact.script_id, source_section_id="section-language", text="Preview the result.",
        audio_reference=artifact.audio_path, duration_seconds=2, sample_rate=24000,
        model_id="local", voice_profile_id="voice-language", voice_version="v1",
        language=language, pronunciation_version="", status="ready")
    monkeypatch.setattr(main, "production_repo", SimpleNamespace(
        list_narration_segments=lambda *_: [segment],
        narration_artifact_reference=lambda _: artifact.audio_path))
    response = main._creation_task_narration_response(task, artifact).model_dump(mode="json")
    assert response["language"] == language
    assert response["segments"][0]["language"] == language
    assert response["segments"][0]["audio_url"].endswith("/segment-language/audio")


def test_granted_qwen_lower_io_priority_is_process_scoped_and_offline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.voice_clone as voices
    calls = []
    provider = _provider(tmp_path, lambda *_args: None)
    monkeypatch.setattr(voices.sys, "platform", "darwin")
    context = SimpleNamespace(threads=1, run=lambda command, **options:
        calls.append((command, options)) or SimpleNamespace(returncode=0, stdout=b"", stderr=b""))
    provider._run_command([str(provider.runtime_python_path), str(provider.worker_path)], grant_context=context)
    command, options = calls[0]
    assert command[:3] == ["/usr/sbin/taskpolicy", "-d", "throttle"]
    assert command[3:] == [str(provider.runtime_python_path), str(provider.worker_path)]
    assert options["env"]["HF_HUB_OFFLINE"] == "1"
    assert options["env"]["OMP_NUM_THREADS"] == "1"
    assert not any(key.endswith("KEY") for key in options["env"])
