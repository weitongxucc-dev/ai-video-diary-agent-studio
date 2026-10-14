from contextlib import closing
import sqlite3

from app.materials.assets import MaterialAssetStore


def test_asset_id_projection_keeps_raw_column_scope_and_caller_transaction(tmp_path):
    def forbidden_connection():
        raise AssertionError("The reader must use the caller connection")

    store = MaterialAssetStore(forbidden_connection)
    database = tmp_path / "asset-id.sqlite3"
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE material_assets (id TEXT, workspace_id TEXT, file_path TEXT, payload TEXT)")
        conn.executemany("INSERT INTO material_assets VALUES (?, ?, ?, ?)", [
            ("column-a", "workspace-a", "/reference.wav", '{"id":"different-payload-id"}'),
            ("column-b", "workspace-b", "/reference.wav", "invalid JSON"),
        ])
        conn.commit()
        assert dict(store._asset_id_row_by_path(conn, "workspace-a", "/reference.wav")) == {"id": "column-a"}
        assert dict(store._asset_id_row_by_path(conn, "workspace-b", "/reference.wav")) == {"id": "column-b"}
        assert store._asset_id_row_by_path(conn, "missing-workspace", "/reference.wav") is None
        assert store._asset_id_row_by_path(conn, "workspace-a", "/missing.wav") is None

        conn.execute("BEGIN")
        conn.execute("INSERT INTO material_assets VALUES (?, ?, ?, ?)",
                     ("pending", "workspace-a", "/pending.wav", "invalid JSON"))
        assert store._asset_id_row_by_path(conn, "workspace-a", "/pending.wav")["id"] == "pending"
        assert conn.in_transaction
        with closing(sqlite3.connect(database)) as observer:
            assert store._asset_id_row_by_path(observer, "workspace-a", "/pending.wav") is None
        conn.rollback()
        assert store._asset_id_row_by_path(conn, "workspace-a", "/pending.wav") is None
        assert store._asset_id_row_by_path(conn, "workspace-a", "/reference.wav")["id"] == "column-a"
