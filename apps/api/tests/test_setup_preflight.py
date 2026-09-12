from __future__ import annotations

import sys
from unittest import mock

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.preflight import setup_preflight

# ── Endpoint contract ───────────────────────────────────────────────────


def test_preflight_endpoint_returns_stable_structure(tmp_path, monkeypatch):
    """GET /api/setup/preflight returns overall, checks[], generated_at."""
    # Ensure directories exist
    for d in [
        tmp_path / "inbox",
        tmp_path / "output",
        tmp_path / "drafts",
        tmp_path / "jianying",
    ]:
        d.mkdir(exist_ok=True)

    s2 = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(tmp_path),
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "db.sqlite"),
    )
    monkeypatch.setattr(main_module, "settings", s2)
    monkeypatch.setattr(main_module, "repo", main_module.repo)

    client = TestClient(main_module.app)
    response = client.get("/api/setup/preflight")
    assert response.status_code == 200

    data = response.json()
    assert data["overall"] in ("ready", "blocked")
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) == 8
    assert "generated_at" in data

    # Every check has the required fields
    for check in data["checks"]:
        assert "id" in check
        assert "label" in check
        assert check["status"] in (
            "ready", "missing", "invalid", "degraded", "not_applicable",
        )
        assert "summary" in check
        assert "impact" in check
        assert "repair_action" in check
        assert isinstance(check["required"], bool)
        # settings_target is optional but present on most
        assert "settings_target" in check

    # Known check IDs
    ids = {c["id"] for c in data["checks"]}
    assert ids == {
        "deepseek", "obsidian", "video_inbox", "video_output",
        "jianying", "ffmpeg", "python", "asr",
    }


# ── Normal / ready path ─────────────────────────────────────────────────


def test_preflight_all_ready_with_mock_ffmpeg(tmp_path, monkeypatch):
    """When all dependencies are configured and directories exist,
    overall is ready and critical checks pass."""
    dirs = {
        "obsidian": tmp_path / "obsidian",
        "inbox": tmp_path / "inbox",
        "output": tmp_path / "output",
        "jianying": tmp_path / "jianying",
    }
    for d in dirs.values():
        d.mkdir()

    # Add a markdown file to obsidian
    (dirs["obsidian"] / "note.md").write_text("# test")

    settings = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(dirs["obsidian"]),
        video_inbox_path=str(dirs["inbox"]),
        video_output_path=str(dirs["output"]),
        jianying_draft_root_path=str(dirs["jianying"]),
        asr_provider="tencentcloud",
        tencentcloud_secret_id="test-id",
        tencentcloud_secret_key="test-key",
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    assert result["overall"] == "ready"

    by_id = {c["id"]: c for c in result["checks"]}
    assert by_id["deepseek"]["status"] == "ready"
    assert by_id["obsidian"]["status"] == "ready"
    assert by_id["ffmpeg"]["status"] == "ready"
    assert by_id["python"]["status"] == "ready"
    assert by_id["asr"]["status"] == "ready"
    assert by_id["video_inbox"]["status"] == "ready"
    assert by_id["video_output"]["status"] == "ready"
    assert by_id["jianying"]["status"] == "ready"


# ── Missing API key ─────────────────────────────────────────────────────


