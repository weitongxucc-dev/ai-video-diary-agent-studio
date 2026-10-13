"""DDL owned by SourceRepository; no authorization or sync service imports."""

SOURCE_TABLES_DDL = """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS source_connectors (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    account_or_rule TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    cursor_value TEXT,
                    permissions TEXT NOT NULL,
                    connection_status TEXT NOT NULL,
                    latest_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_connectors_workspace
                    ON source_connectors(workspace_id);
                CREATE TABLE IF NOT EXISTS source_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT '',
                    source_url TEXT,
                    content_type TEXT NOT NULL,
                    author TEXT,
                    published_at TEXT,
                    body TEXT,
                    subtitles TEXT,
                    media_references TEXT NOT NULL,
                    license TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_reference TEXT,
                    fetched_at TEXT,
                    created_at TEXT NOT NULL,
                    use_state TEXT NOT NULL DEFAULT 'available',
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_items_workspace
                    ON source_items(workspace_id, connector_id, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT,
                    file_reference TEXT,
                    authorization TEXT NOT NULL,
                    ownership TEXT NOT NULL,
                    source_item_id TEXT,
                    version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    -- 新建内容默认私有（用户 2026-09-29 拍板）。注意：这里的 DEFAULT
                    -- 只管 fresh 库的 INSERT；升级库的存量行由
                    -- ensure_content_visibility_columns 回填为 shared。
                    visibility TEXT NOT NULL DEFAULT 'private',
                    -- 上传者身份（已验证的 user_id）；旧行与本地无身份写入留空。
                    owner_user_id TEXT,
                    use_state TEXT NOT NULL DEFAULT 'available',
                    FOREIGN KEY(source_item_id) REFERENCES source_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_items_workspace
                    ON knowledge_items(workspace_id, created_at);
                CREATE TABLE IF NOT EXISTS knowledge_item_versions (
                    knowledge_item_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    content_hash TEXT,
                    title TEXT NOT NULL,
                    body TEXT,
                    file_reference TEXT,
                    source_item_id TEXT,
                    sections TEXT NOT NULL DEFAULT '[]',
                    parameters TEXT NOT NULL DEFAULT '[]',
                    fetched_at TEXT,
                    source_url TEXT,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (knowledge_item_id, version),
                    FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items(id)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_item_versions_workspace
                    ON knowledge_item_versions(workspace_id, saved_at DESC);
                CREATE TABLE IF NOT EXISTS source_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    connector_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    cursor_before TEXT,
                    cursor_after TEXT,
                    item_count INTEGER NOT NULL,
                    reason TEXT,
                    impact TEXT,
                    repair_action TEXT,
                    retryable INTEGER,
                    FOREIGN KEY(connector_id) REFERENCES source_connectors(id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_runs_workspace
                    ON source_runs(workspace_id, connector_id, finished_at DESC);
                """


KNOWLEDGE_GRANTS_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_grants (
    id TEXT PRIMARY KEY,
    knowledge_item_id TEXT NOT NULL REFERENCES knowledge_items(id),
    source_workspace_id TEXT NOT NULL,
    target_workspace_id TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_by TEXT,
    revoked_at TEXT,
    CHECK (source_workspace_id <> target_workspace_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_grants_active_target
    ON knowledge_grants(knowledge_item_id, target_workspace_id)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS knowledge_grants_target
    ON knowledge_grants(target_workspace_id, knowledge_item_id, revoked_at);
"""
