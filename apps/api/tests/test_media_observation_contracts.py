"""Shared observation values retain producer identity without loading execution."""

from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from app import media_observations as observations
from app.persistence_json import _json_payload


def test_observation_import_and_construction_use_only_standard_library(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc
        import sqlite3
        import socket
        import subprocess
        import sys
        class OnlyObservations(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname.startswith('app.') and fullname != 'app.media_observations':
                    raise AssertionError('unexpected application dependency: ' + fullname)
        def forbidden(*args, **kwargs):
            raise AssertionError('value import attempted runtime work')
        sys.meta_path.insert(0, OnlyObservations())
        sqlite3.connect = forbidden
        socket.socket = forbidden
        subprocess.Popen = forbidden
        from app.media_observations import SubtitleSegment, MaterialSpeechAnalysis, SegmentUnderstanding
        assert SubtitleSegment(0, 1000, '测试').text == '测试'
        assert MaterialSpeechAnalysis('blocked').observations == []
        assert SegmentUnderstanding('blocked').ocr_frames == []
    """)
    script = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n" + script
    completed = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_existing_observation_defaults_mutability_and_serialized_values_are_preserved():
    segment = observations.SubtitleSegment(0, 1000, "原始语音")
    with pytest.raises(FrozenInstanceError):
        segment.text = "不能覆盖"
    speech = observations.MaterialSpeechAnalysis("ready", observations=[segment])
    other_speech = observations.MaterialSpeechAnalysis("blocked")
    assert asdict(other_speech) == {
        "status": "blocked", "source_fingerprint": "", "model_id": "", "observations": [], "message": "",
    }
    speech.observations.append(observations.SubtitleSegment(1000, 2000, "接续"))
    assert other_speech.observations == []
    assert '"observations": [{"start_ms": 0, "end_ms": 1000, "text": "原始语音"}' in _json_payload(speech)
    understanding = observations.SegmentUnderstanding("ready")
    other_understanding = observations.SegmentUnderstanding("blocked")
    understanding.sample_times.append(0.5)
    understanding.facts.append({"kind": "object", "text": "人物"})
    understanding.unknowns.append("地点")
    understanding.ocr_frames.append({"time": 0.5, "observations": []})
    assert other_understanding.sample_times == []
    assert other_understanding.facts == []
    assert other_understanding.unknowns == []
    assert other_understanding.ocr_frames == []
    assert (other_understanding.ocr_status, other_understanding.fingerprint, other_understanding.message) == (
        "blocked", "", "",
    )
    assert observations.OCR_RETRIEVAL_MIN_CONFIDENCE == 0.8


def test_original_observation_exports_refer_to_the_same_neutral_values():
    from app import asr, material_speech, visual_understanding

    assert asr.SubtitleSegment is observations.SubtitleSegment
    assert material_speech.SubtitleSegment is observations.SubtitleSegment
    assert material_speech.MaterialSpeechAnalysis is observations.MaterialSpeechAnalysis
    assert visual_understanding.SegmentUnderstanding is observations.SegmentUnderstanding
    assert visual_understanding.OCR_RETRIEVAL_MIN_CONFIDENCE == observations.OCR_RETRIEVAL_MIN_CONFIDENCE
