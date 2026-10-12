import pytest
from app.narration import compile_narration_command, NarrationGenerator


@pytest.mark.parametrize(('command', 'tempo'), [
    ('这句重新配音', 1), ('请这句读慢一点', .9),
    ('重新配音，同时读快一点', 1.1), ('Please regenerate this sentence, then read slower.', .9),
    ('这段重读一遍；语速慢些', .9), ('faster', 1.1),
    ('这句以0.7倍速重新配音', .7), ('请这一句按 0.50 倍速重读一遍。', .5),
    ('所选配音以2.0倍速重新配音', 2), ('配音1倍速', 1),
    ('重新配音，然后以0.7倍速重新读一遍', .7),
    ('读慢一点，同时以0.9倍速重新配音', .9),
])
def test_supported_delivery_is_fully_compiled(command, tempo):
    assert compile_narration_command(command) == tempo


@pytest.mark.parametrize('command', [
    '不要重新配音', '这句不要读慢一点', '读慢一点，但字幕不要变',
    '重新配音，同时字幕变黄', '这句重新配音，开心一点',
    '读快一点同时读慢一点', '不要读快一点，要读慢一点',
    'regenerate but keep the subtitles unchanged', 'not faster', '', '慢镜头',
    '以0.49倍速重新配音', '以2.01倍速重新配音', '以0倍速重新配音',
    '以nan倍速重新配音', '以inf倍速重新配音', '以1e309倍速重新配音',
    '以' + '9' * 400 + '倍速重新配音',
    '这句0.7重新配音', '重新配音120秒', '第2句重新配音',
    '以0.7倍速重新配音但保留字幕', '以0.7倍速重新配音，同时开心一点',
    '以0.7倍速重新配音，同时以0.8倍速重新配音', '读快一点，同时以0.7倍速重新配音',
    '不要以0.7倍速重新配音', '以0.7.1倍速重新配音',
])
def test_unexecutable_delivery_is_rejected(command):
    with pytest.raises(ValueError):
        compile_narration_command(command)


@pytest.mark.parametrize('command', ['不要重新配音', '这句以0.4倍速重新配音'])
def test_invalid_delivery_fails_before_voice_provider_or_reference_access(tmp_path, command):
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
            delivery_instruction=command,
        )
