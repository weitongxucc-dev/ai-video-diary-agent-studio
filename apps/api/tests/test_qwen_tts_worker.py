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


def test_worker_disables_free_cache_before_weight_load_and_inference_and_logs_memory(tmp_path, monkeypatch, capsys):
    import json
    import sys
    from types import ModuleType, SimpleNamespace

    events=[]
    cache={"bytes":128}
    mx=ModuleType("mlx.core")
    def set_cache_limit(limit):
        assert limit==0
        events.append("disable_cache")
        cache["bytes"]=0
    mx.set_cache_limit=set_cache_limit
    mx.get_active_memory=lambda:64
    mx.get_cache_memory=lambda:cache["bytes"]
    mx.get_peak_memory=lambda:256
    mlx=ModuleType("mlx")
    mlx.__path__=[]
    mlx.core=mx
    numpy=ModuleType("numpy")
    numpy.asarray=lambda values:values
    numpy.concatenate=lambda values:SimpleNamespace(size=sum(len(item) for item in values))
    audio=ModuleType("mlx_audio.audio_io")
    def write_audio(path, values, rate):
        assert cache["bytes"]==0 and rate==24 and values.size==48
        events.append("write_audio")
        Path(path).write_bytes(b"worker contract fixture")
    audio.write=write_audio
    utils=ModuleType("mlx_audio.tts.utils")
    class Model:
        def generate(self, **kwargs):
            assert cache["bytes"]==0
            assert kwargs["text"]=="Read this." and kwargs["ref_text"]=="Reference."
            events.append("generate")
            return [SimpleNamespace(audio=[0]*48,sample_rate=24)]
    def load_model(path):
        assert cache["bytes"]==0 and path==str(tmp_path/"model")
        events.append("load_model")
        return Model()
    utils.load_model=load_model
    package=ModuleType("mlx_audio")
    package.__path__=[]
    tts=ModuleType("mlx_audio.tts")
    tts.__path__=[]
    for name,module in {"numpy":numpy,"mlx":mlx,"mlx.core":mx,"mlx_audio":package,
                        "mlx_audio.audio_io":audio,"mlx_audio.tts":tts,"mlx_audio.tts.utils":utils}.items():
        monkeypatch.setitem(sys.modules,name,module)
    (tmp_path/"model").mkdir()
    (tmp_path/"ref.wav").write_bytes(b"reference fixture")
    payload=request("en")
    payload.update(model_path=str(tmp_path/"model"),reference_audio_path=str(tmp_path/"ref.wav"),output_path=str(tmp_path/"audio.wav"))
    input_path=tmp_path/"input.json"
    input_path.write_text(json.dumps(payload))
    response_path=tmp_path/"response.json"
    monkeypatch.setattr(sys,"argv",["worker","--request",str(input_path),"--response",str(response_path)])
    worker.main()
    assert events==["disable_cache","load_model","generate","write_audio"]
    assert json.loads(response_path.read_text())["duration_seconds"]==2.0
    assert (tmp_path/"audio.wav").read_bytes()==b"worker contract fixture"
    log=capsys.readouterr().err
    for phase in ("cache_disabled","weights_loaded","inference_started","inference_returned","audio_written"):
        assert f"QWEN_MEMORY phase={phase} active_bytes=64 cache_bytes=0 peak_bytes=256" in log
    assert str(tmp_path) not in log
