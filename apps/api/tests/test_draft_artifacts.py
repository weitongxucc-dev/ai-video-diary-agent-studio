import json
from datetime import date
from pathlib import Path
from types import ModuleType

from PIL import Image

from app.asr import ASRTranscription, ASRTranscriptionError, SubtitleSegment
from app.config import Settings
from app.drafts import DraftWorkspace
from app.models import Episode, EpisodeStatus, ScriptPackage, ScriptSection


def test_draft_package_creates_script_subtitle_and_cover_artifacts(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    episode = Episode(
        id="ep_test",
        day_number=1,
        episode_date=date(2026, 5, 28),
        status=EpisodeStatus.SCRIPT_READY,
        script=ScriptPackage(
            id="script_1",
            hook="拍照识别卡路里的软件，为什么很难活下去？",
            sections=[
                ScriptSection(
                    id="s1",
                    title="原因一",
                    estimated_seconds=30,
                    content="用户不是每天都愿意拍照、等待识别、再修正结果。",
                ),
                ScriptSection(
                    id="s2",
                    title="原因二",
                    estimated_seconds=35,
                    content="识别准确率一旦不稳定，用户就会回到更轻的记录方式。",
                ),
            ],
            title_options=["拍照识别卡路里，为什么难生存？"],
            cover_copy="AI视频日记 第1天\n卡路里识别软件难生存的3个原因",
            publish_copy="今天聊一个AI健康产品的真实难题。",
            comment_prompt="你会每天拍照记录饮食吗？",
        ),
    )
    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output))
    )

    package = workspace.create_package("talk_preview.mp4", episode)

    package_dir = draft_output / package.package_name
    subtitle_path = package_dir / "subtitle.srt"
    cover_path = package_dir / "cover.png"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))

    assert subtitle_path.exists()
    assert "拍照识别卡路里的软件" in subtitle_path.read_text(encoding="utf-8")
    assert manifest["artifacts"]["subtitle_source"] == "script_estimate"
    assert manifest["artifacts"]["asr_status"] == "not_configured"
    assert manifest["artifacts"]["subtitle_path"] == str(subtitle_path)
    assert manifest["artifacts"]["cover_path"] == str(cover_path)
    with Image.open(cover_path) as cover:
        assert cover.size == (1200, 900)


class FakeASRTranscriber:
    configured = True

    def __init__(self) -> None:
        self.audio_path = None

    def transcribe(self, audio_path):
        self.audio_path = audio_path
        return ASRTranscription(
            provider="tencentcloud",
            text="这是腾讯云识别出来的真实字幕。",
            segments=[SubtitleSegment(0, 2400, "这是腾讯云识别出来的真实字幕。")],
        )


class FailingASRTranscriber:
    configured = True

    def transcribe(self, audio_path):
        raise ASRTranscriptionError("Resource pack exhausted")


def test_draft_package_prefers_tencent_asr_subtitles_when_configured(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"audio")

    transcriber = FakeASRTranscriber()
    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output)),
        asr_transcriber=transcriber,
        runner=runner,
    )
    episode = Episode(
        id="ep_test",
        day_number=1,
        episode_date=date(2026, 5, 28),
        status=EpisodeStatus.SCRIPT_READY,
        script=ScriptPackage(
            id="script_1",
            hook="脚本备用字幕",
            sections=[
                ScriptSection("s1", "备用", 10, "这里不应该优先使用脚本字幕。")
            ],
            title_options=["标题"],
            cover_copy="AI视频日记 第1天\n腾讯云ASR",
            publish_copy="发布文案",
            comment_prompt="评论引导",
        ),
    )

    package = workspace.create_package("talk_preview.mp4", episode)

    package_dir = draft_output / package.package_name
    subtitle = (package_dir / "subtitle.srt").read_text(encoding="utf-8")
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert calls[0][0] == "ffmpeg"
    assert str(package_dir / "audio_16k.wav") in calls[0]
    assert transcriber.audio_path == package_dir / "audio_16k.wav"
    assert "腾讯云识别出来的真实字幕" in subtitle
    assert "这里不应该优先使用脚本字幕" not in subtitle
    assert manifest["artifacts"]["subtitle_source"] == "tencentcloud_asr"
    assert manifest["artifacts"]["asr_status"] == "succeeded"


