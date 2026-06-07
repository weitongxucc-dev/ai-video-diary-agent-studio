from datetime import date
import json
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


def test_draft_package_manifest_includes_template_track_preview(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "tool_review.mp4").write_bytes(b"processed video")
    episode = Episode(
        id="ep_tool_review",
        day_number=3,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        content_template_id="tool_review",
        script=ScriptPackage(
            id="script_tool_review",
            hook="结论先讲，这个 AI 工具能省粗剪时间，但不能替代审美判断。",
            sections=[
                ScriptSection("s1", "结论", 20, "适合标准化口播，不适合复杂叙事。"),
                ScriptSection("s2", "实测", 40, "字幕、封面和 B-roll 插入点都需要可检查。"),
            ],
            title_options=["AI 粗剪工具，真实省时间吗？"],
            cover_copy="AI粗剪工具\n真实测评",
            publish_copy="今天测一个真实粗剪工作流。",
            comment_prompt="你会把粗剪交给 AI 吗？",
        ),
    )
    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output))
    )

    package = workspace.create_package("tool_review.mp4", episode)

    manifest = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
    template_effects = manifest["template_effects"]
    assert template_effects["content_template_id"] == "tool_review"
    assert template_effects["template_id"] == "tool_review"
    assert [track["label"] for track in template_effects["tracks"]] == [
        "主视频",
        "字幕",
        "标题贴纸",
        "关键词高亮",
        "背景音乐",
        "封面",
    ]
    assert {effect["kind"] for effect in template_effects["effects"]} >= {
        "title_card",
        "chapter_bar",
        "keyword_highlight",
        "background_music",
    }
    assert template_effects["import_preview"][0]["track"] == "轨道 1：主视频"


def test_draft_package_status_exposes_template_preview(tmp_path):
    video_output = tmp_path / "processed"
    draft_output = tmp_path / "drafts"
    video_output.mkdir()
    (video_output / "tool_review.mp4").write_bytes(b"processed video")
    episode = Episode(
        id="ep_tool_review",
        day_number=3,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        content_template_id="tool_review",
        script=ScriptPackage(
            id="script_tool_review",
            hook="结论先讲，这个 AI 工具能省粗剪时间。",
            sections=[ScriptSection("s1", "结论", 20, "适合标准化口播。")],
            title_options=["AI 粗剪工具，真实省时间吗？"],
            cover_copy="AI粗剪工具\n真实测评",
            publish_copy="今天测一个真实粗剪工作流。",
            comment_prompt="你会把粗剪交给 AI 吗？",
        ),
    )
    workspace = DraftWorkspace(
        Settings(video_output_path=str(video_output), draft_output_path=str(draft_output))
    )

    workspace.create_package("tool_review.mp4", episode)
    package_asset = workspace.packages()[0]

    assert package_asset.template_id == "tool_review"
    assert package_asset.template_name == "AI 工具测评剪映模板"
    assert package_asset.track_count == 6
    assert package_asset.effect_count == 10
    assert package_asset.track_preview == [
        "轨道 1：主视频",
        "轨道 2：字幕",
        "轨道 3：标题贴纸",
        "轨道 4：关键词高亮",
        "轨道 5：背景音乐",
        "轨道 6：封面",
    ]
    assert "标题卡" in package_asset.effect_preview
    assert "背景音乐音量" in package_asset.effect_preview


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
    episode.intake_mode = "ad_hoc_video"

    package = workspace.create_package("talk_preview.mp4", episode)

    package_dir = draft_output / package.package_name
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    assert calls[0][0] == "ffmpeg"
    assert not (package_dir / "subtitle.srt").exists()
    assert (package_dir / "cover.png").exists()
    assert package.subtitle_path is None
    assert package.cover_path == str(package_dir / "cover.png")
    assert manifest["source_mode"] == "ad_hoc_video"
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
    assert result.draft_path == str(imported_paths[0])
    assert "copy_draft_external" in result.message
    assert manifest["jianying_import"]["status"] == "imported"
    assert manifest["jianying_import"]["draft_path"] == str(imported_paths[0])


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

        def add_track(self, track_type, track_name=None, **kwargs) -> None:
            captured["track_type"] = track_type
            captured["track_name"] = track_name

        def add_segment(self, segment, track_name=None) -> None:
            captured["segment"] = segment
            captured["segment_track_name"] = track_name

        def import_srt(self, subtitle_path, track_name, **kwargs) -> None:
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
    assert captured["track_name"] == "主视频"
    assert captured["segment_track_name"] == "主视频"


