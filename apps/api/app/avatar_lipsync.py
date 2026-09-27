from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal, Protocol

from app.voice_clone import MediaRuntimeFailure
from app.media_timing import MediaTimingError, probe_avatar_media, probe_media_duration

if TYPE_CHECKING:
    from app.config import Settings
    from app.production import ProductionRepository


def compile_person_edit_command(command: str) -> str:
    """Require a complete executable request before offering person generation."""
    operations = set()
    for clause in re.split(r"[,，;；。.!！]|同时|并且|然后|\band\b|\bthen\b", command.casefold()):
        value = clause.strip()
        if not value:
            continue
        if re.fullmatch(r"其他(?:都)?(?:不要动|不变)|(?:保留|保持)(?:当前|原有)?(?:配音|字幕|原声|表情)(?:不变)?|(?:配音|字幕|原声|表情)不变", value):
            continue
        value = re.sub(r"^(?:请|麻烦)(?:你)?\s*", "", value)
        value = re.sub(r"^(?:这里|这一段|这段|当前镜头|所选镜头)\s*", "", value)
        if re.fullmatch(r"(?:只)?(?:重做|重新生成|重新对齐|再对齐)(?:人物)?口型|(?:regenerate|redo) (?:the )?(?:lip sync|lipsync)", value):
            operations.add("RegeneratePersonRender")
        elif re.fullmatch(r"(?:换成|改成|替换为)(?:当前|所选)人物(?:出镜)?|(?:使用|让)(?:当前|所选)人物出镜", value):
            operations.add("ChangePersonSegment")
        else:
            raise ValueError("人物要求尚不支持或含有歧义；可以使用“这一段换成当前人物出镜”或“这里只重做口型，其他都不要动”。")
    if len(operations) != 1:
        raise ValueError("请明确选择换成人物出镜或重做现有口型，不能混合两种操作。")
    return operations.pop()


AuthorizationStatus = Literal["active", "pending", "expired", "revoked"]
AvatarStatus = Literal[
    "succeeded",
    "unavailable",
    "authorization_denied",
    "provider_rejected",
    "output_missing",
    "failed",
]


@dataclass(frozen=True)
class Authorization:
    """An auditable grant for a person, voice, or avatar output.

    This is intentionally a domain contract rather than a second persistence
    store. The repository and API layer own how these values are stored.
    """

    id: str
    version: str
    rights_holder: str
    content_types: tuple[str, ...]
    usage_scopes: tuple[str, ...]
    effective_from: datetime
    expires_at: datetime | None
    evidence_reference: str
    status: AuthorizationStatus
    workspace_id: str = ""

    def failure_for(
        self,
        *,
        content_type: str,
        usage_scope: str,
        at: datetime | None = None,
    ) -> str | None:
        now = _as_utc(at or datetime.now(timezone.utc))
        if not self.id.strip() or not self.evidence_reference.strip():
            return "authorization_evidence_missing"
        if self.status != "active":
            return f"authorization_{self.status}"
        if _as_utc(self.effective_from) > now:
            return "authorization_not_effective"
        if self.expires_at is not None and _as_utc(self.expires_at) <= now:
            return "authorization_expired"
        if content_type not in self.content_types:
            return "authorization_content_type_denied"
        if usage_scope not in self.usage_scopes:
            return "authorization_scope_denied"
        return None


@dataclass(frozen=True)
class PersonProfile:
    id: str
    workspace_id: str
    display_name: str
    version: str
    avatar_reference: str
    background_capabilities: tuple[str, ...]
    shot_capabilities: tuple[str, ...]
    authorization: Authorization


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    workspace_id: str
    person_profile_id: str
    person_version: str
    version: str
    language: Literal["zh", "en", "zh-en"]
    reference_audio_path: str
    reference_text: str
    model_id: str
    authorization: Authorization


