"""Section-row persistence and versioned reads for one script.

The repository owns authorization, transactions, header CAS and request receipts.
Sections have one canonical store; the header no longer carries a body snapshot.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json


SCRIPT_SECTION_WINDOW_LIMIT = 20
SCRIPT_REVIEW_PROTOCOL_VERSION = "script-review-windows/v1"
SCRIPT_SECTION_SCHEMA = (
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_script_workspace_id ON creation_task_scripts(workspace_id,id)',
    'CREATE INDEX IF NOT EXISTS idx_script_active_version ON creation_task_scripts(workspace_id,task_id,updated_at DESC,id DESC)',
    '''CREATE TABLE IF NOT EXISTS creation_task_script_sections (
        workspace_id TEXT NOT NULL,
        script_id TEXT NOT NULL,
        section_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
        section_json TEXT NOT NULL,
        digest TEXT NOT NULL,
        PRIMARY KEY (workspace_id,script_id,section_id),
        UNIQUE (workspace_id,script_id,ordinal),
        FOREIGN KEY (workspace_id,script_id) REFERENCES creation_task_scripts(workspace_id,id)
            ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
    )''',
)


class ScriptWindowConflict(ValueError):
    pass


def script_revision(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _section_json(section: dict) -> str:
    return json.dumps(section, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def section_digest(section: dict) -> str:
    return hashlib.sha256(_section_json(section).encode()).hexdigest()


def write_script_sections(connection, workspace_id: str, script_id: str, sections) -> None:
    """Replace rows inside the repository's existing full-script transaction."""
    if not connection.in_transaction:
        raise ValueError("文案段落写入必须属于原稿件事务")
    connection.execute('DELETE FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?',
                       (workspace_id, script_id))
    def rows():
        for ordinal, section in enumerate(sections, start=1):
            payload = _section_json(section)
            yield workspace_id, script_id, section['id'], ordinal, payload, hashlib.sha256(payload.encode()).hexdigest()
    connection.executemany('''INSERT INTO creation_task_script_sections
        (workspace_id,script_id,section_id,ordinal,section_json,digest) VALUES (?,?,?,?,?,?)''', rows())


def migrate_script_sections(connection) -> None:
    """Move legacy bodies to rows atomically; never keep two writable bodies."""
    if not connection.in_transaction:
        raise ValueError("文案行化迁移必须属于数据库版本事务")
    for statement in SCRIPT_SECTION_SCHEMA:
        connection.execute(statement)
    while True:
        header = connection.execute('''SELECT workspace_id,id FROM creation_task_scripts
            WHERE COALESCE(json_extract(payload,'$._sections_storage'),'') != 'rows' ORDER BY id LIMIT 1''').fetchone()
        if header is None:
            return
        sections = connection.execute('''SELECT e.value FROM creation_task_scripts s, json_each(s.payload,'$.sections') e
            WHERE s.workspace_id=? AND s.id=? ORDER BY CAST(e.key AS INTEGER)''',
            (header[0], header[1]))
        write_script_sections(connection, header[0], header[1], (json.loads(row[0]) for row in sections))
        connection.execute('''UPDATE creation_task_scripts
            SET payload=json_set(json_remove(payload,'$.sections'),'$._sections_storage','rows')
            WHERE workspace_id=? AND id=?''', (header[0], header[1]))


def script_header(connection, workspace_id: str, task_id: str, script_id: str):
    row = connection.execute('''SELECT id,task_id,updated_at,
        json_extract(payload,'$.title') AS title, json_extract(payload,'$.language') AS language,
        json_extract(payload,'$.selection_status') AS selection_status,
        json_extract(payload,'$._sections_storage') AS storage
        FROM creation_task_scripts WHERE workspace_id=? AND task_id=? AND id=?''',
        (workspace_id, task_id, script_id)).fetchone()
    if row is None:
        raise KeyError(script_id)
    if row['storage'] != 'rows':
        raise ScriptWindowConflict("文案尚未完成行化迁移，不能将旧全文读取伪装为有界窗口")
    return row


