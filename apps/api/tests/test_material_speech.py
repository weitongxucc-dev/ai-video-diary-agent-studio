from dataclasses import replace
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from app.asr import ASRTranscription, SubtitleSegment
from app.config import Settings
from app.material_speech import LocalMaterialSpeechAnalyzer, MaterialSpeechAnalysis, speech_text_for_range
from app.montage import NarrationCue, _score_candidate
from app.production import ProductionRepository


def test_local_speech_persists_time_evidence_caches_and_preserves_user_corrections(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    source = library / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model-v1")
    analyzer = LocalMaterialSpeechAnalyzer(Settings(local_whisper_python_path=sys.executable, local_whisper_model_path=str(model), temporary_root_path=str(tmp_path / "temp")))
    transcribe = Mock(return_value=ASRTranscription("local_whisper", "USB port", [SubtitleSegment(0, 1300, "USB port")]))
    analyzer.transcriber = SimpleNamespace(configured=True, transcribe=transcribe)
    repo = ProductionRepository(tmp_path / "db.sqlite", library, scene_reader=lambda path, duration: [], speech_analyzer=analyzer)
    repo.material_source_files.bind_material_ingestion("workspace", expected_directory=str(library))
    repo.material_source_files.confirm_material_copy_complete(
        "workspace", relative_path="sample.mp4",
        expected_revision=repo.material_source_files.material_copy_revision(source.stat()),
        confirmation_source="manual",
    )
    asset = repo.material_intake.scan_library("workspace")[0]
    analyzed = repo.analyze_asset("workspace", asset.id)
    assert analyzed.speech_analysis.status == "ready"
    assert analyzed.speech_analysis.observations[0].end_ms == 1000
    segment = analyzed.segments[0]
    asset = repo.update_asset_review("workspace", asset.id, review_status="approved", usage_role="b_roll")
    repo.set_asset_visibility("workspace", asset.id, "shared")
    restarted = ProductionRepository(tmp_path / "db.sqlite", library, scene_reader=lambda path, duration: [], speech_analyzer=analyzer)
    restarted.analyze_asset("workspace", asset.id)
    assert transcribe.call_count == 1
    assert restarted.material_search.search_material_segments("workspace", query="USB port")[0].segment_ref == f"segment:{segment.id}"
    assert restarted.material_search.search_material_segments("other-workspace", query="USB port") == []
    loaded = restarted.get_asset("workspace", asset.id)
    cue = NarrationCue(id="cue", source_section_id="section", narration_segment_id="voice", text="USB port", duration_seconds=1)
    score = _score_candidate(cue, loaded, loaded.segments[0], 0, 0)
    assert score[1] == 0  # Heard words cannot auto-confirm the picture.
    assert any("语音证据不能确认画面" in reason for reason in score[-1])
    restarted.update_segment("workspace", asset.id, segment.id, description="人物在讲解", transcript="HDMI port",
                             review_status="approved", usage_role="b_roll")
    model.write_bytes(b"model-v2")
    reanalyzed = restarted.analyze_asset("workspace", asset.id)
    assert transcribe.call_count == 2
    assert reanalyzed.segments[0].transcript == "HDMI port"
    assert reanalyzed.segments[0].description == "人物在讲解"
    assert restarted.material_search.search_material_segments("workspace", query="USB") == []
    assert restarted.list_assets_page("workspace", limit=20, offset=0, query="USB")[1] == 0
    assert restarted.material_search.search_material_segments("workspace", query="HDMI")[0].segment_ref == f"segment:{segment.id}"
    assert not list((tmp_path / "temp").iterdir())


def test_speech_range_excludes_neighboring_observations():
    analysis = MaterialSpeechAnalysis("ready", observations=[SubtitleSegment(0, 1000, "first"), SubtitleSegment(1000, 2000, "second")])
    assert speech_text_for_range(analysis, 1, 2) == "second"
    assert speech_text_for_range(replace(analysis, status="failed"), 0, 2) == ""


def test_missing_audio_is_explicit_and_bad_media_does_not_keep_stale_evidence(tmp_path):
    source = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    analyzer = LocalMaterialSpeechAnalyzer(Settings(temporary_root_path=str(tmp_path / "temp")))
    result = analyzer.analyze(source, 1, None)
    assert result.status == "ready"
    assert result.observations == []
    assert result.message == "原片没有音轨。"
    source.write_bytes(b"broken source")
    failed = analyzer.analyze(source, 1, result)
    assert failed.status == "failed"
    assert failed.observations == []
    assert failed.source_fingerprint != result.source_fingerprint


def test_material_query_respects_non_chinese_word_boundaries():
    from app.production import MaterialSegment
    from app.materials.segment_search_use_case import _material_query_reasons
    from unittest.mock import Mock
    asset = Mock(usage_role="b_roll", speech_analysis=None)
    segment = MaterialSegment("segment", "asset", 0, 1, transcript="Education USB port", usage_role="b_roll")
    assert not _material_query_reasons("cat", segment, asset)
    assert not _material_query_reasons("пеuр", segment, asset)
    assert _material_query_reasons("usb", segment, asset)