@dataclass(frozen=True)
class PronunciationEntry:
    id: str
    workspace_id: str
    term: str
    language: Literal["zh", "en", "zh-en"]
    target_pronunciation: str
    scope: Literal["workspace", "person", "task"]
    scope_reference: str | None
    version: str

    def applies_to(
        self,
        *,
        workspace_id: str,
        person_profile_id: str | None,
        task_id: str,
    ) -> bool:
        if self.workspace_id != workspace_id:
            return False
        if self.scope == "workspace":
            return self.scope_reference is None
        if self.scope == "person":
            return self.scope_reference == person_profile_id
        return self.scope_reference == task_id

    def task_input(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "term": self.term,
            "language": self.language,
            "target_pronunciation": self.target_pronunciation,
            "scope": self.scope,
            "scope_reference": self.scope_reference or "",
        }


@dataclass(frozen=True)
class AvatarAndLipSyncRequest:
    workspace_id: str
    person: PersonProfile
    voice: VoiceProfile
    audio_path: str
    audio_cache_key: str
    language: Literal["zh", "en", "zh-en"]
    motion: str
    expression: str
    authorization: Authorization
    model_id: str
    visual_path: str = ""


@dataclass(frozen=True)
class AvatarAndLipSyncResult:
    status: AvatarStatus
    cache_key: str
    artifact_path: str | None
    duration_seconds: float | None
    alignment_evidence: str | None
    failure_code: str | None
    failure_impact: str | None
    remediation_action: str | None
    reason: str | None = None
    impact: str | None = None
    repair_action: str | None = None
    retryable: bool = False

    @property
    def failure(self) -> MediaRuntimeFailure | None:
        if self.reason is None or self.impact is None or self.repair_action is None:
            return None
        return MediaRuntimeFailure(
            reason=self.reason,
            impact=self.impact,
            repair_action=self.repair_action,
            retryable=self.retryable,
        )


class AvatarAndLipSyncProvider(Protocol):
    model_id: str
    supported_languages: tuple[str, ...]

    @property
    def ready(self) -> bool: ...

    @property
    def message(self) -> str: ...

    def render(
        self,
        *,
        request: AvatarAndLipSyncRequest,
        output_path: str | Path,
    ) -> AvatarAndLipSyncResult: ...


class UnavailableAvatarAndLipSyncProvider:
    """Default until an explicitly configured local avatar runtime exists."""

    model_id = "avatar-lipsync-unconfigured"
    supported_languages: tuple[str, ...] = ()

    def __init__(self, message: str = "人物口型能力尚未配置。") -> None:
        self._message = message

    @property
    def ready(self) -> bool:
        return False

    @property
    def message(self) -> str:
        return self._message

    def render(
        self,
        *,
        request: AvatarAndLipSyncRequest,
        output_path: str | Path,
    ) -> AvatarAndLipSyncResult:
        return _failure(
            status="unavailable",
            cache_key=avatar_lipsync_cache_key(request),
            code="capability_unavailable",
            impact="人物与口型步骤未完成，不能生成或复用人物视频。",
            action=self.message,
            retryable=True,
        )