def read_script_sections(connection, workspace_id: str, task_id: str, script_id: str, *,
                         expected_updated_at: str | None = None,
                         after_ordinal: int = 0, limit: int = SCRIPT_SECTION_WINDOW_LIMIT) -> dict:
    """Read indexed section rows under the repository's one read snapshot."""
    if not connection.in_transaction:
        raise ValueError("文案窗口读取必须属于同一数据库快照")
    if type(after_ordinal) is not int or after_ordinal < 0:
        raise ValueError("文案窗口起点必须是非负整数")
    if type(limit) is not int or not 1 <= limit <= SCRIPT_SECTION_WINDOW_LIMIT:
        raise ValueError(f"单次文案读取窗口必须在1到{SCRIPT_SECTION_WINDOW_LIMIT}段之间；请沿返回的下一窗口继续读取")
    if after_ordinal > 0 and expected_updated_at is None:
        raise ValueError("继续读取文案窗口必须提供首个窗口的版本，不能拼接未经核实的版本")
    header = script_header(connection, workspace_id, task_id, script_id)
    if expected_updated_at is not None and script_revision(header['updated_at']) != script_revision(expected_updated_at):
        raise ScriptWindowConflict("文案版本已变化，请重新读取当前版本；未拼接不同版本的段落")
    total = connection.execute('SELECT COUNT(*) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?',
                               (workspace_id, script_id)).fetchone()[0]
    if after_ordinal > total:
        raise ValueError("文案窗口起点超过当前稿件段数")
    rows = connection.execute('''SELECT ordinal,section_json,digest FROM creation_task_script_sections
        WHERE workspace_id=? AND script_id=? AND ordinal>? ORDER BY ordinal LIMIT ?''',
        (workspace_id, script_id, after_ordinal, limit)).fetchall()
    items = [{'ordinal': row['ordinal'], 'section': json.loads(row['section_json']), 'digest': row['digest']} for row in rows]
    end = items[-1]['ordinal'] if items else after_ordinal
    return {'task_id': header['task_id'], 'script_id': header['id'], 'updated_at': header['updated_at'],
            'title': header['title'], 'language': header['language'], 'selection_status': header['selection_status'],
            'total_count': total, 'items': items,
            'next_after_ordinal': end if end < total else None, 'truncated': end < total}


def upsert_script_sections(connection, workspace_id, script_id, sections, *, insert_after_section_id=None):
    """Update stable IDs in place; only new IDs use the explicit insertion anchor."""
    if not connection.in_transaction:
        raise ValueError("文案窗口写入必须属于原稿件事务")
    if not 1 <= len(sections) <= SCRIPT_SECTION_WINDOW_LIMIT:
        raise ValueError("单次修改超出文案窗口，请分次保存同一稿件")
    ids = [section['id'] for section in sections]
    if len(set(ids)) != len(ids):
        raise ValueError("同一窗口包含重复段落ID")
    slots = ','.join('?' for _ in ids)
    existing = {row['section_id'] for row in connection.execute(
        f'SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id IN ({slots})',
        (workspace_id, script_id, *ids))}
    new_sections = [section for section in sections if section['id'] not in existing]
    maximum = connection.execute('SELECT COALESCE(MAX(ordinal),0) FROM creation_task_script_sections WHERE workspace_id=? AND script_id=?',
                                 (workspace_id, script_id)).fetchone()[0]
    anchor = maximum
    if insert_after_section_id is not None:
        if not new_sections:
            raise ValueError("修改已有段落不隐式移动顺序；插入位置仅用于新增段落")
        row = connection.execute('SELECT ordinal FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? AND section_id=?',
                                 (workspace_id, script_id, insert_after_section_id)).fetchone()
        if row is None:
            raise ValueError("插入位置已不存在，请读取当前文案版本")
        anchor = row['ordinal']
    if new_sections and anchor < maximum:
        offset = maximum + len(new_sections)
        connection.execute('UPDATE creation_task_script_sections SET ordinal=ordinal+? WHERE workspace_id=? AND script_id=? AND ordinal>?',
                           (offset, workspace_id, script_id, anchor))
        connection.execute('UPDATE creation_task_script_sections SET ordinal=ordinal-?+? WHERE workspace_id=? AND script_id=? AND ordinal>?',
                           (offset, len(new_sections), workspace_id, script_id, offset))
    for section in sections:
        payload, digest = _section_json(section), section_digest(section)
        if section['id'] in existing:
            connection.execute('UPDATE creation_task_script_sections SET section_json=?,digest=? WHERE workspace_id=? AND script_id=? AND section_id=?',
                               (payload, digest, workspace_id, script_id, section['id']))
    for offset, section in enumerate(new_sections, start=1):
        connection.execute('''INSERT INTO creation_task_script_sections
            (workspace_id,script_id,section_id,ordinal,section_json,digest) VALUES (?,?,?,?,?,?)''',
            (workspace_id, script_id, section['id'], anchor + offset, _section_json(section), section_digest(section)))
    return {'affected_section_ids': ids, 'total_count': maximum + len(new_sections)}


def script_layout_digest(connection, workspace_id, script_id, *, insert_ids=(), insert_after_section_id=None):
    digest = hashlib.sha256()
    def add(section_id):
        digest.update(json.dumps(section_id, ensure_ascii=False).encode() + b'\n')
    inserted = False
    for row in connection.execute('SELECT section_id FROM creation_task_script_sections WHERE workspace_id=? AND script_id=? ORDER BY ordinal',
                                  (workspace_id, script_id)):
        add(row['section_id'])
        if row['section_id'] == insert_after_section_id:
            for section_id in insert_ids: add(section_id)
            inserted = True
    if insert_after_section_id is not None and not inserted:
        raise ValueError('插入位置已不存在，请重新读取当前文案')
    if not inserted:
        for section_id in insert_ids: add(section_id)
    return digest.hexdigest()


def script_patch_digest(sections, expected_updated_at, insert_after_section_id):
    values = {'sections': sections, 'expected_updated_at': script_revision(expected_updated_at).isoformat(),
              'insert_after_section_id': insert_after_section_id}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def script_patch_event_id(script_id, request_id, member_id):
    return 'script_window_' + hashlib.sha256(json.dumps([script_id, request_id, member_id]).encode()).hexdigest()


@dataclass(frozen=True)
class ScriptAdoptionFacts:
    id: str
    task_id: str
    selection_status: str
    updated_at: datetime
    adoption: dict
    body_digest: str


def script_confirmation_digest(expected_updated_at, adoption_kind):
    revision = expected_updated_at.isoformat() if isinstance(expected_updated_at,datetime) else expected_updated_at.strip()
    return hashlib.sha256(json.dumps([revision,adoption_kind]).encode()).hexdigest()