def test_legacy_draft_writer_adds_template_text_cover_and_audio_tracks(
    tmp_path,
    monkeypatch,
):
    video_path = tmp_path / "talk_preview.mp4"
    video_path.write_bytes(b"video")
    cover_path = tmp_path / "cover.png"
    Image.new("RGB", (1200, 900), "#2563eb").save(cover_path)
    subtitle_path = tmp_path / "subtitle.srt"
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n字幕\n", encoding="utf-8")
    episode = Episode(
        id="ep_tool_review",
        day_number=3,
        episode_date=date(2026, 6, 8),
        status=EpisodeStatus.SCRIPT_READY,
        content_template_id="tool_review",
        script=ScriptPackage(
            id="script_tool_review",
            hook="结论先讲，这个 AI 工具能省粗剪时间。",
            sections=[ScriptSection("s1", "结论", 20, "适合标准化口播。")],
            title_options=["AI 粗剪工具，真实省时间吗？"],
            cover_copy="AI粗剪工具\n真实测评",
            publish_copy="今天测一个真实粗剪工作流。",
            comment_prompt="你会把粗剪交给 AI 吗？",
        ),
    )
    workspace = DraftWorkspace(Settings())
    template_effects = {
        "content_template_id": "tool_review",
        "template_id": "tool_review",
        "template_name": "AI 工具测评剪映模板",
        "tracks": [
            {"number": 1, "label": "主视频"},
            {"number": 2, "label": "字幕"},
            {"number": 3, "label": "标题贴纸"},
            {"number": 4, "label": "关键词高亮"},
            {"number": 5, "label": "背景音乐"},
            {"number": 6, "label": "封面"},
        ],
        "effects": [
            {"kind": "title_card", "label": "标题卡", "payload": {"text": "AI 粗剪工具，真实省时间吗？"}},
            {"kind": "chapter_bar", "label": "章节字幕条", "payload": {"chapters": ["结论"]}},
            {"kind": "keyword_highlight", "label": "关键词高亮", "payload": {"keywords": ["AI 粗剪工具", "结论"]}},
            {"kind": "key_sentence_sticker", "label": "重点句大字贴纸", "payload": {"text": "结论先讲，这个 AI 工具能省粗剪时间。"}},
            {"kind": "cover", "label": "封面", "payload": {"asset_ref": str(cover_path)}},
            {"kind": "b_roll_insert", "label": "B-roll 插入点", "payload": {"insert_points": ["结论"]}},
            {"kind": "transition", "label": "简单转场", "payload": {"style": "章节硬切"}},
            {"kind": "background_music", "label": "背景音乐音量", "payload": {"volume_db": -20}},
            {"kind": "intro", "label": "片头", "payload": {"text": "结论先行"}},
            {"kind": "outro", "label": "片尾", "payload": {"text": "你会把粗剪交给 AI 吗？"}},
        ],
    }
    captured: dict[str, list] = {"tracks": [], "segments": [], "subtitles": []}

    class FakeDraftFolder:
        def __init__(self, root: str) -> None:
            self.root = Path(root)

        def create_draft(self, name, width, height, fps, allow_replace):
            return FakeScript(self.root, name)

    class FakeTrackType:
        video = "video"
        text = "text"
        audio = "audio"

    class FakeVideoMaterial:
        def __init__(self, path: str, material_name=None) -> None:
            self.path = path
            self.material_name = material_name
            self.duration = 12_000_000

    class FakeVideoSegment:
        def __init__(self, material, timerange, **kwargs) -> None:
            self.material = material
            self.timerange = timerange
            self.kwargs = kwargs
            self.transition = None

        def add_background_filling(self, *args, **kwargs):
            return self

        def add_transition(self, transition_type, **kwargs):
            self.transition = (transition_type, kwargs)
            return self

    class FakeTextSegment:
        def __init__(self, text, timerange, **kwargs) -> None:
            self.text = text
            self.timerange = timerange
            self.kwargs = kwargs

        def add_animation(self, *args, **kwargs):
            return self

    class FakeTextStyle:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeTextBackground:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeTextBorder:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeClipSettings:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeTextIntro:
        向上滑动 = "text_intro_up"

    class FakeTextOutro:
        渐隐 = "text_outro_fade"

    class FakeTransitionType:
        叠加 = "transition_overlay"

    class FakeScript:
        def __init__(self, root: Path, name: str) -> None:
            self.root = root
            self.name = name

        def add_track(self, track_type, track_name=None, **kwargs) -> None:
            captured["tracks"].append((track_type, track_name, kwargs))

        def add_segment(self, segment, track_name=None) -> None:
            captured["segments"].append((segment.__class__.__name__, track_name, segment))

        def import_srt(self, subtitle_path, track_name, **kwargs) -> None:
            captured["subtitles"].append((subtitle_path, track_name))

        def save(self) -> None:
            draft_path = self.root / self.name
            draft_path.mkdir(parents=True)
            (draft_path / "draft_content.json").write_text(
                json.dumps({"duration": 12_000_000, "materials": {}, "tracks": []}),
                encoding="utf-8",
            )
            (draft_path / "draft_meta_info.json").write_text("{}", encoding="utf-8")

    def fake_trange(start: int, duration: int):
        return {"start": start, "duration": duration}

    fake_module = ModuleType("pyJianYingDraft")
    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = FakeTrackType
    fake_module.VideoMaterial = FakeVideoMaterial
    fake_module.VideoSegment = FakeVideoSegment
    fake_module.TextSegment = FakeTextSegment
    fake_module.TextStyle = FakeTextStyle
    fake_module.TextBackground = FakeTextBackground
    fake_module.TextBorder = FakeTextBorder
    fake_module.ClipSettings = FakeClipSettings
    fake_module.TextIntro = FakeTextIntro
    fake_module.TextOutro = FakeTextOutro
    fake_module.TransitionType = FakeTransitionType
    fake_module.trange = fake_trange
    monkeypatch.setitem(__import__("sys").modules, "pyJianYingDraft", fake_module)
    monkeypatch.setattr(workspace, "_video_duration_microseconds", lambda path: 12_000_000)

    workspace._write_legacy_jianying_draft(
        tmp_path / "package",
        tmp_path / "jianying",
        {
            "package_name": "AI视频日记_测试模板轨道",
            "video": {"path": str(video_path)},
            "artifacts": {
                "cover_path": str(cover_path),
                "subtitle_path": str(subtitle_path),
            },
            "episode": {"id": episode.id},
            "template_effects": template_effects,
        },
    )

    track_names = [track_name for _, track_name, _ in captured["tracks"]]
    assert "主视频" in track_names
    assert "标题贴纸" in track_names
    assert "关键词高亮" in track_names
    assert "背景音乐" in track_names
    assert "封面" in track_names
    assert captured["subtitles"] == [(str(subtitle_path), "字幕")]
    text_segments = [
        segment
        for class_name, _, segment in captured["segments"]
        if class_name == "FakeTextSegment"
    ]
    assert any("AI 粗剪工具" in segment.text for segment in text_segments)
    assert any("B-roll" in segment.text for segment in text_segments)
    assert any(
        class_name == "FakeVideoSegment" and track_name == "封面"
        for class_name, track_name, _ in captured["segments"]
    )
    assert any(
        class_name == "FakeVideoSegment"
        and track_name == "主视频"
        and segment.transition is not None
        for class_name, track_name, segment in captured["segments"]
    )


def test_video_duration_probe_prefers_ffprobe_output(tmp_path, monkeypatch):
    video_path = tmp_path / "talk_preview.mp4"
    video_path.write_bytes(b"video")
    workspace = DraftWorkspace(Settings())

    def fake_duration_reader(path):
        assert path == video_path
        return 1.234

    monkeypatch.setattr("app.drafts.read_media_duration_seconds", fake_duration_reader)

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