def test_draft_package_falls_back_to_script_when_tencent_asr_fails(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"audio")

    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output)),
        asr_transcriber=FailingASRTranscriber(),
        runner=runner,
    )
    episode = Episode(
        id="ep_test",
        day_number=1,
        episode_date=date(2026, 5, 28),
        status=EpisodeStatus.SCRIPT_READY,
        script=ScriptPackage(
            id="script_1",
            hook="脚本备用字幕",
            sections=[ScriptSection("s1", "备用", 10, "ASR失败时应该保留这句。")],
            title_options=["标题"],
            cover_copy="AI视频日记 第1天\n腾讯云ASR失败兜底",
            publish_copy="发布文案",
            comment_prompt="评论引导",
        ),
    )

    package = workspace.create_package("talk_preview.mp4", episode)

    package_dir = draft_output / package.package_name
    subtitle = (package_dir / "subtitle.srt").read_text(encoding="utf-8")
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert calls[0][0] == "ffmpeg"
    assert "ASR失败时应该保留这句" in subtitle
    assert manifest["artifacts"]["subtitle_source"] == "script_estimate"
    assert manifest["artifacts"]["asr_status"] == "failed"
    assert "Resource pack exhausted" in manifest["artifacts"]["asr_error"]


def test_ad_hoc_draft_does_not_invent_script_subtitles_when_asr_fails(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    calls = []

    def runner(command: list[str]) -> None:
        calls.append(command)
        Path(command[-1]).write_bytes(b"audio")

    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output)),
        asr_transcriber=FailingASRTranscriber(),
        runner=runner,
    )
    episode = Episode(
        id="ep_ad_hoc",
        day_number=2,
        episode_date=date(2026, 5, 29),
        status=EpisodeStatus.VIDEO_PROCESSING,
    )
    episode.intake_mode = "ad_hoc_single"

    package = workspace.create_package("talk_preview.mp4", episode)

    package_dir = draft_output / package.package_name
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert calls[0][0] == "ffmpeg"
    assert not (package_dir / "subtitle.srt").exists()
    assert (package_dir / "cover.png").exists()
    assert package.subtitle_path is None
    assert package.cover_path == str(package_dir / "cover.png")
    assert manifest["source_mode"] == "ad_hoc_single"
    assert manifest["artifacts"]["subtitle_path"] is None
    assert manifest["artifacts"]["subtitle_source"] == "none"
    assert manifest["artifacts"]["asr_status"] == "failed"
    assert "Resource pack exhausted" in manifest["artifacts"]["asr_error"]


