"""Storage for workspace business names and ordered material associations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sqlite3
from uuid import uuid4

from app.materials.contracts import BUSINESS_OBJECT_KINDS, MaterialBusinessObject
from app.models import utc_now


class MaterialBusinessObjectStore:
    """Use the application's existing connection factory; own no database setup."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connection_factory

    def list_business_objects(self, workspace_id: str) -> list[MaterialBusinessObject]:
        """List this workspace's name entries, newest first.

        Scoped to the workspace so one member's entries are never offered as
        another's; sharing across members is out of scope for this feature.
        Ordered by insertion rather than by timestamp: entries created in the
        same instant share a timestamp, and falling back to the random id would
        make the list jump around between reads.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, workspace_id, kind, name, created_at FROM business_objects "
                "WHERE workspace_id=? ORDER BY rowid DESC",
                (workspace_id,),
            ).fetchall()
        return [_business_object_from_row(row) for row in rows]

    def create_business_object(self, workspace_id: str, kind: str, name: str) -> MaterialBusinessObject:
        """Add one name entry to this workspace.

        Creating an entry does not attach it to anything. The user still has to
        choose which materials carry it, so no material silently gains an
        association from a name merely being typed.
        """
        if kind not in BUSINESS_OBJECT_KINDS:
            raise ValueError("名称条目类型只能是产品、菜品或人物")
        trimmed = name.strip()
        if not trimmed:
            raise ValueError("请填写名称")
        entry = MaterialBusinessObject(
            id=f"bo_{uuid4().hex[:12]}",
            workspace_id=workspace_id,
            kind=kind,
            name=trimmed,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO business_objects (id, workspace_id, kind, name, created_at) VALUES (?, ?, ?, ?, ?)",
                (entry.id, entry.workspace_id, entry.kind, entry.name, entry.created_at.isoformat()),
            )
        return entry

    def list_asset_business_objects(self, workspace_id: str, asset_id: str) -> list[MaterialBusinessObject]:
        """List the name entries attached to one material, in the order chosen.

        The links are written in the order the user selected them, so reading them
        back by insertion order reproduces that order rather than reshuffling it.
        """
        self._require_asset_in_workspace(workspace_id, asset_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.id, b.workspace_id, b.kind, b.name, b.created_at "
                "FROM material_business_objects AS m "
                "JOIN business_objects AS b ON b.id = m.business_object_id AND b.workspace_id = m.workspace_id "
                "WHERE m.workspace_id=? AND m.asset_id=? ORDER BY m.rowid",
                (workspace_id, asset_id),
            ).fetchall()
        return [_business_object_from_row(row) for row in rows]

    def set_asset_business_objects(
        self, workspace_id: str, asset_id: str, business_object_ids: list[str]
    ) -> list[MaterialBusinessObject]:
        """Replace one material's name entries with exactly the ones named.

        The material carries the whole selection, so this replaces rather than
        merges and an empty list clears earlier associations instead of being
        rejected. Nothing is written until the material and every named entry
        are confirmed to belong to this workspace: a partly-saved selection
        would show the user associations that are not actually stored.
        """
        wanted = list(dict.fromkeys(business_object_ids))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = conn.execute(
                "SELECT id FROM material_assets WHERE workspace_id=? AND id=?",
                (workspace_id, asset_id),
            ).fetchone()
            if asset is None:
                raise KeyError(asset_id)
            known: set[str] = set()
            for entry_id in wanted:
                found = conn.execute(
                    "SELECT id FROM business_objects WHERE workspace_id=? AND id=?",
                    (workspace_id, entry_id),
                ).fetchone()
                if found is None:
                    raise KeyError(entry_id)
                known.add(entry_id)
            conn.execute(
                "DELETE FROM material_business_objects WHERE workspace_id=? AND asset_id=?",
                (workspace_id, asset_id),
            )
            stamp = utc_now().isoformat()
            # 按用户给出的顺序写入，回读时才能还原成同一顺序。
            for entry_id in wanted:
                conn.execute(
                    "INSERT INTO material_business_objects "
                    "(workspace_id, asset_id, business_object_id, created_at) VALUES (?, ?, ?, ?)",
                    (workspace_id, asset_id, entry_id, stamp),
                )
            placeholders = ",".join("?" * len(wanted))
            rows = conn.execute(
                "SELECT id, workspace_id, kind, name, created_at FROM business_objects "
                f"WHERE workspace_id=? AND id IN ({placeholders})",
                (workspace_id, *wanted),
            ).fetchall()
            by_id = {str(row["id"]): _business_object_from_row(row) for row in rows}
            selected = [by_id[entry_id] for entry_id in wanted]
        return selected

    def _require_asset_in_workspace(self, workspace_id: str, asset_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM material_assets WHERE workspace_id=? AND id=?",
                (workspace_id, asset_id),
            ).fetchone()
        if row is None:
            raise KeyError(asset_id)


def _business_object_from_row(row: sqlite3.Row) -> MaterialBusinessObject:
    return MaterialBusinessObject(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        kind=str(row["kind"]),
        name=str(row["name"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )
