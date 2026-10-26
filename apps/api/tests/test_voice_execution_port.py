"""Granted voice adapters share a port, not a concrete provider class."""
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.avatar_lipsync import VoiceProfile
from app.narration import NarrationGenerator
from app.voice_clone import VoiceCloneResult
from test_voice_clone import _active_authorization, _provider, _write_wav


class AlternateGrantedProvider:
    model_id = "synthetic-alternate"
    supported_languages = ("en",)
    supports_pronunciation_dictionary = False
    ready = True
    message = "synthetic"

    def __init__(self, *, duration=1, wrong_output=False):
        self.duration, self.wrong_output = duration, wrong_output
        self.context = None

    def generate(self, **kwargs):
        raise AssertionError("granted business calls must not use the offline port")

    def generate_granted(self, *, grant_context, output_path, **kwargs):
        self.context = grant_context
        grant_context.check()
        grant_context.run(["synthetic-tts"], output_paths=(Path(output_path),))
        return VoiceCloneResult(str(output_path) + (".other" if self.wrong_output else ""),
                                self.duration, 24000)


@pytest.fixture
def narration_input(tmp_path):
    reference = tmp_path / "reference.wav"
    _write_wav(reference)
    voice = VoiceProfile(id="voice", workspace_id="workspace_a", person_profile_id="person",
        person_version="v1", version="v1", language="en", reference_audio_path=str(reference),
        reference_text="Reference.", model_id="synthetic-alternate",
        authorization=_active_authorization(content_type="voice"))
    return dict(workspace_id="workspace_a", task_id="task", script_id="script",
        narration_text="Hello.", max_duration_seconds=30, voice_identity=voice,
        pronunciation_entries=(), resolved_reference_audio_path=reference, language="en")


def granted_context():
    calls = []
    def run(command, *, output_paths):
        calls.append(command)
        _write_wav(output_paths[0])
    return SimpleNamespace(check=lambda: None, run=run), calls


def test_alternate_adapter_receives_original_grant_and_preserves_voice_authority(tmp_path, narration_input):
    provider = AlternateGrantedProvider()
    context, calls = granted_context()
    generator = NarrationGenerator(tmp_path / "output", provider)
    artifact = generator.generate_for_creation_task(**narration_input, grant_context=context)
    assert provider.context is context and len(calls) == 1
    assert Path(artifact.audio_path).is_file()
    voice = narration_input["voice_identity"]
    invalid = replace(voice, authorization=replace(voice.authorization,
                      expires_at=datetime(2025, 1, 2, tzinfo=timezone.utc)))
    with pytest.raises(ValueError, match="authorization_expired"):
        generator.generate_for_creation_task(**{**narration_input, "voice_identity": invalid}, grant_context=context)
    assert len(calls) == 1


@pytest.mark.parametrize("provider", [AlternateGrantedProvider(wrong_output=True), AlternateGrantedProvider(duration=31)])
def test_alternate_adapter_cannot_bypass_output_or_duration_validation(tmp_path, narration_input, provider):
    context, calls = granted_context()
    with pytest.raises(ValueError, match="WAV|实际配音时长"):
        NarrationGenerator(tmp_path / "output", provider).generate_for_creation_task(
            **narration_input, grant_context=context)
    assert len(calls) == 1


def test_offline_only_adapter_is_rejected_before_reference_or_generation(tmp_path, narration_input):
    class OfflineOnly:
        def generate(self, **kwargs):
            raise AssertionError("must not fall back to offline generation")
    context, calls = granted_context()
    with pytest.raises(ValueError, match="受许可"):
        NarrationGenerator(tmp_path / "output", OfflineOnly()).generate_for_creation_task(
            **{**narration_input, "resolved_reference_audio_path": "/not-read.wav"}, grant_context=context)
    assert calls == [] and not (tmp_path / "output").exists()