SCRIPT_LIBRARY_SECTION_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS script_library_sections (
        workspace_id TEXT NOT NULL, entry_id TEXT NOT NULL, section_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL CHECK(ordinal>0), section_json TEXT NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY(workspace_id,entry_id,section_id), UNIQUE(workspace_id,entry_id,ordinal))""",
    "CREATE INDEX IF NOT EXISTS idx_library_browse ON script_library_entries(workspace_id,created_at DESC,id DESC)",
    """CREATE TRIGGER IF NOT EXISTS library_header_rows_insert BEFORE INSERT ON script_library_entries
        WHEN json_type(NEW.payload,'$.sections') IS NOT NULL OR json_extract(NEW.payload,'$._sections_storage') IS NOT 'rows'
        BEGIN SELECT RAISE(ABORT,'library body must use canonical rows'); END""",
    """CREATE TRIGGER IF NOT EXISTS library_header_rows_update BEFORE UPDATE OF payload ON script_library_entries
        WHEN json_type(NEW.payload,'$.sections') IS NOT NULL OR json_extract(NEW.payload,'$._sections_storage') IS NOT 'rows'
        BEGIN SELECT RAISE(ABORT,'library body must use canonical rows'); END""",
    "CREATE INDEX IF NOT EXISTS idx_library_section_window ON script_library_sections(workspace_id,entry_id,ordinal)",
)


def write_library_sections(connection, workspace_id, entry_id, sections):
    if not connection.in_transaction:
        raise ValueError("文案库正文写入必须属于原事务")
    connection.execute(
        "DELETE FROM script_library_sections WHERE workspace_id=? AND entry_id=?",
        (workspace_id, entry_id),
    )
    for ordinal, part in enumerate(sections, 1):
        connection.execute(
            "INSERT INTO script_library_sections VALUES (?,?,?,?,?,?)",
            (
                workspace_id,
                entry_id,
                part["id"],
                ordinal,
                _section_json(part),
                section_digest(part),
            ),
        )


def migrate_library_sections(connection):
    for statement in SCRIPT_LIBRARY_SECTION_SCHEMA:
        connection.execute(statement)
    while True:
        row = connection.execute(
            "SELECT workspace_id,id FROM script_library_entries WHERE COALESCE(json_extract(payload,'$._sections_storage'),'')!='rows' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return
        parts = connection.execute(
            "SELECT j.value FROM script_library_entries e,json_each(e.payload,'$.sections') j WHERE e.workspace_id=? AND e.id=? ORDER BY CAST(j.key AS INTEGER)",
            (row[0], row[1]),
        )
        write_library_sections(
            connection, row[0], row[1], (json.loads(p[0]) for p in parts)
        )
        connection.execute(
            "UPDATE script_library_entries SET payload=json_set(json_remove(payload,'$.sections'),'$._sections_storage','rows') WHERE workspace_id=? AND id=?",
            (row[0], row[1]),
        )


def library_header(connection, workspace_id, entry_id):
    row = connection.execute(
        "SELECT payload FROM script_library_entries WHERE workspace_id=? AND id=?",
        (workspace_id, entry_id),
    ).fetchone()
    if row is None:
        raise KeyError(entry_id)
    data = json.loads(row["payload"])
    if data.pop("_sections_storage", None) != "rows" or "sections" in data:
        raise ScriptWindowConflict("文案库尚未完成正式正文行迁移")
    count = connection.execute(
        "SELECT count(*) FROM script_library_sections WHERE workspace_id=? AND entry_id=?",
        (workspace_id, entry_id),
    ).fetchone()[0]
    return {
        **data,
        "sections": None,
        "sections_loaded": False,
        "sections_total_count": count,
    }


def read_library_sections(
    connection,
    workspace_id,
    entry_id,
    *,
    expected_updated_at=None,
    after_ordinal=0,
    limit=SCRIPT_SECTION_WINDOW_LIMIT,
):
    if not connection.in_transaction:
        raise ValueError("文案库窗口读取必须属于同一快照")
    if (
        type(limit) is not int
        or not 1 <= limit <= SCRIPT_SECTION_WINDOW_LIMIT
        or type(after_ordinal) is not int
        or after_ordinal < 0
    ):
        raise ValueError("请使用合法的文案库正文窗口")
    if after_ordinal and expected_updated_at is None:
        raise ValueError("文案库续页必须绑定首读版本")
    header = library_header(connection, workspace_id, entry_id)
    if expected_updated_at is not None and script_revision(
        expected_updated_at
    ) != script_revision(header["updated_at"]):
        raise ScriptWindowConflict("文案库版本已变化，未拼接不同版本")
    if after_ordinal > header["sections_total_count"]:
        raise ValueError("文案库窗口超过正文范围")
    rows = connection.execute(
        "SELECT ordinal,section_json,digest FROM script_library_sections WHERE workspace_id=? AND entry_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
        (workspace_id, entry_id, after_ordinal, limit),
    ).fetchall()
    items = [
        {
            "ordinal": r["ordinal"],
            "section": json.loads(r["section_json"]),
            "digest": r["digest"],
        }
        for r in rows
    ]
    end = items[-1]["ordinal"] if items else after_ordinal
    more = end < header["sections_total_count"]
    return {
        "entry_id": entry_id,
        "version": header["version"],
        "updated_at": header["updated_at"],
        "total_count": header["sections_total_count"],
        "items": items,
        "next_after_ordinal": end if more else None,
        "has_more": more,
    }