def test_draft_environment_reports_jianying_10_7_bridge_support(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    jianying_root = tmp_path / "com.lveditor.draft"
    encrypted_draft = jianying_root / "5月28日"
    encrypted_draft.mkdir(parents=True)
    (encrypted_draft / "draft_info.json").write_text("{}", encoding="utf-8")
    (encrypted_draft / "crypto_key_store.dat").write_bytes(b"encrypted")

    workspace = DraftWorkspace(
        Settings(
            video_output_path=str(video_output),
            draft_output_path=str(draft_output),
            jianying_draft_root_path=str(jianying_root),
        )
    )

    environment = workspace.environment()

    assert environment.jianying_root_exists is True
    assert environment.jianying_10_7_detected is True
    assert environment.editable_draft_supported is True
    assert environment.jianying_blocker is None


def test_draft_import_uses_bridge_when_jianying_10_7_is_detected(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    jianying_root = tmp_path / "com.lveditor.draft"
    encrypted_draft = jianying_root / "5月28日"
    video_output.mkdir()
    encrypted_draft.mkdir(parents=True)
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    (encrypted_draft / "draft_info.json").write_text("{}", encoding="utf-8")
    (encrypted_draft / "crypto_key_store.dat").write_bytes(b"encrypted")
    imported_paths: list[Path] = []

    def legacy_writer(package_dir: Path, draft_root: Path, manifest: dict) -> Path:
        draft_path = draft_root / manifest["package_name"]
        draft_path.mkdir()
        (draft_path / "draft_content.json").write_text("{}", encoding="utf-8")
        (draft_path / "draft_info.json").write_text("{}", encoding="utf-8")
        imported_paths.append(draft_path)
        return draft_path

    workspace = DraftWorkspace(
        Settings(
            video_output_path=str(video_output),
            draft_output_path=str(draft_output),
            jianying_draft_root_path=str(jianying_root),
        ),
        legacy_draft_writer=legacy_writer,
    )
    package = workspace.create_package("talk_preview.mp4")

    result = workspace.import_package_to_jianying(package.package_name)

    manifest = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
    assert result.status == "imported"
    assert manifest["jianying_import"]["status"] == "imported"


def test_draft_import_uses_legacy_writer_when_supported(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    jianying_root = tmp_path / "legacy_drafts"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")
    imported_paths: list[Path] = []

    def legacy_writer(package_dir: Path, draft_root: Path, manifest: dict) -> Path:
        draft_path = draft_root / manifest["package_name"]
        draft_path.mkdir()
        (draft_path / "draft_content.json").write_text("{}", encoding="utf-8")
        imported_paths.append(draft_path)
        return draft_path

    workspace = DraftWorkspace(
        Settings(
            video_output_path=str(video_output),
            draft_output_path=str(draft_output),
            jianying_draft_root_path=str(jianying_root),
        ),
        legacy_draft_writer=legacy_writer,
    )
    package = workspace.create_package("talk_preview.mp4")

    result = workspace.import_package_to_jianying(package.package_name)

    manifest = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
    assert result.status == "imported"
    assert result.draft_path == str(imported_paths[0])
    assert manifest["jianying_import"]["status"] == "imported"
    assert manifest["jianying_import"]["draft_path"] == str(imported_paths[0])


def test_legacy_draft_finalization_writes_macos_draft_info_and_cover(tmp_path):
    draft_path = tmp_path / "draft"
    draft_path.mkdir()
    cover_path = tmp_path / "cover.png"
    Image.new("RGB", (20, 20), "#2563eb").save(cover_path)
    (draft_path / "draft_content.json").write_text(
        json.dumps(
            {
                "name": "old",
                "path": "",
                "duration": 999_000_000,
                "platform": {"app_source": "lv", "app_version": "5.9.0", "os": "windows"},
                "materials": {},
                "tracks": [
                    {
                        "segments": [
                            {"target_timerange": {"start": 0, "duration": 1_234_000}}
                        ]
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (draft_path / "draft_meta_info.json").write_text(
        json.dumps({"draft_name": "old", "tm_duration": 0}),
        encoding="utf-8",
    )
    workspace = DraftWorkspace(Settings())

    workspace._finalize_legacy_draft_files(
        draft_path,
        {
            "package_name": "AI视频日记_测试草稿",
            "artifacts": {"cover_path": str(cover_path)},
        },
    )

    draft_info = json.loads((draft_path / "draft_info.json").read_text(encoding="utf-8"))
    draft_content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    assert draft_info == draft_content
    assert draft_info["name"] == "AI视频日记_测试草稿"
    assert draft_info["path"] == str(draft_path)
    assert draft_info["duration"] == 1_234_000
    assert draft_info["platform"]["os"] == "mac"
    assert (draft_path / "draft_cover.jpg").exists()
    draft_meta = json.loads((draft_path / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert draft_meta["tm_duration"] == 1_234_000
    assert draft_meta["draft_json_file"] == str(draft_path / "draft_info.json")


def test_legacy_draft_writer_caps_segment_to_material_duration(tmp_path, monkeypatch):
    video_path = tmp_path / "talk_preview.mp4"
    video_path.write_bytes(b"video")
    draft_root = tmp_path / "jianying"
    captured = {}

    class FakeDraftFolder:
        def __init__(self, root: str) -> None:
            self.root = Path(root)

        def create_draft(self, name, width, height, fps, allow_replace):
            return FakeScript(self.root, name)

    class FakeTrackType:
        video = "video"

    class FakeVideoMaterial:
        def __init__(self, path: str) -> None:
            self.path = path
            self.duration = 264_134_000

    class FakeVideoSegment:
        def __init__(self, material, timerange) -> None:
            self.material = material
            self.timerange = timerange

    class FakeScript:
        def __init__(self, root: Path, name: str) -> None:
            self.root = root
            self.name = name

        def add_track(self, track_type) -> None:
            captured["track_type"] = track_type

        def add_segment(self, segment) -> None:
            captured["segment"] = segment

        def import_srt(self, subtitle_path, track_name) -> None:
            captured["subtitle"] = (subtitle_path, track_name)

        def save(self) -> None:
            draft_path = self.root / self.name
            draft_path.mkdir(parents=True)
            (draft_path / "draft_content.json").write_text(
                json.dumps(
                    {
                        "duration": 999_000_000,
                        "materials": {},
                        "tracks": [
                            {
                                "segments": [
                                    {
                                        "target_timerange": {
                                            "start": 0,
                                            "duration": captured["trange"][1],
                                        }
                                    }
                                ]
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (draft_path / "draft_meta_info.json").write_text("{}", encoding="utf-8")

    def fake_trange(start: int, duration: int):
        captured["trange"] = (start, duration)
        return {"start": start, "duration": duration}

    fake_module = ModuleType("pyJianYingDraft")
    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = FakeTrackType
    fake_module.VideoMaterial = FakeVideoMaterial
    fake_module.VideoSegment = FakeVideoSegment
    fake_module.trange = fake_trange
    monkeypatch.setitem(__import__("sys").modules, "pyJianYingDraft", fake_module)

    workspace = DraftWorkspace(Settings())
    monkeypatch.setattr(
        workspace,
        "_video_duration_microseconds",
        lambda path: 264_200_000,
    )

    workspace._write_legacy_jianying_draft(
        tmp_path / "package",
        draft_root,
        {
            "package_name": "AI视频日记_测试",
            "video": {"path": str(video_path)},
            "artifacts": {},
        },
    )

    assert captured["trange"] == (0, 264_134_000)


def test_video_duration_probe_prefers_ffprobe_output(tmp_path, monkeypatch):
    video_path = tmp_path / "talk_preview.mp4"
    video_path.write_bytes(b"video")
    workspace = DraftWorkspace(Settings())

    class Completed:
        stdout = "1.234000\n"

    def fake_run(command, check, capture_output, text):
        assert command[0] == "ffprobe"
        assert str(video_path) in command
        assert check is True
        assert capture_output is True
        assert text is True
        return Completed()

    monkeypatch.setattr("app.drafts.subprocess.run", fake_run)

    assert workspace._video_duration_microseconds(video_path) == 1_234_000


def test_script_estimate_subtitles_are_capped_to_video_duration():
    workspace = DraftWorkspace(Settings())
    script = ScriptPackage(
        id="script_1",
        hook="开头钩子",
        sections=[ScriptSection("s1", "一", 30, "这是一段会被估算成比较长时间的字幕内容。")],
        title_options=["标题"],
        cover_copy="封面",
        publish_copy="发布",
        comment_prompt="评论",
    )

    subtitle = workspace._script_to_srt(script, max_duration_ms=1_000)

    assert "00:00:01,000" in subtitle
    assert "00:00:30" not in subtitle


def test_sync_jianying_root_meta_updates_draft_card_duration(tmp_path):
    jianying_root = tmp_path / "com.lveditor.draft"
    draft_path = jianying_root / "AI视频日记_测试"
    draft_path.mkdir(parents=True)
    (draft_path / "draft_info.json").write_text(
        json.dumps({"duration": 1_000_000, "tracks": []}),
        encoding="utf-8",
    )
    (jianying_root / "root_meta_info.json").write_text(
        json.dumps(
            {
                "all_draft_store": [
                    {
                        "draft_name": "AI视频日记_测试",
                        "draft_fold_path": str(draft_path),
                        "draft_json_file": "",
                        "draft_root_path": "",
                        "tm_duration": 186_000_000,
                    }
                ],
                "draft_ids": 1,
                "root_path": str(jianying_root),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))

    workspace._sync_jianying_root_meta(draft_path)

    root_meta = json.loads((jianying_root / "root_meta_info.json").read_text(encoding="utf-8"))
    entry = root_meta["all_draft_store"][0]
    assert entry["tm_duration"] == 1_000_000
    assert entry["draft_json_file"] == str(draft_path / "draft_info.json")
    assert entry["draft_root_path"] == str(jianying_root)


def test_sync_jianying_root_meta_creates_missing_draft_card_entry(tmp_path):
    jianying_root = tmp_path / "com.lveditor.draft"
    draft_path = jianying_root / "AI视频日记_新增"
    draft_path.mkdir(parents=True)
    video_path = tmp_path / "talk_preview.mp4"
    video_path.write_bytes(b"video")
    (draft_path / "draft_info.json").write_text(
        json.dumps(
            {
                "id": "draft-id",
                "duration": 1_000_000,
                "materials": {"videos": [{"path": str(video_path)}]},
                "tracks": [],
            }
        ),
        encoding="utf-8",
    )
    (jianying_root / "root_meta_info.json").write_text(
        json.dumps({"all_draft_store": [], "draft_ids": 0, "root_path": str(jianying_root)}),
        encoding="utf-8",
    )
    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))

    workspace._sync_jianying_root_meta(draft_path)

    root_meta = json.loads((jianying_root / "root_meta_info.json").read_text(encoding="utf-8"))
    assert root_meta["draft_ids"] == 1
    entry = root_meta["all_draft_store"][0]
    assert entry["draft_name"] == "AI视频日记_新增"
    assert entry["draft_id"] == "draft-id"
    assert entry["tm_duration"] == 1_000_000
    assert entry["draft_timeline_materials_size"] == len(b"video")


# ── Native Jianying draft writer ──────────────────────────


def test_native_writer_creates_valid_draft_content_json(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")

    subtitle_srt = tmp_path / "subtitle.srt"
    subtitle_srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n第一行字幕\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n第二行字幕内容\n",
        encoding="utf-8",
    )

    manifest = {
        "package_name": "AI视频日记_原生测试",
        "video": {"path": str(video_output / "talk_preview.mp4")},
        "artifacts": {"subtitle_path": str(subtitle_srt)},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))

    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    # Draft directory exists at the target path
    assert draft_path.exists()
    assert draft_path == jianying_root / "AI视频日记_原生测试"

    # draft_content.json and draft_info.json both exist and are identical
    content_json = draft_path / "draft_content.json"
    info_json = draft_path / "draft_info.json"
    assert content_json.exists()
    assert info_json.exists()
    content = json.loads(content_json.read_text(encoding="utf-8"))
    info = json.loads(info_json.read_text(encoding="utf-8"))
    assert content == info

    # Top-level structure
    assert content["name"] == "AI视频日记_原生测试"
    assert content["platform"]["os"] == "mac"
    assert content["platform"]["app_id"] == 3704
    assert content["new_version"] == "1"
    assert isinstance(content["duration"], int) and content["duration"] > 0

    # Video material and track
    materials = content["materials"]
    assert len(materials["videos"]) == 1
    assert materials["videos"][0]["type"] == "video"
    video_material_id = materials["videos"][0]["id"]

    tracks = content["tracks"]
    assert len(tracks) >= 2  # video + subtitle tracks
    video_track = tracks[0]
    assert video_track["type"] == "video"
    assert video_track["segments"][0]["material_id"] == video_material_id

    # Subtitle track with text materials
    subtitle_track = tracks[1]
    assert subtitle_track["type"] == "subtitle"
    assert len(subtitle_track["segments"]) == 2
    assert len(materials["texts"]) == 2

    # Each subtitle segment references a text material
    for segment in subtitle_track["segments"]:
        assert "material_id" in segment
        assert "content" in segment
        assert "target_timerange" in segment
        # Verify the material_id points to an existing text material
        text_ids = {tm["id"] for tm in materials["texts"]}
        assert segment["material_id"] in text_ids

    # Text materials have required fields
    for tm in materials["texts"]:
        assert tm["type"] == "text"
        assert "id" in tm
        assert "content" in tm


def test_native_writer_atomic_move_cleans_up_on_failure(tmp_path):
    """If writing fails mid-way, no partial draft directory is left behind."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")

    manifest = {
        "package_name": "AI视频日记_失败清理测试",
        "video": {"path": str(video_output / "talk_preview.mp4")},
        "artifacts": {},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))

    # Make shutil.move fail after writing files
    import shutil as shutil_mod

    original_move = shutil_mod.move

    def failing_move(src, dst, **kwargs):
        raise OSError("simulated disk full")

    # Patch move to fail — the temp dir should still be cleaned up
    try:
        shutil_mod.move = failing_move
        workspace._write_native_jianying_draft(
            tmp_path / "pkg", jianying_root, manifest
        )
    except OSError:
        pass
    finally:
        shutil_mod.move = original_move

    # The target path must NOT exist (no partial draft left behind)
    target = jianying_root / "AI视频日记_失败清理测试"
    assert not target.exists(), (
        f"Partial draft left behind at {target} — temp dir was not cleaned up"
    )


def test_native_writer_retry_after_failure_succeeds(tmp_path):
    """After a failed import, retrying with the same package name succeeds."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk_preview.mp4").write_bytes(b"processed video")

    manifest = {
        "package_name": "AI视频日记_重试测试",
        "video": {"path": str(video_output / "talk_preview.mp4")},
        "artifacts": {},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))

    # First write should succeed
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )
    assert draft_path.exists()

    # Second write with same name should fail with FileExistsError
    try:
        workspace._write_native_jianying_draft(tmp_path / "pkg", jianying_root, manifest)
        raise AssertionError("Expected FileExistsError")
    except FileExistsError:
        pass  # expected — no silent overwrite


def test_srt_to_jianying_segments_includes_text_materials(tmp_path):
    srt_path = tmp_path / "test.srt"
    srt_path.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\n第一段字幕\n\n"
        "2\n00:00:04,000 --> 00:00:07,200\n第二段字幕\n",
        encoding="utf-8",
    )

    workspace = DraftWorkspace(Settings())
    segments, text_materials = workspace._srt_to_jianying_segments(srt_path)

    assert len(segments) == 2
    assert len(text_materials) == 2

    for i, segment in enumerate(segments):
        # Every segment has a material_id referencing its text material
        assert "material_id" in segment
        assert segment["material_id"] == text_materials[i]["id"]
        assert "content" in segment
        assert "target_timerange" in segment

    assert text_materials[0]["content"] == "第一段字幕"
    assert text_materials[0]["type"] == "text"
    assert text_materials[1]["content"] == "第二段字幕"


# ── Template tracks ────────────────────────────────────────────────────

def test_template_tracks_title_card_added_for_video_diary(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "template_title_test",
        "video": {"path": str(video_output / "talk.mp4")},
        "content_template": {
            "template_id": "video_diary",
            "name": "视频日记",
            "script_structure": ["开头", "主体", "结尾"],
        },
        "script_sections": [
            {"index": 0, "label": "开头", "section_id": "s1", "section_title": "Hook", "estimated_seconds": 5},
            {"index": 1, "label": "主体", "section_id": "s2", "section_title": "Body", "estimated_seconds": 30},
        ],
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    tracks = content["tracks"]

    # Track 0 = video, Track 1+ = text tracks (title + chapter)
    text_tracks = [t for t in tracks if t["type"] == "text"]
    assert len(text_tracks) >= 2, f"expected >=2 text tracks, got {len(text_tracks)}"

    # First text track should be the title card
    title_track = text_tracks[0]
    title_content = title_track["segments"][0]["content"]
    assert title_content == "视频日记"

    # Chapter track should contain at least 1 chapter label
    chapter_track = text_tracks[1]
    chapter_labels = [s["content"] for s in chapter_track["segments"]]
    assert "开头" in chapter_labels, f"chapter labels: {chapter_labels}"

    # metadata recorded
    assert "title_card:video_diary" in workspace._last_template_tracks_meta
    assert "chapter_labels" in workspace._last_template_tracks_meta


def test_template_tracks_no_title_or_chapters_without_data(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    # No content_template or script_sections in manifest
    manifest = {
        "package_name": "no_template",
        "video": {"path": str(video_output / "talk.mp4")},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    # Should only have video track (no template data → no text tracks)
    text_tracks = [t for t in content["tracks"] if t["type"] == "text"]
    assert len(text_tracks) == 0, f"expected 0 text tracks without template, got {len(text_tracks)}"


def test_template_tracks_background_music_added_when_configured(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    music_file = tmp_path / "bgm.mp3"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")
    music_file.write_bytes(b"fake music data")

    manifest = {
        "package_name": "music_test",
        "video": {"path": str(video_output / "talk.mp4")},
    }

    workspace = DraftWorkspace(
        Settings(
            jianying_draft_root_path=str(jianying_root),
            draft_background_music_path=str(music_file),
        )
    )
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    # Should have an audio track
    audio_tracks = [t for t in content["tracks"] if t["type"] == "audio"]
    assert len(audio_tracks) == 1
    assert len(content["materials"]["audios"]) == 1
    assert "background_music" in workspace._last_template_tracks_meta


def test_template_tracks_background_music_skipped_when_not_configured(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "no_music",
        "video": {"path": str(video_output / "talk.mp4")},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    audio_tracks = [t for t in content["tracks"] if t["type"] == "audio"]
    assert len(audio_tracks) == 0
    assert "background_music" not in workspace._last_template_tracks_meta


def test_template_tracks_background_music_skipped_when_file_missing(tmp_path):
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "missing_music",
        "video": {"path": str(video_output / "talk.mp4")},
    }

    workspace = DraftWorkspace(
        Settings(
            jianying_draft_root_path=str(jianying_root),
            draft_background_music_path="/nonexistent/bgm.mp3",
        )
    )
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    audio_tracks = [t for t in content["tracks"] if t["type"] == "audio"]
    assert len(audio_tracks) == 0, "should skip music when file missing"


def test_draft_content_json_includes_template_metadata_on_manifest(tmp_path):
    """Verify import result includes template_tracks in manifest jianying_import."""
    from app.config import Settings
    from app.drafts import DraftWorkspace

    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    workspace = DraftWorkspace(Settings(
        video_output_path=str(video_output),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(jianying_root),
        database_path=str(tmp_path / "db.sqlite"),
    ))

    # Create package with episode=None (no template data)
    # First create a minimal manifest manually
    (tmp_path / "drafts").mkdir(exist_ok=True)
    pkg_dir = tmp_path / "drafts" / "test_pkg"
    pkg_dir.mkdir(exist_ok=True)
    manifest_data = {
        "created_at": "2025-01-01T00:00:00",
        "package_name": "test_pkg",
        "source_mode": "standalone_video",
        "video": {
            "file_name": "talk.mp4",
            "path": str(video_output / "talk.mp4"),
            "size_bytes": 5,
            "modified_at": "2025-01-01T00:00:00",
        },
        "episode": None,
        "content_template": {
            "template_id": "video_diary",
            "name": "视频日记",
            "script_structure": ["开头", "主体", "结尾"],
        },
        "script_sections": [
            {"index": 0, "label": "开头", "section_id": "s1", "section_title": "Hook", "estimated_seconds": 5},
        ],
        "artifacts": {
            "subtitle_path": None,
            "cover_path": None,
        },
        "draft_tool": {},
        "jianying_import": {},
        "status": "ready",
    }
    (pkg_dir / "manifest.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8"
    )

    result = workspace.import_package_to_jianying("test_pkg")
    assert result.status == "imported"

    # Re-read manifest to verify template_tracks was recorded
    updated = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    jy_import = updated.get("jianying_import", {})
    assert isinstance(jy_import, dict)
    # template_tracks should list what was generated
    template_tracks = jy_import.get("template_tracks", [])
    assert "title_card:video_diary" in template_tracks
    assert "chapter_labels" in template_tracks
    # With no default_feedback_dimensions, keyword emphasis is absent
    assert "keyword_emphasis:none" in template_tracks


# ── Keyword emphasis ────────────────────────────────────────────────────

def test_keyword_emphasis_added_from_template_dimensions(tmp_path):
    """Keywords derived from content_template.default_feedback_dimensions
    produce a styled text track."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "keyword_test",
        "video": {"path": str(video_output / "talk.mp4")},
        "content_template": {
            "template_id": "video_diary",
            "name": "视频日记",
            "default_feedback_dimensions": ["真实感", "开头速度", "观点清晰度", "行动建议"],
        },
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    text_tracks = [t for t in content["tracks"] if t["type"] == "text"]

    # Should have at least one text track with keyword segments
    all_segment_contents = []
    for track in text_tracks:
        for seg in track["segments"]:
            all_segment_contents.append(seg.get("content", ""))

    assert "真实感" in all_segment_contents
    assert "观点清晰度" in all_segment_contents
    assert "keyword_emphasis" in workspace._last_template_tracks_meta

    # Keyword segments should use accent colour
    keyword_track = text_tracks[-1]  # last text track
    assert keyword_track["segments"][0]["style"]["color"] == "#FF6B35"


def test_keyword_emphasis_caps_at_four_keywords(tmp_path):
    """Only the first 4 dimension labels become keywords."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "keyword_cap_test",
        "video": {"path": str(video_output / "talk.mp4")},
        "content_template": {
            "template_id": "video_diary",
            "name": "视频日记",
            "default_feedback_dimensions": ["K1", "K2", "K3", "K4", "K5", "K6"],
        },
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    draft_path = workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
    # Last text track is keyword emphasis — should have exactly 4 segments
    text_tracks = [t for t in content["tracks"] if t["type"] == "text"]
    keyword_segments = [s for t in text_tracks for s in t["segments"]
                        if s.get("style", {}).get("color") == "#FF6B35"]
    assert len(keyword_segments) == 4


def test_keyword_emphasis_none_when_no_dimensions(tmp_path):
    """Explicit absence recorded when template has no dimensions."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "no_keywords",
        "video": {"path": str(video_output / "talk.mp4")},
        "content_template": {
            "template_id": "video_diary",
            "name": "视频日记",
            # No default_feedback_dimensions
        },
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    assert "keyword_emphasis:none" in workspace._last_template_tracks_meta


def test_keyword_emphasis_none_when_no_template(tmp_path):
    """Explicit absence recorded when no template at all."""
    video_output = tmp_path / "processed"
    jianying_root = tmp_path / "com.lveditor.draft"
    video_output.mkdir()
    jianying_root.mkdir()
    (video_output / "talk.mp4").write_bytes(b"video")

    manifest = {
        "package_name": "no_template",
        "video": {"path": str(video_output / "talk.mp4")},
    }

    workspace = DraftWorkspace(Settings(jianying_draft_root_path=str(jianying_root)))
    workspace._write_native_jianying_draft(
        tmp_path / "pkg", jianying_root, manifest
    )

    assert "keyword_emphasis:none" in workspace._last_template_tracks_meta


def test_safe_keywords_sanitises_inputs():
    """_safe_keywords strips whitespace, truncates, skips empty and non-string."""
    workspace = DraftWorkspace(Settings())

    # Happy path
    assert workspace._safe_keywords({
        "default_feedback_dimensions": ["真实感", "开头速度"]
    }) == ["真实感", "开头速度"]

    # Whitespace-only skipped
    assert workspace._safe_keywords({
        "default_feedback_dimensions": ["   ", "OK"]
    }) == ["OK"]

    # Truncate to 20 chars
    long_kw = "A" * 30
    result = workspace._safe_keywords({
        "default_feedback_dimensions": [long_kw]
    })
    assert result == ["A" * 20]

    # Non-string items skipped
    assert workspace._safe_keywords({
        "default_feedback_dimensions": ["A", 123, None, "B"]
    }) == ["A", "B"]

    # None template → empty
    assert workspace._safe_keywords(None) == []

    # Missing key → empty
    assert workspace._safe_keywords({"name": "test"}) == []


# ── Template tracks in DraftAsset / manifest ────────────────────────────


def test_package_asset_includes_template_tracks_from_manifest(tmp_path):
    """DraftAsset.template_tracks is populated from manifest jianying_import."""
    from app.config import Settings
    from app.drafts import DraftWorkspace

    pkg_dir = tmp_path / "test_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "video.mp4").write_text("video")
    manifest = {
        "package_name": "test_pkg",
        "video": {"path": str(pkg_dir / "video.mp4")},
        "jianying_import": {
            "status": "imported",
            "draft_path": "/tmp/draft",
            "template_tracks": ["title_card:video_diary", "chapter_labels", "keyword_emphasis"],
        },
    }
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    workspace = DraftWorkspace(Settings(
        draft_output_path=str(tmp_path),
        database_path=str(tmp_path / "db.sqlite"),
    ))

    asset = workspace._package_asset_from_path(pkg_dir)
    assert asset.template_tracks == ["title_card:video_diary", "chapter_labels", "keyword_emphasis"]


def test_package_asset_template_tracks_none_when_no_manifest(tmp_path):
    """DraftAsset.template_tracks is None when no manifest exists."""
    from app.config import Settings
    from app.drafts import DraftWorkspace

    pkg_dir = tmp_path / "test_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "video.mp4").write_text("video")

    workspace = DraftWorkspace(Settings(
        draft_output_path=str(tmp_path),
        database_path=str(tmp_path / "db.sqlite"),
    ))

    asset = workspace._package_asset_from_path(pkg_dir)
    assert asset.template_tracks is None


def test_package_asset_template_tracks_none_when_no_jianying_import(tmp_path):
    """DraftAsset.template_tracks is None for old packages without jianying_import."""
    from app.config import Settings
    from app.drafts import DraftWorkspace

    pkg_dir = tmp_path / "test_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "video.mp4").write_text("video")
    manifest = {
        "package_name": "test_pkg",
        "video": {"path": str(pkg_dir / "video.mp4")},
        "status": "ready",
    }
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    workspace = DraftWorkspace(Settings(
        draft_output_path=str(tmp_path),
        database_path=str(tmp_path / "db.sqlite"),
    ))

    asset = workspace._package_asset_from_path(pkg_dir)
    assert asset.template_tracks is None


def test_package_asset_template_tracks_none_when_missing_key(tmp_path):
    """DraftAsset.template_tracks is None when jianying_import has no template_tracks."""
    from app.config import Settings
    from app.drafts import DraftWorkspace

    pkg_dir = tmp_path / "test_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "video.mp4").write_text("video")
    manifest = {
        "package_name": "test_pkg",
        "video": {"path": str(pkg_dir / "video.mp4")},
        "jianying_import": {
            "status": "imported",
            "draft_path": "/tmp/draft",
        },
    }
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    workspace = DraftWorkspace(Settings(
        draft_output_path=str(tmp_path),
        database_path=str(tmp_path / "db.sqlite"),
    ))

    asset = workspace._package_asset_from_path(pkg_dir)
    assert asset.template_tracks is None


def test_safe_template_tracks_from_manifest_unit():
    """_safe_template_tracks_from_manifest returns list or None safely."""
    from app.drafts import _safe_template_tracks_from_manifest

    # Happy path
    assert _safe_template_tracks_from_manifest({
        "jianying_import": {"template_tracks": ["a", "b"]}
    }) == ["a", "b"]

    # Empty list → None
    assert _safe_template_tracks_from_manifest({
        "jianying_import": {"template_tracks": []}
    }) is None

    # Non-list value → None
    assert _safe_template_tracks_from_manifest({
        "jianying_import": {"template_tracks": "not_a_list"}
    }) is None

    # Missing key → None
    assert _safe_template_tracks_from_manifest({
        "jianying_import": {}
    }) is None

    # No jianying_import → None
    assert _safe_template_tracks_from_manifest({}) is None

    # jianying_import is not a dict → None
    assert _safe_template_tracks_from_manifest({
        "jianying_import": ["list"]
    }) is None

    # Filters non-string items
    assert _safe_template_tracks_from_manifest({
        "jianying_import": {"template_tracks": ["ok", 123, "ok2", None]}
    }) == ["ok", "ok2"]
