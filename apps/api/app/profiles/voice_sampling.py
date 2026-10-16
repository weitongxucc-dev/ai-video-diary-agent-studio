"""Member-bound reusable voice samples; reception never claims model readiness."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4
import wave

from app.materials.contracts import MaterialAsset
from app.models import utc_now
from app.profiles.contracts import ProfileReference, VoiceProfile
from app.profiles.registration import ProfileRegistrationUseCase


TEXT_VERSION = "voice-sample-2026-10-v1"
REFERENCE_TEXT = {
    "zh": "你好，这是我的声音样本。今天的阳光很好，我想把生活中的见闻记录下来。无论是一次旅行、一顿晚餐，还是一个新想法，都值得认真讲述。让我们用自然的语气，分享真实的故事。",
    "en": "Hello, this is a sample of my voice. Today I want to share a story about the small things in life. A walk outside, a meal with friends, or a new idea can become a memory worth keeping. Let us tell these stories clearly and naturally.",
    "zh-en": "你好，这是我的声音样本。我想记录生活中的真实故事，分享一路上的发现。Hello, this is my voice. Every day brings a new story, and I would like to share it with you.",
}
MAX_SAMPLE_BYTES = 50 * 1024 * 1024


class VoiceSamplingConflict(ValueError):
    pass


@dataclass(frozen=True)
class VoiceSamplingTask:
    id: str
    workspace_id: str
    owner_user_id: str
    request_id: str
    purpose: str
    display_name: str
    language: str
    reference_text: str
    text_version: str
    status: str
    created_at: str
    updated_at: str
    source_device: str | None = None
    submission_request_id: str | None = None
    submission_digest: str | None = None
    audio_sha256: str | None = None
    duration_seconds: float | None = None
    size_bytes: int | None = None
    voice_profile_id: str | None = None
    voice_version: str | None = None
    received_at: str | None = None
    last_error: str | None = None
    failed_request_id: str | None = None


def _required(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name}不能为空")
    return value.strip()


def _wav_duration(content: bytes) -> float:
    if not content or len(content) > MAX_SAMPLE_BYTES:
        raise ValueError("声音样本不能为空或超过 50 MB")
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            channels, width = audio.getnchannels(), audio.getsampwidth()
            if rate <= 0 or frames <= 0 or channels not in {1, 2} or width not in {1, 2, 3, 4}:
                raise ValueError("声音样本须包含有效的单声道或双声道 PCM 音频")
            duration = frames / rate
            if not 1 <= duration <= 120:
                raise ValueError("声音样本须在 1–120 秒之间，请重录或截取合适范围")
            if len(audio.readframes(frames)) != frames * channels * width:
                raise ValueError("声音样本接收不完整，请重新提交原录音")
            return duration
    except (wave.Error, EOFError) as exc:
        raise ValueError("声音样本不是可读取的 PCM WAV，请重新录制或导入 WAV") from exc


class VoiceSamplingUseCase:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], *,
                 library_path: Path, registration: ProfileRegistrationUseCase):
        self._connect = connection_factory
        self._library_path = Path(library_path)
        self._registration = registration

    def _sample_root(self) -> Path:
        try:
            root = self._library_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("素材保存目录不可用，请连接原磁盘并检查保存位置") from exc
        if not root.is_dir() or not os.access(root, os.W_OK):
            raise ValueError("素材保存目录不可写，请检查磁盘和目录权限")
        if len(root.parts) > 2 and root.parts[1] == "Volumes" and not Path(*root.parts[:3]).is_mount():
            raise ValueError("声音保存位置的外接磁盘未挂载，请连接原磁盘")
        return root

    def create(self, workspace_id: str, owner_user_id: str, *, request_id: str,
               display_name: str, language: str = "zh", purpose: str = "voice_profile") -> VoiceSamplingTask:
        workspace_id = _required(workspace_id, "工作区")
        owner_user_id = _required(owner_user_id, "采样成员")
        request_id = _required(request_id, "原请求")
        display_name = _required(display_name, "声音名称")
        if purpose != "voice_profile" or language not in REFERENCE_TEXT:
            raise ValueError("采样仅用于可复用声音档案，语言须为 zh、en 或 zh-en")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM voice_sampling_tasks WHERE workspace_id=? AND owner_user_id=? AND request_id=?",
                               (workspace_id, owner_user_id, request_id)).fetchone()
            if row:
                task = VoiceSamplingTask(**json.loads(row["payload"]))
                if (task.display_name, task.language, task.purpose) != (display_name, language, purpose):
                    raise VoiceSamplingConflict("同一采样请求不能更改名称、语言或用途")
                return task
            now = utc_now().isoformat()
            task = VoiceSamplingTask(id=f"sample-{uuid4().hex}", workspace_id=workspace_id,
                owner_user_id=owner_user_id, request_id=request_id, purpose=purpose, display_name=display_name,
                language=language, reference_text=REFERENCE_TEXT[language], text_version=TEXT_VERSION,
                status="awaiting_sample", created_at=now, updated_at=now)
            conn.execute("INSERT INTO voice_sampling_tasks(workspace_id,id,owner_user_id,request_id,payload,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (workspace_id, task.id, owner_user_id, request_id, json.dumps(asdict(task), ensure_ascii=False), now, now))
            return task

    def get(self, workspace_id: str, owner_user_id: str, task_id: str) -> VoiceSamplingTask:
        with self._connect() as conn:
            return self._get(conn, workspace_id, owner_user_id, task_id)

    def list(self, workspace_id: str, owner_user_id: str) -> list[VoiceSamplingTask]:
        _required(owner_user_id, "采样成员")
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM voice_sampling_tasks WHERE workspace_id=? AND owner_user_id=? ORDER BY created_at DESC,rowid DESC",
                                (workspace_id, owner_user_id)).fetchall()
            return [VoiceSamplingTask(**json.loads(row["payload"])) for row in rows]

    @staticmethod
    def _get(conn, workspace_id, owner_user_id, task_id):
        _required(owner_user_id, "采样成员")
        row = conn.execute("SELECT payload FROM voice_sampling_tasks WHERE workspace_id=? AND owner_user_id=? AND id=?",
                           (workspace_id, owner_user_id, task_id)).fetchone()
        if row is None:
            raise KeyError("采样任务不存在或不属于当前成员")
        return VoiceSamplingTask(**json.loads(row["payload"]))

    @staticmethod
    def _save(conn, task):
        conn.execute("UPDATE voice_sampling_tasks SET payload=?,updated_at=? WHERE workspace_id=? AND id=? AND owner_user_id=?",
                     (json.dumps(asdict(task), ensure_ascii=False), task.updated_at, task.workspace_id, task.id, task.owner_user_id))

    def receive(self, workspace_id: str, owner_user_id: str, task_id: str, *, request_id: str,
                text_version: str, purpose: str, source_device: str, content: bytes,
                model_id: str = "") -> VoiceSamplingTask:
        request_id = _required(request_id, "提交请求")
        digest = hashlib.sha256(content).hexdigest()
        submission = hashlib.sha256(json.dumps([purpose, text_version, source_device, digest]).encode()).hexdigest()
        created_path: Path | None = None
        temporary: Path | None = None
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                task = self._get(conn, workspace_id, owner_user_id, task_id)
                if purpose != task.purpose or text_version != task.text_version:
                    raise VoiceSamplingConflict("采样用途或朗读文本版本已变化，请重新读取原任务")
                if source_device not in {"desktop", "import", "mobile"}:
                    raise ValueError("采样来源必须为 desktop、import 或 mobile")
                if task.status == "received":
                    if (task.submission_request_id, task.submission_digest) != (request_id, submission):
                        raise VoiceSamplingConflict("此任务已接收声音样本；查询原回执或新建采样任务")
                    return task
                duration = _wav_duration(content)
                relative = Path("approved") / "voices" / f"{task.id}-{digest}.wav"
                root = self._sample_root()
                root_device = root.stat().st_dev
                target = root / relative
                # Resolve before writing: an existing symlink must not escape the media root.
                try:
                    target.resolve().relative_to(root)
                except ValueError as exc:
                    raise ValueError("声音保存位置越出既定素材目录，请检查目录链接") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise ValueError("已保存采样文件发生变化，未覆盖原文件")
                else:
                    temporary = target.with_suffix(f".{uuid4().hex}.partial")
                    with temporary.open("xb") as output:
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    created_path = target
                    temporary = None
                    directory = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                if self._sample_root().stat().st_dev != root_device:
                    raise ValueError("声音接收期间磁盘发生变化，请重新连接原磁盘")
                stat = target.stat()
                asset = MaterialAsset(id=f"source-{task.id}", workspace_id=workspace_id,
                    file_name=f"{task.display_name}.wav", path=str(target.resolve()), size_bytes=len(content),
                    modified_at=datetime.fromtimestamp(stat.st_mtime), media_type="audio",
                    duration_seconds=duration, owner_user_id=owner_user_id,
                    analysis_status="blocked",
                    analysis_message="声音样本已保存；音频内容分析尚未接入。")
                voice = VoiceProfile(id=f"voice-{task.id}", workspace_id=workspace_id, version="v1",
                    language=task.language, reference_audio_path=f"workspace://{relative.as_posix()}",
                    reference_text=task.reference_text, display_name=task.display_name, model_id=model_id,
                    reference=ProfileReference(asset_id=asset.id), owner_user_id=owner_user_id)
                self._registration.register_received_voice(conn, voice, asset)
                now = utc_now().isoformat()
                task = replace(task, status="received", source_device=source_device,
                    submission_request_id=request_id, submission_digest=submission, audio_sha256=digest,
                    duration_seconds=duration, size_bytes=len(content), voice_profile_id=voice.id,
                    voice_version=voice.version, received_at=now, updated_at=now,
                    last_error=None, failed_request_id=None)
                self._save(conn, task)
            return task
        except Exception as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if created_path is not None:
                created_path.unlink(missing_ok=True)
            # Keep failed validation actionable after reopening without changing a received result.
            if isinstance(error, (ValueError, OSError)) and not isinstance(error, VoiceSamplingConflict):
                message = "声音文件未能保存，请检查原磁盘和目录权限后用原请求重试" if isinstance(error, OSError) else str(error)
                with self._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current = self._get(conn, workspace_id, owner_user_id, task_id)
                    if current.status == "awaiting_sample":
                        self._save(conn, replace(current, last_error=message, failed_request_id=request_id,
                                                 updated_at=utc_now().isoformat()))
            raise
