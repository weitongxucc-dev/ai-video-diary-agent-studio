"""Workspace collection storage using the application's existing connection."""

from collections.abc import Callable
import json
import sqlite3

from app.materials.contracts import MaterialCollection
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime


class MaterialCollectionStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def list_collections(self, workspace_id: str) -> list[MaterialCollection]:
        with self._connect() as conn:
            return _list_collections(conn, workspace_id)

    def save_collection(self, collection: MaterialCollection) -> MaterialCollection:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if collection.parent_id is not None:
                parent = next(
                    (
                        item
                        for item in _list_collections(conn, collection.workspace_id)
                        if item.id == collection.parent_id
                    ),
                    None,
                )
                if parent is None:
                    raise KeyError(collection.parent_id)
            collection.updated_at = utc_now()
            conn.execute(
                """
                INSERT INTO material_collections (id, workspace_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    collection.id,
                    collection.workspace_id,
                    _json_payload(collection),
                    collection.updated_at.isoformat(),
                ),
            )
        return collection


def collection_ids_for_workspace(conn: sqlite3.Connection, workspace_id: str) -> set[str]:
    """Read IDs inside the caller's existing asset transaction."""
    return {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM material_collections WHERE workspace_id=?", (workspace_id,)
        )
    }


def _list_collections(conn: sqlite3.Connection, workspace_id: str) -> list[MaterialCollection]:
    rows = conn.execute(
        """
        SELECT payload FROM material_collections
        WHERE workspace_id = ? ORDER BY updated_at DESC
        """,
        (workspace_id,),
    ).fetchall()
    return [_collection_from_payload(json.loads(str(row["payload"]))) for row in rows]


def _collection_from_payload(data: dict[str, object]) -> MaterialCollection:
    data = _with_datetime(data, "created_at", "updated_at")
    return MaterialCollection(**data)
