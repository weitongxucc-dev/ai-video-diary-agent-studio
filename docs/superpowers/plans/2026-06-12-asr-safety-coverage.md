# ASR Safety And Coverage Stabilization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden local Whisper execution paths and lift `app/asr.py` focused coverage above 80%.

**Architecture:** Add a small pure path-validation helper used by transcription, public runtime status, and runtime health. Keep Tencent Cloud ASR and Local Whisper behavior unchanged except for rejecting unsafe Local Whisper path configurations before subprocess execution.

**Tech Stack:** Python 3.11, pytest, pytest-cov, FastAPI runtime settings.

---

## Task 1: Local Whisper Path Safety

**Files:**
- Create: `apps/api/app/local_whisper_security.py`
- Modify: `apps/api/app/asr.py`
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/runtime_health.py`
- Modify: `apps/api/tests/test_tencent_asr.py`
- Modify: `apps/api/tests/test_runtime_settings.py`

- [x] Add tests that reject Local Whisper Python paths outside allowed roots.
- [x] Add tests that require Local Whisper Python to be inside a virtual environment.
- [x] Make `LocalWhisperTranscriber.configured`, `Settings.public_status()`, and runtime health use the same validation logic.

## Task 2: ASR Coverage

**Files:**
- Modify: `apps/api/tests/test_tencent_asr.py`

- [x] Add Tencent ASR tests for missing config, provider failures, failed status, timeout, result-text parsing, and plain-text fallback.
- [x] Add Local Whisper tests for missing audio, runner failure, missing SRT, empty SRT, and invalid SRT timestamps.
- [x] Add fallback transcriber tests for no configured providers and aggregated failures.
- [x] Verify `app/asr.py` focused coverage is at least 80%.

## Verification

```bash
cd apps/api && uv run pytest tests/test_tencent_asr.py tests/test_runtime_settings.py tests/test_diagnostics.py tests/test_2_0_foundation.py -q
cd apps/api && uv run pytest --cov=app.asr --cov-report=term-missing tests/test_tencent_asr.py
```
