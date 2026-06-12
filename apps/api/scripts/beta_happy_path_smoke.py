from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 30


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class JsonHttpClient:
    base_url: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise SmokeFailure(f"{method} {path} failed: HTTP {exc.code} {message}") from exc
        except (OSError, URLError) as exc:
            raise SmokeFailure(f"{method} {path} failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"{method} {path} returned invalid JSON") from exc


def run_smoke(api_base: str, client: Any | None = None) -> dict[str, Any]:
    http = client or JsonHttpClient(api_base)
    episode = _require_mapping(http.get("/api/episodes/today"), "today episode")
    episode_id = _require_string(episode.get("id"), "episode id")

    topics = _topic_list(episode)
    if not topics:
        episode = _require_mapping(
            http.post(f"/api/episodes/{episode_id}/generate-topics"),
            "generated topics response",
        )
        topics = _topic_list(episode)
    if not topics:
        raise SmokeFailure("generate-topics returned no topics")

    topic_id = _selected_topic_id(episode, topics)
    if episode.get("selected_topic_id") != topic_id:
        episode = _require_mapping(
            http.post(
                f"/api/episodes/{episode_id}/approve-topic",
                {"topic_id": topic_id},
            ),
            "approved topic response",
        )
        selected_topic_id = _require_string(
            episode.get("selected_topic_id"),
            "selected topic id",
        )
        if selected_topic_id != topic_id:
            raise SmokeFailure("approve-topic did not persist selected_topic_id")

    script = episode.get("script")
    if not _is_valid_script(script):
        episode = _require_mapping(
            http.post(f"/api/episodes/{episode_id}/generate-script"),
            "generated script response",
        )
        script = episode.get("script")
    if not _is_valid_script(script):
        raise SmokeFailure("generate-script did not return a usable script")
    if episode.get("status") != "script_ready":
        raise SmokeFailure("generate-script did not move the episode to script_ready")

    sections = script.get("sections", [])
    return {
        "episode_id": episode_id,
        "topic_id": topic_id,
        "status": str(episode.get("status") or ""),
        "script_sections": len(sections),
    }


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SmokeFailure(f"{label} is not an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SmokeFailure(f"missing {label}")
    return value


def _topic_list(episode: dict[str, Any]) -> list[dict[str, Any]]:
    raw_topics = episode.get("topics")
    if not isinstance(raw_topics, list):
        return []
    return [topic for topic in raw_topics if isinstance(topic, dict)]


def _selected_topic_id(episode: dict[str, Any], topics: list[dict[str, Any]]) -> str:
    selected = episode.get("selected_topic_id")
    topic_ids = [topic.get("id") for topic in topics if isinstance(topic.get("id"), str)]
    if isinstance(selected, str) and selected in topic_ids:
        return selected
    if not topic_ids:
        raise SmokeFailure("no topics include an id")
    return topic_ids[0]


def _is_valid_script(script: Any) -> bool:
    if not isinstance(script, dict):
        return False
    hook = script.get("hook")
    sections = script.get("sections")
    return isinstance(hook, str) and bool(hook.strip()) and isinstance(sections, list) and bool(sections)


def main() -> int:
    api_base = os.getenv("AI_VIDEO_DIARY_API_BASE", DEFAULT_API_BASE)
    try:
        result = run_smoke(api_base)
    except SmokeFailure as exc:
        print(f"beta happy-path smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
