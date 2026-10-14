"""Versioned media authorization persistence on owned or caller-supplied connections."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
import sqlite3

from app.models import utc_now
from app.persistence_json import (
    _required_text, _optional_string_tuple, _required_datetime, _optional_datetime,
)
from app.profiles.contracts import Authorization, _as_utc
from app.profiles.versioned_payloads import (
    _save_versioned_payload, _list_versioned_payloads, _versioned_row,
)


class MediaAuthorizationStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def save_authorization(self, authorization: Authorization) -> Authorization:
        _validate_authorization(authorization)
        with self._connect() as conn:
            _save_versioned_payload(
                conn,
                table="media_authorizations",
                workspace_id=authorization.workspace_id,
                record_id=authorization.id,
                version=authorization.version,
                payload=_authorization_payload(authorization),
            )
        return authorization

    def get_authorization(
        self,
        workspace_id: str,
        authorization_id: str,
        version: str | None = None,
    ) -> Authorization:
        with self._connect() as conn:
            return self._get_authorization(conn, workspace_id, authorization_id, version)

    def list_authorizations(self, workspace_id: str) -> list[Authorization]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="media_authorizations",
                workspace_id=workspace_id,
                current_only=True,
            )
        return [_authorization_from_payload(payload) for payload in payloads]

    def list_authorization_versions(
        self,
        workspace_id: str,
        authorization_id: str,
    ) -> list[Authorization]:
        with self._connect() as conn:
            payloads = _list_versioned_payloads(
                conn,
                table="media_authorizations",
                workspace_id=workspace_id,
                record_id=authorization_id,
            )
        return [_authorization_from_payload(payload) for payload in payloads]

    def _save_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization: Authorization,
    ) -> None:
        _validate_authorization(authorization)
        if authorization.workspace_id != workspace_id:
            raise ValueError("授权不属于当前工作区")
        _save_versioned_payload(
            conn,
            table="media_authorizations",
            workspace_id=workspace_id,
            record_id=authorization.id,
            version=authorization.version,
            payload=_authorization_payload(authorization),
        )

    def _get_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization_id: str,
        version: str | None,
    ) -> Authorization:
        row = _versioned_row(
            conn,
            table="media_authorizations",
            workspace_id=workspace_id,
            record_id=authorization_id,
            version=version,
        )
        return _authorization_from_payload(json.loads(str(row["payload"])))

    def _current_authorization(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        authorization: Authorization | None,
    ) -> Authorization | None:
        if authorization is None:
            return None
        # An explicitly revoked/expired historical version cannot be upgraded by a later record.
        if authorization.status in {"revoked", "expired"} or (
            authorization.expires_at is not None and _as_utc(authorization.expires_at) <= utc_now()
        ):
            return authorization
        return self._get_authorization(conn, workspace_id, authorization.id, None)


def _authorization_payload(authorization: Authorization) -> dict[str, object]:
    return {
        "id": authorization.id,
        "version": authorization.version,
        "rights_holder": authorization.rights_holder,
        "content_types": list(authorization.content_types),
        "usage_scopes": list(authorization.usage_scopes),
        "effective_from": authorization.effective_from.isoformat(),
        "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else None,
        "evidence_reference": authorization.evidence_reference,
        "status": authorization.status,
        "workspace_id": authorization.workspace_id,
    }


def _authorization_from_payload(data: dict[str, object]) -> Authorization:
    return Authorization(
        id=_required_text(data, "id"),
        version=_required_text(data, "version"),
        rights_holder=str(data.get("rights_holder") or ""),
        content_types=_optional_string_tuple(data, "content_types"),
        usage_scopes=_optional_string_tuple(data, "usage_scopes"),
        effective_from=_required_datetime(data, "effective_from"),
        expires_at=_optional_datetime(data, "expires_at"),
        evidence_reference=str(data.get("evidence_reference") or ""),
        status=_required_text(data, "status"),  # type: ignore[arg-type]
        workspace_id=_required_text(data, "workspace_id"),
    )


def _validate_authorization(authorization: Authorization) -> None:
    if not authorization.workspace_id.strip():
        raise ValueError("授权必须属于一个工作区")
    if not all(
        value.strip()
        for value in (
            authorization.id,
            authorization.version,
            authorization.rights_holder,
            authorization.evidence_reference,
        )
    ):
        raise ValueError("授权缺少必要标识或证据")
    if not authorization.content_types or not authorization.usage_scopes:
        raise ValueError("授权必须声明内容类型和用途范围")
    if authorization.status not in {"active", "pending", "expired", "revoked"}:
        raise ValueError("授权状态无效")
