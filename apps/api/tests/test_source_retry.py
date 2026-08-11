"""Tests for POST /api/sources/{key}/retry — real retry path."""

import dataclasses
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.external_sources import ExternalSourceResponseError
from app.main import app

client = TestClient(app)

_FAKE_ITEMS = [None, None, None]


class _FakeHermesRadarClient:
    def __init__(self, report_dir=None, limit=80):
        pass

    def fetch_items(self, report_date=None):
        return _FAKE_ITEMS


class _FakeHtmlPageClient:
    def __init__(self, urls=None, transport=None, timeout=10.0):
        pass

    def fetch_items(self):
        return _FAKE_ITEMS


class _FailingClient:
    def __init__(self, urls=None, transport=None, timeout=10.0):
        pass

    def fetch_items(self):
        raise ExternalSourceResponseError("connection refused")


# ── happy paths ──────────────────────────────────────────────────────────

def test_retry_hermes_radar_success():
    with patch("app.main.HermesRadarSourceClient", new=_FakeHermesRadarClient):
        resp = client.post("/api/sources/hermes_radar/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "hermes_radar"
    assert data["status"] == "success"
    assert data["item_count"] == 3
    assert data["error"] is None


def test_retry_external_urls_success():
    from app.main import settings
    patched_settings = dataclasses.replace(settings, topic_source_urls=["https://example.com"])
    with (
        patch("app.main.HtmlPageSourceClient", new=_FakeHtmlPageClient),
        patch("app.main.settings", patched_settings),
    ):
        resp = client.post("/api/sources/external_urls/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "external_urls"
    assert data["status"] == "success"
    assert data["item_count"] == 3
    assert data["error"] is None


def test_retry_external_urls_skipped_when_no_urls_configured():
    from app.main import settings
    patched_settings = dataclasses.replace(settings, topic_source_urls=None)
    with (
        patch("app.main.HtmlPageSourceClient", new=_FakeHtmlPageClient),
        patch("app.main.settings", patched_settings),
    ):
        resp = client.post("/api/sources/external_urls/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "skipped"
    assert data["item_count"] == 0


def test_retry_manual_success():
    resp = client.post("/api/sources/manual/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_type"] == "manual"
    assert data["status"] == "success"
    assert data["error"] is None


# ── error paths ──────────────────────────────────────────────────────────

def test_retry_unknown_source_404():
    resp = client.post("/api/sources/unknown_key/retry")
    assert resp.status_code == 404
    assert "unknown" in resp.json()["detail"].lower()


def test_retry_external_urls_upstream_failure():
    from app.main import settings
    patched_settings = dataclasses.replace(settings, topic_source_urls=["https://bad.example"])
    with (
        patch("app.main.HtmlPageSourceClient", new=_FailingClient),
        patch("app.main.settings", patched_settings),
    ):
        resp = client.post("/api/sources/external_urls/retry")
    assert resp.status_code == 502
