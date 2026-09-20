import pytest
from app.narration import compile_narration_command, NarrationGenerator


@pytest.mark.parametrize(('command', 'tempo'), [
    ('这句重新配音', 1), ('请这句读慢一点', .9),
    ('重新配音，同时读快一点', 1.1), ('Please regenerate this sentence, then read slower.', .9),
    ('这段重读一遍；语速慢些', .9), ('faster', 1.1),
])
def test_supported_delivery_is_fully_compiled(command, tempo):
    assert compile_narration_command(command) == tempo


@pytest.mark.parametrize('command', [
    '不要重新配音', '这句不要读慢一点', '读慢一点，但字幕不要变',
    '重新配音，同时字幕变黄', '这句重新配音，开心一点',
    '读快一点同时读慢一点', '不要读快一点，要读慢一点',
    'regenerate but keep the subtitles unchanged', 'not faster', '', '慢镜头',
])
def test_unexecutable_delivery_is_rejected(command):
    with pytest.raises(ValueError):
        compile_narration_command(command)


def test_invalid_delivery_fails_before_voice_provider_or_reference_access(tmp_path):
    class UnusedProvider:
        def generate(self, **kwargs):
            pytest.fail('invalid delivery must not invoke a voice model')
    generator = NarrationGenerator(tmp_path, UnusedProvider())
    with pytest.raises(ValueError, match='配音要求'):
        generator.generate_for_creation_task(
            workspace_id='workspace', task_id='task', script_id='script',
            narration_text='Hello', max_duration_seconds=10,
            voice_identity=None, pronunciation_entries=(),
            resolved_reference_audio_path=tmp_path / 'nonexistent.wav',
            delivery_instruction='不要重新配音',
        )
