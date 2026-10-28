from dataclasses import asdict
import subprocess
from types import SimpleNamespace

import pytest

from app.model_runtime import ModelCatalog, QWEN3_VL_4B_4BIT
from app.production import MaterialSegment, ProductionRepository
from app.visual_understanding import LocalVisualUnderstanding, SegmentUnderstanding, parse_understanding, validate_ocr_frames


def test_observations_require_real_frame_references_and_two_times_for_actions():
    payload = {"summary": "桌面上的电脑", "facts": [{"kind": "object", "text": "电脑", "frames": [0, 2]}], "unknowns": ["品牌不可见"]}
    assert parse_understanding(payload, [1, 2, 3])["facts"][0]["sample_times"] == [1, 3]
    payload["facts"][0]["frames"] = [3]
    with pytest.raises(ValueError, match="采样帧"):
        parse_understanding(payload, [1, 2, 3])
    payload["facts"][0].update(kind="action", frames=[0, 1])
    with pytest.raises(ValueError, match="两个不同时间"):
        parse_understanding(payload, [1, 1])


def test_ocr_rejects_changed_source_and_out_of_bounds_boxes():
    requested = [{"id": "frame", "timestamp_seconds": 1}]
    frame = {"id": "frame", "timestamp_seconds": 1, "width": 100, "height": 50,
             "observations": [{"text": "USB", "confidence": 0.9, "box": [0.1, 0.2, 0.4, 0.3]}]}
    payload = {"model_id": "vision", "coordinate_space": "normalized_top_left", "frames": [frame]}
    assert validate_ocr_frames(payload, requested)[0] == "vision"
    frame["observations"][0]["box"] = [0.8, 0.2, 0.4, 0.3]
    with pytest.raises(ValueError, match="超出图像"):
        validate_ocr_frames(payload, requested)
    frame["id"] = "other-frame"
    with pytest.raises(ValueError, match="来源不匹配"):
        validate_ocr_frames(payload, requested)


