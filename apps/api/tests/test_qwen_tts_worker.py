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


def rule(term, target, *, language="en", scope="workspace", entry_id="entry"):
    return {"id":entry_id, "version":"v1", "term":term, "language":language,
            "target_pronunciation":target, "scope":scope, "scope_reference":""}


def test_worker_passes_once_replaced_text_to_model_and_keeps_reference_text():
    payload=request("en")
    payload["text"]="OpenAI AI A"
    payload["reference_text"]="Original reference remains."
    payload["pronunciation_dictionary"]=[
        rule("AI", "workspace eye", scope="workspace", entry_id="w"),
        rule("AI", "person eye", scope="person", entry_id="p"),
        rule("AI", "generic task eye", language="zh-en", scope="task", entry_id="generic"),
        rule("AI", "exact task sound", scope="task", entry_id="exact"),
        rule("OpenAI", "Open A I", scope="workspace", entry_id="long"),
        rule("A", "B", scope="workspace", entry_id="chain-a"),
        rule("B", "C", scope="workspace", entry_id="chain-b"),
    ]
    worker._validate_request(payload)
    captured=[]

    class Model:
        def generate(self, **kwargs):
            captured.append(kwargs)
            return ["fake audio result"]

    assert worker._generate_with_pronunciation(Model(), payload, Path("/ref.wav")) == ["fake audio result"]
    assert captured[0]["text"] == "Open A I exact task sound B"
    assert captured[0]["ref_text"] == "Original reference remains."


def test_worker_prefers_person_scope_over_workspace_and_leaves_unmatched_rules_alone():
    payload=request("en")
    dictionary=[
        rule("Codex", "workspace version", scope="workspace", entry_id="workspace"),
        rule("Codex", "person version", scope="person", entry_id="person"),
        rule("你好", "你好呀", language="zh", scope="task", entry_id="zh-only"),
    ]
    assert worker._apply_pronunciation_dictionary("Codex 你好", dictionary, "en") == "person version 你好"
    assert worker._apply_pronunciation_dictionary("你好 Codex", [rule("Codex", "generic version", language="zh-en")], "en") == "你好 generic version"
    assert worker._apply_pronunciation_dictionary("Codex", [], "en") == "Codex"


def test_worker_rejects_same_term_language_and_scope_conflict():
    payload=request("en")
    payload["pronunciation_dictionary"]=[
        rule("Codex", "Koh-dex", scope="task", entry_id="one"),
        rule("Codex", "Code-ex", scope="task", entry_id="two"),
    ]
    with pytest.raises(ValueError, match="conflicting pronunciation rules"):
        worker._validate_request(payload)


def test_worker_rejects_malformed_pronunciation_entry():
    payload=request("zh")
    payload["pronunciation_dictionary"]=[{"term":"AI"}]
    with pytest.raises(ValueError,match="pronunciation"):
        worker._validate_request(payload)
