from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Mapping

from app.models import utc_now

PROFILE_ROW_ID = "default"
MAX_LIST_ITEMS = 12


@dataclass(frozen=True)
class CreatorProfile:
    identity: str
    content_domains: list[str] = field(default_factory=list)
    tone_keywords: list[str] = field(default_factory=list)
    preferred_phrases: list[str] = field(default_factory=list)
    banned_phrases: list[str] = field(default_factory=list)
    good_examples: list[str] = field(default_factory=list)
    bad_examples: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=utc_now)


def default_creator_profile() -> CreatorProfile:
    return CreatorProfile(
        identity="技术型个人创作者，关注 AI 工具、自动化、个人创作流程和真实项目复盘。",
        content_domains=["AI 产品", "Agent 工作流", "自动化", "个人创作流程"],
        tone_keywords=["直接", "真实", "具体", "少营销腔"],
        preferred_phrases=["先讲真实问题，再给判断", "用项目现场和踩坑解释观点"],
        banned_phrases=["家人们", "太炸裂了", "神器", "遥遥领先", "震惊体"],
        good_examples=["我今天踩到一个很具体的坑，表面是工具问题，其实是流程问题。"],
        bad_examples=["家人们，这个 AI 神器太炸裂了，错过就亏大了。"],
    )


def creator_profile_prompt_payload(profile: CreatorProfile | None) -> dict[str, object]:
    profile = profile or default_creator_profile()
    return {
        "identity": profile.identity,
        "content_domains": [*profile.content_domains[:6]],
        "voice": [*profile.tone_keywords[:6]],
        "prefer": [*profile.preferred_phrases[:6]],
        "avoid": [*profile.banned_phrases[:6]],
        "style_examples": {
            "good": [*profile.good_examples[:3]],
            "bad": [*profile.bad_examples[:3]],
        },
    }


class CreatorProfileRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def load(self) -> CreatorProfile:
        with sqlite3.connect(self._database_path) as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM creator_profile WHERE id = ?",
                (PROFILE_ROW_ID,),
            ).fetchone()
        if row is None:
            return default_creator_profile()

        payload, updated_at = row
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return _profile_from_mapping(
                    data,
                    updated_at=str(updated_at),
                    allow_blank_identity=True,
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return default_creator_profile()

    def save(self, updates: Mapping[str, object]) -> CreatorProfile:
        current = self.load()
        profile = _profile_from_mapping(
            {
                "identity": updates.get("identity", current.identity),
                "content_domains": updates.get(
                    "content_domains",
                    current.content_domains,
                ),
                "tone_keywords": updates.get("tone_keywords", current.tone_keywords),
                "preferred_phrases": updates.get(
                    "preferred_phrases",
                    current.preferred_phrases,
                ),
                "banned_phrases": updates.get("banned_phrases", current.banned_phrases),
                "good_examples": updates.get("good_examples", current.good_examples),
                "bad_examples": updates.get("bad_examples", current.bad_examples),
            },
            updated_at=utc_now().isoformat(),
            allow_blank_identity=True,
        )
        payload = json.dumps(_profile_to_payload(profile), ensure_ascii=False)
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO creator_profile (id, payload, updated_at)
                VALUES (?, ?, ?)
                """,
                (PROFILE_ROW_ID, payload, profile.updated_at.isoformat()),
            )
        return profile

    def _init_database(self) -> None:
        with sqlite3.connect(self._database_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS creator_profile (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )


def _profile_from_mapping(
    data: Mapping[str, object],
    *,
    updated_at: str | None = None,
    allow_blank_identity: bool = False,
) -> CreatorProfile:
    fallback = default_creator_profile()
    if allow_blank_identity and "identity" in data:
        identity = _trim_text(data.get("identity"))
    else:
        identity = _trim_text(data.get("identity")) or fallback.identity
    return CreatorProfile(
        identity=identity,
        content_domains=_clean_list(data.get("content_domains")),
        tone_keywords=_clean_list(data.get("tone_keywords")),
        preferred_phrases=_clean_list(data.get("preferred_phrases")),
        banned_phrases=_clean_list(data.get("banned_phrases")),
        good_examples=_clean_list(data.get("good_examples")),
        bad_examples=_clean_list(data.get("bad_examples")),
        updated_at=_parse_datetime(updated_at) or fallback.updated_at,
    )


def _profile_to_payload(profile: CreatorProfile) -> dict[str, object]:
    return {
        "schema_version": 1,
        "identity": profile.identity,
        "content_domains": [*profile.content_domains],
        "tone_keywords": [*profile.tone_keywords],
        "preferred_phrases": [*profile.preferred_phrases],
        "banned_phrases": [*profile.banned_phrases],
        "good_examples": [*profile.good_examples],
        "bad_examples": [*profile.bad_examples],
    }


def _clean_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _trim_text(item)
        if not text or text in seen:
            continue
        items.append(text)
        seen.add(text)
        if len(items) >= MAX_LIST_ITEMS:
            break
    return items


def _trim_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