def test_preflight_missing_deepseek_key_uses_local_degraded_mode(tmp_path):
    """No DeepSeek key keeps the local source-grounded generator available."""
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    output.mkdir()
    settings = Settings(
        deepseek_api_key=None,
        video_inbox_path=str(inbox),
        video_output_path=str(output),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    assert result["overall"] == "ready"
    deepseek = next(c for c in result["checks"] if c["id"] == "deepseek")
    assert deepseek["status"] == "degraded"
    assert deepseek["required"] is False
    assert "本地" in deepseek["summary"]
    assert "未配置" in deepseek["summary"]


def test_preflight_deepseek_ready_is_honest_about_local_only(tmp_path):
    """DeepSeek ready must say config is provided, not verified, and defer
    remote availability to /api/runtime/health (no network call)."""
    settings = Settings(
        deepseek_api_key="sk-test",
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    deepseek = next(c for c in result["checks"] if c["id"] == "deepseek")
    assert deepseek["status"] == "ready"
    assert "配置已提供" in deepseek["summary"]
    assert "runtime/health" in deepseek["summary"]
    # Honest wording must not claim verification
    assert "已验证" not in deepseek["summary"]
    assert "verified" not in deepseek["summary"]


# ── Directory not available ─────────────────────────────────────────────


def test_preflight_directory_invalid_blocks(tmp_path):
    """Any configured-but-missing directory → invalid → overall blocked."""
    fake = str(tmp_path / "nonexistent_dir")

    settings = Settings(
        deepseek_api_key="sk-test",
        video_inbox_path=fake,
        video_output_path=fake,
        jianying_draft_root_path=fake,
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    by_id = {c["id"]: c for c in result["checks"]}
    assert by_id["video_inbox"]["status"] == "invalid"
    assert by_id["video_output"]["status"] == "invalid"
    assert by_id["jianying"]["status"] == "invalid"
    # invalid (even non-critical) directories must block overall
    assert result["overall"] == "blocked"


def test_preflight_invalid_obsidian_directory_is_optional(tmp_path):
    """An invalid optional Obsidian path is visible without locking the app."""
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    output.mkdir()
    settings = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(tmp_path / "missing_vault"),
        video_inbox_path=str(inbox),
        video_output_path=str(output),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    obsidian = next(c for c in result["checks"] if c["id"] == "obsidian")
    assert obsidian["status"] == "invalid"
    assert obsidian["required"] is False
    assert result["overall"] == "ready"


# ── Binary not available ────────────────────────────────────────────────


def test_preflight_ffmpeg_missing(tmp_path):
    """ffmpeg not found → missing status, overall blocked."""
    settings = Settings(
        deepseek_api_key="sk-test",
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value=None):
        result = setup_preflight(settings)

    assert result["overall"] == "blocked"
    ffmpeg = next(c for c in result["checks"] if c["id"] == "ffmpeg")
    assert ffmpeg["status"] == "missing"


# ── Degraded: partial Whisper, encrypted Jianying ───────────────────────


def test_preflight_asr_degraded_partial_whisper(tmp_path):
    """ASR is optional: partial Whisper is visible without blocking text use."""
    whisper_python = tmp_path / "python"
    whisper_python.write_text("fake")
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    output.mkdir()

    settings = Settings(
        deepseek_api_key="sk-test",
        local_whisper_python_path=str(whisper_python),
        local_whisper_model_path=None,
        tencentcloud_secret_id=None,
        tencentcloud_secret_key=None,
        video_inbox_path=str(inbox),
        video_output_path=str(output),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    asr = next(c for c in result["checks"] if c["id"] == "asr")
    assert asr["status"] == "degraded"
    assert asr["required"] is False
    assert result["overall"] == "ready"


def test_preflight_asr_ready_is_honest_about_local_only(tmp_path):
    """ASR ready must say config is provided / local conditions met, not
    verified, and defer Tencent remote availability to /api/runtime/health."""
    settings = Settings(
        deepseek_api_key="sk-test",
        asr_provider="tencentcloud",
        tencentcloud_secret_id="test-id",
        tencentcloud_secret_key="test-key",
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    asr = next(c for c in result["checks"] if c["id"] == "asr")
    assert asr["status"] == "ready"
    assert "凭据已配置" in asr["summary"]
    assert "未验证远端调用" in asr["summary"]
    assert "已验证" not in asr["summary"]


def test_preflight_jianying_degraded_encrypted(tmp_path):
    """Jianying root has crypto_key_store.dat → degraded."""
    jianying_root = tmp_path / "jianying"
    jianying_root.mkdir()
    (jianying_root / "draft_001").mkdir()
    (jianying_root / "draft_001" / "crypto_key_store.dat").write_text("enc")

    settings = Settings(
        deepseek_api_key="sk-test",
        jianying_draft_root_path=str(jianying_root),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    jianying = next(c for c in result["checks"] if c["id"] == "jianying")
    assert jianying["status"] == "degraded"
    assert "10.7" in jianying["summary"]
    assert "draft_content.json" in jianying["summary"]
    # Must not use the deprecated '兼容桥模式' wording
    assert "兼容桥" not in jianying["summary"]
    assert "兼容桥" not in jianying["impact"]


def test_preflight_obsidian_degraded_no_md(tmp_path):
    """Obsidian dir exists but has no .md files → degraded."""
    vault = tmp_path / "vault"
    vault.mkdir()

    settings = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(vault),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    obsidian = next(c for c in result["checks"] if c["id"] == "obsidian")
    assert obsidian["status"] == "degraded"
    assert ".md" in obsidian["summary"]


def test_preflight_non_critical_degraded_does_not_block(tmp_path):
    """Non-critical degraded (empty Obsidian + encrypted Jianying) keeps
    overall ready when all critical checks are ready."""
    vault = tmp_path / "vault"
    vault.mkdir()

    jianying_root = tmp_path / "jianying"
    jianying_root.mkdir()
    (jianying_root / "draft_001").mkdir()
    (jianying_root / "draft_001" / "crypto_key_store.dat").write_text("enc")

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    output = tmp_path / "output"
    output.mkdir()

    settings = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(vault),
        video_inbox_path=str(inbox),
        video_output_path=str(output),
        jianying_draft_root_path=str(jianying_root),
        asr_provider="tencentcloud",
        tencentcloud_secret_id="test-id",
        tencentcloud_secret_key="test-key",
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    by_id = {c["id"]: c for c in result["checks"]}
    assert by_id["obsidian"]["status"] == "degraded"
    assert by_id["jianying"]["status"] == "degraded"
    # Critical checks all ready
    assert by_id["deepseek"]["status"] == "ready"
    assert by_id["ffmpeg"]["status"] == "ready"
    assert by_id["asr"]["status"] == "ready"
    # Non-critical degraded must NOT block
    assert result["overall"] == "ready"


# ── Not applicable vs missing ───────────────────────────────────────────


def test_preflight_obsidian_not_configured_is_degraded():
    """Obsidian is optional when the maintained external source pool is used."""
    settings = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path="",
        database_path=":memory:",
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    obsidian = next(c for c in result["checks"] if c["id"] == "obsidian")
    assert obsidian["status"] == "degraded"


# ── Secrets and paths are never leaked ─────────────────────────────────


def test_preflight_never_leaks_secrets_or_absolute_paths(tmp_path):
    """The entire serialized preflight payload must never contain API keys,
    configured directory paths, or sys.executable."""
    dirs = {
        "obsidian": tmp_path / "obsidian",
        "inbox": tmp_path / "inbox",
        "output": tmp_path / "output",
        "jianying": tmp_path / "jianying",
    }
    for d in dirs.values():
        d.mkdir()

    settings = Settings(
        deepseek_api_key="sk-abc123secret",
        obsidian_vault_path=str(dirs["obsidian"]),
        video_inbox_path=str(dirs["inbox"]),
        video_output_path=str(dirs["output"]),
        jianying_draft_root_path=str(dirs["jianying"]),
        tencentcloud_secret_id="tid-secret",
        tencentcloud_secret_key="tkey-secret",
        local_whisper_python_path=str(tmp_path / "python"),
        local_whisper_model_path=str(tmp_path / "model.bin"),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(settings)

    import json
    payload = json.dumps(result, ensure_ascii=False)

    # Never leak API key values
    assert "sk-abc123secret" not in payload
    assert "tid-secret" not in payload
    assert "tkey-secret" not in payload

    # Never leak configured directory paths
    for p in dirs.values():
        assert str(p) not in payload, f"leaked path: {p}"

    # Never leak sys.executable
    assert sys.executable not in payload

    # Sanity: summaries are user-facing, path-free, and honest
    deepseek = next(c for c in result["checks"] if c["id"] == "deepseek")
    assert "配置已提供" in deepseek["summary"]
    assert "sk-abc" not in deepseek["summary"]

    python_check = next(c for c in result["checks"] if c["id"] == "python")
    assert "可用" in python_check["summary"]
    assert "/" not in python_check["summary"]


# ── settings_target populated ───────────────────────────────────────────


def test_preflight_settings_target_present_on_relevant_checks(tmp_path):
    """Most configured checks carry a settings_target for UI navigation."""
    for d in [
        tmp_path / "inbox",
        tmp_path / "output",
        tmp_path / "drafts",
        tmp_path / "jianying",
    ]:
        d.mkdir(exist_ok=True)

    s2 = Settings(
        deepseek_api_key="sk-test",
        obsidian_vault_path=str(tmp_path),
        video_inbox_path=str(tmp_path / "inbox"),
        video_output_path=str(tmp_path / "output"),
        draft_output_path=str(tmp_path / "drafts"),
        jianying_draft_root_path=str(tmp_path / "jianying"),
        database_path=str(tmp_path / "db.sqlite"),
    )

    with mock.patch("app.preflight.shutil.which", return_value="/usr/local/bin/ffmpeg"):
        result = setup_preflight(s2)

    # ffmpeg and python have no settings_target (they're binary checks)
    ffmpeg = next(c for c in result["checks"] if c["id"] == "ffmpeg")
    assert ffmpeg["settings_target"] is None
    python_check = next(c for c in result["checks"] if c["id"] == "python")
    assert python_check["settings_target"] is None

    # Configurable checks DO have settings_target
    for cid in ("deepseek", "obsidian", "video_inbox", "video_output", "jianying", "asr"):
        check = next(c for c in result["checks"] if c["id"] == cid)
        assert check["settings_target"] is not None, f"{cid} missing settings_target"


def test_default_local_preflight_does_not_report_ready_from_cloud_credentials():
    from app.preflight import _asr_check
    settings = Settings(tencentcloud_secret_id="saved", tencentcloud_secret_key="saved")
    check = _asr_check(settings)
    assert check["status"] == "missing"
    assert check["settings_target"] == "local_whisper_backend"
