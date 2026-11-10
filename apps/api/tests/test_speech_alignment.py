import json
from pathlib import Path

import pytest

from app.asr_alignment import read_whisper_alignment
from app.asr import LocalWhisperTranscriber
from app.config import Settings


@pytest.mark.parametrize('backend,payload,expected', [
    ('whisper', {'segments': [{'words': [{'start': .1, 'end': .36, 'word': '限制条件'}]}]}, 'word'),
    ('whisper_cpp', {'transcription': [{'tokens': [
        {'text': '[_BEG_]'}, {'text': '限制', 'offsets': {'from': 100, 'to': 360}},
        {'text': '未对齐词'}]}]}, 'token'),
])
def test_actual_alignment_is_preserved_without_interpolating_missing_units(tmp_path, backend, payload, expected):
    path = tmp_path / 'actual.json'
    path.write_text(json.dumps(payload))
    words, granularity = read_whisper_alignment(path, backend)
    assert granularity == expected
    assert [(word.start_ms, word.end_ms) for word in words] == [(100, 360)]


def test_sentence_only_output_cannot_claim_word_timing(tmp_path):
    path = tmp_path / 'sentence.json'
    path.write_text(json.dumps({'segments': [{'start': 0, 'end': 10, 'text': '这句话没有逐词时间'}]}))
    assert read_whisper_alignment(path, 'whisper') == ([], 'segment')
    path.write_text(json.dumps({'segments': [{'words': [{'start': 10, 'end': 1, 'word': '错误'}]}]}))
    with pytest.raises(ValueError, match='对齐时间'):
        read_whisper_alignment(path, 'whisper')


@pytest.mark.parametrize('backend', ['openai_whisper', 'whisper_cpp'])
def test_alignment_is_requested_from_selected_local_backend_and_returned(tmp_path, backend):
    runtime, model, audio = [tmp_path / name for name in ('runtime', 'model', 'audio.wav')]
    for path in (runtime, model, audio):
        path.write_bytes(b'fixture')

    def runner(command):
        if backend == 'whisper_cpp':
            assert '-ojf' in command
            output = Path(command[command.index('-of') + 1])
            raw = {'transcription': [{'tokens': [{'text': '限定', 'offsets': {'from': 100, 'to': 500}}]}]}
        else:
            assert command[command.index('--word_timestamps') + 1] == 'True'
            output = Path(command[command.index('--output_dir') + 1]) / 'audio'
            raw = {'segments': [{'words': [{'start': .1, 'end': .5, 'word': '限定'}]}]}
        output.with_suffix('.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\n完整限定\n')
        output.with_suffix('.json').write_text(json.dumps(raw))

    settings = Settings(local_whisper_backend=backend, local_whisper_python_path=str(runtime),
        local_whisper_cli_path=str(runtime), local_whisper_model_path=str(model))
    result = LocalWhisperTranscriber(settings, runner=runner).transcribe(audio, word_timestamps=True)
    assert result.segments[0].text == '完整限定'
    assert result.words[0].text == '限定' and result.words[0].start_ms == 100