def test_batched_analysis_caches_and_returns_separate_ocr_evidence(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    worker = tmp_path / "worker"
    worker.write_text("version-one")
    status = SimpleNamespace(ready=True, worker_path=str(worker), runtime_python_path="python", model_path="model", message="ready")
    catalog = SimpleNamespace(status=lambda spec: status)
    analyzer = LocalVisualUnderstanding(catalog, tmp_path / "temp", str(worker))
    calls = []

    def run(command, request, root, name, timeout):
        calls.append(name)
        if name == "ocr":
            return {"model_id": "vision", "coordinate_space": "normalized_top_left", "frames": [
                {"id": frame["id"], "timestamp_seconds": frame["timestamp_seconds"], "width": 64, "height": 64,
                 "observations": [{"text": "USB port", "confidence": 1.0, "box": [0, 0, .5, .5]}]}
                for frame in request["frames"]]}
        return {"segments": [{"id": row["id"], "understanding": {"summary": "黑色画面", "facts": [], "unknowns": []}} for row in request["segments"]]}

    monkeypatch.setattr(analyzer, "_run", run)
    segment = MaterialSegment("segment", "asset", 0, 1)
    result = analyzer.analyze(source, [segment], "content-one")[segment.id]
    assert result.status == result.ocr_status == "ready"
    assert result.ocr_frames[0]["observations"][0]["text"] == "USB port"
    segment.understanding = result
    assert analyzer.analyze(source, [segment], "content-one")[segment.id] == result
    assert calls == ["ocr", "vlm"]
    analyzer.analyze(source, [segment], "content-two")
    assert calls == ["ocr", "vlm", "ocr", "vlm"]
    assert not list((tmp_path / "temp").iterdir())


def test_analysis_commit_preserves_corrections_made_during_processing(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "source.mp4").write_bytes(b"fixture")
    repo = ProductionRepository(tmp_path / "db.sqlite", library, duration_reader=lambda path: 2, scene_reader=lambda path, duration: [])
    repo.bind_material_ingestion("workspace", expected_directory=str(library))
    repo.confirm_material_copy_complete(
        "workspace", relative_path="source.mp4",
        expected_revision=repo.material_copy_revision((library / "source.mp4").stat()),
        confirmation_source="manual",
    )
    asset = repo.scan_library("workspace")[0]
    analyzed = repo.analyze_asset("workspace", asset.id)
    segment_id = analyzed.segments[0].id
    repo.update_asset_review("workspace", asset.id, review_status="approved", usage_role="b_roll")

    def analyze(path, segments, fingerprint):
        repo.update_segment("workspace", asset.id, segment_id, description="用户纠正：这是打印机", transcript="HDMI",
                            review_status="approved", usage_role="b_roll", user_tags=["我的确认"])
        return {segment_id: SegmentUnderstanding("ready", summary="笔记本电脑", facts=[{"kind": "object", "text": "笔记本电脑", "sample_times": [0.3]}])}

    repo.visual_understanding = SimpleNamespace(analyze=analyze)
    result = repo.analyze_asset("workspace", asset.id)
    assert result.segments[0].description == "用户纠正：这是打印机"
    assert result.segments[0].transcript == "HDMI"
    assert result.segments[0].user_tags == ["我的确认"]
    assert "主体:笔记本电脑" not in result.segments[0].system_tags
    assert repo.list_assets_page("workspace", limit=20, offset=0, query="笔记本")[1] == 0
    assert repo.list_assets_page("workspace", limit=20, offset=0, query="打印机")[1] == 1
    assert result.segments[0].understanding.summary == "笔记本电脑"
    restored = repo.get_asset("workspace", asset.id)
    assert asdict(restored.segments[0].understanding) == asdict(result.segments[0].understanding)


def test_vlm_catalog_does_not_reuse_the_existing_tts_runtime(tmp_path):
    shared = tmp_path / "shared-python"
    shared.write_text("python")
    dedicated = tmp_path / "vlm-python"
    catalog = ModelCatalog(tmp_path, shared, None, understanding_runtime_python_path=dedicated)
    status = catalog.status(QWEN3_VL_4B_4BIT)
    assert status.state == "missing_runtime"
    assert status.runtime_python_path == str(dedicated)


def test_low_confidence_ocr_is_retained_but_not_searchable(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "source.mp4").write_bytes(b"fixture")
    repo = ProductionRepository(tmp_path / "db.sqlite", library, duration_reader=lambda path: 2, scene_reader=lambda path, duration: [])
    repo.bind_material_ingestion("workspace", expected_directory=str(library))
    repo.confirm_material_copy_complete(
        "workspace", relative_path="source.mp4",
        expected_revision=repo.material_copy_revision((library / "source.mp4").stat()),
        confirmation_source="manual",
    )
    asset = repo.analyze_asset("workspace", repo.scan_library("workspace")[0].id)
    repo.update_asset_review("workspace", asset.id, review_status="approved", usage_role="b_roll")
    repo.update_segment("workspace", asset.id, asset.segments[0].id, description="", transcript="", review_status="approved", usage_role="b_roll")
    asset = repo.get_asset("workspace", asset.id)
    asset.segments[0].understanding = SegmentUnderstanding("ready", ocr_status="ready", ocr_frames=[{
        "id": "frame", "timestamp_seconds": 1, "width": 64, "height": 64, "observations": [
            {"text": "uncertain", "confidence": 0.3, "box": [0, 0, .5, .5]},
            {"text": "verifiedword", "confidence": 1.0, "box": [0, 0, .5, .5]},
        ]}])
    repo._save_asset(asset)
    repo.set_asset_visibility("workspace", asset.id, "shared")
    assert not repo.material_search.search_material_segments("workspace", query="uncertain")
    assert repo.list_assets_page("workspace", limit=20, offset=0, query="uncertain")[1] == 0
    assert repo.material_search.search_material_segments("workspace", query="verifiedword")
    from app.schemas import OCRTextResponse
    response = OCRTextResponse.model_validate(asset.segments[0].understanding.ocr_frames[0]["observations"][0])
    assert response.model_dump()["retrieval_eligible"] is False
    assert response.text == "uncertain"


def test_changed_source_during_analysis_cannot_publish_new_observations(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    source = library / "source.mp4"
    source.write_bytes(b"fixture")
    repo = ProductionRepository(tmp_path / "db.sqlite", library, duration_reader=lambda path: 2, scene_reader=lambda path, duration: [])
    repo.bind_material_ingestion("workspace", expected_directory=str(library))
    repo.confirm_material_copy_complete(
        "workspace", relative_path="source.mp4",
        expected_revision=repo.material_copy_revision((library / "source.mp4").stat()),
        confirmation_source="manual",
    )
    asset = repo.analyze_asset("workspace", repo.scan_library("workspace")[0].id)
    def analyze(path, segments, fingerprint):
        path.write_bytes(b"changed source content")
        return {segments[0].id: SegmentUnderstanding("ready", summary="过期观察")}
    repo.visual_understanding = SimpleNamespace(analyze=analyze)
    with pytest.raises(ValueError, match="原片发生变化"):
        repo.analyze_asset("workspace", asset.id)
    assert repo.get_asset("workspace", asset.id).segments[0].understanding is None


@pytest.mark.parametrize("description,transcript,filename,expected", [
    ("", "typing at a keyboard in an office", "office_laptop_typing.mp4", []),
    ("A keyboard on a desk", "", "typing.mp4", ["场景:办公", "物体:电脑"]),
    ("办公室里使用电脑打字", "", "clip.mp4", ["场景:办公", "物体:电脑", "动作:打字"]),
])
def test_system_visual_tags_do_not_promote_audio_or_filename_to_actions(description, transcript, filename, expected):
    from app.production import _infer_system_tags
    asset = SimpleNamespace(file_name=filename, usage_role="b_roll")
    segment = MaterialSegment(id="s", asset_id="a", start_seconds=0, end_seconds=4,
        description=description, transcript=transcript)
    assert _infer_system_tags(asset, segment) == ["用途:b_roll", *expected]


def test_short_observation_label_preserves_full_fact_and_real_sample_times():
    from app.visual_understanding import observed_system_tags, understanding_schema
    payload = {"summary": "人走过街道", "facts": [{"kind": "action", "label": "人物走路",
        "text": "戴眼镜的人从画面左侧走向街道右侧", "frames": [0, 2]}], "unknowns": ["身份不可确认"]}
    result = parse_understanding(payload, [1.5, 2.0, 2.5])
    assert result["facts"][0] == {"kind": "action", "label": "人物走路",
        "text": "戴眼镜的人从画面左侧走向街道右侧", "sample_times": [1.5, 2.5]}
    observation = SegmentUnderstanding("ready", **result)
    assert observed_system_tags(observation) == ["动作:人物走路"]
    assert "label" in understanding_schema(3)["properties"]["facts"]["items"]["required"]
    assert "start_ms" not in result["facts"][0]
    payload["facts"][0]["label"] = "长" * 33
    with pytest.raises(ValueError, match="32字"):
        parse_understanding(payload, [1.5, 2.0, 2.5])


def test_legacy_observation_is_not_rewritten_into_a_model_label():
    from app.visual_understanding import observed_system_tags
    payload = {"summary": "办公画面", "facts": [{"kind": "object", "text": "电脑", "frames": [0]}], "unknowns": []}
    parsed = parse_understanding(payload, [2.0])
    assert "label" not in parsed["facts"][0]
    observation = SegmentUnderstanding("ready", **parsed)
    assert observed_system_tags(observation) == ["主体:电脑"]
    observation.facts[0]["text"] = "长" * 33
    assert observed_system_tags(observation) == []
