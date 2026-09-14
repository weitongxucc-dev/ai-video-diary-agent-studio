import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("qwen_tts_worker", Path(__file__).parents[1] / "model_workers" / "qwen3_tts_worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def request(language):
    return {"schema_version":1, "model_id":worker.MODEL_ID, "language":language,
            "model_path":"/models/test", "text":"Read this.", "reference_audio_path":"/ref.wav",
            "reference_text":"Reference.", "output_path":"/result.wav", "pronunciation_dictionary":[]}


@pytest.mark.parametrize("language,code", [("zh","chinese"),("en","english"),("zh-en","auto")])
def test_worker_validates_explicit_language(language,code):
    worker._validate_request(request(language))
    assert worker.LANGUAGE_CODES[language] == code


@pytest.mark.parametrize("language", [None,"fr","zh-CN"])
def test_worker_rejects_unknown_language(language):
    with pytest.raises(ValueError,match="language"):
        worker._validate_request(request(language))


def test_worker_does_not_silently_ignore_pronunciation_dictionary():
    payload=request("zh")
    payload["pronunciation_dictionary"]=[{"term":"AI"}]
    with pytest.raises(ValueError,match="pronunciation"):
        worker._validate_request(payload)