def test_cancelled_grant_never_reaches_adapter(tmp_path, narration_input):
    provider = AlternateGrantedProvider()
    def cancelled():
        raise PermissionError("cancelled grant")
    with pytest.raises(PermissionError, match="cancelled"):
        NarrationGenerator(tmp_path / "output", provider).generate_for_creation_task(
            **narration_input, grant_context=SimpleNamespace(check=cancelled))
    assert provider.context is None and not (tmp_path / "output").exists()


@pytest.mark.parametrize("explicit_root", [False, True])
def test_qwen_granted_port_requires_context_and_never_uses_offline_runner(tmp_path, explicit_root):
    def forbidden(*args):
        raise AssertionError("must not use offline runner")
    declared_root = tmp_path / "nested" / ".." / "declared-temporary" if explicit_root else None
    provider = _provider(tmp_path, forbidden, temporary_root=declared_root)
    expected_root = declared_root.resolve() if explicit_root else (tmp_path / ".cache" / "tmp").resolve()
    assert provider.temporary_root == expected_root and provider.cache_root == (tmp_path / ".cache").resolve()
    reference = tmp_path / "reference.wav"
    _write_wav(reference)
    kwargs = dict(text="Hello.", reference_audio_path=reference, reference_text="Reference.",
                  language="en", pronunciation_dictionary=(), output_path=tmp_path / "output.wav")
    with pytest.raises(PermissionError, match="设备许可"):
        provider.generate_granted(**kwargs, grant_context=None)
    assert not Path(kwargs["output_path"]).exists()
    calls = []
    def run(command, **options):
        calls.append((command, options))
        request_path = Path(command[command.index("--request") + 1])
        response_path = Path(command[command.index("--response") + 1])
        assert request_path.parent.parent == expected_root and response_path.parent == request_path.parent
        assert options["env"]["TMPDIR"] == str(expected_root)
        assert options["env"]["XDG_CACHE_HOME"] == str(provider.cache_root)
        assert options["env"]["HF_HOME"] == str(provider.cache_root / "huggingface")
        request = json.loads(request_path.read_text())
        _write_wav(Path(request["output_path"]))
        Path(command[command.index("--response") + 1]).write_text(json.dumps({
            "schema_version": 1, "model_id": provider.model_id, "audio_path": request["output_path"],
            "duration_seconds": 1, "sample_rate": 24000}))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    context = SimpleNamespace(check=lambda: None, threads=1, run=run)
    result = provider.generate_granted(**kwargs, grant_context=context)
    assert len(calls) == 1 and Path(result.audio_path) == kwargs["output_path"]
    assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert kwargs["output_path"].resolve() in calls[0][1]["output_paths"]


def test_task_output_directory_is_a_pure_fact_and_both_task_writers_use_it(tmp_path,narration_input,monkeypatch):
    import app.narration as narration_module
    root=tmp_path/'output'
    generator=NarrationGenerator(root,AlternateGrantedProvider())
    assert generator.task_output_directory('workspace_a','task')==root/'creation-tasks'/'workspace_a'/'task'
    assert not root.exists()
    declared=tmp_path/'actual-task-output'
    class ScopedGenerator(NarrationGenerator):
        def task_output_directory(self,workspace_id,task_id):
            assert (workspace_id,task_id)==('workspace_a','task')
            return declared
    def forbidden(*args,**kwargs):
        raise AssertionError('directory contract tests must not execute subprocesses')
    monkeypatch.setattr(narration_module.subprocess,'run',forbidden)
    monkeypatch.setattr(narration_module,'_probe_audio_duration',lambda *args,**kwargs:1.0)
    scoped=ScopedGenerator(root,AlternateGrantedProvider())
    context,_=granted_context()
    artifact=scoped.generate_for_creation_task(**narration_input,grant_context=context)
    assert Path(artifact.audio_path).parent==declared
    combined=scoped.combine_creation_task_segments(workspace_id='workspace_a',task_id='task',script_id='script',
        segments=[artifact],max_duration_seconds=30)
    assert Path(combined.audio_path).parent==declared
    assert Path(combined.audio_path).read_bytes()==Path(artifact.audio_path).read_bytes()
    assert not root.exists()