class SubprocessAvatarAndLipSyncProvider:
    """Narrow local-process adapter for a future explicit avatar runtime."""

    def __init__(
        self,
        *,
        runtime_path: str | Path,
        worker_path: str | Path,
        runtime_root: str | Path,
        model_id: str,
        supported_languages: tuple[str, ...] = (),
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.runtime_path = Path(runtime_path).expanduser()
        self.worker_path = Path(worker_path).expanduser()
        self.runtime_root = Path(runtime_root).expanduser()
        self.model_id = model_id
        self.supported_languages = supported_languages
        self._runner = runner or self._run_command

    @property
    def ready(self) -> bool:
        return (
            self.runtime_path.is_file()
            and self.worker_path.is_file()
            and self.runtime_root.is_dir()
            and (self.runtime_root / "scripts" / "inference.py").is_file()
            and (self.runtime_root / "models" / "musetalkV15" / "unet.pth").is_file()
        )

    @property
    def message(self) -> str:
        return "人物口型运行环境已就绪。" if self.ready else "人物口型运行环境不完整。"

    def render(
        self,
        *,
        request: AvatarAndLipSyncRequest,
        output_path: str | Path,
    ) -> AvatarAndLipSyncResult:
        cache_key = avatar_lipsync_cache_key(request)
        output = Path(output_path).expanduser().resolve()
        if not self.ready:
            return _failure(
                status="unavailable",
                cache_key=cache_key,
                code="capability_unavailable",
                impact="人物与口型步骤未完成，不能生成或复用人物视频。",
                action=self.message,
            )
        with TemporaryDirectory(prefix="ai_video_avatar_") as temporary_dir:
            request_path = Path(temporary_dir) / "request.json"
            response_path = Path(temporary_dir) / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model_id": self.model_id,
                        "person_id": request.person.id,
                        "person_version": request.person.version,
                        "voice_profile_id": request.voice.id,
                        "voice_version": request.voice.version,
                        "audio_path": str(Path(request.audio_path).expanduser().resolve()),
                        "visual_path": str(Path(request.visual_path).expanduser().resolve()),
                        "audio_cache_key": request.audio_cache_key,
                        "language": request.language,
                        "motion": request.motion,
                        "expression": request.expression,
                        "authorization_id": request.authorization.id,
                        "authorization_version": request.authorization.version,
                        "output_path": str(output),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                self._runner(
                    [
                        str(self.runtime_path),
                        str(self.worker_path),
                        "--request",
                        str(request_path),
                        "--response",
                        str(response_path),
                        "--runtime-root",
                        str(self.runtime_root.resolve()),
                    ]
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return _failure(
                    status="failed",
                    cache_key=cache_key,
                    code="provider_process_failed",
                    impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
                    action=f"检查本地人物口型运行环境后重试：{exc}",
                )
            if not response_path.is_file():
                return _failure(
                    status="failed",
                    cache_key=cache_key,
                    code="provider_response_missing",
                    impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
                    action="检查人物口型服务响应后重试。",
                )
            try:
                payload = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return _failure(
                    status="failed",
                    cache_key=cache_key,
                    code="provider_response_invalid",
                    impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
                    action=f"检查人物口型服务响应格式后重试：{exc}",
                )
        return _parse_avatar_result(
            payload, expected_output=output, audio_path=Path(request.audio_path), cache_key=cache_key
        )

    def _run_command(self, command: list[str]) -> None:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30 * 60)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise subprocess.SubprocessError(detail or f"exit {completed.returncode}")


class LatentSyncAvatarAndLipSyncProvider(SubprocessAvatarAndLipSyncProvider):
    """Local high-precision option; keep its model identity separate from MuseTalk."""

    def __init__(self, *, pose_root: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.pose_root = Path(pose_root).expanduser()

    @property
    def ready(self) -> bool:
        required = (
            self.worker_path,
            self.worker_path.with_name("latentsync_entry.py"),
            self.runtime_root / "inference_fidelity.py",
            self.runtime_root / "dependencies" / "mlx" / "nn" / "__init__.py",
            self.runtime_root / "upstream" / "latentsync" / "utils" / "affine_transform.py",
            self.runtime_root / "checkpoints" / "latentsync_unet_mlx.safetensors",
            self.runtime_root / "checkpoints" / "vae_mlx.safetensors",
            self.runtime_root / "checkpoints" / "whisper" / "tiny.pt",
            self.pose_root / "official_onnx.py",
            self.pose_root / "dw-ll_ucoco_384.onnx",
            self.pose_root / "dependencies" / "onnxruntime" / "__init__.py",
            self.pose_root / "dependencies" / "loguru" / "__init__.py",
        )
        return (
            self.runtime_path.is_file()
            and all(path.is_file() for path in required)
            and any((self.runtime_root / "dependencies" / "mlx").glob("core*.so"))
        )

    @property
    def message(self) -> str:
        return "高精度口型已就绪；处理时间较长。" if self.ready else "高精度口型环境不完整，请检查外接盘模型与运行依赖。"

    def _run_command(self, command: list[str]) -> None:
        completed = subprocess.run(
            [*command, "--pose-root", str(self.pose_root.resolve())],
            check=False, capture_output=True, text=True, timeout=6 * 60 * 60,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise subprocess.SubprocessError(detail[-4000:] or f"exit {completed.returncode}")


class AvatarAndLipSyncService:
    def __init__(self, output_root: str | Path, provider: AvatarAndLipSyncProvider) -> None:
        self.output_root = Path(output_root).expanduser()
        self.provider = provider

    def render(
        self,
        request: AvatarAndLipSyncRequest,
        *,
        output_cache_key: str | None = None,
    ) -> AvatarAndLipSyncResult:
        cache_key = output_cache_key or avatar_lipsync_cache_key(request)
        failure = _authorization_failure(request)
        if failure is not None:
            return _failure(
                status="authorization_denied",
                cache_key=cache_key,
                code=failure,
                impact="人物与口型步骤被授权门禁阻断，不能生成或复用人物视频。",
                action="补充有效的权利人授权、用途范围和证据后重试。",
                retryable=False,
            )
        audio = Path(request.audio_path).expanduser()
        if not audio.is_file() or audio.stat().st_size == 0:
            return _failure(
                status="failed",
                cache_key=cache_key,
                code="audio_missing",
                impact="人物与口型步骤没有可用配音，不能生成视频。",
                action="重新生成并确认当前版本的配音文件后重试。",
                retryable=False,
            )
        visual = Path(request.visual_path).expanduser()
        if self.provider.ready and (not visual.is_file() or visual.stat().st_size == 0):
            return _failure(
                status="failed",
                cache_key=cache_key,
                code="visual_source_missing",
                impact="人物与口型步骤没有可用人物画面，不能生成视频。",
                action="从当前工作区选择已批准的人物参考视频后重试。",
                retryable=False,
            )
        if self.provider.ready and request.model_id != self.provider.model_id:
            return _failure(
                status="failed",
                cache_key=cache_key,
                code="model_mismatch",
                impact="人物与口型步骤未使用请求指定的模型，不能生成视频。",
                action="选择已配置的人物口型模型版本后重试。",
                retryable=False,
            )
        if self.provider.ready and request.language not in self.provider.supported_languages:
            return _failure(
                status="unavailable",
                cache_key=cache_key,
                code="language_unavailable",
                impact="当前人物口型运行时未声明支持该配音语言，未生成或复用视频。",
                action="选择已声明支持该语言的本地模型。",
                retryable=False,
            )
        output = (
            self.output_root
            / "avatar_lipsync"
            / _safe_component(request.workspace_id)
            / f"{cache_key}.mp4"
        )
        reusable = _probe_avatar_output(output, audio_path=Path(request.audio_path))
        if reusable is not None:
            duration_seconds, alignment_evidence = reusable
            return AvatarAndLipSyncResult(
                status="succeeded",
                cache_key=cache_key,
                artifact_path=str(output.resolve()),
                duration_seconds=duration_seconds,
                alignment_evidence=alignment_evidence,
                failure_code=None,
                failure_impact=None,
                remediation_action=None,
            )
        result = self.provider.render(request=request, output_path=output)
        if result.status != "succeeded":
            # A failed attempt is not a reusable cache entry, even when it left
            # behind a playable but incomplete file. This path is generated
            # scratch output; published immutable artifacts live elsewhere.
            output.unlink(missing_ok=True)
        return replace(result, cache_key=cache_key)

    def render_from_repository(
        self,
        *,
        repository: ProductionRepository,
        workspace_id: str,
        person_id: str,
        person_version: str,
        voice_profile_id: str,
        voice_version: str,
        audio_path: str,
        audio_cache_key: str,
        language: str,
        motion: str,
        expression: str,
        authorization_id: str,
        authorization_version: str,
        model_id: str,
    ) -> AvatarAndLipSyncResult:
        request = repository.resolve_avatar_lipsync_request(
            workspace_id=workspace_id,
            person_id=person_id,
            person_version=person_version,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
            audio_path=audio_path,
            audio_cache_key=audio_cache_key,
            language=language,
            motion=motion,
            expression=expression,
            authorization_id=authorization_id,
            authorization_version=authorization_version,
            model_id=model_id,
        )
        return self.render(request)


class AvatarPreviewService:
    """Persisted short-preview boundary for explicitly configured avatar runtimes."""

    capability_id = "avatar_lipsync"

    def __init__(self, output_root: str | Path, provider: AvatarAndLipSyncProvider) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.provider = provider

    def request(
        self,
        *,
        repository: ProductionRepository,
        workspace_id: str,
        person_id: str,
        person_version: str,
        voice_profile_id: str,
        voice_version: str,
        audio_path: str,
        audio_cache_key: str,
        language: str,
        motion: str,
        expression: str,
        authorization_id: str,
        authorization_version: str,
        model_id: str,
        retry: bool = False,
    ):
        from app.production import MediaPreviewRunRecord

        request = repository.resolve_avatar_lipsync_request(
            workspace_id=workspace_id,
            person_id=person_id,
            person_version=person_version,
            voice_profile_id=voice_profile_id,
            voice_version=voice_version,
            audio_path=audio_path,
            audio_cache_key=audio_cache_key,
            language=language,
            motion=motion,
            expression=expression,
            authorization_id=authorization_id,
            authorization_version=authorization_version,
            model_id=model_id,
        )
        identity = _avatar_preview_identity(
            request,
            capability_id=self.capability_id,
            provider_model_id=self.provider.model_id,
        )
        existing = repository.get_media_preview_run(workspace_id, "avatar_lipsync", identity)
        succeeded_reusable = not (
            existing is not None
            and existing.status == "succeeded"
            and not _valid_avatar_preview_output(existing.output_path, self.output_root)
        )

        candidate = MediaPreviewRunRecord(
            id=f"avatar_preview_{identity}",
            workspace_id=workspace_id,
            preview_type="avatar_lipsync",
            identity=identity,
            status="running",
            capability_id=self.capability_id,
            model_id=request.model_id,
            language=request.language,
            person_profile_id=request.person.id,
            person_version=request.person.version,
            voice_profile_id=request.voice.id,
            voice_version=request.voice.version,
            authorization_id=request.authorization.id,
            authorization_version=request.authorization.version,
        )
        running, claimed = repository.claim_media_preview_run(
            candidate,
            retry=retry,
            succeeded_reusable=succeeded_reusable,
        )
        if not claimed:
            return running
        if existing is not None and existing.status == "succeeded":
            return self._finish(
                repository,
                running,
                status="failed",
                reason="output_missing",
                impact="已记录的人物口型预览输出不可用，不能作为成功结果复用。",
                repair_action="确认输出目录后显式重试人物口型预览。",
            )
        authorization_failure = _authorization_failure(request)
        if authorization_failure is not None:
            return self._finish(
                repository,
                running,
                status="blocked",
                reason=authorization_failure,
                impact="人物口型预览被授权或人物绑定门禁阻断，未生成视频。",
                repair_action="修复授权、人物声音绑定或语言一致性后，以新身份重试。",
            )
        if not self.provider.ready:
            return self._finish(
                repository,
                running,
                status="unavailable",
                reason="capability_unavailable",
                impact="人物口型预览能力未配置，未生成视频。",
                repair_action=self.provider.message,
            )
        if request.language not in self.provider.supported_languages:
            return self._finish(
                repository,
                running,
                status="unavailable",
                reason="language_unavailable",
                impact="当前人物口型运行时不支持请求语言，未生成视频。",
                repair_action="选择已声明支持该语言的本地模型。",
            )
        audio = Path(request.audio_path).expanduser().resolve()
        try:
            audio.relative_to(self.output_root)
        except ValueError:
            return self._finish(
                repository,
                running,
                status="failed",
                reason="audio_path_outside_output_root",
                impact="人物口型预览音频不在允许输出根目录，未生成视频。",
                repair_action="选择当前预览输出根内的配音文件后重试。",
            )
        if not audio.is_file() or audio.stat().st_size == 0:
            return self._finish(
                repository,
                running,
                status="failed",
                reason="audio_missing",
                impact="人物口型预览没有可用音频，未生成视频。",
                repair_action="重新生成并选择当前版本的非空配音文件。",
            )
        result = AvatarAndLipSyncService(self.output_root, self.provider).render(request)
        if result.status == "succeeded":
            if not _valid_avatar_preview_output(result.artifact_path, self.output_root):
                return self._finish(
                    repository,
                    running,
                    status="failed",
                    reason="output_invalid",
                    impact="人物口型预览输出缺失、为空或越出允许输出目录。",
                    repair_action="检查人物口型 provider 输出路径和文件后重试。",
                )
            return self._finish(
                repository,
                running,
                status="succeeded",
                output_path=result.artifact_path,
            )
        return self._finish(
            repository,
            running,
            status=("unavailable" if result.status == "unavailable" else "blocked" if result.status == "authorization_denied" else "failed"),
            reason=result.failure_code or "provider_failed",
            impact=result.failure_impact or "人物口型预览未生成可用视频。",
            repair_action=result.remediation_action or "检查人物口型运行环境后重试。",
        )

    @staticmethod
    def _finish(repository, record, *, status, output_path=None, reason=None, impact=None, repair_action=None):
        completed = replace(
            record,
            status=status,
            output_path=output_path,
            reason=reason,
            impact=impact,
            repair_action=repair_action,
            finished_at=datetime.now(timezone.utc),
        )
        return repository.save_media_preview_run(completed)


def avatar_lipsync_cache_key(request: AvatarAndLipSyncRequest) -> str:
    payload = {
        "person": {
            "id": request.person.id,
            "version": request.person.version,
            "avatar_reference": request.person.avatar_reference,
            "authorization": _authorization_cache_identity(request.person.authorization),
        },
        "voice": {
            "id": request.voice.id,
            "version": request.voice.version,
            "authorization": _authorization_cache_identity(request.voice.authorization),
        },
        "audio_cache_key": request.audio_cache_key,
        "language": request.language,
        "motion": request.motion,
        "expression": request.expression,
        "authorization": _authorization_cache_identity(request.authorization),
        "model_id": request.model_id,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _avatar_preview_identity(
    request: AvatarAndLipSyncRequest,
    *,
    capability_id: str,
    provider_model_id: str,
) -> str:
    payload = {
        "request_cache_key": avatar_lipsync_cache_key(request),
        "capability_id": capability_id,
        "provider_model_id": provider_model_id,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _authorization_failure(request: AvatarAndLipSyncRequest) -> str | None:
    if request.workspace_id != request.person.workspace_id or request.workspace_id != request.voice.workspace_id:
        return "workspace_mismatch"
    if any(
        authorization.workspace_id != request.workspace_id
        for authorization in (
            request.person.authorization,
            request.voice.authorization,
            request.authorization,
        )
    ):
        return "authorization_workspace_mismatch"
    if request.voice.person_profile_id != request.person.id:
        return "voice_person_mismatch"
    if request.voice.person_version != request.person.version:
        return "voice_person_version_mismatch"
    if request.voice.language != request.language:
        return "language_mismatch"
    person_failure = request.person.authorization.failure_for(
        content_type="person_likeness", usage_scope="avatar_lipsync"
    )
    if person_failure:
        return person_failure
    voice_failure = request.voice.authorization.failure_for(
        content_type="voice", usage_scope="avatar_lipsync"
    )
    if voice_failure:
        return voice_failure
    return request.authorization.failure_for(
        content_type="avatar_video", usage_scope="avatar_lipsync"
    )


def _parse_avatar_result(
    payload: object, *, expected_output: Path, audio_path: Path, cache_key: str
) -> AvatarAndLipSyncResult:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_response_invalid",
            impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
            action="检查人物口型服务响应格式后重试。",
        )
    if payload.get("status") == "provider_rejected":
        reason = payload.get("reason")
        return _failure(
            status="provider_rejected",
            cache_key=cache_key,
            code="provider_rejected",
            impact="供应商拒绝生成人物视频，当前视频不能进入后续剪辑。",
            action=str(reason).strip() or "检查供应商限制或输入后重试。",
        )
    if payload.get("status") == "failed":
        reason = payload.get("reason")
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_failed",
            impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
            action=str(reason).strip() or "检查本地人物口型运行环境后重试。",
        )
    if payload.get("status") != "succeeded":
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_response_invalid",
            impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
            action="检查人物口型服务响应状态后重试。",
        )
    if payload.get("artifact_path") != str(expected_output):
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_output_path_mismatch",
            impact="人物与口型步骤失败，当前视频不能进入后续剪辑。",
            action="检查人物口型服务输出路径后重试。",
        )
    if not expected_output.is_file() or expected_output.stat().st_size == 0:
        return _failure(
            status="output_missing",
            cache_key=cache_key,
            code="output_missing",
            impact="人物口型服务没有生成视频，当前视频不能进入后续剪辑。",
            action="检查输出目录权限和供应商任务结果后重试。",
        )
    media = _probe_avatar_output(expected_output, audio_path=audio_path)
    if media is None:
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="output_invalid",
            impact="人物口型视频不可用或与配音时长不一致，当前视频不能进入后续剪辑。",
            action="检查人物口型服务输出编码及完整配音时长后重试。",
        )
    duration = payload.get("duration_seconds")
    alignment_evidence = payload.get("alignment_evidence")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
        or not isinstance(alignment_evidence, str)
        or not alignment_evidence.strip()
    ):
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_evidence_invalid",
            impact="人物口型输出缺少时长或对齐证据，不能作为完成视频使用。",
            action="要求人物口型服务返回有效时长和对齐证据后重试。",
        )
    actual_duration, _ = media
    # Allow container/encoder rounding, but never publish a materially shorter
    # output using only the worker's claimed duration.
    if abs(actual_duration - duration) > 0.1:
        return _failure(
            status="failed",
            cache_key=cache_key,
            code="provider_evidence_invalid",
            impact="人物口型输出的实际时长与服务返回值不一致，不能进入后续剪辑。",
            action="检查人物口型输出是否被截断后重新生成。",
        )
    return AvatarAndLipSyncResult(
        status="succeeded",
        cache_key=cache_key,
        artifact_path=str(expected_output),
        duration_seconds=actual_duration,
        alignment_evidence=alignment_evidence.strip(),
        failure_code=None,
        failure_impact=None,
        remediation_action=None,
        reason=None,
        impact=None,
        repair_action=None,
        retryable=False,
    )


def _failure(
    *,
    status: AvatarStatus,
    cache_key: str,
    code: str,
    impact: str,
    action: str,
    retryable: bool = True,
) -> AvatarAndLipSyncResult:
    return AvatarAndLipSyncResult(
        status=status,
        cache_key=cache_key,
        artifact_path=None,
        duration_seconds=None,
        alignment_evidence=None,
        failure_code=code,
        failure_impact=impact,
        remediation_action=action,
        reason=code,
        impact=impact,
        repair_action=action,
        retryable=retryable,
    )


def _safe_component(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_-" else "_" for character in value)[:80] or "item"


def _valid_avatar_preview_output(output_path: str | None, output_root: Path) -> bool:
    if not output_path:
        return False
    output = Path(output_path).expanduser().resolve()
    try:
        output.relative_to(output_root)
    except ValueError:
        return False
    try:
        return (
            output.is_file()
            and output.stat().st_size > 0
            and b"ftyp" in output.read_bytes()[:64]
        )
    except OSError:
        return False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _authorization_cache_identity(authorization: Authorization) -> dict[str, str | None]:
    return {
        "id": authorization.id,
        "version": authorization.version,
        "status": authorization.status,
        "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else None,
    }


def avatar_lipsync_provider_from_settings(
    settings: Settings,
    *, quality: Literal["high", "fast"] = "fast",
) -> AvatarAndLipSyncProvider:
    if quality == "high":
        if not settings.latentsync_runtime_python_path or not settings.latentsync_root_path or not settings.latentsync_pose_root_path:
            return UnavailableAvatarAndLipSyncProvider("高精度口型环境尚未配置。")
        return LatentSyncAvatarAndLipSyncProvider(
            runtime_path=settings.latentsync_runtime_python_path,
            worker_path=settings.latentsync_worker_path,
            runtime_root=settings.latentsync_root_path,
            pose_root=settings.latentsync_pose_root_path,
            model_id="latentsync-1.5-mlx-fidelity-brow",
            supported_languages=("zh",),
        )
    runtime = settings.avatar_model_runtime_python_path
    runtime_root = settings.avatar_model_root_path
    if not runtime or not runtime_root:
        return UnavailableAvatarAndLipSyncProvider()
    return SubprocessAvatarAndLipSyncProvider(
        runtime_path=runtime,
        worker_path=settings.avatar_model_worker_path,
        runtime_root=runtime_root,
        model_id=settings.avatar_model_id,
        supported_languages=("zh",),
    )


def _probe_avatar_output(path: Path, *, audio_path: Path | None = None) -> tuple[float, str] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        expected_duration = probe_media_duration(audio_path) if audio_path is not None else None
        return probe_avatar_media(path, expected_duration=expected_duration)
    except MediaTimingError:
        return None
